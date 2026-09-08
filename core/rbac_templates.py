# -*- coding: utf-8 -*-
"""身份範本（owner 2026-09-08）：三個人員身份各一組「預設就有」的鑰匙。純規則，無 I/O。

四個身份＝管理員（Lv3，什麼都有、不套範本）＋ 合夥／在職／兼職（＝ crm_staff.status）。
範本存 settings.json `rbac.templates`（{"合夥": [...], "在職": [...], "兼職": [...]}），套用＝寫回帳號自己那份 modules。
**不是角色層**：帳號上仍是自己的 modules，守衛與 token 都不看範本。

合夥＝owner 拍板「除了私帳以外全部」；在職／兼職是建議值，owner 在使用者管理的「身份範本」分頁勾。
"""
from typing import Dict, Iterable, List, Optional

from core.auth import ALL_MODULES, ME_ZONE_MASTER, ME_ZONE1_KEYS, MODULE_BUNDLES

IDENTITIES = ("合夥", "在職", "兼職")

_PARTNER_EXCLUDE = {"finance_mine"}          # 私帳：指名才有，合夥也不給
_HIDDEN = {"me_todos", "me_finance"}          # 員工頁還沒放回的卡，範本不勾

_STAFF = [
    "preprod", "references", "postprod", "comfyui",
    "projects", "crm_clients", "crm_projects", "media_log", "website_admin", "journal",
    "me_profile", "me_today_zone", "me_worklog", "me_week_plan", "me_team_week", "me_project_lookup",
    "me_plan_parttime", "me_leave", "me_petty", "me_benefits", "me_projects",
]
_PARTTIME = ["me_profile", "me_today_zone", "me_worklog", "me_week_plan", "me_team_week", "me_project_lookup", "website_admin"]

DEFAULT_TEMPLATES: Dict[str, List[str]] = {
    "合夥": [m for m in ALL_MODULES if m not in _PARTNER_EXCLUDE and m not in _HIDDEN],
    "在職": [m for m in ALL_MODULES if m in _STAFF],
    "兼職": [m for m in ALL_MODULES if m in _PARTTIME],
}


def normalize(templates: Optional[dict]) -> Dict[str, List[str]]:
    """只留 ALL_MODULES 裡的鑰匙、照 ALL_MODULES 排序；有任一把「今天與這週」子鑰匙就補總開關（後端兩道都要）。
    缺的身份用預設值補。"""
    out: Dict[str, List[str]] = {}
    src = templates if isinstance(templates, dict) else {}
    for ident in IDENTITIES:
        raw = src.get(ident)
        if not isinstance(raw, list):
            out[ident] = list(DEFAULT_TEMPLATES[ident])
            continue
        raw_set = {k for k in raw if isinstance(k, str)}
        for bundle, members in MODULE_BUNDLES.items():          # 存過的範本可能還是成員鍵：有任一成員就收成那把捆
            if raw_set & set(members):
                raw_set.add(bundle)
        keep = {k for k in raw_set if k in ALL_MODULES}
        if keep & set(ME_ZONE1_KEYS):
            keep.add(ME_ZONE_MASTER)
        if keep & {"crm_invoices", "crm_quotes"}:
            keep.add("money_view")     # 第三批一把尺：帳務與報價的讀寫都要金額檢視，範本裡一定配
        out[ident] = [m for m in ALL_MODULES if m in keep]
    return out


def template_for(status: Optional[str], templates: Dict[str, List[str]]) -> Optional[List[str]]:
    """人員身份 → 範本；空白視同在職（同 core.hr_logic.ACTIVE_STATUSES）；專案／單位等沒有範本 → None。"""
    s = (status or "").strip() or "在職"
    return list(templates[s]) if s in templates else None


def diff(modules: Iterable[str], template: Iterable[str]) -> Dict[str, List[str]]:
    """帳號現況 vs 範本：多給的（extra）與少的（missing），都照 ALL_MODULES 排。"""
    have, want = set(modules or []), set(template or [])
    return {"missing": [m for m in ALL_MODULES if m in want - have],
            "extra": [m for m in ALL_MODULES if m in have - want]}
