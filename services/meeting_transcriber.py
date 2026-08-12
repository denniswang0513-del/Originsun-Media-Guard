"""services/meeting_transcriber.py — 會議錄音 → 逐字稿 → AI 整理成會議記錄。

流程（經 core.bg_task.fire，上傳端點起完就回）：ffmpeg 抽 16k 單聲道 wav →
faster-whisper 逐字稿（寫回 transcript + 錄音檔旁落一份 .txt）→ claude 整理
（寫回 ai_summary；**content 空才代填** —— 人寫過的筆記絕不覆蓋）。

🔴 逐字稿是 AI 聽寫，**不是**會議的正本 —— 整理端的鐵則同企劃書/報價分析：
逐字稿裡沒有的不准編，缺的寫「（資料中未見）」。

🔴 whisper 是 CPU 重活（實測約 10–25 分鐘/小時錄音），`_LOCK` 讓**會議錄音
之間**一次只跑一件（語音辨識 Tab 的任務走 task_queue 另一條線，本來就串行，
最多同時各一件）。跑得比 settle 的 STALE_AFTER 久 → 整段包
`core.bg_status.keepalive`（心跳 + phase 進度字都由它落 DB）。

狀態生命週期同企劃書：pending/ok/failed + 讀取端 `core.bg_status.settle` 自癒；
寫入端一律走 `save_job_row` / `run_claude_job`（core/bg_status.py 的唯一正本）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

_MODEL_SIZE = "turbo"                  # 與語音辨識 Tab 同款（模型已在 models/ 快取）
_LOCK = asyncio.Lock()                 # 會議錄音一次一件（見檔頭）
_PROMPT_TRANSCRIPT_CHARS = 50000       # claude 一趟吃得下的量；超過截斷並明講
_TXT_SUFFIX = ".逐字稿.txt"

_PROMPT = """你是一位影像製作公司的製片，負責把會議錄音的逐字稿整理成一份會議記錄。

────────────── 會議基本資料（人填的，可能有空欄） ──────────────
{background}
────────────── 錄音逐字稿（AI 語音辨識，可能有錯字） ──────────────
{transcript}
────────────── 資料結束 ──────────────

請輸出**繁體中文 Markdown** 的會議記錄，依序包含這幾節：

## 會議摘要
三到五句話講完這場會議談了什麼、走向如何。

## 討論重點
逐點列出實際談到的主題與各方說法。

## 決議事項
會中明確定案的事。沒有明確決議就寫「（資料中未見明確決議）」。

## 待辦事項
誰、要做什麼、何時 —— 逐字稿裡有講才寫。沒有就寫「（資料中未見待辦）」。

🔴 鐵則：
1. **只根據逐字稿與基本資料寫。** 逐字稿裡沒有的人名、數字、日期、承諾一律
   不准編；缺的寫「（資料中未見）」。
