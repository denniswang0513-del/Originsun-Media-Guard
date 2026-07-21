"""core/drive_map.py — 磁碟代號 ↔ UNC 對應（公司網路磁碟慣例的單一正本）。

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

import re

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


def effective_map() -> dict:
    """程式預設 + settings.json `drive_map` 覆寫（字母大寫正規化；
    override 值為空字串 → 移除該字母）。settings 讀不到時退純預設。"""
    merged = dict(DEFAULT_DRIVE_MAP)
    try:
        from config import load_settings
        override = load_settings().get("drive_map") or {}
        for k, v in override.items():
            letter = str(k).strip().rstrip(":").upper()
            if not (len(letter) == 1 and letter.isalpha()):
                continue
            v = str(v or "").strip().rstrip("\\/")
            if v:
                merged[letter] = v
            else:
                merged.pop(letter, None)
    except Exception:
        pass
    return merged


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
