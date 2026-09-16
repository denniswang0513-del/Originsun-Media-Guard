# -*- coding: utf-8 -*-
"""桌機「堡壘」子視圖（docs/FORTRESS_PLAN.md §3）：nav 入口與 fortress.js 的契約掃原始碼。"""
import re

from tests.unit._srcscan import js_code_only, js_func_body, repo_src

JS = "frontend/tabs/finance/subviews/fortress.js"


def test_nav_button_is_mine_only():
    html = repo_src("frontend/tabs/finance/finance.html")
    m = re.search(r'<button class="([^"]*)" data-subview="fortress">', html)
    assert m, "finance.html 要有 data-subview=fortress 的 nav 鈕"
    classes = m.group(1).split()
    assert "finance-nav-btn" in classes
    assert "fin-nav-mine-ok" in classes and "fin-nav-mine-only" in classes, "只在私帳出現（母公司帳沒有堡壘）"


def test_subview_entry_and_endpoints():
    js = js_code_only(repo_src(JS))
    assert "export default async function render(" in js, "createSubviewLoader 要 default export 的 render"
    assert "finFetchMine('/fortress')" in js, "整頁一趟讀 GET /finance/fortress（固定私帳）"
    assert "'/fortress/settings'" in js, "分層／目標／必要支出／戰爭假設都走 PUT /fortress/settings"
    assert "'/fortress/earmarks'" in js and "`/fortress/earmarks/${" in js, "預留清單 POST／PUT／DELETE"
    assert "window._finFortress" in js


def test_imports_only_from_shared_utils():
    js = js_code_only(repo_src(JS))
    for m in re.finditer(r"^\s*import\s.*?from\s+'([^']+)'", js, re.M):
        assert m.group(1) in ("../fin-utils.js", "../../crm/crm-utils.js"), f"不得 import 其他子視圖：{m.group(1)}"


def test_delete_asks_confirm():
    js = js_code_only(repo_src(JS))
    body = js_func_body(js, "_ff.delEarmark = async (id, btn) => {")
    assert "confirm(" in body, "刪預留要先問"
    assert "method: 'DELETE'" in body


def test_write_paths_rerender_from_payload():
    """每次寫入都用回傳的整份 payload 重畫，前端不自己重算（規劃 §0）。"""
    js = js_code_only(repo_src(JS))
    put = js_func_body(js, "async function _put(path, body, okMsg, btn) {")
    assert "method: 'PUT'" in put and "_d = await finFetchMine(" in put and "_render()" in put
    assert "monthly_need_override: 0" in js, "「用自動」送 0 回到自動平均"
    war = js_func_body(js, "_ff.saveAssume = (key, btn) => {")
    assert "/ 100" in war and "fx: 1 +" in war, "戰爭假設送小數（60% → 0.6；貶 30% → fx 1.3）"
    assert "care: {" in war and "insurance_monthly:" in war, "長照假設（月費／年數／保險月給付）同一支存"
    g = js_func_body(js, "_ff.saveGrowth = (btn) => {")
    assert "rate:" in g and "inflation:" in g and "annual_add:" in g, "資產預期成長的三個假設"


# ── /polish 階段一：review 抓到的四個地雷，各釘一條 ──────────────────────
def test_layer_autosave_snapshots_the_table_at_change_time():
    """🔴 資料遺失（review 2026-09-16）：分層是 setTimeout 400ms 後才讀表格。
    改完層別立刻切到別的子視圖 → finance.js 已把 container.innerHTML 換掉 → 讀到 0 列 →
    送出 `{account_layers:{}, account_flags:{}}`，而後端 merge_settings 是**整份取代** → 全部分層被清空。
    修法：change 當下就把整張表快照起來（_pendingLayers），計時器只負責送；沒有快照就不送。"""
    js = js_code_only(repo_src(JS))
    change = js_func_body(js, "function _onLayerChange(ev) {")
    assert "_pendingLayers = _readLayers()" in change, "change 當下就要讀表（不能等計時器）"
    save = js_func_body(js, "_ff.saveLayers = () => {")
    assert "if (!_pendingLayers) return;" in save, "沒有待存快照就不要送（空的會把設定清光）"
    read = js_func_body(js, "function _readLayers() {")
    assert "if (!rows.length) return null;" in read, "表格不在畫面上（已切走）就回 null，不要回空物件"


def test_render_reapplies_pending_layer_edits():
    """在途的 PUT 回來會整頁重畫；重畫後要把還沒存成功的勾選貼回去，不然使用者的第二次修改會不見。"""
    js = js_code_only(repo_src(JS))
    render = js_func_body(js, "function _render() {")
    assert "_applyPending()" in render


def test_blank_boxes_keep_the_saved_value():
    """目標倍數／戰爭假設是**整份取代**：清空一格再存，會讓那一格掉回後端預設。
    修法：以目前存著的設定當底，只覆蓋真的有填的格子。"""
    js = js_code_only(repo_src(JS))
    tg = js_func_body(js, "_ff.saveTargets = () => {")
    assert "..._cfg().targets" in tg, "以目前設定為底"
    war = js_func_body(js, "_ff.saveAssume = (key, btn) => {")
    assert "_num(" in war and "_cfg().war" in war and "...w," in war, "空白格保留原值，不要變成 0"
    assert "_cfg().care" in war and "...c," in war, "長照那半也一樣"


def test_earmark_row_buttons_use_data_attributes():
    """列上的鈕不要把 id 內插進 onclick 的 JS 字串（esc 是 HTML escape，不是 JS escape）。"""
    js = js_code_only(repo_src(JS))
    assert "onclick=\"window._finFortress.togglePaid('" not in js and "onclick=\"window._finFortress.delEarmark('" not in js
    assert "data-em=" in js and "data-em-act=" in js, "改用 data 屬性＋委派監聯"
    render = js_func_body(js, "function _render() {")
    assert "_onEarmarkClick" in render


def test_failed_save_restores_the_status_line():
    """存失敗時狀態列不能一直停在「儲存中…」。"""
    js = js_code_only(repo_src(JS))
    put = js_func_body(js, "async function _put(path, body, okMsg, btn) {")
    assert "_layerStatus(" in put
