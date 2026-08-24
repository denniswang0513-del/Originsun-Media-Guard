# -*- coding: utf-8 -*-
"""owner 私帳 Google Sheet「結案總表」→ crm_projects（entity='mine'）＋客戶對映
＋委外/稅款應付 → crm_payment_requests ＋收支明細回掛 project_id。

    # dry-run（預設；預設 dev 庫）— 只解析、對映與報告，不寫
    .venv/Scripts/python.exe scripts/import_my_projects.py --csv closed.csv
    # 真的寫入 / 對生產庫
    .venv/Scripts/python.exe scripts/import_my_projects.py --csv closed.csv --apply [--prod]

🔴 預設 dry-run。契約比照 scripts/import_my_ledger.py。

CSV 來源：私帳 Sheet「結案總表」分頁（gid=2098748516）→ export?format=csv。
（「進行中」分頁 gid=1372199169 是它的未收款 filter —— 2026-08-24 實測代碼
42/43 重疊、僅檢視差異 —— 所以**只匯這一張**，進行中表當應收錨點。）

§8 拍板（2026-08-24）：專案與客戶全面共用、只有錢分帳 ——
- 客戶進共用 clients 表。對映**只做確定的**（鐵則：絕不 fuzzy 自動 merge）：
  精確吻合／normalize 相等／短名是長名的 prefix（如 台新 ⊂ 台新銀行 ——
  法人前綴 財團法人/社團法人 與 公司尾巴在 normalize 時拿掉）。其餘一律
  **新建**（寧可留重複讓人合併，不冒錯併危險）。決策全記
  scripts/data/my_ledger_client_map.json 供覆核。
- 🔴 時間戳用**結案日**不是匯入當下：專案列表按 updated_at desc 排序，全帶
  now() 會讓 402 案整片壓在最上面、把母公司的案子埋掉（2026-08-24 實際發生）。
  執行中的案沒有結案日 → 落 now()，它們本來就該排在前面。
- 專案 entity='mine'：status 一律 '結案'（表本身就是結案總表）、
  contract_amount=營收(含稅)、amount_receivable/received 照表、
  税別=發票→tax_rate 5 其餘 0。案碼/案源/税別/稅務欄/工種拆分/備註收進
  notes（結構化文字；案碼行 `[私帳匯入] 案碼:XXXX` 是收支回掛的鍵）。
- 委外應付>0 或 應付稅款未付 → crm_payment_requests（entity='mine'，
  payment_status='應付款'）＝「我現在要匯多少給人家」的資料源。
  收款人埋在 Sheet 備註自由文字 → 進 summary，payee_name 留白由 owner 補。
- 收支回掛：階段 1 匯入的明細 project_label='{案碼}｜{標籤}' → 依案碼
  UPDATE project_id。
- 冪等：--apply 先清 entity='mine' 的 crm_projects 與 crm_payment_requests、
  重置 mine 收支的 project_id。客戶**不刪**（重跑靠 short_name 精確吻合重用；
  首跑建的客戶帶 source_channel='私帳匯入' 供辨識）。
- 🔴 既有母公司客戶的 status **不動**（匯入大量歷史專案會讓分級整批跳動）；
  只有新建客戶按 0=潛在/1=新/2+=舊 設定。
"""
import argparse
import asyncio
import csv
import io
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts._common import resolve_db_url  # noqa: E402

import asyncpg  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"

# Sheet 內部已知同客戶（人工確認過的兩組；normalize 相等那組其實會自動中）
INTERNAL_MERGE = {
    "中強光電文化基金會": "中強光電文化藝術基金會",
    "台北當代藝術展": "台北當代藝術館",
}

_STRIP = re.compile(
    r"(財團法人|社團法人|股份有限公司|有限公司|股份|有限|公司|基金會|協會|"
    r"工作室|事務所|文化|藝術|創意|傳播|設計|影像|國際)")


def norm(s: str) -> str:
    return _STRIP.sub("", re.sub(r"\s+", "", s or "")).lower()


def money(s: str) -> int:
    s = (s or "").replace("NT$", "").replace(",", "").strip()
    s = s.replace("（", "(").replace("）", ")")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").strip()
    if not s:
        return 0
    try:
        v = float(s)
    except ValueError:
        return 0
    return -round(v) if neg else round(v)


def close_month_date(s: str):
    m = re.match(r"(\d{4})/(\d{1,2})", (s or "").strip())
    if not m:
        return None
    return datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=timezone.utc)


