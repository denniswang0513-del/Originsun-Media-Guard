/**
 * brief-view.js — 提案的「企劃書」分頁（多版 + 生成 + 選一段改寫）。
 *
 * 企劃矩陣是思考過程，這裡是輸出物。左邊版本清單、右邊正文；每一版都能就地
 * 編輯（草稿不是聖旨），「重新生成」是**新增一版**不覆蓋。
 *
 * 兩個介面共用（後台提案庫的詳情、獨立企劃頁），所以照 proposal-folders 那三條：
 * 只 import js/shared 與同目錄、自帶 --bv-* 變數、外殼由呼叫端給。
 *
 * 🔴 **矩陣的文字在這裡渲染**再送給後端。方法論的標籤（三視角 × 四提問）住在
 * plan-templates.js，後端只存 blob、不知道 key 對應的中文 —— 讓後端自己組就得
 * 複製一份方法論定義，改版時兩邊一定分岔。
 */

import { autosave } from '../../js/shared/autosave.js';
import { pollJob } from '../../js/shared/poll-job.js';
import { ensureStyle, esc } from '../../js/shared/utils.js';
import { PLAN_TEMPLATES } from './plan-templates.js';
import { field, openDialog } from './prop-dialog.js';
import { tfetch } from './prop-fetch.js';

const API = '/api/v1/crm';

export const LENGTHS = [
    ['brief', '精簡（一頁）'], ['full', '完整提案'], ['deep', '逐節詳寫'],
];
export const TONES = [['pitch', '提案說服（對客戶）'], ['plain', '實務直白（對內）']];
export const INCLUDES = [
    ['survey', '現況盤點表'], ['refs', '參考影片清單'],
    ['notes', '內部備註'], ['budget', '預算範圍'],
];
const REWRITES = [
    ['expand', '寫詳細'], ['shorten', '改精簡'],
    ['rephrase', '換說法'], ['softer', '語氣放軟'],
];

/**
 * 企劃矩陣 → 給 Claude 讀的文字。**空格明確標「（未填）」** —— 那是整個
 * 生成器最重要的一條：看得見缺口，它才不會自己把空格編滿。
 */
export function renderMatrixText(plan) {
    const tpl = PLAN_TEMPLATES[(plan || {}).template_id];
    if (!tpl) return '';
    const cells = (plan || {}).cells || {};
    const val = (lens, how) => {
        const v = ((cells[lens] || {})[how] || {});
        const s = (typeof v === 'string' ? v : (v.text || '')).trim();
        return s || '（未填）';
    };
    const out = [`THEME：${((plan || {}).theme || '').trim() || '（未填）'}`, ''];
    for (const how of tpl.hows) {
        out.push(`【${how.label}】${how.hint ? '　' + how.hint : ''}`);
        for (const lens of tpl.lenses) out.push(`· ${lens.label}：${val(lens.key, how.key)}`);
        out.push('');
    }
    const fv = ((plan || {}).field_values || {}).plan || {};
    const summary = (tpl.summary_fields || [])
        .map(f => `· ${f.label}：${(fv[f.key] || '').trim() || '（未填）'}`);
    if (summary.length) out.push(`【${tpl.summary_label || '取捨'}】`, ...summary, '');
    if (((plan || {}).memo || '').trim()) out.push('【備忘】', plan.memo.trim());
    return out.join('\n');
}

/**
 * @param host  掛載容器（會被清空）
 * @param opts.proposalId
 * @param opts.plan       目前的企劃矩陣（生成時渲染成文字送出）
 * @param opts.toast      可選；成功訊息怎麼顯示由呼叫端決定
 */
export async function renderBriefs(host, { proposalId, plan = null, toast = null }) {
    ensureStyle('bv-style', STYLE);
    host.classList.add('bv');
    host.__bv = { proposalId, plan, toast, items: [], open: '', polling: new Set() };
    await _load(host);
}

const S = (h) => h.__bv;

const _list = async (pid) =>
    (await tfetch(`${API}/proposals/${encodeURIComponent(pid)}/briefs`)).briefs || [];

