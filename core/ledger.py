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


def hide_mine_projects(request) -> bool:
    """這個請求該不該**看見私帳專案**（owner 2026-08-28「連專案都看不到」）。

    🔴 這是**列的可見性**，不是金額抹除 —— 兩件事：
      · `core.money.redact_mine`：共用的列（客戶、器材…）只藏金額鍵
      · 這一支：私帳**專案**對沒有 mine scope 的人整列不存在
    2026-08-24「專案與客戶全面共用、只有錢分帳」對**專案那一半**因此被推翻
    （客戶那側不變）。

    🔴 每個**列舉專案**的查詢都要問它一次（選單、清單、挑選視窗）。用 id 反查
    名字的地方不必 —— 那是使用者已經在看的那一列，藏掉名字只會讓畫面難懂。
    掃描測試在 tests/unit/test_money_visibility.py 釘住這條分界。
    """
    from core.money import viewer_has_mine_scope
    return not viewer_has_mine_scope(request)


def not_mine(entity_col):
    """SQL 述詞：母公司的金額聚合**排除私帳**（§8）。

    命名一份、兩處聚合（客戶績效、母公司專案毛利）共用 —— 散落的
    `entity != "mine"` 字面沒有可 grep 的名字，第三個聚合點就會從零重新決定。
    """
    return entity_col != MINE

def not_mine_project(model):
    """SQL 述詞：這一列**不屬於私帳專案**（owner 2026-08-28「已經轉到私帳的
    專案不能列入母公司的成本」）。

    給**沒有 entity 欄、只有 project_id** 的成本表用（crm_project_expenses /
    crm_project_cost_lines / crm_project_staff）—— 那些列跟著專案走，專案搬進
    私帳它們就不再是母公司的成本，但表上看不出來。

    🔴 `project_id IS NULL` 要放行：NOT IN 遇到 NULL 整條述詞是 NULL（不是 TRUE），
    沒特別處理的話「沒掛專案的雜支」會被整批篩掉 —— 零用金的一般花費全在那一類。

    🔴 為什麼不是 `not_mine(...)` 就好：那支比的是列自己的 entity 欄，這幾張表
    沒有那一欄。兩個名字分開，才看得出「這一列的帳本是誰說了算」。
    """
    from sqlalchemy import or_, select

    from db.models import CrmProject
    mine = select(CrmProject.id).where(CrmProject.entity == MINE)
    return or_(model.project_id.is_(None), model.project_id.notin_(mine))


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
    - Lv3（access_level >= 3）或 legacy role=='admin' → **母公司**全開（view/full）。
      🔴 mine **不**隨 Lv3 —— owner 2026-08-25：私帳只有 denniswang0513 看得到，
      而生產還有其他 Lv3 帳號。finance_mine 是「指名才有」的模組
      （core.auth.EXPLICIT_ONLY_MODULES：_enrich 不會塞給管理員），這裡也直接看
      modules 清單本身，不走 payload_grants（那條規則對 Lv3 永遠回 True）。
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
    scope: set[str] = set()
    if payload_grants(payload):
        scope.add("parent")               # 管理員＝母公司全開；mine 見下（指名才有）
    elif payload_grants(payload, "crm_invoices") and payload_grants(payload, MONEY_MODULE):
        scope.add("parent")
    elif level == "view" and payload_grants(payload, PARTNER_MODULE):
        scope.add("parent")
    # 🔴 直接看 modules 本身 —— payload_grants 對 Lv3 永遠 True，會把「指名才有」
    # 判成人人有；_enrich 那層已保證管理員的 modules 不含 finance_mine（除非明勾）。
    if MINE_MODULE in (payload.get("modules") or []):
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
# 專案的 entity 幾乎不動（匯入時定；唯一的寫入路徑是「推送至私帳」按鈕，
# 它會呼叫 invalidate_mine_projects()），逐請求查 DB 是純浪費
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


def invalidate_mine_projects() -> None:
    """搬帳本之後把快取丟掉 —— 呼叫端＝`routers/crm/projects.move_project_ledger`
    （全 repo 唯一的 entity 寫入路徑）。不清的話最多一分鐘內，剛搬過去的案子
    還會被 money_dep 當成母公司的。"""
    global _MINE_PROJECT_IDS, _MINE_IDS_AT
    _MINE_PROJECT_IDS, _MINE_IDS_AT = None, 0.0
