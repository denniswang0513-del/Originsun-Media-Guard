# -*- coding: utf-8 -*-
"""發版時把版本說明接上 CHANGELOG（`publish_update.append_changelog`）。

為什麼值得一支測試：2026-09-19 的 /health 抓到工作日誌落後 **13 版** ——
以前那一段是 /health 每隔一陣子從發版 commit 重新生成的，中間沒跑到就一路空著。
現在改成發版順手寫，這支釘住三件事：
  1. 真的寫得進去，而且插在最前面（新的在上，同下面那一段的順序）
  2. 同一版不重複寫（重發同一版、或 /health 已經生成過那一行）
  3. 🔴 **寫不進去不可以讓發版失敗** —— 版號、ZIP、機隊都已經好了，
     為了一行工作日誌把整個發版判定成失敗是本末倒置
"""
import publish_update as pu
from tests.unit._srcscan import func_body, repo_src


def _write(tmp_path, monkeypatch, text):
    """把 CHANGELOG 指到暫存檔（`append_changelog` 以 publish_update.py 所在目錄為基準）。"""
    path = tmp_path / "CHANGELOG.md"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(pu, "CHANGELOG_FILE", str(path))
    monkeypatch.setattr(pu.os.path, "dirname", lambda _p: str(tmp_path))
    return path


HEAD = "# 工作日誌\n\n---\n\n"


def test_the_line_looks_like_the_ones_already_there():
    line = pu.changelog_line("2.5.58", "做了一件事", "2026-09-19")
    assert line == "- **2.5.58**（2026-09-19）— 做了一件事"


def test_a_multi_line_note_becomes_one_line():
    """--notes-file 可以有好幾行；那一段是清單，不是文件。"""
    line = pu.changelog_line("2.5.58", "第一件\n\n第二件\n", "2026-09-19")
    assert "\n" not in line
    assert "第一件；第二件" in line


def test_an_empty_note_still_gets_a_line():
    assert "微幅更新" in pu.changelog_line("2.5.58", "   ", "2026-09-19")


def test_the_newest_version_goes_on_top(tmp_path, monkeypatch):
    path = _write(tmp_path, monkeypatch, HEAD)
    assert pu.append_changelog("2.5.58", "舊的", "2026-09-19")
    assert pu.append_changelog("2.5.59", "新的", "2026-09-20")
    body = path.read_text(encoding="utf-8")
    assert body.index("2.5.59") < body.index("2.5.58"), "新的要在上面"
    assert pu.CHANGELOG_HEADING in body


def test_the_list_has_no_blank_lines_between_entries(tmp_path, monkeypatch):
    """下面 /health 生成的那一段是一版一行，兩段長得不一樣很難讀。"""
    path = _write(tmp_path, monkeypatch, HEAD)
    pu.append_changelog("2.5.58", "甲", "2026-09-19")
    pu.append_changelog("2.5.59", "乙", "2026-09-20")
    body = path.read_text(encoding="utf-8")
    assert "- **2.5.59**（2026-09-20）— 乙\n- **2.5.58**" in body


def test_the_same_version_is_never_written_twice(tmp_path, monkeypatch):
    """重發同一版（允許的），或 /health 已經生成過那一行。"""
    path = _write(tmp_path, monkeypatch, HEAD)
    pu.append_changelog("2.5.58", "第一次", "2026-09-19")
    pu.append_changelog("2.5.58", "第二次", "2026-09-19")
    assert path.read_text(encoding="utf-8").count("**2.5.58**") == 1
    assert "第二次" not in path.read_text(encoding="utf-8")


def test_an_older_generated_section_is_left_alone(tmp_path, monkeypatch):
    """/health 生成的那一大段不能被動到。"""
    old = HEAD + "## 自動生成：v1.10.1 → v2.5.44\n\n- **2.5.44**（2026-09-17）— 舊的那版\n"
    path = _write(tmp_path, monkeypatch, old)
    pu.append_changelog("2.5.58", "新的", "2026-09-19")
    body = path.read_text(encoding="utf-8")
    assert "## 自動生成：v1.10.1 → v2.5.44" in body and "舊的那版" in body
    assert body.index("2.5.58") < body.index("自動生成"), "新的那段要在上面"


def test_a_missing_changelog_does_not_kill_the_publish(tmp_path, monkeypatch):
    monkeypatch.setattr(pu, "CHANGELOG_FILE", "沒有這個檔.md")
    monkeypatch.setattr(pu.os.path, "dirname", lambda _p: str(tmp_path))
    assert pu.append_changelog("2.5.58", "x", "2026-09-19") is False   # 不丟例外


def test_a_write_failure_does_not_kill_the_publish(tmp_path, monkeypatch):
    """🔴 版號、ZIP、機隊都好了，不該為了一行工作日誌判定發版失敗。"""
    _write(tmp_path, monkeypatch, HEAD)

    def boom(*a, **k):
        raise OSError("磁碟滿了")
    monkeypatch.setattr("builtins.open", boom)
    assert pu.append_changelog("2.5.58", "x", "2026-09-19") is False


def test_it_runs_after_every_gate_has_passed():
    """中途任何一道 gate 沒過都會把版號回滾 —— 那時候不該留下一行說它發過。"""
    body = func_body(repo_src("publish_update.py"), "def main(")
    assert "append_changelog(" in body
    at = body.index("append_changelog(")
    for gate in ("_rollback_version(", "preflight", "pytest"):
        assert body.index(gate) < at, f"append_changelog 要排在 {gate} 後面"
