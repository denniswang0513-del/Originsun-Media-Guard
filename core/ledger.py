"""兩本帳（公司實體）scope 判定 — 正本。

規格：docs/LEDGER_ENTITY_PLAN.md（§2.2 本檔、§2.3 合夥人鐵則）。

錢流資料帶 `entity` 欄（'parent'＝母公司（預設；既有資料全歸此）/
'mine'＝我的帳（owner 私帳）），財務域所有讀寫以 entity 過濾。誰看得到哪本帳，
唯一判定入口就是這裡的 `allowed_entities()` / `require_entity()` —— 各 router
的守衛一律呼叫本模組，不准自己散落判定（否則「合夥人看得到什麼」在 repo 裡
會有好幾份而且必漂）。

scope 分兩層（level）：
- "view"＝看報表（儀表板/三表/drilldown/稅務包）—— 合夥人可及。
- "full"＝記帳/銀行/原始帳列/月結寫入 —— 合夥人的 key 不在此。

🔴 §2.3 合夥人帳號的鐵則（理由照抄 plan，設定帳號前必讀）：

合夥人 = access_level 1 + modules=['finance_partner']，**僅此一把**：

- **絕不給 `money_view`**：橫切金額鑰匙，會讓合夥人在「只要登入」的 CRM 讀取
  端點看到母公司 CRM 明細之外、更重要的是拿到完整財務 UI 的寫入面；報表唯讀
  的邊界就破了。`finance_partner` 本身就含「母公司報表範圍內的金額可見」。
- **絕不給 Lv3**（Lv3 看得到一切）、絕不給 `finance_mine`。
- 合夥人打 CRM 帳務逐筆端點（invoices/cash-entries…）會被 `money_dep` 403，
  打 api_finance 的寫入/銀行/原始帳端點會被 `level="full"` 403 —— 都是刻意的。

循環 import 說明：本模組 import core.auth / core.money 沒問題 —— 兩者都不 import
ledger（core.money 自己也只 import core.auth）。
"""

from fastapi import HTTPException, Request

from core.auth import _extract_token, payload_grants
from core.money import MODULE_KEY as MONEY_MODULE

# 合法實體值。'parent'＝母公司（預設；既有資料全歸此）、'mine'＝我的帳。
ENTITIES = ("parent", "mine")
DEFAULT_ENTITY = "parent"
MINE = "mine"


def not_mine(entity_col):
    """SQL 述詞：母公司的金額聚合**排除私帳**（§8）。

    命名一份、兩處聚合（客戶績效、母公司專案毛利）共用 —— 散落的
    `entity != "mine"` 字面沒有可 grep 的名字，第三個聚合點就會從零重新決定。
    """
    return entity_col != MINE

# 帳本模組 key（core/auth.py ALL_MODULES 尾端）。
# finance_partner＝母公司報表唯讀（合夥人；橫切帳本 key、不是 tab）。
# finance_mine＝我的帳（owner 私帳全功能＋獨立 tab 的入口 key）。
# money_view 的 key 名不在這裡寫死 —— 從 core/money.py（那條規則的正本）拿。
PARTNER_MODULE = "finance_partner"
MINE_MODULE = "finance_mine"


def allowed_entities(payload, level: str = "view") -> set[str]:
    """由 token payload 算出這個人看得到哪幾本帳（scope）。

    payload 為 `core.auth._extract_token` 的結果（dict 或 None）：
    - None（未登入）→ 空 set
    - Lv3（access_level >= 3）或 legacy role=='admin' → 兩本全開（view 與 full 皆是）
    - level="view"（看報表）：
        'parent' ⟺ (modules 含 'crm_invoices' AND 'money_view') OR 'finance_partner'
        'mine'   ⟺ modules 含 'finance_mine'
    - level="full"（記帳/銀行/原始帳列/月結寫入）：
        'parent' ⟺ modules 同時含 'crm_invoices' 與 'money_view'
                   （等同今天 api_finance 的 _guard；合夥人的 key 不在此）
        'mine'   ⟺ modules 含 'finance_mine'
    """
    if not payload:
        return set()
    # 不帶 key 的 payload_grants ＝ 純 admin 判定（core/auth.py 那條規則的正本，
    # 不在這裡複寫 access_level>=3 or role=='admin'）。
    if payload_grants(payload):
        return {"parent", "mine"}
    scope: set[str] = set()
    if payload_grants(payload, "crm_invoices") and payload_grants(payload, MONEY_MODULE):
        scope.add("parent")
    elif level == "view" and payload_grants(payload, PARTNER_MODULE):
        scope.add("parent")
    if payload_grants(payload, MINE_MODULE):
        scope.add("mine")
    return scope


def require_entity(request: Request, entity: str = "", level: str = "view") -> str:
    """驗證請求者對 `entity` 那本帳在 `level` 層有權，回傳解析後的 entity 字串。

    - 未登入 → 401
    - scope 為空（登入但無任何財務檢視權）→ 403
    - entity 空字串 → 預設 'parent'；但 scope 裡沒有 parent 時
      自動落到 scope 裡的那本 —— 前端不帶參數也拿得到自己那本帳
    - entity 非法值 → 422
    - entity 不在 scope → 403
    """
    payload = _extract_token(request)
    if payload is None:
        raise HTTPException(status_code=401, detail="未登入或 token 已過期")
    scope = allowed_entities(payload, level=level)
    if not scope:
        raise HTTPException(status_code=403, detail="沒有財務檢視權限")
    if not entity:
        entity = DEFAULT_ENTITY if DEFAULT_ENTITY in scope else next(iter(scope))
    if entity not in ENTITIES:
        raise HTTPException(status_code=422, detail=f"未知的帳本實體: {entity}")
    if entity not in scope:
        raise HTTPException(status_code=403, detail="沒有該帳本的檢視權限")
    return entity


# ── mine 專案 id 快取（core/money.money_dep 的熱路徑守衛用）──────────────
#
# money 端點（成本明細/財務摘要/雜支…）每個請求都要判「目標專案是不是私帳」。
# 專案的 entity 實務上不可變（匯入時定，API 無改道），逐請求查 DB 是純浪費
# （專案詳情一開就是 4-6 支並發 money 端點）。快取整個 mine id 集合（數百個、
# 幾 KB），TTL 60 秒 —— 就算未來出現 entity 寫入路徑，一分鐘內收斂。
_MINE_PROJECT_IDS: set | None = None
_MINE_IDS_AT: float = 0.0
_MINE_IDS_TTL = 60.0


async def is_mine_project(session_factory, project_id: str) -> bool:
    """project_id 是否屬於私帳（帶 60s TTL 快取）。查不到專案＝False。"""
    import time
    global _MINE_PROJECT_IDS, _MINE_IDS_AT
    now = time.monotonic()
    if _MINE_PROJECT_IDS is None or now - _MINE_IDS_AT > _MINE_IDS_TTL:
        from sqlalchemy import select

        from db.models import CrmProject
        async with session_factory() as session:
            ids = (await session.execute(
                select(CrmProject.id)
                .where(CrmProject.entity == MINE))).scalars().all()
        _MINE_PROJECT_IDS = set(ids)
        _MINE_IDS_AT = now
    return project_id in _MINE_PROJECT_IDS
