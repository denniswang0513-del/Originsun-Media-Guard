/**
 * stage-editor.js — 工作階段設定（唯一正本）。
 *
 * 每個分類（core.hr_logic.WORK_TYPES 九類）自己的階段清單：改名、上下排序、新增、停用不刪。
 * 資料存 work_stage_nodes（GET/POST/PUT/DELETE /api/v1/crm/work-stages/nodes）；每個動作立刻寫入，
 * 沒有「儲存」鈕——關閉時把最新的 active 清單（{分類: [{id, name}]}）回給呼叫端，新列的下拉立即更新。
 * 工作追蹤分頁「設定」與 /my.html「工作階段設定」鈕開的是同一個。
 *
 * 主題：CSS 變數 --se-*（預設深色）；白底頁在 .stage-editor 上覆寫。
 */
import { esc, ensureStyle } from './dom.js';
import { tsFetch } from './ts-sheet.js';

const API = '/api/v1/crm/work-stages/nodes';
const CSS = `
.stage-editor-bg { position:fixed; inset:0; background:rgba(0,0,0,.55); z-index:1200; display:flex; align-items:flex-start; justify-content:center; padding:40px 20px; overflow:auto; }
.stage-editor { --se-bg:#1b1b1b; --se-ink:#eee; --se-sub:#888; --se-line:#3a3a3a; --se-box:#232323; --se-off:#666; --se-btn:#3b82f6; --se-input:#1a1a1a;
    background:var(--se-bg); color:var(--se-ink); border:1px solid var(--se-line); border-radius:12px; width:100%; max-width:860px; padding:18px 22px; box-shadow:0 16px 48px rgba(0,0,0,.4); font-size:13px; }
.stage-editor h3 { margin:0 0 4px; font-size:16px; }
.stage-editor .se-sub { color:var(--se-sub); font-size:12px; line-height:1.6; }
.stage-editor .se-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:12px; margin-top:12px; }
.stage-editor .se-g { border:1px solid var(--se-line); border-radius:8px; padding:10px 12px; background:var(--se-box); }
.stage-editor .se-g h4 { margin:0 0 6px; font-size:13px; display:flex; justify-content:space-between; align-items:center; }
.stage-editor .se-g ul { list-style:none; margin:0; padding:0; }
.stage-editor .se-g li { display:flex; align-items:center; gap:4px; padding:3px 0; }
.stage-editor .se-g li input { flex:1; min-width:0; border:1px solid transparent; border-radius:4px; padding:3px 6px; font:inherit; font-size:13px; background:var(--se-input); color:var(--se-ink); }
.stage-editor .se-g li input:focus { border-color:var(--se-btn); outline:0; }
.stage-editor .se-g li.off input { color:var(--se-off); text-decoration:line-through; }
.stage-editor .se-ic { border:1px solid transparent; background:none; color:var(--se-sub); cursor:pointer; font-size:12px; padding:2px 5px; border-radius:4px; font-family:inherit; }
.stage-editor .se-ic:hover { color:var(--se-ink); border-color:var(--se-line); }
.stage-editor .se-ic:disabled { opacity:.35; cursor:default; }
.stage-editor .se-foot { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-top:14px; }
.stage-editor .se-msg { color:var(--se-sub); font-size:12px; }
.stage-editor .se-done { background:var(--se-btn); color:#fff; border:none; border-radius:6px; padding:7px 16px; font-size:13px; cursor:pointer; font-family:inherit; }
.stage-editor .se-x { float:right; border:0; background:none; font-size:20px; cursor:pointer; color:var(--se-sub); line-height:1; }
`;

/** 節點樹 → 給格子的清單（只留 active；順序照 sort）。 */
export function stagesMapFrom(categories) {
    const out = {};
    for (const c of categories || []) {
        out[c.name] = (c.stages || []).filter(s => s.active !== false)
            .slice().sort((a, b) => (a.sort ?? 0) - (b.sort ?? 0)).map(s => ({ id: s.id, name: s.name }));
    }
    return out;
}

/**
 * 開工作階段設定視窗。cfg：tfetch（預設 tsFetch）、onSaved(stagesMap)（關閉時、有改過才叫）、onClose()。
 * 回傳 Promise，關閉時 resolve（stagesMap 或 null）。
 */
