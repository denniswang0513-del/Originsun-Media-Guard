"""core/image_utils.py — 圖片上傳共用工具（單一正本）。

save_image_as_webp 原本在 routers/website/admin_posts.py 與 routers/crm/_shared.py
各持一份近雙胞（admin_posts 版多 EXIF 方向校正）——2026-07-20 收編到 core/：
core/ 在 /publish scp 到 NAS website-api 容器的清單內，website 與 crm 兩棵
router 樹都 import 得到，改進只落一處。
"""
import io
import logging
import os

logger = logging.getLogger(__name__)

# iPhone HEIC：PIL 需要 pillow-heif 註冊 opener 才開得動（劇組手機大宗是 iPhone —
# 影像紀錄上傳的主力格式）。沒裝的環境（舊 agent/NAS 容器）安靜跳過，HEIC 只是無縮圖。
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass


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
