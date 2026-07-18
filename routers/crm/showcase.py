"""routers/crm/showcase.py — 專案 Showcase / 結案上架：封面 / 圖庫 /
製程 / credits / 發布 / Token 外部編輯 / AI 描述互動撰寫 / AI 參考資料 /
對外 Site API。

自 routers/api_crm.py 原樣搬移（純搬移，行為不變）。
delete_showcase_gallery / delete_showcase_process 內以 __file__ 推 uploads
路徑的運算多包一層 os.path.dirname（檔案移深一層，維持原專案根目錄基準）。
"""
from __future__ import annotations

import os
import uuid

from fastapi import HTTPException, Request, UploadFile, File, Query

from core.crm_logic import (SHOWCASE_EDIT_EXPIRES_DAYS, SHOWCASE_EDIT_SCOPE,
                            WORK_WIRE_FIELD_MAP, is_main_work,
                            showcase_edit_url, work_completeness, work_url_slug)
from core.schemas import ShowcasePayload, StaffQuickAddPayload

from ._shared import (router, _check_auth, _check_website_auth, _require_db,
                      _get_factory, _mark_dirty_safe, _now, _UPLOAD_BASE,
                      _ALLOWED_IMG_EXT, _verify_token_generic,
                      _to_staff_public_dict, STAFF_CREATED_VIA_SHOWCASE_EDIT)

try:
    from ._shared import (select, or_, delete,
                          Client, CrmProject, CrmStaff, CrmProjectStaff,
                          CrmProjectShowcase)
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同原檔 try/except
    pass

# ── Project Showcase / Delivery Endpoints ──────────────────

def _showcase_dir(project_id: str) -> str:
    d = os.path.join(_UPLOAD_BASE, "projects", project_id, "showcase")
    os.makedirs(d, exist_ok=True)
    return d


# ── AI 參考資料上傳（文件抽文字 → 餵 AI 寫描述 / SEO）──────────
_ALLOWED_REF_EXT = {".txt", ".md", ".csv", ".pdf", ".docx"}
_REF_MAX_BYTES = 10 * 1024 * 1024
_REF_MAX_CHARS_PER_FILE = 8000


def _extract_doc_text(content: bytes, filename: str) -> str:
    import io, os
    ext = os.path.splitext(filename or "")[1].lower()
    try:
        if ext in (".txt", ".md", ".csv"):
            return content.decode("utf-8", errors="replace")
        if ext == ".pdf":
            from pypdf import PdfReader
            return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(content)).pages)
        if ext == ".docx":
            import docx
            return "\n".join(p.text for p in docx.Document(io.BytesIO(content)).paragraphs)
    except Exception:
        return ""
    return ""


def _ai_refs_dir(project_id: str) -> str:
    d = os.path.join(_showcase_dir(project_id), "ai_refs")
    os.makedirs(d, exist_ok=True)
    return d


def _ref_files_minimal(files) -> list:
    """回傳給前端的精簡清單（只 name + chars，不含 text 大塊 / 磁碟檔名）。"""
    return [
        {"name": f.get("name", ""), "chars": f.get("chars", 0)}
        for f in (files or []) if isinstance(f, dict)
    ]


def _save_image_as_webp(content: bytes, dest_dir: str, base_name: str) -> str:
    """寫圖檔到 dest_dir/<base_name>.webp。原始 JPG/PNG/HEIC 也轉成 WebP。

    回傳寫好的檔案路徑（含副檔名）。Pillow 的 WebP encoder 預設 quality=80，
    對作品集封面 / 圖庫足夠。WebP 通常比 JPG 小 30%，比 PNG 小 50-70%。

    fallback：Pillow 不可用 / 開檔失敗 → 直接寫原始 bytes 到原副檔名。
    """
    webp_path = os.path.join(dest_dir, base_name + ".webp")
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(content))
        # GIF / animated WebP 邏輯先不處理；第一格即可
        if img.mode in ("RGBA", "LA"):
            img = img.convert("RGBA")
        else:
            img = img.convert("RGB")
        img.save(webp_path, "WEBP", quality=82, method=6)
        return webp_path
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("[upload] WebP convert failed (%s) — fallback raw", e)
        # fallback to raw bytes (won't have .webp extension to be honest about format)
        raw_path = webp_path.replace(".webp", ".bin")
        with open(raw_path, "wb") as f:
            f.write(content)
        return raw_path


