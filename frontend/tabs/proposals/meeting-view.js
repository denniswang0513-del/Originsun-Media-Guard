/**
 * meeting-view.js — 提案的「會議記錄」分頁（多筆，逐欄自動儲存）。
 *
 * **沒有版本**：會議記錄是創意發想與企劃書的輸入素材，不是產出物。AI 只做
 * **輸入輔助**：每筆可傳一個會議錄音檔 → 後端 whisper 逐字稿 → claude 整理
 * （content 空才代填；人寫過的字後端絕不覆蓋）。
 *
 * 兩個介面共用（後台提案庫的詳情、獨立企劃頁），所以照 brief-view / quote-view
 * 那三條：只 import js/shared 與同目錄、自帶 --mv-* 變數、外殼由呼叫端給。
 *
 * 🔴 內部資料：公開 ?t= 訪客模式**不掛**這個元件 —— 呼叫端連分頁鈕都不建
 * （比照企劃書/報價單；唯讀不是靠隱藏元素，是靠沒建出來）。
 */

import { autosaveDelegated, syncBaseline } from '../../js/shared/autosave.js';
import { pollJob } from '../../js/shared/poll-job.js';
import { fmtSize } from '../../js/shared/clip_utils.js';
import { authDownload, bearerHeader, ensureStyle, esc, proxyBodyLimit,
         uploadWithProgress } from '../../js/shared/utils.js';
import { tfetch } from './prop-fetch.js';

const API = '/api/v1/crm';
const _base = (pid) => `${API}/proposals/${encodeURIComponent(pid)}/meetings`;

/**
 * @param host  掛載容器（會被清空）
 * @param opts.proposalId
 */
export async function renderMeetings(host, { proposalId }) {
    ensureStyle('mv-style', STYLE);
    host.classList.add('mv');
    host.__mv = { proposalId, notes: [], polling: new Set() };
    // 逐欄自動儲存走共用件（debounce + 失敗退基準重試 + 重畫不重綁）。
    // 委派綁在 host 上一次就好 —— 清單每次動作都整塊重畫，逐顆綁必漏。
    autosaveDelegated(host, '[data-f]', (v, el) =>
        tfetch(`${_base(proposalId)}/${encodeURIComponent(el.dataset.id)}`,
               { method: 'PATCH', json: { [el.dataset.f]: v } }),
        { onError: (e) => alert('儲存失敗：' + (e.message || e)) });
    await _load(host);
}

async function _load(host) {
    const s = host.__mv;
    try {
        s.notes = (await tfetch(_base(s.proposalId))).notes || [];
    } catch (e) {
        host.innerHTML = `<div class="mv-note mv-err">載入失敗：${esc(e.message || e)}</div>`;
        return;
    }
    _render(host);
}

function _render(host) {
    const s = host.__mv;
    host.innerHTML = `
        <div class="mv-bar">
            <button class="mv-btn primary" data-add>新增會議記錄</button>
            <span class="mv-note">共 ${s.notes.length} 筆；打完字自動儲存。</span>
        </div>
        ${s.notes.map(_cardHtml).join('') ||
          '<div class="mv-note">還沒有會議記錄 —— 開會前先按上面新增一筆，邊聽邊記。</div>'}`;
    _wire(host);
}

/** 值一律走 DOM property 餵（不進模板字串）—— 這裡裝的是自由文字。 */
function _cardHtml(m) {
    const id = esc(String(m.id));
    return `
        <div class="mv-card" data-card="${id}">
            <div class="mv-head">
                <input type="date" class="mv-in mv-date" data-f="met_at" data-id="${id}">
                <input class="mv-in mv-title" data-f="title" data-id="${id}"
                       placeholder="會議主題">
                <span class="mv-gap"></span>
                <button class="mv-btn sm" data-del="${id}">刪除</button>
            </div>
            <input class="mv-in mv-att" data-f="attendees" data-id="${id}"
                   placeholder="出席者（自由填，例：客戶王經理、導演、製片）">
            <textarea class="mv-in mv-body" data-f="content" data-id="${id}"
                      placeholder="會議記錄…"></textarea>
            <div class="mv-audio"></div>
            <div class="mv-note mv-by"></div>
        </div>`;
}

// ── 錄音 → 逐字稿 → AI 整理 ─────────────────────────────────

