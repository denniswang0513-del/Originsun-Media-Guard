# -*- coding: utf-8 -*-
"""「我們認得哪些影片格式」只能有一個答案。

2026-08-30 體檢量到這個問題在 repo 裡有 **13 份答案，互相矛盾**。最糟的一份
正在漏資料，而且是靜默的：`footage_indexer` 掃素材時副檔名不在自己那份 7 種
清單裡就 `continue`，於是 RED（.r3d）與 BRAW 的素材在素材庫檢索裡不存在 ——
沒有錯誤、沒有紀錄，只有人覺得「怎麼找不到那支片」。

後端八份已經收成 `core/media_exts` 一份，`app.js` 那三份直接刪掉（不送 `exts`
就吃 `ListDirRequest` 的預設值）。剩下兩份前端鏡射抄不掉（vanilla JS 沒有
build step），由這裡逐值比對。
"""
import re

from tests.unit._srcscan import repo_src, schemas_src

from core.media_exts import (IMAGE_EXTS, MEDIA_EXTS, TRANSCRIBE_EXTS,
                             VIDEO_EXTS, sorted_video_exts,
                             video_filetypes_spec)


def _js_array(src: str, name: str) -> list:
    """`const NAME = [...]` 的字串元素。"""
    m = re.search(rf"const {name}\s*=\s*\[([^\]]*)\]", src)
    assert m, f"找不到 {name} 的定義（改名了？那正本那側也要跟著改）"
    return re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))


# ── 後端：所有消費端都指向同一個物件 ────────────────────────────

def test_every_backend_consumer_points_at_the_one_source():
    """🔴 用 `is` 比對，不是 `==`：值相等只代表**此刻**一樣，各自留一份的話
    下次改就又漂開了。要的是「它們根本是同一個物件」。"""
    from core.drone_watcher import _MEDIA_EXTS
    from core.scheduler import _VIDEO_EXTS as SCHED
    from services.footage_indexer import VIDEO_EXTS as FOOTAGE

    for name, obj in [("scheduler", SCHED), ("footage_indexer", FOOTAGE)]:
        assert obj is VIDEO_EXTS, f"{name} 又自己留了一份影片副檔名清單"
    # 空拍監控只問「是不是媒體檔」（照片與影片同一個掃描迴圈）
    assert _MEDIA_EXTS is MEDIA_EXTS