def _to_showcase_dict(s) -> dict:
    return {
        "id": s.id,
        "cover_url": s.cover_url or "",
        "description": s.description or "",
        "video_url": s.video_url or "",
        "extra_videos": s.extra_videos or [],
        "gallery": s.gallery or [],
        "process_mode": s.process_mode or "gallery",
        "process_items": s.process_items or [],
        "credits": s.credits or [],
        "credits_mode": s.credits_mode or "block",
        "credits_text": s.credits_text or "",
        "slug": s.slug or "",
        "published": bool(s.published),
        "published_at": s.published_at.isoformat() if s.published_at else None,
        "edit_token": s.edit_token or "",
        "editable": bool(s.editable) if s.editable is not None else True,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


async def _verify_showcase_edit_token(session, token: str, require_editable: bool = False):
    return await _verify_token_generic(session, token, 'showcase_edit', CrmProjectShowcase, 'editable', require_editable)


# Phase M-W Showcase 雙向支援：
#   - Sync hook：Showcase 儲存時鏡像 5 欄到 crm_projects.public_*
#     (Astro 官網讀 public_*，編輯在 Showcase 端；hook 讓資料一致)
#   - Showcase edit token：CRM 完稿 Tab 與官網管理 Tab iframe 共用同一把鑰匙

# SHOWCASE_EDIT_SCOPE / SHOWCASE_EDIT_EXPIRES_DAYS 正本在 core.crm_logic
# （child work 建立在 project_service 也要 mint，共用一份避免 scope 漂移）


async def _sync_showcase_to_public(session, sc) -> None:
    """把 Showcase 可鏡像欄位同步到 crm_projects.public_*（呼叫者統一 commit）。

    同步範圍（7 欄 + slug detection）：
      sc.description  → public_description
      sc.slug         → public_slug（含 SEO 301：舊 slug 自動 append 到 public_old_slugs）
      sc.video_url    → public_youtube_id（parse YT ID）
      sc.credits      → public_credits（block-structured snapshot）
      sc.cover_url    → public_cover_url（OG image fallback 用）
      sc.published    → public + public_published_at（first publish auto-assign number）

    不同步 title/client/year — 這些讓 Astro read-time 從 project.name / client
    動態 join，避免雙寫需要刷新的複雜度。

    credits 是 block 結構（list of dict）— 直接整份覆寫到 public_credits 作快照，
    這樣將來職位庫改名（rename role_zh）時歷史作品不會被動跟著改（snapshot 隔離）。
    """
    project = await session.get(CrmProject, sc.id)
    if not project:
        return
    # SEO 301：先偵測 slug 變動再覆寫 — 跟 update_project_public 同邏輯
    new_slug = sc.slug or None
    try:
        from services.website.project_service import append_old_slug_if_changed
        append_old_slug_if_changed(project, new_slug)
    except ImportError:
        pass
    project.public_description = sc.description or None
    project.public_slug = new_slug
    # Block-structured credits → public_credits（snapshot；rename role 不影響歷史）
    project.public_credits = sc.credits or []
    # Credits 雙模式 mirror — admin 在 showcase-edit 切「結構化」/「純文字」模式
    project.public_credits_mode = sc.credits_mode or "block"
    project.public_credits_text = sc.credits_text
    # Cover image → public_cover_url（OG image fallback 用）
    project.public_cover_url = sc.cover_url or None
    # sc.youtube_id 是寫入時已 parse 好的快取（_apply_showcase_fields）— 優先用；
    # 沒有才 parse（lazy import：services/website/ 不在 OTA AGENT_DIRS，頂層 import
    # 會炸 agent 開機。Showcase publish 只在 master 跑，該檔一定在）。
    # 鏡射一律覆寫（含 None）— 換成 FB/Vimeo 連結時要清掉舊 YT ID，否則對外站
    # 繼續播舊影片。清成 None 安全：skeleton 守衛（admin_works.py）另有
    # _work_has_content 兜底、work_completeness 數的是 raw video_url。
    yt_id = sc.youtube_id
    if not yt_id:
        from services.website.notion_service import _extract_youtube_id
        yt_id = _extract_youtube_id(sc.video_url or "")
    project.public_youtube_id = yt_id or None
    # 自動補 published_at — 從未發布變為發布時 set now（避免 admin/PM toggle 公開時忘了帶日期）
    if sc.published and not sc.published_at:
        sc.published_at = _now()
    project.public = bool(sc.published)
    project.public_published_at = sc.published_at if sc.published else None
    # 第一次 publish → auto-assign 連續編號（unpublish 不收回，避免 republish 改號）。
    # 1:N 後編號正本在 sc.number — 兩邊交叉補齊，主作品永遠同號（避免各叫一次
    # counter 拿到不同號）。
    if project.public and project.public_number is None:
        if sc.number is not None:
            project.public_number = sc.number
        else:
            from services.website.project_service import _next_public_number
            project.public_number = await _next_public_number(session)
    if sc.published and sc.number is None:
        sc.number = project.public_number


def _is_valid_edit_token(token) -> bool:
    """存庫的 edit_token 是否仍可用。jwt_secret 輪替後舊 token 驗不過 —
    2026-07-10 輪替實案：生產 226/230 個庫存 token 失效、編輯器整片 404。
    重用前必驗，驗不過就重發（自癒，之後再輪替也不會壞）。"""
    if not token:
        return False
    try:
        from core.auth import verify_token
        payload = verify_token(token)
        return bool(payload) and payload.get("scope") == SHOWCASE_EDIT_SCOPE
    except Exception:
        return False


async def _mint_showcase_edit_token(
    session, project_id: str, *, reuse_existing: bool = False,
) -> tuple[str, CrmProjectShowcase]:
    """取得/產生 Showcase edit token，upsert CrmProjectShowcase row。

    Args:
        reuse_existing: True 時如果 sc.edit_token 仍有效就重用（冪等），不動 updated_at；
                        無效（如 secret 輪替）或 False 時產新 token 覆寫
                        （CRM 完稿 Tab「重新產生分享連結」的語義）。

    回傳 (token, showcase_row)。不 commit，caller 統一 commit。
    新建 showcase 時才設 editable=True；既有 row 保留 CRM 管理員手動設的 editable 值。
    """
    from core.auth import create_token
    sc = await session.get(CrmProjectShowcase, project_id)
    if sc and reuse_existing and _is_valid_edit_token(sc.edit_token):
        return sc.edit_token, sc
    token = create_token({"sub": project_id, "scope": SHOWCASE_EDIT_SCOPE},
                         expires_days=SHOWCASE_EDIT_EXPIRES_DAYS)
    if not sc:
        sc = CrmProjectShowcase(id=project_id, project_id=project_id,
                                edit_token=token, editable=True)
        session.add(sc)
    else:
        if not sc.project_id:
            sc.project_id = sc.id
        sc.edit_token = token
        sc.updated_at = _now()
    return token, sc


@router.get("/projects/{project_id}/showcase")
async def get_project_showcase(project_id: str, request: Request):
    """取得或建立專案 Showcase（1:1 關聯）。"""
    _check_website_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc:
            sc = CrmProjectShowcase(id=project_id, project_id=project_id)
            session.add(sc)
            await session.commit()
            await session.refresh(sc)
        # secret 輪替後的殭屍 token 清掉 — 前端拿到空 token 會走重發流程（自癒）
        if sc.edit_token and not _is_valid_edit_token(sc.edit_token):
            sc.edit_token = None
            await session.commit()
        data = _to_showcase_dict(sc)
        # 對外公開連結 — 給 delivery Tab「預覽」按鈕直接連到 originsun-studio.com/works/{slug}
        public_url = ""
        if sc.published:
            site, url_path = await _public_work_url(session, sc)
            if url_path:
                public_url = f"{site}{url_path}"
        data["public_url"] = public_url
        return data


# Showcase 可從 admin (PUT /projects/{id}/showcase) 跟 token (PUT /public/showcase-edit/{token})
# 兩條路徑改 — 共用此 helper 統一欄位 setattr + slug 正規化（None 取代空字串）。
_SHOWCASE_UPDATABLE_FIELDS = (
    "description", "video_url", "credits", "credits_mode", "credits_text",
    "process_mode", "process_items", "slug",
    "gallery",  # 成品展示圖 — 刪除/排序/改 caption 走 PUT 送整個 array（上傳有專用 endpoint）
    "published",  # showcase-edit toggle 公開 — _sync 會鏡射到 project.public
    "extra_videos",  # 附加影片 [{url, caption}]（一頁多影片；只從 token 編輯器送，
                     # 刻意不進 ShowcasePayload — admin PUT 的 model_dump 會帶預設值洗掉它）
)


def _apply_showcase_fields(sc, payload: dict) -> None:
    """Set sc 可更新欄位 — caller 統一 commit + _sync_showcase_to_public。

    payload 可以是 Pydantic model_dump (admin path) 或 raw body dict (token path)。
    缺欄位的不動（partial update 語意）。

    slug 特殊：空字串 → None。原因是 sc.slug 有 unique index，空字串視為
    「使用 number 作 URL」，必須存 NULL 避免兩個作品都用空字串撞 unique。
    其他欄位（description / video_url）空字串是合法值，不轉 None。

    video_url 變動時一律重算 sc.youtube_id（讀路徑的快取欄）— 非 YouTube
    連結（FB/Vimeo/空值）要清成 None，與 _sync_showcase_to_public 對
    public_youtube_id 的鏡射行為一致，避免對外站播到舊影片。
    """
    # SEO 301（順序沿舊版「replace 再 append」）：先套 payload 的 old_slugs 全量替換
    # （若有），再偵測 slug 變動把舊 slug append 進 sc.old_slugs — append 不被 replace 洗掉。
    # Lazy import — services/website/ 不在 OTA AGENT_DIRS（同 _sync 的理由）
    from services.website.project_service import (
        append_old_slug_if_changed_work, normalize_old_slugs_input,
    )
    if "public_old_slugs" in payload and isinstance(payload["public_old_slugs"], list):
        sc.old_slugs = normalize_old_slugs_input(payload["public_old_slugs"])
    if "slug" in payload:
        append_old_slug_if_changed_work(sc, payload["slug"] or None)
    for field in _SHOWCASE_UPDATABLE_FIELDS:
        if field in payload:
            value = payload[field]
            if field == "slug":
                value = value or None
            elif field == "extra_videos":
                # 正規化：只收 {url, caption}、去空 url 項
                value = [
                    {"url": (v.get("url") or "").strip(),
                     "caption": (v.get("caption") or "").strip()}
                    for v in (value or [])
                    if isinstance(v, dict) and (v.get("url") or "").strip()
                ]
            setattr(sc, field, value)
    if "video_url" in payload:
        from services.website.notion_service import _extract_youtube_id
        # 一律重算（None 也要寫）— 換成 FB/Vimeo 連結時要清掉舊 YT ID，
        # 否則對外站繼續播舊影片（stale youtube_id 快取）。
        yt_id = _extract_youtube_id(sc.video_url or "")
        sc.youtube_id = yt_id
    sc.updated_at = _now()


# 作品身分欄位（1:N 後正本在 sc）— showcase-edit token 路徑允許編的子集，
# 派生自 core.crm_logic.WORK_WIRE_FIELD_MAP 單一正本（wire key 沿用 public_* 命名）。
_WORK_IDENTITY_FIELD_MAP = {k: WORK_WIRE_FIELD_MAP[k] for k in (
    "public_title", "public_year", "public_featured",
    "public_featured_image", "public_noindex",
)}


def _apply_work_identity_fields(sc, project, payload: dict) -> None:
    """寫作品身分欄位（標題 / 年份 / featured / 精選圖 / noindex）— 正本寫 sc.*。

    public_client 例外 — 客戶是專案層資料，寫 project（所有作品共用顯示）。
    主作品的 public_* 鏡射由 caller 統一走 mirror_main_work_to_project。
    """
    for wire_key, field in _WORK_IDENTITY_FIELD_MAP.items():
        if wire_key in payload:
            setattr(sc, field, payload[wire_key])
    if "public_client" in payload and project is not None:
        project.public_client = payload["public_client"]


@router.put("/projects/{project_id}/showcase")
async def update_project_showcase(project_id: str, req: ShowcasePayload, request: Request):
    """更新專案 Showcase 欄位。"""
    _check_website_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc:
            sc = CrmProjectShowcase(id=project_id, project_id=project_id)
            session.add(sc)
        _apply_showcase_fields(sc, req.model_dump())
        await _sync_showcase_to_public(session, sc)
        await session.commit()
        await session.refresh(sc)
    await _mark_dirty_safe("showcase PUT")
    return _to_showcase_dict(sc)


@router.post("/projects/{project_id}/showcase/cover")
async def upload_showcase_cover(project_id: str, request: Request, file: UploadFile = File(...)):
    """上傳 Showcase 封面圖。"""
    _check_auth(request)
    _require_db()
    ext = (os.path.splitext(file.filename or "cover.jpg")[1] or ".jpg").lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")
    sdir = _showcase_dir(project_id)
    # Remove old covers (任何副檔名)
    for f in os.listdir(sdir):
        if f.startswith("cover."):
            os.remove(os.path.join(sdir, f))
    content = await file.read()
    saved = _save_image_as_webp(content, sdir, "cover")
    fname = os.path.basename(saved)
    url = f"/uploads/projects/{project_id}/showcase/{fname}"
    factory = await _get_factory()
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc:
            sc = CrmProjectShowcase(id=project_id, cover_url=url)
            session.add(sc)
        else:
            sc.cover_url = url
            sc.updated_at = _now()
        await session.commit()
    return {"status": "ok", "cover_url": url}


@router.post("/projects/{project_id}/showcase/gallery")
async def upload_showcase_gallery(project_id: str, request: Request,
                                  file: UploadFile = File(...),
                                  caption: str = Query("")):
    """上傳 Showcase 圖庫圖片。"""
    _check_auth(request)
    _require_db()
    ext = (os.path.splitext(file.filename or "img.jpg")[1] or ".jpg").lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")
    sdir = _showcase_dir(project_id)
    content = await file.read()
    saved = _save_image_as_webp(content, sdir, uuid.uuid4().hex)
    fname = os.path.basename(saved)
    url = f"/uploads/projects/{project_id}/showcase/{fname}"
    factory = await _get_factory()
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc:
            sc = CrmProjectShowcase(id=project_id, gallery=[{"url": url, "caption": caption}])
            session.add(sc)
        else:
            gallery = list(sc.gallery or [])
            gallery.append({"url": url, "caption": caption})
            sc.gallery = gallery
            sc.updated_at = _now()
        await session.commit()
        await session.refresh(sc)
    return {"status": "ok", "gallery": sc.gallery or []}


@router.delete("/projects/{project_id}/showcase/gallery/{index}")
async def delete_showcase_gallery(project_id: str, index: int, request: Request):
    """刪除 Showcase 圖庫中指定索引的項目。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc:
            raise HTTPException(status_code=404, detail="尚未建立 Showcase")
        gallery = list(sc.gallery or [])
        if index < 0 or index >= len(gallery):
            raise HTTPException(status_code=400, detail="索引超出範圍")
        removed = gallery.pop(index)
        # Try to delete the file
        file_url = removed.get("url", "")
        if file_url.startswith("/uploads/"):
            fpath = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                 file_url.lstrip("/").replace("/", os.sep))
            if os.path.isfile(fpath):
                os.remove(fpath)
        sc.gallery = gallery
        sc.updated_at = _now()
        await session.commit()
    return {"status": "ok", "gallery": gallery}


@router.post("/projects/{project_id}/showcase/process")
async def upload_showcase_process(project_id: str, request: Request,
                                  file: UploadFile = File(...),
                                  caption: str = Query(""),
                                  phase: str = Query(""),
                                  item_type: str = Query("image")):
    """上傳 Showcase 製程項目圖片。"""
    _check_auth(request)
    _require_db()
    ext = (os.path.splitext(file.filename or "img.jpg")[1] or ".jpg").lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")
    sdir = _showcase_dir(project_id)
    content = await file.read()
    saved = _save_image_as_webp(content, sdir, f"process_{uuid.uuid4().hex}")
    fname = os.path.basename(saved)
    url = f"/uploads/projects/{project_id}/showcase/{fname}"
    item = {"type": item_type, "url": url, "caption": caption, "phase": phase, "video_url": ""}
    factory = await _get_factory()
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc:
            sc = CrmProjectShowcase(id=project_id, project_id=project_id, process_items=[item])
            session.add(sc)
        else:
            items = list(sc.process_items or [])
            items.append(item)
            sc.process_items = items
            sc.updated_at = _now()
        await session.commit()
        await session.refresh(sc)
    return {"status": "ok", "process_items": sc.process_items or []}


@router.delete("/projects/{project_id}/showcase/process/{index}")
async def delete_showcase_process(project_id: str, index: int, request: Request):
    """刪除 Showcase 製程項目中指定索引的項目。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc:
            raise HTTPException(status_code=404, detail="尚未建立 Showcase")
        items = list(sc.process_items or [])
        if index < 0 or index >= len(items):
            raise HTTPException(status_code=400, detail="索引超出範圍")
        removed = items.pop(index)
        file_url = removed.get("url", "")
        if file_url.startswith("/uploads/"):
            fpath = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                 file_url.lstrip("/").replace("/", os.sep))
            if os.path.isfile(fpath):
                os.remove(fpath)
        sc.process_items = items
        sc.updated_at = _now()
        await session.commit()
    return {"status": "ok", "process_items": items}


