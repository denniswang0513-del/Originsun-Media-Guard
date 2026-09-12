"""routers/crm/invoice_files.py — 已開立電子發票的檔案：上傳／下載／分享連結，
外加「發票根目錄」與申請人、代開費率這幾個設定端點。

從 routers/crm/finance.py 原樣搬出來（純搬移，行為不變 —— URL 一個都沒改）。
搬出來的理由：這 500 行是 PDF/PNG 的儲存與分享 token，跟同一個檔案裡的錢的規則
（收款狀態、分配表、月結守衛）是兩件事，夾在中間只是讓那個檔案更難讀。
量過的相依：這個區塊**不需要** finance.py 的任何名字，而 finance.py 只用得到
這裡的 _resync_invoice_file 一支。

⚠ router / token_router 是 _shared 的單例，兩個檔案掛在同一組上 —— 端點註冊
不需要額外接線（routers/crm/__init__.py 匯入本模組即可）。
"""
from __future__ import annotations

import asyncio
import glob
import ntpath
import os
import re
import shutil

from fastapi import Depends, File, HTTPException, Query, Request, UploadFile
from core.no_store import no_store_file

from core.auth import check_admin
from core.finance_logic import PASSTHROUGH_FEE_RATES
from core.ledger import require_entity
from core.public_access import surface_gate
from core import invoice_share, share_link
from core.drive_map import to_canonical_path, to_local_path

# 🔴 這支檔裡「路徑 → 檔名」一律用 `ntpath.basename`，不用 `os.path.basename`。
#    `file_url` 存的是 **Windows 視角**的路徑（`\\192.168.1.132\Archive\…`），而
#    這份程式也跑在 NAS 的 Linux 容器裡 —— 那邊 `os.path` 是 posixpath，反斜線不是
#    分隔字元，`basename` 會把**整條內部路徑原封不動**回傳。那個字串會被寫進
#    `share_snapshot["file"]["name"]` 並印在寄給客戶的分享頁上。
#    `ntpath.basename` 兩種分隔字元都認，所以兩台都對。

from ._shared import (router, public_router, _check_finance_auth, money_dep,
                      _require_db, _get_factory, _fmt_day, _now)

try:
    from ._shared import select, CrmInvoice
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同原檔 try/except
    pass


# ── 已開立電子發票檔 ────────────────────────────────────────
#
# 骨架與零用金收據（routers/crm/costs.py）同一套：後台可設根目錄 → 依規則產生
# 資料夾與檔名 → 串流寫入 + 大小上限 + 副檔名黑名單 → 走白名單守衛的下載端點。
# 刻意**不**共用 receipts_root：收據是我們付出去的憑證、發票是我們開出去的，
# 交給會計師的是兩包不同東西，常常指到不同位置。
_MAX_INVOICE_BYTES = 30 * 1024 * 1024   # 電子發票是 PDF 或存證聯圖，比照收據


def _invoices_root() -> str:
    """電子發票檔根目錄的單一正本：settings.invoices_root（後台可設，要指到
    NAS 或會計師的共用資料夾就填那個路徑），留空＝uploads/invoices。"""
    from config import load_settings
    root = (load_settings().get("invoices_root") or "").strip()
    return root or os.path.join(os.getcwd(), "uploads", "invoices")


def _invoices_write_root() -> str:
    """要拿去 `makedirs`／寫檔的根目錄 ＝ **執行這支的這台**看得到的那個視角。

    🔴 設定裡存的是 master 視角的路徑（UNC 或磁碟代號）。NAS 的 office-api 容器上
       不翻譯就會 `makedirs` 出一個名字裡帶反斜線的資料夾
       （`/app/\\192.168.1.132\Archive\…`）—— 寫檔會成功、回 200、DB 也記下
       一個沒有任何一台讀得到的路徑，**全程沒有一行 error**。
       讀那側（`_local_invoice_path`）早就翻了，只有寫這側漏掉。
    """
    return to_local_path(_invoices_root())


def _stored_path(local_path: str) -> str:
    """寫完檔之後要存進 DB 的那個字串 ＝ canonical UNC（見 `drive_map.to_canonical_path`）。

    收檔那台若直接存自己的本機路徑，別台就讀不到 —— NAS 存 `/share/…`，master
    是 Windows，翻不回去。存 canonical 的話哪台讀都能 `to_local_path` 翻成自己的視角。
    """
    return to_canonical_path(local_path)


def _safe_part(s: str, limit: int = 0) -> str:
    """檔名片段：拿掉 Windows 不收的字元，可選截斷。"""
    out = re.sub(r'[\\/:*?"<>|]', "", (s or "").strip())
    return out[:limit] if limit else out


def _invoice_file_name(inv, ext: str) -> str:
    """{日期}_{發票號碼}_{抬頭前20字}_{含稅金額}[_作廢].{ext}

    - 日期開頭 → 資料夾內自然照時間排序，整包交給會計師順序就是對的
    - 發票號碼 → 法定唯一識別，對帳/查調/作廢都用它；沒號碼（未開立）用 id 前 8 碼
    - 抬頭截斷 20 字 → 台灣公司名很長，不截會撞 Windows 260 字元路徑上限
    - 含稅金額 → 交叉核對用，檔名數字跟系統對不上就知道有問題
    - 作廢放**尾端**不放前綴 —— 放前面會破壞日期排序
    """
    day = _fmt_day(inv.invoice_date)          # 🔴 台北歸一，直接 strftime 會差一天
    parts = [day.replace("-", "") or "nodate",
             _safe_part(inv.invoice_number) or inv.id[:8],
             _safe_part(inv.company_name, 20) or "無抬頭",
             str(inv.amount_total or 0)]
    if (inv.issue_status or "") == "作廢" or (inv.payment_status or "") == "作廢":
        parts.append("作廢")
    return "_".join(p for p in parts if p) + ext