async function _load(host) {
    const s = S(host);
    try {
        s.items = await _list(s.proposalId);
    } catch (e) {
        host.innerHTML = `<div class="bv-note bv-err">載入失敗：${esc(e.message || e)}</div>`;
        return;
    }
    // 開著的那版不在了（剛被刪掉）→ 改看成案/最新的那版
    if (!s.items.some(x => x.id === s.open)) {
        s.open = (s.items.find(x => x.status === 'ok') || s.items[0] || {}).id || '';
    }
    await _render(host);
}

const _when = (iso) => (iso ? String(iso).slice(0, 16).replace('T', ' ') : '');
const _statusLabel = { ok: '', pending: '生成中…', failed: '失敗' };

async function _render(host) {
    const s = S(host);
    // 重畫會換掉整個 textarea —— 先把還沒送出去的那一版催出去。debounce 期間
    // 切版本／輪詢收斂都會走到這裡，不催就是打完字直接掉。
    if (s.saver) { const sv = s.saver; s.saver = null; await sv.flush(); sv.dispose(); }
    host.innerHTML = `
        <div class="bv-bar">
            <button class="bv-btn primary" data-act="gen">✦ 用企劃矩陣生成</button>
            <button class="bv-btn" data-act="blank">＋ 空白版本</button>
            <span class="bv-gap"></span>
            <span class="bv-note">生成的是草稿：沒填的格子會被列進「待補」，不會替你想。</span>
        </div>
        ${s.items.length ? `
        <div class="bv-body">
            <div class="bv-side">
                ${s.items.map(b => `
                    <button class="bv-ver${b.id === s.open ? ' on' : ''}" data-ver="${esc(b.id)}">
                        <span class="bv-when">${esc(_when(b.created_at))}</span>
                        <span class="bv-meta">${esc(_statusLabel[b.status] ?? b.status)}${
                            b.status === 'ok' ? `${b.chars} 字` : ''}</span>
                    </button>`).join('')}
            </div>
            <div class="bv-main" id="bv-main"></div>
        </div>` : '<div class="bv-note">還沒有企劃書 —— 按上面的按鈕生成第一版。</div>'}`;

    host.querySelectorAll('[data-act]').forEach(el => {
        el.addEventListener('click', () => {
            if (el.dataset.act === 'gen') _openGenerator(host);
            if (el.dataset.act === 'blank') _blank(host);
        });
    });
    host.querySelectorAll('[data-ver]').forEach(el => {
        el.addEventListener('click', () => { s.open = el.dataset.ver; _render(host); });
    });
    if (s.open) await _paintOne(host);
}

async function _paintOne(host) {
    const s = S(host);
    const main = host.querySelector('#bv-main');
    if (!main) return;
    const meta = s.items.find(x => x.id === s.open);
    if (meta && meta.status === 'pending') {
        main.innerHTML = '<div class="bv-note">生成中…（跑 Claude，約一到三分鐘）</div>';
        pollJob(s.open, s.polling, {
            alive: () => host.isConnected,       // 切走分頁就停
            list: _list.bind(null, s.proposalId),
            onSettled: async (items) => { s.items = items; await _render(host); },
        });
        return;
    }
    if (meta && meta.status === 'failed') {
        main.innerHTML = `<div class="bv-note bv-err">生成失敗：${esc(meta.error || '')}</div>`;
        return;
    }
    main.innerHTML = '<div class="bv-note">載入中…</div>';
    let b;
    try {
        b = (await tfetch(`${API}/briefs/${encodeURIComponent(s.open)}`)).brief;
    } catch (e) {
        main.innerHTML = `<div class="bv-note bv-err">載入失敗：${esc(e.message || e)}</div>`;
        return;
    }
    if (S(host).open !== b.id) return;          // await 期間已切到別版
    main.innerHTML = `
        <div class="bv-tools">
            ${REWRITES.map(([k, label]) =>
                `<button class="bv-btn sm" data-rw="${k}" disabled>${esc(label)}</button>`).join('')}
            <span class="bv-note bv-hint">在下面反白一段，再按上面的按鈕叫 Claude 改寫</span>
            <span class="bv-gap"></span>
            <span class="bv-note bv-save"></span>
            <button class="bv-btn sm" data-act="del">刪除這一版</button>
        </div>
        <textarea class="bv-text" spellcheck="false">${esc(b.content || '')}</textarea>`;

    const ta = main.querySelector('.bv-text');
    const save = main.querySelector('.bv-save');
    // 反白才有得改寫 —— 沒選就把按鈕關著，比按下去才說「請先選一段」誠實
    const sync = () => {
        const has = ta.selectionEnd > ta.selectionStart;
        main.querySelectorAll('[data-rw]').forEach(x => (x.disabled = !has));
    };
    ['select', 'keyup', 'mouseup', 'input'].forEach(ev => ta.addEventListener(ev, sync));
    // 共用的自動儲存（停手就送、離開欄位也送、沒改過不打 API）。手寫過一版，
    // 少了 dirty-check 與 blur flush —— 打完字直接切版本會整段掉。
    ta.addEventListener('input', () => { save.textContent = '未儲存'; });
    s.saver = autosave(ta, (v) => tfetch(`${API}/briefs/${encodeURIComponent(b.id)}`,
                                         { method: 'PATCH', json: { content: v } }), {
        onOk: () => { save.textContent = '已儲存'; },
        onError: (err) => { save.textContent = '儲存失敗：' + (err.message || err); },
    });
    main.querySelectorAll('[data-rw]').forEach(el => {
        el.addEventListener('click', () => _rewrite(host, b.id, ta, el.dataset.rw, main));
    });
    main.querySelector('[data-act="del"]').addEventListener('click', () => _del(host, b.id));
}

