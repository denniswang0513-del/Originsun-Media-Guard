/**
 * brief-templates.js — 企劃**範本庫**（`{提案根目錄}/_範本/`）。
 *
 * ⚠️ 別跟同目錄的 `plan-templates.js` 搞混：那支是**方法論模板**（黃鴻儒的
 * 三視角 × 四提問矩陣定義，純資料）。這支是「以前寫得好的企劃書」，拿來給
 * Claude 參考文風與章節結構的。兩者都叫 template，但一個是矩陣的骨、一個是
 * 成品的樣板。
 *
 * 生成企劃書時給 Claude 參考的「好範本」。這支管清單、上傳、改名、改骨架、
 * 刪除；消化（抽文字 → 骨架）由後端跑，這裡只按按鈕看狀態。
 *
 * 兩個介面共用（後台提案庫 Tab 與獨立企劃頁），所以照 proposal-folders 的三條：
 *   (a) 只 import js/shared 與同目錄（NAS 對外容器只 serve 這兩處）；
 *   (b) 自帶 --pt-* 變數（深色預設、白底靠 html.plan-theme-light 覆寫）；
 *   (c) 外殼由呼叫端注入（`mount(html) -> element`）。
 *
 * 🔴 骨架是**可以直接編輯的純文字**。生成出來不對味時改這段，比回頭調 prompt
 * 直觀得多 —— 所以這個畫面的重點不是「管理檔案」，是「看得到並改得動骨架」。
 */

import { pollJob } from '../../js/shared/poll-job.js';
import {
    bearerHeader, ensureStyle, esc, inputUploadItems, uploadItems,
} from '../../js/shared/utils.js';
import { tfetch } from './prop-fetch.js';

const API = '/api/v1/crm/brief-templates';

// 能抽得出文字的格式（後端 ALLOWED_EXTS 的 UI 鏡像；權威在
// routers/crm/brief_templates.py，這裡只為了不讓人白上傳一次）
export const TEMPLATE_EXTS = ['.pdf', '.pptx', '.docx', '.md', '.txt'];
export const isTemplateFile = (name) =>
    TEMPLATE_EXTS.some(e => String(name || '').toLowerCase().endsWith(e));

// 狀態掛在這個實例的根節點上（同 proposal-folders：對話框連按兩下會疊出兩個）
const S = (ov) => ov.__pt;

/**
 * @param mount (html) => element   呼叫端提供外殼；回傳**包住**內容的容器。
 */
export async function openTemplateLibrary(mount) {
    ensureStyle('pt-style', STYLE);
    const shell = mount(`
        <div class="pt">
            <div class="pt-head">
                <span class="pt-note">範本會被濃縮成「骨架」（章節結構＋語氣），
                    生成企劃書時餵給 Claude 的就是骨架，不是整份原文。</span>
                <span class="pt-gap"></span>
                <button class="pt-btn primary pt-add">＋ 上傳範本</button>
            </div>
            <div class="pt-list"><div class="pt-note">載入中…</div></div>
        </div>`);
    const ov = shell.querySelector('.pt');
    ov.__pt = { items: [], open: '', polling: new Set() };
    ov.querySelector('.pt-add').addEventListener('click', () => _pick(ov));
    ov.querySelector('.pt-list').addEventListener('click', (e) => _onClick(ov, e));
    await _load(ov);
}

async function _load(ov) {
    const list = ov.querySelector('.pt-list');
    try {
        S(ov).items = (await tfetch(API)).templates || [];
    } catch (e) {
        list.innerHTML = `<div class="pt-note pt-err">載入失敗：${esc(e.message || e)}</div>`;
        return;
    }
    _render(ov);
}

const _statusLabel = { ok: '已消化', pending: '待消化', failed: '消化失敗' };

