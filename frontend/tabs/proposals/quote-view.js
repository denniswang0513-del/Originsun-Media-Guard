/**
 * quote-view.js — 提案的「報價單」分頁（CRM 報價唯讀 + 外部報價檔多版本 + AI 分析）。
 *
 * 三段式：CRM 報價管理的版本（唯讀鏡像，正本在報價 Tab）、上傳的外部報價檔
 * （多版本、備註可編、可下載刪除）、以及「分析各版差異」（Claude 背景跑，
 * pollJob 輪詢收斂）。
 *
 * 兩個介面共用（後台提案庫的詳情、獨立企劃頁），所以照 brief-view 那三條：
 * 只 import js/shared 與同目錄、自帶 --qv-* 變數、外殼由呼叫端給。
 *
 * 🔴 金額敏感：公開 ?t= 訪客模式**不掛**這個元件 —— 呼叫端連分頁鈕都不建
 * （比照企劃書分頁；唯讀不是靠隱藏元素，是靠沒建出來）。
 */

import { autosaveDelegated, syncBaseline } from '../../js/shared/autosave.js';
import { pollJob } from '../../js/shared/poll-job.js';
import { authDownload, ensureStyle, esc } from '../../js/shared/utils.js';
import { tfetch } from './prop-fetch.js';

const API = '/api/v1/crm';

/**
 * @param host  掛載容器（會被清空）
 * @param opts.proposalId
 */
export async function renderQuotes(host, { proposalId }) {
    ensureStyle('qv-style', STYLE);
    host.classList.add('qv');
    host.__qv = { proposalId, data: null, polling: new Set() };
    // 備註走共用 autosave（債等：debounce + 失敗退基準重試 + 重畫不重綁）。
    // 委派綁在 host 上一次就好 —— 清單每次動作都整塊重畫，逐顆綁必漏
    autosaveDelegated(host, '.qv-notein', (v, el) =>
        tfetch(`${_base(proposalId)}/${encodeURIComponent(el.dataset.note)}`,
               { method: 'PATCH', json: { note: v } }),
        { onError: (e) => alert('備註儲存失敗：' + (e.message || e)) });
    await _load(host);
}

const S = (h) => h.__qv;
const _base = (pid) => `${API}/proposals/${encodeURIComponent(pid)}/quotes`;

async function _load(host) {
    const s = S(host);
    try {
        const d = await tfetch(_base(s.proposalId));
        // 欄位缺漏防禦性補齊 —— 這支端點另一頭還在長
        s.data = { files: d.files || [], crm: d.crm_quotations || [],
                   analysis: d.analysis || null };
    } catch (e) {
        host.innerHTML = `<div class="qv-note qv-err">載入失敗：${esc(e.message || e)}</div>`;
        return;
    }
    _render(host);
}

const _day = (iso) => (iso ? String(iso).slice(0, 10) : '');
// 千分位。金額缺（後端欄位還沒到位、或報價還沒填）就照實顯示「—」，不編數字。
const _money = (v) => (v === null || v === undefined || v === '' || isNaN(Number(v)))
    ? '—' : 'NT$ ' + Number(v).toLocaleString('en-US');

function _render(host) {
    const s = S(host);
    const { files, crm, analysis } = s.data;
    // AI 分析要有東西可比：檔 + CRM 版本合計 ≥ 2 才開放
    const total = files.length + crm.length;

    const crmSec = crm.length ? `
        <div class="qv-sec">
            <div class="qv-h">CRM 報價 <span class="qv-note">來自 CRM 報價管理（唯讀）</span></div>
            ${crm.map(q => `
                <div class="qv-row">
                    <span class="qv-ver">v${esc(q.version ?? '')}</span>
                    <span class="qv-note qv-st">${esc(q.status || '')}</span>
                    <span class="qv-amt">${_money(q.final_price ?? q.total)}</span>
                    <span class="qv-note">${esc(q.quote_date || '')}</span>
                </div>`).join('')}
        </div>` : '';

    // 新在上（後端 version desc；照拿不重排）
    const fileRows = files.map(f => `
        <div class="qv-file">
            <div class="qv-fline">
                <span class="qv-ver">v${esc(f.version ?? '')}</span>
                <span class="qv-name">${esc(f.filename)}</span>
                <span class="qv-note">${esc(_day(f.created_at))}</span>
                <span class="qv-gap"></span>
                <button class="qv-btn sm" data-dl="${esc(String(f.id))}">下載</button>
                <button class="qv-btn sm" data-del="${esc(String(f.id))}">刪除</button>
            </div>
            <input class="qv-notein" data-note="${esc(String(f.id))}"
                   placeholder="備註（打完自動儲存）">
        </div>`).join('');

    host.innerHTML = `
        ${crmSec}
        <div class="qv-sec">
            <div class="qv-h">上傳報價單</div>
            <div class="qv-bar">
                <button class="qv-btn primary" data-up>上傳報價單</button>
                <input type="file" class="qv-upfile" style="display:none;">
                <span class="qv-note">上限 50MB；每次上傳是新的一版，不覆蓋舊版。</span>
            </div>
            ${fileRows || '<div class="qv-note">還沒有上傳的報價檔。</div>'}
        </div>
        <div class="qv-sec">
            <div class="qv-h">AI 分析</div>
            <div class="qv-bar">
                <button class="qv-btn" data-analyze${total < 2 ? ' disabled' : ''}
                    title="${total < 2
                        ? '至少要有 2 份報價（上傳檔 + CRM 版本合計）才有差異可以比'
                        : '叫 Claude 比對各版報價的差異'}">分析各版差異</button>
                ${analysis && analysis.status === 'ok'
                    ? '<span class="qv-note">重新分析會取代下面的結果。</span>' : ''}
            </div>
            <div class="qv-ana"></div>
        </div>`;

    _wireUpload(host);
    _wireFiles(host, files);
    _wireAnalysis(host, analysis);
}

