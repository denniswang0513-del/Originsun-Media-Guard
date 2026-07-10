/**
 * crm-projects-delivery.js — 完稿結案 Tab
 *
 * 直接嵌入「官網上架」那套完整編輯器（frontend/showcase-edit.html，?embed=1），
 * 兩邊 100% 同一個介面、同一批後端（token 路徑 /api/v1/crm/public/showcase-edit/{token}）。
 * showcase-edit 本就內建 parent-iframe 溝通（postMessage），embed 模式會隱藏自己的
 * 大頁首、把 accent 換成 CRM 藍、並回傳內容高度讓外框自動長高（免內部捲軸）。
 *
 * 好處：作品基本資料 / 精選圖 / AI 撰寫描述 / SEO / credits 雙模式 / 分類 全部即得，
 * 且不會再有「兩套編輯器各做一半、日後分岔」的問題。
 */

import { crmFetch as _fetch, esc as _esc } from './crm-utils.js';

let _embedMsgBound = false;

// ── Main Load ──────────────────────────────────────────────

export async function loadDeliveryTab(projectId) {
    const container = document.getElementById('proj-detail-delivery');
    if (!container) return;

    container.innerHTML = '<div class="crm-empty">載入中...</div>';

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
        container.innerHTML = '<div class="crm-empty">載入失敗：' + _esc(e.message || String(e)) + '</div>';
        return;
    }
    if (!token) {
        container.innerHTML = '<div class="crm-empty">無法取得編輯權杖</div>';
        return;
    }

    const src = location.origin + '/showcase-edit.html?token=' + encodeURIComponent(token) + '&embed=1';
    container.innerHTML = `
        <iframe id="delivery-showcase-frame" src="${_esc(src)}" title="作品上架編輯"
                style="width:100%;border:0;min-height:640px;display:block;background:#0e0e0e;border-radius:8px;"></iframe>`;

    // showcase-edit（embed 模式）會 postMessage 內容高度 → 外框自動長高、避免雙捲軸
    if (!_embedMsgBound) {
        _embedMsgBound = true;
        window.addEventListener('message', (e) => {
            const d = e && e.data;
            if (!d || d.type !== 'showcase-embed-height') return;
            const f = document.getElementById('delivery-showcase-frame');
            if (f && d.height) f.style.height = d.height + 'px';
        });
    }
}

// ── Init (called once at module load) ──────────────────────

export function initDeliveryHandlers() {
    // iframe 自帶所有事件；此處無全域 handler 需綁定。
}