2. 語音辨識的同音錯字可以依上下文改正，但**不可以**因此增加新內容。
3. 直接輸出 Markdown 本文，不要開場白、不要程式碼圍欄。
"""


def transcript_txt_path(audio_path: str) -> str:
    """錄音檔旁那份逐字稿 .txt 的路徑。"""
    return os.path.splitext(audio_path)[0] + _TXT_SUFFIX


def disk_artifacts(audio: str) -> tuple:
    """一個錄音在磁碟上擁有的**全部**檔案（錄音本體 + 逐字稿 .txt）。
    寫入端（本檔）與清理端（router 的換檔/刪列）共用這一份定義 ——
    以後多一種 sidecar 只改這裡，清理端不會漏。

    純字串運算，絕對路徑或**相對路徑**都吃 —— 清理端拿的是 rel，好讓每一條
    都各自經過 `proposal_assets` 那道「檔案能不能離開共用磁碟」的判斷。"""
    return (audio, transcript_txt_path(audio))


def _whisper_transcribe(wav_path: str, progress: dict) -> str:
    """faster-whisper → 「[時間] 一句」逐行。progress['phase'] 由 keepalive
    的心跳落 DB。"""
    from core.whisper_helpers import cleanup_gpu, format_srt_timestamp, load_model
    model = load_model(_MODEL_SIZE)
    try:
        segments, info = model.transcribe(wav_path, beam_size=5)
        lines = []
        for seg in segments:
            text = seg.text.strip()
            if text:
                lines.append(f"[{format_srt_timestamp(seg.start)}] {text}")
            if info.duration:
                progress["phase"] = f"辨識中 {min(99, int(seg.end / info.duration * 100))}%"
        return "\n".join(lines)
    finally:
        del model
        cleanup_gpu()


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8-sig") as f:   # BOM：同事雙擊記事本開不亂碼
        f.write(text)


def _background(ctx: dict) -> str:
    note = (ctx.get("content") or "").strip()
    return "\n".join([
        f"- 會議主題：{ctx.get('title') or '（未填）'}",
        f"- 會議日期：{ctx.get('met_at') or '（未填）'}",
        f"- 出席者：{ctx.get('attendees') or '（未填）'}",
        f"- 既有的人工筆記：{note[:2000] or '（未填）'}",
    ])


async def _fill_empty_content(mid: str) -> None:
    """AI 整理成功後，content **當下還全空**才代填一次 —— 讀活列不讀快照
    （處理期間有人動手寫了就一個字都不碰）。"""
    from core.db_guard import db_factory_or_503
    from db.models import PreprodMeetingNote
    factory = db_factory_or_503()
    async with factory() as session:
        row = await session.get(PreprodMeetingNote, mid)
        if (not row or row.status != "ok" or not (row.ai_summary or "").strip()
                or (row.content or "").strip()):
            return
        row.content = row.ai_summary
        await session.commit()


async def summarize_meeting(mid: str, *, transcript: str, ctx: dict) -> None:
    """逐字稿 → claude 整理（可單獨重跑 —— claude 那段失敗不必重付一小時的
    whisper）。寫 ai_summary；成功後視情況代填 content。"""
    from core.bg_status import run_claude_job
    from db.models import PreprodMeetingNote
    text = transcript
    if len(text) > _PROMPT_TRANSCRIPT_CHARS:
        text = (text[:_PROMPT_TRANSCRIPT_CHARS]
                + "\n（⚠️ 逐字稿過長，只截取前段 —— 後段未納入整理）")
    prompt = _PROMPT.format(background=_background(ctx), transcript=text)
    await run_claude_job(PreprodMeetingNote, mid, prompt,
                         field="ai_summary", tag="meeting_transcriber")
    await _fill_empty_content(mid)


async def process_meeting_audio(mid: str, *, audio_path: str, ctx: dict) -> None:
    """完整流程（經 bg_task.fire，回傳值沒人接）。ctx = 上傳當下的
    {title, met_at, attendees, content} 快照，只進 prompt 背景段。"""
    from core.bg_status import keepalive, save_job_row
    from core.whisper_helpers import extract_wav
    from db.models import PreprodMeetingNote
    progress = {"phase": ""}

    async def _phase(p: str, **fields) -> None:
        # dict 給心跳讀、DB 給 UI 讀 —— **一律經這裡**兩邊一起換，不然畫面
        # 會停在上一階段（`fields` 讓「換階段順便存結果」也走同一條路）
        progress["phase"] = p
        await save_job_row(PreprodMeetingNote, mid, phase=p, **fields)

    tmp_wav = ""
    try:
        # 心跳只負責讓長工不被 settle 誤判；phase 欄的內容是本模組的事
        async with keepalive(PreprodMeetingNote, mid,
                             lambda: {"phase": progress["phase"]}):
            # 抽音訊在 _LOCK 之外 —— 它只是解碼 I/O，不用排在前一件的
            # 幾十分鐘 whisper 後面（NAS 讀取也趁早做）
            await _phase("抽取音訊")
            fd, tmp_wav = tempfile.mkstemp(suffix=".wav", prefix="meet_")
            os.close(fd)
            err = await asyncio.to_thread(extract_wav, audio_path, tmp_wav)
            if err:
                await save_job_row(PreprodMeetingNote, mid,
                                   status="failed", error=err)
                return
            await _phase("排隊中（等前一件錄音轉完）")
            async with _LOCK:
                await _phase("辨識中 0%（長錄音可能要幾十分鐘）")
                transcript = await asyncio.to_thread(
                    _whisper_transcribe, tmp_wav, progress)
            if not transcript.strip():
                await save_job_row(PreprodMeetingNote, mid, status="failed",
                                   error="辨識不到任何語音內容 —— 檔案可能沒有人聲")
                return
            await _phase("AI 整理中", transcript=transcript)
            try:                                # best-effort：NAS 搆不到不擋整理
                await asyncio.to_thread(_write_text,
                                        transcript_txt_path(audio_path), transcript)
            except OSError:
                logger.warning("[meeting_transcriber] %s 逐字稿 .txt 落不了 NAS", mid)
            await summarize_meeting(mid, transcript=transcript, ctx=ctx)
    except Exception as e:                      # noqa: BLE001 — 不寫回就永遠 pending
        logger.exception("[meeting_transcriber] %s 掛了", mid)
        await save_job_row(PreprodMeetingNote, mid, status="failed",
                           error=f"{type(e).__name__}: {e}")
    finally:
        if tmp_wav:
            try:
                os.remove(tmp_wav)
            except OSError:
                pass
