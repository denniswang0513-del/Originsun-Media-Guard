# -*- coding: utf-8 -*-
"""routers/crm/cash.py — 收支明細（含應付／應收、收款↔發票分配）。

2026-08-30 從 `finance.py` 切出來。那個檔 2,996 行 —— 超過 AI 單次讀取上限
（2,000 行），而且是這三週改動次數第一名（56 次）：**最常改的檔案正好是
一次讀不完的那個**，改起來等於盲改。

怎麼決定切在哪：不是照註解分隔線（那樣切會看到假的循環依賴），而是
① 函式呼叫圖的強連通分量 —— 實測**零循環**，所以拆得開；
② 每個 helper 歸給「用到它的端點群」，多群共用的留在 finance；
③ 迭代到不動點，任何造成反向邊的名字拉回 finance。
結果本檔**只 import finance，沒有反向依賴**。

URL 一條都沒變，route 一樣掛在 `_shared.router` 上。
🔴 檔內順序不可重排：`/{id}` 這種吃萬用字元的路徑必須排在具名路徑之後
（FastAPI 先註冊先贏）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, UploadFile, File, Query

from core.finance_logic import (INVOICE_COLLECTED_STATUSES as INVOICE_COLLECTED,
                                month_of)
from core.auth import check_logged_in
from sqlalchemy import and_ as _sa_and
from core.cash_taxonomy import SEP as _TAX_SEP, split_category as _tax_split
from core.ledger import (not_mine as _cli_not_mine, require_entity)
from core.project_link import CASH_CATEGORIES as _PROJECT_LINK_CATEGORIES
from core.schemas import (CashEntryPayload,
                          CashInvoiceLinksPayload, CashPaymentLinksPayload)

from ._shared import (router, money_dep, _require_db,
                      ledger_categories_and_tree,
                      _get_factory, _now,
                      _parse_shoot_date, _assert_month_open, _locked_month_set, _raise_locked_batch, map_csv_row)

# 這個檔案唯一用得到發票檔那邊的東西：改發票時要跟著改檔名

try:
    from ._shared import (select, or_, Client, CrmProject,
                          CrmStaff, CrmInvoice, CrmPaymentRequest,
                          CrmCashEntry, CrmProjectExpense,
                          CrmCashInvoiceLink)
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同原檔 try/except
    pass

# ── CSV 匯入共用 ────────────────────────────────────────────


# 正本在 finance.py 的共用 helper（單向依賴：finance 不用本檔）
from .finance import (  # noqa: F401
    _ALLOC_KINDS,
    _alloc_verdict,
    _entity_for_write,
    _get_mine_project,
    _import_money_csv,
    _load_allocs,
    _load_payment_allocs,
    _mine_or_admin_write,
    _mine_or_admin_write_rows,
    _parse_money,
    _payment_verdict,
    _set_primary_invoice,
    replace_invoice_allocs,
    resettle_invoices,
    resolve_allocs)



async def resolve_invoice_allocs(session, items, ent, by_id=None):
    return await resolve_allocs(session, items, ent, "invoice", by_id)


def _to_cash_dict(e, project_name: str = "", invoice_title: str = "",
                  petty_status: str = "", tax_path=None) -> dict:
    return {
        "id": e.id,
        "entity": e.entity or "parent",
        "entry_date": e.entry_date.isoformat() if e.entry_date else None,
        "expense": e.expense, "claim": e.claim, "deposit": e.deposit,
        "summary": e.summary or "", "note": e.note or "",
        "bank_memo": getattr(e, "bank_memo", "") or "",
        "category": e.category or "",
        # 三層（book/item 由複合鍵拆出；item 欄有值時以欄為準 —— 匯入時填的
        # 比拆字串可靠）。sub_item 是第三層。
        "book": _tax_split(e.category)[0],
        "item": (e.item or "") or _tax_split(e.category)[1],
        "sub_item": e.sub_item or "", "payee": e.payee or "",
        "status": e.status or "",
        "has_invoice": e.has_invoice, "invoice_number": e.invoice_number or "",
        "project_label": e.project_label or "", "project_id": e.project_id or "",
        "project_name": project_name,
        "payment_date": e.payment_date.isoformat() if e.payment_date else None,
        "payment_status": e.payment_status or "",
        "invoice_id": e.invoice_id or "",
        "bank_fee": e.bank_fee,
        "advance_payment_id": e.advance_payment_id or "",
        "bank_account_id": getattr(e, 'bank_account_id', '') or "",
        "payment_request_id": getattr(e, 'payment_request_id', '') or "",
        # 零用金推送狀態（routers/crm/petty.petty_from_cash）。expense_id 指向
        # 已刪的單據時 petty_status 會是空的 —— 那就是「沒推過」，跟撤銷後一樣。
        "expense_id": getattr(e, 'expense_id', '') or "",
        "petty_status": petty_status or "",
        # 分類樹：節點 id ＋ **完整**路徑。上面的 book/item/sub_item 只是前三層的
        # 鏡射，第四層以後（家用▸變動支出▸醫療保健▸乳癌治療▸台北馬偕）只有這裡看得到。
        "taxonomy_node_id": getattr(e, 'taxonomy_node_id', '') or "",
        "taxonomy_path": list(tax_path or []),
        "invoice_title": invoice_title,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


# F1 月結守衛（_assert_month_open / _assert_rows_open / _locked_month_set /
# _raise_locked_batch）唯一實作在 routers/crm/_shared.py — 含「守哪個日期欄」判準表。


# 這幾欄是 soft FK：空字串跟 NULL 在 SQL 裡不是同一件事，`WHERE project_id IS NULL`
# 會漏掉存成 '' 的列。寫入前一律把空字串收斂成 None。
_CASH_FK_FIELDS = ("project_id", "invoice_id", "bank_account_id",
                   "advance_payment_id", "payment_request_id")


def _normalize_cash_fks(e):
    for f in _CASH_FK_FIELDS:
        if getattr(e, f, None) == "":
            setattr(e, f, None)


async def _sync_taxonomy(session, e, data: dict, paths: dict | None = None):
    """`taxonomy_node_id` ↔ `category`/`item`/`sub_item` 保持一致 —— **規則只有這一份**。

    兩個方向都要走，因為寫入端不只一個：
    - 前端挑了分類樹的節點 → 送 `taxonomy_node_id`，**節點是正本**，三欄由
      `mirror_from_path` 推導（前端送什麼 category 都不算數 —— 兩邊各算一次
      必漂，而且漂的是會計對映的鍵）。
    - CSV／銀行對帳單匯入、對帳腳本走舊路，只有 category/sub_item →
      反查掛上節點。不掛的話那些列要等下次 boot 的回填才進得了樹狀篩選。

    🔴 `taxonomy_node_id` 明確送空 → 連三欄一起清掉。只清節點會留下一列
    「有類別、但不在樹上」的孤兒，樹狀篩選看不到它。
    """
    from core.cash_taxonomy import mirror_from_path, path_from_columns
    from core.cash_tree import find_node_id, node_id_in, path_map

    ent = e.entity or "parent"
    if "taxonomy_node_id" in data:
        nid = (data.get("taxonomy_node_id") or "").strip()
        if not nid:
            e.taxonomy_node_id = None
            e.category = e.item = e.sub_item = ""
            return
        # `paths` ＝呼叫端已經算好的 id→路徑表（批次分類一次 200 列，各撈一次
        # 樹就是 200 趟）。沒帶就自己撈 —— 單筆更新那條路不必為此改。
        path = (paths if paths is not None else await path_map(session, ent)).get(nid)
        if not path:
            raise HTTPException(status_code=400, detail="找不到這個分類節點")
        e.taxonomy_node_id = nid
        e.category, e.item, e.sub_item = mirror_from_path(path)
        return
    if not ({"category", "item", "sub_item"} & set(data)):
        return                                  # 這次沒動分類，不必重算
    # 送什麼就先落到列上，**沒送的欄一律不動**。
    #
    # 🔴 這裡不推導 `item`。它看起來像 category 第二層的鏡射，但那只在私帳成立
    # （私帳的 category 是 `書_項目` 複合鍵）。母公司那本的 category 全是平的，
    # `item` 放的是**獨立資料**：零用金放那張 AP 的類別、福委會放 AP_CATEGORY、
    # CSV 匯入吃「項目」欄。拿 `item_of(category)`（平類別＝空字串）去覆蓋，等於
    # 每次從編輯視窗按存檔就把那一欄清空 —— 而畫面上不會有任何提示。
    for k in ("category", "item", "sub_item"):
        if k in data:
            setattr(e, k, data.get(k) or "")
    # `paths` 給了就在記憶體裡反查（`find_node_id` 是逐層各一次 SELECT，批次那
    # 條路一次跑幾百列就是上千趟）。兩種查法都住在 core/cash_tree，路徑怎麼算
    # 也只有 `path_from_columns` 一份 —— 這裡沒有第二份規則。
    want = [x for x in path_from_columns(e.category, e.sub_item) if x]
    # 🔴 本來就掛在**第 4 層以後**、而三欄描述的前三層沒變 → 保留原本的節點。
    #
    # 三欄只裝得下 3 層（`mirror_from_path` 的規則：第 4 層以後刻意不進那三欄，
    # 深度活在 `taxonomy_node_id` 裡）。拿三欄回頭反查一定只找得到第 3 層那個，
    # 所以不擋的話，使用者只是來改別的欄位按一次存檔，分類就被降級了 ——
    # 收支明細的編輯視窗每次都會送 category＋sub_item，等於每存一次降一次。
    # 實測：`家用▸變動支出▸醫療保健▸乳癌治療▸台北馬偕` 會掉成
    # `家用▸變動支出▸醫療保健`，而私帳有 60 筆掛在那麼深。
    #
    # 前三層真的被改掉時（`cur[:3] != want`）就不保留 —— 那是使用者確實換了分類。
    if e.taxonomy_node_id and len(want) == 3:
        tbl = paths if paths is not None else await path_map(session, ent)
        cur = tbl.get(e.taxonomy_node_id)
        if cur and len(cur) > 3 and cur[:3] == want:
            return
    nid = ""
    if want:
        nid = (node_id_in(paths, want) if paths is not None
               else await find_node_id(session, ent, want))
    e.taxonomy_node_id = nid or None
    # 🔴 這條路**只寫節點**，三欄維持呼叫端送進來的。想從路徑反推三欄的話要用
    # `mirror_from_path`，但它跟 `path_from_columns` 不是互為反向：平的類別配上
    # 子項目時（母公司的類別全是平的），`path_from_columns('其他','保險費')` 出
    # `['其他','保險費']`，mirror 讀位置 1 當項目 → `('其他_保險費','保險費','')`
    # —— 類別被改寫成一個 finance_category_map 裡沒有的複合鍵（那一列於是掉進
    # 三表的「未歸類」），子項目還不見了。生產有這種列。


async def _assert_project_same_entity(session, e):
    """🔴 不變式：收支只能掛**同一本帳**的專案。

    下拉帶了 entity 之後這條路正常走不到，但守衛不能靠 UI —— 跨帳本的組合會讓
    那筆錢在兩本帳的「掛帳支出」裡**都不出現**（api_finance_projects._rollups
    按 entity 篩收支、再按 entity 迭代專案，兩者對不上就靜靜消失），比多算一筆
    更難發現。比照 api_finance_projects._owned_project 回 403。
    """
    if not e.project_id:
        return
    from db.models import CrmProject
    p = await session.get(CrmProject, e.project_id)
    if p and (p.entity or "parent") != (e.entity or "parent"):
        raise HTTPException(
            status_code=403,
            detail="不能把收支掛到另一本帳的專案上")


async def _sync_mine_project_received(session, project_id, delta: int):
    """私帳收款規則（owner 2026-08-25「勾選專案，如果金額到齊，就是收款」）：
    掛在專案上的**收入**驅動該案的 已收/應收/收款狀態。

    🔴 增量制（±delta），不是「從掛帳收入整個重算」—— 歷史已收是匯入的基準值
    （並非每一筆歷史收款都有對應的掛帳收支列），重算會把老案的已收洗掉。
    新增 +deposit、刪除 −deposit、編輯＝先減舊再加新，帳永遠平。

    🔴 只管 entity='mine'（_get_mine_project）。到齊（已收 ≥ 營收且營收 > 0）
    ＝全額到帳；部分＝部分到帳；歸零＝未到帳。應收 = 營收 − 已收（可為負：
    溢收要看得見，Sheet 的泛亞 −3,000 就是這種）。
    """
    if not delta:
        return
    p = await _get_mine_project(session, project_id)
    if not p:
        return
    from core.ledger_project import norm_detail, receivable_fields
    p.amount_received = int(p.amount_received or 0) + int(delta)
    # 應收與收款狀態的算式正本在 core（四個寫入端共用；基準＝實際會進帳的錢，
    # 不是營收 —— 見 receivable_fields）
    p.amount_receivable, p.payment_status = receivable_fields(
        int(p.contract_amount or 0), p.amount_received, norm_detail(p.ledger_detail))
    p.updated_at = _now()


def _enforce_cash_project_link(e):
    """🔴 不變式：只有專案類的收支可以掛專案（core/project_link.CASH_CATEGORIES）。

    在**賦值之後**檢查最終狀態 —— 賦值前預測會漏掉「只改 category、不動 project_id」
    那條路（零用金那邊踩過同樣的洞，見 petty._enforce_project_link 的說明）。
    行政／薪資／房租那種公司層級支出掛到專案上，專案毛利就會多算一筆不屬於它的錢。
    """
    from core.project_link import cash_can_link
    _ent = getattr(e, "entity", None)      # 測試替身可能沒有這個欄；預設＝母公司規則
    if e.project_id and not cash_can_link(_ent, e.category):
        if (_ent or "parent") == "mine":
            raise HTTPException(
                status_code=409,
                detail=f"「{e.category or '未分類'}」不能掛專案 —— 私帳只有"
                       "「公司」開頭的類別（公司_專案／公司_專案支出…）能掛，"
                       "個人與家用掛上去會污染專案毛利")
        raise HTTPException(
            status_code=409,
            detail=f"「{e.category or '未分類'}」的收支不能連結專案 —— "
                   f"可連結的類別：{'、'.join(_PROJECT_LINK_CATEGORIES)}")


@router.get("/cash-entries/options", dependencies=[Depends(money_dep)])
async def cash_entry_options(request: Request, entity: str = Query("")):
    """收支明細的下拉選項來源（比照 /petty/options）。

    類別清單與「哪些類別可連結專案」都由後端說了算 —— 前端寫死的下拉會跟
    finance_category_map 脫節：`貸款繳款`／`貸款補貼`／`銀行借款` 這些後來加的
    類別就沒同步進去，結果對帳單匯入自己寫出來的列，使用者在編輯視窗選不到
    它的類別（實測種子 32 個、前端只有 27 個）。
    """
    ent = require_entity(request, entity, level="full")
    _require_db()
    factory = await _get_factory()
    from core.cash_taxonomy import book_of, taxonomy
    async with factory() as session:
        # 🔴 **這本帳**的值域 —— 不是整份 finance_category_map。那份是兩本共用的，
        # 私帳拿到的會是母公司的平面科目（行政／薪資／交際應酬…），而私帳的報表
        # 根本沒有那些類別：選了存進去就靜靜落到「未歸類」，看起來卻像分好了
        # （owner 2026-08-29「這裡的分類規則不需要和 crm 共用」）。母公司那半仍是
        # 同一份平面科目，所以母公司這條路一個字都沒變。
        cats, tree = await ledger_categories_and_tree(session, ent)
        used = list((await session.execute(
            select(CrmCashEntry.category).where(
                CrmCashEntry.entity == ent,
                CrmCashEntry.category.isnot(None),
                CrmCashEntry.category != "").distinct())).scalars())
        # 只活在拆項裡的類別也算「用過」（父列的 category 已讓位）
        from db.models import CrmCashSplit as _CS
        used += [c for c in (await session.execute(
            select(_CS.category).join(CrmCashEntry,
                                      CrmCashEntry.id == _CS.entry_id)
            .where(CrmCashEntry.entity == ent, _CS.category.isnot(None),
                   _CS.category != "").distinct())).scalars() if c not in used]
        subs = list((await session.execute(
            select(CrmCashEntry.sub_item).where(
                CrmCashEntry.entity == ent,
                CrmCashEntry.sub_item.isnot(None),
                CrmCashEntry.sub_item != "").distinct())).scalars())
    from core.project_link import MINE_CASH_LINK_PREFIX
    linkable = ([c for c in cats if c.startswith(MINE_CASH_LINK_PREFIX)]
                if ent == "mine" else list(_PROJECT_LINK_CATEGORIES))
    # 舊的 taxonomy 扁平欄位先留一版：前端已全面改吃 tree，但發版當下還開著的
    # 舊分頁仍讀它；下一版可以連同 core.cash_taxonomy.taxonomy() 一起收掉。
    # 它的值域＝**這本帳實際用到的類別** ∪ 值域裡同一本底下的（還沒用過的新
    # 類別也要挑得到）。
    books_used = {book_of(c) for c in used}
    tax_cats = used + [c for c in cats if book_of(c) in books_used and c not in used]
    return {"categories": cats,
            "project_link_categories": linkable,
            "taxonomy": taxonomy(tax_cats, subs),
            "tree": tree}


@router.get("/cash-entries", dependencies=[Depends(money_dep)])
async def list_cash_entries(
    request: Request,
    q: str = Query(""), category: str = Query(""),
    project_id: str = Query(""),
    bank_account_id: str = Query(""), direction: str = Query(""),
    date_from: str = Query(""), date_to: str = Query(""),
    sub_item: str = Query(""), book: str = Query(""), item: str = Query(""),
    node_id: str = Query(""),
    status: str = Query(""),
    amount_min: str = Query(""), amount_max: str = Query(""),
    entity: str = Query(""),
):
    """direction：'in'＝只看有收入的、'out'＝只看有支出的、空＝全部。
    date_from/date_to＝含當日；amount_min/max 比的是該列**金額量級**
    （收入或 支出＋匯費，取大者）—— owner 2026-08-26「可以篩選日期區間、
    分類、子項目、金額」。"""
    # 兩本帳：money_dep 之上疊第二層 entity scope（plan §2.4）
    # full：CRM 帳務＝原始帳列，合夥人不可及 —— money_dep 已擋一層，刻意雙保險
    ent = require_entity(request, entity, level="full")
    _require_db()
    factory = await _get_factory()
    from core.cash_tree import load_nodes, path_map, subtree_ids
    async with factory() as session:
        # 分類樹一請求只撈一次（篩選的子樹與每列的路徑共用同一份；含停用 ——
        # 歷史列掛在停用節點上路徑照樣要印得出來）
        tax_nodes = await load_nodes(session, ent, include_inactive=True)
        query = (
            select(CrmCashEntry, CrmProject.name.label("pn"),
                   CrmInvoice.title.label("inv_title"),
                   CrmProjectExpense.status.label("petty_status"))
            .outerjoin(CrmProject, CrmProject.id == CrmCashEntry.project_id)
            .outerjoin(CrmInvoice, CrmInvoice.id == CrmCashEntry.invoice_id)
            # 推去零用金的那幾列要在清單上看得出狀態（PK join，成本可忽略）
            .outerjoin(CrmProjectExpense,
                       CrmProjectExpense.id == CrmCashEntry.expense_id)
            .where(CrmCashEntry.entity == ent)
            .order_by(CrmCashEntry.entry_date.desc())
        )
        # 拆項（帳目一筆、內容拆裂）：分類與專案的正本可能在拆項裡 ——
        # **每一個**分類形狀的篩選都要問拆項（split_category_pred），漏一個
        # 就是拆過的錢在那個篩選裡人間蒸發。lazy import 是循環（cash_splits
        # import 本檔的 _sync_taxonomy），一函式一次。
        from routers.crm.cash_splits import (_split_to_dict, entry_has_splits_subq,
                                             load_advance_links_map, load_splits_map,
                                             project_names_map, split_category_pred)
        from db.models import CrmCashSplit
        if category == "__none__":
            # 「未分類」快篩：卡單匯入後真的分不出的尾巴（owner 逐筆點完就歸零）
            # 🔴 拆項父列的 category 也是空 —— 但它**已經分好了**（正本在拆項），
            # 混進未分類會讓人再分一次、還會被 apply-unclassified 的規則掃到
            query = query.where(or_(CrmCashEntry.category.is_(None),
                                    CrmCashEntry.category == ""),
                                CrmCashEntry.id.notin_(entry_has_splits_subq()))
        elif category:
            query = query.where(or_(
                CrmCashEntry.category == category,
                CrmCashEntry.id.in_(split_category_pred(
                    CrmCashSplit.category == category))))
        if sub_item:
            query = query.where(or_(
                CrmCashEntry.sub_item == sub_item,
                CrmCashEntry.id.in_(split_category_pred(
                    CrmCashSplit.sub_item == sub_item))))
        if status:
            # 卡片明細＝status='card'（owner 2026-08-27「切這個按鈕就切換成
            # 信用卡的明細」）；其餘 status 值同義比對
            query = query.where(CrmCashEntry.status == status)
        # 三層分類的前兩層：儲存是複合鍵，篩選在鍵上做前綴/後綴比對
        # （規則正本 core.cash_taxonomy —— 別在這裡自己拼字串）
        # 分類樹：選到哪一層就看**那一支整支**（選「家用」＝家用底下全部）。
        # 這條蓋過上面 book/item/sub_item 三個舊參數 —— 前端切過去之後只送這個。
        if node_id:
            _ids = await subtree_ids(session, ent, node_id, nodes=tax_nodes)
            query = query.where(or_(
                CrmCashEntry.taxonomy_node_id.in_(_ids),
                CrmCashEntry.id.in_(split_category_pred(
                    CrmCashSplit.taxonomy_node_id.in_(_ids)))))
        if book:
            query = query.where(or_(
                CrmCashEntry.category == book,
                CrmCashEntry.category.like(book + _TAX_SEP + "%"),
                CrmCashEntry.id.in_(split_category_pred(or_(
                    CrmCashSplit.category == book,
                    CrmCashSplit.category.like(book + _TAX_SEP + "%"))))))
        if item:
            query = query.where(or_(
                CrmCashEntry.item == item,
                CrmCashEntry.category.like("%" + _TAX_SEP + item),
                CrmCashEntry.id.in_(split_category_pred(or_(
                    CrmCashSplit.item == item,
                    CrmCashSplit.category.like("%" + _TAX_SEP + item))))))
        if date_from:
            d = _parse_shoot_date(date_from)
            if d:
                query = query.where(CrmCashEntry.entry_date >= d)
        if date_to:
            d = _parse_shoot_date(date_to)
            if d:
                from datetime import timedelta
                query = query.where(CrmCashEntry.entry_date < d + timedelta(days=1))
        if amount_min or amount_max:
            from sqlalchemy import func as _fn
            _amt = _fn.greatest(
                _fn.coalesce(CrmCashEntry.deposit, 0),
                _fn.coalesce(CrmCashEntry.expense, 0) + _fn.coalesce(CrmCashEntry.bank_fee, 0))
            if (amount_min or "").strip().lstrip("-").isdigit():
                query = query.where(_amt >= int(amount_min))
            if (amount_max or "").strip().lstrip("-").isdigit():
                query = query.where(_amt <= int(amount_max))
        if project_id:
            # 拆項掛的專案也要找得到（父列的 project_id 已讓位給拆項）
            query = query.where(or_(
                CrmCashEntry.project_id == project_id,
                CrmCashEntry.id.in_(split_category_pred(
                    CrmCashSplit.project_id == project_id))))
        if bank_account_id:
            query = query.where(CrmCashEntry.bank_account_id == bank_account_id)
        if direction == "in":
            query = query.where(CrmCashEntry.deposit > 0)     # NULL > 0 在 SQL 本來就不成立
        elif direction == "out":
            query = query.where(CrmCashEntry.expense > 0)
        if q:
            # 備註也要搜得到 —— 應收發票標籤、匯款客戶、待確認原因都寫在那裡，
            # 只搜摘要的話「找某張發票的那筆收款」永遠找不到。
            ql = f"%{q}%"
            query = query.where(or_(CrmCashEntry.summary.ilike(ql),
                                    CrmCashEntry.sub_item.ilike(ql),
                                    CrmCashEntry.payee.ilike(ql),
                                    CrmCashEntry.note.ilike(ql),
                                    CrmCashEntry.bank_memo.ilike(ql),
                                    CrmCashEntry.invoice_number.ilike(ql)))
        rows = (await session.execute(query)).all()
        # 路徑一次撈完（幾百個節點一張 dict），不逐列往上爬
        paths = await path_map(session, ent, nodes=tax_nodes)
        # 拆項整本帳一次撈（表很小；用列 id 進 IN 的話，未篩選清單就是
        # 4,733 個 bind param 的 SQL —— /simplify 效率審查）
        smap = await load_splits_map(session, entity=ent)
        amap = await load_advance_links_map(
            session, [s.id for subs in smap.values() for s in subs])
        pnames = await project_names_map(
            session, [s for subs in smap.values() for s in subs])
    out = []
    for r in rows:
        d = _to_cash_dict(r[0], r[1] or "", r[2] or "", r[3] or "",
                          paths.get(r[0].taxonomy_node_id))
        subs = smap.get(r[0].id, [])
        d["splits"] = [_split_to_dict(s, paths.get(s.taxonomy_node_id),
                                      amap.get(s.id),
                                      pnames.get(s.project_id, ""))
                       for s in subs]
        d["split_count"] = len(subs)
        out.append(d)
    return {"entries": out, "total": len(out)}


@router.post("/cash-entries")
async def create_cash_entry(req: CashEntryPayload, request: Request):
    _require_db()
    # summary 在 schema 是選填（PUT 要能只送幾個欄位做部分更新），建立時必填由這裡驗
    if not (req.summary or "").strip():
        raise HTTPException(status_code=422, detail="內容（摘要）必填")
    ent = _entity_for_write(request, req.entity)
    factory = await _get_factory()
    date_fields = {"entry_date", "payment_date"}
    dates = {f: _parse_shoot_date(getattr(req, f)) for f in date_fields}
    data = req.model_dump(exclude=date_fields | {"entity"})
    # 🔴 分類同步要看「前端**真的送了**哪些欄」，不能看 data —— 建立時 data 是
    # 整包（每個欄位都在），照它判會把「只送了 category」的建立當成「明確送了
    # 空的 taxonomy_node_id」，分類當場被清掉。
    sent = req.model_dump(exclude_unset=True)
    e = CrmCashEntry(id=uuid.uuid4().hex, **dates, entity=ent, created_at=_now(), **data)
    _normalize_cash_fks(e)
    async with factory() as session:
        await _assert_month_open(session, dates.get("entry_date"), entity=ent)
        # 分類樹 → 三欄鏡射要跑在可掛專案判定**之前**（判定吃的是 category）
        await _sync_taxonomy(session, e, sent)
        _enforce_cash_project_link(e)
        await _assert_project_same_entity(session, e)
        if ent == "mine":
            await _sync_mine_project_received(session, e.project_id,
                                              int(e.deposit or 0))
        session.add(e)
        await session.flush()      # 讓 _resettle_invoice 的查詢看得到這筆
        # 🔴 建立也要進分配表，不能只有更新路徑做（2026-08-20 實測：新開一筆掛了
        # 發票的收款，列表那欄看得到，但「關聯發票」面板是空的、發票的已收金額
        # 停在 0 —— 因為那兩處讀的是 crm_cash_invoice_links 不是 invoice_id。
        # 要按一次編輯再存檔才會補上，等於帳目正確與否取決於有沒有人多按一下。）
        await _sync_single_alloc(session, e)   # 收款狀態也由它一起收尾
        await session.commit()
    return {"status": "ok", "entry_id": e.id, "entry": {"id": e.id}}


async def _sync_single_alloc(session, e):
    """編輯表單改了發票 → 分配表跟著換成「這一張、全額」。

    🔴 不同步的話兩邊會講不同的話：`crm_cash_entries.invoice_id`（列表那欄讀它）
    指向 A，`crm_cash_invoice_links`（關聯發票面板與 _invoice_collections 讀它）
    還留著 B —— 於是列表顯示 A、面板顯示 B，而 A 的「已收金額」永遠不會動。
    收入列才有意義（分配表講的是收款）。

    🔴 但**已經掛了多張**的收款不歸這裡管 —— 那是合併匯款/分期，由「關聯發票」
    面板維護。硬同步會把三張壓成一張全額（2026-08-20 實測：180,000 掛 A/B/C
    三張，有人在編輯視窗動一下發票下拉，A 與 C 的已收就憑空歸零、B 變成收了
    180,000 超過面額，全程沒有任何提示）。所以多張時擋下來，請他去面板改。
    """
    existing = (await session.execute(
        select(CrmCashInvoiceLink.invoice_id)
        .where(CrmCashInvoiceLink.cash_entry_id == e.id))).scalars().all()
    if len(existing) > 1:
        if e.invoice_id in existing:
            return          # 只是動了別的欄位，多張分配原封不動
        raise HTTPException(
            status_code=409,
            detail=f"這筆收款已經分配給 {len(existing)} 張發票（合併匯款或分期）—— "
                   f"要改請用「關聯發票」面板，從編輯視窗改會把其他幾張的已收金額清掉")
    # deposit 被清成 0（改成支出列）→ 空分配：這筆錢已經不是收款了，
    # 留著舊分配列會讓那張發票永遠算已收（實測會留孤兒）。
    ent = e.entity or "parent"
    rows = (await resolve_invoice_allocs(session, [(e.invoice_id, int(e.deposit or 0))], ent)
            if e.invoice_id and (e.deposit or 0) else [])
    # set_primary=False：這條路的主要發票是使用者在表單挑的，不是從分配表推的
    await replace_invoice_allocs(session, e, rows, when=e.entry_date,
                                 set_primary=False)


@router.put("/cash-entries/{entry_id}")
async def update_cash_entry(entry_id: str, req: CashEntryPayload, request: Request):
    """部分更新：**只寫前端真的送來的欄位**（`exclude_unset=True`）。

    🔴 為什麼不是整包 model_dump：收支明細有 20 幾個欄位，但編輯視窗只送 10 個
    （見前端 `_FIELDS`）。整包寫回會把沒送的 status／project_label／invoice_number／
    payee 全部洗成 pydantic 預設值 —— 匯入進來的「待確認」標記、專案標籤、發票號碼
    只要有人用編輯視窗改一下就沒了。這是全 repo 的既定慣例（20+ 個更新端點都用
    exclude_unset，`update_loan` 也是），這支是漏網的。

    也因為是部分更新，詳情面板的快速連結下拉直接打這支送 `{project_id}` 就好，
    不需要另開一支「只改關聯欄」的端點。
    """
    check_logged_in(request)           # 先擋匿名（不給 401/404 當存在性預言機）
    _require_db()
    factory = await _get_factory()
    date_fields = {"entry_date", "payment_date"}
    data = req.model_dump(exclude_unset=True, exclude={"entity"})
    dates = {f: _parse_shoot_date(data[f]) for f in date_fields if f in data}
    async with factory() as session:
        e = await session.get(CrmCashEntry, entry_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此收支紀錄")
        # 兩本帳：payload.entity None＝維持既有值；帶不同值＝想搬帳本 → 422
        # （寫入守衛也在 _entity_for_write 裡定案）
        ent = _entity_for_write(request, req.entity, e)
        # 🔴 拆項父列：金額、分類、專案的正本在拆項（Σ 不變式＋分類讓位）——
        # 改這些欄會讓 Σ 悄悄失衡或長出第二份分類；要動請走「拆內容」。
        # 🔴 比「值有沒有變」而不是「鍵有沒有送」：編輯視窗每次都整包送
        # _FIELDS（含 deposit/category），按鍵判的話拆項父列連改個摘要都 409
        # —— 使用者被迫「先解除拆項再改」，代墊結清連結跟著陪葬。
        def _norm(field, v):
            if field in ("deposit", "expense"):
                return int(v or 0)
            return (v or "") if not isinstance(v, str) else (v.strip() or "")
        _guarded = {"deposit", "expense", "category", "item", "sub_item",
                    "taxonomy_node_id", "project_id"}
        _changed = {k for k in _guarded & set(data)
                    if _norm(k, data.get(k)) != _norm(k, getattr(e, k, None))}
        if _changed:
            from routers.crm.cash_splits import entry_has_splits
            if await entry_has_splits(session, entry_id):
                raise HTTPException(
                    status_code=409,
                    detail="這一列已經拆項（帳目一筆、內容拆裂）—— 金額、分類與"
                           "專案要在「拆內容」裡改，或先解除拆項"
                           f"（這次想改：{'、'.join(sorted(_changed))}）")
        await _assert_month_open(session, e.entry_date, dates.get("entry_date"),
                                 entity=ent)
        # 私帳收款同步要用「改之前」的 (收入, 專案) 當減項 —— setattr 之後就沒了
        _old_dep, _old_pid = int(e.deposit or 0), e.project_id
        for k, v in data.items():
            if k not in date_fields:
                setattr(e, k, v)
        for k, v in dates.items():
            setattr(e, k, v)
        e.updated_at = _now()
        # 🔴 發票收款狀態只在「這次真的動到 invoice_id」時才連動 —— 部分更新時
        # req.invoice_id 是 None 只代表「沒送這個欄位」，不代表要解除關聯。
        # 🔴 金額變了也要同步分配表，不是只有換發票才要 —— 收款 180,000 打錯成
        # 18,000，分配列會留在 180,000，那張發票的 outstanding 就少算 162,000
        # （明明還欠錢卻顯示收滿）。2026-08-20 實測。
        if "invoice_id" in data:
            _set_primary_invoice(e, await session.get(CrmInvoice, e.invoice_id)
                                 if e.invoice_id else None)
        if "invoice_id" in data or "deposit" in data:
            # 分配表、主要發票欄、相關發票的收款狀態一次到位；換掉的舊發票也會
            # 被重算（共用寫入者比對分配表的前後差異），不用在這裡再比一次
            await _sync_single_alloc(session, e)
        await _sync_taxonomy(session, e, data)      # 同上：要在可掛專案判定之前
        _normalize_cash_fks(e)
        _enforce_cash_project_link(e)
        await _assert_project_same_entity(session, e)
        if ent == "mine" and ("deposit" in data or "project_id" in data):
            new_dep, new_pid = int(e.deposit or 0), e.project_id
            if (_old_dep, _old_pid) != (new_dep, new_pid):
                await _sync_mine_project_received(session, _old_pid, -_old_dep)
                await _sync_mine_project_received(session, new_pid, new_dep)
        await session.commit()
        # 分類是後端從節點路徑推導的（_sync_taxonomy）—— 把推導結果回給前端，
        # 它就地 patch 那一列就好，不必為了拿三欄鏡射把 4,700 列整表重載。
        # 只在這次真的動到分類時才多算（path_map 是一次 237 列的撈取）。
        entry_patch = {}
        if {"taxonomy_node_id", "category", "item", "sub_item"} & set(data):
            from core.cash_tree import path_map
            paths = await path_map(session, e.entity or "parent")
            entry_patch = {
                "category": e.category or "",
                "book": _tax_split(e.category)[0],
                "item": (e.item or "") or _tax_split(e.category)[1],
                "sub_item": e.sub_item or "",
                "taxonomy_node_id": e.taxonomy_node_id or "",
                "taxonomy_path": list(paths.get(e.taxonomy_node_id) or []),
            }
    return {"status": "ok", "entry": entry_patch}


@router.patch("/cash-entries/batch-taxonomy")
async def batch_set_taxonomy(request: Request):
    """批次分類（owner 2026-08-28「我想要有一個可以批次處理分類的按鈕功能」）。

    信用卡明細一天好幾筆「街口電支－統一超商」，一筆一筆點三格分類要按上千次。
    這支把選起來的列一次掛到同一個節點（`taxonomy_node_id=''` ＝一次清掉分類）。

    規則一條都不另寫：三欄鏡射走 `_sync_taxonomy`（**唯一**那份），可掛專案的
    不變式走 `_enforce_cash_project_link`，鎖月走 `_locked_month_set` ——
    單筆更新有的守衛，批次一個都不能少（批次才是會一次弄壞幾百列的那條路）。

    🔴 有問題就**整批不動**（比照 batch-project）：跳過壞列會讓人以為全部都成功了，
    而畫面上看不出哪幾列沒改到 —— 補歷史時這種靜默漏網最難查。
    """
    check_logged_in(request)           # 先擋匿名（不給 401/404 當存在性預言機）
    _require_db()
    factory = await _get_factory()
    body = await request.json()
    ids = body.get("entry_ids") or []
    # 兩種挑法，因為兩本帳的分類長得不一樣：私帳有分類樹（送節點 id），
    # 母公司是平的一層（送 category 字串 —— 那本沒有樹，種子只種 mine）。
    # 兩邊都只是同一份 `_sync_taxonomy` 的兩個入口，不是兩套規則。
    nid = body.get("taxonomy_node_id")
    cat = body.get("category")
    if not ids:
        raise HTTPException(status_code=400, detail="請提供 entry_ids")
    if nid is None and cat is None:
        raise HTTPException(status_code=400,
                            detail="請提供 taxonomy_node_id 或 category")
    nid = (nid or "").strip() if nid is not None else None
    async with factory() as session:
        rows = (await session.execute(
            select(CrmCashEntry).where(CrmCashEntry.id.in_(ids)))).scalars().all()
        # 拆項父列不收批次分類（分類的正本在拆項）—— 比照本端點「有問題就整批
        # 不動」的鐵則，混進一列就整批擋下並點名，不靜默跳過
        from routers.crm.cash_splits import entry_has_splits_subq as _ehs
        _split_parents = set((await session.execute(
            select(CrmCashEntry.id).where(
                CrmCashEntry.id.in_([r.id for r in rows]),
                CrmCashEntry.id.in_(_ehs())))).scalars())
        if _split_parents:
            _names = [f"{(r.summary or r.id)[:20]}" for r in rows
                      if r.id in _split_parents][:5]
            raise HTTPException(
                status_code=409,
                detail=f"{len(_split_parents)} 列已拆項，分類要在「拆內容」裡改："
                       f"{'、'.join(_names)}")
        if not rows:
            raise HTTPException(status_code=404, detail="找不到這些收支紀錄")
        _mine_or_admin_write_rows(request, rows)
        ents = {(e.entity or "parent") for e in rows}
        # 節點只屬於一本帳的樹 —— 混選必然有一半找不到節點。與其讓它報
        # 「找不到這個分類節點」（看起來像樹壞了），不如說清楚是選錯了。
        if len(ents) > 1:
            raise HTTPException(status_code=400,
                                detail="選取的列橫跨兩本帳，請分開處理")
        ent = ents.pop()
        locked = await _locked_month_set(session, entity=ent)
        violations = [f"{e.summary or e.id}（{month_of(e.entry_date)}）"
                      for e in rows if month_of(e.entry_date) in locked]
        if violations:
            _raise_locked_batch(violations)
        # id→路徑表算一次給整批用（_sync_taxonomy 的 paths 參數就為了這個）
        from core.cash_tree import path_map
        paths = await path_map(session, ent)
        now = _now()
        for e in rows:
            if nid is not None:
                await _sync_taxonomy(session, e, {"taxonomy_node_id": nid}, paths)
            else:
                # 平的那本：這條路的語意是「這幾列現在就是這個類別」，所以更深的
                # 兩欄要一起清掉 —— 不清的話「清空分類」會留下一列沒有類別、卻
                # 還顯示著項目與子項目的列（正是這一輪在消滅的孤兒形狀）。
                # 三欄自己賦值、節點交給同一份規則反查（母公司沒有樹 → 查不到
                # 就是 None，正常）。`paths` 一樣要帶：不帶的話 `_sync_taxonomy`
                # 逐列打 `find_node_id`，一批 200 列就是 200 趟必定落空的查詢。
                e.category, e.item, e.sub_item = (cat or ""), "", ""
                await _sync_taxonomy(session, e, {"category": e.category}, paths)
            _enforce_cash_project_link(e)
            e.updated_at = now
        await session.commit()
        # 整批同一個節點 → 鏡射結果也只有一份，回一份給前端就地 patch 那幾列
        e0 = rows[0]
        entry_patch = {
            "category": e0.category or "",
            "book": _tax_split(e0.category)[0],
            "item": (e0.item or "") or _tax_split(e0.category)[1],
            "sub_item": e0.sub_item or "",
            "taxonomy_node_id": e0.taxonomy_node_id or "",
            "taxonomy_path": list(paths.get(e0.taxonomy_node_id) or []),
        }
    return {"status": "ok", "updated": len(rows), "entry": entry_patch}


@router.delete("/cash-entries/{entry_id}")
async def delete_cash_entry(entry_id: str, request: Request):
    check_logged_in(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e = await session.get(CrmCashEntry, entry_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此收支紀錄")
        _mine_or_admin_write(request, e.entity)  # 兩本帳寫入守衛：母公司=Lv3、私帳=mine full
        await _assert_month_open(session, e.entry_date, entity=e.entity or "parent")
        # 對帳工作台反向清理：這筆若被對帳單明細認領，解除認領
        # （否則該列永遠顯示已勾銷、指向不存在的收支）
        from sqlalchemy import delete as _sadelete, update as _saupdate
        from db.models import BankStatementLine
        await session.execute(_saupdate(BankStatementLine).where(
            BankStatementLine.matched_entry_id == entry_id).values(matched_entry_id=None))
        # 🔴 發票分配也要一起刪：連結是 soft FK，DB 不會自動清。留著的話那張發票
        # 的「已收金額」會一直把這筆刪掉的錢算進去 —— 實測刪掉三筆分期收款後，
        # 發票仍顯示已收滿額、尚欠 0，等於帳面上憑空多收了一次。
        touched = (await session.execute(
            select(CrmCashInvoiceLink.invoice_id)
            .where(CrmCashInvoiceLink.cash_entry_id == entry_id))).scalars().all()
        await session.execute(_sadelete(CrmCashInvoiceLink).where(
            CrmCashInvoiceLink.cash_entry_id == entry_id))
        if (e.entity or "parent") == "mine":
            await _sync_mine_project_received(session, e.project_id,
                                              -int(e.deposit or 0))
        # 拆項連刪＋專案已收沖回：teardown 只有 cash_splits 那一份
        # （remove_entry_splits —— 解除拆項與刪列走同一條，不各抄一遍）
        from routers.crm.cash_splits import remove_entry_splits
        from db.models import CrmCashSplitAdvanceLink
        await remove_entry_splits(session, e)
        # 反向：這一列若是**被沖銷的代墊流出**，指向它的結清連結也要清 ——
        # 留著的話那些回款拆項顯示沖到一列不存在的代墊
        await session.execute(_sadelete(CrmCashSplitAdvanceLink).where(
            CrmCashSplitAdvanceLink.advance_entry_id == entry_id))
        await session.delete(e)
        await session.flush()
        # 被這筆收款影響到的發票要重算 —— 舊碼是「無條件打回未收款」，那會把
        # 合併匯款裡另外兩筆收款也收過的發票一起打回去。
        await resettle_invoices(
            session, set(touched) | ({e.invoice_id} if e.invoice_id else set()),
            unmark_if_empty=True)
        await session.commit()
    return {"status": "ok"}


_CASH_COL_MAP = {
    "entry_date": ["日期"], "expense": ["支出"], "claim": ["請款"],
    "deposit": ["存入"], "summary": ["摘要"], "note": ["附註"],
    "category": ["類別"], "item": ["項目"], "sub_item": ["子項目"],
    "payee": ["收款人"], "status": ["狀態"],
    "invoice_number": ["發票號碼"], "project_label": ["專案標籤"],
    "payment_date": ["付款日"], "payment_status": ["付款狀態"],
}
_CASH_INT_FIELDS = {"expense", "claim", "deposit"}


def _map_cash_row(header_map: dict, row: dict) -> dict:
    return map_csv_row(
        _CASH_COL_MAP, header_map, row,
        coerce=lambda f, v: _parse_money(v) if f in _CASH_INT_FIELDS else v)


@router.post("/cash-entries/import_csv")
async def import_cash_csv(request: Request, file: UploadFile = File(...)):
    return await _import_money_csv(
        request, file, map_row=_map_cash_row,
        skip_if=lambda d: not d.get("summary"),
        date_fields=("entry_date", "payment_date"),
        build=lambda data, d: CrmCashEntry(
            id=uuid.uuid4().hex, entry_date=d["entry_date"], entity="parent",
            payment_date=d["payment_date"], created_at=_now(), **data))


# ── Accounts Payable (應付帳款) ─────────────────────────────

@router.get("/payables/summary", dependencies=[Depends(money_dep)])
async def payables_summary(request: Request, month: str = Query(""),
                           status: str = Query(""), entity: str = Query("")):
    """請款彙總，按收款人分組。month=all 或空=全部應付款；month=YYYY-MM=該月。"""
    # 兩本帳：money_dep 之上疊第二層 entity scope（plan §2.4）
    # full：CRM 帳務＝原始帳列，合夥人不可及 —— money_dep 已擋一層，刻意雙保險
    ent = require_entity(request, entity, level="full")
    _require_db()
    factory = await _get_factory()

    filter_all = (not month) or month == "all"

    async with factory() as session:
        query = (
            select(CrmPaymentRequest, CrmStaff.id_number, CrmStaff.bank_name, CrmStaff.bank_account)
            .outerjoin(CrmStaff, CrmStaff.name == CrmPaymentRequest.payee_name)
            .where(or_(CrmPaymentRequest.is_advance == 0, CrmPaymentRequest.is_advance.is_(None)))
            .where(CrmPaymentRequest.entity == ent)
            .order_by(CrmPaymentRequest.request_date)
        )

        if not filter_all:
            parts = month.split("-")
            try:
                year, mon = int(parts[0]), int(parts[1])
            except (IndexError, ValueError):
                raise HTTPException(status_code=400, detail="month 格式無效，請使用 YYYY-MM")
            from calendar import monthrange
            try:
                start = datetime(year, mon, 1, tzinfo=timezone.utc)
            except ValueError:
                raise HTTPException(status_code=400, detail="month 格式無效，請使用 YYYY-MM")
            _, last_day = monthrange(year, mon)
            end = datetime(year, mon, last_day, 23, 59, 59, tzinfo=timezone.utc)
            query = query.where(or_(
                CrmPaymentRequest.planned_month == month,
                CrmPaymentRequest.request_date.between(start, end),
            ))

        if status == "已付款":
            query = query.where(CrmPaymentRequest.payment_status == "已付款")
        elif status == "all":
            pass  # 全部狀態，不加篩選
        elif not status or status == "應付款":
            # 預設：應付款（含舊資料的「未付款」）
            query = query.where(CrmPaymentRequest.payment_status.in_(["應付款", "未付款"]))

        rows = (await session.execute(query)).all()

    # 分組聚合是純邏輯，抽在 core/crm_logic.py（有單元測試）
    from core.crm_logic import group_payables
    return {"month": month or "all", **group_payables(rows)}


@router.get("/receivables/summary", dependencies=[Depends(money_dep)])
async def receivables_summary(request: Request, status: str = Query(""),
                              entity: str = Query("")):
    """應收帳款彙總：已開立的**收款**發票按客戶（company_name）分組。
    status=未收款/已收款/空=全部（空＝尚未收到的）。

    🔴 只算收款方向。發票的 payment_type 有 收款／付款 兩種：「付款」是代開發票
    （我們開給對方、錢是我們要付出去的）。原本只用
    `payment_status NOT IN ('已收款','作廢')` 過濾，「已轉撥」不在那個清單裡就被
    當成應收 —— 2026-08-19 匯 394 筆歷史發票後實測：應收 13,554,350 裡有
    10,656,093（79%、183 張）其實是代開的付款發票，把應收虛增成 4.7 倍。
    以前系統裡 0 筆發票，這個缺陷看不出來。
    """
    # 兩本帳：money_dep 之上疊第二層 entity scope（plan §2.4）
    # full：CRM 帳務＝原始帳列，合夥人不可及 —— money_dep 已擋一層，刻意雙保險
    ent = require_entity(request, entity, level="full")
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        query = (
            select(CrmInvoice, CrmProject.name.label("pn"),
                   Client.tax_id.label("c_tax_id"), Client.payment_info, Client.payment_note)
            .outerjoin(CrmProject, CrmProject.id == CrmInvoice.project_id)
            # 🔴 代稱不再全域唯一（CRM/私帳各一筆同名）—— 不指名母公司那筆，
            # 這個 join 會讓同一張發票回兩列
            .outerjoin(Client, _sa_and(Client.short_name == CrmInvoice.company_name,
                                       _cli_not_mine(Client.entity)))
            .where(CrmInvoice.issue_status == "已開立")
            .where(CrmInvoice.entity == ent)
            # 🔴 日期後面一定要有 tiebreaker：一天開 5-10 張發票很常見，只用
            # invoice_date 排序時，任一列被 UPDATE 後 Postgres 回傳的相對順序就可能改變
            # —— 畫面上是「改了一筆，同日期的列整組跳動」（2026-08-19 實測：連點三次
            # 種類切換鈕，每次點到的是不同筆）。
            .order_by(CrmInvoice.invoice_date.desc(), CrmInvoice.created_at.desc(), CrmInvoice.id)
        )
        # 收款方向才是應收（NULL 視為收款 —— 與 _to_invoice_dict 的 `or "收款"` 一致；
        # 「付款」的代開發票與「作廢」都排除）
        query = query.where(or_(CrmInvoice.payment_type == "收款",
                                CrmInvoice.payment_type.is_(None)))
        if status:
            query = query.where(CrmInvoice.payment_status == status)
        else:
            query = query.where(
                CrmInvoice.payment_status.notin_([*INVOICE_COLLECTED, "作廢"]))
        rows = (await session.execute(query)).all()

    # 分組聚合是純邏輯，抽在 core/crm_logic.py（有單元測試）
    from core.crm_logic import group_receivables
    return group_receivables(rows, _now())



# ── 收款 ↔ 發票 多對多分配（合併匯款 / 分期收款）────────────────
#
# owner 2026-08-19：「有些時候客戶會合併匯款，我希望款項的發票如果需要的話可以
# 讓我掛一張以上的發票，並且做金額的檢查」。
#
# 金額檢查是**提示不是閘門**：實收常比發票少幾十元（收款方扣匯費）、也常見分期
# 只收一半。硬擋會逼人亂填，所以後端算出差額與判讀，由人決定要不要理。

async def _entry_for_alloc(session, entry_id: str, request, *, month_guard=False):
    """取這筆收支 ＋ 驗帳本（＋ 需要時擋已鎖月）。四個分配端點共用這段前言。

    level="full"：CRM 帳務＝原始帳列，合夥人不可及 —— money_dep 已擋一層，
    這裡刻意雙保險（plan §2.4）。
    """
    e = await session.get(CrmCashEntry, entry_id)
    if not e:
        raise HTTPException(status_code=404, detail="收支明細不存在")
    ent = require_entity(request, e.entity or "parent", level="full")
    if month_guard:
        await _assert_month_open(session, e.entry_date, entity=ent)
    return e, ent


@router.get("/cash-entries/{entry_id}/invoices", dependencies=[Depends(money_dep)])
async def list_cash_entry_invoices(entry_id: str, request: Request):
    """這筆收款掛了哪些發票、各分配多少、與實收差多少。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e, _ent = await _entry_for_alloc(session, entry_id, request)
        items, allocated = await _load_allocs(session, e)
    return {"items": items, "check": _alloc_verdict(e, allocated)}