def test_no_backend_file_hardcodes_the_list_again():
    """掃原始碼：除了正本以外，誰再寫一次 `.braw` 字面就是又生一份。

    抓的是**字面量**而不是變數名 —— 下一個複製貼上的人不會沿用 `VIDEO_EXTS`
    這個名字，他會直接貼一組大括號進去。
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = []
    for rel in ("core", "routers", "services", "db"):
        for p in (root / rel).rglob("*.py"):
            if "__pycache__" in p.as_posix() or p.name == "media_exts.py":
                continue
            src = p.read_text(encoding="utf-8", errors="replace")
            # 只抓「跟 .mov 或 .mp4 出現在同一行」的 —— 單獨提到 .braw
            # （例如註解裡舉例）不算重造清單
            for ln in src.splitlines():
                if ".braw" in ln and (".mov" in ln or ".mp4" in ln) and "#" not in ln.split(".braw")[0]:
                    offenders.append(f"{p.relative_to(root).as_posix()}: {ln.strip()[:70]}")
    assert not offenders, ("這些地方又硬寫了一份影片副檔名清單，改用 "
                           f"`from core.media_exts import VIDEO_EXTS`：{offenders}")


def test_the_pydantic_defaults_come_from_the_source():
    """🔴 `Field(default_factory=...)` 而不是 `= [...]`：後者是求值一次的可變
    預設值，正本改了它不會跟著改（而且 list 當預設值本來就是雷）。"""
    from core.schemas import CompareSourceRequest, ListDirRequest
    assert ListDirRequest(path="x").exts == sorted_video_exts()
    assert CompareSourceRequest(source_dir="a", output_dir="b").video_exts == sorted_video_exts()
    src = schemas_src()
    assert src.count("Field(default_factory=sorted_video_exts)") == 2


# ── 前端：兩份抄不掉的鏡射 ──────────────────────────────────────

def test_the_footage_filter_offers_everything_the_index_accepts():
    """🔴 索引收得到、篩選卻列不出來 ＝ 那些素材進得去、找不到。

    這正是原本的狀態：索引端收 7 種、篩選下拉只列 5 種、而正本有 9 種。
    """
    got = _js_array(repo_src("frontend/tabs/footage/footage.js"), "FOOTAGE_EXTS")
    assert got == sorted_video_exts(), \
        f"素材庫篩選與 core/media_exts.VIDEO_EXTS 不同步：{got} vs {sorted_video_exts()}"


def test_the_transcribe_list_matches_the_decodable_set():
    got = _js_array(repo_src("frontend/tabs/transcribe/transcribe.js"),
                    "_VIDEO_EXTS_FOR_LIST")
    assert got == sorted(TRANSCRIBE_EXTS), \
        f"語音辨識的清單與 TRANSCRIBE_EXTS 不同步：{got} vs {sorted(TRANSCRIBE_EXTS)}"


def test_app_js_no_longer_carries_its_own_copy():
    """app.js 那三份是直接刪掉的 —— 不送 `exts` 就吃後端預設，這是唯一
    真正消滅鏡射的辦法（其他兩份做不到，因為它們是畫在畫面上的選項）。"""
    src = repo_src("frontend/app.js")
    assert ".braw" not in src, "app.js 又把副檔名清單抄回去了；不要送 exts，讓後端預設生效"


# ── 兩組值域刻意不同，別被「統一」掉 ────────────────────────────

def test_the_two_sets_differ_on_purpose_and_the_difference_is_documented():
    """🔴 有人（包括我）會想把這兩組合成一組。合了會壞：

      · `.r3d` / `.braw` 是素材、但 ffmpeg 解不開（要原廠 SDK）→ 備份要收，
        轉檔與語音辨識不能收，收了只會在跑到一半時失敗。
      · `.webm` / `.ts` 反過來：不是拍攝素材，但 ffmpeg 讀得動。

    所以差集兩邊都必須非空。這支測試存在的意義是把「為什麼是兩組」釘在程式裡，
    而不是只寫在註解裡等人讀到。
    """
    assert VIDEO_EXTS - TRANSCRIBE_EXTS == {".r3d", ".braw"}
    assert TRANSCRIBE_EXTS - VIDEO_EXTS == {".webm", ".ts"}
    doc = repo_src("core/media_exts.py")
    assert "SDK" in doc, "兩組不同的理由要寫在正本的檔頭，不然下一個人就合併了"


def test_the_raw_camera_formats_really_are_in_the_index_now():
    """這是整件事的重點：那兩種格式現在真的收得到了。"""
    from services.footage_indexer import VIDEO_EXTS as FOOTAGE
    from core.drone_watcher import _MEDIA_EXTS as DRONE
    for ext in (".r3d", ".braw"):
        assert ext in FOOTAGE, f"素材庫索引還是看不到 {ext}"
        assert ext in DRONE, f"空拍監控還是看不到 {ext}"


def test_the_file_picker_lists_both_cases():
    """🔴 tkinter 在 Windows 上比對副檔名**區分大小寫**，而相機出的檔常是大寫。

    原本只手寫了 `.MOV` `.MP4` 兩個大寫變體 —— `.MXF` 那類在挑選視窗裡看不見
    （檔案就在那，只是被濾掉了，而使用者只會覺得「這個資料夾是空的」）。
    """
    spec = video_filetypes_spec()
    for ext in VIDEO_EXTS:
        assert f"*{ext}" in spec, f"挑選視窗漏了 {ext}"
        assert f"*{ext.upper()}" in spec, f"挑選視窗漏了大寫的 {ext.upper()}"
    from routers.api_utils import _VIDEO_FILETYPES
    assert _VIDEO_FILETYPES == spec


def test_images_and_media_stay_consistent():
    assert MEDIA_EXTS == VIDEO_EXTS | IMAGE_EXTS
    assert not (VIDEO_EXTS & IMAGE_EXTS), "同一個副檔名同時算影片與照片"