// ── 上傳 ─────────────────────────────────────────────────

function _wireUpload(host) {
    const s = S(host);
    const btn = host.querySelector('[data-up]');
    const input = host.querySelector('.qv-upfile');
    btn.addEventListener('click', () => input.click());
    input.addEventListener('change', async () => {
        const f = input.files[0];
        if (!f) return;
        btn.disabled = true;
        btn.textContent = '上傳中…';
        const fd = new FormData();
        fd.append('file', f);
        try {
            await tfetch(_base(s.proposalId) + '/upload', { method: 'POST', body: fd });
            await _load(host);      // 成功整塊重畫（新版本列出現就是回饋）
        } catch (e) {
            alert('上傳失敗：' + (e.message || e));   // 400 沒資產夾 / 413 超過 50MB 照實顯示
            btn.disabled = false;
            btn.textContent = '上傳報價單';
            input.value = '';
        }
    });
}

// ── 檔案列（下載 / 刪除 / 備註 blur 儲存）──────────────────

function _wireFiles(host, files) {
    const s = S(host);
    const byId = {};
    files.forEach(f => { byId[String(f.id)] = f; });

    host.querySelectorAll('[data-dl]').forEach(el => {
        el.addEventListener('click', () => {
            const f = byId[el.dataset.dl];
            authDownload(`${_base(s.proposalId)}/${encodeURIComponent(el.dataset.dl)}/download`,
                         f.filename, '報價單下載');
        });
    });
    host.querySelectorAll('[data-del]').forEach(el => {
        el.addEventListener('click', async () => {
            const f = byId[el.dataset.del];
            if (!confirm(`刪除報價檔 v${f.version}？其他版本不受影響。`)) return;
            try {
                await tfetch(`${_base(s.proposalId)}/${encodeURIComponent(el.dataset.del)}`,
                             { method: 'DELETE' });
                await _load(host);
            } catch (e) { alert('刪除失敗：' + (e.message || e)); }
        });
    });
    // 備註值走 DOM property（不進模板字串 — XSS 防線）；送出由 renderQuotes
    // 綁的委派 autosave 接手，這裡只要把重畫後的基準對齊
    host.querySelectorAll('[data-note]').forEach(el => {
        el.value = (byId[el.dataset.note] || {}).note || '';
    });
    syncBaseline(host, '.qv-notein');
}

// ── AI 分析（生成 + 輪詢 + 顯示）──────────────────────────