@router.get("/cash-entries/{entry_id}/payments", dependencies=[Depends(money_dep)])
async def get_cash_entry_payments(entry_id: str, request: Request):
    """這筆匯款掛了哪些請款單、各分配多少、與實付差多少。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e, _ent = await _entry_for_alloc(session, entry_id, request)
        items, allocated = await _load_payment_allocs(session, e)
    return {"items": items, "check": _payment_verdict(e, allocated)}


async def _write_allocs(request, entry_id: str, kind: str, items, fee=None):
    """整組取代某筆收支的分配。兩側**同一條**寫入流程。

    驗帳本 → 擋鎖月 → 驗每一列 → 整組取代（連帶同步主要單據欄與相關單據的
    收付狀態）→ 有匯費就認列 → 回最新的明細與判讀。

    🔴 兩側本來各寫一遍這十二行，於是「存檔後回什麼」「commit 之前還做了什麼」
    要對兩處看。差別只剩匯費的**形狀**：收款側逐張（存進連結表，面板重開畫得回來），
    付款側整筆一個（跨行手續費對一次匯出收一次）。
    """
    k = _ALLOC_KINDS[kind]
    # 🔴 寫入守衛＝列的帳本說了算（母公司=Lv3、私帳=mine full）——原本開頭
    # _check_auth 只認 Lv3，帳本主人在生產存「請款單分配」直接 權限不足 ×3
    # （2026-08-26 實測；上次開 mine 寫入權時漏了分配這條路，它不走
    # _entity_for_write 咽喉）。先 check_logged_in 再載列，匿名不得拿 404 探 id。
    check_logged_in(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e, ent = await _entry_for_alloc(session, entry_id, request,
                                        month_guard=True)
        _mine_or_admin_write(request, e.entity)
        rows = await resolve_allocs(
            session, [(getattr(it, k["id_field"]), it.amount) for it in (items or [])],
            ent, kind)
        # 逐張匯費只有收款側有（見 _ALLOC_KINDS 的 perItemFee 那段說明）——
        # 存下來，關聯面板重開時才畫得回來
        if k.get("per_item_fee"):
            fees = {(e.id, getattr(it, k["id_field"])): int(getattr(it, "fee", 0) or 0)
                    for it in (items or [])}
            await k["replace"](session, e, rows, fees=fees)
        else:
            await k["replace"](session, e, rows)
        if fee is not None:
            # 不變量與冪等性在 core.finance_logic 的兩支 recognize_*_fee ——
            # 那是錢的規則，要有自己的單元測試，不該住在端點裡。
            # 🔴 依 kind 取，不要寫死付款側：收款側加上 fee 欄位之後（對帳單匯入
            #    那條路先做了），這裡若還只認 recognize_bank_fee，關聯面板重存一次
            #    就會把 deposit 的補回值抹掉 —— 帳戶淨流悄悄變回含匯費的數字，
            #    那一列從此在對帳工作台配不上，而且畫面上完全看不出來。
            try:
                k["fee"](e, fee)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
        await session.commit()
        items_out, allocated = await k["load"](session, e)
    return {"ok": True, "items": items_out, "check": k["verdict"](e, allocated)}


@router.put("/cash-entries/{entry_id}/payments")
async def set_cash_entry_payments(entry_id: str, req: CashPaymentLinksPayload,
                                  request: Request):
    """整組取代這筆匯款的請款單分配（owner 2026-08-22）。

    金額對不對得上實付**不擋**（跟發票那側同一個道理：出納合併匯款、跨行
    手續費 10~15 元，硬擋會逼人亂填）。擋的是一定錯的：單不存在／不同帳本／
    重複／金額 ≤ 0。

    副作用（刻意）：
      · `payment_request_id` 同步成金額最大的那張 —— classify_cash_entry 的
        硬連結優先序讀它。
      · 相關請款單的付款狀態重算（付滿才標已付款，分次支付標應付款，
        連結被拿光退回未付款並清掉付款日）。
      · `fee` 有給就寫進 bank_fee —— 那 10 元從此是管理費用，不是對不起來的差額。
    """
    return await _write_allocs(request, entry_id, "payment", req.items,
                               fee=req.fee)


@router.put("/cash-entries/{entry_id}/invoices")
async def set_cash_entry_invoices(entry_id: str, req: CashInvoiceLinksPayload,
                                  request: Request):
    """整組取代這筆收款的發票分配。

    金額檢查不擋（見上），但這幾件會擋 —— 它們是**一定錯**而不是可能錯：
      發票不存在／不同帳本；同一張發票重複出現；分配金額 ≤ 0。

    副作用（刻意）：`invoice_id` / `invoice_number` / `has_invoice` 同步成金額最大
    的那張發票 —— 列表與舊查詢都讀這幾欄，不同步的話畫面會跟明細對不起來。

    🔴 `fee` 從**各列加總**來，不是 payload 上的單一欄：收款側的匯費是逐張發票
    被扣的（客戶匯三張的錢，可能只有其中一張被扣了 30），前端也是那樣填的。
    加總後才是這筆收款要補回 deposit 的金額。
    """
    fee = sum(int(it.fee or 0) for it in (req.items or []))
    return await _write_allocs(request, entry_id, "invoice", req.items,
                               fee=fee if req.items else None)
