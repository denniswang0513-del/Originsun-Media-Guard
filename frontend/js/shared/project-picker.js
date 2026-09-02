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
    const clientOf = (p) => p.client_short_name || p.client || '';
    // 🔴 看不到金額的人：`amount_receivable` 整個鍵被 MoneyRedactRoute 抹掉
    //（不是 0），回 null 讓下面畫「—」而不是「未收 $0」——「你沒授權」被畫成
    // 「已收齊」是謊報。判準用 `'key' in obj`（core/money 檔頭那條）。
    const dueOf = (p) => ('amount_receivable' in p
        ? (Number(p.amount_receivable) || 0) : null);
    // 主清單＝還在等錢的，從 `projects` 推導。**「未收怎麼算」只有 dueOf 一份**：
    // 呼叫端各自先 filter 一次的話，那條判準（含上面的三態）就有兩份。
    const due = all.filter((p) => dueOf(p) === null || dueOf(p) > 0);

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

/** 一張請款單「搜得到什麼」—— 收款人／摘要／類別／專案標籤四欄。
 *  🔴 兩個入口（列表格子的挑選視窗、詳情面板的分配面板）打同一批
 *  `_paymentList`，欄位集合各寫一份的話，同一串字在 A 找得到、B 找不到，
 *  而畫面上沒有任何跡象說明為什麼。 */
export const paymentHay = (p) => (`${p.payee_name || ''} ${p.summary || ''} `
    + `${p.category || ''} ${p.project_label || ''}`).toLowerCase();

/** 多選視窗底部：已選幾張、合計對本列金額差多少 —— 收付兩側同一句話。 */
const sumFooter = (noun, sumLabel, amtOf, rowAmount) => (ids) => {
    if (!ids.length) { return `未選任何${noun}（儲存＝取消連結）`; }
    const sum = ids.reduce((n, id) => n + amtOf(id), 0);
    const row = Number(rowAmount) || 0;
    const diff = sum - row;
    return `已選 ${ids.length} 張　${sumLabel} <b style="color:#eee;">$${money(sum)}</b>`
        + (row ? `　／　本列 $${money(row)}`
            + (diff ? `<span style="color:#fbbf24;">　差 ${diff > 0 ? '+' : ''}${money(diff)}</span>`
                : '<span style="color:#86efac;">　剛好</span>') : '');
};

/** id → 金額。建表不用 find：合計每次重畫都跑一遍，清單有 800+ 張未付單，
 *  勾 5 張就是每次重畫 4,000 次比較。掛在這一列上的那幾張也一起收進來。 */
const amtIndex = (rows, linked, amtOf) => {
    const m = new Map([...rows, ...(linked || [])].map((r) => [r.id, Number(amtOf(r)) || 0]));
    return (id) => m.get(id) || 0;
};

export function openPaymentPicker(o) {
    const list = o.payments || [];
    const amtOf = amtIndex(list, o.linkedRows, (p) => p.amount);
    openRowPicker({
        rows: list,
        // 已付掉的單不在「還沒付完」的清單裡，但它可能正掛在這一列上 ——
        // 撈不回來的話，重開視窗就少一張、按儲存就把它洗掉
        extraRows: o.linkedRows || [],
        multi: true,
        currentIds: o.currentIds || [],
        // 一筆匯出付多張時，「湊不湊得起來」是當下唯一要看的事 ——
        // 張皓雲那筆 17,200 + 3,000 剛好等於匯出的 20,200。
        footer: sumFooter('請款單', '合計', amtOf, o.rowAmount),
        title: o.title || '連結請款單',
        placeholder: '搜尋收款人／摘要／類別…',
        emptyMain: '沒有還沒付完的請款單',
        hay: paymentHay,
        line: (p) => `<span style="flex:1;min-width:0;">
                <span style="color:#eee;font-size:13px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(paymentLabel(p) || '（無收款人）')}</span>
                <span style="color:#9ca3af;font-size:11px;">${esc(p.payee_name ? (p.summary || '').slice(0, 30) : '')}${p.category ? '　·　' + esc(p.category) : ''}</span>
            </span>
            <span style="color:#fbbf24;font-size:11px;white-space:nowrap;">$${money(p.amount)}</span>`,
        onPick: o.onPick,
    });
}


/** 一張發票「搜得到什麼」—— 號碼／抬頭／公司／專案。
 *  同 `paymentHay` 的理由：挑選視窗與分配面板打同一批 `_invoiceList`，
 *  欄位集合各寫一份就會出現「A 找得到 B 找不到」。 */
export const invoiceHay = (i) => (`${i.invoice_number || ''} ${i.title || ''} `
    + `${i.company_name || ''} ${i.project_name || ''}`).toLowerCase();

/**
 * openInvoicePicker({invoices, currentIds, linkedRows, rowAmount, title, onPick})
 *
 * 收款可以一次對到好幾張發票（合併匯款：客戶一次匯 3 張的錢）——
 * `onPick` 收到的是 id 陣列（空陣列＝取消全部連結）。
 *
 * 🔴 這個視窗只決定「掛哪幾張」。**逐張的分配金額與匯費不在這裡調** ——
 * 那是詳情面板那個分配面板的事（發票側是 per-item fee，收款分期時金額也不等於
 * 面額）。呼叫端負責：已經掛著的那幾張，金額與匯費原封保留。
 */
export function openInvoicePicker(o) {
    const list = o.invoices || [];
    const amtOf = amtIndex(list, o.linkedRows,
                           (i) => (i.outstanding != null ? i.outstanding : i.amount_total));
    openRowPicker({
        rows: list,
        extraRows: o.linkedRows || [],
        multi: true,
        currentIds: o.currentIds || [],
        footer: sumFooter('發票', '尚欠合計', amtOf, o.rowAmount),
        title: o.title || '連結發票',
        placeholder: '搜尋發票號碼／抬頭／公司…',
        emptyMain: '沒有還沒收齊的發票',
        hay: invoiceHay,
        line: (i) => `<span style="flex:1;min-width:0;">
                <span style="color:#eee;font-size:13px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(i.invoice_number || '無號碼')}　${esc((i.title || '').slice(0, 28))}</span>
                ${i.company_name ? `<span style="color:#9ca3af;font-size:11px;">${esc(i.company_name)}</span>` : ''}
            </span>
            <span style="color:#fbbf24;font-size:11px;white-space:nowrap;">$${money(amtBy.get(i.id) || 0)}</span>`,
        onPick: o.onPick,
    });
}
