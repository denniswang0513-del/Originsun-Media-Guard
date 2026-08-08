"""core/image_utils.py — 圖片上傳共用工具（單一正本）。

save_image_as_webp 原本在 routers/website/admin_posts.py 與 routers/crm/_shared.py
各持一份近雙胞（admin_posts 版多 EXIF 方向校正）——2026-07-20 收編到 core/：
core/ 在 /publish scp 到 NAS website-api 容器的清單內，website 與 crm 兩棵
router 樹都 import 得到，改進只落一處。
"""
import io
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# iPhone HEIC：PIL 需要 pillow-heif 註冊 opener 才開得動（劇組手機大宗是 iPhone —
# 影像紀錄上傳的主力格式）。沒裝的環境（舊 agent/NAS 容器）安靜跳過，HEIC 只是無縮圖。
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass


def save_webp_or_none(content: bytes, dest_dir: str, base_name: str,
                      max_side: int = 0):
    """轉 WebP 寫入 dest_dir/base_name.webp（makedirs 自理），回完整路徑；
    PIL 開不了 → save_image_as_webp 會 fallback 寫 .bin，那對「要縮圖/貼圖」
    的情境沒意義（原檔已另存）→ 刪掉回 None。

    貼圖（api_paste）與影像紀錄縮圖（crm.media_log）共用：兩邊都是「寫一張
    webp，非圖就當失敗刪掉」，helper 收掉這段 + 省去呼叫端自己判斷 .bin。
    """
    os.makedirs(dest_dir, exist_ok=True)
    saved = save_image_as_webp(content, dest_dir, base_name, max_side)
    if saved.endswith(".webp"):
        return saved
    try:
        os.remove(saved)
    except OSError:
        pass
    return None


# PDF 首頁 → 縮圖。提案的主體多半是 PDF，只給檔案圖示等於整面卡片都長一樣。
# PyMuPDF 是純 wheel（不像 poppler 要外部執行檔），且**惰性**讀 —— 30 頁的簡報
# 算首頁實測 26ms、heap 尖峰 19KB，不是整份進記憶體。
# 沒裝就回 None 退成檔案圖示（同下方 pillow_heif 的處理），不讓環境差異變成故障。
# ⚠️ 生產跑的是 python_embed 不是 .venv，新依賴要手動裝進去並記進
#    requirements_server.txt（見 memory: prod python_embed 依賴）。
def _pdf_first_page_png(src_path: str, max_side: int) -> Optional[bytes]:
    try:
        import pymupdf
    except ImportError:
        return None
    try:
        with pymupdf.open(src_path) as doc:
            if not doc.page_count:
                return None
            page = doc.load_page(0)
            longest = max(page.rect.width, page.rect.height) or 1
            zoom = (max_side / longest) if max_side else 1.0
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            return pix.tobytes("png")
    except Exception as e:
        logger.warning("[thumb] PDF first page failed (%s)", e)
        return None


def webp_thumb_from_path(src_path: str, dest_dir: str, base_name: str,
                         max_side: int = 0):
    """同 `save_webp_or_none`，但**不把原檔整份讀進記憶體** → 路徑或 None。

    🔴 給「原檔已經在磁碟上」的情境（影像紀錄的縮圖）。走 bytes 版的話，一張
    300MB 的 TIFF 就是 300MB 常駐在 Python heap，而那台 NAS 容器同時還壓著幾個
    進行中的分塊緩衝。PIL 直接吃 file object 是惰性解碼，實測 68MB 的 TIFF 從
    71MB 常駐降到 0.2MB。

    `draft()` 讓 JPEG 在**解碼階段**就降到接近目標尺寸（DCT scaling），不是解完
    整張再縮 —— 縮圖情境純賺（實測 12MB JPEG 363ms → 226ms）。
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    os.makedirs(dest_dir, exist_ok=True)
    webp_path = os.path.join(dest_dir, base_name + ".webp")

    # PDF 走另一條解碼路徑，之後的縮放/存檔完全共用（不另開一支近雙胞函式）
    if os.path.splitext(src_path)[1].lower() == ".pdf":
        png = _pdf_first_page_png(src_path, max_side)
        if not png:
            return None
        return save_webp_or_none(png, dest_dir, base_name, max_side)

    try:
        # 1MB 緩衝：預設 8KB 對 SMB 上的大圖是上百次小讀取
        with open(src_path, "rb", buffering=1 << 20) as fp:
            img = Image.open(fp)
            if max_side:
                img.draft("RGB", (max_side, max_side))   # JPEG 才有效，其餘無視
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGBA" if img.mode in ("RGBA", "LA") else "RGB")
            if max_side and max(img.size) > max_side:
                img.thumbnail((max_side, max_side), Image.LANCZOS)
            img.save(webp_path, "WEBP", quality=82, method=6)
        return webp_path
    except Exception as e:
        logger.warning("[thumb] WebP from path failed (%s)", e)
        try:
            os.remove(webp_path)          # 半成品不要留在圖床
        except OSError:
            pass
        return None


def save_image_as_webp(content: bytes, dest_dir: str, base_name: str,
                       max_side: int = 0) -> str:
    """寫圖檔到 dest_dir/<base_name>.webp，原始格式自動轉 WebP（quality 82）。

    max_side > 0：長邊超過時等比縮到該尺寸（封面/縮圖用；0 = 不縮）。
    EXIF 方向一律先校正（手機直拍不轉會躺著）。
    回傳寫好的檔案路徑；Pillow 不可用 / 開檔失敗 → fallback 寫原 bytes 到
    .bin（副檔名誠實反映非 WebP）。
    """
    webp_path = os.path.join(dest_dir, base_name + ".webp")
    try:
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(content))
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA"):
            img = img.convert("RGBA")
        else:
            img = img.convert("RGB")
        if max_side and max(img.size) > max_side:
            img.thumbnail((max_side, max_side), Image.LANCZOS)
        img.save(webp_path, "WEBP", quality=82, method=6)
        return webp_path
    except Exception as e:
        logger.warning("[upload] WebP convert failed (%s) — fallback raw", e)
        raw_path = webp_path.replace(".webp", ".bin")
        with open(raw_path, "wb") as f:
            f.write(content)
        return raw_path
