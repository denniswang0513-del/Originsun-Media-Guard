"""core/drive_map.py — 任務進件路徑層：磁碟對應翻譯 + 路徑語法快篩。

enqueue 咽喉（core/worker.enqueue_job）共用 `_JOB_PATH_FIELDS` registry：
先 translate_job_request 把磁碟代號翻成 UNC，再 first_invalid_job_path
擋語法垃圾（黏路徑等）— 找「路徑驗證在哪」就是這裡。

動機（2026-07-21 煥民新村備份 6 連敗）：Windows 對映磁碟綁定登入 session、
且各機不一定都有掛——任務在哪台跑就用哪台的磁碟視角，T:\\ 一缺整包炸，
連錯誤 log 都寫不出來。與其要求所有人改打 UNC，讓系統認得「T: 就是
Project_Longterm」：任務進件時把磁碟代號路徑翻成對應 UNC，機器有沒有
掛磁碟都不影響。

- DEFAULT_DRIVE_MAP：公司慣例（隨 OTA 下發，全機隊一致）。
- settings.json 的 drive_map 區可覆寫/擴充（per-machine；字母設空字串 = 停用該字母）。
- 本機實體磁碟（C/D/E/G/H 讀卡槽等）不在 map 內 → 原樣通過，不受影響。
"""
from __future__ import annotations

import os
import re
from typing import Callable

# 公司網路磁碟慣例（2026-07-21 自 master `net use` 盤點）
DEFAULT_DRIVE_MAP = {
    "N": r"\\192.168.1.132\Originsun",
    "P": r"\\192.168.1.132\00_Inbox",
    "Q": r"\\192.168.1.132\PreProduction",
    "R": r"\\192.168.1.132\Project_ShortTerm",
    "S": r"\\192.168.1.132\Project_Midterm",
    "T": r"\\192.168.1.132\Project_Longterm",
    "U": r"\\192.168.1.132\ProjectYuan",
    "V": r"\\192.168.1.130\storage 01",
}

_DRIVE_RE = re.compile(r"^([A-Za-z]):([\\/]|$)")


def parse_override_items(raw: dict) -> list:
    """settings 覆寫 dict → [(字母, 清洗後 UNC 或 ""=墓碑)]。字母大寫正規化、
    去冒號、非單一英文字母跳過；值去頭尾空白與尾斜線。
    effective_map 與 api_utils._scan_drive_mappings（前端表）共用 — 兩邊清洗
    規則一致，同一筆設定不會後端乾淨前端帶尾斜線。"""
    out = []
    for k, v in (raw or {}).items():
        letter = str(k).strip().rstrip(":").upper()
        if len(letter) == 1 and letter.isalpha():
            out.append((letter, str(v or "").strip().rstrip("\\/")))
    return out


_map_cache: tuple = (None, None)   # (settings mtime, merged map) — 每請求免重讀 settings.json


def effective_map() -> dict:
    """程式預設 + settings.json `drive_map` 覆寫（空字串=墓碑移除該字母）。
    settings 讀不到時退純預設。以 settings.json mtime 做快取 — list_dir 等
    高頻端點每次呼叫不再付磁碟讀+parse（modal 存檔會動 mtime，即時失效）。"""
    global _map_cache
    try:
        import config
        mtime = os.path.getmtime(config._SETTINGS_FILE)
        if _map_cache[0] == mtime:
            return _map_cache[1]
        merged = dict(DEFAULT_DRIVE_MAP)
        for letter, v in parse_override_items(config.load_settings().get("drive_map")):
            if v:
                merged[letter] = v
            else:
                merged.pop(letter, None)
        _map_cache = (mtime, merged)
        return merged
    except Exception:
        return _map_cache[1] if _map_cache[1] is not None else dict(DEFAULT_DRIVE_MAP)


def translate_path(path: str, mapping: dict | None = None) -> str:
    """"T:\\a\\b" → "\\\\192.168.1.132\\Project_Longterm\\a\\b"。
    不在 map 的字母（本機碟）與非磁碟代號路徑（UNC/相對）原樣回。"""
    p = str(path or "")
    m = _DRIVE_RE.match(p)
    if not m:
        return p
    root = (mapping if mapping is not None else effective_map()).get(m.group(1).upper())
    if not root:
        return p
    rest = p[2:].lstrip("\\/")
    return f"{root}\\{rest}" if rest else root


_PATH_BAD_CHARS = set('<>"|?*')


