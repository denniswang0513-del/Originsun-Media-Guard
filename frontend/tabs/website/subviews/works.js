/**
 * works.js — 作品集管理子視圖
 *
 * 網站管理員（role=website_admin）的作品全流程：列表/搜尋/篩選、新增、編輯、
 * 公開/精選切換。編輯 UI 透過 iframe 嵌入 /showcase-edit.html?token=XXX —
 * 重用既有 showcase-edit.html 避免重寫 544 行的 CRM 完稿 Tab 編輯器。
 */
import { websiteFetch, esc, toastOk, toastErr, renderLoadError, debounce } from '../website-utils.js';

let _works = [];
let _categories = [];
let _seoAudit = null;  // Map<project_id, auditItem>；null = audit 端點不可用

async function _reloadWorks() {
    try {
        const [res] = await Promise.all([
            websiteFetch('/api/website/admin/works?include_non_public=true'),
            _reloadSeoAudit(),
        ]);
        _works = res?.items || [];
        _renderTable();
    } catch { /* silently skip — user can reload manually */ }
}

// AI SEO audit：重抓所有公開作品的 SEO 完整度（completeness 0-6），給狀態欄用。
async function _reloadSeoAudit() {
    try {
        const res = await websiteFetch('/api/website/admin/seo/projects/audit');
        _seoAudit = new Map((res?.items || []).map(it => [it.project_id, it]));
    } catch (e) {
        // 端點失敗（NAS website-api 跑舊版沒此 router）→ 保持 _seoAudit 原值，_seoCell 顯示「—」
        console.warn('[website/works] SEO audit 載入失敗:', e?.message || e);
    }
}

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    container.innerHTML = '<h2>作品集管理</h2><div style="color:#888;padding:20px;">載入中…</div>';
    let portfolioPdfUrl = '';
    try {
        const [worksRes, catsRes, settingsRes, auditRes] = await Promise.all([
            websiteFetch('/api/website/admin/works?include_non_public=true'),
            websiteFetch('/api/website/admin/categories'),
            websiteFetch('/api/website/admin/settings'),
            websiteFetch('/api/website/admin/seo/projects/audit').catch(() => null),
        ]);
        if (!isCurrent()) return;
        _works = worksRes?.items || [];
        _categories = catsRes?.items || [];
        portfolioPdfUrl = (settingsRes?.settings?.['portfolio.pdf_url'] || '').toString();
        _seoAudit = auditRes ? new Map((auditRes.items || []).map(it => [it.project_id, it])) : null;
    } catch (e) {
        if (!isCurrent()) return;
        renderLoadError(container, '作品集管理', e);
        return;
    }

    container.innerHTML = `
        <h2>作品集管理 <span style="color:#888;font-size:13px;font-weight:400;">· ${_works.length} 件作品</span></h2>

        <div class="card" style="border-left:3px solid #c8a45c;margin-bottom:12px;">
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                <label style="color:#ddd;font-size:13px;font-weight:600;white-space:nowrap;">歷年作品 PDF</label>
                <input id="portfolio-pdf-url" type="url" value="${esc(portfolioPdfUrl)}"
                    placeholder="https://drive.google.com/... 或 https://originsun-studio.com/files/portfolio.pdf"
                    style="flex:1;min-width:320px;" />
                <button class="btn btn-sm" onclick="window._websiteSavePortfolioPdf()">儲存</button>
                ${portfolioPdfUrl
                    ? `<a class="btn btn-sm btn-ghost" href="${esc(portfolioPdfUrl)}" target="_blank" rel="noopener">預覽</a>`
                    : ''}
            </div>
            <div style="color:#888;font-size:11px;margin-top:6px;">
                填值後 /portfolio 頁面頂部會顯示「下載作品集 PDF」按鈕；留空則隱藏。儲存後 60 秒內對外網站重 build。
            </div>
        </div>

        <div style="margin-bottom:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
            <button class="btn" onclick="window._websiteNewWork()" style="background:#059669;">新增作品</button>
            <input id="works-filter" type="text" placeholder="搜尋標題 / 客戶 / slug…" style="flex:1;min-width:240px;max-width:320px;" />
            <select id="works-cat-filter" style="min-width:140px;">
                <option value="">所有分類</option>
                ${_categories.map(c => `<option value="${c.id}">${esc(c.name_zh)}</option>`).join('')}
            </select>
            <label style="color:#ccc;font-size:12px;display:flex;align-items:center;gap:6px;">
                <input type="checkbox" id="works-public-only" /> 只顯示已公開
            </label>
        </div>

        <div class="card" style="padding:0;">
            <table id="works-table"></table>
        </div>
    `;

    document.getElementById('works-filter').addEventListener('input', debounce(_renderTable, 150));
    document.getElementById('works-cat-filter').addEventListener('change', _renderTable);
    document.getElementById('works-public-only').addEventListener('change', _renderTable);
    _renderTable();
    _ensureEditPanel();
}


