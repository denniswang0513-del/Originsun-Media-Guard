"""core/nas_auth.py — NAS(SMB) 認證韌性層：碰 UNC 之前先確認連得上，
認證瞬斷時自動重連再重試一次。

動機（2026-09-10 `20260910_循環杯` 備份 2 連敗，18:08 / 18:10）：

    [WinError 1326] 使用者名稱或密碼不正確。: '\\\\192.168.1.132\\Project_Longterm\\'

那個結尾的反斜線就是兇手的指紋 —— `ntpath.dirname()` 對 UNC 路徑回傳的
share 根目錄帶尾斜線，也就是 `os.makedirs()` 一路往上遞迴、最後在
`mkdir(\\\\host\\share\\)` 上噴的。半小時後同一台同一個帳號用同一條路徑
讀寫全部正常 —— 是 SMB session 的暫時性認證失效，不是路徑或權限問題。

Windows 對 NAS 的 SMB session 是「登入 session 綁定 + 會過期」的東西：
NAS 端 SMB 服務重啟、QNAP 連線封鎖、閒置後 session 重建撞到過期憑證，
都會讓一條原本好好的路徑突然回 1326。`core/drive_map` 解決的是「哪台有掛
磁碟」（`T:\\` → UNC，2026-07-21 煥民新村 6 連敗的治本），**沒有**解決
「哪台有認證」—— 翻成 UNC 之後照樣要有 session 才開得了。

v1.7.2 把 `main.py` 開機自動掛載拿掉之後，整包程式再沒有任何一行認證邏輯，
一次瞬斷就讓整個備份任務死掉、畫面只吐一行原始 WinError，操作的人無從判斷
該重跑還是該去修 NAS。這支補上三件事：

  ensure_ready()  任務開跑前的前置檢查 —— 連不上先退避重連，再不行 fail fast
  guard()         包住會碰 UNC 的呼叫 —— 認證錯誤 → 退避重連 → 重跑一次
  friendly()      WinError → 看得懂、可行動的中文訊息

重試是「15 秒 ×5 次」而不是立刻重試一次（owner 2026-09-10 拍板）：那天 18:08
與 18:10 各失敗一次，代表 NAS 端的狀態至少持續了幾分鐘，毫秒級的重連只會再撞
一次同一面牆。代價是任務放棄前會多花最多 60 秒，這對備份任務划算。

非 Windows（NAS 自己的 Linux 容器）整支 no-op —— 那邊看到的是 `/share`
掛載點（見 `drive_map.nas_local_path`），沒有 SMB session 這回事。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Iterable, Sequence

_IS_WINDOWS = os.name == "nt"

# 「重連一次也許就好」的 Windows 錯誤碼 —— 全部是 session/認證層，不是權限層。
# 刻意不收 5 (ACCESS_DENIED)：那是「這個帳號真的沒權限」，重連幾次都一樣，
# 蓋成認證問題只會讓人往錯的方向找。
AUTH_ERRNOS: dict = {
    1326: "帳號或密碼不正確（SMB 認證失敗）",
    1219: "同一台伺服器已有另一組帳密的連線（認證衝突）",
    1311: "找不到可用的登入伺服器",
    53:   "找不到網路路徑",
    64:   "指定的網路名稱已無法使用（連線被中斷）",
    67:   "找不到網路名稱（分享區可能被改名或停用）",
}

# 上面那組裡真正是「帳密」問題的 —— 只有這幾個才該叫使用者去翻認證管理員。
# 53/64/67 是連得到但名字或連線斷了，講成認證失敗會把人帶去錯的地方找。
_CREDENTIAL_ERRNOS = frozenset({1326, 1219, 1311})

# WNetAddConnection2W 用得到的常數
_RESOURCETYPE_DISK = 0x00000001
_ERROR_SESSION_CREDENTIAL_CONFLICT = 1219
_ERROR_ALREADY_ASSIGNED = 85
_NO_ERROR = 0

# 退避重試（owner 2026-09-10 拍板）：每次隔 15 秒、總共試 5 次 —— 最壞 4×15＝60 秒
# 才放棄。立即重試接不住那天那種故障：18:08 與 18:10 各失敗一次，代表 NAS 端的
# 狀態至少持續了幾分鐘，毫秒級的重連只會再撞一次同樣的牆。
RECONNECT_ATTEMPTS = 5
RECONNECT_DELAY_SEC = 15

# 冷卻閘：整輪退避失敗後，這個 share 在冷卻期內直接判死，不再跑第二輪。
# 沒有這道閘，NAS 真的掛掉時 guard() 是**逐檔**呼叫的 —— 5000 個檔案各自
# 燒 60 秒＝83 小時的殭屍任務，比原本直接失敗還糟。
RECONNECT_COOLDOWN_SEC = 60

_down_until: dict = {}          # share 根目錄(大寫) → 判死到什麼時候（monotonic）
_down_lock = threading.Lock()

# Session 0 的解法就這一行。用 raw string —— 裡面有 Windows 路徑的反斜線，
# 一般字串靠「無效跳脫原樣保留」才會對，那是會隨 Python 版本消失的運氣。
BOOT_TASK_CMD = (
    r'schtasks /Create /TN OriginsunAgent_Boot '
    r'/TR "wscript.exe C:\OriginsunAgent\start_hidden.vbs" /SC ONLOGON /IT /F'
)


# ── 路徑判讀（純函式，跨平台） ──────────────────────────────────────

def session_id():
    """這個行程跑在哪個 Windows session。非 Windows／問不到 → None。"""
    if not _IS_WINDOWS:
        return None
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        sid = ctypes.c_ulong()
        if k32.ProcessIdToSessionId(k32.GetCurrentProcessId(), ctypes.byref(sid)):
            return int(sid.value)
    except Exception:
        return None
    return None


def in_service_session() -> bool:
    """在 Session 0 嗎（服務／非互動）。

    🔴 為什麼這支住在 NAS 認證這個檔裡：Session 0 的**後果**就是認證。NAS 的憑證
       存在**互動使用者**的認證管理員裡，服務 session 一輩子看不到它 —— 於是同一台
       機器「在 cmd 裡 dir 得開、agent 卻說帳密不正確」。
       2026-09-10 備檔電腦（192.168.1.120）就是這樣：`cmdkey /list` 明明有那筆，
       WinError 1326 照噴，害人往「密碼改過了」的方向找了一輪。
    """
    return session_id() == 0


def is_unc(path: str) -> bool:
    """是不是 `\\\\host\\share...` 形式的 UNC 路徑。"""
    p = str(path or "")
    return p.startswith("\\\\") or p.startswith("//")


def share_root(path: str) -> str:
    """UNC 路徑 → `\\\\host\\share`（不帶尾斜線）。非 UNC 回空字串。

    自己切而不用 `ntpath.splitdrive`：這支在 Linux 容器裡也會被呼叫到
    （`ensure_ready` 對整串路徑做判斷），`os.path` 在那邊是 posixpath，
    對 UNC 切不出東西來。
    """
    p = str(path or "").replace("/", "\\")
    if not p.startswith("\\\\"):
        return ""
    parts = [x for x in p[2:].split("\\") if x]
    if len(parts) < 2:
        return ""
    return "\\\\" + parts[0] + "\\" + parts[1]


def share_roots(paths: Iterable[str]) -> list:
    """一串路徑 → 去重後的 share 根目錄清單（保持出現順序）。"""
    out: list = []
    for p in paths or []:
        r = share_root(p)
        if r and r.upper() not in [x.upper() for x in out]:
            out.append(r)
    return out


def is_auth_error(exc: BaseException) -> bool:
    """這個例外是不是「重連一次也許就好」的 SMB session 問題。"""
    return getattr(exc, "winerror", None) in AUTH_ERRNOS


def friendly(exc: BaseException, path: str = "") -> str:
    """認證類 WinError → 可行動的中文訊息。不是認證問題回空字串
    （呼叫端就照原樣用 `str(exc)`，不要把別的錯誤蓋成認證錯誤）。"""
    code = getattr(exc, "winerror", None)
    if code not in AUTH_ERRNOS:
        return ""
    target = share_root(path) or share_root(getattr(exc, "filename", "") or "") or "NAS"
    is_cred = code in _CREDENTIAL_ERRNOS
    # 🔴 帳密類錯誤 ＋ 真的在 Session 0 → 那才是最可能的原因，要排在第一條。
    #    憑證在互動使用者的認證管理員裡，服務 session 看不到 —— 症狀跟「密碼錯」
    #    一模一樣，但去改密碼是白費工（2026-09-10 備檔電腦實際發生）。
    #    **只有偵測到才加**，不然十台裡有九台會看到一句與它無關的雜訊。
    session0 = is_cred and in_service_session()
    checks = (
        ([] if not session0 else
         ["1. 🔴 這台的 agent 跑在 Session 0（服務／非互動）—— 看不到你存在"
          "「認證管理員」裡的憑證。密碼很可能是對的。"
          f"改用互動式排程重啟 agent：{BOOT_TASK_CMD}"])
        + [f"{2 if session0 else 1}. 這台的 Windows「認證管理員」裡 {_host_of(target)} 的帳密還有效（密碼改過就要更新）",
           f"{3 if session0 else 2}. NAS 端沒有把這台的 IP 列入連線封鎖",
           f"{4 if session0 else 3}. NAS 開著、SMB 服務正常"]
        if is_cred else
        [f"1. NAS（{_host_of(target)}）開著、SMB 服務正常",
         f"2. 分享區名稱沒有被改掉或停用（設定裡指的是 {target}）",
         "3. 網路沒斷（有線／VPN）"]
    )
    return (
        f"{'NAS 認證失敗' if is_cred else 'NAS 連線失敗'}"
        f"（WinError {code}：{AUTH_ERRNOS[code]}）：{target}\n"
        f"　已自動重連仍失敗。請依序確認：\n"
        + "".join(f"　{c}\n" for c in checks)
        + "　排除後直接重跑這個任務即可，已備份完成的檔案會由中斷點跳過。"
    )


def _host_of(root: str) -> str:
    parts = [x for x in str(root or "").replace("/", "\\")[2:].split("\\") if x]
    return parts[0] if parts else "NAS"


# ── 重連（只有 Windows 真的做事） ───────────────────────────────────

def reconnect(root: str) -> bool:
    """對 `\\\\host\\share` 重建一條 SMB 連線。成功（或本來就通）回 True。

    帳密一律傳 NULL —— 讓 Windows 自己用目前登入帳號 / 認證管理員裡存的那筆，
    程式裡不碰、也不存任何 NAS 密碼。

    撞到 1219（同主機已有另一組帳密的連線）時才會先斷開既有連線再重連，
    而且 **fForce=FALSE** —— 有人正在讀寫時系統會拒絕斷開（回 ERROR_DEVICE_IN_USE），
    這正是我們要的：絕不把別的任務正在用的磁碟從底下抽掉。
    """
    if not _IS_WINDOWS or not root:
        return False
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return False

    class _NETRESOURCE(ctypes.Structure):
        _fields_ = [
            ("dwScope", wintypes.DWORD),
            ("dwType", wintypes.DWORD),
            ("dwDisplayType", wintypes.DWORD),
            ("dwUsage", wintypes.DWORD),
            ("lpLocalName", wintypes.LPWSTR),
            ("lpRemoteName", wintypes.LPWSTR),
            ("lpComment", wintypes.LPWSTR),
            ("lpProvider", wintypes.LPWSTR),
        ]

    try:
        mpr = ctypes.WinDLL("mpr.dll")
    except Exception:
        return False

    nr = _NETRESOURCE()
    nr.dwType = _RESOURCETYPE_DISK
    nr.lpLocalName = None          # 不佔用磁碟代號，只建立 connectionless session
    nr.lpRemoteName = root
    nr.lpProvider = None

    rc = mpr.WNetAddConnection2W(ctypes.byref(nr), None, None, 0)
    if rc in (_NO_ERROR, _ERROR_ALREADY_ASSIGNED):
        return True

    if rc == _ERROR_SESSION_CREDENTIAL_CONFLICT:
        # fForce=FALSE：有開啟中的檔案就斷不掉，那也好過砍掉別人的任務
        mpr.WNetCancelConnection2W(root, 0, False)
        rc = mpr.WNetAddConnection2W(ctypes.byref(nr), None, None, 0)

    return rc in (_NO_ERROR, _ERROR_ALREADY_ASSIGNED)


# ── 前置檢查與重試包裝 ─────────────────────────────────────────────

def _probe(root: str) -> BaseException | None:
    """戳一下 share 根目錄，通了回 None，不通回那個例外。

    用 `os.stat` 而不是 `os.path.exists` —— exists 對 1326 也只是回 False，
    「認證失敗」跟「路徑真的不存在」會被壓成同一個答案，錯誤訊息就廢了。
    """
    try:
        os.stat(root)
        return None
    except OSError as exc:
        return exc


def _wait(seconds: float, should_stop: Callable[[], bool] | None) -> bool:
    """可被中斷的等待。使用者按停止就立刻回 False，別讓任務卡在退避裡。"""
    end = time.monotonic() + seconds
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return True
        if should_stop and should_stop():
            return False
        time.sleep(max(0.0, min(0.5, left)))


def _cooldown_left(root: str) -> float:
    with _down_lock:
        return max(0.0, _down_until.get(root.upper(), 0.0) - time.monotonic())


def _mark(root: str, down: bool) -> None:
    with _down_lock:
        if down:
            _down_until[root.upper()] = time.monotonic() + RECONNECT_COOLDOWN_SEC
        else:
            _down_until.pop(root.upper(), None)


def reconnect_with_backoff(
    root: str,
    should_stop: Callable[[], bool] | None = None,
    log: Callable[[str], None] | None = None,
    attempts: int = RECONNECT_ATTEMPTS,
    delay: float = RECONNECT_DELAY_SEC,
) -> bool:
    """重連並確認真的通得了；不成就退避後再試，最多 `attempts` 次。

    成功的判準是「戳得到 share 根目錄」而不是「WNetAddConnection2 回 0」——
    後者只代表連線項目建起來了，不代表待會真的開得了檔。

    整輪失敗後把這個 share 標成冷卻中（見 `RECONNECT_COOLDOWN_SEC`）：冷卻期內
    再問直接回 False，不會每個檔案都自己跑一輪 60 秒。
    """
    if not _IS_WINDOWS or not root:
        return False

    left = _cooldown_left(root)
    if left > 0:
        if log:
            log(f"[NAS] {root} 仍在冷卻中（約 {left:.0f} 秒），略過重連。")
        return False

    for i in range(1, attempts + 1):
        reconnect(root)
        err = _probe(root)
        if err is None:
            if log and i > 1:
                log(f"[NAS] {root} 第 {i} 次重連成功。")
            _mark(root, down=False)
            return True
        # 🔴 Session 0 ＋ 帳密類錯誤 ＝ 重試一定也是同一個結果：那些憑證存在
        #    **互動使用者**的認證管理員裡，服務 session 看不到它，等 60 秒之後
        #    看到的還是同一片空白。這裡快速失敗，把時間留給人去修真正的問題。
        #    🔴 只有這個組合可以跳過重試 —— 純 1326 **要**重試（檔頭那次事故
        #    18:08／18:10 兩連敗、半小時後自己好，15 秒 ×5 就是為了接住它）。
        if i == 1 and in_service_session() \
                and getattr(err, "winerror", None) in _CREDENTIAL_ERRNOS:
            if log:
                log(f"[NAS] {root} 認證失敗，而這台的 agent 跑在 Session 0"
                    f"（服務／非互動）—— 看不到認證管理員裡的憑證，重連不會成功。"
                    f"不再等待，直接放棄。解法：{BOOT_TASK_CMD}")
            break
        if i == attempts:
            break
        if log:
            log(f"[NAS] {root} 第 {i}/{attempts} 次重連失敗，{delay:.0f} 秒後再試…")
        if not _wait(delay, should_stop):
            if log:
                log(f"[NAS] {root} 重連等待被中斷。")
            return False

    _mark(root, down=True)
    return False


def ensure_ready(
    paths: Sequence[str],
    log: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple:
    """任務開跑前的 UNC 前置檢查。回傳 `(ok, 失敗原因)`。

    每個用到的 share 根目錄戳一次；不通就退避重連（15 秒 ×5），還是不通就
    fail fast，讓錯誤停在「任務還沒動任何檔案」的時候 —— 而不是跑到一半在某個
    `os.makedirs` 上噴一行看不懂的 WinError。

    非 Windows、或路徑裡根本沒有 UNC → 直接 `(True, "")`。
    """
    if not _IS_WINDOWS:
        return True, ""
    for root in share_roots(paths):
        exc = _probe(root)
        if exc is None:
            continue
        if log:
            log(f"[NAS] {root} 目前不通（{exc}），開始重連…")
        if reconnect_with_backoff(root, should_stop=should_stop, log=log):
            continue
        exc2 = _probe(root) or exc
        return False, (friendly(exc2, root) or f"NAS 路徑不可用：{root}（{exc2}）")
    return True, ""


def guard(path: str, fn: Callable[..., Any], *args,
          should_stop: Callable[[], bool] | None = None, **kwargs):
    """跑 `fn(*args)`；撞到認證類 WinError 就退避重連，接回來之後再跑一次。

    只對 `AUTH_ERRNOS` 裡的錯誤重試 —— 磁碟滿了、檔名不合法這種重試幾次都一樣的
    錯誤要立刻往上丟，不要被無聲吞掉。

    `fn` 本身只重跑**一次**：等待迴圈在 `reconnect_with_backoff` 裡（已經確認
    share 通了才回來），這裡再包一層迴圈只會讓逐檔呼叫的成本翻倍。
    """
    try:
        return fn(*args, **kwargs)
    except OSError as exc:
        if not _IS_WINDOWS or not is_auth_error(exc):
            raise
        root = share_root(path) or share_root(getattr(exc, "filename", "") or "")
        if not root or not reconnect_with_backoff(root, should_stop=should_stop):
            raise
        return fn(*args, **kwargs)
