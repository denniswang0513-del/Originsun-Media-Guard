# -*- coding: utf-8 -*-
"""內部代開發票的「付款方向」是**生命週期旗標**，不是錢的方向（owner 2026-09-05）。

全庫證據：內部代開＋payment_type=付款 的發票，有分配的 171 筆**全部對到收入**
（客戶匯進來那筆）。所以：
    收款／未收款  → 客戶還沒付
    付款／待撥款  → 客戶付了，換我們欠代開人（面額 − 代開費 ＝ commission）
    付款／已撥款  → 已匯給代開人
收付款分頁原本用 payment_type==='收款' 濾掉它們，客戶已付的期款整張消失
（快樂學游泳：第一期款 161,700 收到了，畫面「客戶已匯 $0」、「已開發票」少一張）。
"""
from tests.unit._srcscan import js_code_only, js_func_body, repo_src


def _pay():
    return js_code_only(repo_src("frontend/tabs/crm/crm-projects-pay.js"))


def test_kai_invoices_count_regardless_of_direction():
    fn = js_func_body(_pay(), "export function payStatus(")
    assert "const isKai = (i) => /代開/.test(i.category || '');" in fn
    assert "(i.payment_type || '收款') === '收款' || isKai(i)" in fn, "代開不看方向"
    # 非代開的付款方向發票才走「另列、不計入」那條
    assert "=== '付款' && !isKai(i)" in fn


def test_received_falls_back_to_invoices_when_project_field_is_empty():
    """專案的 amount_received 是收支同步寫的；沒同步到（發票代開的收支列以前掛不了
    專案）就是 NULL → 從發票推：分配金額，沒分配但已走到撥款階段就用面額。"""
    fn = js_func_body(_pay(), "export function payStatus(")
    assert "const collectedOf = (i) => Number(i.collected || 0)" in fn
    assert "COLLECTED_RE.test(i.payment_status || '') ? Number(i.amount_total || 0) : 0" in fn
    assert "p.amount_received != null ? Number(p.amount_received) : receivedInv" in fn


def test_unreceived_is_split_into_uncollected_and_uninvoiced():
    """「未收 $1,029,000」把「已開未收 836,500」跟「還沒開 192,500」混成一個數 ——
    一個要催款、一個要開票，意義完全不同。"""
    js = _pay()
    fn = js_func_body(js, "export function payStatus(")
    assert "const invoicedUnreceived = Math.max(invoiced - received, 0);" in fn
    assert "const uninvoiced = Math.max(contract - invoiced, 0);" in fn
    strip = js_func_body(js, "function _strip(")
    assert "tile('已開未收', m(s.invoicedUnreceived)" in strip
    assert "未開發票 ${m(s.uninvoiced)}" in strip
    assert "tile('未收'," not in strip, "舊的合併數字不要留"


def test_kai_remit_obligation_is_shown():
    """客戶付了之後要撥給代開人的錢（commission）要看得到：待撥的、已撥的分開。"""
    js = _pay()
    fn = js_func_body(js, "export function payStatus(")
    assert "const kaiDue = " in fn and "i.payment_status !== '已撥款'" in fn
    assert "const kaiRemitted = " in fn and "i.payment_status === '已撥款'" in fn
    strip = js_func_body(js, "function _strip(")
    assert "應撥代開人 ${m(s.kaiDue)}" in strip and "已撥代開人 ${m(s.kaiRemitted)}" in strip
    hints = js_func_body(js, "export function nextSteps(")
    assert "s.kaiDue > 0" in hints and "s.uninvoiced > 0" in hints
    # 催款那條要用「已開未收」；「發票都標已收但帳上未收還有 X」那條仍看合約層的
    # unreceived（那是在抓匯款列沒連到本案），兩者是不同的檢查，都要在
    assert "，未收 ' + m(s.invoicedUnreceived)" in hints
    assert "匯款列可能還沒連結到本案" in hints
