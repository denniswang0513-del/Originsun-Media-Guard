# -*- coding: utf-8 -*-
"""db/seed_cash_taxonomy.py
---
私帳收支分類樹的種子 + 回填（owner 2026-08-27 給的 Google Sheet `索引` 分頁）。

在 main.py startup 呼叫：
    await seed_cash_taxonomy(session_factory)

**冪等**：節點以 (entity, parent_id, name) 查，查無才 insert —— 絕不覆蓋 owner
在後台改過的排序／停用／改名。回填只填 `taxonomy_node_id IS NULL` 的列。

🔴 種子不是唯一來源：灌完正本後還會掃一次 `crm_cash_entries` 實際用過的組合，
把正本沒列到的補上。不補的話，那些歷史分類在下拉裡會**挑不到**（實測 Sheet 的
`個人_固定支出` 沒列「家電」，但帳上有一筆）—— 正本負責完整與排序，歷史負責不消失。

樹的形狀與「為什麼是樹」見 db.models.CashTaxonomyNode 的 docstring。
"""
from __future__ import annotations

import copy
import logging
import uuid

from core.cash_taxonomy import SEP, path_from_columns

logger = logging.getLogger(__name__)

ENTITY = "mine"


def _leaf(*names) -> dict:
    """一串沒有下層的節點。"""
    return {n: {} for n in names}


# ── 個人／家用同名的 8 組共用同一份子項目 ──────────────────────────
# Sheet 只畫了「家用」那 8 組的第三層，個人的同名 8 組沒畫；帳上資料印證兩邊
# 用的是同一份（`個人_被動收入` 用的正是 政府補助／信用卡回饋／利息／股息／退稅）。
# 定義一次、兩邊各 deepcopy 一份 —— 樹裡它們是兩棵子樹（日後可各自演化），
# 但出生時保證一致。
HOUSE_GROUPS: dict = {
    "主動收入": _leaf("薪資", "獎金", "士源家用", "陳陵家用", "轉匯"),
    "被動收入": _leaf("政府補助", "紅包", "租金", "信用卡回饋", "利息", "股息",
                      "外匯", "股票", "ETF", "退稅", "轉匯"),
    "借款支出": _leaf("信用卡", "親友借款", "抵押貸款", "汽車貸款", "學生貸款", "轉匯"),
    "固定支出": _leaf("保險", "電信", "串流服務", "水電瓦斯", "家俱", "家電",
                      "雜貨", "孩童教育", "汽車保養", "轉匯"),
    "變動支出": _leaf("交通", "燃油", "食材採買", "外出用餐", "孩童活動", "衣著",
                      "社交活動", "各種快樂", "國內旅遊", "出國度假", "紅包", "禮物",
                      "進修", "書籍", "線上課程", "生活用品", "雜支", "醫療保健", "轉匯"),
    "稅負支出": _leaf("所得稅", "房屋稅", "地價稅", "燃料稅", "牌照稅", "轉匯"),
    "投資支出": _leaf("股息", "利息", "外匯", "股票", "ETF", "轉匯"),
    "其他支出": _leaf("帳務對齊", "信用卡款", "轉匯"),
}

_HOUSE = copy.deepcopy(HOUSE_GROUPS)
# 🔴 唯一長到第五層的一支（Sheet 裝不下，被 owner 溢出到「款別」「專案標籤」兩欄）：
#     家用 ▸ 變動支出 ▸ 醫療保健 ▸ 乳癌治療 ▸ 就醫地點
# 帳上 62 筆醫療保健有 60 筆是這一支，醫院名現在躺在 project_label（55 筆），
# 階段 4 會搬進來並把 project_label 清掉 —— 那一欄要留給真專案。
_HOUSE["變動支出"]["醫療保健"] = {
    "乳癌治療": _leaf("內湖禾馨", "台北馬偕", "台北中心診所", "淡水馬偕"),
}

