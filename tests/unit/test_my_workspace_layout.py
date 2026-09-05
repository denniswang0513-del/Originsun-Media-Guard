# -*- coding: utf-8 -*-
"""員工工作台 /my.html 的版面鐵則（docs/JOURNAL_WORKLOG_PLAN.md §8–§14、BUILD_SPEC §3.2／§3.5）。

- 最上排功能鍵（既有頁面的入口）
- 第一區三顆視圖鈕：今天的專案紀錄／團隊的一週／專案查詢（記住上次視圖）
- 第二區上週回顧＝內嵌 /journal.html（iframe 仍在）
- 🔴 員工頁不出現任何個人工時合計（planned_total／actual_total／本月工時／累計工時）
- UI 無 emoji（程式碼與模板字串；註解不算）
"""
import re

from tests.unit._srcscan import js_code_only, repo_src

MY = "frontend/my.html"


def _code(html: str) -> str:
    """去掉 HTML 註解與 JS 註解後的程式碼（模板字串裡的 UI 文字都還在）。"""
    return js_code_only(re.sub(r"<!--.*?-->", "", html, flags=re.S))


def test_three_view_buttons_and_the_remembered_view():
    html = repo_src(MY)
    for v in ("log", "week", "find"):
        assert f'class="view-btn" data-view="{v}"' in html, v
    assert "今天的專案紀錄" in html and "團隊的一週" in html and "專案查詢" in html
    assert 'localStorage.getItem(Z1_KEY)' in html and 'localStorage.setItem(Z1_KEY, v)' in html
    # 三個視圖各自的資料來源（契約 BUILD_SPEC §2）
    code = _code(html)
    assert '"/api/v1/me/today"' in code
    assert '"/api/v1/me/team_week?start="' in code
    assert '"/api/v1/timesheets/mine?date="' in code and '"/api/v1/timesheets/options"' in code
    assert '"/api/v1/timesheets/projects"' in code and '"/api/v1/timesheets/project?name="' in code


def test_no_personal_hours_totals_anywhere():
    html = repo_src(MY)
    for bad in ("planned_total", "actual_total", "本月工時", "累計工時", "本週合計", "超時"):
        assert bad not in html, bad
    # 舊「我的專案」派工卡退場；請款卡改名、不再畫每案小時
    assert "cardProjects(" not in html
    assert "請款與薪酬" in html and "工時與請款" not in html
    code = _code(html)
    assert "合計" not in code.replace("未付請款", ""), "員工頁不出現「合計」（金額文案也改用「共」）"


def test_no_emoji_in_ui_code():
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐✅❌]")
    m = emoji.search(_code(repo_src(MY)))
    assert not m, f"my.html 的程式碼含 emoji：{m.group()!r}"


def test_journal_iframe_and_zones_stay():
    html = repo_src(MY)
    assert 'id="ws-journal"' in html and '/journal.html?embed=1' in html
    assert 'id="ws-zone1"' in html and 'id="ws-actions"' in html and 'id="ws-grid"' in html
    assert "journal-embed-height" in html
    # 三顆帶入鈕與工作階段設定鈕
    for act in ("import-shoots", "import-todos", "copy-prev", "stages", "row-add", "save"):
        assert f'data-z1="{act}"' in html, act
    # 複製上個工作日跳過週末
    assert "function _prevWorkday(" in html and "_dow(d) === 0 || _dow(d) === 6" in html


def test_action_bar_links_to_existing_pages_only():
    html = repo_src(MY)
    for href in ("/petty-cash.html", "/m/crm.html#invoice", "/m/crm.html#calendar", "/index.html#tab_equipment"):
        assert href in html, href
    # 領用器材沒權限就不顯示（equipment／preprod_plan 任一）
    assert 'g.has("equipment", "preprod_plan")' in html


def test_find_view_has_advanced_search():
    """owner 2026-09-05：專案查詢加進階搜尋——最後填報日期區間、案型多選、消耗率上下限；全在瀏覽器端篩。"""
    src = repo_src("frontend/my.html")
    assert 'id="z1-find-adv"' in src and "z1-fa-from" in src and "z1-fa-max" in src and "data-type=" in src
    assert "st.types.has(p.project_type" in src and "Number(p.pct) >= min" in src and "(p.last_entry || \"\") >= st.from" in src
    shared = repo_src("frontend/js/shared/ts-projects.js")
    assert "export function pctClass(" in shared and "ts-pct ${pctClass(p.pct)}" in shared
