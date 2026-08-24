# -*- coding: utf-8 -*-
"""owner 私帳 Google Sheet「固定資產折舊表」→ equipment（entity='mine'）。

    # dry-run（預設；預設 dev 庫）
    .venv/Scripts/python.exe scripts/import_my_equipment.py --csv assets.csv
    # 真的寫入 / 對生產庫
    .venv/Scripts/python.exe scripts/import_my_equipment.py --csv assets.csv --apply [--prod]

🔴 預設 dry-run。契約比照 scripts/import_my_ledger.py。

CSV 來源：私帳 Sheet「固定資產」分頁（gid=1293578410）→ export?format=csv。
版面：前 7 列是折舊 dashboard，表頭列以「建置日期」定位。

對映（§8 階段 4，2026-08-24）：
- 名稱/建置日期/建構金額/使用期限(月) → name/purchase_date/purchase_cost/
  depreciation_months（期限 0 或空 → 48 預設）。
- 狀態：使用中→在庫；報廢/報廢_轉售→除役（Sheet 無除役日 → retired_date 留空，
  引擎對「status='除役' 且無日期」的處理＝不計淨值，正合需求）。
- 殘值率/分期資訊收進 note。
- ⚠️ 已知差異：Sheet 用 5% 殘值、系統直線攤到 0 —— 匯入後系統的私帳器材
  淨值會**低於** Sheet 的 449,903（差額＝各項殘值底床）。dry-run 會算出
  兩種口徑並列，差異屬口徑不同非資料錯誤。
- 冪等：--apply 先清 entity='mine' 的 equipment 再重建。
"""
import csv
import io
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts._common import (assert_safe_rebuild, cli, col_getter, connect, db_target,  # noqa: E402
                             find_header, money, parse_date, print_dry_run_end,
                             print_target)


def load_rows(csv_path: str):
    rows = list(csv.reader(io.open(csv_path, encoding="utf-8")))
    h = find_header(rows, "建置日期")
    col = col_getter(rows[h])

    out = []
    for r in rows[h + 1:]:
        name = col(r, "名稱")
        if not name or not col(r, "建置日期"):
            continue
        status_raw = col(r, "狀態")
        months = money(col(r, "使用期限(月)")) or 48
        salvage = col(r, "殘值率")
        inst = money(col(r, "分期(月)"))
        note_bits = ["[私帳匯入]"]
        if salvage:
            note_bits.append(f"殘值率:{salvage}")
        if inst > 1:
            note_bits.append(f"分期:{inst}期")
        if status_raw.startswith("報廢_"):
            note_bits.append(status_raw)          # 報廢_2024年轉售給邱家琳 之類
        out.append({
            "name": name[:128],
            "date": parse_date(col(r, "建置日期")),
            "cost": money(col(r, "建構金額")),
            "months": months,
            "status": "除役" if status_raw.startswith("報廢") else "在庫",
            "note": " ".join(note_bits),
            "sheet_net": money(col(r, "淨值")),
        })
    return out


def system_net(rows) -> int:
    """系統口徑（直線攤到 0）的在庫淨值 —— 走報表引擎那一份，不自己算。

    🔴 這行印出來的數字唯一的用途，是讓 owner 確認「Sheet 449,903（5% 殘值）
    vs 系統 X（攤到 0）的差額＝殘值底床、口徑不同不是錯誤」。用**另一套**算法
    算出來的 X 沒辦法證明那句話 —— 原本這裡自己寫的版本把購入當月不算折舊
    （引擎是當月即折一整月），123 項整體差了大約一個月的總折舊。
    而且原本 as_of 寫死 (2026, 8)，時間一過就靜默失真。
    """
    from datetime import datetime, timezone

    from core.finance_logic import equipment_net_rows
    return equipment_net_rows(
        [{"name": e["name"], "purchase_cost": e["cost"], "purchase_date": e["date"],
          "depreciation_months": e["months"], "retired_date": None,
          "status": e["status"]} for e in rows],
        datetime.now(timezone.utc).strftime("%Y-%m"))["net_total"]


async def run(csv_path: str, apply: bool, prod: bool, force: bool = False):
    dbname, dsn = db_target(prod)
    rows = load_rows(csv_path)
    active = [r for r in rows if r["status"] == "在庫"]
    print_target(dbname, apply)
    print(f"解析：{len(rows)} 項（在庫 {len(active)}／除役 {len(rows) - len(active)}）")
    print(f"在庫建構金額合計: {sum(r['cost'] for r in active):,}（Sheet 錨點 1,434,948）")
    print(f"Sheet 淨值（5% 殘值口徑）: {sum(r['sheet_net'] for r in active):,}（錨點 449,903）")
    print(f"系統淨值（攤到 0 口徑）: {system_net(rows):,} —— 差額＝殘值底床，口徑不同非錯誤")
    if not apply:
        print_dry_run_end()
        return

    c = await connect(dsn)
    try:
        n0 = await c.fetchval("SELECT count(*) FROM equipment WHERE entity='mine'")
        await assert_safe_rebuild(c, [
            ("非匯入建立的 mine 器材", "SELECT count(*) FROM equipment"
             " WHERE entity='mine' AND (note IS NULL OR note NOT LIKE '[私帳匯入]%')"),
            ("有領用紀錄的 mine 器材", "SELECT count(*) FROM equipment_checkouts c"
             " JOIN equipment e ON e.id=c.equipment_id WHERE e.entity='mine'"),
        ], force)
        print(f"\n清場：mine equipment={n0} → 重建")
        await c.execute("DELETE FROM equipment WHERE entity='mine'")
        await c.executemany(
            """INSERT INTO equipment
                   (id, entity, name, purchase_date, purchase_cost,
                    depreciation_months, status, note, created_at, updated_at)
               VALUES ($1,'mine',$2,$3,$4,$5,$6,$7,now(),now())""",
            [(uuid.uuid4().hex, r["name"], r["date"], r["cost"],
              r["months"], r["status"], r["note"]) for r in rows])
        n = await c.fetchval("SELECT count(*) FROM equipment WHERE entity='mine'")
        tot = await c.fetchval(
            "SELECT SUM(purchase_cost) FROM equipment WHERE entity='mine' AND status='在庫'")
        print(f"寫入 {n} 項；在庫建構合計 {tot:,}")
        if n != len(rows):
            print("🔴 筆數不符")
            sys.exit(1)
        print("驗證通過 ✓")
    finally:
        await c.close()


if __name__ == "__main__":
    cli(run, "固定資產分頁的 CSV（gid=1293578410）")