CANONICAL: dict = {
    "公司": {
        # 第三層＝領薪的人（Sheet X 欄，與帳上 343 筆的子項目一字不差）
        "薪水": _leaf("眷屬加保", "王士源", "郭昭君", "連婕妤", "蔡念栩", "陳陵",
                      "劉禮瑜", "陳偉健", "陳妍蓁", "陳美如", "陳嶸", "黃柔依",
                      "林依靜", "趙宇晨", "葉念如"),
        "代墊": {},
        # 第三層＝這筆錢在案子裡的角色（Sheet 的「款別」欄，610 筆）。
        # 專案是另一軸，走 crm_cash_entries.project_id，不在樹裡再存一次。
        "專案": _leaf("已收帳款", "已付稅款", "委外已付"),
        "教育訓練": {},
        "餐敘": {},
        "軟體與耗材": {},
        "器材": {},
        "其他": {},
        "預收款": {},
        "股東往來": {},
        "股利": {},
        "年終獎金": {},
        "業績獎金": {},
        "加班": {},
    },
    "個人": {
        "贈予": _leaf("紅包", "禮物", "轉匯"),
        "生活": _leaf("薪資", "獎金", "信用卡回饋", "士源家用", "交通", "油資",
                      "衣著", "食材採買", "外出用餐", "雜支", "保險", "電信",
                      "生活用品", "串流服務", "轉匯"),
        "學習": _leaf("進修", "書籍", "線上課程", "運動", "轉匯"),
        "旅遊": _leaf("國內旅遊", "出國度假", "轉匯"),
        "投資": _leaf("股息", "利息", "外匯", "股票", "ETF", "轉匯"),
        "娛樂": _leaf("社交活動", "各種快樂", "轉匯"),
        **copy.deepcopy(HOUSE_GROUPS),
    },
    "家用": _HOUSE,
    "轉匯與定存": _leaf("台幣定存", "外幣定存", "公司信用卡", "代收款", "代支款", "證券"),
    "信用卡": {},          # 卡費那條流動，沒有第二層
}


def canonical_paths(tree=None, prefix=()) -> list:
    """把 CANONICAL 攤平成路徑清單 —— 給測試與比對用。"""
    out = []
    for name, kids in (CANONICAL if tree is None else tree).items():
        path = prefix + (name,)
        out.append(path)
        out.extend(canonical_paths(kids, path))
    return out


# ── 種子 ────────────────────────────────────────────────────────────
def _ensure_child(session, cache, entity, parent_id, name, depth, sort):
    """回傳 (node_id, created)。冪等：查得到就用既有的，不覆蓋任何欄位。
    純記憶體＋session.add，沒有 I/O —— 所以是同步函式。"""
    from db.models import CashTaxonomyNode

    key = (entity, parent_id, name)
    if key in cache:
        return cache[key], False
    node = CashTaxonomyNode(
        id=uuid.uuid4().hex, entity=entity, parent_id=parent_id,
        name=name, depth=depth, sort=sort, active=1,
    )
    session.add(node)
    cache[key] = node.id
    return node.id, True


def _walk(session, cache, entity, tree, parent_id, depth, stats):
    for i, (name, kids) in enumerate(tree.items()):
        nid, created = _ensure_child(session, cache, entity, parent_id,
                                     name, depth, i)
        if created:
            stats["created"] += 1
        _walk(session, cache, entity, kids, nid, depth + 1, stats)