# 證明聯的解析規則（號碼／統編／金額的正則與比對）全在 core/invoice_pdf.py ——
# 純函式、有單元測試。這裡刻意**不留第二份**：同一條規則兩個地方寫，
# 遲早會有兩個答案。


def _read_invoice_pdf(path: str) -> tuple:
    """PDF → (全文, 解析出來的欄位)。抽不到就 ("", {})。

    **同步**（呼叫端要丟 asyncio.to_thread）—— pypdf 是 CPU-bound 純同步解析。
    圖片檔（JPG/PNG）走到這裡會被 extract_text 判成不支援 → 空的，不做 OCR。
    解析規則在 core/invoice_pdf.py（純函式、有單元測試），這裡只負責讀檔。
    """
    if os.path.splitext(path)[1].lower() != ".pdf":
        return "", {}
    try:
        from core.doc_text import extract_text
        text, err = extract_text(path)
    except Exception:
        return "", {}
    if err or not text:
        return "", {}
    from core.invoice_pdf import parse_invoice_text
    return text, parse_invoice_text(text)


def _detect_invoice_number(path: str) -> str:
    """從電子發票證明聯（PDF）抽發票號碼。抽不到回 ""。

    **同步**（呼叫端要丟 asyncio.to_thread）—— pypdf 是 CPU-bound 純同步解析。

    兩段式：先找「發票號碼：」標籤後面的號碼；找不到標籤才退而求其次，看全文
    是否**恰好只有一組**符合格式的字串。「恰好一組」是刻意的 —— 證明聯上除了
    發票號碼還可能有隨機碼、載具號碼，多於一組時猜錯的代價（把法定號碼寫錯）
    遠大於讓人自己填。

    圖片檔（JPG/PNG）走到這裡會被 extract_text 判成不支援 → 回 ""，不做 OCR。
    """
    return (_read_invoice_pdf(path)[1] or {}).get("invoice_number") or ""


def _resync_invoice_file(inv) -> str:
    """把磁碟上的檔名/資料夾重新對齊這張發票現在的欄位；回傳新路徑（沒動就回原值）。

    為什麼需要：檔名是**上傳當下**由發票欄位組出來的。上傳時還沒填發票號碼的話
    檔名會落到 id 前 8 碼（fallback），之後號碼補上了，檔名還停在
    `20260806_42ec03d2_…`（owner 2026-08-19 實際遇到）。日期改了也一樣 ——
    資料夾是 {年}/{年-月}，改日期就該換資料夾。

    只搬不刪：目標已存在同名檔就不動（不覆蓋別人的憑證），搬移失敗也只是維持原狀，
    絕不讓「改個發票號碼」因為檔案系統的問題而整個失敗。
    """
    old = inv.file_url or ""
    local_old = to_local_path(old) if old else ""
    if not local_old or not os.path.isfile(local_old):
        return old
    ext = os.path.splitext(local_old)[1]
    day = _fmt_day(inv.invoice_date)
    base = os.path.join(_invoices_write_root(), day[:4] or "nodate", day[:7] or "nodate")
    target = os.path.join(base, _invoice_file_name(inv, ext))
    if os.path.abspath(target) == os.path.abspath(local_old) or os.path.exists(target):
        return old
    try:
        os.makedirs(base, exist_ok=True)
        # 🔴 一定要 shutil.move 不能用 os.replace/os.rename：那兩支**不能跨磁碟區**，
        # 本機 C:\ → NAS \\192.168.1.132\... 會直接丟 WinError 17（2026-08-19 把
        # 三個電子發票搬上 NAS 時實際踩到；幸好失敗時是維持原狀而不是弄丟檔）。
        # shutil.move 在跨裝置時會退成「複製再刪來源」。
        shutil.move(local_old, target)
        return _stored_path(target)
    except (OSError, shutil.Error):
        return old


