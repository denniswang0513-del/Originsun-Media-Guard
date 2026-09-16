/**
 * fmt.js — 桌機與手機共用的數字格式（2026-09-17 /health 調整：原本堡壘桌機、月報桌機、士源帳本各有一份「萬」）。
 *
 * fmtWan：元 → 「12.3 萬」／「1.5 億」。一位小數（.0 去掉）；一億以上兩位小數（尾零去掉）；負數前面是「−」（U+2212，跟後端
 * core.monthly_report._wan 同一個字）；null／undefined／非數字 → '—'。
 * 🔴 這裡改了顯示法，堡壘、月報、總覽頂卡三處一起變；後端建議文字裡的數字（_wan）也是同一個口徑，別讓它們分家。
 */
export function fmtWan(n) {
    if (n === null || n === undefined || !Number.isFinite(Number(n))) return '—';
    const v = Number(n);
    const a = Math.abs(v);
    // 一億以上用「億」：財富階梯的門檻寫成「10,000 萬」沒人看得懂
    const s = a >= 1e8
        ? `${(a / 1e8).toFixed(2).replace(/\.?0+$/, '')} 億`
        : `${(a / 10000).toFixed(1).replace(/\.0$/, '')} 萬`;
    // 四捨五入到 0 的負數不要寫成「−0 萬」
    return (v < 0 && !/^0(\.0+)? /.test(s) ? '−' : '') + s;
}
