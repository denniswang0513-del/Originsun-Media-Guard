# -*- coding: utf-8 -*-
"""員工工作台 /my.html 的版面鐵則（docs/JOURNAL_WORKLOG_PLAN.md §8–§14、BUILD_SPEC §3.2／§3.5）。

- 最上排功能鍵（既有頁面的入口）
- 第一區三顆視圖鈕：今天的專案紀錄／團隊的一週／專案查詢（記住上次視圖）
- 第二區上週回顧＝內嵌 /journal.html（iframe 仍在）
- 🔴 員工頁不出現任何個人工時合計（planned_total／actual_total／本月工時／累計工時）
- UI 無 emoji（程式碼與模板字串；註解不算）
"""
import re

from tests.unit._srcscan import js_code_only, my_page_src, repo_src


def _code(html: str) -> str:
    """去掉 HTML 註解與 JS 註解後的程式碼（模板字串裡的 UI 文字都還在）。"""
    return js_code_only(re.sub(r"<!--.*?-->", "", html, flags=re.S))


def test_three_view_buttons_and_the_remembered_view():
    html = my_page_src()
    # 2026-09-08 起按鈕依鑰匙動態畫（一顆功能一把；test_me_zone_keys 釘閘門）：模板長這樣
    for v, label in (("log", "今天的專案紀錄"), ("week", "團隊的一週"), ("find", "專案查詢")):
        assert f'btn("{v}", "{label}")' in html, v
    assert '`<button type="button" class="view-btn" data-view="${v}">${label}</button>`' in html
    assert "今天的專案紀錄" in html and "團隊的一週" in html and "專案查詢" in html
    assert 'localStorage.getItem(Z1_KEY)' in html and 'localStorage.setItem(Z1_KEY, v)' in html
    # 三個視圖各自的資料來源（契約 BUILD_SPEC §2）
    code = _code(html)
    assert '"/api/v1/me/today"' in code
    assert '"/api/v1/me/team_week?start="' in code
    assert '"/api/v1/timesheets/mine?date="' in code and '"/api/v1/timesheets/options"' in code
    # 專案查詢：員工端唯讀版 /me/projects_burn（2026-09-06 拿掉往 /timesheets/projects、/summary 的三段備援）
    assert '"/api/v1/me/projects_burn"' in code and '"/api/v1/timesheets/project?name="' in code
    # /summary 只在 ts-zone 的管理端點表（manageApi）；員工端點表 defaultApi 與殼都沒有
    from tests.unit._srcscan import js_func_body, my_shell_src
    ctx = repo_src("frontend/js/shared/ts-zone/ctx.js")
    assert '"/api/v1/timesheets/summary"' not in js_func_body(ctx, "export function defaultApi() {")
    assert '"/api/v1/timesheets/summary"' not in _code(my_shell_src())


def test_no_personal_hours_totals_anywhere():
    html = my_page_src()
    for bad in ("planned_total", "actual_total", "本月工時", "累計工時", "本週合計", "超時"):
        assert bad not in html, bad
    # 舊「我的專案」派工卡退場；請款卡改名、不再畫每案小時
    assert "cardProjects(" not in html
    assert "請款與薪酬" in html and "工時與請款" not in html
    from tests.unit._srcscan import my_shell_src
    code = _code(my_shell_src())
    assert "合計" not in code.replace("未付請款", ""), "員工頁不出現「合計」（金額文案也改用「共」）"
    # 共用的 ts-zone 只有管理視角才畫「週合計」：出現「合計」的每一行都要有 z.manage 守著
    zone = js_code_only(repo_src("frontend/js/shared/ts-zone/team-week.js"))
    for line in zone.splitlines():
        if "合計" in line:
            assert "z.manage" in line, line.strip()[:120]


def test_no_emoji_in_ui_code():
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐✅❌]")
    m = emoji.search(_code(my_page_src()))
    assert not m, f"my.html 的程式碼含 emoji：{m.group()!r}"


