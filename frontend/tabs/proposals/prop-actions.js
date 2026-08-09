/**
 * prop-actions.js — 提案的「動作層」：狀態管線、成案轉專案、刪除、簡報、參考片。
 *
 * 後台「📑 提案庫」、CRM 專案的提案帶、獨立頁 /proposal-plan.html 共用同一份。
 * 理由同 pins-store：真正會漂移的不是版面，是**端點與規則**。實際發生過 ——
 * 「成案」原本有三份實作，其中 CRM 那份的組織學習原因是選填、提案庫那份是
 * 必填，同一個守門從不同入口進去鬆緊不一樣。
 *
 * 🔴 規則的權威在後端 `routers/api_proposals.py`（狀態機、convert 的副作用都在
 * 那裡）。這裡只負責「前端怎麼問、送哪支端點」。
 *
 * 部署限制：**凡是 /proposal-plan.html 或 /reference.html 的 import 閉包裡的檔案**
 * 都只能靜態 import `tabs/proposals` 與 `js/shared` —— NAS 對外容器只 serve 這兩個
 * 子目錄（正本在 `core/public_assets.py`，由 `tests/unit/test_public_surface.py`
 * 釘住），拉到 crm-utils / website-utils 會讓客戶那頁在 NAS 上直接載入失敗。
 * 同目錄的 proposals.js / proposal-folders.js **不受此限**（SPA 專用），別照著
 * 它們抄。需要 CRM 的東西時走動態 import + catch（範例見 prop-fetch.js）。
 *
 * ⚠️ 那個守衛只驗 **import**，不驗 **fetch**。所以「在閉包裡」不等於「NAS 上跑得動」：
 * prop-editor 打 /api/v1/crm/clients，而 NAS 的 nginx 沒有那條 location —— 它只在
 * 登入模式（master/foundry）載入，客戶那條路碰不到。新增會打 CRM 端點的檔案時，
 * 要自己確認它不在客戶走的路徑上。
 *
 * 對話走 confirm/prompt/alert：跟原本行為逐字相同。這一輪刻意不換 UI ——
 * 換的話兩個介面的確認流程會在同一次改動裡同時變動，出事很難分辨是哪一半。
 */

import { tfetch } from './prop-fetch.js';
import { API } from './prop-const.js';

// 這裡**不** re-export 常數：分界是「常數一律 prop-const、動作一律
// prop-actions」。轉出來的話就等於把兩者的依賴差異又抹掉，讀者每次
// import 都要重做一次決定（而分家的整個理由就是依賴輪廓不同）。

const DECK_MAX_BYTES = 50 * 1024 * 1024;

/**
 * 狀態轉換。**不只是寫一個欄位** —— 成案會推進（或建立）CRM 專案、
 * 成案/未成案都要留下組織學習的原因。
 *
 * @returns {Promise<{ok: boolean, message?: string}>}
 *          ok:false = 使用者取消或沒過守門（該問的已經問過了，呼叫端只要把
 *          下拉還原）。真失敗會 throw。
 *          成功訊息回傳給呼叫端去顯示 —— 這個模組不決定成功要怎麼呈現。
 */
