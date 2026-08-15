/**
 * delivery-view.js — 完稿結案（歸檔清單 + 專案回顧 + 官網上架編輯器）
 *
 * 直接嵌入「官網上架」那套完整編輯器（frontend/showcase-edit.html，?embed=1），
 * 兩邊 100% 同一個介面、同一批後端（token 路徑 /api/v1/crm/public/showcase-edit/{token}）。
 * showcase-edit 本就內建 parent-iframe 溝通（postMessage），embed 模式會隱藏自己的
 * 大頁首、把 accent 換成 CRM 藍、並回傳內容高度讓外框自動長高（免內部捲軸）。
 *
 * 1 專案 : N 作品（2026-07）：先 GET /projects/{id}/works —— 單作品時行為照舊
 * （單 iframe）；多作品時 iframe 上方出 tab 條逐作品切換（GET /works/{id}/edit-token
 * 換 src），另有「＋ 新增影片作品」直接 POST 建子作品並切過去。
 *
 * 好處：作品基本資料 / 精選圖 / AI 撰寫描述 / SEO / credits 雙模式 / 分類 全部即得，
 * 且不會再有「兩套編輯器各做一半、日後分岔」的問題。
 */

import { esc as _esc } from '../../js/shared/dom.js';
import { renderArchiveCard } from './archive-card.js';

// 🔴 **兩個呼叫端**：CRM 專案詳情、專案頁（/proposal-plan.html?id=）。所以
// host 與 fetcher 都由呼叫端給 —— 這個檔住在 `tabs/proposals/`（公開頁的
// import 封閉範圍內），不能靜態拉 tabs/crm 的 crmFetch；而寫死 DOM id
// （proj-detail-delivery / delivery-showcase-frame…）等於只服務得了一個呼叫端。
// 呼叫端範圍內的查找一律掛在 host 上；唯一問 document 的是高度監聽
// （全域一份，見 _bindEmbedHeightListener 的說明）。

let _embedMsgBound = false;

// 歸檔清單 + 專案回顧掛在最上方。靜態 import 同目錄的鄰居即可 ——
// 它的載入失敗由這裡的 try 接住，不會擋掉下面的編輯器。
async function _mountArchive(host, projectId, fetcher) {
    if (!host) return;
    try {
        await renderArchiveCard(host, projectId, fetcher);
    } catch (e) {
        host.innerHTML = `<div class="crm-empty" style="padding:12px;">歸檔清單載入失敗：${_esc(e.message || e)}</div>`;
    }
}

// showcase-edit（embed 模式）會 postMessage 內容高度 → 外框自動長高、避免雙捲軸。
// 每則訊息都重新 querySelectorAll（frame 是 class 不是 id）—— 換 src /
// 重建 iframe 都沿用同一個全域監聽，不必跟著重綁。
function _bindEmbedHeightListener() {
    if (_embedMsgBound) return;
    _embedMsgBound = true;
    window.addEventListener('message', (e) => {
        const d = e && e.data;
        if (!d || d.type !== 'showcase-embed-height') return;
        // 全域監聽、但只餵還活著的那些 frame（兩個呼叫端可能各有一個）
        document.querySelectorAll('.delivery-showcase-frame').forEach(f => {
            if (d.height) f.style.height = d.height + 'px';
        });
    });
}

// ── Main Load ──────────────────────────────────────────────

export async function loadDeliveryTab(projectId, opts = {}) {
    // host 一律由呼叫端給，**沒有 DOM id fallback** —— 留著 fallback 的話，
    // 第三個呼叫端忘了傳 host 會靜默畫進 CRM 的容器（或一個藏著的），
    // 而不是當場失敗
    const container = opts.host;
    const _fetch = opts.fetcher;
    if (!container || !_fetch) return;

    // 歸檔清單先立殼並開始載入 —— 它是**專案層級**資料，跟「這支片要不要上官網」
    // 無關。放在下面的 works/token 邏輯之後的話，取不到編輯權杖的專案（還沒建
    // showcase、或不打算上官網）就永遠看不到自己的歸檔清單與專案回顧。
    container.innerHTML = `
        <div class="delivery-archive"></div>
        <div class="delivery-showcase crm-empty">載入中...</div>`;
    // 不 await：歸檔要打 DB，別讓編輯器等它
    _mountArchive(container.querySelector('.delivery-archive'), projectId, _fetch);
    const showcase = container.querySelector('.delivery-showcase');

    // 先撈作品清單決定單/多作品 UI（端點失敗 → 走舊單作品路徑）
    let works = [];
    try {
        const r = await _fetch('/projects/' + projectId + '/works');
        works = (r && r.items) || [];
    } catch { /* fallback：單作品舊路徑 */ }

    // 作品列永遠顯示（單作品也一顆 tab + 新增按鈕）— 否則單作品專案在此分頁
    // 沒有任何「加第二支影片」的入口（雞生蛋）。works 端點失敗才走 legacy 單 iframe。
    if (works.length >= 1) {
        showcase.className = '';
        showcase.innerHTML = `
        <div class="delivery-works-tabs" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:8px;"></div>
        <iframe class="delivery-showcase-frame" title="作品上架編輯"
                style="width:100%;border:0;min-height:640px;display:block;background:#0e0e0e;border-radius:8px;"></iframe>`;
        _bindEmbedHeightListener();
        const primary = works.find(w => w.is_primary) || works[0];  // items 主作品先，保險再 find 一次
        _renderDeliveryTabs(showcase, projectId, works, primary.id, _fetch);
        await _selectDeliveryWork(showcase, primary.id, _fetch);
        return;
    }

    // ── Legacy fallback（works 端點打不到時）：既有 token 取得邏輯 + 單 iframe ──
    // 取 showcase → 拿 edit_token；沒有就用現成端點鑄一把
    let token = '';
    try {
        const sc = await _fetch('/projects/' + projectId + '/showcase');
        token = (sc && sc.edit_token) || '';
        if (!token) {
            const t = await _fetch('/projects/' + projectId + '/showcase/generate-edit-token', {
                method: 'POST', body: '{}',
            });
            token = (t && t.token) || '';
        }
    } catch (e) {
        // 只換掉上架編輯器那塊 —— 歸檔清單照常留在上面
        showcase.innerHTML = '載入失敗：' + _esc(e.message || String(e));
        return;
    }
    if (!token) {
        showcase.innerHTML = '無法取得編輯權杖';
        return;
    }

    const src = location.origin + '/showcase-edit.html?token=' + encodeURIComponent(token) + '&embed=1';
    showcase.className = '';
    showcase.innerHTML = `
        <iframe class="delivery-showcase-frame" src="${_esc(src)}" title="作品上架編輯"
                style="width:100%;border:0;min-height:640px;display:block;background:#0e0e0e;border-radius:8px;"></iframe>`;
    _bindEmbedHeightListener();
}

