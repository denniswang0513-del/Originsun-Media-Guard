"""db/seed_finance.py
---
財務管理階段二種子資料：會計科目表 + category → 科目對映。

在 main.py startup 呼叫：
    await seed_finance_stage2(session_factory)

冪等：科目以 code 查、對映以 (source, category_text) 查，查無才 insert，
絕不覆蓋使用者後台調整過的列。種子科目 is_system=True（不可刪，引擎依賴）。

設計原則：科目代碼藏引擎 — 使用者日常只填中文 category，報表引擎透過
finance_category_map 翻譯成科目與會計處理方式（treatment）。
SEED_CATEGORY_MAP 同時是 tests/unit/test_finance_logic.py 的 fixture 來源。
"""
from __future__ import annotations

import logging
import uuid
from core.finance_logic import INVOICE_PASSTHROUGH_CATEGORIES

logger = logging.getLogger(__name__)

# ── 種子科目表（is_system=True）──────────────────────────────
# (code, name, name_plain, acct_type, cf_activity, pnl_group)
# cf_activity 預設 operating；1500/1509 investing、2400/6410 financing、
# 3xxx/6500 none。pnl_group=None 表示不進損益表（資產/負債/權益科目）。
# pnl_group 值域對齊 owner 現行內部三表 Excel（2026-07-11 定案）：
#   營業收入 / 營業成本-料 / 營業成本-工 / 營業成本-費 /
#   營業費用-銷售 / 營業費用-管理 / 營業費用-研發 / 業外收入 / 業外支出 / 稅
SEED_ACCOUNTS: list[tuple] = [
    # ── 資產 ──
    ("1100", "銀行存款", "公司銀行帳戶裡的錢", "asset", "operating", None),
    ("1110", "零用金", None, "asset", "operating", None),
    ("1200", "應收帳款", "客戶還沒付的錢", "asset", "operating", None),
    ("1300", "員工往來-預支", "預支給同事還沒核銷的錢", "asset", "operating", None),
    # cf_activity=investing：把錢換成金融資產＝投資活動的現金流出（現金流量表
    # 的恆等式要靠它才平 —— 見 core.finance_logic.paired_transfer_ids）
    ("1400", "其他金融資產", "換匯／定存／證券／沒被系統追蹤的銀行帳戶 —— "
     "錢沒被花掉，只是換了個地方放", "asset", "investing", None),
    ("1500", "器材設備", "攝影器材等資產原價", "asset", "investing", None),
    ("1509", "累計折舊", "器材已折舊掉的部分，負值概念", "asset", "investing", None),
    # ── 負債 ──
    ("2100", "應付帳款", "我們還沒付給別人的錢", "liability", "operating", None),
    ("2200", "應付營業稅", "幫政府代收還沒繳的稅", "liability", "operating", None),
    ("2400", "銀行貸款", "欠銀行的錢", "liability", "financing", None),
    # ── 權益 ──
    ("3100", "期初權益", "導入系統時公司的淨值", "equity", "none", None),
    ("3200", "業主往來", "老闆投入/領出", "equity", "none", None),
    ("3900", "累積損益", "開始記帳後賺/虧的累計", "equity", "none", None),
    # ── 收入 ──
    ("4100", "營業收入", None, "income", "operating", "營業收入"),
    ("4200", "其他收入", None, "income", "operating", "業外收入"),
    ("4210", "代開手續費收入", None, "income", "operating", "業外收入"),
    ("4220", "利息收入", None, "income", "operating", "業外收入"),
    ("4230", "貸款補貼收入", "政府貸款利息補貼（如文創貸款貼息）", "income", "operating", "業外收入"),
    # ── 費用（營業成本=製作直接成本 料/工/費；營業費用分 銷售/管理/研發）──
    ("5100", "外包成本", None, "expense", "operating", "營業成本-工"),
    ("5200", "專案雜支", None, "expense", "operating", "營業成本-費"),
    ("6100", "薪資費用", None, "expense", "operating", "營業費用-管理"),
    ("6110", "勞健保", None, "expense", "operating", "營業費用-管理"),
    ("6120", "獎金", None, "expense", "operating", "營業費用-管理"),
    ("6200", "房租", None, "expense", "operating", "營業費用-管理"),
    ("6210", "水電網路", None, "expense", "operating", "營業費用-管理"),
    ("6220", "軟體網路服務", None, "expense", "operating", "營業費用-管理"),
    # 耗材/維護 owner 報表放營業成本（製作直接成本）不放費用
    ("6230", "設備耗材", None, "expense", "operating", "營業成本-料"),
    ("6240", "設備維護", None, "expense", "operating", "營業成本-費"),
    ("6250", "辦公室管理費", None, "expense", "operating", "營業費用-管理"),
    ("6300", "交際應酬", None, "expense", "operating", "營業費用-管理"),
    ("6310", "業務推廣", None, "expense", "operating", "營業費用-銷售"),
    ("6320", "教育訓練", None, "expense", "operating", "營業費用-研發"),
    ("6330", "行政", None, "expense", "operating", "營業費用-管理"),
    ("6340", "會計", None, "expense", "operating", "營業費用-管理"),
    ("6400", "銀行手續費", None, "expense", "operating", "營業費用-管理"),
    ("6410", "利息費用", None, "expense", "financing", "業外支出"),
    ("6500", "折舊費用", None, "expense", "none", "營業成本-費"),  # 器材折舊屬製作成本
    ("6900", "所得稅費用", None, "expense", "operating", "稅"),
    ("6990", "其他費用", None, "expense", "operating", "營業費用-管理"),
]

