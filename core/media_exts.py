# -*- coding: utf-8 -*-
"""「我們認得哪些影片格式」的單一正本。

2026-08-30 體檢時，這個問題在 repo 裡有 **13 份答案，而且互相矛盾**：

    9 種（完整）  scheduler / schemas×2 / api_proxy / api_utils / app.js×3
    7 種          drone_watcher / footage_indexer / api_utils 的檔案挑選視窗
    9 種（不同）  transcribe.js —— 有 .webm .ts，沒有 .r3d .braw
    5 種          footage.js 的篩選下拉

🔴 那個 7 種的版本正在漏資料，而且是靜默的：`footage_indexer` 掃素材時副檔名
不在自己那份清單裡就 `continue`，所以 **RED（.r3d）與 BRAW 的素材在素材庫索引裡
根本不存在**，空拍監控資料夾也一樣看不到它們。沒有錯誤訊息，就只是沒有 ——
這種缺陷不會有人回報，只會有人覺得「怎麼找不到那支片」。

前端是 vanilla JS、沒有 build step，抄不到這裡的常數，所以還是各留一份鏡射；
但 `tests/unit/test_media_exts_sync.py` 會逐值比對，漂開就紅。

## 兩組值域刻意不同

`VIDEO_EXTS` 是「這是不是一支素材」—— 備份、索引、瀏覽、列表、清點都問這個。
`TRANSCRIBE_EXTS` 是「ffmpeg 解不解得開」—— 轉檔與語音辨識問這個。

差別在兩端：`.r3d` / `.braw` 是原廠 raw 格式，ffmpeg 沒有解碼器（要 REDCINE /
Blackmagic SDK），所以它們是素材、但抽不出音軌；`.webm` / `.ts` 反過來，
不是拍攝素材但 ffmpeg 讀得動，語音辨識收得下。

合成一組會壞掉，所以是兩個名字，不是一個。
"""

# 拍攝素材（備份 / 索引 / 瀏覽 / 列表）
VIDEO_EXTS = frozenset({
    ".mov", ".mp4", ".mkv", ".mxf", ".avi", ".mts", ".m2ts", ".r3d", ".braw",
})

# ffmpeg 解得開的（轉檔 / 語音辨識）
# 原廠 RAW：是素材（索引／備份要看到），但隨附的 ffmpeg 8 沒有 REDCODE 解碼器、沒有 BRAW demuxer，
# 任何會餵 ffmpeg 的管線（轉檔／串帶／空拍寫入）都要把它們挑出來另外處理，不能靜默丟進去失敗
_RAW_CAMERA_EXTS = frozenset({".r3d", ".braw"})
RAW_CAMERA_EXTS = _RAW_CAMERA_EXTS
_WEB_EXTS = frozenset({".webm", ".ts"})
TRANSCRIBE_EXTS = (VIDEO_EXTS - _RAW_CAMERA_EXTS) | _WEB_EXTS

# 空拍照片走 exiftool-only 那條路（core/worker._drone_meta_sync）
IMAGE_EXTS = frozenset({
    ".dng", ".jpg", ".jpeg", ".arw", ".cr2", ".cr3",
    ".nef", ".raf", ".orf", ".rw2", ".tif", ".tiff",
})

MEDIA_EXTS = VIDEO_EXTS | IMAGE_EXTS


def video_filetypes_spec() -> str:
    """tkinter 檔案挑選視窗的 filetypes 字串（`_run_picker_subprocess` 用）。

    大小寫都列：tkinter 在 Windows 上的副檔名比對是**區分大小寫**的，而相機出的
    檔常常是 `.MOV` / `.MP4`。原本只手寫了這兩個大寫變體，於是 `.MXF` 那類
    在挑選視窗裡看不見（檔案就在那，只是被濾掉了）。
    """
    pats = " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))
    pats_upper = " ".join(f"*{e.upper()}" for e in sorted(VIDEO_EXTS))
    return f"(('Video', '{pats} {pats_upper}'), ('All files', '*.*'))"


def sorted_video_exts() -> list:
    """給 Pydantic 預設值與 API 回應用的穩定順序（set 的順序每次啟動都不同，
    會讓回應內容無謂地跳動、也讓測試無法逐值比對）。"""
    return sorted(VIDEO_EXTS)
