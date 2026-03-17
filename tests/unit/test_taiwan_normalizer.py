"""
Layer 1 — 台灣正音引擎 (taiwan_normalizer) 單元測試。

patch 策略：taiwan_normalizer.py 使用模組層級常數 DICT_PATH，
因此用 monkeypatch.setattr 將它指向 tmp_dict fixture 的臨時檔案路徑。
"""
import json
import pytest
from utils.taiwan_normalizer import normalize_for_taiwan_tts


@pytest.fixture(autouse=True)
def _patch_dict_path(tmp_dict, monkeypatch):
    """Redirect DICT_PATH to the tmp_dict fixture for every test."""
    monkeypatch.setattr("utils.taiwan_normalizer.DICT_PATH", str(tmp_dict))


# ── 1. 單一 vocab 替換 ──────────────────────────────────────
def test_vocab_single():
    """視頻 → 影片"""
    assert normalize_for_taiwan_tts("這部視頻很好看") == "這部影片很好看"


# ── 2. 多個 vocab 同時替換 ───────────────────────────────────
def test_vocab_multiple():
    """視頻 + 軟件 同時替換"""
    result = normalize_for_taiwan_tts("用這個軟件看視頻")
    assert "軟體" in result
    assert "影片" in result
    assert "軟件" not in result
    assert "視頻" not in result


# ── 3. pronunciation_hacks 替換 ──────────────────────────────
def test_pronunciation_hack():
    """垃圾 → 勒色"""
    assert normalize_for_taiwan_tts("倒垃圾") == "倒勒色"


# ── 4. 無命中時原樣回傳 ──────────────────────────────────────
def test_no_match_passthrough():
    """不包含任何字典詞彙時，文字原樣回傳。"""
    original = "今天天氣很好"
    assert normalize_for_taiwan_tts(original) == original


# ── 5. 空字串不 crash ────────────────────────────────────────
def test_empty_string():
    assert normalize_for_taiwan_tts("") == ""


# ── 6. 最長優先匹配 ─────────────────────────────────────────
def test_longest_match_first():
    """「手機APP」(3+3=6 字) 應優先於單獨匹配「手機」等短詞。
    tmp_dict 中只有「手機APP」→「手機 App」，不含「手機」單獨映射，
    所以此測試確認長詞被完整替換。"""
    result = normalize_for_taiwan_tts("下載手機APP")
    assert result == "下載手機 App"


# ── 7. 熱更新（修改 json 後不重啟也生效）─────────────────────
def test_hot_reload(tmp_dict):
    """修改字典 json 後，下次呼叫自動生效。"""
    # 初始：視頻 → 影片
    assert "影片" in normalize_for_taiwan_tts("視頻")

    # 動態新增一筆
    with open(tmp_dict, "r", encoding="utf-8") as f:
        d = json.load(f)
    d["vocab_mapping"]["網絡"] = "網路"
    with open(tmp_dict, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)

    # 不重啟，直接呼叫 → 新詞生效
    assert normalize_for_taiwan_tts("網絡") == "網路"


# ── 8. vocab + pronunciation 同一句話 ────────────────────────
def test_combined():
    """同一句話同時觸發 vocab_mapping 和 pronunciation_hacks。"""
    result = normalize_for_taiwan_tts("視頻裡有垃圾")
    assert "影片" in result
    assert "勒色" in result
    assert "視頻" not in result
    assert "垃圾" not in result
