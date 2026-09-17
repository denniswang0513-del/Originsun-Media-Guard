"""薪資（docs/PAYROLL_OVERTIME_PLAN.md §2）：薪資主檔／費率表／每月薪資單（`db.models` 套件的一段，2026-09-17）。

對外一律從 `db.models` 匯入，別直接指名這個檔。
金額欄全部在 core/money.py 的 `_PAYROLL_ONLY` 表態：薪資端點整支 check_admin_or_module('hr_payroll') 檔，
不走 CRM 的抹除層。
"""
from ._base import (Base, Column, DateTime, Float, Index, Integer, JSONB, String, Text, UniqueConstraint, func)


class StaffPayProfile(Base):
    """薪資主檔 — 一人一段期間一列；調薪＝新增一列（effective_from 之後適用），歷史不改寫。"""
    __tablename__ = "staff_pay_profiles"

    id = Column(String(32), primary_key=True)
    staff_id = Column(String(32), nullable=False)                 # soft FK → crm_staff.id
    staff_name = Column(String(64), nullable=False, default="")   # 快照
    effective_from = Column(String(7), nullable=False)            # 'YYYY-MM' 從哪個月起適用
    pay_type = Column(String(8), nullable=False, default="月薪")    # 制度：按月／按時／按日（core.payroll_logic.PAY_TYPES）
    base_amount = Column(Integer, nullable=False, default=0)      # 底薪金額：月薪制＝每月、時薪制＝每小時、日薪制＝每天
    meal_allowance = Column(Integer, nullable=False, default=0)   # 伙食費金額（免稅上限 3,000）
    labor_grade = Column(Integer, nullable=False, default=0)      # 勞保投保金額級距
    health_grade = Column(Integer, nullable=False, default=0)     # 健保投保金額級距
    pension_self_rate = Column(Float, nullable=False, default=0.0)  # 勞退自提比例（%，0–6；不是金額）
    dependents = Column(Integer, nullable=False, default=0)       # 健保眷屬加保人數
    payroll_entity = Column(String(8), nullable=False, default="公司")  # 誰付：公司／代發（股東自己的人，公司只是過帳）
    pay_day = Column(Integer, nullable=False, default=5)          # 每月幾號匯款
    note = Column(Text, nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_spp_staff_from", "staff_id", "effective_from"),
                      UniqueConstraint("staff_id", "effective_from", name="uq_spp_staff_from"))


class PayrollRateTable(Base):
    """費率表 — 一年一份（勞保／健保／勞退費率與級距、加班倍率）。內容整包 JSON，形狀由 core.payroll_logic.normalize_rates 管。"""
    __tablename__ = "payroll_rate_tables"

    id = Column(String(32), primary_key=True)
    year = Column(Integer, nullable=False, unique=True)
    data = Column(JSONB, nullable=False)
    updated_by = Column(String(64), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class PayrollRun(Base):
    """每月薪資單 — 一個月一張；草稿可改可刪，已確認後每列長出請款單（crm_payment_requests）走既有匯款流程。"""
    __tablename__ = "payroll_runs"

    id = Column(String(32), primary_key=True)
    entity = Column(String(16), nullable=False, server_default="parent")   # 只有母公司帳（docs/PAYROLL_OVERTIME_PLAN.md §8）
    month = Column(String(7), nullable=False)                     # 'YYYY-MM' 這張單是哪一個月的
    status = Column(String(8), nullable=False, default="草稿")     # 草稿／已確認
    table_year = Column(Integer, nullable=False, default=0)       # 用哪一年的勞健保級距表
    confirmed_by = Column(String(64), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    note = Column(Text, nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("entity", "month", name="uq_payroll_run_month"),)


class PayrollLine(Base):
    """薪資單一列＝一個人一個月。主檔與費率在產生時快照進來，之後改主檔不會動已產生的列。"""
    __tablename__ = "payroll_lines"

    id = Column(String(32), primary_key=True)
    run_id = Column(String(32), nullable=False)
    staff_id = Column(String(32), nullable=False)
    staff_name = Column(String(64), nullable=False, default="")
    profile_id = Column(String(32), nullable=True)                # 從哪一段主檔算的
    pay_type = Column(String(8), nullable=False, default="月薪")
    payroll_entity = Column(String(8), nullable=False, default="公司")
    work_hours = Column(Float, nullable=False, default=0.0)       # 按時計的人手填的時數（按月計的不用）
    base_amount = Column(Integer, nullable=False, default=0)      # 底薪金額快照
    base_pay = Column(Integer, nullable=False, default=0)         # 底薪那一格的金額
    meal_allowance = Column(Integer, nullable=False, default=0)   # 伙食費金額
    overtime_pay = Column(Integer, nullable=False, default=0)     # 加班費金額（二期由加班申請帶入；一期手填）
    bonus_pay = Column(Integer, nullable=False, default=0)        # 獎金金額
    leave_deduction = Column(Integer, nullable=False, default=0)  # 請假扣薪金額
    other_deduction = Column(Integer, nullable=False, default=0)  # 其他扣款金額
    labor_grade = Column(Integer, nullable=False, default=0)      # 勞保投保金額級距快照
    health_grade = Column(Integer, nullable=False, default=0)     # 健保投保金額級距快照
    dependents = Column(Integer, nullable=False, default=0)
    pension_self_rate = Column(Float, nullable=False, default=0.0)  # 勞退自提比例（%）
    labor_self = Column(Integer, nullable=False, default=0)       # 勞保自負金額
    health_self = Column(Integer, nullable=False, default=0)      # 健保自負金額
    pension_self = Column(Integer, nullable=False, default=0)     # 勞退自提金額
    labor_employer = Column(Integer, nullable=False, default=0)   # 雇主勞保（含職災）金額
    health_employer = Column(Integer, nullable=False, default=0)  # 雇主健保金額
    pension_employer = Column(Integer, nullable=False, default=0) # 雇主勞退提繳金額
    gross_pay = Column(Integer, nullable=False, default=0)        # 應發金額
    net_pay = Column(Integer, nullable=False, default=0)          # 實發金額（匯款）
    employer_total = Column(Integer, nullable=False, default=0)   # 公司總成本金額
    manual_fields = Column(JSONB, nullable=True)                  # 手改過的欄名（畫面標「手改」；重算不覆蓋）
    bank_name = Column(String(64), nullable=True)
    bank_account = Column(String(32), nullable=True)
    payment_request_id = Column(String(32), nullable=True)        # 確認後長出的請款單
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_payroll_line_run", "run_id"),
                      UniqueConstraint("run_id", "staff_id", name="uq_payroll_line_staff"))