function _render(ov) {
    const s = S(ov);
    const list = ov.querySelector('.pt-list');
    if (!s.items.length) {
        list.innerHTML = `<div class="pt-note">還沒有範本 —— 按「＋ 上傳範本」，
            或在提案的檔案列上按 📚 把某一份設為範本。</div>`;
        return;
    }
    list.innerHTML = s.items.map(t => {
        const st = t.status || 'pending';
        return `
        <div class="pt-item" data-id="${esc(t.id)}">
            <div class="pt-row">
                <span class="pt-name">${esc(t.name)}</span>
                <span class="pt-pill ${esc(st)}">${esc(_statusLabel[st] || st)}</span>
                <span class="pt-note">${esc(t.filename)}</span>
                <span class="pt-gap"></span>
                <button class="pt-btn" data-act="digest">${
                    t.skeleton ? '重新消化' : '消化'}</button>
                <button class="pt-btn" data-act="edit">${
                    t.id === s.open ? '收起' : '骨架'}</button>
                <button class="pt-btn danger" data-act="del">刪除</button>
            </div>
            ${t.id === s.open ? `
            <div class="pt-panel">
                ${st === 'failed' && t.error
                    ? `<div class="pt-note pt-err">消化失敗：${esc(t.error)}</div>` : ''}
                <textarea class="pt-sk" placeholder="骨架還沒生成 —— 也可以直接自己寫：這份範本有哪幾節、各節在回答什麼、語氣是什麼。">${esc(t.skeleton || '')}</textarea>
                <div class="pt-bar">
                    <input class="pt-rename" type="text" value="${esc(t.name)}" placeholder="顯示名">
                    <button class="pt-btn primary" data-act="save">儲存</button>
                </div>
            </div>` : ''}
        </div>`;
    }).join('');
}

function _onClick(ov, e) {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const item = btn.closest('[data-id]');
    const id = item.dataset.id;
    const s = S(ov);
    if (btn.dataset.act === 'edit') {
        s.open = s.open === id ? '' : id;      // 再按一次收起來
        _render(ov);
        return;
    }
    if (btn.dataset.act === 'save') return _save(ov, item, id);
    if (btn.dataset.act === 'digest') return _digest(ov, id);
    if (btn.dataset.act === 'del') return _del(ov, id);
}

async function _save(ov, item, id) {
    const body = {
        name: item.querySelector('.pt-rename').value.trim(),
        skeleton: item.querySelector('.pt-sk').value,
    };
    try {
        await tfetch(`${API}/${encodeURIComponent(id)}`, { method: 'PATCH', json: body });
        await _load(ov);
    } catch (e) { alert('儲存失敗：' + (e.message || e)); }
}

/**
 * 跑消化（claude，數十秒到數分鐘）。端點是**背景跑立刻回**，所以這裡開一條
 * 輪詢把狀態追到底 —— 不追的話畫面會一直停在「待消化」，看起來像沒反應。
 */
async function _digest(ov, id) {
    const t = S(ov).items.find(x => x.id === id);
    if (t && t.skeleton
        && !confirm(`重新消化會**覆蓋**目前的骨架（包含你手改過的內容）。要繼續嗎？`)) return;
    try {
        await tfetch(`${API}/${encodeURIComponent(id)}/digest`, { method: 'POST' });
    } catch (e) { alert('消化啟動失敗：' + (e.message || e)); return; }
    await _load(ov);
    pollJob(id, S(ov).polling, {
        alive: () => ov.isConnected,             // 對話框關掉就別再打了
        list: async () => (await tfetch(API)).templates || [],
        onSettled: (items) => { S(ov).items = items; _render(ov); },
    });
}

async function _del(ov, id) {
    const t = S(ov).items.find(x => x.id === id);
    if (!confirm(`確定刪除範本「${t ? t.name : id}」？_範本 夾裡那份檔案也會刪掉`
                 + '（原提案的檔案不動）。')) return;
    try {
        await tfetch(`${API}/${encodeURIComponent(id)}`, { method: 'DELETE' });
        await _load(ov);
    } catch (e) { alert('刪除失敗：' + (e.message || e)); }
}