@router.post("/invoices/{invoice_id}/file")
async def upload_invoice_file(invoice_id: str, request: Request, file: UploadFile = File(...)):
    """上傳這張發票開好的電子發票檔。同一張再傳一次＝取代（舊檔留在磁碟不刪，
    避免誤傳覆蓋掉唯一的正本；要清掉走 DELETE）。"""
    _check_finance_auth(request)
    _require_db()
    from core.project_folders import BLOCKED_UPLOAD_EXTS, stream_to_disk

    ext = os.path.splitext(file.filename or "")[1] or ".pdf"
    if ext.lower() in BLOCKED_UPLOAD_EXTS:
        raise HTTPException(status_code=400, detail=f"不接受的檔案格式：{ext}")

    factory = await _get_factory()
    async with factory() as session:
        inv = await session.get(CrmInvoice, invoice_id)
        if not inv:
            raise HTTPException(status_code=404, detail="找不到此發票")
        require_entity(request, inv.entity or "parent", level="full")

        day = _fmt_day(inv.invoice_date)
        # {root}/{年}/{年-月}/ —— 按月不按「期」（營業稅兩個月一期，要交一期抓兩夾），
        # 也刻意不按專案（發票是照稅務期間歸檔的，收據才按專案）
        base = os.path.join(_invoices_write_root(), day[:4] or "nodate", day[:7] or "nodate")
        try:
            os.makedirs(base, exist_ok=True)
        except OSError as e:
            raise HTTPException(status_code=422, detail=f"資料夾無法使用：{e}")

        filepath = os.path.join(base, _invoice_file_name(inv, ext))
        written = await asyncio.to_thread(stream_to_disk, file.file, filepath,
                                          _MAX_INVOICE_BYTES)
        if written < 0:
            raise HTTPException(status_code=413,
                                detail=f"檔案超過 {_MAX_INVOICE_BYTES // 1024 // 1024}MB")
        inv.file_url = _stored_path(filepath)
        # 有了電子發票證明聯就代表這張已經開出去了（owner 2026-08-19）。
        # 作廢的不動 —— 作廢也會留存證明聯，那不是「未開立」。
        if (inv.issue_status or "") != "作廢":
            inv.issue_status = "已開立"
        inv.updated_at = _now()
        await session.commit()
        current_number = inv.invoice_number or ""
        snapshot = {"invoice_number": current_number, "tax_id": inv.tax_id,
                    "amount_total": inv.amount_total,
                    "company_name": inv.company_name}
    # 一次讀檔，號碼與比對共用（讀兩次等於把 pypdf 跑兩遍）
    text, parsed = await asyncio.to_thread(_read_invoice_pdf, filepath)
    # 偵測到的號碼只**回報**、不自動寫入 —— 發票號碼是法定識別，套不套用由人決定
    detected = (parsed or {}).get("invoice_number") or ""
    # 「傳錯張」的警示（Soca 2026-08-21）：比對 PDF 上的統編／金額／號碼／抬頭。
    # 🔴 是警示不是閘門 —— 抽不到就安靜。擋下來會變成「明明是對的卻傳不上去」，
    #    那比偶爾漏警示更糟（掃描件、字型把字拆開、版面不同都會抽不到）。
    from core.invoice_pdf import compare_invoice_pdf
    warnings = compare_invoice_pdf(parsed, snapshot, text)
    return {"status": "ok", "file_url": _stored_path(filepath),
            "file_name": ntpath.basename(filepath),
            "detected_invoice_number": detected,
            # 與現有值相同就不用麻煩使用者
            "detected_differs": bool(detected and detected != current_number),
            "warnings": warnings,
            "checked": bool(parsed)}


@router.delete("/invoices/{invoice_id}/file")
async def clear_invoice_file(invoice_id: str, request: Request):
    """解除關聯。**不刪磁碟上的檔** —— 那可能是唯一一份正本，且稅務憑證誤刪
    救不回來；要清檔案由人到資料夾裡處理。"""
    _check_finance_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        inv = await session.get(CrmInvoice, invoice_id)
        if not inv:
            raise HTTPException(status_code=404, detail="找不到此發票")
        require_entity(request, inv.entity or "parent", level="full")
        inv.file_url = None
        inv.updated_at = _now()
        await session.commit()
    return {"status": "ok"}


@router.get("/invoice-file")
async def serve_invoice_file(path: str = Query(""), request: Request = None):
    """提供電子發票檔下載/檢視。路徑白名單：只放行 invoices_root 底下的檔
    （比照 costs.serve_receipt —— 沒有這道，這支就是任意檔案讀取）。"""
    require_entity(request, "parent", level="full")   # 第三批一把尺：發票影像＝金額，跟讀取同一把
    # 白名單與翻譯只有 `_local_invoice_path` 一份 —— 這裡原本自己寫了一份純
    # startswith 的比對，兩個後果：`…/00_電子發票_舊` 會通過 `…/00_電子發票` 的檢查，
    # 而且沒翻譯，所以在 NAS 的 office-api 上每一張都 404（UNC 在那台不是檔案）。
    abs_path = _local_invoice_path(path)
    if not abs_path:
        raise HTTPException(status_code=404, detail="檔案不存在")
    return no_store_file(abs_path, filename=ntpath.basename(abs_path))


# 短碼長度：secrets.token_urlsafe(9) → 12 字元（72 bits）。這串是要寄給客戶、
# 有時會被人工轉貼甚至念出來的，所以要短；72 bits 對「猜不到」而言仍綽綽有餘，
# 而且每次嘗試都要打一次伺服器（share_token 上有 unique index，查詢是索引命中）。
_SHARE_CODE_BYTES = 9


def _new_share_code() -> str:
    import secrets
    return secrets.token_urlsafe(_SHARE_CODE_BYTES)


