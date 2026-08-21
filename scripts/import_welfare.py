# -*- coding: utf-8 -*-
"""把 Notion「🦥 後期福委會」匯進系統（docs/BENEFIT_POOL_PLAN.md）。

    .venv/Scripts/python.exe scripts/import_welfare.py             # dry-run（預設）
    .venv/Scripts/python.exe scripts/import_welfare.py --apply     # 寫進 dev
    .venv/Scripts/python.exe scripts/import_welfare.py --apply --prod   # 寫進生產

資料在 `scripts/data/welfare_notion.json`（帶 checksum，載入時對帳，抄錯就中止）。

映射：
    聚餐 → 池「快樂」（owner 說的名字）        進修 → 池「進修」
    收支 > 0（年度預算進款）→ 撥款 hr_benefit_fundings
    收支 < 0                → 員工登記 hr_benefit_entries（金額取絕對值）
    發款 ✓ → 已付款          發款 ✗ → **待審**（見下）

🔴 未發款的那 3 筆匯成「待審」而不是「已核准」：
   已核准會在系統裡對應一張應付款（進應付帳款），而那張單只能由 approve 端點產出
   —— 匯入腳本自己塞一張，等於繞過那條路的冪等與守衛。讓它們落在待審佇列，
   owner 按一下核准就會正常產生應付款、進應付帳款、然後可以付。
   代價：匯完當下「進修」餘額會是 35,897（比 Notion 的 34,907 多 990），
   owner 核准那 3 筆之後就會回到 34,907。這是刻意的，不是誤差。

🔴 冪等：以 (池, 日期, 項目, 金額) 為鍵，已存在就跳過。重跑不會變兩份。

🔴 「美如」在 crm_staff 查無此人（生產 151 人裡沒有）—— 不亂配對，
   staff_id 留空、只存姓名快照（staff_name 這個欄位本來就是為此存在）。
   其餘四人（士源→王士源、念栩→蔡念栩、禮瑜→劉禮瑜、婕妤→連婕妤）唯一命中。
"""
import argparse
import asyncio
import io
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "scripts" / "data" / "welfare_notion.json"

POOL_OF = {"聚餐": "快樂", "進修": "進修"}
FUND_TITLE = "年度預算進款"


def load_rows():
    """讀資料檔並對帳 —— checksum 對不上就中止（抄錯 67 筆是很容易的事）。"""
    d = json.load(io.open(DATA, encoding="utf-8"))
    for name in POOL_OF:
        rows, c = d[name], d["_checksum"][name]
        inc = sum(r[2] for r in rows if r[2] > 0)
        exp = sum(r[2] for r in rows if r[2] < 0)
        if not (len(rows) == c["rows"] and inc == c["income"]
                and exp == c["expense"]):
            sys.exit(f"🔴 {name} 對帳不符：{len(rows)}列/{inc}/{exp} "
                     f"vs 期望 {c['rows']}/{c['income']}/{c['expense']}")
    return d


