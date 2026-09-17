"""薪資（routers/api_payroll.py）的請求模型。"""
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class PayProfileCreate(BaseModel):
    """POST /hr/payroll/profiles：一人一段。labor_grade／health_grade 留空 → 後端照底薪＋伙食費帶預設級距。"""
    staff_id: str
    effective_from: str                      # 'YYYY-MM'
    pay_type: str = "月薪"                    # 月薪／時薪／日薪
    base_amount: int = Field(0, ge=0)
    meal_allowance: int = Field(0, ge=0)
    labor_grade: Optional[int] = None
    health_grade: Optional[int] = None
    pension_self_rate: float = Field(0.0, ge=0, le=6)
    dependents: int = Field(0, ge=0, le=9)
    payroll_entity: str = "公司"               # 公司／代發
    pay_day: int = Field(5, ge=1, le=31)
    note: Optional[str] = None


class PayProfileUpdate(BaseModel):
    """PUT /hr/payroll/profiles/{id}：只改給的欄位；staff_id 不能改。"""
    effective_from: Optional[str] = None
    pay_type: Optional[str] = None
    base_amount: Optional[int] = Field(None, ge=0)
    meal_allowance: Optional[int] = Field(None, ge=0)
    labor_grade: Optional[int] = Field(None, ge=0)
    health_grade: Optional[int] = Field(None, ge=0)
    pension_self_rate: Optional[float] = Field(None, ge=0, le=6)
    dependents: Optional[int] = Field(None, ge=0, le=9)
    payroll_entity: Optional[str] = None
    pay_day: Optional[int] = Field(None, ge=1, le=31)
    note: Optional[str] = None


class RateTablePut(BaseModel):
    """PUT /hr/payroll/rates/{year}：整包費率（形狀由 core.payroll_logic.normalize_rates 收斂）。"""
    data: Dict[str, Any]


class PayrollRunCreate(BaseModel):
    """POST /hr/payroll/runs：產生某個月的薪資單草稿。"""
    month: str
    note: Optional[str] = None


class PayrollLineUpdate(BaseModel):
    """PUT /hr/payroll/runs/{id}/lines/{line_id}：只收手填欄（core.payroll_logic.LINE_EDITABLE）。"""
    work_hours: Optional[float] = Field(None, ge=0, le=744)
    overtime_pay: Optional[int] = Field(None, ge=0)
    bonus_pay: Optional[int] = Field(None, ge=0)
    leave_deduction: Optional[int] = Field(None, ge=0)
    other_deduction: Optional[int] = Field(None, ge=0)
    note: Optional[str] = None
