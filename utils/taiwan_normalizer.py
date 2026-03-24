"""
台灣正音引擎 — Originsun Media Guard Pro
即時讀取 taiwan_dict.json，依序進行用語轉換與發音校正。
字典檔案修改後無需重啟，下次呼叫自動生效。
"""
import json
import os
import re

DICT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "taiwan_dict.json")


def _load_dict() -> dict:
    """每次呼叫時即時讀取字典，支援熱更新。"""
    if not os.path.exists(DICT_PATH):
        return {"vocab_mapping": {}, "pronunciation_hacks": {}}
    try:
        with open(DICT_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"vocab_mapping": {}, "pronunciation_hacks": {}}


def normalize_for_taiwan_tts(text: str) -> str:
    """
    將輸入文字進行台灣正音處理：
    1. vocab_mapping — 大陸用語 → 台灣用語（例如「視頻」→「影片」）
    2. pronunciation_hacks — 發音校正（例如「垃圾」→「勒色」）

    替換順序：先長詞再短詞，避免子字串誤替換。
    """
    d = _load_dict()

    # Phase 1: 用語轉換
    vocab = d.get("vocab_mapping", {})
    if vocab:
        # Sort by key length descending to match longest first
        for original, replacement in sorted(vocab.items(), key=lambda x: len(x[0]), reverse=True):
            text = text.replace(original, replacement)

    # Phase 2: 發音校正
    hacks = d.get("pronunciation_hacks", {})
    if hacks:
        for original, replacement in sorted(hacks.items(), key=lambda x: len(x[0]), reverse=True):
            text = text.replace(original, replacement)

    return text
