/**
 * project-picker.js — 專案／請款單的挑選視窗（薄殼，殼在 row-picker）
 *
 * owner 2026-09-01「分類是專案時，專案列表要可以勾選、搜尋」—— 私帳 405 個
 * 專案塞在原生 <select> 裡等於沒得選；同日補充「這個清單要是款項沒收齊的
 * 清單」與「支出的請款單勾記我希望也可以在這裡直接勾，比照專案」。
 *
 * 兩個挑選器共用同一個殼（js/shared/row-picker）—— 這裡只提供「一列長什麼樣」
 * 與文案。單選：一列收支掛一個專案／一張主要請款單（要複選並分配金額走詳情
 * 面板的分配面板，那才是分配表的正本路徑）。
 */
import { esc } from './dom.js';
import { openRowPicker } from './row-picker.js';
// 千分位走既有那份（modal-styles 同款先例：shared → crm-utils）
import { fmtNum as money } from '../../tabs/crm/crm-utils.js';

/**
 * openProjectPicker({projects, outstanding, currentId, title, onPick})
 * 預設只列**還沒收齊**的案：記一筆進帳要連的一定是還在等錢的案；411 案裡
 * 217 案早就結清，全列出來等於把答案埋在雜訊裡。已結清的用「顯示全部」切換
 * （補記舊帳還是得選得到）。
 */
export function openProjectPicker(o) {
    const all = o.projects || [];
    // `outstanding` 是 `projects` 的子集（同一種物件、多一個 receivable）——
    // 兩份索引再 spread 合併的舞步是上一版的殘留，那時它來自另一支端點。
    const due = o.outstanding || [];
    const clientOf = (p) => p.client_short_name || p.client || '';
    // 🔴 看不到金額的人：`amount_receivable` 整個鍵被 MoneyRedactRoute 抹掉，
    // 回 null 讓下面畫「—」而不是「未收 $0」（把沒授權說成已收齊）。
    const dueOf = (p) => ('amount_receivable' in p
        ? (Number(p.amount_receivable) || 0) : null);

    openRowPicker({
        rows: due,
        allRows: all,
        currentId: o.currentId || '',
        title: o.title || '選專案',
        placeholder: '搜尋專案名稱／客戶…',
        emptyMain: '沒有還在等錢的案 —— 要連結已收齊的案請按「顯示全部」',
        emptyAll: '找不到符合的專案',
        scopeMain: '還沒收齊的', scopeAll: '全部',
        hay: (p) => `${p.name || ''} ${clientOf(p)} ${p.status || ''}`,
        line: (p) => `<span style="flex:1;min-width:0;">
                <span style="color:#eee;font-size:13px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(p.name || p.id)}</span>
                ${clientOf(p) || p.status ? `<span style="color:#9ca3af;font-size:11px;">${esc(clientOf(p))}${p.status ? '　·　' + esc(p.status) : ''}</span>` : ''}
            </span>
            ${dueOf(p) === null
                ? '<span style="color:#4b5563;font-size:11px;white-space:nowrap;">—</span>'
                : dueOf(p)
                ? `<span style="color:#fbbf24;font-size:11px;white-space:nowrap;">未收 $${money(dueOf(p))}</span>`
                : '<span style="color:#4b5563;font-size:11px;white-space:nowrap;">已收齊</span>'}`,
        onPick: o.onPick,
    });
}

/**
 * openPaymentPicker({payments, currentIds, linkedRows, rowAmount, title, onPick})
 * `payments`＝還沒付完的請款單（清單端點已經用 payment_status 篩過）。
 * 已付掉的那幾張若正掛在這一列上，殼會自己把它們留在清單裡。
 *
 * **多選**（owner 2026-09-01「這個要可以多選」）：一筆匯出常常是一個人的好幾張
 * 單併著發 —— 2026/08/31 匯給張皓雲的 20,200 ＝ 17,200 ＋ 3,000。`onPick` 收到
 * 的是 id 陣列（空陣列＝取消全部連結）。
 */
/** 一張請款單顯示成什麼 —— 後端 `routers/crm/cash.payment_label()` 的鏡射：
 *  收款人優先、沒有才退回摘要。🔴 兩個 JS 呼叫點（列表 patch、這個視窗）
 *  各寫一份的話會像 2026-09-01 那樣漂：視窗寫「（無收款人）」、選完格子裡
 *  卻顯示摘要，同一張單兩個名字。 */
export const paymentLabel = (p) => (p ? (p.payee_name || p.summary || '') : '');

export function openPaymentPicker(o) {
    const list = o.payments || [];
    const amtOf = (id) => (list.find((p) => p.id === id) || {}).amount || 0;
    openRowPicker({
        rows: list,
        allRows: [],
        // 已付掉的單不在「還沒付完」的清單裡，但它可能正掛在這一列上 ——
        // 撈不回來的話，重開視窗就少一張、按儲存就把它洗掉
        extraRows: o.linkedRows || [],
        multi: true,
        currentIds: o.currentIds || [],
        // 一筆匯出付多張時，「湊不湊得起來」是當下唯一要看的事 ——
        // 張皓雲那筆 17,200 + 3,000 剛好等於匯出的 20,200。
        footer: (ids) => {
            if (!ids.length) { return '未選任何請款單（儲存＝取消連結）'; }
            const sum = ids.reduce((n, id) => n + amtOf(id), 0);
            const row = Number(o.rowAmount) || 0;
            const diff = sum - row;
            return `已選 ${ids.length} 張　合計 <b style="color:#eee;">$${money(sum)}</b>`
                + (row ? `　／　本列 $${money(row)}`
                    + (diff ? `<span style="color:#fbbf24;">　差 ${diff > 0 ? '+' : ''}${money(diff)}</span>`
                        : '<span style="color:#86efac;">　剛好</span>') : '');
        },
        title: o.title || '連結請款單',
        placeholder: '搜尋收款人／摘要／類別…',
        emptyMain: '沒有還沒付完的請款單',
        hay: (p) => `${p.payee_name || ''} ${p.summary || ''} ${p.category || ''} ${p.project_label || ''}`,
        line: (p) => `<span style="flex:1;min-width:0;">
                <span style="color:#eee;font-size:13px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(paymentLabel(p) || '（無收款人）')}</span>
                <span style="color:#9ca3af;font-size:11px;">${esc(p.payee_name ? (p.summary || '').slice(0, 30) : '')}${p.category ? '　·　' + esc(p.category) : ''}</span>
            </span>
            <span style="color:#fbbf24;font-size:11px;white-space:nowrap;">$${money(p.amount)}</span>`,
        onPick: o.onPick,
    });
}