export async function openStageEditor(cfg = {}) {
    ensureStyle('stage-editor-css', CSS);
    const f = cfg.tfetch || tsFetch;
    let cats = [];
    let dirty = false;
    const bg = document.createElement('div');
    bg.className = 'stage-editor-bg';
    bg.innerHTML = `<div class="stage-editor" role="dialog" aria-label="工作階段設定">
        <button type="button" class="se-x" data-se="close" title="關閉">×</button>
        <h3>工作階段設定</h3>
        <div class="se-sub">每個分類自己的階段。改名直接打字（離開格子就存）、上下鍵排序、「停用」不刪（舊列照樣顯示）；改完新列的階段下拉立即更新。</div>
        <div class="se-grid" data-se="grid"><div class="se-sub" style="padding:12px;">載入中…</div></div>
        <div class="se-foot"><span class="se-msg" data-se="msg"></span><button type="button" class="se-done" data-se="close">完成</button></div>
    </div>`;
    document.body.appendChild(bg);
    const grid = bg.querySelector('[data-se="grid"]'), msg = bg.querySelector('[data-se="msg"]');
    const say = (t, err) => { msg.textContent = t || ''; msg.style.color = err ? '#f87171' : ''; };

    const draw = () => {
        grid.innerHTML = cats.map(c => {
            const list = (c.stages || []).slice().sort((a, b) => (a.sort ?? 0) - (b.sort ?? 0));
            return `<div class="se-g" data-cat="${esc(c.id)}"><h4><span>${esc(c.name)}</span><button type="button" class="se-ic" data-se="add" data-parent="${esc(c.id)}">＋ 階段</button></h4>
                <ul>${list.map((s, i) => `<li class="${s.active === false ? 'off' : ''}" data-id="${esc(s.id)}">
                    <input value="${esc(s.name)}" data-se="name" data-id="${esc(s.id)}" data-orig="${esc(s.name)}">
                    <button type="button" class="se-ic" data-se="up" data-id="${esc(s.id)}" ${i === 0 ? 'disabled' : ''} title="往上">↑</button>
                    <button type="button" class="se-ic" data-se="down" data-id="${esc(s.id)}" ${i === list.length - 1 ? 'disabled' : ''} title="往下">↓</button>
                    <button type="button" class="se-ic" data-se="toggle" data-id="${esc(s.id)}">${s.active === false ? '啟用' : '停用'}</button>
                </li>`).join('') || '<li class="se-sub">（還沒有階段）</li>'}</ul></div>`;
        }).join('') || '<div class="se-sub" style="padding:12px;">還沒有分類。</div>';
    };
    const load = async () => {
        try { cats = (await f(API)).categories || []; draw(); }
        catch (e) { grid.innerHTML = `<div class="se-sub" style="padding:12px;color:#f87171;">載入失敗：${esc(e.message || e)}</div>`; }
    };
    const find = (id) => {
        for (const c of cats) { const s = (c.stages || []).find(x => x.id === id); if (s) return { c, s }; }
        return null;
    };
    const put = async (id, body) => { await f(`${API}/${encodeURIComponent(id)}`, { method: 'PUT', body }); dirty = true; };

    const close = () => {
        bg.remove();
        const map = dirty ? stagesMapFrom(cats) : null;
        if (map && cfg.onSaved) cfg.onSaved(map);
        cfg.onClose?.();
        done(map);
    };
    let done = () => {};
    const p = new Promise(res => { done = res; });

    bg.addEventListener('click', async (ev) => {
        if (ev.target === bg) return close();
        const b = ev.target.closest('[data-se]');
        if (!b) return;
        const act = b.dataset.se;
        try {
            if (act === 'close') return close();
            if (act === 'add') {
                const r = await f(API, { method: 'POST', body: { parent_id: b.dataset.parent, name: '新階段' } });
                dirty = true;
                await load();
                const inp = grid.querySelector(`input[data-id="${CSS_escape((r.node && r.node.id) || r.id || '')}"]`);   // 端點回 {node:{id}}
                if (inp) { inp.focus(); inp.select(); }
                return;
            }
            const hit = find(b.dataset.id);
            if (!hit) return;
            const list = hit.c.stages.slice().sort((a, x) => (a.sort ?? 0) - (x.sort ?? 0));
            const i = list.findIndex(s => s.id === hit.s.id);
            // 值就在手上：寫完就地改、重畫，不整棵樹重抓（只有「＋階段」要新 id 才 load）
            if (act === 'toggle') { const on = hit.s.active === false; await put(hit.s.id, { active: on }); hit.s.active = on; dirty = true; return draw(); }
            const swap = async (o, a, b) => { await Promise.all([put(hit.s.id, { sort: a }), put(o.id, { sort: b })]); hit.s.sort = a; o.sort = b; dirty = true; draw(); };
            if (act === 'up' && i > 0) { const o = list[i - 1]; return swap(o, o.sort ?? i - 1, hit.s.sort ?? i); }
            if (act === 'down' && i < list.length - 1) { const o = list[i + 1]; return swap(o, o.sort ?? i + 1, hit.s.sort ?? i); }
        } catch (e) { say('沒存：' + (e.message || e), true); }
    });
    bg.addEventListener('change', async (ev) => {
        const inp = ev.target.closest('input[data-se="name"]');
        if (!inp) return;
        const name = inp.value.trim();
        if (!name || name === inp.dataset.orig) { inp.value = inp.dataset.orig; return; }
        try { await put(inp.dataset.id, { name }); inp.dataset.orig = name; const hit = find(inp.dataset.id); if (hit) hit.s.name = name; say('已存 ' + name); }
        catch (e) { say('沒存：' + (e.message || e), true); inp.value = inp.dataset.orig; }
    });
    bg.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') close(); });
    await load();
    return p;
}

// CSS.escape 在舊瀏覽器可能沒有；id 只有 hex/底線，退回原字即可
const CSS_escape = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/"/g, '\\"'));