async def run(apply: bool):
    from sqlalchemy import select

    from core.finance_logic import local_day
    from db.models import (CrmStaff, HrBenefitEntry, HrBenefitFunding,
                           HrBenefitPool)
    from db.session import get_session_factory, init_db

    data = load_rows()
    await init_db()
    async with get_session_factory()() as s:
        # ── 人名 → crm_staff（唯一命中才算；查無此人只留姓名快照）──
        staff = (await s.execute(select(CrmStaff.id, CrmStaff.name))).all()
        who = {}
        for short in sorted({r[3] for n in POOL_OF for r in data[n] if r[3]}):
            hits = [(i, n) for i, n in staff if n and short in n]
            who[short] = hits[0] if len(hits) == 1 else None
            mark = f"→ {hits[0][1]}" if len(hits) == 1 else (
                f"🔴 {len(hits)} 筆命中，不配對（只留姓名）")
            print(f"  人名對照 {short:<4} {mark}")

        made = {"pool": 0, "funding": 0, "entry": 0, "skip": 0}
        for sheet, pool_name in POOL_OF.items():
            pool = (await s.execute(select(HrBenefitPool).where(
                HrBenefitPool.name == pool_name,
                HrBenefitPool.entity == "parent"))).scalar_one_or_none()
            if pool is None:
                pool = HrBenefitPool(id=uuid.uuid4().hex[:16], entity="parent",
                                     name=pool_name,
                                     sort_order=0 if pool_name == "快樂" else 1,
                                     notes="自 Notion 後期福委會匯入")
                if apply:
                    s.add(pool)
                    await s.flush()
                made["pool"] += 1
                print(f"\n[{sheet} → 池「{pool_name}」] 新建")
            else:
                print(f"\n[{sheet} → 池「{pool_name}」] 已存在，沿用")

            # 冪等鍵：撥款用 (年,金額)、登記用 (日期,項目,金額)
            have_f = {(f.year, int(f.amount or 0)) for f in (await s.execute(
                select(HrBenefitFunding).where(
                    HrBenefitFunding.pool_id == pool.id))).scalars().all()
                } if pool.id else set()
            # 🔴 日期一定要過 local_day 再比：spend_date 是 timestamptz，
            # 回讀是 UTC 表示，直接 strftime 會少一天 → 冪等鍵永遠對不上，
            # 重跑就整份再匯一次（實測 dev 上真的變兩份）。
            have_e = {(local_day(e.spend_date).strftime("%Y-%m-%d")
                       if e.spend_date else "",
                       e.title, int(e.amount or 0))
                      for e in (await s.execute(select(HrBenefitEntry).where(
                          HrBenefitEntry.pool_id == pool.id))).scalars().all()
                      } if pool.id else set()

            for day, title, amount, short, paid in data[sheet]:
                when = datetime.strptime(day, "%Y-%m-%d")
                if amount > 0:                      # 撥款
                    key = (when.year, amount)
                    if key in have_f:
                        made["skip"] += 1
                        continue
                    if apply:
                        s.add(HrBenefitFunding(
                            id=uuid.uuid4().hex[:16], pool_id=pool.id,
                            year=when.year, amount=amount, fund_date=when,
                            notes="Notion 匯入"))
                    have_f.add(key)
                    made["funding"] += 1
                else:                               # 員工登記
                    amt = -amount
                    key = (day, title, amt)
                    if key in have_e:
                        made["skip"] += 1
                        continue
                    hit = who.get(short) if short else None
                    if apply:
                        s.add(HrBenefitEntry(
                            id=uuid.uuid4().hex[:16], pool_id=pool.id,
                            staff_id=hit[0] if hit else None,
                            # 對得到就用檔案上的全名，對不到就留 Notion 的簡稱
                            staff_name=(hit[1] if hit else (short or "（未指定）")),
                            title=title, amount=amt, spend_date=when,
                            status="已付款" if paid else "待審",
                            notes="Notion 匯入"))
                    have_e.add(key)
                    made["entry"] += 1

        if apply:
            await s.commit()

        # ── 驗算：從 DB 重讀，餘額要跟 Notion 對得上 ──
        print("\n── 結果 ──")
        print(f"  池 {made['pool']}｜撥款 {made['funding']}｜登記 {made['entry']}"
              f"｜跳過（已存在）{made['skip']}")
        if not apply:
            print("\n（dry-run，什麼都沒寫。要寫請加 --apply）")
            return
        from core.hr_logic import benefit_pool_balance
        for sheet, pool_name in POOL_OF.items():
            pool = (await s.execute(select(HrBenefitPool).where(
                HrBenefitPool.name == pool_name,
                HrBenefitPool.entity == "parent"))).scalar_one()
            fs = (await s.execute(select(HrBenefitFunding.amount).where(
                HrBenefitFunding.pool_id == pool.id))).scalars().all()
            es = (await s.execute(select(HrBenefitEntry.status,
                                         HrBenefitEntry.amount)
                                  .where(HrBenefitEntry.pool_id == pool.id))).all()
            b = benefit_pool_balance(fs, es)
            want = data["_checksum"][sheet]["balance"]
            # 待審的還沒吃預算 → 系統餘額 = Notion 餘額 + 待審合計
            expect = want + b["pending"]
            flag = "✅" if b["balance"] == expect else "🔴"
            print(f"  {flag} {pool_name}：撥款 {b['funded']:,}｜已用 {b['used']:,}"
                  f"｜審核中 {b['pending']:,}｜餘額 {b['balance']:,}"
                  f"（Notion {want:,}"
                  + (f"，核准待審那 {b['pending']:,} 之後就會相等）" if b["pending"]
                     else "）"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的寫入（預設 dry-run）")
    ap.add_argument("--prod", action="store_true", help="寫生產庫 mediaguard")
    a = ap.parse_args()
    sys.path.insert(0, str(REPO))
    if a.prod:
        # 生產庫：走 C:\OriginsunAgent 的 settings（庫名 mediaguard）
        sys.path.insert(0, r"C:\OriginsunAgent")
        os.chdir(r"C:\OriginsunAgent")
    print(f"目標：{'生產 mediaguard' if a.prod else 'dev mediaguard_dev'}"
          f"｜模式：{'APPLY（會寫入）' if a.apply else 'dry-run'}\n")
    asyncio.run(run(a.apply))


if __name__ == "__main__":
    main()