function _audioHtml(m) {
    if (m.status === 'pending') {
        return `
            <div class="mv-abar">
                <span class="mv-spin"></span>
                <span class="mv-note">處理中：${esc(m.phase || '…')} ——
                    可以先離開這頁，回來再看。</span>
            </div>`;
    }
    // 一條 bar，兩種內容（結構只寫一次）
    const parts = [`<div class="mv-abar">${m.audio_name ? `
            <span class="mv-note mv-aname">${esc(m.audio_name)}</span>
            <span class="mv-gap"></span>
            <button class="mv-btn sm" data-adl>下載錄音</button>
            ${m.has_transcript ? '<button class="mv-btn sm" data-resum>重跑 AI 整理</button>' : ''}
            <button class="mv-btn sm" data-aup>重新上傳</button>` : `
            <button class="mv-btn sm" data-aup>上傳會議錄音</button>
            <span class="mv-note">AI 會轉成逐字稿並整理成會議記錄
                （上面欄位是空的才代填，寫過的字不會被動到）。</span>`}</div>`];
    if (m.status === 'failed') {
        parts.push(`<div class="mv-note mv-err">處理失敗：${esc(m.error || '')}</div>`);
    }
    // 全文不隨清單一起載（一小時錄音幾十 KB）—— 展開時才打單筆 GET
    if (m.has_transcript) parts.push(_foldHtml('transcript', '逐字稿', m));
    if (m.has_summary) parts.push(_foldHtml('summary', 'AI 整理', m));
    return parts.join('');
}

const _FOLD_FIELD = { transcript: 'transcript', summary: 'ai_summary' };

function _foldHtml(kind, label, m) {
    const body = m[_FOLD_FIELD[kind]];      // 單筆 GET 回來的才有值
    return `
        <details class="mv-fold" data-fold="${kind}"><summary>${label}</summary>
            <div class="mv-pre">${body ? esc(body) : '載入中…'}</div>
        </details>`;
}

function _wireAudio(host, card, m) {
    const s = host.__mv;
    const mid = String(m.id);
    card.querySelector('[data-aup]')?.addEventListener('click', () => {
        if (m.audio_name &&
            !confirm('重新上傳會換掉這一筆的錄音、逐字稿與 AI 整理（手寫的欄位不受影響）。繼續？')) return;
        const input = document.createElement('input');
        input.type = 'file';
        // 白名單與後端一致（後端仍會再驗一次）
        input.accept = '.mp3,.wav,.m4a,.aac,.flac,.ogg,.opus,.webm,.mp4,.mov,.mkv,.mts';
        input.addEventListener('change', () => {
            if (input.files[0]) _uploadAudio(host, card, mid, input.files[0]);
        });
        input.click();
    });
    card.querySelector('[data-adl]')?.addEventListener('click', () => {
        authDownload(`${_base(s.proposalId)}/${encodeURIComponent(mid)}/audio/download`,
                     m.audio_name, '錄音下載');
    });
    card.querySelector('[data-resum]')?.addEventListener('click', async (e) => {
        e.target.disabled = true;
        try {
            const d = await tfetch(`${_base(s.proposalId)}/${encodeURIComponent(mid)}/summarize`,
                                   { method: 'POST' });
            _refreshCard(host, d.note);
            _poll(host, mid);           // 這裡才是「剛變成 pending」的地方
        } catch (err) { alert('重跑失敗：' + (err.message || err)); e.target.disabled = false; }
    });
    // 展開才載全文（清單那趟只給 has_*）；載過就不再打
    card.querySelectorAll('[data-fold]').forEach(el => {
        el.addEventListener('toggle', async () => {
            const field = _FOLD_FIELD[el.dataset.fold];
            const cur = s.notes.find(n => String(n.id) === mid) || {};
            if (!el.open || cur[field]) return;
            try {
                const d = await tfetch(`${_base(s.proposalId)}/${encodeURIComponent(mid)}`);
                Object.assign(cur, d.note || {});
                el.querySelector('.mv-pre').textContent = cur[field] || '（沒有內容）';
            } catch (e) {
                el.querySelector('.mv-pre').textContent = '載入失敗：' + (e.message || e);
            }
        });
    });
}