DEPT_COLS = ["前期製作", "動態攝影", "剪輯", "調光", "動態效果",
             "平面攝影", "諮詢", "教學", "錄混音", "其他"]


def load_projects(csv_path: str):
    rows = list(csv.reader(io.open(csv_path, encoding="utf-8")))
    hdr = [h.strip() for h in rows[0]]
    ix = {h: i for i, h in enumerate(hdr)}

    def col(r, name, default=""):
        i = ix.get(name)
        return (r[i] if i is not None and len(r) > i else default).strip()

    out = []
    for r in rows[1:]:
        if len(r) < 14 or not r[2].strip() or not r[3].strip():
            continue
        client = INTERNAL_MERGE.get(r[2].strip(), r[2].strip())
        code = (r[-1] or "").strip()
        recv_status = col(r, "收款狀態")
        receivable = money(col(r, "應收帳款"))
        received = money(col(r, "已收帳款"))
        tax_kind = col(r, "税別") or col(r, "稅別")
        depts = {d: money(col(r, d)) for d in DEPT_COLS}
        out.append({
            "code": code, "client": client, "name": r[3].strip(),
            "type": col(r, "類型"), "source": col(r, "案源"),
            "note": col(r, "備註"), "progress": col(r, "進度"),
            "close_month": r[0].strip(),
            "tax_kind": tax_kind,
            "contract": money(col(r, "營收(含稅)")),
            "received": received, "receivable": receivable,
            "recv_status": recv_status,
            "out_due": money(col(r, "委外應付")),
            "out_paid": money(col(r, "委外已付")),
            "tax_due": money(col(r, "應付稅款")),
            "tax_status": col(r, "稅款狀態"),
            "tax": money(col(r, "稅金")), "buy_inv": money(col(r, "買發票")),
            "inv_fee": money(col(r, "發票代辦費")),
            "personal_tax": money(col(r, "個人稅款")),
            "depts": {k: v for k, v in depts.items() if v},
        })
    return out


def build_notes(p: dict) -> str:
    lines = [f"[私帳匯入] 案碼:{p['code'] or '無'}"]
    meta = [f"案源:{p['source'] or '?'}", f"税別:{p['tax_kind'] or '?'}"]
    if p["progress"]:
        meta.append(f"進度:{p['progress']}")
    lines.append("｜".join(meta))
    tax_bits = [(n, v) for n, v in [("稅金", p["tax"]), ("買發票", p["buy_inv"]),
                                    ("發票代辦費", p["inv_fee"]),
                                    ("個人稅款", p["personal_tax"])] if v]
    if tax_bits:
        lines.append("｜".join(f"{n}:{v:,}" for n, v in tax_bits))
    if p["depts"]:
        lines.append("工種:" + "｜".join(f"{k} {v:,}" for k, v in p["depts"].items()))
    if p["note"]:
        lines.append(f"原備註:{p['note']}")
    return "\n".join(lines)


def match_client(name: str, prod_index: dict, sheet_norms: set):
    """(client_id, how) 或 (None, 'new')。只做確定的三種：精確／norm 相等／prefix。

    prefix 規則的兩道保險（2026-08-24 dry-run 實抓：文心藝所 被併進
    文心藝術基金會 —— 但 Sheet 裡兩者是**不同客戶**）：
    1. 短邊 normalize 後至少 3 字（"文心"/"台新" 這種 2 字核心太容易誤傷）。
    2. 對映目標的 norm 若同時是 Sheet 裡**另一個**客戶的 norm → 歧義，不併。
    寧可新建重複讓人合併，不冒錯併 —— 錯併會把兩個客戶的專案史攪在一起。
    """
    if name in prod_index["exact"]:
        return prod_index["exact"][name], "exact"
    n = norm(name)
    if n and n in prod_index["norm"]:
        return prod_index["norm"][n], "norm"
    for pn, cid in prod_index["norm"].items():
        if pn == n or not pn or not n:
            continue
        if pn.startswith(n) or n.startswith(pn):
            if min(len(pn), len(n)) < 3:
                continue
            if pn in sheet_norms and pn != n:
                continue          # 目標是 Sheet 另一個客戶 → 歧義
            return cid, f"prefix({pn})"
    return None, "new"


