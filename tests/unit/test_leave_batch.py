# -*- coding: utf-8 -*-
"""我的假勤「挑幾天（不連續）」（owner 2026-09-15「一次挑好幾個不連續的日期」）。

- 資料模型不變：一天一張單（管理端逐張核准、各自扣時數帳）；後端 /me/leave/batch 一個 transaction 全建或全不建。
- 試算 evaluate_batch：每一天各走一次 evaluate（撞單／假日／時段規則在那裡），再把**總時數**對餘額（單日各自夠、加起來不夠只有這裡看得到）。
- 前端：起迄那組與「挑幾天」互斥；選了日期就走 batch 端點；送出後回到起迄模式。
"""
import pytest

from tests.unit._srcscan import func_body, js_code_only, js_func_body, repo_src


def test_normalize_dates_dedupes_sorts_and_caps():
    from core.leave_logic import MAX_BATCH_DATES, normalize_dates
    assert normalize_dates(["2026-10-16", "2026-10-14", "2026-10-16"]) == ["2026-10-14", "2026-10-16"]
    with pytest.raises(ValueError):
        normalize_dates([])
    with pytest.raises(ValueError):
        normalize_dates(["2026-13-40"])
    with pytest.raises(ValueError):
        normalize_dates([f"2026-01-{d:02d}" for d in range(1, 32)] + ["2026-02-01"])
    assert MAX_BATCH_DATES == 31


def test_batch_endpoints_are_self_service_and_all_or_nothing():
    me = repo_src("routers/api_me.py")
    for fn in ("async def preview_my_leave_batch(", "async def apply_my_leave_batch("):
        assert 'require_bound_staff(request, "me_leave")' in func_body(me, fn), fn
    apply = func_body(me, "async def apply_my_leave_batch(")
    assert 'raise HTTPException(status_code=422, detail="事由必填' in apply
    assert 'bad = ev["errors"] + [e for p in ev["dates"] for e in p["errors"]]' in apply, "任一天有錯就整批 422"
    assert apply.index("if bad:") < apply.index("session.add(obj)") < apply.index("await session.commit()"), "先驗完再建，同一個 commit"
    assert apply.count("await session.commit()") == 1
    svc = repo_src("services/leave_service.py")
    ev = func_body(svc, "async def evaluate_batch(")
    assert "normalize_dates(batch.dates)" in ev
    assert "_DayBody(batch, d), today=today, holidays=holidays, self_service=self_service" in ev, "每天各走一次 evaluate（自助假別限制也套）"
    assert "check_balance=False" in ev, "單日不各自擋餘額，整批依日期順序把餘額用完"
    assert "fit_days_to_balance([(p[\"date\"], p[\"hours\"], p[\"part\"]) for p in ok], free)" in ev
    assert "餘額到這天已經用完，這天塞不下（請拿掉）" in ev and '_err("trimmed"' in ev
    assert ev.index("if batch.leave_type in LEDGER_TYPES:") < ev.index("bal = (await balances_for("), "走時數帳的假別一律回 balance（挑的過程要看得到還剩多少）"
    assert "if w[\"code\"] not in seen_w" in ev, "同一種警告整批只講一次"


def test_card_switches_between_range_and_picked_dates():
    js = js_code_only(repo_src("frontend/js/my/cards-hr.js"))
    render = js_func_body(js, "function renderLeave(")
    for i in ("lv-multi-toggle", "lv-multi", "lv-multi-date", "lv-multi-chips"):
        assert f'id="{i}"' in render, i
    assert 'onclick="lvAddDate()"' in render, "只有按「加入這天」才加（Enter 不加，同我的一週的規矩）"
    prev = js_func_body(js, "async function lvPreview(")
    assert 'multi ? "/api/v1/me/leave/batch/preview" : "/api/v1/me/leave/preview"' in prev
    assert "x.errors.map(e => ({ msg: `${x.date.slice(5)" in prev, "每一天自己的錯誤要標日期"
    apply = js_func_body(js, "async function applyLeave(")
    assert '"/api/v1/me/leave/batch"' in apply and '"/api/v1/me/leave"' in apply
    assert "_lvDates = [];" in render, "重畫（送單後）就回到起迄模式"
    toggle = js_func_body(js, "function lvToggleMulti(")
    assert 'if (!on) { _lvDates = []; _lvTrim = {}; }' in toggle, "關掉挑幾天就清空（連削過的標記），不會殘留到起迄模式"
    assert "×" in js and "✕" not in js and "❌" not in js, "拿掉鈕用 ×，不用 emoji"


