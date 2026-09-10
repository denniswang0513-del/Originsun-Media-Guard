# -*- coding: utf-8 -*-
"""專案編輯視窗的欄位收送（2026-09-10 修一個靜默洗資料的既有 bug）。

`_FIELDS` 是一份清單，`crm-projects.html` 是另一份真相 —— 兩邊漂掉時，
原本的 `el ? el.value.trim() : ''` 會把清單裡有、畫面上沒有的欄位送成空字串，
而後端 PUT 是 exclude_unset：有送就會寫。實案：`shoot_date` 在 _FIELDS 裡但
畫面沒有那個 input（拍攝日期改由拍攝行事曆管），於是**按一次儲存就把它洗掉**。
"""
import re

from tests.unit._srcscan import js_code_only, repo_src

CORE = "frontend/tabs/crm/crm-projects-core.js"
HTML = "frontend/tabs/crm/crm-projects.html"


def _read(p):
    return open(p, encoding="utf-8").read()


def _code(p):
    """去掉註解再比對 —— 註解裡寫著「原本是什麼、為什麼不能回去」，
    那段字面不該讓釘住舊寫法的測試誤判。"""
    return js_code_only(repo_src(p))


def _fields():
    src = _read(CORE)
    block = src[src.index("const _FIELDS = ["):]
    block = block[:block.index("]")]
    return re.findall(r"'([a-z_]+)'", block)


def test_missing_elements_are_skipped_not_sent_as_blank():
    """治的是通例，不是只補一個 shoot_date 的 input。"""
    code = _code(CORE)
    save = code[code.index("export async function saveProject("):]
    save = save[:save.index("payload.pm_usernames")]
    assert "if (!el) continue;" in save
    assert "el ? el.value.trim() : ''" not in save, "回退了就會再洗一次資料"


def test_open_and_save_agree_about_missing_elements():
    """讀那側早就跳過了；寫這側跟它不一致才是 bug 的形狀。"""
    src = _read(CORE)
    opener = src[src.index("export async function openModal("):
                 src.index("export async function saveProject(")]
    assert "if (!el) continue;" in opener


def test_shoot_date_is_the_known_drifted_field():
    """釘住這個實案：它仍在 _FIELDS、畫面上仍沒有 —— 兩者哪天對上了，
    這條就會紅，提醒回來確認 saveProject 的跳過邏輯還需不需要。"""
    assert "shoot_date" in _fields()
    assert "proj-f-shoot_date" not in _read(HTML)


def test_backup_roots_do_have_their_inputs():
    """新加的三根不能重蹈覆轍 —— 清單裡有、畫面上也要有。"""
    html = _read(HTML)
    for f in ("backup_local_root", "backup_nas_root", "backup_proxy_root"):
        assert f in _fields(), f"{f} 不在 _FIELDS"
        assert f"proj-f-{f}" in html, f"{f} 在 _FIELDS 但畫面沒有對應 input"