async def seed_cash_taxonomy(session_factory) -> dict:
    """灌正本 → 補上帳上用過但正本沒列的 → 回填 entry 的 taxonomy_node_id。"""
    from sqlalchemy import select
    from db.models import CashTaxonomyNode, CrmCashEntry

    stats = {"created": 0, "from_usage": 0, "linked": 0}
    async with session_factory() as session:
        cache = {(r.entity, r.parent_id, r.name): r.id for r in (
            await session.execute(select(CashTaxonomyNode))).scalars()}

        _walk(session, cache, ENTITY, CANONICAL, "", 1, stats)
        await session.flush()

        # 帳上用過、正本沒列的組合 —— 補進去，否則歷史分類在下拉裡挑不到
        used = (await session.execute(
            select(CrmCashEntry.category, CrmCashEntry.sub_item)
            .where(CrmCashEntry.entity == ENTITY,
                   CrmCashEntry.category.isnot(None),
                   CrmCashEntry.category != "")
            .distinct())).all()
        for cat, sub in used:
            # columns→path 的規則走 path_from_columns —— 跟寫入端反查節點
            # （finance._sync_taxonomy）同一份，各拼各的兩邊就會歧義
            path = path_from_columns(cat, sub)
            pid, depth = "", 1
            for name in path:
                pid, created = _ensure_child(session, cache, ENTITY, pid,
                                             name, depth, 999)
                if created:
                    stats["from_usage"] += 1
                depth += 1
        await session.commit()

    stats["linked"] = await backfill_taxonomy_nodes(session_factory)
    if stats["created"] or stats["from_usage"] or stats["linked"]:
        logger.info("[cash-taxonomy] 正本 %d 節點／補歷史 %d 節點／回填 %d 列",
                    stats["created"], stats["from_usage"], stats["linked"])
    return stats


# ── 回填 ────────────────────────────────────────────────────────────
# 三句 UPDATE 依深度由深到淺：先試三層（有子項目），再兩層，最後只有類別。
# 全部只碰 `taxonomy_node_id IS NULL`，冪等；跑完之後每次 boot 只會掃到真正
# 分不出類別的那些列（帳上 270 筆空類別），成本可忽略。
_BACKFILL_SQL = [
    # 三層：category = 類別_項目，sub_item = 子項目
    f"""
    UPDATE crm_cash_entries e SET taxonomy_node_id = n3.id
      FROM cash_taxonomy_nodes n1
      JOIN cash_taxonomy_nodes n2 ON n2.parent_id = n1.id AND n2.entity = n1.entity
      JOIN cash_taxonomy_nodes n3 ON n3.parent_id = n2.id AND n3.entity = n1.entity
     WHERE n1.parent_id = '' AND n1.entity = e.entity
       AND e.taxonomy_node_id IS NULL
       AND e.category = n1.name || '{SEP}' || n2.name
       AND e.sub_item = n3.name
    """,
    # 兩層：有項目、沒子項目
    f"""
    UPDATE crm_cash_entries e SET taxonomy_node_id = n2.id
      FROM cash_taxonomy_nodes n1
      JOIN cash_taxonomy_nodes n2 ON n2.parent_id = n1.id AND n2.entity = n1.entity
     WHERE n1.parent_id = '' AND n1.entity = e.entity
       AND e.taxonomy_node_id IS NULL
       AND e.category = n1.name || '{SEP}' || n2.name
       AND COALESCE(e.sub_item, '') = ''
    """,
    # 一層：只有類別（`信用卡`、`轉匯與定存` 那 340 筆）
    """
    UPDATE crm_cash_entries e SET taxonomy_node_id = n1.id
      FROM cash_taxonomy_nodes n1
     WHERE n1.parent_id = '' AND n1.entity = e.entity
       AND e.taxonomy_node_id IS NULL
       AND e.category = n1.name
       AND COALESCE(e.sub_item, '') = ''
    """,
]


async def backfill_taxonomy_nodes(session_factory) -> int:
    """把「有 category 但沒掛節點」的既有私帳收支補上 taxonomy_node_id，回補到幾筆。

    SQL 冪等（都帶 taxonomy_node_id IS NULL 條件），開機每次跑、補完自然歸零。
    沒掛節點的列在收支明細的樹狀篩選裡看不到 —— 這支就是在清那種孤兒。
    """
    from sqlalchemy import text

    n = 0
    async with session_factory() as session:
        for sql in _BACKFILL_SQL:
            n += (await session.execute(text(sql))).rowcount or 0
        await session.commit()
    return n