def test_preview_shows_remaining_hours_while_picking():
    """owner 2026-09-15「挑的過程可以直接知道還剩下多少小時」：試算那行下面「可用 → 這次 → 還剩／超過」，特休補休都有、單日與挑幾天都有。"""
    js = js_code_only(repo_src("frontend/js/my/cards-hr.js"))
    fn = js_func_body(js, "function _lvBalanceLine(")
    assert "b.available - (b.reserved || 0)" in fn and "free - Number(d.hours || 0)" in fn
    assert "超過" in fn and "還剩" in fn
    assert "_lvBalanceLine(p.leave_type, d)" in js_func_body(js, "async function lvPreview(")


def test_last_day_is_trimmed_to_the_remaining_hours():
    """owner 2026-09-15「挑了 3 天 8 小時、實際只要休 20 個小時，那需要有一天假剩下 4 小時」：
    依日期順序把餘額用完，卡在中間那天只休剩下的小時（4h→上午；其他→09:00 起的時段），後面塞不下的 hours=0。"""
    from core.leave_logic import fit_days_to_balance
    days = [("2026-12-01", 8, "all"), ("2026-12-02", 8, "all"), ("2026-12-03", 8, "all")]
    out = fit_days_to_balance(days, 20)
    assert [(o["date"], o["hours"], o["part"]) for o in out] == [("2026-12-01", 8, "all"), ("2026-12-02", 8, "all"), ("2026-12-03", 4.0, "am")]
    assert out[2]["trimmed_from"] == 8 and out[0]["trimmed_from"] is None
    out = fit_days_to_balance(days, 18.5)
    assert (out[2]["hours"], out[2]["part"], out[2]["start_time"], out[2]["end_time"]) == (2.5, "range", "09:00", "11:30")
    out = fit_days_to_balance(days, 8)
    assert [o["hours"] for o in out] == [8, 0.0, 0.0], "餘額用完後面的天 0 小時（呼叫端標錯要拿掉）"
    assert fit_days_to_balance([("2026-12-01", 4, "pm")], 2)[0]["start_time"] == "13:00", "下午選的時段從 13:00 起"
    assert fit_days_to_balance([("2026-12-01", 8, "all")], 4.4)[0]["hours"] == 4.0, "0.5 步進往下取"
    me = repo_src("routers/api_me.py")
    apply = func_body(me, "async def apply_my_leave_batch(")
    assert 'leave_service._DayBody(body, p["date"], p)' in apply and "只剩 {p['hours']:g} 小時（原本 {p['trimmed_from']:g}）／" in apply
    js = js_code_only(repo_src("frontend/js/my/cards-hr.js"))
    assert "只休 ${_lvH(t.hours)} 小時" in js_func_body(js, "function _lvBalanceLine(")
    assert "_lvTrim = Object.fromEntries((d.trimmed || []).map(t => [t.date, t.hours])); _lvDrawChips();" in js_func_body(js, "async function lvPreview(")


def test_multi_mode_without_dates_never_submits_the_hidden_range():
    """/polish 2026-09-15 BUG-1：開了「挑幾天」還沒挑日期就按送出，原本會把藏起來的起迄日期送出去。
    模式由那塊有沒有打開決定，不看有沒有挑到日期；沒挑時試算與送出都擋。"""
    js = js_code_only(repo_src("frontend/js/my/cards-hr.js"))
    assert 'const _lvMulti = () => _lvMultiOn();' in js and 'b.style.display !== "none"' in js
    assert 'if (multi && !_lvDates.length) {' in js_func_body(js, "async function lvPreview(")
    assert 'if (_lvMulti() && !_lvDates.length) { show("還沒挑日期"); return; }' in js_func_body(js, "async function applyLeave(")
    assert "_lvDates = []; _lvTrim = {};" in js_func_body(js, "function renderLeave("), "送單後重畫連削過的標記一起清"