async def _toggle_publish(session, sc) -> None:
    """發布/下架切換共用核心 — project-scoped 與 work-scoped 端點同一套：
    flip → published_at → 首發配號（子作品沒有對應 project、_sync 是 no-op，
    必須在這裡配）→ 主作品鏡射。caller 統一 commit。"""
    sc.published = not sc.published
    if sc.published:
        sc.published_at = _now()
    sc.updated_at = _now()
    from services.website.project_service import ensure_work_number
    await ensure_work_number(session, sc)
    await _sync_showcase_to_public(session, sc)


@router.post("/projects/{project_id}/showcase/publish")
async def toggle_showcase_publish(project_id: str, request: Request):
    """切換 Showcase 發布狀態。"""
    _check_website_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc:
            raise HTTPException(status_code=404, detail="尚未建立 Showcase")
        await _toggle_publish(session, sc)
        await session.commit()
        await session.refresh(sc)
    await _mark_dirty_safe("showcase publish")
    return _to_showcase_dict(sc)


@router.get("/projects/{project_id}/showcase/credits/auto")
async def auto_showcase_credits(project_id: str, request: Request):
    """自動從專案派工生成 credits 列表。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmProjectStaff).where(CrmProjectStaff.project_id == project_id)
        )).scalars().all()
        credits = []
        for ps in rows:
            staff = await session.get(CrmStaff, ps.staff_id)
            if not staff:
                continue
            resume_url = ""
            if staff.resume_visible:
                resume_url = f"/resume.html?id={staff.id}"
            credits.append({
                "name": staff.name,
                "role": ps.role_in_project or staff.role or "",
                "staff_id": staff.id,
                "resume_url": resume_url,
            })
    return {"credits": credits}


@router.post("/projects/{project_id}/showcase/generate-edit-token")
async def generate_showcase_edit_token(project_id: str, request: Request):
    """產生 Showcase 外部編輯永久連結 Token（永遠產新 token，為分享連結「重發」語義）。"""
    _check_website_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        token, _sc = await _mint_showcase_edit_token(session, project_id, reuse_existing=False)
        await session.commit()
    return {"status": "ok", "token": token, "url": showcase_edit_url(token)}


# ── Showcase Public Endpoints ──────────────────────────────
# 舊版 /showcase.html 公開預覽頁已移除（對外網站 originsun-studio.com/works/{slug} 取代）。
# 編輯入口仍在 /public/showcase-edit/{token}（透過 token 授權給 PM/客戶協作）。


async def _public_work_url(session, sc) -> tuple:
    """(site_url, url_path) — 對外公開網址組裝（GET 資料與 /status 端點共用）。

    site_url 從 website_settings 讀，沒設 fallback 到 production 網域；
    URL slug 優先序：admin 自訂 slug > number（1, 2, 3）> 無法產 URL（path 空字串）。
    """
    try:
        from services.website import settings_service
        _all_settings = await settings_service.get_all_settings(session)
        site_url = (_all_settings.get("seo.site_url") or "").strip()
    except Exception:
        site_url = ""
    site_url = (site_url or "https://originsun-studio.com").rstrip("/")
    url_slug = work_url_slug(sc)
    return site_url, (f"/works/{url_slug}" if url_slug else "")


async def _build_showcase_edit_data(session, sc) -> dict:
    """組 showcase-edit「一頁全包」資料 — GET /public/showcase-edit/{token} 與
    carry_in 共用同一個 serializer，確保回傳 shape 完全一致。呼叫者已完成 token
    驗證並提供對應 sc（CrmProjectShowcase）。"""
    from db.models_website import WebsiteCategory, WebsiteProjectCategory
    proj = await session.get(CrmProject, sc.project_id or sc.id)
    project_name = proj.name if proj else ""
    data = _to_showcase_dict(sc)
    # Strip financial / internal fields
    data.pop("edit_token", None)
    data["project_name"] = project_name
    data["project_id"] = sc.project_id or sc.id
    # AI 參考資料（製作人上傳文件抽文字 + 補充說明）— 前端渲染清單 + notes
    data["ai_reference_files"] = list(sc.ai_reference_files or [])
    data["ai_reference_notes"] = sc.ai_reference_notes or ""
    # 作品基本資料 — 給 showcase-edit 編輯器一頁全包（wire key 沿用 public_* 命名，
    # 正本已在 sc；client 是專案層資料照舊讀 project）
    data["public_title"] = sc.title or ""
    data["public_client"] = (proj.public_client if proj else "") or ""
    data["public_year"] = sc.year
    data["public_featured"] = bool(sc.featured)
    data["public_featured_image"] = sc.featured_image or ""
    data["public_noindex"] = bool(sc.noindex)
    data["public_published"] = bool(sc.published)
    data["public_old_slugs"] = list(sc.old_slugs or [])
    # 對外公開 URL — 給 showcase-edit「網站連結」直接組公開網址用
    _site_url, _url_path = await _public_work_url(session, sc)
    data["public_site_url"] = _site_url
    data["public_url_path"] = _url_path
    # 完成度 + 上架驗證 — 編輯器檢查清單 / 發布時間線用（正本 core/crm_logic）
    data["completeness"] = work_completeness(
        video_url=sc.video_url, youtube_id=sc.youtube_id, extra_videos=sc.extra_videos,
        gallery=sc.gallery, cover_url=sc.cover_url, featured_image=sc.featured_image,
        description=sc.description, credits=sc.credits, credits_text=sc.credits_text)
    data["verified_at"] = sc.verified_at.isoformat() if sc.verified_at else None

    # 對外 categories 候選清單（含 kind）+ 此作品已勾選的 ID
    cat_rows = (await session.execute(
        select(WebsiteCategory)
        .where(WebsiteCategory.visible.is_(True))
        .order_by(WebsiteCategory.sort_order, WebsiteCategory.id)
    )).scalars().all()
    data["categories_available"] = [
        {"id": c.id, "slug": c.slug, "name_zh": c.name_zh,
         "name_en": c.name_en, "kind": c.kind or "category"}
        for c in cat_rows
    ]
    sel_rows = (await session.execute(
        select(WebsiteProjectCategory.category_id)
        .where(WebsiteProjectCategory.project_id == sc.id)
    )).scalars().all()
    data["selected_category_ids"] = list(sel_rows)

    # 作品系列候選清單（含 hidden — owner 可先建好系列再慢慢掛）+ 此作品目前歸屬
    from db.models_website import WebsiteSeries
    ser_rows = (await session.execute(
        select(WebsiteSeries)
        .order_by(WebsiteSeries.sort_order, WebsiteSeries.id))).scalars().all()
    data["series_available"] = [
        {"id": s.id, "slug": s.slug, "title_zh": s.title_zh,
         "visible": bool(s.visible)}
        for s in ser_rows
    ]
    data["series_id"] = sc.series_id
    data["series_order"] = sc.series_order or 0

    # 演職員職位庫 + 模板候選清單（給 showcase-edit 編輯器用）。
    # 委派 service 層維持單一 hydrate 真相源；list_roles 多回的 sort_order /
    # visible / usage_count 前端忽略無妨。
    from services.website import credit_service, seo_service
    data["credit_roles_available"] = await credit_service.list_roles(
        session, visible_only=True
    )
    data["credit_templates_available"] = await credit_service.list_templates(session)
    # 作品級 SEO（pipeline 寫入 — PM 可在 showcase-edit 看 + 改覆寫欄位 + 觸發 AI 重生）
    data["seo"] = await seo_service.get_project_seo(session, sc.id)
    return data


async def _build_credit_blocks_from_staff(session, project_id: str) -> list:
    """從 crm_project_staff JOIN crm_staff 生 credits BLOCK 結構
    （與 core/schemas_website.CreditBlock / TS ICreditBlock 對齊）。

    以 role_in_project 分組，每個不同職務 → 一個 block；同職務多人合併進同一
    block 的 entries。role_id 由 name_zh==role_in_project 對到 website_credit_roles，
    對不到則 None。保持穩定順序（依派工出現順序，各職務首次出現定序）。
    """
    from db.models_website import WebsiteCreditRole
    rows = (await session.execute(
        select(CrmProjectStaff).where(CrmProjectStaff.project_id == project_id)
    )).scalars().all()
    role_rows = (await session.execute(select(WebsiteCreditRole))).scalars().all()
    role_map = {r.name_zh: r for r in role_rows}

    blocks: list = []
    block_index: dict = {}  # role_key → blocks 索引（同職務合併 entries + 穩定順序）
    for ps in rows:
        staff = await session.get(CrmStaff, ps.staff_id)
        if not staff:
            continue
        role_key = ps.role_in_project or ""
        # CrmStaff 目前無 resume_url 欄位 — 沿用既有 auto credits 慣例：
        # resume_visible 才給 /resume.html?id=；未來若加 resume_url 欄位自動優先。
        resume_url = ""
        if getattr(staff, "resume_visible", False):
            resume_url = getattr(staff, "resume_url", None) or f"/resume.html?id={staff.id}"
        entry = {"duty": "", "name": staff.name, "resume_url": resume_url}
        if role_key in block_index:
            blocks[block_index[role_key]]["entries"].append(entry)
        else:
            matched = role_map.get(role_key)
            blocks.append({
                "role_id": matched.id if matched else None,
                "name_zh": role_key or "團隊",
                "name_en": (matched.name_en if matched else "") or "",
                "entries": [entry],
            })
            block_index[role_key] = len(blocks) - 1
    return blocks


@router.get("/public/showcase-edit/{token}")
async def get_showcase_edit_data(token: str):
    """透過 Token 取得 Showcase 編輯資料（無需認證）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await _verify_showcase_edit_token(session, token, require_editable=False)
        return await _build_showcase_edit_data(session, sc)


