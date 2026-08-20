# -*- coding: utf-8 -*-
"""發票 → 專案 連結套用（吃 match_inv_proj 產的提案 JSON）。

用法（🔴 預設 dry-run）：
    python scripts/link_invoices_to_projects.py --proposal <json>            # 試算
    python scripts/link_invoices_to_projects.py --proposal <json> --apply    # 寫入生產

只套用 A/B/C/D 層（比對器的自動可信層），且：
- 逐張再驗一次 project_id 真的存在（提案與套用之間專案可能被刪）
- 已經有 project_id 的發票**不覆蓋**（人先掛好的優先）
- EXCLUDE 名單：比對器放行但人工複核降級的（原因寫在旁邊）

2026-08-20 人工複核紀錄：全部 A-D 逐筆看過，抓掉三類誤配後才定案 ——
「趨勢基金會長板坡」差點連到同客戶的鍾馗案（重疊全來自客戶名，實為
《長坂坡・漢津口》的錯別字）、「王道週年影片」差點被塞給該客戶唯一的堤頂之星案、
「金安獎影片拍攝」（抬頭豐譽營造＝台水金安獎）差點連到東仁社宅金安獎。
"""
import argparse
import asyncio
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

# 比對器放行、人工降級：工研院同時有節能「科普」「減碳」「標竿」多個近名案，
# 0.62 的字元包含不足以斷定這兩張屬於哪個。
EXCLUDE = {
    "VJ30962659": "節能標杆 vs 科普/減碳/標竿 多案近名，人工判",
    "VJ30962660": "同上",
}


async def run(proposal_path: str, apply: bool):
    prop = json.load(io.open(proposal_path, encoding="utf-8"))
    rows = [r for t in "ABCD" for r in prop.get(t, [])]
    from config import load_settings
    url = load_settings().get("database_url", "").replace("/mediaguard_dev", "/mediaguard")
    print(f"目標資料庫: {url.rsplit('/', 1)[-1]}   模式: "
          f"{'APPLY（會寫入！）' if apply else 'DRY-RUN（不寫入）'}")
    eng = create_async_engine(url, pool_pre_ping=True)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    from db.models import CrmInvoice, CrmProject
    async with Session() as s:
        pids = {p.id for p in (await s.execute(select(CrmProject))).scalars().all()}
        planned, skipped = [], []
        for r in rows:
            if r["no"] in EXCLUDE:
                skipped.append((r, "人工降級: " + EXCLUDE[r["no"]]))
                continue
            inv = await s.get(CrmInvoice, r["id"])
            if not inv:
                skipped.append((r, "發票不存在"))
                continue
            if r.get("pid") not in pids:
                skipped.append((r, "專案已不存在"))
                continue
            if inv.project_id:
                skipped.append((r, f"已有連結（不覆蓋）→ {inv.project_id[:8]}"))
                continue
            planned.append((inv, r))
        print(f"提案 {len(rows)} 筆 → 會連結 {len(planned)} 筆，跳過 {len(skipped)} 筆")
        for r, why in skipped:
            print(f"  跳過 {r['no']:<12} {r['title'][:16]:<18} — {why}")
        if not apply:
            print("\nDRY-RUN 結束 —— 沒有寫入任何東西。")
            await eng.dispose()
            return
        from routers.crm._shared import _now
        for inv, r in planned:
            inv.project_id = r["pid"]
            inv.updated_at = _now()
        await s.commit()
        n = (await s.execute(select(CrmInvoice.id)
             .where(CrmInvoice.project_id.isnot(None)))).scalars().all()
        print(f"\n✅ 已連結 {len(planned)} 筆。全庫有專案連結的發票: {len(n)} 張")
    await eng.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--proposal", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    asyncio.run(run(a.proposal, a.apply))
