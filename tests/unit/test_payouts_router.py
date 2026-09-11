# -*- coding: utf-8 -*-
"""匯款通知那支 router 的三條規則 —— 它們都是「壞掉也不會噴錯」的那種。

`routers/crm/payouts.py` 是照發票分享那份抄的，但有三件事是 owner 對**這個**
功能單獨拍板的，而且違反時沒有任何徵兆：錯誤只有收款人看得到，或者根本沒人
看得到（帳被改了而已）。所以在這裡用原始碼掃描釘住。

為什麼是掃原始碼不是打端點：這幾條講的是「這段程式不准做某件事」，
端點測試只能證明「某一次呼叫沒做」，證不了「所有路徑都沒做」。
"""
import re

from tests.unit._srcscan import code_only, flow_body, repo_src

SRC = repo_src("routers/crm/payouts.py")

#: 🔴 用 `flow_body` 不是 `func_body`：釘的是「**這條流程**有沒有做這件事」，
#: 不是「這段程式住在哪一支函式裡」。`create_payout` 現在 82 行，下一個人把驗證
#: 那幾段抽成同檔 helper 是好事 —— 用 func_body 的話那會讓測試變紅（假紅），
#: 而且更糟的是：被禁止的寫入只要搬進 helper 就再也抓不到，測試會安靜地失效。
#: flow_body 會把它在同一個檔裡呼叫的 `_` 開頭 helper 一起帶進來。


def test_creating_a_payout_never_touches_payment_status():
    """產通知**不改付款狀態**（owner 2026-09-11）。

    出納可能先匯再標、也可能先標再產通知；把兩件事綁在一起的話，
    「只想補一張通知」就得先把狀態退回去。而且這支會一次掃過好幾筆請款單 ——
    偷偷順手標成已付款，帳面上看起來完全正常。
    """
    body = code_only(flow_body(SRC, "async def create_payout("))
    for forbidden in ("payment_status", "payment_date"):
        assert forbidden not in body, (
            f"create_payout 碰了 {forbidden} —— 產通知不准改付款狀態，"
            "標已付款是面板上另一顆按鈕的事")


def test_revoking_only_clears_the_token():
    """撤銷只讓連結失效，**不動 `payout_id`**。

    「這幾筆是同一次匯出去的」是已經發生的事實，跟「那條連結還能不能打開」
    是兩件事。清掉 payout_id 等於把那次匯款的組成憑空抹掉，而且無法復原。
    """
    body = code_only(flow_body(SRC, "async def revoke_payout_share("))
    assert "share_token = None" in body, "撤銷要把短碼清成 NULL"
    # 比對的是「有沒有寫進去」，不是「有沒有出現這個字」—— payout_id 是路徑參數名，
    # 它本來就會出現在簽名上。
    assert not re.search(r"\.payout_id\s*=[^=]", body), (
        "撤銷不准寫 payout_id —— 那是「這幾筆同一次匯出去」的事實，不是連結狀態")


def test_the_public_page_only_serves_the_whitelist_projection():
    """公開那頁回的是 `payout_share.share_view(...)`，不是 DB 活值也不是原始快照。

    多回一個欄位不會有任何徵兆 —— **而錯誤只有收款人看得到**（同發票分享頁
    那條）。另外要有 `surface_gate`：master 的路由有擋一次，NAS 對外容器那條
    沒有經過 main.py，漏掉等於那面關不掉。
    """
    body = code_only(flow_body(SRC, "async def payout_share_page("))
    assert "payout_share.share_view(" in body, (
        "公開頁一定要過 share_view 投影，不可以直接回 share_snapshot")
    assert "surface_gate(request)" in body, (
        "公開端點要自己 await surface_gate —— NAS 那條不經過 main.py")
