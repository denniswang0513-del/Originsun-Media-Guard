"""
共用格式化工具 — Originsun Media Guard Pro
提供人類可讀的檔案大小格式化等通用函式。
"""


def fmt_size(size: float) -> str:
    """將位元組數轉為人類可讀字串（B / KB / MB / GB / TB）。

    使用浮點除法保留一位小數精度，例如 1536 bytes → "1.5 KB"。
    """
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"