async function _uploadAudio(host, card, mid, f) {
    const s = host.__mv;
    const box = card.querySelector('.mv-audio');
    // 遠端（foundry 隧道）單一請求 100MB 硬上限 —— 與其讓 Cloudflare 回一頁
    // 空白 413，不如先講清楚
    const cap = proxyBodyLimit();
    if (cap && f.size > cap) {
        alert(`遠端連線的單次上傳上限是 ${fmtSize(cap)} —— 請在公司內網上傳`
              + '這個檔，或先把影片轉成純音檔再傳。');
        return;
    }
    box.innerHTML = '<div class="mv-abar"><span class="mv-spin"></span>'
                  + '<span class="mv-note mv-uppct">上傳中…</span></div>';
    const pct = box.querySelector('.mv-uppct');
    const fd = new FormData();
    fd.append('file', f);
    try {
        const d = await uploadWithProgress(
            `${_base(s.proposalId)}/${encodeURIComponent(mid)}/audio`, fd, {
                headers: bearerHeader(),
                onProgress: (loaded, total) => {
                    if (total) pct.textContent = `上傳中… ${Math.round(loaded / total * 100)}%`;
                },
                onUploaded: () => { pct.textContent = '上傳完成，等伺服器落檔…'; },
            });
        _refreshCard(host, d.note);
        _poll(host, mid);               // 這裡才是「剛變成 pending」的地方
    } catch (e) {
        alert('上傳失敗：' + (e.message || e));   // 400 沒資產夾 / 413 超限 / 422 副檔名照實顯示
        const cur = s.notes.find(n => String(n.id) === mid);
        if (cur) _paintAudio(host, card, cur);
    }
}

/** 開始追一筆處理中的錄音。**只從「剛變成 pending」的地方呼叫**（上傳成功、
 *  重跑整理、初次載入時已在跑的）—— 從重畫路徑呼叫會讓上限自動續約。 */
function _poll(host, mid) {
    const s = host.__mv;
    pollJob(mid, s.polling, {
        alive: () => host.isConnected,
        // 一小時的上限：後端進度字幾十秒才換一次，15 秒問一次夠靈敏也不燒連線
        maxTicks: 240,
        tickMs: 15000,
        list: async () => {
            const d = await tfetch(`${_base(s.proposalId)}/${encodeURIComponent(mid)}`);
            return d.note ? [d.note] : [];
        },
        onSettled: (items) => {
            if (host.isConnected && items[0]) _refreshCard(host, items[0]);
        },
    });
}

/** 錄音區的畫＋綁（首次渲染與輪詢刷新共用）。 */
function _paintAudio(host, card, m) {
    card.querySelector('.mv-audio').innerHTML = _audioHtml(m);
    _wireAudio(host, card, m);
}

/** 只換這張卡的錄音區（整清單重畫會把別張卡打字中的欄位掀掉）。 */
function _refreshCard(host, note) {
    const s = host.__mv;
    const i = s.notes.findIndex(n => String(n.id) === String(note.id));
    if (i >= 0) s.notes[i] = note;
    const card = host.querySelector(`.mv-card[data-card="${CSS.escape(String(note.id))}"]`);
    if (!card) return;
    _paintAudio(host, card, note);
    // AI 代填 content：欄位還空著、也沒人正在打，才帶上（基準一起對齊，
    // 免得 autosave 把 AI 填的那份又送回去一次）
    const ta = card.querySelector('textarea[data-f="content"]');
    if (ta && !ta.value.trim() && note.content && document.activeElement !== ta) {
        ta.value = note.content;
        ta._asSaved = ta.value;
        _grow(ta);
    }
}

function _wire(host) {
    const s = host.__mv;
    const byId = {};
    s.notes.forEach(m => { byId[String(m.id)] = m; });

    host.querySelector('[data-add]').addEventListener('click', async (e) => {
        e.target.disabled = true;
        try {
            await tfetch(_base(s.proposalId), { method: 'POST', json: {} });
            await _load(host);
        } catch (err) {
            alert('新增失敗：' + (err.message || err));
            e.target.disabled = false;
        }
    });

    host.querySelectorAll('[data-del]').forEach(el => {
        el.addEventListener('click', async () => {
            const m = byId[el.dataset.del];
            const what = (m.title || '').trim() || m.met_at || '這一筆';
            if (!confirm(`刪除會議記錄「${what}」？`)) return;
            try {
                await tfetch(`${_base(s.proposalId)}/${encodeURIComponent(el.dataset.del)}`,
                             { method: 'DELETE' });
                await _load(host);
            } catch (e) { alert('刪除失敗：' + (e.message || e)); }
        });
    });

    // 值與「誰建的」在重畫後填回；textarea 隨內容長高（同企劃矩陣的手感）
    host.querySelectorAll('[data-f]').forEach(el => {
        const m = byId[el.dataset.id] || {};
        el.value = m[el.dataset.f] || '';
        if (el.tagName === 'TEXTAREA') {
            _grow(el);
            el.addEventListener('input', () => _grow(el));
        }
    });
    host.querySelectorAll('.mv-card').forEach(card => {
        const m = byId[card.dataset.card] || {};
        const by = card.querySelector('.mv-by');
        by.textContent = m.created_by
            ? `由 ${m.created_by} 建立於 ${String(m.created_at || '').slice(0, 10)}` : '';
        _paintAudio(host, card, m);
        // 進來時就已經在跑的（別人傳的、或自己上次關掉分頁前傳的）
        if (m.status === 'pending') _poll(host, String(m.id));
    });
    syncBaseline(host, '[data-f]');
}