/** 反白一段 → Claude 改寫 → **接回原處**（用選取範圍，不做字串搜尋取代）。 */
async function _rewrite(host, bid, ta, mode, main) {
    const [a, z] = [ta.selectionStart, ta.selectionEnd];
    const selection = ta.value.slice(a, z).trim();
    if (!selection) return;
    const btns = main.querySelectorAll('button');
    btns.forEach(x => (x.disabled = true));
    const save = main.querySelector('.bv-save');
    save.textContent = 'Claude 改寫中…';
    try {
        const d = await tfetch(`${API}/briefs/${encodeURIComponent(bid)}/rewrite`,
                               { method: 'POST', json: { selection, mode } });
        ta.value = ta.value.slice(0, a) + d.text + ta.value.slice(z);
        ta.setSelectionRange(a, a + d.text.length);
        await S(host).saver.flush();      // 同一個寫入者 —— 自己再 PATCH 一次的話
        save.textContent = '已改寫並儲存';  // dirty 基準不會前進，下次 blur 又送一次全文
    } catch (e) {
        save.textContent = '';
        alert('改寫失敗：' + (e.message || e));
    } finally {
        btns.forEach(x => (x.disabled = false));
    }
}

async function _blank(host) {
    const s = S(host);
    try {
        const d = await tfetch(`${API}/proposals/${encodeURIComponent(s.proposalId)}/briefs`,
                               { method: 'POST', json: { content: '' } });
        s.open = d.brief.id;
        await _load(host);
    } catch (e) { alert('建立失敗：' + (e.message || e)); }
}

async function _del(host, bid) {
    if (!confirm('刪除這一版企劃書？其他版本不受影響。')) return;
    try {
        await tfetch(`${API}/briefs/${encodeURIComponent(bid)}`, { method: 'DELETE' });
        await _load(host);
    } catch (e) { alert('刪除失敗：' + (e.message || e)); }
}