# ── category → 科目 對映種子（預設值，後台可改）──────────────
# account_code 於 seed 時轉成 finance_accounts.id。
# treatment 全集：direct_expense/direct_income/ap_settlement/ar_settlement/
# transfer/tax_vat/tax_income/advance/passthrough/loan
SEED_CATEGORY_MAP: list[dict] = [
    # ── source='cash'（收支明細 category）—— 原生項目 + 貸款相關（繳款/撥款/
    #    補貼/借款）。數量會隨業務長，別在註解裡寫死一個會過期的數字。
    {"source": "cash", "category_text": "水電網路", "account_code": "6210", "treatment": "direct_expense"},
    {"source": "cash", "category_text": "交際應酬", "account_code": "6300", "treatment": "direct_expense"},
    {"source": "cash", "category_text": "行政", "account_code": "6330", "treatment": "direct_expense"},
    {"source": "cash", "category_text": "其他", "account_code": "6990", "treatment": "direct_expense"},
    {"source": "cash", "category_text": "其他收入", "account_code": "4200", "treatment": "direct_income"},
    {"source": "cash", "category_text": "房租", "account_code": "6200", "treatment": "direct_expense"},
    {"source": "cash", "category_text": "建構", "account_code": "6230", "treatment": "direct_expense"},
    {"source": "cash", "category_text": "專案", "account_code": "4100", "treatment": "direct_income"},
    {"source": "cash", "category_text": "專案外包", "account_code": "5100", "treatment": "direct_expense"},
    {"source": "cash", "category_text": "專案雜支", "account_code": "5200", "treatment": "direct_expense"},
    {"source": "cash", "category_text": "教育訓練", "account_code": "6320", "treatment": "direct_expense"},
    {"source": "cash", "category_text": "設備耗材", "account_code": "6230", "treatment": "direct_expense"},
    {"source": "cash", "category_text": "設備維護", "account_code": "6240", "treatment": "direct_expense"},
    {"source": "cash", "category_text": "軟體網路服務", "account_code": "6220", "treatment": "direct_expense"},
    {"source": "cash", "category_text": "勞健保", "account_code": "6110", "treatment": "direct_expense"},
    {"source": "cash", "category_text": "發票代開", "account_code": "4210", "treatment": "passthrough"},
    {"source": "cash", "category_text": "會計", "account_code": "6340", "treatment": "direct_expense"},
    {"source": "cash", "category_text": "業務推廣", "account_code": "6310", "treatment": "direct_expense"},
    {"source": "cash", "category_text": "製作金", "account_code": "5200", "treatment": "direct_expense"},
    {"source": "cash", "category_text": "銀行利息", "account_code": "4220", "treatment": "direct_income"},
    {"source": "cash", "category_text": "獎金", "account_code": "6120", "treatment": "direct_expense"},
    {"source": "cash", "category_text": "請款單", "account_code": "2100", "treatment": "ap_settlement"},
    {"source": "cash", "category_text": "辦公室管理費", "account_code": "6250", "treatment": "direct_expense"},
    {"source": "cash", "category_text": "營所稅", "account_code": "6900", "treatment": "tax_income"},
    {"source": "cash", "category_text": "營業稅", "account_code": "2200", "treatment": "tax_vat"},
    {"source": "cash", "category_text": "薪資", "account_code": "6100", "treatment": "direct_expense"},
    {"source": "cash", "category_text": "轉存", "account_code": "1100", "treatment": "transfer"},
    # 階段四（銀行貸款）：撥款/繳款皆 treatment='loan' — 不進損益（利息費用權責
    # 按攤還表 due_date 另行認列），CF 走科目 2400 cf_activity=financing
    {"source": "cash", "category_text": "貸款繳款", "account_code": "2400", "treatment": "loan"},
    {"source": "cash", "category_text": "貸款撥款", "account_code": "2400", "treatment": "loan"},
    # 政府貸款貼息（合庫「中心轉存／文創補貼息」、一銀「中小X月」中小企業信保補貼）。
    # 🔴 core/bank_statement.py 的 KEYWORD_RULES 會把這些摘要判成「貸款補貼」，
    # 種子沒有這一列的話，任何從種子建起來的環境都會把每一筆補貼標成「科目未對映」，
    # 三表也會丟進未歸類 —— 對帳單匯入的預覽警告就會變成使用者學會忽略的雜訊。
    {"source": "cash", "category_text": "貸款補貼", "account_code": "4230", "treatment": "direct_income"},
    {"source": "cash", "category_text": "銀行借款", "account_code": "2400", "treatment": "loan"},
    # 零用金整合（docs/PETTY_CASH_PLAN.md）：Sheet 上實際用過的 12 個項目裡，
    # 只有這一個沒有對映（實測 2026-08-17）。歸「專案雜支」同一科目。
    {"source": "cash", "category_text": "後期雜支", "account_code": "5200", "treatment": "direct_expense"},
    # ── source='payment'（請款單 category）──
    #
    # 🔴 零用金核准後產出的應付款，`category` 帶的是**會計項目**（行政／專案雜支
    # ／設備耗材…），而費用認列就發生在請款單這一側（core.finance_logic
    # .iter_expense_items 查的是 ('payment', category)）。原本這裡只有 4 個值，
    # 那些項目查不到就全部掉進「未歸類支出」—— 帳面上看得到錢、看不出花在哪。
    #
    # ⚠️ 不要拿 `零用金` 這個既有的 payment 對映去掛：它是 treatment='transfer'
    # （撥補備用金＝現金在帳戶間搬家，不是費用），掛上去會讓整批請款從損益消失。
    {"source": "payment", "category_text": "專案外包", "account_code": "5100", "treatment": "direct_expense"},
    {"source": "payment", "category_text": "零用金", "account_code": "1110", "treatment": "transfer"},
    {"source": "payment", "category_text": "轉存", "account_code": "1100", "treatment": "transfer"},
    {"source": "payment", "category_text": "發票代開", "account_code": "4210", "treatment": "passthrough"},
    # 零用金會用到的費用項目 —— 科目與 source='cash' 那組**逐一對齊**
    # （同一筆錢不管走收支還是走請款單，都該落在同一個科目）
    {"source": "payment", "category_text": "專案雜支", "account_code": "5200", "treatment": "direct_expense"},
    {"source": "payment", "category_text": "後期雜支", "account_code": "5200", "treatment": "direct_expense"},
    {"source": "payment", "category_text": "行政", "account_code": "6330", "treatment": "direct_expense"},
    {"source": "payment", "category_text": "設備耗材", "account_code": "6230", "treatment": "direct_expense"},
    {"source": "payment", "category_text": "建構", "account_code": "6230", "treatment": "direct_expense"},
    {"source": "payment", "category_text": "設備維護", "account_code": "6240", "treatment": "direct_expense"},
    {"source": "payment", "category_text": "業務推廣", "account_code": "6310", "treatment": "direct_expense"},
    {"source": "payment", "category_text": "軟體網路服務", "account_code": "6220", "treatment": "direct_expense"},
    {"source": "payment", "category_text": "薪資", "account_code": "6100", "treatment": "direct_expense"},
    {"source": "payment", "category_text": "其他", "account_code": "6990", "treatment": "direct_expense"},
    # 「專案」在收支是**收入**（4100）。零用金的單據行不會是收入，但項目下拉是
    # 同一份清單、歷史資料裡也真的有 39 列選了它 —— 對映成收入會讓那 39 列的錢
    # 變成營收。掛成 5200（專案成本）才是它在請款單語境下的意思。
    {"source": "payment", "category_text": "專案", "account_code": "5200", "treatment": "direct_expense"},
    # ── source='invoice'（發票 category）──
    {"source": "invoice", "category_text": "專案", "account_code": "4100", "treatment": "direct_income"},
    # 代開類別由 core.finance_logic.INVOICE_PASSTHROUGH_CATEGORIES 決定 ——
    # 這裡跟那邊各列一份的話，新增一種代開就會「走過路流程但沒有科目對映」
    *({"source": "invoice", "category_text": c, "account_code": "4210",
       "treatment": "passthrough"} for c in INVOICE_PASSTHROUGH_CATEGORIES),
]


