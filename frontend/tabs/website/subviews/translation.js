/**
 * translation.js — 英文翻譯（transcreation）後台卡片
 *
 * 跟 AI SEO 生成同一套：排程 + 立即翻譯 + 逐項審核/編輯/核准。
 * 用 claude 把中文「在地化改寫」成專業英文，寫進各實體 _en 欄。
 * 客戶名/人名（專有名詞）不 AI 翻，只手動指定。
 */
import {
    websiteFetch, esc, toastOk, toastErr, renderLoadError, openModal, closeModal,
} from '../website-utils.js';

let _state = { audit: [], settings: {} };
let _container = null;
const _tr = (window._tr = window._tr || {});

const ETYPE_LABEL = { work: '🎬 作品', post: '📝 文章', service: '🧩 服務' };
const STATUS = {
    missing:    ['缺英文',    '#7f1d1d', '#fca5a5'],
    stale:      ['中文已改',  '#78350f', '#fbbf24'],
    pending:    ['待翻譯',    '#3f3f46', '#d4d4d8'],
    translated: ['待審核',    '#1e3a5f', '#93c5fd'],
    approved:   ['已核准',    '#064e3b', '#6ee7b7'],
};
const FIELD_LABEL = {
    public_title_en: '標題', public_description_en: '描述',
    title_en: '標題', excerpt_en: '摘要', seo_title_en: 'SEO 標題', seo_description_en: 'SEO 描述',
    short_desc_en: '短描述', full_desc_en: '完整描述',
};

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    _container = container;
    container.innerHTML = '<h2>🌐 英文翻譯</h2><div style="color:#888;padding:20px;">載入中…</div>';
    try {
        const [audit, settings] = await Promise.all([
            websiteFetch('/api/website/admin/translation/audit'),
            websiteFetch('/api/website/admin/translation/settings'),
        ]);
        if (!isCurrent()) return;
        _state.audit = audit?.items || [];
        _state.settings = settings || {};
    } catch (e) {
        if (!isCurrent()) return;
        renderLoadError(container, '🌐 英文翻譯', e);
        return;
    }
    _renderShell();
}

function _counts() {
    const c = { missing: 0, stale: 0, pending: 0, translated: 0, approved: 0 };
    for (const it of _state.audit) c[it.status] = (c[it.status] || 0) + 1;
    return c;
}

