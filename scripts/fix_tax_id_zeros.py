# -*- coding: utf-8 -*-
"""把已經存進 DB、缺前導 0 的統編補回 8 碼。

    .venv/Scripts/python.exe scripts/fix_tax_id_zeros.py            # dry-run
    .venv/Scripts/python.exe scripts/fix_tax_id_zeros.py --apply
    .venv/Scripts/python.exe scripts/fix_tax_id_zeros.py --apply --prod

一次性腳本。成因不在系統（後端只做 .strip()）而在來源試算表：Excel／Sheets 把
`00973926` 存成數字 973926，匯出 CSV 就少了兩個 0。寫入端的正規化已經補上
（core.crm_logic.normalize_tax_id + 兩個 payload 的 validator + 兩支 CSV 匯入），
這支只負責把**已經進去的**歷史值補回來。

規則就是 normalize_tax_id：純數字且不足 8 碼 → 左補 0。含字母的（身分證）、
已經 8 碼以上的、空的，一律不動。
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _rule():
    """從 **repo** 的檔案路徑明確載入規則，不靠 sys.path。

    🔴 `--prod` 會把 `C:\\OriginsunAgent` 插到 sys.path 最前面（要用生產的
    settings 才連得到生產庫），而那邊還沒部署這支新函式 —— 直接 import 會拿到
    舊的 core.crm_logic 然後 ImportError。規則只有一份正本，這裡用檔案路徑取。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_repo_crm_logic", REPO / "core" / "crm_logic.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.normalize_tax_id


async def run(apply: bool):
    from sqlalchemy import select

    from db.models import Client, CrmInvoice
    from db.session import get_session_factory, init_db
    normalize_tax_id = _rule()

    await init_db()
    async with get_session_factory()() as s:
        total = 0
        for model, label, name_col in ((Client, "客戶", Client.short_name),
                                       (CrmInvoice, "發票", CrmInvoice.company_name)):
            rows = (await s.execute(select(model))).scalars().all()
            hits = []
            for r in rows:
                old = (r.tax_id or "")
                new = normalize_tax_id(old)
                if new != old.strip() or (old and old != old.strip()):
                    if new != old:
                        hits.append((r, old, new))
            print(f"\n=== {label}：{len(rows)} 筆，要補的 {len(hits)} 筆 ===")
            seen = set()
            for r, old, new in hits:
                nm = getattr(r, name_col.key) or ""
                line = f"   {nm[:26]:<28} {old!r} → {new!r}"
                if line not in seen:
                    print(line)
                    seen.add(line)
                if apply:
                    r.tax_id = new
            total += len(hits)
        if apply:
            await s.commit()
            print(f"\n✅ 已更新 {total} 筆")
            # 覆核：重讀一次，確認沒有殘留
            left = 0
            for model in (Client, CrmInvoice):
                for r in (await s.execute(select(model))).scalars().all():
                    t = (r.tax_id or "").strip()
                    if t and t.isdigit() and len(t) < 8:
                        left += 1
            print(f"覆核：仍不足 8 碼的純數字統編 {left} 筆"
                  + ("（乾淨）" if left == 0 else " 🔴"))
        else:
            print(f"\n（dry-run，共 {total} 筆會被改。要寫請加 --apply）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--prod", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, str(REPO))
    if a.prod:
        sys.path.insert(0, r"C:\OriginsunAgent")
        os.chdir(r"C:\OriginsunAgent")
    print(f"目標：{'生產 mediaguard' if a.prod else 'dev mediaguard_dev'}"
          f"｜模式：{'APPLY' if a.apply else 'dry-run'}")
    asyncio.run(run(a.apply))


if __name__ == "__main__":
    main()