async def seed_finance_stage2(session_factory) -> None:
    """冪等種子：科目（以 code 查）+ 對映（以 (entity, source, category_text) 查）。

    只補缺的列，不覆蓋既有列 — 使用者後台改過的科目/對映不會被還原。

    種子那批全是**母公司**的（平的科目）；私帳的收支類別來自分類樹，由
    `backfill_category_map_entity` 判定歸屬，種子不碰。
    """
    from sqlalchemy import select
    from db.models import BankImportRule, FinanceAccount, FinanceCategoryMap

    async with session_factory() as session:
        # 1) 科目：code → id 對照（既有的沿用其 id；新種子 id 直接用 code，可讀好查）
        code_to_id: dict[str, str] = {
            code: acc_id for acc_id, code in
            (await session.execute(select(FinanceAccount.id, FinanceAccount.code))).all()
        }
        added_accounts = 0
        for i, (code, name, name_plain, acct_type, cf_activity, pnl_group) in enumerate(SEED_ACCOUNTS):
            if code in code_to_id:
                continue
            session.add(FinanceAccount(
                id=code,  # 種子科目 id == code（deterministic；後台新增的科目才用 uuid）
                code=code, name=name, name_plain=name_plain,
                acct_type=acct_type, cf_activity=cf_activity,
                pnl_group=pnl_group, is_system=True,
                sort_order=(i + 1) * 10, active=True,
            ))
            code_to_id[code] = code
            added_accounts += 1

        # 2) 對映：查無 (source, category_text) 才 insert
        existing_pairs = {
            (ent, src, txt) for ent, src, txt in
            (await session.execute(
                select(FinanceCategoryMap.entity, FinanceCategoryMap.source,
                       FinanceCategoryMap.category_text))).all()
        }
        added_maps = 0
        for row in SEED_CATEGORY_MAP:
            key = ("parent", row["source"], row["category_text"])
            if key in existing_pairs:
                continue
            account_id = code_to_id.get(row["account_code"])
            if not account_id:  # 理論上不會發生（科目種子在前）
                continue
            session.add(FinanceCategoryMap(
                id=uuid.uuid4().hex, entity="parent",
                source=row["source"], category_text=row["category_text"],
                account_id=account_id, treatment=row["treatment"], active=True,
            ))
            added_maps += 1

        # 3) 對帳單分類規則：種子 = core.bank_statement 原本寫死的那 14 條。
        #    以 (keyword, bank_account_id) 查 —— 使用者刪掉某條就是刪掉了，
        #    不要每次開機又長回來（那會讓「我明明刪過」變成鬧鬼）。
        #    🔴 所以只在**整張表是空的**時候灌，不是逐條補。
        has_rule = (await session.execute(
            select(BankImportRule.id).limit(1))).scalar() is not None
        added_rules = 0
        if not has_rule:
            from core.bank_statement import KEYWORD_RULES
            for i, (kw, cat, direction) in enumerate(KEYWORD_RULES):
                session.add(BankImportRule(
                    id=uuid.uuid4().hex, keyword=kw, bank_account_id=None,
                    category=cat, direction=direction,
                    sort_order=(i + 1) * 10, active=True,
                    note="系統預設（從合庫／一銀對帳單反推）"))
                added_rules += 1

        if added_accounts or added_maps or added_rules:
            await session.commit()
            logger.info("[seed_finance] 科目 +%d、對映 +%d、對帳單規則 +%d",
                        added_accounts, added_maps, added_rules)