function _renderShell() {
    const s = _state.settings;
    const c = _counts();
    const pending = _state.audit.filter(it => it.needs_ai).length;
    const lastSum = s.last_run_summary
        ? `上次：處理 ${s.last_run_summary.processed || 0}、錯誤 ${s.last_run_summary.errors || 0}`
        : '尚未執行';

    _container.innerHTML = `
        <h2 style="margin:0 0 4px;">🌐 英文翻譯 <span style="color:#888;font-size:12px;font-weight:400;">· transcreation（在地化改寫，非逐字直譯）</span></h2>
        <p style="color:#888;font-size:12px;margin:0 0 14px;">
            用 claude 把作品/文章/服務的中文改寫成專業英文，寫進 <code>_en</code> 欄。
            <strong style="color:#ddd;">客戶名、人名不 AI 翻</strong>（專有名詞，需手動指定）。英文欄空時前端顯示中文。
        </p>

        <!-- 排程 + 立即翻譯 -->
        <div class="card" style="margin-bottom:14px;">
            <h3 style="color:#fff;margin:0 0 10px;font-size:14px;">⚙️ 排程與設定</h3>
            <div style="display:grid;grid-template-columns:auto 1fr;gap:10px 14px;align-items:center;font-size:13px;">
                <label style="color:#ddd;display:inline-flex;gap:6px;align-items:center;grid-column:1/3;">
                    <input id="tr-enabled" type="checkbox" ${s.enabled ? 'checked' : ''} style="width:auto;"/> 啟用排程自動翻譯</label>
                <span style="color:#9aa0a6;">Cron</span>
                <input id="tr-cron" value="${esc(s.cron || '0 4 * * *')}" style="${_inp()}" />
                <span style="color:#9aa0a6;">每批數量</span>
                <input id="tr-batch" type="number" value="${s.batch_size || 10}" style="${_inp()};max-width:100px;" />
                <label style="color:#ddd;display:inline-flex;gap:6px;align-items:center;grid-column:1/3;">
                    <input id="tr-auto" type="checkbox" ${s.auto_approve ? 'checked' : ''} style="width:auto;"/>
                    自動核准（翻完直接上，不進待審）</label>
                <span style="color:#9aa0a6;align-self:start;padding-top:6px;">品牌調性</span>
                <textarea id="tr-voice" rows="2" placeholder="例：專業但有溫度、避免浮誇；公司自稱用 Originsun Studio" style="${_inp()};resize:vertical;">${esc(s.brand_voice || '')}</textarea>
                <span style="color:#9aa0a6;align-self:start;padding-top:6px;">術語表</span>
                <textarea id="tr-glossary" rows="2" placeholder="固定英譯，例：形象影片→Brand Film；紀錄片→Documentary" style="${_inp()};resize:vertical;">${esc(_glossaryStr(s.glossary))}</textarea>
            </div>
            <div style="display:flex;gap:8px;align-items:center;margin-top:12px;flex-wrap:wrap;">
                <button class="btn" style="background:#3b82f6;" onclick="window._tr.saveSettings()">💾 儲存設定</button>
                <button class="btn" style="background:#059669;" onclick="window._tr.runNow(this)"
                        ${s.running ? 'disabled' : ''}>${s.running ? '翻譯中…' : '⚡ 立即翻譯整批'}</button>
                <span style="color:#888;font-size:12px;">${esc(lastSum)}${s.progress ? ` · 進度 ${esc(s.progress)}` : ''}</span>
            </div>
        </div>

        <!-- 狀態統計 -->
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px;font-size:12px;">
            ${Object.entries(STATUS).map(([k, v]) =>
                `<span class="website-pill" style="background:${v[1]};color:${v[2]};">${v[0]} ${c[k] || 0}</span>`).join('')}
            <span style="color:#888;margin-left:auto;">待翻譯/更新：<strong style="color:#ddd;">${pending}</strong> / ${_state.audit.length}</span>
        </div>

        <!-- 清單 -->
        ${_state.audit.length ? `
            <table>
                <thead><tr><th style="width:70px;">型別</th><th>標題</th><th style="width:90px;">狀態</th>
                    <th style="width:130px;">最後翻譯</th><th style="width:150px;">操作</th></tr></thead>
                <tbody>${_state.audit.map(_row).join('')}</tbody>
            </table>` : '<div style="color:#666;padding:24px;text-align:center;">沒有可翻譯的內容</div>'}
    `;
}

function _inp() {
    return 'background:#0d0d0d;border:1px solid #333;color:#f0f0f0;padding:7px 9px;border-radius:4px;width:100%;box-sizing:border-box;font-size:13px;font-family:inherit;';
}
function _glossaryStr(g) {
    if (!g) return '';
    if (typeof g === 'object') return Object.entries(g).map(([k, v]) => `${k}→${v}`).join('\n');
    return String(g);
}

function _row(it) {
    const st = STATUS[it.status] || ['?', '#333', '#aaa'];
    const when = it.last_translated_at ? new Date(it.last_translated_at).toLocaleDateString() : '—';
    return `
        <tr>
            <td style="color:#aaa;font-size:12px;">${ETYPE_LABEL[it.entity_type] || it.entity_type}</td>
            <td style="color:#ddd;">${esc(it.title || '(未命名)')}</td>
            <td><span class="website-pill" style="background:${st[1]};color:${st[2]};">${st[0]}</span></td>
            <td style="color:#888;font-size:11px;">${when}${it.reviewed_by ? ` · ${esc(it.reviewed_by)}` : ''}</td>
            <td><button class="btn btn-sm" onclick="window._tr.open('${it.entity_type}','${esc(it.entity_id)}',this)">生成 / 檢視</button></td>
        </tr>`;
}

// ── 儲存設定 / 立即翻譯 ──

_tr.saveSettings = async () => {
    const v = (id) => document.getElementById(id);
    const payload = {
        enabled: v('tr-enabled').checked,
        cron: v('tr-cron').value.trim(),
        batch_size: Number(v('tr-batch').value) || 10,
        auto_approve: v('tr-auto').checked,
        brand_voice: v('tr-voice').value.trim(),
        glossary: v('tr-glossary').value.trim(),
    };
    try {
        _state.settings = await websiteFetch('/api/website/admin/translation/settings', { method: 'PUT', body: payload });
        toastOk('設定已儲存');
    } catch (e) { toastErr(e.message); }
};