function _pick(ov) {
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.multiple = true;
    inp.accept = TEMPLATE_EXTS.join(',');
    inp.addEventListener('change', async () => {
        const bad = [...inp.files].filter(f => !isTemplateFile(f.name));
        if (bad.length) {
            alert(`這些格式抽不出文字，沒辦法當範本：\n${bad.map(f => f.name).join('\n')}\n\n`
                  + `只收 ${TEMPLATE_EXTS.join(' / ')}（Keynote 請先另存 PDF 或 PPTX）`);
            return;
        }
        const items = inputUploadItems(inp.files);
        if (!items.length) return;
        try {
            const d = await uploadItems(`${API}/upload`, items, { headers: bearerHeader() });
            const skipped = (d.skipped || []).map(x => `${x.filename}（${x.reason}）`);
            if (skipped.length) alert('部分項目未上傳：\n' + skipped.join('\n'));
            await _load(ov);
        } catch (e) { alert('上傳失敗：' + (e.message || e)); }
    });
    inp.click();
}

/** 把某個提案資產夾裡的檔案設為範本（folder-view 的 rowAction 呼叫這支）。 */
export async function templateFromAsset(projectId, file) {
    const name = (prompt('範本顯示名', (file.filename || '').replace(/\.[^.]+$/, '')) || '').trim();
    if (!name) return false;
    await tfetch(`${API}/from-asset`, {
        method: 'POST', json: { project_id: projectId, rel: file.rel, name },
    });
    return true;
}

const STYLE = `
.pt { --pt-ink: #ddd; --pt-sub: #8b8b8b; --pt-line: #2a2a2a; --pt-card: #161616;
      --pt-accent: #c9372c; --pt-warn: #fbbf24; --pt-ok: #4ade80; --pt-err: #f87171;
      color: var(--pt-ink); }
html.plan-theme-light .pt { --pt-ink: #262626; --pt-sub: #737373; --pt-line: #e5e5e5;
      --pt-card: #fafafa; --pt-accent: #c9372c; --pt-warn: #b8860b; --pt-ok: #147a3d;
      --pt-err: #d33; }
.pt * { box-sizing: border-box; }
.pt-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.pt-gap { flex: 1; }
.pt-note { font-size: 11.5px; color: var(--pt-sub); line-height: 1.7; min-width: 0;
      overflow-wrap: anywhere; }
.pt-err { color: var(--pt-err); }
.pt-item { border: 1px solid var(--pt-line); border-radius: 4px; margin-bottom: 6px; }
.pt-row { display: flex; gap: 8px; align-items: center; padding: 9px 10px; }
.pt-name { font-weight: 600; font-size: 13px; }
.pt-pill { font-size: 11px; padding: 1px 8px; border: 1px solid var(--pt-line);
      border-radius: 2px; color: var(--pt-sub); white-space: nowrap; }
.pt-pill.ok { color: var(--pt-ok); border-color: var(--pt-ok); }
.pt-pill.failed { color: var(--pt-err); border-color: var(--pt-err); }
.pt-pill.pending { color: var(--pt-warn); border-color: var(--pt-warn); }
.pt-btn { border: 1px solid var(--pt-line); background: none; cursor: pointer;
      color: var(--pt-ink); font: inherit; font-size: 12px; padding: 4px 9px; border-radius: 3px; }
.pt-btn:hover { border-color: var(--pt-accent); color: var(--pt-accent); }
.pt-btn.primary { border-color: var(--pt-accent); color: var(--pt-accent); }
.pt-btn.danger:hover { border-color: var(--pt-err); color: var(--pt-err); }
.pt-panel { border-top: 1px solid var(--pt-line); padding: 10px; background: var(--pt-card); }
.pt-sk { width: 100%; min-height: 160px; resize: vertical; background: none;
      color: var(--pt-ink); border: 1px solid var(--pt-line); border-radius: 3px;
      padding: 8px; font: inherit; font-size: 12.5px; line-height: 1.75; outline: none; }
.pt-sk:focus { border-color: var(--pt-accent); }
.pt-bar { display: flex; gap: 6px; align-items: center; margin-top: 8px; }
.pt-bar input { flex: 1; min-width: 0; background: none; color: var(--pt-ink);
      border: 1px solid var(--pt-line); border-radius: 3px; padding: 6px 9px;
      font: inherit; font-size: 12.5px; outline: none; }
.pt-bar input:focus { border-color: var(--pt-accent); }
@media (max-width: 720px) {
  .pt-row { flex-wrap: wrap; }
  .pt-name { flex: 1 0 100%; }
  .pt-gap { display: none; }
}`;