def test_journal_iframe_and_zones_stay():
    html = my_page_src()
    assert 'id="ws-journal"' in html and '/journal.html?embed=1' in html
    assert 'id="ws-zone1"' in html and 'id="ws-actions"' in html and 'id="ws-grid"' in html
    assert "journal-embed-height" in html
    # 工作階段設定／加五列／儲存草稿；帶入鈕 2026-09-07 owner 拿掉
    for act in ("stages", "row-add", "save"):
        assert f'data-z1="{act}"' in html, act
    assert "import-shoots" not in html and "import-todos" not in html
    # 複製上個工作日跳過週末
    assert "function _prevWorkday(" in html and "_dow(d) === 0 || _dow(d) === 6" in html


def test_action_bar_is_petty_cash_and_leave():
    """owner 2026-09-05：「先只留下零用金」；owner 2026-09-15：放回第二顆「假勤」（開 /leave.html 寬頁）。

    原本七顆（零用金／請款／請開發票／登記拍攝／領用器材／請假／福委會），
    一次擺七顆等於沒有重點。要放回來就一顆一顆放，不要整排長回去 —— 目前兩顆。
    連結各指獨立頁、各受自己那把鑰匙（me_petty／me_leave）閘門。
    """
    html = my_page_src()
    fn = html.split("function renderActions(")[1].split("\n}")[0]
    assert '{ label: "零用金", href: "/petty-cash.html" }' in fn
    assert '{ label: "假勤", href: "/leave.html" }' in fn
    assert 'has("me_petty")' in fn and 'has("me_leave")' in fn, "閘門不能一起拿掉"
    assert fn.count("label:") == 2, "最上排只有零用金與假勤（要放第三顆要有 owner 的話）"
    # owner 2026-09-15：點了開寬的浮動視窗（iframe＋叉叉／Esc／背景），不跳頁；href 留著給「另開」與中鍵
    assert "openActionModal('${it.href}', '${it.label}'); return false;" in fn
    modal = html.split("function openActionModal(")[1].split("\n}")[0]
    assert 'embed=1' in modal and 'class="wam-close"' in modal and 'wam-backdrop").onclick = closeActionModal' in modal
    assert 'if (e.key === "Escape") closeActionModal();' in html
    for page in ("frontend/leave.html", "frontend/petty-cash.html"):
        src = repo_src(page)
        assert 'new URLSearchParams(location.search).get("embed")' in src and "html.embed header { display: none; }" in src, page


def test_find_view_filters_are_one_row():
    """owner 2026-09-05：搜尋篩選整併成一列——搜尋框、狀態、案型、消耗率區間、最後填報多久內、清除；沒有進階面板。"""
    src = my_page_src()
    for i in ("z1-find-q", "z1-f-status", "z1-f-type", "z1-f-pct", "z1-f-from", "z1-f-to", "z1-f-clear"):
        assert f'id="{i}"' in src, i
    assert "z1-find-adv" not in src and "data-type=" not in src
    assert "PCT_BANDS" in src and "_pctInBand(p.pct, st.pct)" in src and "_lastInRange(p.last_entry, st.from, st.to)" in src
    assert 'defaultSort: { key: "last", dir: "desc" }' in src, "預設最新填報在最上面"
    shared = repo_src("frontend/js/shared/ts-projects.js")
    assert "export function pctClass(" in shared and "ts-pct ${pctClass(p.pct)}" in shared


def test_leave_and_petty_cards_are_input_panels_opening_the_modal():
    """owner 2026-09-15「這兩塊規劃成輸入面板」：工作台的假勤卡只放數字＋待審件數＋一顆鈕、零用金卡一顆鈕，
    都開最上排那種寬的浮動視窗（/leave.html、/petty-cash.html 嵌在裡面）；完整三步表單只在 /leave.html 畫。"""
    html = my_page_src()
    assert "const _lvFullHost = () => typeof window.onLeaveRendered === \"function\";" in html
    render = html.split("function renderLeave(body)")[1].split("\n}")[0]
    assert "if (!_lvFullHost()) { body.innerHTML = _lvPanelHtml(v, an, comp, sick, ledgerEmpty); return; }" in render
    panel = html.split("function _lvPanelHtml(")[1].split("\n}")[0]
    assert "openActionModal('/leave.html', '假勤')" in panel and 'id="lv-inv"' not in panel, "面板不畫表單"
    petty = html.split("function cardPettyCash()")[1].split("\n}")[0]
    assert "openActionModal('/petty-cash.html', '零用金'); return false;" in petty