@router.get("/public/showcase-edit/{token}/status")
async def get_showcase_edit_status(token: str):
    """透過 token 查發布狀態（編輯器「發布時間線」輪詢用；唯讀 token 也可看）。

    rebuild 資訊經 core.crm_logic.rebuild_status_public 白名單過濾 ——
    絕不回傳 output_tail / error 內文。
    """
    import time as _time

    from core.crm_logic import rebuild_status_public
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await _verify_showcase_edit_token(session, token, require_editable=False)
        site_url, url_path = await _public_work_url(session, sc)
        out = {
            "published": bool(sc.published),
            "published_at": sc.published_at.isoformat() if sc.published_at else None,
            "verified_at": sc.verified_at.isoformat() if sc.verified_at else None,
            "public_url": (site_url + url_path) if url_path else None,
        }
    try:
        from services.website import rebuild_service
        raw = await rebuild_service.get_rebuild_status()
    except Exception:
        raw = None  # 精簡 agent 無 website services → 安全降級 idle
    out["rebuild"] = rebuild_status_public(raw, _time.time())
    return out


@router.get("/public/showcase-edit/{token}/staff_search")
async def search_staff_via_token(token: str, q: str = Query("")):
    """透過 token 搜尋 crm_staff（給 showcase-edit autocomplete 用）。

    回傳前 10 筆，name 或 role 包含 q（case-insensitive）。
    q 為空時回傳前 10 筆 name 字典序（給 input focus 但還沒打字時的初始下拉）。
    """
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        await _verify_showcase_edit_token(session, token, require_editable=False)
        query = select(CrmStaff).order_by(CrmStaff.name)
        if q.strip():
            ql = f"%{q.strip()}%"
            query = query.where(or_(CrmStaff.name.ilike(ql), CrmStaff.role.ilike(ql)))
        query = query.limit(10)
        rows = (await session.execute(query)).scalars().all()
    return {"items": [_to_staff_public_dict(s) for s in rows]}


