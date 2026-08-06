/**
 * crm-projects-proposals.js — 專案管線裡的提案庫進程（2026-08-03 提案×專案整合）
 * ---
 * 提案在成案前對專案管理隱形 → 在「提案」「未成案」分頁頂端鑲提案卡列，
 * 總表給一條數字帶。資料源＝提案庫（/api/v1/proposals），**不複製狀態**：
 * 這裡只呈現與轉換（成案），提案本身的編輯走提案庫／共編頁。
 *
 * 同步機制：loadProjects() 尾端呼叫 syncProposalStrip()（咽喉 —— 所有改
 * state.filters 的路徑都經過 loadProjects），不要在個別事件 handler 再各掛一次。
 */

import { crmCacheFetch, esc } from './crm-utils.js';
import { openDeck, tfetch } from '../proposals/prop-fetch.js';
import { _createFormModal } from '../../js/shared/modal-styles.js';
import { loadProjects } from './crm-projects-core.js';
import { state } from './crm-projects-state.js';

const API = '/api/v1/proposals';
const HOST_ID = 'proj-proposal-strip';
const STYLE_ID = 'projprop-style';

// 提案狀態 → 落在哪個管線分頁。這是**分頁歸類**，不是狀態字典 —— 全清單正本在
// db/models.py PreprodProposal.status（前端 proposals.js STATUSES）；成案的變真專案列、
// 擱置的刻意不進管線。上游加新狀態時要決定它歸哪一頁（不歸＝不出現在管線）。
const STAGE_STATUSES = {
    '提案': ['草稿', '已提案', '入圍'],
    '未成案': ['未成案'],
};

const CSS = `
#${HOST_ID}:empty { display: none; }
#${HOST_ID} { flex: none; padding: 8px 12px 0; }
.projprop-cards { display: flex; flex-direction: column; gap: 6px; margin-bottom: 8px; }
.projprop-card { display: flex; gap: 10px; align-items: center; padding: 8px 12px;
  border: 1px dashed #3a3a3a; border-radius: 4px;
  background: rgba(59, 130, 246, .05); font-size: 12.5px; }
.projprop-card .t { font-weight: 600; cursor: pointer; }
.projprop-card .t:hover { color: #60a5fa; text-decoration: underline; }
.projprop-card .m { color: #8b8b8b; font-size: 11.5px; }
.projprop-badge { flex: none; font-size: 10.5px; padding: 1px 7px; border-radius: 2px;
  border: 1px solid #3b82f6; color: #60a5fa; }
.projprop-pill { flex: none; font-size: 10.5px; padding: 1px 7px; border-radius: 8px;
  background: #2a2a2a; color: #8b8b8b; }
.projprop-card .sp { flex: 1; }
.projprop-card button { background: none; border: 1px solid #3a3a3a;
  border-radius: 3px; color: inherit; font-size: 11.5px; padding: 3px 10px; cursor: pointer; }
.projprop-card button:hover { border-color: #3b82f6; color: #60a5fa; }
.projprop-bar { display: flex; gap: 10px; align-items: center; padding: 7px 12px;
  margin-bottom: 8px; border: 1px dashed #3a3a3a; border-radius: 4px;
  font-size: 12px; color: #8b8b8b; }
.projprop-bar b { color: #60a5fa; }
.projprop-bar a { color: #60a5fa; cursor: pointer; margin-left: auto; }
.projprop-bar a:hover { text-decoration: underline; }
`;

function _injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const st = document.createElement('style');
    st.id = STYLE_ID;
    st.textContent = CSS;
    document.head.appendChild(st);
}

// 30 秒快取：切分頁很頻繁，提案庫不需要每次都重抓；詳情「提案來源」也吃同一份
// （crmCacheFetch 走不了非 /api/v1/crm 路徑，故自持一份 —— TTL 與它同 30s 慣例）。
let _cache = null;
let _cacheAt = 0;

async function _proposals() {
    if (_cache && Date.now() - _cacheAt < 30_000) return _cache;
    _cache = (await tfetch(API)).proposals || [];
    _cacheAt = Date.now();
    return _cache;
}

// ── 共用卡片渲染（管線卡與詳情「提案來源」同一份模板）─────────
const _chip = (v, title) => v ? `<span class="m"${title ? ` title="${esc(title)}"` : ''}>${esc(v)}</span>` : '';

function _propCard(p, { badge, action = '', style = '' }) {
    return `
        <div class="projprop-card" data-pid="${esc(p.id)}"${style ? ` style="${style}"` : ''}>
            <span class="projprop-badge">${badge}</span>
            <span class="t" data-open>${esc(p.title)}</span>
            <span class="projprop-pill">${esc(p.status)}</span>
            ${_chip(p.client_name)}
            ${_chip(p.pitch_date && '提案日 ' + p.pitch_date)}
            ${_chip(p.budget_range)}
            ${_chip(p.refs_count && '參考 ' + p.refs_count)}
            ${p.deck_url ? '<a class="m" href="#" data-deck>deck ↗</a>' : ''}
            ${_chip(p.outcome_reason && '成案原因…', p.outcome_reason)}
            <span class="sp"></span>${action}
        </div>`;
}

