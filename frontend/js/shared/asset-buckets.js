/**
 * frontend/js/shared/asset-buckets.js — 資產儀表板「哪些手填桶帶入這次估計」的唯一規則。
 * 零 import 葉節點；桌機 tabs/finance/subviews/assets.js 與手機 m/views/ledger-assets.js 共用
 * （2026-09-13 從 assets.js 抽出：士源帳本要算同一個「現在估計」，不能再抄一份名單）。
 *
 * 拍快照時，上次快照裡這些桶名**不帶入**手填欄 —— 它們已被系統自動欄取代
 * （Sheet 時代的桶名 → 系統桶）：生活帳戶+公司資產(現金)→銀行現金、
 * 公司資產(應收帳款)→應收帳款、財富自由(總額)→證券現值。帶入會重複計。
 * 2026-08-26 帳戶/資產補齊後再收兩顆：備用金（=保險逐列+美國匯豐，已進持股）、
 * 其他資產（=外幣現金 11 幣+外幣活存，已進持股）。
 * ⚠ 預付帳款**留手填**：它含家用 629,897＋個人_ 各科往來 —— 家用代墊已上
 * BS（自動），但儀表板沒有對應自動桶；真把它 supersede 會少掉其他科的錢。
 */
export const SUPERSEDED = new Set(['生活帳戶', '公司資產(現金)', '公司資產(應收帳款)',
                                   '財富自由(總額)', '備用金(Past)', '備用金', '其他資產']);

/** 上次快照的手填桶（排除被系統自動桶取代者）——「哪些桶帶入下一次」只有這一份規則 */
export function manualBuckets(auto, last) {
    const manual = {};
    if (last) {
        for (const [k, v] of Object.entries(last.buckets || {})) {
            if (!SUPERSEDED.has(k) && !(k in auto)) manual[k] = v;
        }
    }
    return manual;
}

/** 「現在估計」＝系統自動桶 ＋ 上次快照的手填桶（未被取代者）。 */
export function estimatedTotal(auto, last) {
    const manual = manualBuckets(auto, last);
    return Object.values(auto || {}).reduce((a, b) => a + Number(b || 0), 0)
        + Object.values(manual).reduce((a, b) => a + Number(b || 0), 0);
}