function _wireAnalysis(host, analysis) {
    const s = S(host);
    const box = host.querySelector('.qv-ana');
    const btn = host.querySelector('[data-analyze]');

    if (analysis && analysis.status === 'pending') {
        btn.disabled = true;
        box.innerHTML = '<div class="qv-note">分析中…（跑 Claude，約一到三分鐘）</div>';
        // 輪詢帶 ?only=analysis（只要 status，別讓後端每 5 秒陪跑 files/crm
        // 兩條 query）；收斂那一 tick 手上就是完整的 analysis（含 content）——
        // 就地換上重畫，不再全量重抓（files/crm 在分析期間不會變）
        pollJob(analysis.id, s.polling, {
            alive: () => host.isConnected,       // 切走分頁就停
            list: async () => {
                const d = await tfetch(_base(s.proposalId) + '?only=analysis');
                return d.analysis ? [d.analysis] : [];
            },
            onSettled: (items) => {
                if (!host.isConnected) return;
                if (items && items[0]) { s.data.analysis = items[0]; _render(host); }
                else return _load(host);         // 那一列不見了（被刪/換新）才全量重抓
            },
        });
    } else if (analysis && analysis.status === 'failed') {
        box.innerHTML = `<div class="qv-note qv-err">分析失敗：${esc(analysis.error || '')}</div>`;
    } else if (analysis && analysis.status === 'ok') {
        // Markdown 原文照畫（escape + pre-wrap）—— 與企劃書分頁同一套顯示法，
        // 不在前端拉 Markdown 渲染器
        box.innerHTML = `
            <div class="qv-note" style="margin-bottom:4px;">${esc(_day(analysis.created_at))}
                ${esc(_inputsLabel(analysis.inputs))}</div>
            <div class="qv-md">${esc(analysis.content || '')}</div>`;
    } else {
        box.innerHTML = '<div class="qv-note">還沒有分析 —— 湊滿兩份報價後按上面的按鈕。</div>';
    }

    btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
            // POST 回應就帶 pending 那筆 —— 就地換上，files/crm 這一刻不會變
            const d = await tfetch(_base(s.proposalId) + '/analyze', { method: 'POST' });
            s.data.analysis = d.analysis;
            _render(host);          // → pending 分支開始輪詢
        } catch (e) {
            alert('分析失敗：' + (e.message || e));
            btn.disabled = false;
        }
    });
}

/** 「本次分析納入：上傳 v2、v1；CRM v3、v2」—— 檔案與報價都會繼續長，
 *  不標的話這份分析講的是什麼永遠說不清（inputs 快照就是為此存的）。 */
function _inputsLabel(inputs) {
    const vs = (list) => (list || []).map(x => 'v' + x.version).join('、');
    const parts = [];
    if (inputs?.files?.length) parts.push('上傳 ' + vs(inputs.files));
    if (inputs?.crm_quotations?.length) parts.push('CRM ' + vs(inputs.crm_quotations));
    return parts.length ? `｜本次納入：${parts.join('；')}` : '';
}

const STYLE = `
.qv { --qv-ink: #ddd; --qv-sub: #8b8b8b; --qv-line: #2a2a2a; --qv-card: #161616;
      --qv-accent: #c9372c; --qv-err: #f87171; color: var(--qv-ink); }
html.plan-theme-light .qv { --qv-ink: #262626; --qv-sub: #737373; --qv-line: #e5e5e5;
      --qv-card: #fafafa; --qv-accent: #c9372c; --qv-err: #d33; }
.qv * { box-sizing: border-box; }
.qv-gap { flex: 1; }
.qv-note { font-size: 11.5px; color: var(--qv-sub); line-height: 1.7; }
.qv-err { color: var(--qv-err); }
.qv-sec { margin-bottom: 18px; }
.qv-h { font-size: 13px; font-weight: 600; letter-spacing: .05em; margin-bottom: 8px;
      display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.qv-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.qv-btn { border: 1px solid var(--qv-line); background: none; cursor: pointer;
      color: var(--qv-ink); font: inherit; font-size: 12.5px; padding: 6px 12px; border-radius: 3px; }
.qv-btn:hover:not(:disabled) { border-color: var(--qv-accent); color: var(--qv-accent); }
.qv-btn.primary { border-color: var(--qv-accent); color: var(--qv-accent); }
.qv-btn.sm { font-size: 11.5px; padding: 3px 9px; }
.qv-btn:disabled { opacity: .4; cursor: default; }
.qv-row { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
      padding: 6px 8px; border-bottom: 1px dashed var(--qv-line); font-size: 12.5px; }
.qv-row:last-child { border-bottom: none; }
.qv-ver { flex: none; font-size: 12px; color: var(--qv-accent); min-width: 28px; }
.qv-st { min-width: 48px; }
.qv-amt { font-size: 13px; }
.qv-file { border: 1px solid var(--qv-line); border-radius: 3px; background: var(--qv-card);
      padding: 8px 10px; margin-bottom: 8px; }
.qv-fline { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
/* 檔名可能很長（NAS 上的中文命名），不給斷點會把整欄撐開（比照 .deck-name） */
.qv-name { font-size: 12.5px; word-break: break-all; min-width: 0; }
.qv-notein { width: 100%; margin-top: 6px; font: inherit; font-size: 12px;
      color: var(--qv-ink); background: transparent; border: 1px solid transparent;
      border-radius: 2px; padding: 4px 6px; outline: none; }
.qv-notein:hover { border-color: var(--qv-line); }
.qv-notein:focus { border-color: var(--qv-accent); }
.qv-md { border: 1px solid var(--qv-line); border-radius: 3px; background: var(--qv-card);
      padding: 12px 14px; font-size: 13px; line-height: 1.9; white-space: pre-wrap;
      word-break: break-word; }
`;