/** 內容多長格子就多長（min-height 由 CSS 保底）。 */
function _grow(ta) {
    ta.style.height = 'auto';
    ta.style.height = ta.scrollHeight + 'px';
}

const STYLE = `
.mv { --mv-ink: #ddd; --mv-sub: #8b8b8b; --mv-line: #2a2a2a; --mv-card: #161616;
      --mv-accent: #c9372c; --mv-err: #f87171; color: var(--mv-ink); }
html.plan-theme-light .mv { --mv-ink: #262626; --mv-sub: #737373; --mv-line: #e5e5e5;
      --mv-card: #fafafa; --mv-accent: #c9372c; --mv-err: #d33; }
.mv * { box-sizing: border-box; }
.mv-gap { flex: 1; }
.mv-note { font-size: 11.5px; color: var(--mv-sub); line-height: 1.7; }
.mv-err { color: var(--mv-err); }
.mv-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.mv-btn { border: 1px solid var(--mv-line); background: none; cursor: pointer;
      color: var(--mv-ink); font: inherit; font-size: 12.5px; padding: 6px 12px; border-radius: 3px; }
.mv-btn:hover:not(:disabled) { border-color: var(--mv-accent); color: var(--mv-accent); }
.mv-btn.primary { border-color: var(--mv-accent); color: var(--mv-accent); }
.mv-btn.sm { font-size: 11.5px; padding: 3px 9px; }
.mv-btn:disabled { opacity: .4; cursor: default; }
.mv-card { border: 1px solid var(--mv-line); border-radius: 3px; background: var(--mv-card);
      padding: 10px 12px; margin-bottom: 10px; }
.mv-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
/* 可編控件：平時像文字，focus 才顯邊框（同基本資料的漸進揭露） */
.mv-in { font: inherit; color: var(--mv-ink); background: transparent;
      border: 1px solid transparent; border-radius: 2px; padding: 4px 6px; outline: none; }
.mv-in:hover { border-color: var(--mv-line); }
.mv-in:focus { border-color: var(--mv-accent); }
.mv-date { flex: none; font-size: 12.5px; color: var(--mv-sub); }
.mv-title { flex: 1; min-width: 160px; font-size: 13.5px; font-weight: 600; }
.mv-att { width: 100%; font-size: 12px; color: var(--mv-sub); }
.mv-body { width: 100%; font-size: 13px; line-height: 1.9; min-height: 88px;
      resize: none; overflow-y: hidden; }
.mv-by { margin-top: 4px; }
/* 錄音區 */
.mv-audio { margin-top: 6px; border-top: 1px dashed var(--mv-line); padding-top: 8px; }
.mv-abar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.mv-aname { word-break: break-all; min-width: 0; }
.mv-fold { margin-top: 6px; }
.mv-fold summary { font-size: 11.5px; color: var(--mv-sub); cursor: pointer;
      user-select: none; }
.mv-pre { border: 1px solid var(--mv-line); border-radius: 3px; margin-top: 4px;
      padding: 10px 12px; font-size: 12.5px; line-height: 1.9;
      white-space: pre-wrap; word-break: break-word; max-height: 320px; overflow-y: auto; }
.mv-spin { width: 12px; height: 12px; border: 2px solid var(--mv-line);
      border-top-color: var(--mv-accent); border-radius: 50%; flex: none;
      animation: mv-rot 1s linear infinite; }
@keyframes mv-rot { to { transform: rotate(360deg); } }
/* 手機：日期與主題各佔一行，觸控目標 44px（同 proposal-plan 的既有斷點） */
@media (max-width: 720px) {
  .mv-in { font-size: 16px; }
  .mv-title { min-width: 100%; }
  .mv-btn { min-height: 44px; }
}
`;