window._websiteSavePortfolioPdf = async () => {
    const url = (document.getElementById('portfolio-pdf-url')?.value || '').trim();
    if (url && !/^https?:\/\//i.test(url)) {
        toastErr('PDF 連結必須是 http:// 或 https:// 開頭');
        return;
    }
    try {
        await websiteFetch('/api/website/admin/settings', {
            method: 'PUT',
            body: { values: { 'portfolio.pdf_url': url } },
        });
        toastOk(url ? '已儲存 PDF 連結（60 秒後對外網站重 build）' : '已清除 PDF 連結');
    } catch (e) { toastErr(e.message || '儲存失敗'); }
};

function _renderTable() {
    const table = document.getElementById('works-table');
    if (!table) return;
    const q = (document.getElementById('works-filter')?.value || '').toLowerCase().trim();
    const catId = document.getElementById('works-cat-filter')?.value || '';
    const publicOnly = document.getElementById('works-public-only')?.checked;

    const rows = _works.filter(w => {
        if (publicOnly && !w.public) return false;
        if (q && !`${w.public_title || ''}${w.name || ''}${w.client || ''}${w.slug || ''}`.toLowerCase().includes(q)) return false;
        if (catId) {
            const catSlug = _categories.find(c => c.id === Number(catId))?.slug;
            if (!catSlug || !w.categories?.includes(catSlug)) return false;
        }
        return true;
    });

    if (!rows.length) {
        table.innerHTML = '<tr><td colspan="11" style="color:#888;text-align:center;padding:30px;">沒有符合條件的作品</td></tr>';
        return;
    }

    // 唯讀狀態徽章 — 比 disabled checkbox 更顯眼，使用者能一眼看出 on/off
    // 修改入口在右側「編輯」按鈕，不靠 inline toggle。on = 實心藍方塊、off = 空心方塊
    const _roBadge = (on) => on
        ? '<span style="display:inline-flex;width:22px;height:22px;background:#3b82f6;border-radius:4px;cursor:default;"></span>'
        : '<span style="display:inline-flex;width:22px;height:22px;background:transparent;border:1px solid #4b5563;border-radius:4px;cursor:default;"></span>';

    // 1 專案 : N 作品 — w.id !== w.project_id 代表同專案下的子作品
    const _subBadge = (w) => (w.project_id && w.id !== w.project_id)
        ? '<span title="同專案下的子作品" style="display:inline-block;margin-left:5px;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600;background:#1e3a5f;color:#93c5fd;vertical-align:middle;white-space:nowrap;">子作品</span>'
        : '';

    table.innerHTML = `
        <thead>
            <tr>
                <th>縮圖</th>
                <th>客戶</th>
                <th>標題</th>
                <th>slug</th>
                <th>分類</th>
                <th>年份</th>
                <th>公開</th>
                <th>精選</th>
                <th title="個別作品強制 noindex（站級允許索引仍會被擋）">noindex</th>
                <th title="AI 自動生成的作品 SEO 內容（標題／描述／關鍵字／長文／FAQ）">AI SEO</th>
                <th title="作品內容填寫狀況：影片／圖／說明（專案描述）／credits">完成度</th>
                <th>操作</th>
            </tr>
        </thead>
        <tbody>
            ${rows.map(w => {
                const thumb = w.youtube_id
                    ? `<img src="https://img.youtube.com/vi/${esc(w.youtube_id)}/default.jpg" style="width:80px;height:45px;object-fit:cover;border-radius:3px;background:#000;" onerror="this.style.visibility='hidden'" />`
                    : '<div style="width:80px;height:45px;background:#333;border-radius:3px;"></div>';
                return `
                <tr data-id="${esc(w.id)}">
                    <td>${thumb}</td>
                    <td style="color:#fff;">${esc(w.client || '-')}</td>
                    <td style="color:#fff;">${esc(w.title || w.name)}${_subBadge(w)}</td>
                    <td style="color:#888;font-size:12px;">
                        ${esc(w.slug || '(未設)')}
                        ${w.redirect_count > 0
                            ? `<span title="此作品有 ${w.redirect_count} 條舊 slug 被 301 轉址到目前 slug" style="color:#3b82f6;margin-left:6px;">轉址 ${w.redirect_count}</span>`
                            : ''}
                    </td>
                    <td>${(w.categories || []).map(s => `<span class="website-pill">${esc(s)}</span>`).join(' ') || '<span style="color:#666;">-</span>'}</td>
                    <td>${w.year ?? '-'}</td>
                    <td title="${w.public ? '已公開' : '未公開'}（僅顯示，請進編輯頁修改）">${_roBadge(w.public)}</td>
                    <td title="${w.featured ? '已設精選' : '未設精選'}（僅顯示，請進編輯頁修改）">${_roBadge(w.featured)}</td>
                    <td title="${w.noindex ? '已設 noindex' : '允許索引'}（僅顯示，請進編輯頁修改）">${_roBadge(w.noindex)}</td>
                    <td>${_seoCell(w)}</td>
                    <td>${_compCell(w.completeness)}</td>
                    <td style="white-space:nowrap;">
                        <button class="btn btn-sm" onclick="window._websiteEditWork('${esc(w.id)}')">編輯</button>
                        <button class="btn btn-sm btn-ghost" title="在同一專案下新增子作品（開啟編輯器，關閉時未填內容會自動清掉空殼）" onclick="window._websiteAddSubWork('${esc(w.project_id || w.id)}')">＋子作品</button>
                    </td>
                </tr>
                `;
            }).join('')}
        </tbody>
    `;
}