_tr.runNow = async (btn) => {
    if (btn) { btn.disabled = true; btn.textContent = '啟動中…'; }
    try {
        const r = await websiteFetch('/api/website/admin/translation/run', { method: 'POST' });
        if (r.status === 'busy') toastErr('已有一輪翻譯在跑');
        else toastOk('已開始整批翻譯（背景執行，稍後重整看結果）');
    } catch (e) { toastErr(e.message); }
    if (btn) { btn.disabled = false; btn.textContent = '⚡ 立即翻譯整批'; }
};

// ── 單項：生成 → 審核 → 套用 ──

_tr.open = async (etype, eid, btn) => {
    if (btn) { btn.disabled = true; btn.textContent = '生成中…'; }
    let r;
    try {
        r = await websiteFetch(`/api/website/admin/translation/${etype}/${eid}/generate`, { method: 'POST' });
    } catch (e) { toastErr(e.message); r = null; }
    if (btn) { btn.disabled = false; btn.textContent = '生成 / 檢視'; }
    if (!r || !r.ok) { toastErr((r && r.error) || '生成失敗'); return; }
    _showReview(etype, eid, r);
};

function _showReview(etype, eid, r) {
    const zh = r.zh || {};
    const fields = r.fields || {};
    // 只給字串 _en 欄位做可編輯（body_en 是陣列，套用時原樣帶回）
    const strKeys = Object.keys(fields).filter(k => typeof fields[k] === 'string');
    const rows = strKeys.map(k => {
        const zhKey = k.replace(/_en$/, '');
        const label = FIELD_LABEL[k] || k;
        return `
            <div style="margin-bottom:14px;">
                <label style="color:#9aa0a6;font-size:11px;display:block;margin-bottom:3px;">${esc(label)}</label>
                <div style="color:#666;font-size:12px;background:#0d0d0d;border:1px solid #222;border-radius:4px;padding:6px 8px;margin-bottom:5px;white-space:pre-wrap;">${esc(zh[zhKey] || '（無中文）')}</div>
                <textarea data-en-key="${esc(k)}" rows="2" style="${_inp()};resize:vertical;">${esc(fields[k])}</textarea>
            </div>`;
    }).join('');
    const bodyNote = r.body_segments
        ? `<div style="color:#93c5fd;font-size:12px;margin-bottom:12px;">📄 內文 ${r.body_segments} 段已翻譯（套用後生效，此處不逐段編輯）</div>` : '';
    // 暫存 body_en 供套用帶回
    _tr._pendingBody = fields.body_en || null;
    _tr._pendingHash = r.source_hash || '';
    const inner = `
        <div style="padding:14px 18px;border-bottom:1px solid #2a2a2a;display:flex;justify-content:space-between;align-items:center;">
            <h3 style="margin:0;color:#fff;font-size:15px;">審核英文翻譯 <span style="color:#888;font-size:12px;">· ${ETYPE_LABEL[etype] || etype}</span></h3>
            <button onclick="window._tr.close()" style="background:#252525;border:1px solid #333;color:#aaa;cursor:pointer;width:30px;height:30px;border-radius:4px;">✕</button>
        </div>
        <div style="padding:18px;max-height:60vh;overflow:auto;">
            ${bodyNote}${rows || '<div style="color:#888;">沒有可編輯欄位</div>'}
        </div>
        <div style="padding:12px 18px;border-top:1px solid #2a2a2a;display:flex;justify-content:flex-end;gap:8px;">
            <button class="btn btn-ghost btn-sm" onclick="window._tr.close()">取消</button>
            <button class="btn" style="background:#059669;" onclick="window._tr.apply('${etype}','${esc(eid)}')">✓ 套用並核准</button>
        </div>`;
    openModal('tr-modal', inner, { width: '680px' });
}

_tr.close = () => closeModal('tr-modal');

_tr.apply = async (etype, eid) => {
    const fields = {};
    document.querySelectorAll('#tr-modal [data-en-key]').forEach(el => {
        const val = el.value.trim();
        if (val) fields[el.dataset.enKey] = val;
    });
    if (_tr._pendingBody) fields.body_en = _tr._pendingBody;
    try {
        await websiteFetch(`/api/website/admin/translation/${etype}/${eid}/apply`, {
            method: 'POST', body: { fields, source_hash: _tr._pendingHash },
        });
        toastOk('已核准並套用（對外站 60 秒後重建）');
        _tr.close();
        const audit = await websiteFetch('/api/website/admin/translation/audit');
        _state.audit = audit?.items || [];
        _renderShell();
    } catch (e) { toastErr(e.message); }
};