@router.get("/public/showcase-edit/{token}/clients_search")
async def search_clients_via_token(token: str, q: str = Query("")):
    """透過 token 搜尋 clients（給 showcase-edit「客戶」欄 autocomplete 用）。

    回傳前 10 筆，short_name 或 full_name 包含 q（case-insensitive）。
    q 為空時回傳前 10 筆 short_name 字典序。
    回傳簡化欄位：id / short_name / full_name（避免洩漏 tax_id / payment_info 等敏感欄位）。
    """
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        await _verify_showcase_edit_token(session, token, require_editable=False)
        query = select(Client).order_by(Client.short_name)
        if q.strip():
            ql = f"%{q.strip()}%"
            query = query.where(or_(Client.short_name.ilike(ql), Client.full_name.ilike(ql)))
        query = query.limit(10)
        rows = (await session.execute(query)).scalars().all()
    return {"items": [
        {"id": c.id, "short_name": c.short_name, "full_name": c.full_name or ""}
        for c in rows
    ]}


@router.post("/public/showcase-edit/{token}/staff_quick_add")
async def quick_add_staff_via_token(token: str, req: StaffQuickAddPayload):
    """透過 token 快速建 minimal staff（給 showcase-edit「找不到→建立」用）。

    最小欄位：name + role。狀態預設「專案」、resume_visible 預設 false。
    自動標記 created_via='showcase_edit' + created_for_project_id=當前作品 id。
    """
    _require_db()
    factory = await _get_factory()
    now = _now()
    async with factory() as session:
        sc = await _verify_showcase_edit_token(session, token, require_editable=True)
        s = CrmStaff(
            id=uuid.uuid4().hex,
            name=req.name.strip(),
            role=(req.role or "").strip(),
            status="專案",
            resume_visible=False,
            created_via=STAFF_CREATED_VIA_SHOWCASE_EDIT,
            created_for_project_id=sc.project_id or sc.id,
            created_at=now, updated_at=now,
        )
        session.add(s)
        await session.commit()
        await session.refresh(s)
    return _to_staff_public_dict(s)


