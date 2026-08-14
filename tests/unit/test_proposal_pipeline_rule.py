# -*- coding: utf-8 -*-
"""「這筆提案該不該補殼專案」—— `api_proposals.enters_pipeline` 的正反面。

為什麼值得一支單元測試：這條規則有三個消費者（`update_proposal` 的自癒尾巴、
startup 的存量遷移、進度分頁自癒 CTA 打的 `create_shell_for_proposal`），
三個都要真 DB —— 在這支之前，這條規則唯一的守門是一支只有在 dev/test 資料庫
上才跑得起來的 e2e。

規則本身是純函式，所以這裡不需要 DB、不需要 session。
"""
from types import SimpleNamespace as NS

import pytest

from routers.api_proposals import (PIPELINE_EXEMPT_STATUS,
                                   _PROP_STATUS_TO_PROJECT_STATUS,
                                   enters_pipeline)


# 空字串是這裡唯一有趣的另一個值（`(prop.status or "")` 那一段）—— 其餘狀態
# 走的是同一條分支，多列幾個只是讓下一個讀的人以為它們各自證明了什麼
@pytest.mark.parametrize("status", ["", "草稿"])
def test_pipeline_less_proposals_get_a_shell(status):
    assert enters_pipeline(NS(project_id=None, status=status)) is True


def test_the_exempt_status_stays_out():
    """已停擺的提案刻意不進管線洗版 —— 這是唯一的豁免。"""
    assert enters_pipeline(NS(project_id=None, status=PIPELINE_EXEMPT_STATUS)) is False


@pytest.mark.parametrize("status", ["草稿", PIPELINE_EXEMPT_STATUS])
def test_already_linked_never_gets_a_second_shell(status):
    """已經有專案就不再建 —— 遷移每台機隊 startup 都會跑，重複建殼是災難。"""
    assert enters_pipeline(NS(project_id="p1", status=status)) is False


def test_exempt_status_is_a_real_proposal_status():
    """豁免值必須是提案狀態表裡真的有的字 —— 打錯字的話這條規則靜默失效
    （每筆提案都會入管線，而且沒有任何東西會紅；另外三支測試都用符號，
    所以打錯字它們照樣綠）。

    比對的是**執行時真的在用**的那份對照表（`_create_shell_project` 拿它決定
    殼專案的階段），不是另抄一份狀態清單 —— 抄的那份會跟著一起改名，偵測不到
    它宣稱要偵測的漂移。
    """
    assert PIPELINE_EXEMPT_STATUS in _PROP_STATUS_TO_PROJECT_STATUS
