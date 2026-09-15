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
    assert 'if free < total:' in ev and '_err("insufficient"' in ev, "總時數對餘額"
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
    assert 'if (!on) _lvDates = [];' in toggle, "關掉挑幾天就清空，不會殘留到起迄模式"
    assert "×" in js and "✕" not in js and "❌" not in js, "拿掉鈕用 ×，不用 emoji"