// 完成度欄：影片／圖／說明（專案描述）／credits 填寫狀況（與結案看板同邏輯）
function _compChip(label, ok) {
    const on = !!ok;
    return `<span title="${esc(label)}${on ? '：已完成' : '：未完成'}" style="display:inline-flex;align-items:center;gap:1px;padding:1px 5px;border-radius:4px;font-size:10px;font-weight:600;white-space:nowrap;background:${on ? '#14532d' : '#2a2a2a'};color:${on ? '#86efac' : '#6b7280'};">${on ? '✓' : '✗'}${esc(label)}</span>`;
}
function _compCell(c) {
    if (!c) return '<span style="color:#666;">-</span>';
    return `<div style="display:flex;gap:3px;flex-wrap:wrap;">${_compChip('影片', c.video)}${_compChip('圖', c.images)}${_compChip('說明', c.description)}${_compChip('credits', c.credits)}</div>`;
}

// ══════════════════════════════════════════════════════════
// AI SEO 狀態欄
// ══════════════════════════════════════════════════════════
// 後端 AI SEO（services/website/seo_runner.py）用 `claude --print` 自動補
// 作品 SEO 內容（標題／描述／關鍵字／長文／key_facts／FAQ）。audit 端點
// 回每件公開作品的 completeness（0-6）：
//   completeness 0  → 待生成（給「生成」按鈕）
//   completeness >0 → 已生成（顯示 n/6）
// 非公開作品不適用（後端 get_project_seo_draft_context 會擋未公開作品）。