@router.post("/invoices/{invoice_id}/share")
async def create_invoice_share_link(invoice_id: str, request: Request):
    """產生（或取回）給客戶下載這張電子發票的短連結。

    冪等：已經有一張就原樣回傳 —— 同一張發票寄兩次信不該讓先寄出去的連結失效。
    要作廢走 DELETE（重置語意）。

    🔴 憑證＝「網址裡那串字與 DB 存的完全相同」，不驗簽章。所以 jwt_secret 輪替
    不會連坐殺掉已經寄給客戶的連結，而那些連結本來就偽造不了。
    v1 用的是 220+ 字元的 JWT，整條網址 287 字元 —— owner 要求縮短，改成 12 字元
    短碼掛在根路徑 `/e/{code}`（約 49 字元）。**舊的長網址仍然有效**（見
    download_invoice_file_public），已經寄出去的連結不會因為這次改版失效。
    """
    _check_finance_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        inv = await session.get(CrmInvoice, invoice_id)
        if not inv:
            raise HTTPException(status_code=404, detail="找不到此發票")
        require_entity(request, inv.entity or "parent", level="full")
        if not inv.file_url:
            raise HTTPException(status_code=422, detail="這張發票還沒有上傳電子發票檔")
        # 🔴 開不到檔就不要鑄連結。原本這裡是「開不到就 size=0，照樣回 ok」——
        #    複製給客戶的連結按下載會 404，而**只有他看得到**（我們這邊回的是 ok）。
        #    寧可在按鈕上當場講一句人話，讓人去把檔補回去。
        local = _local_invoice_path(inv.file_url)
        if not local:
            raise HTTPException(
                status_code=422,
                detail="這張的電子發票檔在這台機器上開不到（檔案被移走或改名了），"
                       "請重新上傳一次再建連結")
        try:
            size = os.path.getsize(local)
        except OSError as exc:
            raise HTTPException(status_code=422, detail=f"電子發票檔讀不到：{exc}")
        if not inv.share_token:
            # unique index 擋碰撞；72 bits 幾乎不會撞，但撞到就換一個而不是噴 500
            for _ in range(5):
                candidate = _new_share_code()
                dup = (await session.execute(
                    select(CrmInvoice.id).where(CrmInvoice.share_token == candidate))).first()
                if not dup:
                    inv.share_token = candidate
                    break
            else:
                raise HTTPException(status_code=503, detail="短碼產生失敗，請再試一次")
            inv.updated_at = _now()
        # 🔴 **每按一次都重新定稿**（不只第一次鑄碼時）。這顆鈕的語意是「我現在要把這條
        #    寄出去」—— 那一刻客戶會看到的就該是現在這一版。不重新定稿的話，改過金額
        #    之後再複製一次連結，客戶看到的還是舊的，而只有他看得到。
        #    也因為這樣不需要另外做一顆「更新分享內容」。
        inv.share_snapshot = invoice_share.make_snapshot(
            _inv_dict(inv), file_name=ntpath.basename(inv.file_url or ""), file_size=size)
        inv.updated_at = _now()
        await session.commit()
        token, fname = inv.share_token, (inv.share_snapshot.get("file") or {}).get("name", "")
    path = f"/e/{token}"
    return {"status": "ok", "token": token, "path": path,
            "url": share_link.share_url(path, _share_public_base()),
            "file_name": fname}


def _inv_dict(inv) -> dict:
    """ORM 列 → 給 invoice_share 用的 dict。只取白名單那幾個欄位就好，
    整列 `__dict__` 丟過去等於把「以後有人加了新欄位」變成潛在的外洩。"""
    return {k: getattr(inv, k, None) for k in invoice_share.SNAPSHOT_FIELDS}


def _share_public_base() -> str:
    """客戶連結要用的對外網址。報價與發票共用同一個設定（core/share_link.py）。"""
    from config import load_settings
    return share_link.public_base(load_settings())


@router.delete("/invoices/{invoice_id}/share")
async def revoke_invoice_share_link(invoice_id: str, request: Request):
    """作廢已發出的下載連結（寄錯人、客戶換窗口時用）。之後可再產一張新的。"""
    _check_finance_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        inv = await session.get(CrmInvoice, invoice_id)
        if not inv:
            raise HTTPException(status_code=404, detail="找不到此發票")
        require_entity(request, inv.entity or "parent", level="full")
        inv.share_token = None
        inv.updated_at = _now()
        await session.commit()
    return {"status": "ok"}


def _local_invoice_path(db_path) -> str:
    """DB 存的路徑 → **這台機器看得到的**絕對路徑；看不到／不在白名單內回空字串。

    🔴 `file_url` 存的是 **master 視角**的路徑（UNC 或磁碟代號）。NAS 的容器上
       `\\192.168.1.132\Archive\…` 是 `/share/Archive/…` —— 不翻譯的話那邊一律 404，
       而且是安靜的（客戶看到「檔案不存在」，我們這邊什麼都沒發生）。
    🔴 白名單比對要在**翻譯之後**做：翻譯前比對等於拿兩個不同視角的字串比，
       可能誤放行、也可能把好的擋掉。
    🔴 用 `ap != ar and not ap.startswith(ar + os.sep)` 而不是純 startswith：
       `/share/Archive/00_電子發票_舊` 也 startswith `/share/Archive/00_電子發票`。
    """
    raw = str(db_path or "").strip()
    if not raw:
        return ""
    ap = os.path.abspath(to_local_path(raw))
    ar = os.path.abspath(to_local_path(_invoices_root()))
    if ap != ar and not ap.startswith(ar + os.sep):
        return ""      # 免登入端點更不能讓 DB 裡一個被改壞的路徑變成任意檔案讀取
    return ap if os.path.isfile(ap) else ""


async def _share_row(token: str):
    """短碼 → (本機檔案路徑, 快照, 是否作廢)。**免登入**，憑證就是網址裡那串字。

    短碼與舊 JWT 共用這一支：查詢就是「share_token 逐字等於來訪者出示的字串」，
    格式不影響判定 —— 所以改成短碼之後，改版前已經寄出去的長網址照樣有效。
    """
    _require_db()
    if not token:
        raise HTTPException(status_code=401, detail="連結已失效")
    factory = await _get_factory()
    async with factory() as session:
        row = (await session.execute(
            select(CrmInvoice.file_url, CrmInvoice.share_snapshot, CrmInvoice.issue_status)
            .where(CrmInvoice.share_token == token))).first()
    if not row:
        raise HTTPException(status_code=401, detail="連結已失效")
    return _local_invoice_path(row[0]), row[1], invoice_share.is_voided(row[2])


