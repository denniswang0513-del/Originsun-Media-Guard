# -*- coding: utf-8 -*-
"""services/knowledge_podcast.py — 每一章一集 podcast（docs/KNOWLEDGE_BASE_PLAN.md §9.13）。

owner 2026-09-19：「你能根據這些內容製作聊天式的podcast嗎」「書本每一章的內容」
「每一章一個podcast」「內容深度深一點」「我希望podcast 儲存的路徑是D槽」。

一集怎麼長出來：

    第 N 章的「章節重點」
       ↓ ① claude：拆成 6–8 段，每段列出那一段**真的有**的具體素材
       ↓ ② claude：一段一段寫成兩人對談（每一項素材都要講到）
    [{who, text} …]
       ↓ ③ edge-tts：每一句配它的聲音（主持人＝雲希、來賓＝曉臻）
    一句一個小 mp3
       ↓ ④ ffmpeg concat
    <書的資料夾>\\podcast\\chNN.mp3　＋　chNN.json（逐字稿）

🔴 **為什麼是兩趟**：一次寫完整集實測 3,711 字／13.8 分鐘，而且把那一章大量的具體東西
   （人名、年份、頁碼、外文原詞）整批跳過 —— 模型要在一個回合裡顧到整章，只能挑幾個點講。
   拆段之後 5,565 字／19.8 分鐘，抽查 12 個具體項目全部出現。深度是被清單逼出來的。

🔴 **檔案放在那本書自己的資料夾底下**（不是另開一個目錄）：跟著書架的根目錄走
   （`D:\\Originsun-Knowledge\\books`；dev 是 `books_dev`），刪書一起刪、dev 與正式站天然分開。

🔴 **只有 master 跑得動**：claude CLI 與 ffmpeg 都只在主控上（同編譯）。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from typing import Optional

from core import knowledge_logic as kl
from services import knowledge_service as ks

#: 正在產的那幾集：`"<book_id>/<n>" -> 進度字串`。不在裡面＝沒在跑（同編譯：狀態只活在記憶體）
_making: dict = {}

#: 一集最多幾句（防走鐘：模型真的瘋掉時不要跑一小時的 TTS）
MAX_LINES = 400
#: 寫稿的逾時（一段一段各算）
SCRIPT_TIMEOUT = 900.0


def podcast_dir(book_id: str, make: bool = False) -> str:
    """那本書的 `podcast\\`。🔴 id 直接走 `book_dir()`（同 `asset_path` 的規矩）。"""
    d = os.path.join(ks.book_dir(book_id), kl.PODCAST_DIR)
    if make:
        os.makedirs(d, exist_ok=True)
    return d


def podcast_path(book_id: str, n: int, ext: str = "mp3") -> str:
    """第 n 章的音檔／逐字稿路徑。🔴 檔名先過白名單再拼。"""
    name = kl.podcast_name(n, ext)
    if not name:
        raise ks.ChapterNotFound(f"{book_id}/{n}")
    return os.path.join(podcast_dir(book_id), name)


def _size_mb(path: str) -> float:
    try:
        return round(os.path.getsize(path) / 1024 / 1024, 1)
    except OSError:
        return 0.0


def key_of(book_id: str, n) -> str:
    return f"{book_id}/{int(n)}"


def podcast_state(book_id: str) -> dict:
    """`{items: [{n, title, has, mb, minutes, stage}], done, total}`。

    `stage` 非空＝那一集正在產。`has` 是音檔已經在了。
    """
    rows = ks.chapter_rows_public(book_id)
    out = []
    for c in rows:
        n = int(c.get("n") or 0)
        mp3 = os.path.join(podcast_dir(book_id), kl.podcast_name(n) or "_")
        script = os.path.join(podcast_dir(book_id), kl.podcast_name(n, "json") or "_")
        minutes = 0.0
        if os.path.isfile(script):
            try:
                with open(script, encoding="utf-8") as f:
                    minutes = kl.podcast_minutes(json.load(f))
            except (OSError, ValueError):
                minutes = 0.0
        out.append({"n": n, "title": c.get("title") or "", "has": os.path.isfile(mp3),
                    "mb": _size_mb(mp3), "minutes": minutes,
                    "stage": _making.get(key_of(book_id, n), "")})
    return {"items": out, "done": sum(1 for x in out if x["has"]), "total": len(out),
            "making": sum(1 for x in out if x["stage"])}


def read_script(book_id: str, n: int) -> list:
    """第 n 章的逐字稿（沒有就空清單）。"""
    try:
        with open(podcast_path(book_id, n, "json"), encoding="utf-8") as f:
            rows = json.load(f)
    except (OSError, ValueError, ks.ChapterNotFound):
        return []
    return rows if isinstance(rows, list) else []


def start_podcast(book_id: str, n: int) -> None:
    """端點呼叫：先擋住重複、確認那一章在，再標 queued。真正的工作在 `run_podcast`。"""
    n = int(n)
    if not kl.podcast_name(n):
        raise ks.ChapterNotFound(f"{book_id}/{n}")
    if key_of(book_id, n) in _making:
        raise ks.BookBusy(book_id)
    ks.read_chapter(book_id, n)          # 那一章不在就 404（書不在也是）
    _making[key_of(book_id, n)] = "queued"


def cancel_all(book_id: str) -> None:
    """刪書之前把還掛著的進度清掉（不然那本書的鍵會永遠留著）。"""
    for k in [k for k in _making if k.startswith(book_id + "/")]:
        _making.pop(k, None)


async def _write_script(book_id: str, n: int, model: str, on_stage) -> list:
    """① 拆段 → ② 一段一段寫。回 `[{who, text}]`。"""
    from services.knowledge_claude import call_claude

    meta = ks.read_meta(book_id)
    ch = ks.read_chapter(book_id, n)
    body = ch.get("md") or ""
    if len(body.strip()) < kl.PODCAST_MIN_CHAPTER:
        # 硬做只會生出掰的或重複的內容（實測 173 字的章節被寫成 5,815 字）
        raise ValueError(f"這一章只有 {len(body.strip())} 字，太短了做不出 podcast")

    on_stage("拆段落")
    text, err = await call_claude(kl.podcast_outline_prompt(body), model=model,
                                  cwd=podcast_dir(book_id, make=True), timeout_sec=SCRIPT_TIMEOUT)
    if not text:
        raise RuntimeError(err or "拆段落失敗")
    a, b = text.find("["), text.rfind("]")
    try:
        outline = json.loads(text[a:b + 1]) if a >= 0 and b > a else []
    except ValueError:
        outline = []
    outline = [s for s in outline if isinstance(s, dict) and (s.get("facts") or s.get("point"))]
    if not outline:
        raise RuntimeError("拆不出段落（這一章可能太短）")

    rows = ks.chapter_rows_public(book_id)
    nxt = next((c for c in rows if int(c.get("n") or 0) == n + 1), None)
    next_hint = (nxt.get("title") or "") if nxt else ""
    label = f"第 {n} 章"
    budget = kl.podcast_budget(len(body), len(outline))   # 總長跟著原章節走，短章節不要硬撐

    lines: list = []
    for i, sec in enumerate(outline):
        on_stage(f"寫第 {i + 1}/{len(outline)} 段")
        prompt = kl.podcast_section_prompt(
            sec, body=body, budget=budget,
            opening=kl.podcast_opening(meta.get("title") or "", label, i == 0),
            closing=kl.podcast_closing(next_hint, i == len(outline) - 1))
        got, err = await call_claude(prompt, model=model,
                                     cwd=podcast_dir(book_id), timeout_sec=SCRIPT_TIMEOUT)
        if not got:
            raise RuntimeError(err or f"第 {i + 1} 段寫不出來")
        lines += kl.parse_podcast_script(got)
        if len(lines) >= MAX_LINES:
            break
    if not lines:
        raise RuntimeError("稿子是空的")
    return lines[:MAX_LINES]


async def _speak(lines: list, dest: str, on_stage) -> list:
    """③ 每一句配它的聲音。回小 mp3 的路徑清單。"""
    import edge_tts

    host_voice, host_who = kl.PODCAST_HOST
    guest_voice, _ = kl.PODCAST_GUEST
    paths = []
    for i, ln in enumerate(lines):
        if i % 20 == 0:
            on_stage(f"配音 {i}/{len(lines)} 句")
        voice = host_voice if ln.get("who") == host_who else guest_voice
        p = os.path.join(dest, f"{i:04d}.mp3")
        await edge_tts.Communicate(ln["text"], voice, rate=kl.PODCAST_RATE).save(p)
        paths.append(p)
    return paths


def _stitch(paths: list, out_path: str) -> None:
    """④ ffmpeg 把小 mp3 接成一集。同步的，呼叫端要丟執行緒。"""
    exe = shutil.which("ffmpeg") or "ffmpeg"
    lst = out_path + ".txt"
    with open(lst, "w", encoding="utf-8") as f:
        for p in paths:
            f.write("file '" + p.replace("\\", "/") + "'\n")
    try:
        r = subprocess.run([exe, "-y", "-f", "concat", "-safe", "0", "-i", lst,
                            "-c", "copy", out_path], capture_output=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError("接音檔失敗："
                               + (r.stderr or b"")[-300:].decode("utf-8", "replace"))
    finally:
        try:
            os.unlink(lst)
        except OSError:
            pass


async def run_podcast(book_id: str, n: int, model: str = "") -> None:
    """背景：寫稿 → 配音 → 接起來 → 落檔。失敗就把進度字串換成錯誤訊息再清掉。"""
    n = int(n)
    key = key_of(book_id, n)
    tmp = ""

    def on_stage(s: str) -> None:
        _making[key] = s

    try:
        model = ks.pick_model(model)
        lines = await _write_script(book_id, n, model, on_stage)
        dest = podcast_dir(book_id, make=True)
        tmp = tempfile.mkdtemp(prefix="kbpod_")
        paths = await _speak(lines, tmp, on_stage)
        on_stage("接起來")
        out = os.path.join(dest, kl.podcast_name(n))
        await asyncio.to_thread(_stitch, paths, out)
        # 音檔成功才寫逐字稿 —— 反過來的話會留下「有稿沒聲音」的半成品
        with open(os.path.join(dest, kl.podcast_name(n, "json")), "w", encoding="utf-8") as f:
            json.dump(lines, f, ensure_ascii=False, indent=1)
    except Exception as e:                       # noqa: BLE001 — 背景工作，錯誤要留給畫面看
        _making[key] = f"失敗：{str(e)[:120]}"
        await asyncio.sleep(20)                  # 讓畫面輪詢得到那句話再消失
    finally:
        _making.pop(key, None)
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def public_podcast(share_id: str, n: int) -> Optional[str]:
    """公開分享頁要聽的那一集。**沒勾「可以聽 podcast」就一律 None**。"""
    from services import knowledge_share as kshare
    book_id = kshare.book_of(share_id)
    if not book_id or not kl.shares(kshare.shared_parts(book_id), "podcast"):
        return None
    try:
        path = podcast_path(book_id, n)
    except Exception:
        return None
    return path if os.path.isfile(path) else None