@router.put("/public/showcase-edit/{token}")
async def update_showcase_edit_data(token: str, request: Request):
    """透過 Token 更新 Showcase 資料（無需認證）。"""
    from db.models_website import WebsiteProjectCategory
    _require_db()
    factory = await _get_factory()
    body = await request.json()
    async with factory() as session:
        sc = await _verify_showcase_edit_token(session, token, require_editable=True)
        _apply_showcase_fields(sc, body)
        # 對外作品分類 / 標籤關聯（共用 website_categories 表，後端不分 kind）：
        # 全量替換 — delete all then re-add，避免 diff 邏輯 race。
        if "category_ids" in body:
            ids = [int(x) for x in (body["category_ids"] or []) if str(x).isdigit() or isinstance(x, int)]
            await session.execute(
                delete(WebsiteProjectCategory)
                .where(WebsiteProjectCategory.project_id == sc.id)
            )
            for cid in ids:
                session.add(WebsiteProjectCategory(project_id=sc.id, category_id=cid))
        # 作品系列歸屬（series_id 驗證存在；null = 解除）。順序不收前端值 —
        # 新掛的作品排到系列尾端（max+1），精排在官網管理成員面板做（全量替換端點）。
        if "series_id" in body:
            sid = body["series_id"]
            new_sid = int(sid) if sid not in ("", None) else None
            if new_sid != sc.series_id:
                if new_sid is None:
                    sc.series_id, sc.series_order = None, 0
                else:
                    from sqlalchemy import func as sa_func
                    from db.models_website import WebsiteSeries
                    if not await session.get(WebsiteSeries, new_sid):
                        raise HTTPException(status_code=404, detail="指定的作品系列不存在")
                    max_order = (await session.execute(
                        select(sa_func.max(CrmProjectShowcase.series_order))
                        .where(CrmProjectShowcase.series_id == new_sid))).scalar()
                    sc.series_id = new_sid
                    sc.series_order = (max_order if max_order is not None else -1) + 1
        # 作品身分欄位（public_title / year / featured / noindex → sc；
        # client → project）。token 路徑信任 PM 編輯所有作品相關欄位。
        project = await session.get(CrmProject, sc.project_id or sc.id)
        _apply_work_identity_fields(sc, project, body)
        # 首次發布配號（token PUT 可帶 published；子作品 _sync no-op，這裡保底）
        from services.website.project_service import ensure_work_number
        await ensure_work_number(session, sc)
        if project and is_main_work(sc):
            from services.website.project_service import (
                mirror_main_work_to_project, mirror_public_to_crm,
            )
            mirror_main_work_to_project(sc, project)   # dual-write（Phase 4 停寫）
            # name/client_id 回寫只限主作品 — 子作品改標題不可改掉 CRM 專案名
            await mirror_public_to_crm(session, project, body)
        # SEO 覆寫欄位 — 只接 SHOWCASE_EDIT_PATCHABLE_FIELDS allowlist 內的鍵
        # narrative_long / key_facts / faqs 由 admin Tab + AI pipeline 寫，token 路徑不暴露
        if isinstance(body.get("seo"), dict):
            from services.website import seo_service
            seo_payload = {
                k: v for k, v in body["seo"].items()
                if k in seo_service.SHOWCASE_EDIT_PATCHABLE_FIELDS
            }
            if seo_payload:
                await seo_service.update_project_seo(
                    session, sc.id, seo_payload, by="showcase-edit",
                )
        await _sync_showcase_to_public(session, sc)
        await session.commit()
    # 不論作品 published 與否都標 dirty，邏輯簡單；
    # publish=false 的作品最壞情況是浪費一次 rebuild cycle。
    await _mark_dirty_safe("showcase token PUT")
    return {"status": "ok"}


@router.post("/public/showcase-edit/{token}/carry_in")
async def carry_in_showcase_credits(token: str, request: Request):
    """一鍵帶入 — 從專案派工生 credits BLOCK 結構，並補齊作品 meta（標題/客戶/年份）。

    body: {mode: 'append'|'replace'}（預設 'replace'）。
      - 'replace'：整份覆寫 sc.credits 為新 blocks。
      - 'append'：接在既有 sc.credits 後面。
    另補作品基本資料（僅在原本為空時）：public_title←name、public_client←客戶代稱、
    public_year←completion_date.year。commit 後鏡射 public_* + 觸發 rebuild。

    回傳 shape 與 GET /public/showcase-edit/{token} 完全相同（前端直接刷新畫面）。
    """
    _require_db()
    factory = await _get_factory()
    body = await request.json()
    mode = (body.get("mode") or "replace")
    if mode not in ("append", "replace"):
        mode = "replace"
    async with factory() as session:
        sc = await _verify_showcase_edit_token(session, token, require_editable=True)
        # 派工在專案層 — 子作品也從「所屬專案」帶入 credits
        blocks = await _build_credit_blocks_from_staff(session, sc.project_id or sc.id)
        if mode == "append":
            sc.credits = list(sc.credits or []) + blocks
        else:
            sc.credits = blocks
        sc.credits_mode = "block"
        sc.updated_at = _now()
        # 補作品 meta（僅在原本為空時）— 身分欄位正本在 sc；主作品 dual-write 回 project
        project = await session.get(CrmProject, sc.project_id or sc.id)
        main = bool(project) and is_main_work(sc)
        if project:
            if not sc.title:
                sc.title = project.name
            if not project.public_client:
                client = await session.get(Client, project.client_id)
                if client:
                    project.public_client = client.short_name or client.full_name or ""
            if not sc.year and project.completion_date:
                sc.year = project.completion_date.year
            if main:
                project.public_title = project.public_title or sc.title
                project.public_year = project.public_year or sc.year
        await _sync_showcase_to_public(session, sc)
        await session.commit()
        data = await _build_showcase_edit_data(session, sc)
    await _mark_dirty_safe("showcase carry_in")
    return data


