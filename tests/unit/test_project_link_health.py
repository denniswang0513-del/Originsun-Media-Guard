# -*- coding: utf-8 -*-
"""core/project_link.py 發票掛案（可複數）的兩支讀寫規則 —— 特徵測試（/health 2026-09-13）。

owner 2026-09-04「連結的專案可以複數」之後這兩支是唯一的讀法／寫法，
但沒有直接測試（test_invoice_project_link 只掃原始碼）。
"""
import json

from core import project_link as pl


class TestInvoiceProjectIds:
    def test_already_in_list_keeps_list_order_and_dedupes(self):
        # docstring 說「project_id 永遠＝第一個」，但只有「不在清單裡」才會插到最前面；
        # 寫入端 normalize_invoice_projects 一定把它放第一個，所以 DB 不會出現這種順序。釘現況。
        assert pl.invoice_project_ids("p1", json.dumps(["p2", "p1", "p3", "p2"])) == ["p2", "p1", "p3"]

    def test_project_id_prepended_when_missing_from_list(self):
        assert pl.invoice_project_ids("p1", json.dumps(["p2"])) == ["p1", "p2"]

    def test_bad_or_empty_json_falls_back_to_project_id_only(self):
        assert pl.invoice_project_ids("p1", None) == ["p1"]
        assert pl.invoice_project_ids("p1", "not json") == ["p1"]
        assert pl.invoice_project_ids(None, None) == []

    def test_non_string_and_empty_entries_are_dropped(self):
        assert pl.invoice_project_ids(None, json.dumps(["p1", 7, "", None, "p2"])) == ["p1", "p2"]


class TestNormalizeInvoiceProjects:
    def test_not_sent_keeps_existing_when_form_project_still_in_list(self):
        d = {"project_id": "p2"}
        pl.normalize_invoice_projects(d, existing_ids=["p1", "p2"])
        assert d["project_id"] == "p2"                       # 表單那欄變第一個
        assert json.loads(d["project_ids"]) == ["p2", "p1"]

    def test_not_sent_resets_list_when_form_project_outside_existing(self):
        d = {"project_id": "p9"}
        pl.normalize_invoice_projects(d, existing_ids=["p1", "p2"])
        assert d == {"project_id": "p9", "project_ids": None}

    def test_not_sent_and_no_project_clears_everything(self):
        d = {"project_id": ""}
        pl.normalize_invoice_projects(d, existing_ids=["p1"])
        assert d == {"project_id": None, "project_ids": None}

    def test_sent_list_single_stores_no_json(self):
        d = {"project_ids": ["p1"], "project_id": None}
        pl.normalize_invoice_projects(d)
        assert d == {"project_ids": None, "project_id": "p1"}

    def test_sent_list_multi_puts_form_project_first_and_dedupes(self):
        d = {"project_ids": ["p2", "", "p3", "p2"], "project_id": "p1"}
        pl.normalize_invoice_projects(d)
        assert d["project_id"] == "p1"
        assert json.loads(d["project_ids"]) == ["p1", "p2", "p3"]
