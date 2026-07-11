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
# transfer/tax_vat/tax_income/advance/passthrough
SEED_CATEGORY_MAP: list[dict] = [
    # ── source='cash'（收支明細 category，27 值全覆蓋）──
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
    # ── source='payment'（請款單 category，4 值）──
    {"source": "payment", "category_text": "專案外包", "account_code": "5100", "treatment": "direct_expense"},
    {"source": "payment", "category_text": "零用金", "account_code": "1110", "treatment": "transfer"},
    {"source": "payment", "category_text": "轉存", "account_code": "1100", "treatment": "transfer"},
    {"source": "payment", "category_text": "發票代開", "account_code": "4210", "treatment": "passthrough"},
    # ── source='invoice'（發票 category）──
    {"source": "invoice", "category_text": "專案", "account_code": "4100", "treatment": "direct_income"},
    {"source": "invoice", "category_text": "內部代開", "account_code": "4210", "treatment": "passthrough"},
    {"source": "invoice", "category_text": "外部代開", "account_code": "4210", "treatment": "passthrough"},
]


async def seed_finance_stage2(session_factory) -> None:
    """冪等種子：科目（以 code 查）+ 對映（以 (source, category_text) 查）。

    只補缺的列，不覆蓋既有列 — 使用者後台改過的科目/對映不會被還原。
    """
    from sqlalchemy import select
    from db.models import FinanceAccount, FinanceCategoryMap

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
            (src, txt) for src, txt in
            (await session.execute(
                select(FinanceCategoryMap.source, FinanceCategoryMap.category_text))).all()
        }
        added_maps = 0
        for row in SEED_CATEGORY_MAP:
            key = (row["source"], row["category_text"])
            if key in existing_pairs:
                continue
            account_id = code_to_id.get(row["account_code"])
            if not account_id:  # 理論上不會發生（科目種子在前）
                continue
            session.add(FinanceCategoryMap(
                id=uuid.uuid4().hex,
                source=row["source"], category_text=row["category_text"],
                account_id=account_id, treatment=row["treatment"], active=True,
            ))
            added_maps += 1

        if added_accounts or added_maps:
            await session.commit()
            logger.info("[seed_finance] 科目 +%d、對映 +%d", added_accounts, added_maps)