async def serve_invoice_by_share_token(token: str):
    """把檔案本體送出去（attachment）。/e/{code} 那頁的下載鈕與舊長網址共用這一支。

    🔴 **永遠是 attachment，不做 inline**（owner 2026-09-10 拍板不做內嵌預覽）。
       inline 送出使用者上傳的檔＝在官網網域上執行別人給的內容；而上傳黑名單
       `core.project_folders.BLOCKED_UPLOAD_EXTS` 並沒有擋 .svg／.html。
       不 inline 就完全沒有這個面 —— 別為了「順手可以線上看」把它加回來。
    """
    path, _snap, _voided = await _share_row(token)
    if not path:
        raise HTTPException(status_code=404, detail="檔案不存在")
    return no_store_file(path, filename=ntpath.basename(path))


@public_router.get("/public/invoice-file/{token}/meta")
async def invoice_share_meta(token: str, request: Request):
    """分享頁要顯示的東西。**白名單投影**，規則正本 core/invoice_share.py。

    掛 public_router ＝ NAS 對外容器也吃得到（master 關機客戶照樣打得開）。
    曝露面白名單在 test_media_log_public_router 與 test_public_surface，兩支都要同步。
    """
    await surface_gate(request)
    path, snap, voided = await _share_row(token)
    if not invoice_share.has_snapshot(snap):
        # 舊連結（這次改版前鑄的）沒有快照。誠實回報，不要拿 DB 活值硬湊一頁 ——
        # 那正是這次要避免的「畫面與附件對不起來」。重新按一次分享就會定稿。
        raise HTTPException(status_code=503, detail="這個連結還沒有可顯示的內容，請與我們聯繫")
    if not path:
        raise HTTPException(status_code=404, detail="檔案不存在")
    from config import load_settings
    return invoice_share.meta(snap, voided=voided, seller=load_settings().get("company") or {},
                              bankbook=(not voided) and bool(_bankbook_local_path()))


@public_router.get("/public/invoice-file/{token}/bankbook")
async def invoice_share_bankbook(token: str, request: Request):
    """分享頁「匯款資訊」那列的「存摺影本」下載（owner 2026-09-12）。

    憑證跟其他三支一樣是那張發票的短碼（沒有短碼就拿不到存摺）；作廢的發票不給
    （`meta()` 那頭已經不畫那一列，這裡再擋一次是為了直接打網址的人）。
    檔案在發票根目錄底下（`_BANKBOOK_DIR`），走同一份 `_local_invoice_path` 白名單，
    所以 NAS 對外容器也送得出去 —— master 關機客戶照樣拿得到。**永遠 attachment**。
    """
    await surface_gate(request)
    _path, _snap, voided = await _share_row(token)
    path = "" if voided else _bankbook_local_path()
    if not path:
        raise HTTPException(status_code=404, detail="檔案不存在")
    return no_store_file(path, filename=ntpath.basename(path))