function _seoCell(w) {
    if (_seoAudit === null) {
        // audit 端點不可用（NAS website-api 跑舊版沒 admin_seo router）
        return '<span style="color:#666;font-size:11px;" title="AI SEO audit 端點不可用，請在 master 跑 /publish 同步後端到 NAS">—</span>';
    }
    if (!w.public) {
        return '<span style="color:#666;font-size:11px;" title="作品未公開，AI SEO 不適用（需先公開作品才能生成）">未公開</span>';
    }
    const score = _seoAudit.get(w.id)?.completeness || 0;
    if (score > 0) {
        return `<span style="color:#4ade80;font-size:12px;white-space:nowrap;" title="AI SEO 已生成（內容完整度 ${score}/6）">已生成 <span style="color:#888;">${score}/6</span></span>`;
    }
    return `
        <div style="display:flex;align-items:center;gap:6px;white-space:nowrap;">
            <span style="color:#f59e0b;font-size:12px;">待生成</span>
            <button class="btn btn-sm" style="background:#7c3aed;"
                title="用 AI 自動生成此作品的 SEO 內容（約 30-60 秒）"
                onclick="window._websiteGenSeo('${esc(w.id)}', this)">生成</button>
        </div>`;
}

// 單筆 AI SEO 生成 — 呼叫既有 /seo/projects/{id}/run（claude --print，約 30-60 秒/筆）。
// 完成後重抓 audit 並重畫表格，狀態欄即時翻成「已生成」。
window._websiteGenSeo = async (pid, btn) => {
    if (btn) { btn.disabled = true; btn.textContent = '生成中…'; }
    try {
        const r = await websiteFetch(`/api/website/admin/seo/projects/${pid}/run`, { method: 'POST' });
        if (r?.status === 'busy') {
            toastErr('已有 AI SEO 任務在執行，請稍後再試');
        } else if ((r?.errors || 0) > 0) {
            const detail = (r.works || [])[0]?.detail || r.error || '未知錯誤';
            toastErr('生成失敗：' + detail);
        } else if ((r?.processed || 0) > 0) {
            toastOk('AI SEO 已生成（60 秒後對外網站重 build）');
        } else {
            toastErr('生成未完成 — 後端未回報任何處理結果');
        }
    } catch (e) {
        toastErr('生成失敗：' + (e.message || e));
    } finally {
        await _reloadSeoAudit();
        _renderTable();   // 重畫 → 該列狀態翻新、loading 按鈕一併被取代
    }
};


// ══════════════════════════════════════════════════════════
// Edit panel（iframe 嵌 /showcase-edit.html?token=XXX）
// ══════════════════════════════════════════════════════════

// iframe → parent postMessage 監聽：
//   showcase-saved   → reload 列表（public_title / 客戶 / 公開狀態都可能變了）
//   showcase-title-change → live update 上方「編輯：XXX」label，三處紅框同步
let _msgListenerInstalled = false;
function _installMessageListener() {
    if (_msgListenerInstalled) return;
    _msgListenerInstalled = true;
    window.addEventListener('message', (e) => {
        const t = e?.data?.type;
        if (t === 'showcase-saved') {
            _reloadWorks();
        } else if (t === 'showcase-title-change') {
            const titleEl = document.getElementById('website-edit-panel-title');
            if (titleEl && e.data.title) titleEl.textContent = `編輯：${e.data.title}`;
        }
    });
}

function _ensureEditPanel() {
    _installMessageListener();
    if (document.getElementById('website-edit-panel-overlay')) return;
    const overlay = document.createElement('div');
    overlay.id = 'website-edit-panel-overlay';
    overlay.style.cssText = `
        position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9000;
        display:none;align-items:stretch;justify-content:flex-end;
    `;
    overlay.innerHTML = `
        <div id="website-edit-panel" style="
            width:75%;max-width:960px;height:100%;background:#0e0e0e;
            border-left:1px solid #2a2a2a;display:flex;flex-direction:column;
            box-shadow:-8px 0 24px rgba(0,0,0,0.6);
        ">
            <div style="display:flex;align-items:center;gap:12px;padding:10px 14px;border-bottom:1px solid #2a2a2a;background:#161616;flex-shrink:0;">
                <strong id="website-edit-panel-title" style="color:#fff;font-size:14px;flex:1;">編輯作品</strong>
                <button class="btn btn-sm btn-ghost" onclick="window._websiteCloseEditPanel()">關閉並重新整理</button>
            </div>
            <iframe id="website-edit-panel-iframe" style="flex:1;width:100%;border:0;background:#0e0e0e;"></iframe>
        </div>
    `;
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) window._websiteCloseEditPanel();
    });
    document.body.appendChild(overlay);
}

