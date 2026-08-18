# -*- coding: utf-8 -*-
"""清掉建案時種下的 $0 佔位雜支列（2026-08-18 起不再種，見 routers/crm/projects.py）。

它們長得跟真資料一模一樣（有日期、有刪除鈕），生產庫積了 ~193 列，
是專案頁雜支區「看起來一堆列其實全空」的來源。

🔴 金絲雀鐵則：只刪**嚴格特徵**全中的列 —— 金額全 0、無細項、無收據、
無發票、無備註、無收款人、沒綁員工、沒進請款單、沒關聯預支、沒有消費日。
任何一欄有東西就不是佔位列，不碰。刪之前先把完整列備份成 JSON。

用法：
    python scripts/cleanup_placeholder_expenses.py            # dev 庫，只列不刪
    python scripts/cleanup_placeholder_expenses.py --apply    # dev 庫，備份後刪
    python scripts/cleanup_placeholder_expenses.py --prod --apply
"""
import asyncio
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402


def _resolve_db_url(prod: bool) -> str:
    """dev/prod 庫切換沿用 import_petty_cash.resolve_db_url（單一正本）。"""
    spec = importlib.util.spec_from_file_location(
        "imp_petty", REPO / "scripts" / "import_petty_cash.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.resolve_db_url(prod)


# 嚴格特徵：全部條件同時成立才算佔位列
WHERE = """
    COALESCE(actual, 0) = 0 AND COALESCE(estimated, 0) = 0
    AND COALESCE(sub_item, '') = '' AND COALESCE(receipt_url, '') = ''
    AND COALESCE(invoice_no, '') = '' AND COALESCE(notes, '') = ''
    AND COALESCE(payee, '') = ''
    AND staff_id IS NULL AND claim_id IS NULL AND advance_id IS NULL
    AND expense_date IS NULL
"""


async def main() -> None:
    prod = "--prod" in sys.argv
    apply = "--apply" in sys.argv
    url = _resolve_db_url(prod)
    dbname = url.rsplit("/", 1)[-1]
    eng = create_async_engine(url)
    try:
        async with eng.connect() as c:
            rows = (await c.execute(text(
                "SELECT id, project_id, cost_group_id, category, "
                "       to_char(created_at, 'YYYY-MM-DD') AS created_at "
                f"FROM crm_project_expenses WHERE {WHERE} ORDER BY project_id"))).mappings().all()
        print(f"db={dbname} 佔位列 {len(rows)} 筆")
        for r in rows[:20]:
            print(f"  {r['id']}  {r['created_at']}  {r['category']}  proj={r['project_id']}")
        if len(rows) > 20:
            print(f"  …（其餘 {len(rows) - 20} 筆見備份檔）")
        if not rows:
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = REPO / "scripts" / f"_placeholder_backup_{dbname}_{stamp}.json"
        backup.write_text(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=1),
                          encoding="utf-8")
        print(f"備份：{backup}")
        if not apply:
            print("（只列不刪；要刪加 --apply）")
            return
        ids = [r["id"] for r in rows]
        async with eng.begin() as c:
            # 🔴 雙保險：只刪備份過的 id，且刪除當下仍要**再次**全中佔位特徵 ——
            # 兩次查詢之間有人真的填了某列，那列就不再符合條件而倖存
            # （rowcount 少於備份數＝有列被救下，會印出來）。
            res = await c.execute(
                text(f"DELETE FROM crm_project_expenses WHERE id = ANY(:ids) AND {WHERE}"),
                {"ids": ids})
        print(f"已刪 {res.rowcount} 筆（備份 {len(ids)} 筆"
              + ("" if res.rowcount == len(ids) else "，差額＝期間被填入資料而倖存的列")
              + "）")
    finally:
        await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())