# ── 存摺影本（匯款資訊的附件）────────────────────────────────
#
# 全公司一份、放在**發票根目錄**底下而不是 master 的 company_assets/：那頁由 NAS 對外
# 容器 serve，company_assets/ 是 master 本機資料夾、NAS 看不到 —— 放那裡的話 master
# 關機時「下載發票」好的、「存摺影本」404，同一頁兩顆鈕一顆好一顆壞。
# 位置固定（`_公司/存摺影本.<ext>`）而且**不進設定**：兩台都直接到那裡找，所以上傳完
# 兩邊立刻生效，不必等 /publish 把路徑字串送去 NAS（第一版存了 `company.bankbook_path`，
# 換副檔名後 NAS 那台會指著已刪的舊檔 —— 設定沒有承擔任何事，拿掉）。
_BANKBOOK_DIR = "_公司"
_BANKBOOK_STEM = "存摺影本"
_BANKBOOK_MAX_BYTES = 10 * 1024 * 1024
# 看檔頭不看副檔名（同 api_system 的公司圖）：存摺影本是 PDF 或掃描圖
_BANKBOOK_MAGIC = ((b"%PDF", ".pdf"), (b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"))


def _bankbook_ext(head: bytes) -> str:
    for magic, ext in _BANKBOOK_MAGIC:
        if head.startswith(magic):
            return ext
    raise HTTPException(status_code=400, detail="只收 PDF／PNG／JPG")


_BANKBOOK_EXTS = tuple(ext for _magic, ext in _BANKBOOK_MAGIC)


def _bankbook_candidates() -> list:
    """固定位置底下叫 `存摺影本.<認得的副檔名>` 的檔，**最新的排前面**；資料夾不存在＝空清單，不炸。

    `.part` 半成品不在裡面（副檔名白名單濾掉）—— 它同時是「別再刪到同時進行中的另一個上傳」的保證。
    最新優先而不是字母序：Windows 上舊檔正被人下載時刪不掉（見 upload_bankbook），兩個副檔名並存
    的那一小段時間，要給剛上傳的那份，不然客戶與設定頁會一直拿到舊的、而上傳明明回了 200。
    """
    base = os.path.join(_invoices_write_root(), _BANKBOOK_DIR)
    found = [p for p in glob.glob(os.path.join(glob.escape(base), _BANKBOOK_STEM + ".*"))
             if os.path.splitext(p)[1].lower() in _BANKBOOK_EXTS]
    return sorted(found, key=os.path.getmtime, reverse=True)


def _bankbook_local_path() -> str:
    """存摺影本 → 這台開得到的絕對路徑；沒有／不在白名單回空字串。"""
    for p in _bankbook_candidates():
        return _local_invoice_path(p)
    return ""


@router.post("/invoices/bankbook")
async def upload_bankbook(request: Request, file: UploadFile = File(...)):
    """上傳／換掉存摺影本（管理員）：存進發票根目錄 `_公司/存摺影本.<ext>`，同名只留一份。"""
    check_admin(request)
    head = await file.read(8)
    ext = _bankbook_ext(head)
    await file.seek(0)
    base = os.path.join(_invoices_write_root(), _BANKBOOK_DIR)
    try:
        os.makedirs(base, exist_ok=True)
    except OSError as e:
        raise HTTPException(status_code=422, detail=f"資料夾無法使用：{e}")
    from core.project_folders import stream_to_disk
    filepath = os.path.join(base, _BANKBOOK_STEM + ext)
    tmp = filepath + ".part"
    written = await asyncio.to_thread(stream_to_disk, file.file, tmp, _BANKBOOK_MAX_BYTES)
    if written < 0:                        # stream_to_disk 自己刪半成品
        raise HTTPException(status_code=413, detail=f"檔案超過 {_BANKBOOK_MAX_BYTES // 1024 // 1024}MB")
    try:
        os.replace(tmp, filepath)         # 先換上新檔再清舊的：replace 失敗時舊檔還在、連結不會 404
    except OSError as e:                  # Windows 上舊檔正被人下載中會拒絕覆蓋；半成品要清掉、錯誤要講人話
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise HTTPException(status_code=422, detail=f"檔案無法寫入（可能正被下載中，請稍後再試）：{e}")
    leftover = []
    for old in _bankbook_candidates():    # 換副檔名也不殘留（.pdf → .jpg 舊的那份要走）
        if old != filepath:
            try:
                os.remove(old)
            except OSError:               # 正被人下載中刪不掉：新檔靠 mtime 已經排前面，但要講出來
                leftover.append(ntpath.basename(old))
    return {"status": "ok", "path": _stored_path(filepath),
            "file_name": ntpath.basename(filepath), "size": os.path.getsize(filepath),
            "leftover": leftover}


@router.get("/invoices/bankbook")
async def get_bankbook(request: Request, download: bool = Query(False)):
    """設定頁那格「目前檔案」（管理員）：`?download=1` 給檔，否則只回檔名與大小；沒有回 404。"""
    check_admin(request)
    path = _bankbook_local_path()
    if not path:
        raise HTTPException(status_code=404, detail="尚未上傳")
    if download:
        return no_store_file(path, filename=ntpath.basename(path))
    return {"file_name": ntpath.basename(path), "size": os.path.getsize(path)}


@public_router.get("/public/invoice-file/{token}/download")
async def invoice_share_download(token: str, request: Request):
    """分享頁那顆下載鈕。"""
    await surface_gate(request)
    return await serve_invoice_by_share_token(token)


@public_router.get("/public/invoice-file/{token}")
async def download_invoice_file_public(token: str, request: Request):
    """舊的長網址（v1 的 JWT 版）。改用 /e/{code} 短碼之後仍保留這條 ——
    改版前已經寄給客戶的連結不該因為我們換格式而變成死連結。

    🔴 語意維持「**直接下載**」，不要跟著改成回那一頁：那些連結是很久以前寄出去的，
    對方（多半是會計師）的習慣是點了就拿到檔。
    2026-09-10 從 token_router 移到 public_router —— owner「master 關機也要拿得到」。
    """
    await surface_gate(request)
    return await serve_invoice_by_share_token(token)


@router.post("/invoices/backfill-share-snapshots")
async def backfill_share_snapshots(request: Request, apply: bool = Query(False)):
    """把**改版前**鑄的分享連結補上定稿快照（owner 2026-09-10 一次性）。

    2026-09-10 之前的 `/e/{短碼}` 是「點了直接下載」，DB 裡沒有 `share_snapshot`；
    改成一頁之後那些連結會落到 `/meta` 的 503（「還沒有可顯示的內容」）。
    這支把它們一次補齊 —— 效果等同人工去每一張按一次「複製連結」。

    預設 dry-run，`?apply=true` 才真的寫 —— 比照 migrate-files 的契約。
    **冪等**：已經有快照的直接跳過，重複跑不會蓋掉已經定稿的內容。

    🔴 補的是「**現在**這一版的欄位」，不是寄出當下的。對這批舊連結沒有更好的來源
       —— 人工按那顆鈕也是補現在這一版。差別只在定稿的時刻是「補齊的當下」而不是
       「當初寄出時」，所以**發版後越早跑越接近當初**。

    🔴 跟 migrate-files 同一個限制：一定要由 agent 自己跑。發票檔可能在 NAS 上，
       其他 session 看不到那些對映（memory 的 Session 0 陷阱）。
    """
    check_admin(request)
    _require_db()
    factory = await _get_factory()
    done, already, no_file = [], 0, []
    async with factory() as session:
        rows = (await session.execute(
            select(CrmInvoice).where(CrmInvoice.share_token.isnot(None),
                                     CrmInvoice.share_token != ""))).scalars().all()
        for inv in rows:
            if invoice_share.has_snapshot(inv.share_snapshot):
                already += 1
                continue
            local = _local_invoice_path(inv.file_url)
            if not local:
                # 有連結卻沒有（看得到的）檔 —— 補了也送不出東西。列出來讓人決定
                # 是把檔補回去還是把連結撤掉，不要靜默寫一個指向空氣的快照。
                no_file.append({"id": inv.id, "invoice_number": inv.invoice_number or "",
                                "title": inv.title, "path": inv.file_url or ""})
                continue
            try:
                size = os.path.getsize(local)
            except OSError:
                size = 0
            if apply:
                inv.share_snapshot = invoice_share.make_snapshot(
                    _inv_dict(inv), file_name=ntpath.basename(inv.file_url or ""),
                    file_size=size)
                # 🔴 不動 updated_at：這是補資料不是使用者改了發票，
                #    動它會讓「最近更新」整批跳到今天、把真正的異動蓋掉。
            done.append({"id": inv.id, "invoice_number": inv.invoice_number or "",
                         "title": inv.title})
        if apply and done:
            await session.commit()
    return {"status": "ok", "applied": bool(apply),
            "scanned": len(rows), "already_had": already,
            "backfilled": len(done), "rows": done,
            "no_file": no_file}


@router.post("/invoices/migrate-files")
async def migrate_invoice_files(request: Request, apply: bool = Query(False)):
    """把已上傳的電子發票檔搬到**目前**的發票根目錄底下（改過根目錄後補搬）。

    預設 dry-run，`?apply=true` 才真的搬 —— 比照匯入腳本的契約。

    🔴 一定要由 agent 自己跑，不能從別的 shell：主控 agent 跑在 Session 1、
    握有 NAS 的 SMB 連線；其他 session（例如排程或維運用的 shell）看不到那些
    對映與 UNC，會一路 Access denied（memory 的 Session 0 陷阱）。

    搬移交給 _resync_invoice_file —— 它算的目標路徑就是「目前根目錄 + 這張發票
    現在的欄位」，所以順便把舊檔名（例如上傳時號碼還空著而落到 id 前 8 碼的）
    一起對齊。不覆蓋同名檔、搬不動就維持原狀並列在回應裡。
    """
    check_admin(request)
    _require_db()
    factory = await _get_factory()
    moved, skipped, missing = [], [], []
    async with factory() as session:
        rows = (await session.execute(
            select(CrmInvoice).where(CrmInvoice.file_url.isnot(None),
                                     CrmInvoice.file_url != ""))).scalars().all()
        for inv in rows:
            old = inv.file_url
            local_old = to_local_path(old or "")
            if not local_old or not os.path.isfile(local_old):
                missing.append({"title": inv.title, "path": old})
                continue
            if apply:
                # 🔴 _resync_invoice_file 只搬檔、**不會**改 inv.file_url —— DB 這半
                # 一定要在這裡自己寫回去。少了這行＝檔案搬走了、DB 還指著舊路徑，
                # 稅務憑證全變孤兒檔（2026-08-19 dev 實測到，幸好沒先對生產跑）。
                # 搬不動（目標已存在／權限不足）時它回傳原路徑，就是下面的相等判斷。
                new = await asyncio.to_thread(_resync_invoice_file, inv)
            else:
                day = _fmt_day(inv.invoice_date)
                new = _stored_path(os.path.join(
                    _invoices_write_root(), day[:4] or "nodate", day[:7] or "nodate",
                    _invoice_file_name(inv, os.path.splitext(local_old)[1])))
            if os.path.abspath(new) == os.path.abspath(old):
                skipped.append({"title": inv.title, "path": old,
                                **({"why": "目標已存在或搬移失敗"} if apply else {})})
                continue
            if apply:
                inv.file_url = new
                inv.updated_at = _now()
            moved.append({"title": inv.title, "from": old, "to": new})
        if apply:
            await session.commit()
    return {"status": "ok", "applied": apply, "root": _invoices_root(),
            "moved": moved, "skipped": skipped, "missing": missing,
            "summary": f"{'已搬移' if apply else '將搬移'} {len(moved)} 個；"
                       f"原地不動 {len(skipped)} 個；找不到檔案 {len(missing)} 個"}


@router.get("/invoice-applicants", dependencies=[Depends(money_dep)])
async def get_invoice_applicants(request: Request):
    """開發票的申請人名單（誰能被選為這張票的申請人）。

    🔴 存在 settings 而不是瀏覽器的 localStorage —— 那是每台電腦各自一份，
    在辦公室設好、回家開就只剩空白，而且沒有人知道少了誰（2026-08-20 owner 實際
    踩到：下拉只剩「—」）。這是全公司共用的一份名單，本來就該存在伺服器。

    ⚠ 與 get_invoices_root 同樣刻意不走 settings/load 整包：那條回應對機密欄位是
    遮罩過的，前端拿整包改一個鍵再存回會把真密碼洗成遮罩值。

    沒設定過就用發票資料裡實際用過的人（依張數排序）當預設，名單永遠不會是空的。
    """
    _require_db()
    from sqlalchemy import func as _f
    safunc_count = _f.count
    from config import load_settings
    saved = load_settings().get("invoice_applicants")
    if isinstance(saved, list):
        return {"applicants": [str(x) for x in saved if str(x).strip()],
                "source": "settings"}
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmInvoice.applicant, safunc_count(CrmInvoice.id))
            .where(CrmInvoice.applicant.isnot(None), CrmInvoice.applicant != "")
            .group_by(CrmInvoice.applicant)
            .order_by(safunc_count(CrmInvoice.id).desc()))).all()
    return {"applicants": [r[0].strip() for r in rows if (r[0] or "").strip()],
            "source": "data"}