/** 生成對話框：選範本、勾要納入哪些資料、選篇幅與語氣（owner 指定要能自己決定）。 */
async function _openGenerator(host) {
    const s = S(host);
    const matrix = renderMatrixText(s.plan);
    if (!matrix.trim()) {
        alert('這個提案還沒有開始企劃 —— 先去「企劃」分頁選一套方法論並填一些格子。');
        return;
    }
    let templates = [];
    try {
        templates = (await tfetch(`${API}/brief-templates`)).templates || [];
    } catch { /* 範本拿不到照樣可以生成（就是沒有參考） */ }
    const ready = templates.filter(t => (t.skeleton || '').trim());
    const filled = (matrix.match(/（未填）/g) || []).length;

    const theme0 = ((s.plan || {}).theme || '').trim();
    const dlg = openDialog({
        title: '用企劃矩陣生成企劃書',
        width: 560,
        body: `
            <div class="bv-note" style="margin-bottom:10px;">
                範本骨架是<b>通用的</b>（不綁題材與年齡）—— 下面這兩個值才會讓它
                長出這一次的樣貌。
            </div>
            ${field('主題', `<input class="bv-theme" type="text" value="${esc(theme0)}"
                placeholder="這一支要講什麼？（預設帶入矩陣的 THEME）">`)}
            ${field('目標受眾／年齡層', `<input class="bv-aud" type="text"
                placeholder="例：6–10 歲兒童與家長／品牌客戶的決策者">`)}
            ${field('參考範本（可不選，最多 2 份）', ready.length
                ? `<div class="bv-picks">${ready.map(t => `
                    <label class="bv-pick"><input type="checkbox" class="bv-tpl"
                        value="${esc(t.id)}" data-q="${esc(JSON.stringify(t.questions || []))}">
                    <span>${esc(t.name)}</span></label>`).join('')}</div>
                   <div class="bv-qs"></div>`
                : `<div class="bv-note">範本庫還沒有消化好的範本 —— 不選也可以生成，
                   只是沒有文風可以照。</div>`)}
            ${field('篇幅', `<select class="bv-len">${LENGTHS.map(([v, l], i) =>
                `<option value="${v}"${i === 1 ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>`)}
            ${field('語氣', `<select class="bv-tone">${TONES.map(([v, l]) =>
                `<option value="${v}">${esc(l)}</option>`).join('')}</select>`)}
            ${field('要納入哪些資料（企劃矩陣一定納入）', `<div class="bv-picks">${
                INCLUDES.map(([v, l], i) => `<label class="bv-pick">
                    <input type="checkbox" class="bv-inc" value="${v}"${i < 2 ? ' checked' : ''}>
                    <span>${esc(l)}</span></label>`).join('')}</div>`)}
            <div class="bv-note">
                ${filled ? `目前矩陣有 <b>${filled}</b> 格還沒填 —— 那些會被列進「待補」，
                    <b>不會</b>替你想。` : '矩陣全部填滿了。'}
            </div>
            <div class="pdlg-err bv-err2"></div>
            <div class="pdlg-acts">
                <button class="pdlg-btn ghost bv-cancel">取消</button>
                <button class="pdlg-btn bv-go">開始生成</button>
            </div>`,
    });
    const el = dlg.el;

    // 範本的「生成前必問」——它的結構需要、但企劃矩陣不會涵蓋的東西
    // （場地細節、行程時間、參與者年齡、許可…）。勾了才問，答案進 prompt。
    const qbox = el.querySelector('.bv-qs');
    const syncQs = () => {
        if (!qbox) return;
        const qs = [];
        el.querySelectorAll('.bv-tpl:checked').forEach(x => {
            try { JSON.parse(x.dataset.q || '[]').forEach(q => qs.includes(q) || qs.push(q)); }
            catch { /* 骨架被人改壞了就當沒有問題，不要擋住生成 */ }
        });
        qbox.innerHTML = qs.length ? `
            <div class="bv-note" style="margin:8px 0 4px;">
                這幾題是<b>這份範本寫不出來就得問</b>的（矩陣不會涵蓋）。
                留空也能生成 —— 沒答的會被列進「待補」，不會被編出來。
            </div>
            ${qs.map((q, i) => `<label class="bv-q">
                <span>${esc(q)}</span>
                <input type="text" class="bv-ans" data-q="${esc(q)}">
            </label>`).join('')}` : '';
    };
    el.querySelectorAll('.bv-tpl').forEach(x => x.addEventListener('change', syncQs));
    syncQs();

    el.querySelector('.bv-cancel').addEventListener('click', dlg.close);
    el.querySelector('.bv-go').addEventListener('click', async () => {
        const tplIds = [...el.querySelectorAll('.bv-tpl:checked')].map(x => x.value);
        if (tplIds.length > 2) {
            el.querySelector('.bv-err2').textContent = '最多選 2 份範本（再多 prompt 會被撐爆）';
            return;
        }
        el.querySelectorAll('button').forEach(x => (x.disabled = true));
        try {
            const d = await tfetch(
                `${API}/proposals/${encodeURIComponent(s.proposalId)}/briefs/generate`,
                { method: 'POST', json: {
                    matrix_text: matrix,
                    template_ids: tplIds,
                    options: {
                        theme: el.querySelector('.bv-theme').value,
                        audience: el.querySelector('.bv-aud').value,
                        length: el.querySelector('.bv-len').value,
                        tone: el.querySelector('.bv-tone').value,
                        include: [...el.querySelectorAll('.bv-inc:checked')].map(x => x.value),
                        answers: Object.fromEntries(
                            [...el.querySelectorAll('.bv-ans')]
                                .map(x => [x.dataset.q, x.value])
                                .filter(([, v]) => (v || '').trim())),
                    },
                } });
            dlg.close();
            if (s.toast) s.toast('開始生成 —— 約一到三分鐘，完成後這一版會自己出現');
            s.open = d.brief.id;
            await _load(host);
        } catch (e) {
            el.querySelector('.bv-err2').textContent = (e && e.message) || String(e);
            el.querySelectorAll('button').forEach(x => (x.disabled = false));
        }
    });
}

const STYLE = `
.bv { --bv-ink: #ddd; --bv-sub: #8b8b8b; --bv-line: #2a2a2a; --bv-card: #161616;
      --bv-accent: #c9372c; --bv-err: #f87171; color: var(--bv-ink); }
html.plan-theme-light .bv { --bv-ink: #262626; --bv-sub: #737373; --bv-line: #e5e5e5;
      --bv-card: #fafafa; --bv-accent: #c9372c; --bv-err: #d33; }
.bv * { box-sizing: border-box; }
.bv-gap { flex: 1; }
.bv-note { font-size: 11.5px; color: var(--bv-sub); line-height: 1.7; }
.bv-err { color: var(--bv-err); }
.bv-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.bv-btn { border: 1px solid var(--bv-line); background: none; cursor: pointer;
      color: var(--bv-ink); font: inherit; font-size: 12.5px; padding: 6px 12px; border-radius: 3px; }
.bv-btn:hover:not(:disabled) { border-color: var(--bv-accent); color: var(--bv-accent); }
.bv-btn.primary { border-color: var(--bv-accent); color: var(--bv-accent); }
.bv-btn.sm { font-size: 11.5px; padding: 3px 9px; }
.bv-btn:disabled { opacity: .4; cursor: default; }
.bv-body { display: flex; gap: 12px; align-items: flex-start; }
.bv-side { flex: none; width: 132px; display: flex; flex-direction: column; gap: 4px; }
.bv-ver { text-align: left; background: none; cursor: pointer; color: var(--bv-sub);
      border: 1px solid transparent; border-radius: 3px; padding: 6px 8px; font: inherit; }
.bv-ver:hover { border-color: var(--bv-line); color: var(--bv-ink); }
.bv-ver.on { border-color: var(--bv-accent); color: var(--bv-ink); background: var(--bv-card); }
.bv-when { display: block; font-size: 12px; }
.bv-meta { display: block; font-size: 10.5px; opacity: .7; }
.bv-main { flex: 1; min-width: 0; }
.bv-tools { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 6px; }
.bv-hint { font-size: 11px; }
.bv-text { width: 100%; min-height: 60vh; resize: vertical; background: none;
      color: var(--bv-ink); border: 1px solid var(--bv-line); border-radius: 3px;
      padding: 12px 14px; font: inherit; font-size: 13px; line-height: 1.9; outline: none;
      white-space: pre-wrap; }
.bv-text:focus { border-color: var(--bv-accent); }
.bv-picks { display: flex; flex-wrap: wrap; gap: 4px 14px; }
.bv-pick { display: inline-flex; align-items: center; gap: 5px; font-size: 12.5px; cursor: pointer; }
.bv-q { display: block; margin: 6px 0; font-size: 12.5px; }
.bv-q span { display: block; color: var(--bv-sub); margin-bottom: 3px; }
@media (max-width: 720px) {
  .bv-body { flex-direction: column; }
  .bv-side { width: 100%; flex-direction: row; flex-wrap: wrap; }
  .bv-text { min-height: 50vh; }
}`;