async def run(csv_path: str, apply: bool, prod: bool):
    url = resolve_db_url(prod)
    dbname = url.rsplit("/", 1)[-1]
    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    projects = load_projects(csv_path)
    print(f"目標資料庫: {dbname}   模式: {'寫入 (--apply)' if apply else 'DRY-RUN（不寫入）'}")
    n_run = sum(1 for p in projects if p["progress"] == "執行中")
    print(f"解析：{len(projects)} 案（執行中 {n_run}、代碼缺漏 "
          f"{sum(1 for p in projects if not p['code'])}）")
    ar_open = sum(p["receivable"] for p in projects if p["recv_status"] == "未收款")
    ar_running = sum(p["receivable"] for p in projects if not p["recv_status"])
    print(f"營收(含稅)合計: {sum(p['contract'] for p in projects):,}"
          f"｜結案作業應收: {ar_open:,}（進行中表錨點 2,079,209）"
          f"｜執行中應收: {ar_running:,}")
    if ar_open != 2_079_209:
        print("🔴 結案作業應收與進行中表錨點不符 — 檢查資料")
        sys.exit(1)

    c = await asyncio.wait_for(asyncpg.connect(dsn), 15)
    try:
        # 客戶索引
        rows = await c.fetch("SELECT id, short_name, full_name FROM clients")
        prod_index = {"exact": {}, "norm": {}}
        for r in rows:
            for key in (r["short_name"], r["full_name"]):
                if key and key.strip():
                    prod_index["exact"].setdefault(key.strip(), r["id"])
            n = norm(r["short_name"])
            if n:
                prod_index["norm"].setdefault(n, r["id"])

        sheet_norms = {norm(p["client"]) for p in projects}
        decisions, new_clients = {}, {}
        for p in projects:
            if p["client"] in decisions:
                continue
            cid, how = match_client(p["client"], prod_index, sheet_norms)
            decisions[p["client"]] = {"client_id": cid, "how": how}
            if cid is None:
                new_clients[p["client"]] = None
        n_match = sum(1 for d in decisions.values() if d["client_id"])
        print(f"客戶對映：{len(decisions)} 名 → 吻合 {n_match}／新建 {len(new_clients)}")
        for name, d in sorted(decisions.items()):
            if d["client_id"] and d["how"] != "exact":
                print(f"  併入既有: {name} [{d['how']}]")

        DATA_DIR.mkdir(exist_ok=True)
        map_path = DATA_DIR / "my_ledger_client_map.json"
        map_path.write_text(json.dumps(decisions, ensure_ascii=False, indent=1,
                                       default=str), encoding="utf-8")
        print(f"對映決策已存 {map_path}（覆核用）")

        # 應付款預覽
        prs = []
        for p in projects:
            if p["out_due"] > 0:
                prs.append((p, "專案外包", p["out_due"],
                            f"委外應付：{p['name']}" + (f"（{p['note']}）" if p["note"] else "")))
            if p["tax_due"] > 0 and p["tax_status"] != "已付款":
                prs.append((p, "其他", p["tax_due"], f"應付稅款（發票代辦）：{p['name']}"))
        print(f"應付請款單：{len(prs)} 張，合計 {sum(x[2] for x in prs):,}"
              f"（錨點：委外 92,600＋稅款 145,896＋結案殘留）")

        if not apply:
            print("\nDRY-RUN 結束 —— 沒有寫入任何東西。確認上面數字無誤後加 --apply。")
            return

        # ── 冪等清場（只碰 mine）──
        n1 = await c.fetchval("SELECT count(*) FROM crm_projects WHERE entity='mine'")
        n2 = await c.fetchval("SELECT count(*) FROM crm_payment_requests WHERE entity='mine'")
        print(f"\n清場：mine projects={n1} payment_requests={n2} → 重建")
        await c.execute("UPDATE crm_cash_entries SET project_id=NULL WHERE entity='mine'")
        await c.execute("DELETE FROM crm_payment_requests WHERE entity='mine'")
        await c.execute("DELETE FROM crm_projects WHERE entity='mine'")

        # ── 新建客戶（不刪既有；重跑靠 exact 重用）──
        mine_count_by_client = {}
        for p in projects:
            mine_count_by_client[p["client"]] = mine_count_by_client.get(p["client"], 0) + 1
        for name in new_clients:
            n_proj = mine_count_by_client.get(name, 0)
            status = "舊客戶" if n_proj >= 2 else ("新客戶" if n_proj == 1 else "潛在客戶")
            cid = uuid.uuid4().hex
            await c.execute(
                """INSERT INTO clients (id, short_name, full_name, status,
                                        source_channel, created_at, updated_at)
                   VALUES ($1,$2,'',$3,'私帳匯入',now(),now())""",
                cid, name, status)
            decisions[name]["client_id"] = cid
            decisions[name]["how"] = "created"
        map_path.write_text(json.dumps(decisions, ensure_ascii=False, indent=1,
                                       default=str), encoding="utf-8")

        # ── 專案 ──
        code_to_pid = {}
        for p in projects:
            pid = uuid.uuid4().hex
            if p["code"]:
                code_to_pid[p["code"]] = pid
            pay_status = ("全額到帳" if p["recv_status"] == "已收款"
                          else "部分到帳" if p["received"] > 0 and p["receivable"] > 0
                          else "未到帳")
            # 進度=執行中 ＝ 還在跑的案子（2026-08-24 實查 23 案，含稅 1.79M 應收）
            # → status='製作'、無結案日；其餘（結案/結案作業）→ '結案'
            status = "製作" if p["progress"] == "執行中" else "結案"
            await c.execute(
                """INSERT INTO crm_projects
                       (id, entity, name, client_id, status, project_type,
                        completion_date, contract_amount, tax_rate,
                        amount_receivable, amount_received, payment_status,
                        notes, profit_target_pct, misc_budget_pct,
                        public_credits_mode, public_old_slugs,
                        created_at, updated_at)
                   VALUES ($1,'mine',$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,
                           20,5,'text','[]'::jsonb,
                           COALESCE($6, now()), COALESCE($6, now()))""",
                pid, p["name"], decisions[p["client"]]["client_id"], status,
                p["type"], close_month_date(p["close_month"]),
                p["contract"] or None, 5 if p["tax_kind"] == "發票" else 0,
                p["receivable"] or None, p["received"] or None,
                pay_status, build_notes(p))
        print(f"專案：{len(projects)} 案寫入")

        # ── 應付請款單 ──
        for p, cat, amt, summary in prs:
            await c.execute(
                """INSERT INTO crm_payment_requests
                       (id, entity, amount, summary, category, project_id,
                        project_label, payment_status, needs_invoice,
                        is_advance, advance_returned, created_at)
                   VALUES ($1,'mine',$2,$3,$4,$5,$6,'應付款',0,0,0,now())""",
                uuid.uuid4().hex, amt, summary[:250], cat,
                code_to_pid.get(p["code"]), f"{p['client']}_{p['name']}"[:120])
        print(f"應付請款單：{len(prs)} 張寫入")

        # ── 收支回掛 ──
        linked = 0
        for code, pid in code_to_pid.items():
            r = await c.execute(
                """UPDATE crm_cash_entries SET project_id=$1
                   WHERE entity='mine' AND project_label LIKE $2""",
                pid, f"{code}｜%")
            linked += int(r.split()[-1])
        print(f"收支回掛：{linked} 筆明細掛上專案")

        # ── 驗證 ──
        print("\n=== 驗證 ===")
        tot = await c.fetchrow(
            """SELECT count(*) n, SUM(COALESCE(contract_amount,0)) ca,
                      SUM(COALESCE(amount_receivable,0)) ar
               FROM crm_projects WHERE entity='mine'""")
        print(f"mine 專案 {tot['n']}｜合約合計 {tot['ca']:,}｜應收合計 {tot['ar']:,}")
        pr = await c.fetchrow(
            """SELECT count(*) n, SUM(amount) s FROM crm_payment_requests
               WHERE entity='mine' AND payment_status='應付款'""")
        print(f"應付款請款單 {pr['n']} 張，合計 {pr['s']:,}")
        if tot["n"] != len(projects):
            print("🔴 專案筆數不符")
            sys.exit(1)
        # 錨點：進行中表（未收款子集）應收 2,079,209
        ar_all = await c.fetchval(
            """SELECT SUM(COALESCE(amount_receivable,0)) FROM crm_projects
               WHERE entity='mine' AND payment_status != '全額到帳'""")
        print(f"未全收專案應收合計 {ar_all:,}（＝結案作業 2,079,209 ＋ 執行中 1,791,543）")
        print("驗證通過 ✓")
    finally:
        await c.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="結案總表的 CSV（gid=2098748516）")
    ap.add_argument("--prod", action="store_true", help="對生產庫 mediaguard（預設 dev）")
    ap.add_argument("--apply", action="store_true", help="真的寫入（預設 dry-run）")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run(a.csv, a.apply, a.prod))
