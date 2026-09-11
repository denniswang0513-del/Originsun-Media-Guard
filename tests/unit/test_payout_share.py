# -*- coding: utf-8 -*-
"""匯款通知頁的欄位規則（owner 2026-09-11）。

那頁寄給收款人、可以被轉傳。錯誤**只有他看得到**，我們永遠不會知道 ——
所以投影是白名單，而且 NEVER_SHARE 另列一份在這裡逐欄釘住。
"""
from datetime import date, datetime, timezone
from types import SimpleNamespace as NS

from core.payout_share import (ITEM_FIELDS, NEVER_SHARE, SNAPSHOT_FIELDS,
                               build_snapshot, share_view)


def _item(summary="攝影與訪問", amount=36000, project="東仁社宅金安獎影片", **extra):
    base = dict(summary=summary, amount=amount, project_label=project,
                # 下面這些是請款列上真的有、但**不准**上那頁的
                id="pay-1", payee_id="G122017033", bank_account="342168993550",
                bank_name="台北富邦(012)", category="專案雜支", payee_type="現金",
                payment_status="已付款", planned_month="2026-09", entity="parent",
                project_id="proj-1", notes="內部備註：先欠著", advance_by="某某")
    base.update(extra)
    return NS(**base)


class TestSnapshot:
    def test_only_the_three_columns_survive_per_item(self):
        snap = build_snapshot("陳志廷", date(2026, 9, 11), "源日有限公司", [_item()])
        assert set(snap["items"][0]) == set(ITEM_FIELDS)

    def test_total_is_the_sum_of_what_is_on_the_page(self):
        """頁上印的合計要等於明細加起來 —— 不然他一對就發現對不起來。"""
        snap = build_snapshot("陳志廷", date(2026, 9, 11), "源日", [
            _item(amount=1495), _item(amount=2250), _item(amount=36000), _item(amount=7000)])
        assert snap["total"] == 46745 == sum(r["amount"] for r in snap["items"])

    def test_top_level_shape_is_exactly_the_whitelist(self):
        snap = build_snapshot("陳志廷", date(2026, 9, 11), "源日", [_item()])
        assert set(snap) == set(SNAPSHOT_FIELDS)

    def test_dates_are_normalised_to_a_plain_day(self):
        for given in (date(2026, 9, 11), datetime(2026, 9, 11, 15, 30, tzinfo=timezone.utc),
                      "2026-09-11", "2026-09-11T00:00:00+08:00"):
            assert build_snapshot("x", given, "y", [])["paid_date"] == "2026-09-11"

    def test_empty_items_is_a_zero_not_a_crash(self):
        snap = build_snapshot("陳志廷", None, "源日", [])
        assert snap["total"] == 0 and snap["items"] == [] and snap["paid_date"] == ""


class TestNothingLeaks:
    """🔴 逐欄斷言：就算輸入裡有，輸出也不能有。"""

    def test_snapshot_never_carries_the_forbidden_fields(self):
        snap = build_snapshot("陳志廷", date(2026, 9, 11), "源日", [_item()])
        blob = repr(snap)
        for field in NEVER_SHARE:
            assert field not in snap, field
            assert field not in (snap["items"][0] if snap["items"] else {}), field
        for value in ("342168993550", "G122017033", "內部備註：先欠著", "proj-1"):
            assert value not in blob, value

    def test_the_public_view_filters_again_even_if_the_snapshot_is_dirty(self):
        """快照存在 DB 裡會被改、會被舊版本寫過 —— 公開那一層是最後一道。"""
        dirty = {
            "payee_name": "陳志廷", "paid_date": "2026-09-11", "payer": "源日",
            "total": 46745,
            "items": [{"summary": "攝影", "project": "某案", "amount": 36000,
                       "bank_account": "342168993550", "notes": "內部", "payee_id": "G1"}],
            "bank_account": "342168993550", "payee_id": "G122017033", "entity": "parent",
        }
        view = share_view(dirty)
        assert set(view) == set(SNAPSHOT_FIELDS)
        assert set(view["items"][0]) == set(ITEM_FIELDS)
        assert "342168993550" not in repr(view) and "G122017033" not in repr(view)

    def test_the_two_lists_can_never_overlap(self):
        """白名單與 NEVER_SHARE 不准有交集 —— 這條守的是「未來」那一側。

        build_snapshot 與 share_view 現在都是硬編碼欄位，所以此刻沒有洩漏路徑。
        但日後要在通知頁多加一欄時，改的是 ITEM_FIELDS ＋ build_snapshot，而
        share_view 的 item 層是照著 ITEM_FIELDS 跑迴圈的 —— 加了 notes 就會照送，
        而上面那條 set(view 的 item) == set(ITEM_FIELDS) 是套套邏輯，一樣是綠的。
        這條讓它在改 ITEM_FIELDS 的當下就紅。
        """
        overlap = (set(SNAPSHOT_FIELDS) | set(ITEM_FIELDS)) & set(NEVER_SHARE)
        assert not overlap, (
            f"這幾個欄位同時在白名單與 NEVER_SHARE 裡：{sorted(overlap)} —— "
            "要嘛它可以給收款人看（從 NEVER_SHARE 拿掉），要嘛不行（從白名單拿掉）")

    def test_bank_account_is_deliberately_out_even_though_it_is_his_own(self):
        """他自己的帳號也不上 —— 連結可被轉傳，一頁上同時有姓名＋帳號＋金額
        就是一份現成的資料。他不需要從這頁知道自己的帳號。"""
        assert "bank_account" in NEVER_SHARE and "payee_id" in NEVER_SHARE
        assert "bank_account" not in SNAPSHOT_FIELDS and "bank_account" not in ITEM_FIELDS


class TestShareViewIsTotalOnJunk:
    def test_none_and_garbage_do_not_explode(self):
        for junk in (None, {}, {"items": None}, {"items": ["不是 dict"]},
                     {"total": "abc", "paid_date": None}):
            view = share_view(junk)
            assert set(view) == set(SNAPSHOT_FIELDS)
            assert isinstance(view["total"], int)

    def test_amounts_are_numbers_not_strings(self):
        view = share_view({"items": [{"summary": "a", "project": "b", "amount": "1495"}]})
        assert view["items"][0]["amount"] == 1495


def test_the_module_stays_pure():
    """無 I/O：規則要能在任何一台（含 NAS 對外容器）直接 import。"""
    from tests.unit._srcscan import code_only, repo_src
    src = code_only(repo_src("core/payout_share.py"))
    for forbidden in ("import requests", "session", "select(", "open("):
        assert forbidden not in src, forbidden
