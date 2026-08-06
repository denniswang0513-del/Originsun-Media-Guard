"""services/reference_archiver.py — 參考影片封存 runner（docs/REFERENCE_LIBRARY.md §12）
---
把片庫的片自動「建檔」到 NAS：yt-dlp 下載 ≤720p → 統一轉成瀏覽器可直播的
H.264 mp4 → ffmpeg 抽 5 張等距截圖進既有截圖牆 → 寫 info.json（人類可讀建檔）。

跑法：core/scheduler 每 60s tick 呼叫 `tick()`；該跑就丟一個背景 asyncio task，
**長時下載絕不阻塞排程迴圈**，且同一時間只有一個封存工作（_busy 旗標）。

Gate（全部在這裡，tick 呼叫端不用管）：
- `is_master_machine()` —— 排程性質的「機隊只跑一次」閘（NAS SMB 憑證與 yt-dlp 也只在 master）
- settings `reference_archive.enabled`（預設 False；管理卡打開才動）
- 節流：`per_hour`（預設 6）—— YouTube 對機器人下載敏感，寧慢勿封 IP
- 磁碟上限 `max_gb`：超過即暫停（狀態留給管理卡顯示）

yt-dlp 用**獨立執行檔**不用 pip 套件：生產 8000 跑 python_embed（pip 依賴要手動裝
的既有坑）；單檔 exe 完全繞開，且內建自我更新（phase 2 接 `-U` 自救）。
首次使用自動從 GitHub 下載到 tools/（不進 git、不進 OTA —— 二進制不得進 OTA ZIP）。

失敗處理（三路，owner 要求「下載失效自動更新解決」的核心）：
- **影片已死**（private/removed/unavailable）→ `unavailable`，不重試；每 30 天復查一次
  （平台誤判/重新公開的機會窗）
- **抽取器壞了**（Unable to extract / signature / 403 —— YouTube 改版的典型症狀）
  → 跑 `yt-dlp.exe -U` 自我更新 → **當下立刻重試一次**；仍敗 → retry 退避
- **暫時性**（網路/timeout）→ retry 退避階梯 1h → 4h → 24h（archive_tries 決定）
連續 3 次失敗（已含自救重試）→ `archive_failed` 告警（alert_webhook + email relay）。
每週日固定 `-U` 一次 —— 不等壞掉才更新。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger("uvicorn.error")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS_DIR = os.path.join(_REPO, "tools")
_YTDLP = os.path.join(_TOOLS_DIR, "yt-dlp.exe")
_YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
_FFMPEG = os.path.join(_REPO, "ffmpeg.exe")
_FFPROBE = os.path.join(_REPO, "ffprobe.exe")

_DL_TIMEOUT = 30 * 60          # 單支下載上限（秒）
_STILLS = 5                    # 等距截圖張數
_AUTO_KEY = "auto-archive"     # 系統截圖的 created_key（冪等替換的依據）

# 失敗分類的 stderr 樣式（順序重要：先判「已死」再判「抽取器」）。
# 刻意不收的：HTTP 403（地區/年齡限制居多，-U 救不了 → transient 退避即可）、
# HTTP 404（可能只是單一 fragment 暫時錯，標死要等 30 天復查，代價太高）、
# Requested format is not available（多半是 max_height 太嚴，不是版本問題）。
_DEAD_PATTERNS = re.compile(
    r"Private video|Video unavailable|has been removed|no longer available|"
    r"This video is not available|account.*terminated|"
    r"not made this video available in your country", re.I)
# 平台改版害 yt-dlp 舊版失效的**明確**症狀 → 自我更新後立刻重試
# （OAuth token 401 = Vimeo 撤銷 yt-dlp 內建 client 憑證，2026-08 實例）
_EXTRACTOR_PATTERNS = re.compile(
    r"Unable to extract|Signature extraction|nsig extraction|"
    r"Precondition check failed|Failed to fetch \w+ OAuth token", re.I)

_ALERT_TRIES = 3               # 連敗幾次告警（恰好跨過門檻那次發，不重複轟炸）
_RECHECK_DEAD_DAYS = 30        # unavailable 的復查週期
_SELFUPDATE_MIN_GAP = 6 * 3600 # 兩次 -U 之間至少隔多久（失敗風暴不重複更新）

_busy = False                  # 同時只跑一個封存工作（tick 與 archive_one 共用）
_task = None                   # 背景 task 的引用（fire-and-forget 會被 GC 收走 — asyncio 明文）
_last_start = 0.0              # 節流基準（上一支開始/上一次空轉時間）
_paused_reason = ""            # 磁碟滿等暫停原因（管理卡顯示；phase 3 讀）
_current_rid = ""              # 正在建檔的片（管理卡顯示）


# retry 退避階梯（唯一正本 —— _pick_next 的 SQL CASE 由它生成，別再寫第二份）
_BACKOFF_H = ((1, 1), (2, 4))          # (tries 上限, 冷卻小時)
_BACKOFF_MAX_H = 24


def _classify_failure(stderr: str) -> str:
    """yt-dlp stderr → 'dead' | 'extractor' | 'transient'（三路處置不同，見檔頭）。"""
    if _DEAD_PATTERNS.search(stderr or ""):
        return "dead"
    if _EXTRACTOR_PATTERNS.search(stderr or ""):
        return "extractor"
    return "transient"


def _conf() -> dict:
    from config import load_settings
    return load_settings().get("reference_archive") or {}


def status() -> dict:
    """給管理卡（phase 3）的即時狀態。used_gb 只讀快取，**絕不**在這裡觸發 NAS 掃描。"""
    return {"busy": _busy, "current_rid": _current_rid, "paused_reason": _paused_reason,
            "used_gb": _size_cache[1], "ytdlp_present": os.path.isfile(_YTDLP)}


# ── tick（scheduler 每 60s 呼叫；只做便宜的判斷與派工）─────────


def tick() -> None:
    global _busy, _task
    if _busy:
        return
    try:
        from core.topology import is_master_machine
        if not is_master_machine():              # 先擋機隊：load_settings 每次真讀檔，別讓 8 台每分鐘白讀
            return
    except Exception:
        return
    conf = _conf()
    if not conf.get("enabled"):
        return
    per_hour = max(1, int(conf.get("per_hour") or 6))
    if time.time() - _last_start < 3600 / per_hour:
        return                                   # 節流：還沒到下一支的時間
    _busy = True
    try:
        _task = asyncio.create_task(_run_one_guarded())
    except Exception:
        _busy = False                            # 建 task 失敗不能讓旗標卡死 runner
        raise


async def _run_one_guarded() -> None:
    global _busy
    try:
        await _run_one()
    except Exception:
        logger.exception("[ref_archiver] 封存工作異常")
    finally:
        _busy = False


# ── 主工作：挑一支 → 下載 → 轉檔 → 五圖 → info.json ────────────


async def _run_one() -> None:
    global _last_start, _paused_reason
    from core.db_guard import db_factory_or_503
    try:
        factory = db_factory_or_503()
    except Exception:
        return                                   # DB 沒上線，下一 tick 再說

    conf = _conf()
    dest_root = str(conf.get("dir") or "").strip()
    if not dest_root:
        return

    await _weekly_selfupdate()          # 便宜（marker stat）；週日首輪才真的跑 -U
    # 挑片在前：佇列空的時候別去付 NAS 掃描與工具自舉的錢
    await _mark_unsupported(factory)
    ref = await _pick_next(factory)
    if not ref:
        _last_start = time.time()                # 空轉也吃節流，別每 60s 全表 SELECT 一次
        return
    rid, url = ref
    _last_start = time.time()                    # 節流從「開始」算（含失敗，避免失敗風暴）

    if not await asyncio.to_thread(_ensure_ytdlp):
        return                                   # 抓不到工具，下一 tick 再試
    used_gb = await asyncio.to_thread(_dir_size_gb_cached, dest_root)
    if used_gb is not None and used_gb >= float(conf.get("max_gb") or 200):
        _paused_reason = f"封存空間已用 {used_gb:.0f}GB（上限 {conf.get('max_gb')}GB），已暫停"
        logger.warning("[ref_archiver] %s", _paused_reason)
        return
    _paused_reason = ""
    await _archive_ref(factory, rid, url, conf)


async def archive_one(rid: str) -> bool:
    """指定單支建檔（管理卡「立即建檔/重試」與測試用；不經節流與挑片）。

    與排程共用 _busy —— 排程正在下載時按「立即建檔」不會變成兩隻 yt-dlp
    同時打同一個對外 IP（節流存在的唯一理由就是防這件事）。"""
    global _busy
    if _busy:
        return False
    _busy = True
    try:
        from core.db_guard import db_factory_or_503
        from db.models import PreprodReference
        try:
            factory = db_factory_or_503()
        except Exception:
            return False
        conf = _conf()
        if not str(conf.get("dir") or "").strip():
            return False
        if not await asyncio.to_thread(_ensure_ytdlp):
            return False
        async with factory() as s:
            ref = await s.get(PreprodReference, rid)
            if not ref or not (ref.url or "").lower().startswith(("http://", "https://")):
                return False
            url = ref.url
        return await _archive_ref(factory, rid, url, conf)
    finally:
        _busy = False


# ── 封存資料夾命名 `{片名}_{品牌}`（2026-08-06 owner 指定）───────
# 規則正本在 core.project_folders（與影像紀錄/提案庫共用）。片庫是跨專案共用
# 資產（一支片可被多個專案引用），所以按**片**命名而不是按專案。
# 資料夾名不另存欄位 —— archive_path 的 dirname 就是它。


def _brand_of(ref) -> str:
    """facets.brand 是多選 → 取第一個當資料夾名的一部分（名字不宜過長）。"""
    v = (ref.facets or {}).get("brand")
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v or "")


def archive_folder_name(ref, taken=None) -> str:
    """這支片該用的封存資料夾名。片名空 → 退回影片 ID → "reference"。"""
    from core.project_folders import make_reference_folder_name
    return make_reference_folder_name(
        ref.title or "", _brand_of(ref),
        fallback=ref.video_id or "", taken=taken)


async def _taken_folder_names(factory, exclude_rid: str = "") -> set:
    """同 root 底下已被佔用的資料夾名（撞名補 -2 用）—— 從既有 archive_path 反推。"""
    from sqlalchemy import select
    from db.models import PreprodReference
    async with factory() as s:
        rows = (await s.execute(
            select(PreprodReference.id, PreprodReference.archive_path)
            .where(PreprodReference.archive_path.isnot(None)))).all()
    return {os.path.basename(os.path.dirname(p)) for rid_, p in rows
            if p and rid_ != exclude_rid}


async def _folder_name_for(factory, rid: str) -> str:
    """建檔時算資料夾名；ref 讀不到（極罕見）→ 退回 rid，不擋建檔。"""
    from db.models import PreprodReference
    async with factory() as s:
        ref = await s.get(PreprodReference, rid)
        if not ref:
            return rid
        return archive_folder_name(
            ref, await _taken_folder_names(factory, exclude_rid=rid))


async def rename_archive_folder(session, ref) -> tuple:
    """片名／品牌改動 → 封存資料夾跟著改名 + `archive_path` 同步。
    回 `(changed, warning)`，**不 commit**（與呼叫端的欄位更新同一交易）。

    還沒建檔（archive_path 空）→ no-op，之後建檔自然用新名。改名失敗
    （檔案被開著）→ 回 warning，資料夾與 archive_path 都維持原狀。
    """
    from sqlalchemy import select
    from core.drive_map import to_local_path
    from core.project_folders import remap_prefix, rename_dir
    from db.models import PreprodReference

    old_path = ref.archive_path or ""
    if not old_path:
        return False, ""
    old_dir = os.path.dirname(old_path)
    rows = (await session.execute(
        select(PreprodReference.id, PreprodReference.archive_path)
        .where(PreprodReference.archive_path.isnot(None)))).all()
    taken = {os.path.basename(os.path.dirname(p)) for rid_, p in rows
             if p and rid_ != ref.id}
    new_name = archive_folder_name(ref, taken)
    if new_name == os.path.basename(old_dir):
        return False, ""
    new_dir = os.path.join(os.path.dirname(old_dir), new_name)

    ok, err = await asyncio.to_thread(
        rename_dir, to_local_path(old_dir), to_local_path(new_dir))
    if not ok:
        return False, f"封存資料夾改名失敗（{err}）— 資料夾維持舊名"
    ref.archive_path = remap_prefix(old_path, old_dir, new_dir)
    return True, ""


async def migrate_folder_names() -> int:
    """既有封存資料夾（舊命名＝uuid）→ `{片名}_{品牌}`。冪等，回改名筆數。

    master-only（NAS 憑證只在 master）+ 逐筆容錯：單支改不動（檔案被開著、
    片名空）就跳過，下次啟動再試，絕不擋 startup。改名與 archive_path 更新
    同一個 session commit —— 中途掛掉不會留下「資料夾已改、DB 還指舊路徑」。
    """
    from core.topology import is_master_machine
    if not is_master_machine():
        return 0
    from db.session import get_session_factory
    factory = get_session_factory()
    if not factory:
        return 0
    from sqlalchemy import select
    from db.models import PreprodReference

    async with factory() as s:
        ids = (await s.execute(
            select(PreprodReference.id)
            .where(PreprodReference.archive_path.isnot(None)))).scalars().all()
    migrated = 0
    for rid in ids:
        try:
            async with factory() as s:
                ref = await s.get(PreprodReference, rid)
                if not ref or not ref.archive_path:
                    continue
                changed, _warn = await rename_archive_folder(s, ref)
                if changed:
                    await s.commit()
                    migrated += 1
        except Exception as e:
            logger.warning("[ref_archiver] 資料夾改名遷移跳過 %s：%s", rid, e)
    if migrated:
        logger.info("[ref_archiver] 封存資料夾改名遷移：%d 支片改成 片名_品牌", migrated)
    return migrated


async def _archive_ref(factory, rid: str, url: str, conf: dict) -> bool:
    """單支建檔核心（挑片路徑與指定路徑共用）。"""
    global _current_rid
    dest_root = str(conf.get("dir") or "").strip()
    _current_rid = rid
    logger.info("[ref_archiver] 開始建檔 %s（%s）", rid, url[:80])
    await _set_status(factory, rid, "downloading")

    dest_dir = os.path.join(dest_root, await _folder_name_for(factory, rid))
    max_h = int(conf.get("max_height") or 720)
    ok, err, video_path = await _download(url, dest_dir, max_h)
    if not ok and _classify_failure(err) == "extractor":
        # YouTube 改版的典型症狀 → 自我更新 → 當下立刻重試一次（owner 要求的自救核心）
        if await _selfupdate():
            logger.info("[ref_archiver] 抽取器失效 → yt-dlp 已自我更新，立刻重試 %s", rid)
            ok, err, video_path = await _download(url, dest_dir, max_h)
    if not ok:
        kind = _classify_failure(err)
        # dead 不算「連敗」：復查循環（30 天一次）會讓 tries 永久累積，
        # 之後真的抽取器失效時門檻永遠對不上 → 該告警的反而不告警
        tries = await _set_status(factory, rid,
                                  "unavailable" if kind == "dead" else "retry",
                                  error=(err or "")[:1500],
                                  bump_tries=(kind != "dead"))
        _current_rid = ""
        logger.warning("[ref_archiver] %s 失敗（%s，第 %d 次）：%s",
                       rid, kind, tries, (err or "")[:200])
        if kind != "dead" and tries >= _ALERT_TRIES:
            await _alert_failure(factory, rid, err or "", tries)
        return False

    probe = await _probe(video_path)
    video_path = await _ensure_h264_mp4(video_path, probe)
    if probe.get("vcodec") != "h264":            # 轉檔後長度不變，但檔案換了 → 只在必要時重 probe
        probe = await _probe(video_path)
    stills = await _extract_stills(video_path, dest_dir, probe.get("duration") or 0.0)
    await _write_info_json(factory, rid, dest_dir, video_path)
    await _replace_auto_shots(factory, rid, stills)
    await _set_status(factory, rid, "done", path=video_path)
    _current_rid = ""
    logger.info("[ref_archiver] %s 建檔完成：%s（截圖 %d 張）",
                rid, os.path.basename(video_path), len(stills))
    return True


async def _mark_unsupported(factory) -> None:
    """facebook 一律標 unavailable（需登入、成功率過低）—— 一句 UPDATE，不佔挑片隊。"""
    from sqlalchemy import or_, update
    from db.models import PreprodReference
    async with factory() as s:
        await s.execute(
            update(PreprodReference)
            .where(PreprodReference.provider == "facebook",
                   or_(PreprodReference.archive_status.is_(None),
                       PreprodReference.archive_status.in_(("pending", "retry"))))
            .values(archive_status="unavailable",
                    archive_error="來源不支援封存（Facebook 影片需登入，成功率過低）"))
        await s.commit()


async def _pick_next(factory):
    """挑下一支（全 SQL、limit 1）：NULL（新片，免 backfill）/ pending /
    冷卻過的 retry / **卡超過 2 小時的 downloading**（行程重啟或斷電的殘留 ——
    不撿回來就永久消失於隊列；殘檔由下載端 --force-overwrites 蓋掉）。"""
    from datetime import timedelta
    from sqlalchemy import and_, case, func as safunc, or_, select
    from db.models import PreprodReference
    # 時間比較全部用 DB 端 func.now()（timestamptz）—— Python 端 aware datetime
    # 進到「now - interval」運算式會被降成 naive timestamp，asyncpg 直接拒收（實測踩過）
    now_sql = safunc.now()
    # retry 退避階梯（_BACKOFF_H 唯一正本）—— SQL CASE，挑片仍是一句 limit 1
    cooldown = case(
        *[(PreprodReference.archive_tries <= t, timedelta(hours=h)) for t, h in _BACKOFF_H],
        else_=timedelta(hours=_BACKOFF_MAX_H))
    async with factory() as s:
        row = (await s.execute(
            select(PreprodReference.id, PreprodReference.url)
            .where(
                PreprodReference.url.ilike("http%"),
                or_(PreprodReference.provider.is_(None),
                    PreprodReference.provider != "facebook"),
                or_(PreprodReference.archive_status.is_(None),
                    PreprodReference.archive_status == "pending",
                    and_(PreprodReference.archive_status == "retry",
                         PreprodReference.updated_at + cooldown < now_sql),
                    # 已死的每 30 天復查（平台誤判/重新公開的機會窗）
                    and_(PreprodReference.archive_status == "unavailable",
                         PreprodReference.updated_at
                         + timedelta(days=_RECHECK_DEAD_DAYS) < now_sql),
                    # 行程重啟/斷電殘留的 downloading（不撿回=永久消失於隊列）
                    and_(PreprodReference.archive_status == "downloading",
                         PreprodReference.updated_at + timedelta(hours=2) < now_sql)))
            .order_by(PreprodReference.created_at)
            .limit(1)
        )).first()
    return (row[0], row[1]) if row else None


async def _set_status(factory, rid: str, status_: str, *, error: str = "",
                      path: str = "", bump_tries: bool = False) -> int:
    """寫狀態；回 **bump 之後**的累計失敗次數（告警門檻用）。done 歸零 tries。

    「排除建檔」是使用者意志：下載途中被標 excluded，runner 結束時**不得覆寫**
    （否則使用者按了排除、幾分鐘後狀態又自己變回來 —— 實測踩過的競態）。"""
    from db.models import PreprodReference
    async with factory() as s:
        ref = await s.get(PreprodReference, rid)
        if not ref:
            return 0
        if ref.archive_status == "excluded":
            return ref.archive_tries or 0
        ref.archive_status = status_
        ref.archive_error = error or None
        if path:
            ref.archive_path = path
            ref.archived_at = datetime.now(timezone.utc)
        if status_ == "done":
            ref.archive_tries = 0
        elif bump_tries:
            ref.archive_tries = (ref.archive_tries or 0) + 1
        ref.updated_at = datetime.now(timezone.utc)
        tries = ref.archive_tries or 0
        await s.commit()
        return tries


_last_alert_at = 0.0


async def _alert_failure(factory, rid: str, err: str, tries: int) -> None:
    """連敗告警（走既有 alert_webhook + email relay；best-effort 不炸 runner）。

    **全域節流 6 小時**：YouTube 改版是「全庫同時壞」的故障型態，per-row 門檻
    會變成每支片各發一則 —— 同一波故障只該吵一次（intel/social runner 同哲學）。"""
    global _last_alert_at
    if time.time() - _last_alert_at < _SELFUPDATE_MIN_GAP:
        return
    _last_alert_at = time.time()
    from db.models import PreprodReference
    try:
        async with factory() as s:
            ref = await s.get(PreprodReference, rid)
            title = (ref.title or ref.url or rid) if ref else rid
        from notifier import notify_tab_async
        await notify_tab_async("archive_failed", title=title,
                               error=(err or "")[:300], tries=tries)
    except Exception:
        logger.exception("[ref_archiver] 告警發送失敗（不影響 runner）")


async def _weekly_selfupdate() -> None:
    """每週日固定 -U 一次（不等壞掉才更新）。marker 檔記上次日期，跨重啟仍準。"""
    now = datetime.now()
    if now.weekday() != 6 or not os.path.isfile(_YTDLP):
        return
    marker = os.path.join(_TOOLS_DIR, "ytdlp_weekly.txt")
    today = now.strftime("%Y-%m-%d")
    try:
        if os.path.isfile(marker) and open(marker, encoding="ascii").read().strip() == today:
            return
    except OSError:
        pass
    # marker 記「今天已嘗試」不是「今天已成功」—— 失敗若不寫，接下來整個週日
    # 每輪都會歸零守衛再打一次 GitHub（-U 失敗常見：網路/檔案被鎖/防毒），沒人會發現
    try:
        with open(marker, "w", encoding="ascii") as f:
            f.write(today)
    except OSError:
        pass
    global _last_selfupdate
    _last_selfupdate = 0            # 週更不受 6 小時間隔限制
    await _selfupdate()


_last_selfupdate = 0.0


async def _selfupdate() -> bool:
    """yt-dlp.exe -U 自我更新（6 小時內不重複 —— 失敗風暴時別狂打 GitHub）。"""
    global _last_selfupdate
    if time.time() - _last_selfupdate < _SELFUPDATE_MIN_GAP:
        return False
    _last_selfupdate = time.time()
    from core.subproc import run_capture
    rc, out, err_ = await run_capture([_YTDLP, "-U"], timeout=300)
    text = (out or b"").decode("utf-8", errors="replace")[:200]
    logger.info("[ref_archiver] yt-dlp -U（rc=%d）：%s", rc, text)
    return rc == 0


# ── yt-dlp 自舉與下載 ───────────────────────────────────────


def _ensure_ytdlp() -> bool:
    """tools/yt-dlp.exe 不在就從 GitHub 下載（首次自舉；不進 git / OTA）。"""
    if os.path.isfile(_YTDLP):
        return True
    try:
        os.makedirs(_TOOLS_DIR, exist_ok=True)
        tmp = _YTDLP + ".part"
        logger.info("[ref_archiver] 下載 yt-dlp.exe …")
        with urllib.request.urlopen(_YTDLP_URL, timeout=60) as resp, open(tmp, "wb") as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(tmp, _YTDLP)
        logger.info("[ref_archiver] yt-dlp.exe 就緒（%d bytes）", os.path.getsize(_YTDLP))
        return True
    except Exception as e:
        logger.warning("[ref_archiver] yt-dlp 自舉失敗（下一 tick 重試）：%s", e)
        return False


def _vimeo_player_url(url: str):
    """vimeo.com/<id>[/<hash>] → player.vimeo.com 端點（沒中回 None）。

    Vimeo 主站抽取器要先打 OAuth token API —— Vimeo 已撤銷 yt-dlp 內建的
    client 憑證（401，最新 stable 也救不了）；player 端點走 config JSON，
    不經 OAuth，公開片與帶私密雜湊的未公開片實測都通。"""
    m = re.match(r"https?://(?:www\.)?vimeo\.com/(\d+)(?:/([0-9a-fA-F]+))?/?(?:[?#]|$)", url)
    if not m:
        return None
    vid, h = m.groups()
    return f"https://player.vimeo.com/video/{vid}" + (f"?h={h}" if h else "")


async def _download(url: str, dest_dir: str, max_height: int):
    """yt-dlp 下載 → (ok, stderr, 影片檔路徑)。輸出固定叫 video.<ext>。"""
    from core.subproc import run_capture
    os.makedirs(dest_dir, exist_ok=True)
    out_tmpl = os.path.join(dest_dir, "video.%(ext)s")
    player = _vimeo_player_url(url)
    args = [
        _YTDLP, player or url,
        "-f", f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b",
        "--merge-output-format", "mp4",
        "--ffmpeg-location", _FFMPEG,
        "-o", out_tmpl,
        "--no-playlist", "--no-progress", "--force-overwrites",
        "--socket-timeout", "30", "--retries", "3",
    ]
    if player:
        args += ["--referer", "https://vimeo.com/"]   # player 端點驗 Referer
    rc, _out, err = await run_capture(args, timeout=_DL_TIMEOUT)
    err_text = (err or b"").decode("utf-8", errors="replace")
    if rc != 0:
        return False, err_text or f"yt-dlp 結束碼 {rc}", ""
    for name in os.listdir(dest_dir):
        if name.startswith("video.") and not name.endswith((".part", ".ytdl")):
            return True, "", os.path.join(dest_dir, name)
    return False, "yt-dlp 回報成功但找不到輸出檔", ""


async def _probe(video_path: str) -> dict:
    """ffprobe 一次，(codec, duration) 兩處共用 —— 別對同一個檔 probe 兩遍。"""
    from core.subproc import run_capture
    _rc, out, _err = await run_capture(
        [_FFPROBE, "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", video_path], timeout=60)
    try:
        info = json.loads(out.decode("utf-8", errors="replace"))
        return {
            "vcodec": next((s.get("codec_name") for s in info.get("streams", [])
                            if s.get("codec_type") == "video"), ""),
            "duration": float(info.get("format", {}).get("duration") or 0),
        }
    except Exception:
        return {"vcodec": "", "duration": 0.0}


async def _ensure_h264_mp4(video_path: str, probe: dict) -> str:
    """統一成瀏覽器可直播的 H.264/AAC mp4；已符合就原檔保留（不重壓、不失真）。"""
    from core.subproc import run_capture
    vcodec = probe.get("vcodec") or ""
    if vcodec == "h264" and video_path.lower().endswith(".mp4"):
        return video_path
    logger.info("[ref_archiver] 轉檔 %s（原編碼 %s）", os.path.basename(video_path), vcodec or "?")
    fixed = os.path.join(os.path.dirname(video_path), "video.mp4")
    tmp = fixed + ".transcoding.mp4"
    rc, _o, err = await run_capture(
        [_FFMPEG, "-y", "-i", video_path, "-c:v", "libx264", "-preset", "fast",
         "-crf", "23", "-c:a", "aac", "-movflags", "+faststart", tmp],
        timeout=_DL_TIMEOUT)
    if rc != 0:
        logger.warning("[ref_archiver] 轉檔失敗，保留原下載檔：%s",
                       (err or b"")[:200].decode("utf-8", errors="replace"))
        try:
            os.remove(tmp)
        except OSError:
            pass
        return video_path
    if os.path.abspath(video_path) != os.path.abspath(fixed):
        try:
            os.remove(video_path)
        except OSError:
            pass
    os.replace(tmp, fixed)
    return fixed


# ── 五張等距截圖 ─────────────────────────────────────────────


async def _extract_stills(video_path: str, dest_dir: str, duration: float) -> list:
    """片長/6 取 1..5 段點 → still_1..5.jpg。回 [(檔路徑, 'M:SS'), …]。

    `-nostdin` 與 `-noaccurate_seek` 抄 core_engine.generate_film_strip 的既有調校：
    前者防 pythonw 無 console 掛住，後者在 long-GOP 長片上抽圖快非常多。"""
    from core.subproc import run_capture
    if duration <= 0:
        return []
    stills = []
    for i in range(1, _STILLS + 1):
        t = duration * i / (_STILLS + 1)
        path = os.path.join(dest_dir, f"still_{i}.jpg")
        rc, _o, _e = await run_capture(
            [_FFMPEG, "-nostdin", "-y", "-noaccurate_seek", "-ss", f"{t:.2f}",
             "-i", video_path, "-frames:v", "1", "-q:v", "3", path], timeout=120)
        if rc == 0 and os.path.isfile(path):
            stills.append((path, f"{int(t // 60)}:{int(t % 60):02d}"))
    return stills


async def _replace_auto_shots(factory, rid: str, stills: list) -> None:
    """五張截圖進既有截圖牆（created_key=auto-archive，重跑先清舊 → 冪等）。
    手動貼的截圖排前面，系統的墊底（sort_order 接在最大值之後）。"""
    if not stills:
        return
    import uuid as _uuid
    from sqlalchemy import func as safunc, select
    from core.image_utils import save_webp_or_none
    from db.models import PreprodReferenceShot
    from routers.api_references import _UPLOAD_BASE, _delete_shot_file

    async with factory() as s:
        old = (await s.execute(
            select(PreprodReferenceShot)
            .where(PreprodReferenceShot.reference_id == rid,
                   PreprodReferenceShot.created_key == _AUTO_KEY))).scalars().all()
        for shot in old:
            _delete_shot_file(shot.image_url or "")
            await s.delete(shot)
        base = (await s.execute(
            select(safunc.coalesce(safunc.max(PreprodReferenceShot.sort_order), 0))
            .where(PreprodReferenceShot.reference_id == rid))).scalar() or 0
        web_dir = os.path.join(_UPLOAD_BASE, "references", rid)
        for n, (jpg, timecode) in enumerate(stills, start=1):
            with open(jpg, "rb") as f:
                content = f.read()
            sid = _uuid.uuid4().hex
            saved = await asyncio.to_thread(save_webp_or_none, content, web_dir, sid, 1800)
            if saved is None:
                continue
            s.add(PreprodReferenceShot(
                id=sid, reference_id=rid,
                image_url=f"/uploads/references/{rid}/{os.path.basename(saved)}",
                timecode=timecode, caption="", sort_order=base + n,
                created_by="系統封存", created_key=_AUTO_KEY))
        await s.commit()


async def _write_info_json(factory, rid: str, dest_dir: str, video_path: str) -> None:
    """人類可讀的建檔中繼資料 —— 脫離系統時資料夾本身仍是完整檔案庫。"""
    from db.models import PreprodReference
    from routers.api_references import _norm_facets, _norm_research
    async with factory() as s:
        ref = await s.get(PreprodReference, rid)
        if not ref:
            return
        info = {
            "title": ref.title or "", "url": ref.url or "",
            "note": ref.note or "", "description": ref.description or "",
            "facets": _norm_facets(ref.facets),
            "research": _norm_research(ref.research) if ref.research else None,
            "video_file": os.path.basename(video_path),
            "archived_at": datetime.now(timezone.utc).isoformat(),
        }
    def _write():
        with open(os.path.join(dest_dir, "info.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
    await asyncio.to_thread(_write)


# ── 磁碟用量（快取，NAS 遞迴掃描不便宜）───────────────────────

_size_cache: tuple = (0.0, None)     # (量測時間, GB)


def _dir_size_gb_cached(root: str):
    global _size_cache
    ts, val = _size_cache
    if val is not None and time.time() - ts < 3600:
        return val
    try:
        total = 0
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
        gb = total / (1 << 30)
    except OSError:
        return None                   # NAS 不可達 → 這輪不擋（下載本身會失敗並記錄）
    _size_cache = (time.time(), gb)
    return gb
