# -*- coding: utf-8 -*-
"""軌道範本（core/project_flow）與訊號實作（routers/crm/flow）必須對齊。

🔴 這是整個工作流設計**唯一的靜默失效點**：範本宣告了一個 AUTO 項、router
忘了算（或 item_key 打錯字），`build()` 就把它畫成「未完成」——那盞燈永遠
不會亮，而且沒有任何錯誤、沒有 log、沒有例外。

第一版的測試是**恆真的**：它拿 AUTO_KEYS 造一個 dict、再丟回 missing_facts
比對，等於自己跟自己比 —— router 漏算一個訊號時它照樣綠燈。這支改成真的
呼叫 `_gather_facts`，用假的 session 接住那唯一一次 SQL，比對它**實際**
產出的鍵。
"""
from core import project_flow as pf


class _Row:
    """聚合查詢那一列 —— **只認得 SELECT 真的有選的欄位**。

    🔴 一律 `__getattr__ → 0` 的話會開出第二個靜默失效點：把 `.label("h_loc")`
    改名卻忘了改 `row.h_loc`，測試照樣綠、真的 Row 會 AttributeError。
    所以用 statement 的實際欄位名建，未知名稱就炸。
    """
    def __init__(self, names):
        self._names = set(names)

    def __getattr__(self, name):
        # __getattr__ 只在正常查找失敗時才進來，所以 self._names 走不到這裡
        if name in self._names:
            return 0
        raise AttributeError(
            f"聚合查詢沒有選 `{name}` —— label 與讀取端對不上（打錯字？）")


class _Result:
    def __init__(self, names):
        self._names = names

    def one(self):
        return _Row(self._names)

    def all(self):
        return []          # 沒有 showcase 作品列


class _Session:
    """只接住 execute() —— _gather_facts 只跟 session 要這個。"""
    def __init__(self):
        self.calls = 0

    async def execute(self, stmt, *_a, **_kw):
        self.calls += 1
        try:
            names = list(stmt.selected_columns.keys())
        except AttributeError:
            names = []
        return _Result(names)


class _Project:
    id = "p1"
    name = "測試專案"
    client_id = ""
    archive_checklist = None
    review_kpta = None
    website_prod_stage = None
    flow_checks = None


async def test_gather_facts_covers_every_auto_signal():
    """router 算出來的鍵，必須蓋過範本宣告的每一個 AUTO 項。"""
    from routers.crm.flow import _gather_facts
    facts, _detail, _meta = await _gather_facts(_Session(), _Project())

    missing = pf.missing_facts(facts)
    assert not missing, (
        f"範本宣告了這些 AUTO 訊號但 routers/crm/flow.py 沒算："
        f"{'、'.join(missing)} —— 那幾盞燈會永遠不亮且沒有任何錯誤")


async def test_gather_facts_has_no_stray_keys():
    """反向：router 算了範本沒有的鍵 = 打錯字或範本刪項時的殘留。"""
    from routers.crm.flow import _gather_facts
    facts, _detail, _meta = await _gather_facts(_Session(), _Project())

    stray = sorted(set(facts) - set(pf.AUTO_KEYS))
    assert not stray, f"這些鍵不在軌道範本裡（打錯字？）：{stray}"


async def test_gather_facts_stays_at_two_queries():
    """一趟聚合 + 一趟 showcase。回到逐支 await 的話這裡會紅。

    連線池只有 5 條（pool_timeout=5s）—— 每個訊號一趟查詢的話，四個人同時
    開進度分頁就把池子吃光。
    """
    s = _Session()
    from routers.crm.flow import _gather_facts
    await _gather_facts(s, _Project())
    # `<=` 不是 `==`：要擋的是回到「一個訊號一趟查詢」，而不是擋住「把
    # showcase 那趟也併進聚合」這種正向改動
    assert s.calls <= 2, f"_gather_facts 發了 {s.calls} 個查詢（應 ≤ 2）"


# 這裡曾有一支 `test_every_auto_item_is_reachable_from_a_track` —— 它斷言
# AUTO_KEYS 的每一項都在 ITEMS 裡、且掛在某條軌道上。但 AUTO_KEYS 是從
# ITEMS 推導、ITEMS 是從 TRACKS 推導（project_flow.py），所以它**恆真**，
# 20 個綠燈買不到任何東西 —— 正是本檔開頭批評第一版的那個毛病。已刪。