// 從「跳過小表單」流程建出來的 skeleton project_id；overlay 關閉時呼叫
// if-skeleton 清掉沒被填內容的孤兒紀錄。從「編輯」既有作品開的 overlay 不會
// 設這個值，關閉時不會誤刪。
let _pendingSkeletonId = null;

function _openEditPanel(url, title, { skeletonProjectId = null } = {}) {
    _ensureEditPanel();
    const overlay = document.getElementById('website-edit-panel-overlay');
    const iframe = document.getElementById('website-edit-panel-iframe');
    const titleEl = document.getElementById('website-edit-panel-title');
    if (titleEl) titleEl.textContent = title || '編輯作品';
    iframe.src = url;
    overlay.style.display = 'flex';
    _pendingSkeletonId = skeletonProjectId;
}

window._websiteCloseEditPanel = async () => {
    const overlay = document.getElementById('website-edit-panel-overlay');
    if (!overlay) return;
    overlay.style.display = 'none';
    const iframe = document.getElementById('website-edit-panel-iframe');
    if (iframe) iframe.src = 'about:blank';

    // 跳過小表單流程下的 skeleton：使用者開了 overlay 但什麼都沒填就關掉
    // → 呼叫 if-skeleton 讓後端決定刪不刪（後端 sanity check name + 各 public_*
    // 是否仍空）。await 完才 reload，避免列表還秀著待刪的 skeleton。
    const skId = _pendingSkeletonId;
    _pendingSkeletonId = null;
    if (skId) {
        try {
            await websiteFetch(`/api/website/admin/works/${skId}/if-skeleton`, { method: 'DELETE' });
        } catch (e) {
            // 失敗就留著（DB 中保留 skeleton，下次列表會看到，使用者可手動處理）
            console.warn('[website/works] if-skeleton 清理失敗:', e.message || e);
        }
    }
    _reloadWorks();
};

window._websiteEditWork = async (pid) => {
    try {
        const r = await websiteFetch(`/api/website/admin/works/${pid}/edit-url`, { method: 'POST' });
        const w = _works.find(x => x.id === pid);
        _openEditPanel(r.edit_url, `編輯：${w?.public_title || w?.name || pid}`);
    } catch (e) {
        toastErr(e.message);
    }
};


// ══════════════════════════════════════════════════════════
// 新增作品：直接建 skeleton 後跳編輯 overlay（跳過小表單）
// ══════════════════════════════════════════════════════════
// 客戶/年份/分類都改在編輯頁裡填。Overlay 關閉時 _websiteCloseEditPanel
// 會呼叫 /works/{id}/if-skeleton；後端確認 name 仍是 sentinel 且各 public_*
// 都沒填內容才刪掉，避免使用者開了又馬上關留下空殼紀錄。

window._websiteNewWork = async () => {
    try {
        // POST 不帶 body — 後端塞 sentinel name「（未命名作品）」、status 預設「已結案」
        const r = await websiteFetch('/api/website/admin/works/create', {
            method: 'POST', body: {},
        });
        toastOk('已建立空作品 — 在編輯頁填寫資訊後按儲存');
        _openEditPanel(r.edit_url, `編輯：${r.name}`, { skeletonProjectId: r.id });
    } catch (e) {
        toastErr(e.message || '建立失敗');
    }
};

// ══════════════════════════════════════════════════════════
// 新增子作品：同一 CRM 專案下再掛一個官網作品（1 專案 : N 作品）
// ══════════════════════════════════════════════════════════
// title 留空讓後端補預設；關閉 overlay 時 if-skeleton 已支援子作品 id，
// 未填內容的空殼會被清掉（不動專案本身）。

window._websiteAddSubWork = async (projectId) => {
    try {
        const r = await websiteFetch(`/api/website/admin/projects/${projectId}/works`, {
            method: 'POST', body: {},
        });
        toastOk('已建立子作品 — 在編輯頁填寫內容後按儲存');
        _openEditPanel(r.edit_url, `編輯：${r.title || '新子作品'}`, { skeletonProjectId: r.id });
    } catch (e) {
        toastErr(e.message || '建立子作品失敗');
    }
};