// 事件委派綁在 host（innerHTML 重畫不掉監聽；每個 host 只綁一次）
function _wireHost(host) {
    if (host.dataset.wired) return;
    host.dataset.wired = '1';
    host.addEventListener('click', async (e) => {
        const pid = e.target.closest('[data-pid]')?.dataset.pid;
        if (!pid) return;
        if (e.target.closest('[data-convert]')) {
            _openConvertChooser((_cache || []).find(x => x.id === pid));
        } else if (e.target.closest('[data-deck]')) {
            // deck 可能在 NAS 資產夾（非 web root）→ 走帶權限下載
            e.preventDefault();
            openDeck(pid, (_cache || []).find(x => x.id === pid)?.deck_url);
        } else if (e.target.closest('[data-open]')) {
            window.open(`/proposal-plan.html?pid=${encodeURIComponent(pid)}`, '_blank', 'noopener');
        }
    });
}

/** 依 filters 狀態（或明確傳入的 override，如結案收件匣要清空）更新提案帶。
 *
 * 提案=專案合體（2026-08-06）後**正常情況這條帶永遠是空的** —— 提案誕生即
 * 建殼專案、存量也由 startup 遷移補完，全部就長在下面的專案列裡。留著純為
 * 防呆：萬一遷移失敗（DB 故障）產生孤兒提案，它們不會人間蒸發。
 * 空字串 innerHTML + `#proj-proposal-strip:empty{display:none}` = 完全不佔版面。
 */
export async function syncProposalStrip(statusOverride) {
    const host = document.getElementById(HOST_ID);
    if (!host) return;
    const stage = statusOverride !== undefined ? statusOverride : (state.filters.status || '');
    // 總表不再顯示提案數字帶（提案已在專案列內）→ 連提案 API 都不用打
    if (!(stage in STAGE_STATUSES)) { host.innerHTML = ''; return; }
    _injectStyle();
    _wireHost(host);

    let all;
    try {
        all = await _proposals();
    } catch (_) { host.innerHTML = ''; return; }   // 拿不到（罕見）就安靜隱藏，不擋專案列表

    const rows = all.filter(p => STAGE_STATUSES[stage].includes(p.status)
                             && !p.project_id);
    host.innerHTML = rows.length
        ? `<div class="projprop-cards">${rows.map(p => _propCard(p, {
            badge: '尚未入管線',
            action: stage === '提案' ? '<button data-convert>建立專案 →</button>' : '',
        })).join('')}</div>`
        : '';
}

// ── 專案詳情「提案來源」（成案回填 project_id 的提案反查）──────
// renderDetail 重畫很頻繁（每次 inline 編輯都來一次）→ 快取新鮮就本地過濾，零請求。
export async function renderProposalSource(projectId, host) {
    if (!host) return;
    let rows;
    try {
        if (_cache && Date.now() - _cacheAt < 30_000) {
            rows = _cache.filter(p => p.project_id === projectId);
        } else {
            rows = (await tfetch(`${API}?project_id=${encodeURIComponent(projectId)}`)).proposals || [];
        }
    } catch (_) { host.innerHTML = ''; return; }
    if (!rows.length) { host.innerHTML = ''; return; }
    _injectStyle();
    _wireHost(host);
    host.innerHTML = rows.map(p => _propCard(p, { badge: '提案來源', style: 'margin:8px 0 0;' })).join('');
}

// ── 成案 chooser（建立新專案 vs 連結既有專案）──────────────────
// 專案清單**自己抓全量**（/crm/projects 不帶篩選）—— state.projects 是當前分頁
// 過濾後的視圖（提案分頁只剩「提案」狀態專案），拿它當名錄會永遠找不到目標。
async function _openConvertChooser(p) {
    if (!p) return;
    let projects = [];
    try {
        projects = (await crmCacheFetch('projects', '/projects')).projects || [];
    } catch (_) { /* 名錄拿不到仍可走「建立新專案」 */ }

    _createFormModal({
        id: 'projprop-convert-modal',
        title: `成案：「${p.title}」`,
        submitLabel: '成案',
        fields: [
            { key: 'project_id', type: 'select', searchable: true,
              label: '連結既有專案（不選＝建立新專案）', placeholder: '搜尋專案名稱…',
              defaultValue: '',
              options: [{ value: '', label: `— 建立新專案（客戶帶「${p.client_name || '未選'}」）—` },
                        ...projects.map(pr => ({ value: pr.id,
                                                 label: `${pr.name}（${pr.status || ''}）` }))] },
            { key: 'outcome_reason', type: 'text', label: '成案原因（組織學習欄）',
              placeholder: '為什麼拿下這案…', hint: '會存進提案的 win/loss 學習欄。' },
        ],
        onSubmit: async (vals, setError, close) => {
            try {
                const d = await tfetch(`${API}/${encodeURIComponent(p.id)}/convert`, {
                    method: 'POST',
                    json: { project_id: vals.project_id, outcome_reason: vals.outcome_reason },
                });
                close();
                _cache = null;                      // 提案狀態變了，快取作廢
                await loadProjects();               // 咽喉會順帶 syncProposalStrip
                alert(d.linked_existing ? '已成案並連結既有專案 ✓' : '已成案，新專案已建立 ✓');
            } catch (e) { setError('成案失敗：' + (e.message || e)); }
        },
    });
}