async def backfill_category_map_entity(session_factory) -> int:
    """一次性：把 `source='cash'` 的對映判給它真正屬於的那本帳。

    判定＝**這個 category 在不在私帳的分類樹裡**（`ledger_category_domain`）。
    2026-08-30 在生產庫上核對過：這條規則跟「哪本帳實際在用這個類別」零矛盾
    （27 筆只有母公司用、41 筆只有私帳用、0 筆兩本都用），比看有沒有底線可靠
    —— `公司_專案支出` 有底線但不在樹上，是母公司的。

    🔴 只跑一次：任何一列已經是 'mine' 就當作跑過了，直接返回。不這樣的話，
    使用者哪天把某個對映手動改判給另一本帳，下次開機就被改回去。

    `payment`／`invoice` 不碰：那兩種兩本帳共用（私帳的請款用的就是母公司那批
    類別），全部留在 'parent'，讀取端也不按帳本過濾。
    """
    from sqlalchemy import select, update

    from db.models import FinanceCategoryMap

    async with session_factory() as session:
        done = (await session.execute(
            select(FinanceCategoryMap.id)
            .where(FinanceCategoryMap.entity == "mine").limit(1))).scalar()
        if done:
            return 0
        from routers.crm._shared import ledger_category_domain
        mine = await ledger_category_domain(session, "mine")
        if not mine:
            return 0                      # 分類樹還沒種好 —— 下次開機再說
        rows = (await session.execute(
            select(FinanceCategoryMap)
            .where(FinanceCategoryMap.source == "cash"))).scalars().all()
        ids = [r.id for r in rows if r.category_text in mine]
        if not ids:
            return 0
        await session.execute(
            update(FinanceCategoryMap)
            .where(FinanceCategoryMap.id.in_(ids)).values(entity="mine"))
        await session.commit()
        logger.info("[seed_finance] 科目對映帳本回填：%d 筆判給私帳", len(ids))
        return len(ids)