@router.get("/invoice-fee-rates", dependencies=[Depends(money_dep)])
async def get_invoice_fee_rates(request: Request):
    """代開手續費率（%）。應匯給代開人的錢 = 面額 × (1 − 費率)。

    🔴 存 settings 不存 localStorage：費率是公司政策，每台電腦各一份的話，
    同一張發票在不同人手上會算出不同的應匯金額（而那個數字會變成一張應付款）。
    """
    from config import load_settings
    saved = load_settings().get("invoice_fee_rates")
    rates = dict(PASSTHROUGH_FEE_RATES)
    if isinstance(saved, dict):
        rates.update({k: float(v) for k, v in saved.items()
                      if k in PASSTHROUGH_FEE_RATES})
    return {"rates": rates}


@router.put("/invoice-fee-rates")
async def set_invoice_fee_rates(request: Request):
    check_admin(request)
    from config import load_settings, save_settings
    body = await request.json()
    rates = body.get("rates")
    if not isinstance(rates, dict):
        raise HTTPException(status_code=422, detail="rates 要是物件")
    clean = {}
    for k, v in rates.items():
        if k not in PASSTHROUGH_FEE_RATES:
            raise HTTPException(status_code=422, detail=f"未知的代開類別：{k}")
        try:
            pct = float(v)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"{k} 的費率要是數字")
        if not 0 <= pct < 100:
            raise HTTPException(status_code=422, detail=f"{k} 的費率要在 0~100 之間")
        clean[k] = pct
    st = load_settings()
    st["invoice_fee_rates"] = clean
    save_settings(st)
    return {"ok": True, "rates": clean}