def invalid_path_reason(path: str) -> str | None:
    """Windows 路徑語法快篩 — 回中文原因字串，合法回 None。

    2026-07-23 實案：書籤帶入 C:/A_TEST_PROXY 後使用者在尾端貼上 S:/... 沒清空
    → 黏成 C:/A_TEST_PROXYS:/...，派發後全機隊 WinError 123。這類垃圾要在
    進件當下擋（400 + 明確訊息），不是到執行機才炸。"""
    p = str(path or "")
    if not p:
        return None
    # 冒號只允許出現在磁碟代號位（index 1）；UNC 開頭 \\ 無冒號
    for i, ch in enumerate(p):
        if ch == ":" and i != 1:
            return f"第 {i + 1} 字元含非法冒號（像是兩段路徑黏在一起）"
        if ch in _PATH_BAD_CHARS:
            return f"含非法字元「{ch}」"
        if ord(ch) < 32:
            return "含控制字元"
    return None


def make_translator() -> Callable[[str], str]:
    """回 tr(path)：以當下 effective_map 翻譯 — 同步端點進件用。
    需要變動明細（[UNC] log）的走 translate_job_request。"""
    mapping = effective_map()
    return lambda v: translate_path(v, mapping)


# 各任務型別的路徑欄位（enqueue_job 中央咽喉用 — HTTP/排程/書籤三條進件路都經過它）。
# 形狀： "str"=單一路徑欄、"list"=路徑清單、"pairs"=[(a,b)] 兩側皆路徑、
#        "cards"=[(名稱, 路徑)] 只翻第二欄。
_JOB_PATH_FIELDS: dict = {
    "backup":     [("local_root", "str"), ("nas_root", "str"), ("proxy_root", "str"),
                   ("report_output", "str"), ("cards", "cards")],
    "transcode":  [("sources", "list"), ("dest_dir", "str")],
    "concat":     [("sources", "list"), ("dest_dir", "str")],
    "verify":     [("pairs", "pairs")],
    "transcribe": [("sources", "list"), ("dest_dir", "str")],
    "report":     [("source_dir", "str"), ("output_dir", "str"), ("nas_root", "str"),
                   ("exclude_dirs", "list")],
    "tts":        [("output_dir", "str"), ("reference_audio", "str")],
}


def _iter_job_paths(req, task_type: str):
    """依 _JOB_PATH_FIELDS 展開 request 的所有路徑值 → (欄位名, 路徑)。
    ⚠ kind 分派與 translate_job_request（下方）鏡像 — 加新 kind 兩處同步。"""
    for name, kind in _JOB_PATH_FIELDS.get(task_type, []):
        val = getattr(req, name, None)
        if not val:
            continue
        if kind == "str":
            yield name, val
        elif kind == "pairs":
            for a, b in val:
                yield name, a
                yield name, b
        elif kind == "cards":
            for _n, p in val:
                yield name, p
        elif kind == "list":
            for p in val:
                yield name, p


def first_invalid_job_path(req, task_type: str) -> str | None:
    """回第一個語法不合法路徑的中文錯誤（欄位點名），全合法回 None。
    2026-07-23 實案（C:/A_TEST_PROXYS: 黏路徑）的進件防線。"""
    for name, p in _iter_job_paths(req, task_type):
        why = invalid_path_reason(p)
        if why:
            return f"{name} 路徑格式不正確：{why} — {str(p)[:80]}"
    return None


def translate_job_request(req, task_type: str) -> list:
    """就地把任務 request 的路徑欄位翻成 UNC，回傳變動明細 ["原 → 新", ...]
    （無對應型別 → []，如 drone_meta/timeline_export 刻意不翻——路徑綁定
    設定它的那台機器）。欄位不存在則跳過（如 TtsRequest 沒有 reference_audio）。
    ⚠ kind 分派與 _iter_job_paths（上方）鏡像 — 加新 kind 兩處同步。"""
    fields = _JOB_PATH_FIELDS.get(task_type)
    if not fields:
        return []
    mapping = effective_map()
    changes: list = []

    def tr(v):
        nv = translate_path(v, mapping)
        if nv != v:
            changes.append(f"{v} → {nv}")
        return nv

    for name, kind in fields:
        val = getattr(req, name, None)
        if not val:
            continue
        if kind == "str":
            setattr(req, name, tr(val))
        elif kind == "list":
            setattr(req, name, [tr(v) for v in val])
        elif kind == "pairs":
            setattr(req, name, [(tr(a), tr(b)) for a, b in val])
        elif kind == "cards":
            setattr(req, name, [(n, tr(p)) for n, p in val])
    return changes