@router.post("/public/showcase-edit/{token}/request_ai_review")
async def request_ai_review_via_token(token: str):
    """PM 從 showcase-edit 觸發「請 AI 重新生」— mark needs_ai_review=True 讓
    Claude 下次跑 audit 會撈到此作品。不直接呼 LLM，pipeline 由 admin/Claude
    自行決定何時跑（避免 PM 點按鈕直接打 LLM 帳）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await _verify_showcase_edit_token(session, token, require_editable=True)
        from services.website import seo_service
        await seo_service.mark_project_seo_needs_review(session, sc.id)
    return {"status": "ok"}


@router.post("/public/showcase-edit/{token}/run_ai_now")
async def run_ai_now_via_token(token: str):
    """PM 從 showcase-edit 觸發「立即執行此作品」— 直接呼 claude --print
    生 SEO 內容寫進 DB。耗 Max 訂閱額度。每作品 30-60 秒。
    """
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await _verify_showcase_edit_token(session, token, require_editable=True)
        from services.website import seo_runner, rebuild_service
        result = await seo_runner.run_pipeline(session, target_project_id=sc.id)
        if result.get("processed", 0) > 0:
            await rebuild_service.mark_dirty()
        return result


def _parse_questions(text: str) -> list:
    """把 claude 輸出解析成問題清單。優先走「每行一題」純文字（claude 最穩），
    另備 JSON 解析（含雙重編碼/智慧引號/fence）當 fallback。最多回 5 個。"""
    import json
    import re

    def _norm(s: str) -> str:
        return (s or "").replace("“", '"').replace("”", '"') \
                        .replace("‘", "'").replace("’", "'")

    def _as_list(s: str):
        try:
            v = json.loads(s)
        except Exception:
            return None
        return v if isinstance(v, list) else None

    body = re.sub(r"```(?:json)?", "", _norm((text or "").strip())).strip()

    # 1) JSON（若 claude 硬回 JSON）：整段 → 抽 [...] → 解開單元素雙重編碼
    arr = _as_list(body)
    if arr is None:
        m = re.search(r"\[.*\]", body, re.DOTALL)
        if m:
            arr = _as_list(m.group(0))
    if isinstance(arr, list):
        if len(arr) == 1 and isinstance(arr[0], str) and arr[0].strip().startswith("["):
            inner = _as_list(arr[0])
            if inner is not None:
                arr = inner
        out = [str(x).strip() for x in arr if str(x).strip()]
        if len(out) >= 2:
            return out[:5]

    # 2) 逐行（純文字/編號/項目符號都吃）：去掉行首符號與引號，優先取含問號的行
    lines = []
    for ln in body.splitlines():
        ln = re.sub(r'^\s*[-*•\d.、)）.\s"\']+', "", ln).strip().strip('"[],')
        if ln and ln not in ("[", "]"):
            lines.append(ln)
    q = [ln for ln in lines if "？" in ln or "?" in ln]
    return (q or lines)[:5]


async def _ai_description_context(session, sc):
    """撈這個作品給 AI 用的精簡上下文（名稱 / 客戶 / 現有描述 / 參考資料）。"""
    project = await session.get(CrmProject, sc.project_id or sc.id)
    client_name = ""
    if project and project.client_id:
        client = await session.get(Client, project.client_id)
        if client:
            client_name = (client.short_name or client.full_name or "").strip()
    # 作品標題優先（子作品有自己的名字）、fallback 專案名
    name = (sc.title or "").strip() or (project.name if project else "") or ""
    existing = (sc.description or "") or (project.public_description if project else "")
    from services.website import seo_service
    ref = seo_service.build_ai_reference_prompt(sc.ai_reference_files, sc.ai_reference_notes)
    return name, client_name, existing.strip(), ref


@router.post("/public/showcase-edit/{token}/ai_description/questions")
async def ai_description_questions(token: str):
    """AI 互動撰寫描述 step 1：依作品資訊出 3-5 個引導問題（headless claude，~30-60s）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await _verify_showcase_edit_token(session, token, require_editable=True)
        name, client_name, existing, ref = await _ai_description_context(session, sc)
    ref_block = (
        f"製作人提供的參考資料（請據此讓內容更準確、更具體、更貼近實情）：\n{ref}\n\n"
        if ref else ""
    )
    prompt = (
        "你是影像製作公司的 SEO 文案助手。針對以下作品，出 3-5 個「簡短、具體」的引導問題，"
        "幫製作人寫出一段能吸引 Google 搜尋流量、讓陌生潛在客戶看到的專案描述。\n"
        f"{ref_block}"
        f"作品名稱：{name}\n客戶：{client_name or '（未填）'}\n"
        f"現有描述：{existing or '（無）'}\n\n"
        "每行一個問題，純文字繁體中文。不要編號、不要引號、不要 JSON、不要任何開場白或結尾說明。"
    )
    from services.website.seo_runner import _call_claude
    out, err = await _call_claude(prompt)
    if not out:
        raise HTTPException(status_code=502, detail=f"AI 產生問題失敗：{err}")
    return {"questions": _parse_questions(out)}


@router.post("/public/showcase-edit/{token}/ai_description/draft")
async def ai_description_draft(token: str, request: Request):
    """AI 互動撰寫描述 step 2：綜合製作人答案 + 作品資訊，生成精簡 SEO 描述。
    只回草稿（不寫 DB）；由前端審核後再套用（存進 public_description + seo_description）。"""
    _require_db()
    body = await request.json()
    answers = body.get("answers") or []   # [{q, a}, ...]
    factory = await _get_factory()
    async with factory() as session:
        sc = await _verify_showcase_edit_token(session, token, require_editable=True)
        name, client_name, existing, ref = await _ai_description_context(session, sc)
    qa_text = "\n".join(
        f"Q：{a.get('q','')}\nA：{a.get('a','')}"
        for a in answers if isinstance(a, dict) and (a.get("a") or "").strip()
    ) or "（製作人未補充）"
    ref_block = (
        f"製作人提供的參考資料（請據此讓內容更準確、更具體、更貼近實情）：\n{ref}\n\n"
        if ref else ""
    )
    prompt = (
        "你是影像製作公司的 SEO 文案。根據以下作品資訊與製作人回答，寫一段精簡的繁體中文專案描述。\n"
        "規則：\n"
        "- 80-160 字、2-4 句；精簡不浮誇、不要條列、不要 keyword stuffing。\n"
        "- 開頭就講「為誰做的什麼影片」；自然帶入 客戶名、影片類型、主題/產業 等搜尋詞。\n"
        "- 客戶名、人名不翻成英文。\n"
        "- 只回描述本文，不要標題、不要引號、不要任何說明文字。\n\n"
        f"{ref_block}"
        f"作品名稱：{name}｜客戶：{client_name or '（未填）'}\n"
        f"製作人回答：\n{qa_text}"
    )
    from services.website.seo_runner import _call_claude
    out, err = await _call_claude(prompt)
    if not out:
        raise HTTPException(status_code=502, detail=f"AI 生成描述失敗：{err}")
    desc = out.strip().strip('"“”').strip()
    return {"description": desc}


# ── AI 參考資料上傳 / 補充說明 / 刪除（token-authed，寫 draft 狀態不 rebuild）──

@router.post("/public/showcase-edit/{token}/ai_reference/upload")
async def upload_ai_reference(token: str, file: UploadFile = File(...)):
    """透過 Token 上傳 AI 參考資料文件（txt/md/csv/pdf/docx），抽出文字供 AI
    寫描述 / SEO 用（無需認證）。抽不到文字（掃描版 PDF）→ 400。"""
    _require_db()
    factory = await _get_factory()
    original_name = file.filename or "file"
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in _ALLOWED_REF_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"不支援的檔案格式：{ext or '（無副檔名）'}（僅支援 txt/md/csv/pdf/docx）",
        )
    content = await file.read()
    if len(content) > _REF_MAX_BYTES:
        raise HTTPException(status_code=400, detail="檔案過大（上限 10MB）")
    text = (_extract_doc_text(content, original_name) or "").strip()
    if not text:
        raise HTTPException(
            status_code=400,
            detail="抽不到文字（可能是掃描版 PDF），請改用有文字層的檔或直接貼文字",
        )
    text = text[:_REF_MAX_CHARS_PER_FILE]
    async with factory() as session:
        sc = await _verify_showcase_edit_token(session, token, require_editable=True)
        project_id = sc.id
        # 存原始檔到 ai_refs 子目錄（uuid 檔名避免碰撞 / 非 ASCII 開檔問題）
        stored_name = f"{uuid.uuid4().hex}{ext}"
        try:
            with open(os.path.join(_ai_refs_dir(project_id), stored_name), "wb") as fh:
                fh.write(content)
        except OSError:
            stored_name = ""
        entry = {"name": original_name, "text": text, "chars": len(text), "stored": stored_name}
        files = list(sc.ai_reference_files or [])
        files.append(entry)
        sc.ai_reference_files = files
        sc.updated_at = _now()
        await session.commit()
        await session.refresh(sc)
        result_files = _ref_files_minimal(sc.ai_reference_files)
        notes = sc.ai_reference_notes or ""
    return {"ai_reference_files": result_files, "ai_reference_notes": notes}