// ── 多作品 tab 條 ──────────────────────────────────────────

function _renderDeliveryTabs(container, projectId, works, activeId, _fetch) {
    const bar = container.querySelector('.delivery-works-tabs');
    if (!bar) return;
    bar.innerHTML = works.map(w => {
        const active = String(w.id) === String(activeId);
        const primaryTag = w.is_primary ? ' <span style="font-size:10px;opacity:0.75;">主</span>' : '';
        return `<button class="crm-btn crm-btn-sm ${active ? 'crm-btn-primary' : 'crm-btn-secondary'}"
                        data-work-id="${_esc(w.id)}">${_esc(w.title || '（未命名作品）')}${primaryTag}</button>`;
    }).join('') + `
        <button class="crm-btn crm-btn-secondary crm-btn-sm" data-add-work="1"
                title="在此專案下新增一個官網作品">＋ 新增影片作品</button>`;

    bar.querySelectorAll('button[data-work-id]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const wid = btn.dataset.workId;
            _renderDeliveryTabs(container, projectId, works, wid, _fetch);  // 先亮 tab
            await _selectDeliveryWork(container, wid, _fetch);
        });
    });

    const addBtn = bar.querySelector('button[data-add-work]');
    if (addBtn) addBtn.addEventListener('click', async () => {
        addBtn.disabled = true;
        try {
            // POST 已回 {id, title}，直接補進清單重繪 — 免再 GET 一次
            const r = await _fetch('/projects/' + projectId + '/works', { method: 'POST', body: '{}' });
            const fresh = works.concat([{ id: r.id, title: r.title, is_primary: false }]);
            _renderDeliveryTabs(container, projectId, fresh, r.id, _fetch);
            await _selectDeliveryWork(container, r.id, _fetch);
        } catch (e) {
            alert('新增作品失敗：' + (e.message || e));
            addBtn.disabled = false;
        }
    });
}

// workId → 編輯器 URL 快取：後端 edit-token 是 reuse 語義（同 token 穩定回傳），
// 同一顆 tab 反覆點擊不用重打 API
const _workEditUrlCache = new Map();

async function _selectDeliveryWork(container, workId, _fetch) {
    const f = container.querySelector('.delivery-showcase-frame');
    if (!f) return;
    try {
        let url = _workEditUrlCache.get(workId);
        if (!url) {
            const t = await _fetch('/works/' + workId + '/edit-token');
            url = (t && t.url) || ((t && t.token) ? '/showcase-edit.html?token=' + encodeURIComponent(t.token) : '');
            if (!url) throw new Error('無法取得編輯權杖');
            if (url.startsWith('/')) url = location.origin + url;
            if (!url.includes('embed=1')) url += (url.includes('?') ? '&' : '?') + 'embed=1';
            _workEditUrlCache.set(workId, url);
        }
        f.src = url;
    } catch (e) {
        alert('切換作品失敗：' + (e.message || e));
    }
}

/** 專案頁 lazy-tab 表要的 (host, opts) 轉接口 —— 本尊簽名由 CRM 呼叫端
 *  持有，轉接寫在這裡，那張表就不必為了一支特例長出旗標與分支。 */
export const renderDelivery = (host, opts) =>
    loadDeliveryTab(opts.projectId, { ...opts, host });

// ── Init (called once at module load) ──────────────────────

export function initDeliveryHandlers() {
    // iframe 自帶所有事件；此處無全域 handler 需綁定。
}