@router.put("/invoice-applicants")
async def set_invoice_applicants(request: Request):
    """整份取代申請人名單。空陣列＝清空（下次讀會退回用發票資料推導）。"""
    check_admin(request)
    from config import load_settings, save_settings
    body = await request.json()
    names = body.get("applicants")
    if not isinstance(names, list):
        raise HTTPException(status_code=422, detail="applicants 要是陣列")
    clean, seen = [], set()
    for n in names:
        n = str(n or "").strip()
        if n and n not in seen:      # 去重、保留順序（順序＝下拉的顯示順序）
            seen.add(n)
            clean.append(n)
    cfg = load_settings()
    cfg["invoice_applicants"] = clean
    save_settings(cfg)
    return {"status": "ok", "applicants": clean}


def validate_root_dir(root: str) -> None:
    """檔案根目錄設定的唯一驗證：完整路徑（磁碟機或 UNC）＋建得出來＋真的寫得進去。
    發票根目錄與報價單資料夾共用（規則只此一份；422 的字也一樣）。"""
    if root:
        # 🔴 一定要擋相對路徑。`os.makedirs("192.168.1.132\\Archive\\…")` 會**成功** ——
        # 它在 agent 的工作目錄底下建出一整串資料夾，於是「存到 NAS」變成靜靜存進
        # C:\OriginsunAgent\192.168.1.132\… 而畫面上一切正常。2026-08-19 owner 少打
        # 開頭的兩個反斜線就中招（三個電子發票檔全落在本機）。
        # 合法只有兩種：磁碟機開頭（D:\...）或真正的 UNC（\\server\share\...）。
        # 🔴 `os.path.isabs()` 不夠：Windows 對「單一個反斜線開頭」也回 True，
        # 但那是「目前磁碟的根目錄」—— 少打一個反斜線的 \\192.168.1.132\Archive
        # 會變成 C:\192.168.1.132\Archive，而且 makedirs 與寫入測試**都會成功**，
        # 完全看不出存錯地方（2026-08-19 少打兩個、又少打一個，各中一次）。
        drive, _ = ntpath.splitdrive(root)
        looks_unc = root.startswith("\\\\") or root.startswith("//")
        if not (looks_unc or (drive and drive.endswith(":"))):
            raise HTTPException(status_code=422, detail=(
                f"「{root}」不是完整路徑。NAS 請用 \\\\192.168.1.132\\Archive\\... "
                f"（**開頭兩個**反斜線），本機碟請用 D:\\... —— 只有一個反斜線或沒有的話，"
                f"檔案會被存進主控端自己的資料夾裡，而且畫面上完全看不出來。"))
        try:
            os.makedirs(root, exist_ok=True)
        except OSError as e:
            raise HTTPException(status_code=422, detail=f"資料夾無法使用：{e}")
        # makedirs 成功不等於寫得進去（NAS 可能給了列目錄權限卻不給寫）——
        # 實際寫一個檔再刪掉才算數，不然錯誤會延到「同事上傳發票」那一刻才爆。
        probe = os.path.join(root, ".originsun_write_test")
        try:
            with open(probe, "wb") as f:
                f.write(b"ok")
            os.remove(probe)
        except OSError as e:
            raise HTTPException(status_code=422, detail=f"資料夾不可寫入：{e}")


@router.get("/invoices-root")
async def get_invoices_root(request: Request):
    """電子發票根目錄設定（admin 專用）。

    ⚠ 與 costs.get_receipts_root 同樣刻意不走 settings/load 整包 —— 那條回應對
    機密欄位是遮罩過的，前端拿整包改一鍵再存回會把真密碼洗成遮罩值。
    """
    check_admin(request)
    from config import load_settings
    return {"invoices_root": (load_settings().get("invoices_root") or ""),
            "default": os.path.join(os.getcwd(), "uploads", "invoices"),
            "effective": _invoices_root()}


@router.post("/invoices-root")
async def set_invoices_root(request: Request):
    """設定電子發票根目錄（管理員）：空字串＝回到預設；路徑先 validate_root_dir（要存在、可寫）。"""
    check_admin(request)
    from config import load_settings, save_settings
    body = await request.json()
    root = (body.get("invoices_root") or "").strip()
    validate_root_dir(root)
    # settings.json 寫入也包起來 —— agent 自己會定期寫 settings，撞到檔案佔用
    # 會炸成裸 500（收據那支踩過，同一個坑）
    try:
        s = load_settings()
        s["invoices_root"] = root
        save_settings(s)
    except OSError as e:
        raise HTTPException(status_code=503, detail=f"設定檔忙碌中，請再按一次儲存（{e}）")
    return {"status": "ok", "invoices_root": root, "effective": _invoices_root()}