@router.put("/public/showcase-edit/{token}/ai_reference")
async def update_ai_reference_notes(token: str, request: Request):
    """透過 Token 更新 AI 參考資料的「補充說明」文字（無需認證）。"""
    _require_db()
    factory = await _get_factory()
    body = await request.json()
    notes = body.get("notes") or ""
    async with factory() as session:
        sc = await _verify_showcase_edit_token(session, token, require_editable=True)
        sc.ai_reference_notes = notes
        sc.updated_at = _now()
        await session.commit()
        await session.refresh(sc)
        result_files = _ref_files_minimal(sc.ai_reference_files)
        n = sc.ai_reference_notes or ""
    return {"ai_reference_files": result_files, "ai_reference_notes": n}


@router.delete("/public/showcase-edit/{token}/ai_reference/file")
async def delete_ai_reference_file(token: str, request: Request):
    """透過 Token 刪除一筆 AI 參考資料文件（依 name 比對，移除第一筆）+
    best-effort 刪磁碟原始檔（無需認證）。"""
    _require_db()
    factory = await _get_factory()
    body = await request.json()
    target = body.get("name") or ""
    async with factory() as session:
        sc = await _verify_showcase_edit_token(session, token, require_editable=True)
        project_id = sc.id
        files = list(sc.ai_reference_files or [])
        kept: list = []
        removed: list = []
        matched = False
        for f in files:
            if (not matched) and isinstance(f, dict) and f.get("name") == target:
                matched = True
                removed.append(f)
            else:
                kept.append(f)
        sc.ai_reference_files = kept
        sc.updated_at = _now()
        await session.commit()
        await session.refresh(sc)
        result_files = _ref_files_minimal(sc.ai_reference_files)
        notes = sc.ai_reference_notes or ""
    # best-effort 刪磁碟檔
    for f in removed:
        stored = f.get("stored") if isinstance(f, dict) else ""
        if stored:
            p = os.path.join(_ai_refs_dir(project_id), stored)
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
    return {"ai_reference_files": result_files, "ai_reference_notes": notes}


@router.post("/public/showcase-edit/{token}/gallery")
async def upload_showcase_edit_gallery(token: str, file: UploadFile = File(...),
                                       caption: str = Query("")):
    """透過 Token 上傳 Showcase 圖庫圖片（無需認證）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await _verify_showcase_edit_token(session, token, require_editable=True)
        project_id = sc.id
    ext = (os.path.splitext(file.filename or "img.jpg")[1] or ".jpg").lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")
    sdir = _showcase_dir(project_id)
    content = await file.read()
    saved = _save_image_as_webp(content, sdir, uuid.uuid4().hex)
    fname = os.path.basename(saved)
    url = f"/uploads/projects/{project_id}/showcase/{fname}"
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        gallery = list(sc.gallery or [])
        gallery.append({"url": url, "caption": caption})
        sc.gallery = gallery
        sc.updated_at = _now()
        await session.commit()
        await session.refresh(sc)
    return {"status": "ok", "gallery": sc.gallery or []}


@router.post("/public/showcase-edit/{token}/featured_image")
async def upload_showcase_edit_featured_image(token: str, file: UploadFile = File(...)):
    """透過 Token 上傳作品「精選圖 / 首頁輪播圖」（無需認證）。

    與 gallery（append 到 sc.gallery）不同，這裡寫的是 CrmProject.public_featured_image
    單一欄位 —— 首頁精選輪播會優先用這張，沒設就 fallback 到成果展示第一張。
    """
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await _verify_showcase_edit_token(session, token, require_editable=True)
        project_id = sc.id
    ext = (os.path.splitext(file.filename or "img.jpg")[1] or ".jpg").lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")
    sdir = _showcase_dir(project_id)
    content = await file.read()
    saved = _save_image_as_webp(content, sdir, f"featured_{uuid.uuid4().hex}")
    fname = os.path.basename(saved)
    url = f"/uploads/projects/{project_id}/showcase/{fname}"
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc:
            raise HTTPException(status_code=404, detail="找不到作品")
        # 單值欄位（覆寫語意）— 刪掉舊精選圖檔，免得 showcase 目錄堆孤兒 webp（比照封面上傳）
        old = sc.featured_image or ""
        if old and old != url:
            old_path = os.path.join(sdir, os.path.basename(old))
            if os.path.isfile(old_path):
                try:
                    os.remove(old_path)
                except OSError:
                    pass
        sc.featured_image = url
        sc.updated_at = _now()
        # 主作品 dual-write（Phase 4 停寫）
        if is_main_work(sc):
            project = await session.get(CrmProject, sc.project_id)
            if project:
                project.public_featured_image = url
        await session.commit()
    # 對外網站首頁輪播讀精選圖 → 標 dirty 觸發 rebuild
    await _mark_dirty_safe("featured_image")
    return {"url": url}


@router.post("/public/showcase-edit/{token}/process")
async def upload_showcase_edit_process(token: str, file: UploadFile = File(...),
                                       caption: str = Query(""),
                                       phase: str = Query(""),
                                       item_type: str = Query("image")):
    """透過 Token 上傳 Showcase 製程項目圖片（無需認證）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await _verify_showcase_edit_token(session, token, require_editable=True)
        project_id = sc.id
    ext = (os.path.splitext(file.filename or "img.jpg")[1] or ".jpg").lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")
    sdir = _showcase_dir(project_id)
    content = await file.read()
    saved = _save_image_as_webp(content, sdir, f"process_{uuid.uuid4().hex}")
    fname = os.path.basename(saved)
    url = f"/uploads/projects/{project_id}/showcase/{fname}"
    item = {"type": item_type, "url": url, "caption": caption, "phase": phase, "video_url": ""}
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        items = list(sc.process_items or [])
        items.append(item)
        sc.process_items = items
        sc.updated_at = _now()
        await session.commit()
        await session.refresh(sc)
    return {"status": "ok", "process_items": sc.process_items or []}


# ── Site API (for future website) ──────────────────────────

@router.get("/public/site/works")
async def list_published_works():
    """列出所有已發布的 Showcase（供官網使用，無需認證）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmProjectShowcase)
            .where(CrmProjectShowcase.published == True)  # noqa: E712
            .order_by(CrmProjectShowcase.published_at.desc())
        )).scalars().all()
        works = []
        for sc in rows:
            proj = await session.get(CrmProject, sc.project_id or sc.id)
            client_name = ""
            project_name = ""
            if proj:
                project_name = proj.name or ""
                client = await session.get(Client, proj.client_id) if proj.client_id else None
                client_name = client.short_name if client else ""
            works.append({
                "id": sc.id,
                "project_name": project_name,
                "client_name": client_name,
                "cover_url": sc.cover_url or "",
                "slug": sc.slug or "",
                "description": sc.description or "",
                "video_url": sc.video_url or "",
                "published_at": sc.published_at.isoformat() if sc.published_at else None,
            })
    return {"works": works}


@router.get("/public/site/team")
async def list_public_team():
    """列出所有公開履歷的人員（供官網使用，無需認證）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmStaff)
            .where(CrmStaff.resume_visible == True)  # noqa: E712
            .order_by(CrmStaff.name)
        )).scalars().all()
        team = []
        for s in rows:
            team.append({
                "id": s.id,
                "name": s.name,
                "role": s.role or "",
                "photo_url": s.photo_url or "",
            })
    return {"team": team}
