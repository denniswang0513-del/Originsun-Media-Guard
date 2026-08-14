# -*- coding: utf-8 -*-
"""「這筆提案該不該補殼專案」—— `api_proposals.enters_pipeline` 的正反面。

為什麼值得一支單元測試：這條規則有三個消費者（`update_proposal` 的自癒尾巴、
startup 的存量遷移、前端進度分頁的自癒 CTA），但前兩個都要真 DB，第三個要真
瀏覽器 —— 也就是說在這支之前，這條規則唯一的守門是一支 e2e，而那支只有在
dev/test 資料庫上才跑得起來（`dev_db_only`）。

規則本身是純函式，所以這裡不需要 DB、不需要 session。
"""
import pytest

from routers.api_proposals import PIPELINE_EXEMPT_STATUS, enters_pipeline


class _Prop:
    """只帶這條規則看得到的兩個欄位 —— 用真的 ORM 實體會把 DB 相依拖進來。"""

    def __init__(self, project_id=None, status=""):
        self.project_id = project_id
        self.status = status


@pytest.mark.parametrize("status", ["", "草稿", "已提案", "入圍", "成案", "未成案"])
def test_pipeline_less_proposals_get_a_shell(status):
    """沒有專案 → 一律補（含空狀態：撈不到狀態不該變成「不進管線」）。"""
    assert enters_pipeline(_Prop(status=status)) is True


def test_the_exempt_status_stays_out():
    """已停擺的提案刻意不進管線洗版 —— 這是唯一的豁免。"""
    assert enters_pipeline(_Prop(status=PIPELINE_EXEMPT_STATUS)) is False


@pytest.mark.parametrize("status", ["草稿", PIPELINE_EXEMPT_STATUS])
def test_already_linked_never_gets_a_second_shell(status):
    """已經有專案就不再建 —— 遷移每台機隊 startup 都會跑，重複建殼是災難。"""
    assert enters_pipeline(_Prop(project_id="p1", status=status)) is False


def test_exempt_status_is_a_real_proposal_status():
    """豁免值必須是提案狀態表裡真的有的字 —— 打錯字的話這條規則靜默失效
    （每筆提案都會入管線，而且沒有任何東西會紅）。"""
    # 正本是 core/schemas.py ProposalPayload.status 的註解所列那六個
    assert PIPELINE_EXEMPT_STATUS in {"草稿", "已提案", "入圍", "成案", "未成案", "擱置"}