export async function changeStatus(prop, next) {
    if (next === '成案') {
        if (prop.project_id) {
            // 提案＝專案合體：本來就有連結專案 → PUT，後端把還在前期的專案
            // 推進「製作」階段
            const reason = prompt('成案原因（必填 — 組織學習欄）', prop.outcome_reason || '');
            if (reason === null) return { ok: false };
            if (!reason.trim()) { alert('成案原因必填'); return { ok: false }; }
            await tfetch(`${API}/${prop.id}`,
                { method: 'PUT', json: { status: next, outcome_reason: reason.trim() } });
            return { ok: true, message: '已成案 ✅ 連結的專案已推進「製作」階段' };
        }
        // legacy 無專案提案（缺客戶未遷移）→ 建一個新專案
        if (!confirm(`確定成案？將自動建立 CRM 專案「${prop.title}」`)) return { ok: false };
        const reason = prompt('成案原因（組織學習欄，建議填寫；可留空）', prop.outcome_reason || '');
        if (reason === null) return { ok: false };
        const d = await convertToProject(prop, { reason });
        return { ok: true, message: '已成案 ✅ 已自動建立 CRM 專案（project_id: ' + d.project_id + '）' };
    }
    if (next === '未成案') {
        const reason = prompt('未成案原因（必填 — 組織學習欄）', prop.outcome_reason || '');
        if (reason === null) return { ok: false };
        if (!reason.trim()) { alert('未成案原因必填'); return { ok: false }; }
        await tfetch(`${API}/${prop.id}`,
            { method: 'PUT', json: { status: next, outcome_reason: reason.trim() } });
        return { ok: true };
    }
    await tfetch(`${API}/${prop.id}`, { method: 'PUT', json: { status: next } });
    return { ok: true };
}

/**
 * 成案轉專案：`projectId` 給了就連結既有專案，沒給就讓後端開一個新的。
 *
 * 端點與 payload 收在這裡 —— 呼叫端有兩個（提案庫的狀態下拉、CRM 專案帶的
 * 成案選擇器），它們的**問法**不同（一個只問原因、一個要先選專案），但送出去
 * 的東西必須一樣。
 */
export function convertToProject(prop, { projectId = '', reason = '' } = {}) {
    const json = { outcome_reason: (reason || '').trim() };
    if (projectId) json.project_id = projectId;
    return tfetch(`${API}/${prop.id}/convert`, { method: 'POST', json });
}

/** 刪除提案（含確認）。@returns 真的刪了才 true。 */
export async function removeProposal(prop) {
    if (!confirm(`確定刪除提案「${prop.title}」？參考片掛載會解除（片庫保留）。`)) return false;
    await tfetch(`${API}/${prop.id}`, { method: 'DELETE' });
    return true;
}

/**
 * 上傳/更換提案簡報。前端先擋 50MB（後端同樣把關，回 413）——
 * 走隧道時真正的天花板是 CDN 的 100MB/請求，簡報遠小於它，不必分塊。
 * @returns 有沒有真的上傳（沒選檔 / 超過上限 → false）
 */
export async function uploadDeck(prop, file) {
    if (!file) return false;
    if (file.size > DECK_MAX_BYTES) { alert('簡報檔超過 50MB 上限'); return false; }
    const fd = new FormData();
    fd.append('file', file);
    await tfetch(`${API}/${prop.id}/deck`, { method: 'POST', body: fd });
    return true;
}

// ── 參考片：共用片庫 ↔ 這個提案的掛載 ──────────────────────
// 片庫本體是跨提案共用的；掛載/解除只動這個提案的關聯，不動片庫。

// 片庫本體只有 addRefByUrl 會變（掛載/解除只動關聯）→ memo 一份。
// 沒有它的話，每次「加一支/移除一支」的局部重畫都要把整個片庫重抓一次。
let _libCache = null;

/** 整個共用片庫（挑選器用）。拿不到就回空陣列 —— 不該擋住詳情，也不進快取。 */
export async function libraryRefs() {
    if (_libCache) return _libCache;
    try {
        _libCache = (await tfetch(`${API}/references`)).references || [];
        return _libCache;
    } catch {
        return [];
    }
}

export const linkRef = (pid, rid) =>
    tfetch(`${API}/${pid}/refs`, { method: 'POST', json: { reference_id: rid } });

export const unlinkRef = (pid, rid) =>
    tfetch(`${API}/${pid}/refs/${encodeURIComponent(rid)}`, { method: 'DELETE' });

/** 貼一條新網址：先進共用片庫，再掛到這個提案（UI 上是一個動作）。 */
export async function addRefByUrl(pid, url, title) {
    const d = await tfetch(`${API}/references`, { method: 'POST', json: { url, title } });
    _libCache = null;                    // 片庫多了一支
    await linkRef(pid, d.reference.id);
    return d.reference;
}
