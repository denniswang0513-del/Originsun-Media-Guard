/**
 * household.js — 🏠 家用（owner 2026-08-26「開一個家用記帳頁面（都我在記）」）。
 *
 * 語意：家用支出＝owner 代墊（資產「家用代墊」＋）、家人還款＝沖銷（−）——
 * 不是業主提取。餘額口徑＝BS 的「家用代墊」線（後端 /household 與報表引擎
 * 同一條算法），對齊 owner Sheet「公司-富邦帳戶預付款/家用」那條公式。
 *
 * 記一筆＝寫一般收支列（POST /crm/cash-entries，entity='mine'、家用類別）——
 * 這頁只是家用視角的快速入口，資料仍在收支明細裡（那邊照樣看得到、改得到）。
 * 同 projects/gear：固定打私帳，入口由 .fin-nav-mine-only 指名門把關。
 */
import { finFetchMine, finSubviewBoot, esc, fmtNum, finToast, todayStr } from '../fin-utils.js';
import { crmFetch } from '../../crm/crm-utils.js';

let _c = null;
let _isCurrent = () => true;
let _d = null;

export default async function render(container, ctx = {}) {
    _c = container;
    if (ctx.isCurrent) _isCurrent = ctx.isCurrent;
    const r = await finSubviewBoot(_c, {
        title: '🏠 家用', isCurrent: _isCurrent,
        fetchers: [() => finFetchMine('/household')],
        retry: 'window._finHouse.reload()',
    });
    if (!r) return;
    _d = r[0];
    _render();
}

function _render() {
    const d = _d;
    const ym = todayStr().slice(0, 7);
    const cur = (d.monthly || []).find((m) => m.month === ym) || { advanced: 0, repaid: 0 };
    const spendCats = (d.categories || []).filter((c) => c.includes('支出'));
    const card = (label, val, color, sub = '') => `
        <div style="background:#222;border:1px solid #333;border-radius:8px;padding:12px 16px;flex:1 1 160px;min-width:150px;">
            <div style="color:#888;font-size:11px;">${label}</div>
            <div style="font-size:20px;font-weight:700;color:${color};margin-top:4px;white-space:nowrap;">$${fmtNum(val)}</div>
            ${sub ? `<div style="color:#666;font-size:11px;margin-top:3px;">${sub}</div>` : ''}
        </div>`;

    _c.innerHTML = `
        <h2 style="color:#eee;margin:0 0 12px;font-size:18px;">🏠 家用</h2>
        <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px;">
            ${card('代墊餘額（家用欠你）', d.balance, '#fbbf24', '＝資產負債表的「家用代墊」線')}
            ${card('本月代墊', cur.advanced, '#fca5a5')}
            ${card('本月收回', cur.repaid, '#86efac')}
        </div>
        <div style="background:#202020;border:1px solid #3b82f6;border-radius:8px;padding:14px;margin-bottom:14px;">
            <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;">
                <label style="color:#888;font-size:11px;">日期<input class="crm-input" type="date" id="hh-date" value="${todayStr()}"></label>
                <label style="color:#888;font-size:11px;">方向<select class="crm-input" id="hh-dir">
                    <option value="out">代墊支出</option><option value="in">家人還款</option></select></label>
                <label style="color:#888;font-size:11px;" id="hh-cat-wrap">類別<select class="crm-input" id="hh-cat">
                    ${spendCats.map((c) => `<option${c === '家用_變動支出' ? ' selected' : ''}>${esc(c)}</option>`).join('')}
                </select></label>
                <label style="color:#888;font-size:11px;">金額<input class="crm-input" type="number" id="hh-amt" style="width:110px;"></label>
                <label style="color:#888;font-size:11px;">子項目<input class="crm-input" id="hh-sub" list="hh-subs" style="width:120px;"></label>
                <label style="color:#888;font-size:11px;">摘要<input class="crm-input" id="hh-sum" placeholder="（空＝自動帶）" style="width:170px;"></label>
                <label style="color:#888;font-size:11px;">帳戶<select class="crm-input" id="hh-acct">
                    <option value="">—（刷卡）</option>
                    ${(d.accounts || []).map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join('')}
                </select></label>
                <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._finHouse.add(this)">記一筆</button>
            </div>
            <datalist id="hh-subs">${(d.sub_items || []).map((s) => `<option value="${esc(s)}">`).join('')}</datalist>
            <div style="color:#666;font-size:10px;margin-top:6px;">
                代墊支出＝餘額增加；家人還款＝餘額減少（還款一律記進「家用」類）。
                帳戶留空＝這筆是刷卡（進信用卡帳）。資料寫進收支明細，那邊照樣看得到。</div>
        </div>
        <div style="display:grid;grid-template-columns:280px 1fr;gap:14px;align-items:start;">
            <div style="background:#202020;border:1px solid #2e2e2e;border-radius:8px;overflow:hidden;">
                <table class="crm-table" style="width:100%;font-size:12px;">
                    <thead><tr><th>月份</th><th style="text-align:right;">代墊</th><th style="text-align:right;">收回</th></tr></thead>
                    <tbody>${(d.monthly || []).slice(0, 12).map((m) => `
                        <tr><td style="color:#888;">${esc(m.month)}</td>
                            <td style="text-align:right;color:#fca5a5;">${m.advanced ? fmtNum(m.advanced) : ''}</td>
                            <td style="text-align:right;color:#86efac;">${m.repaid ? fmtNum(m.repaid) : ''}</td></tr>`).join('')
                        || '<tr><td colspan="3" style="color:#666;padding:12px;">（無）</td></tr>'}</tbody>
                </table>
            </div>
            <div style="background:#202020;border:1px solid #2e2e2e;border-radius:8px;max-height:calc(100vh - 420px);overflow-y:auto;">
                <table class="crm-table" style="width:100%;font-size:12px;">
                    <thead><tr style="position:sticky;top:0;background:#202020;z-index:1;">
                        <th>日期</th><th>摘要</th><th>類別</th><th>子項目</th>
                        <th style="text-align:right;">代墊</th><th style="text-align:right;">收回</th></tr></thead>
                    <tbody>${(d.recent || []).map((e) => `
                        <tr><td style="white-space:nowrap;color:#888;">${esc(e.date)}</td>
                            <td style="overflow:hidden;text-overflow:ellipsis;max-width:260px;white-space:nowrap;" title="${esc(e.summary)}">${esc(e.summary)}</td>
                            <td style="color:#9a9a9a;white-space:nowrap;">${esc(e.category)}</td>
                            <td style="color:#9a9a9a;">${esc(e.sub_item)}</td>
                            <td style="text-align:right;color:#fca5a5;">${e.expense ? fmtNum(e.expense) : ''}</td>
                            <td style="text-align:right;color:#86efac;">${e.deposit ? fmtNum(e.deposit) : ''}</td></tr>`).join('')
                        || '<tr><td colspan="6" style="color:#666;padding:12px;">（無）</td></tr>'}</tbody>
                </table>
                <div style="color:#666;font-size:11px;padding:8px 12px;">顯示最近 ${(d.recent || []).length} 筆／共 ${fmtNum(d.total_rows)} 筆 —— 全部在收支明細（類別選 家用＊）。</div>
            </div>
        </div>`;
    const dir = document.getElementById('hh-dir');
    dir.addEventListener('change', () => {
        // 還款一律記「家用」類（歷史還款都在那）—— 類別下拉只在支出時有意義
        document.getElementById('hh-cat-wrap').style.display = dir.value === 'in' ? 'none' : '';
    });
}

const _fh = (window._finHouse = window._finHouse || {});

_fh.reload = () => { if (_c) render(_c, { isCurrent: _isCurrent }); };

_fh.add = async (btn) => {
    const g = (id) => document.getElementById(id).value.trim();
    const amt = parseInt(g('hh-amt'), 10) || 0;
    if (amt <= 0) { finToast('金額必填', 'error'); return; }
    const isRepay = g('hh-dir') === 'in';
    if (isRepay && !g('hh-acct')) {
        finToast('還款請選入帳帳戶（錢匯進哪裡）', 'error');
        return;
    }
    const cat = isRepay ? '家用' : (g('hh-cat') || '家用_變動支出');
    const sub = g('hh-sub');
    const body = {
        entity: 'mine', entry_date: g('hh-date') || todayStr(),
        category: cat, sub_item: sub,
        summary: g('hh-sum') || (isRepay ? '家用還款' : `家用｜${sub || '支出'}`),
        bank_account_id: g('hh-acct') || null,
        // 帳戶留空＝刷卡列（進信用卡帳，不動銀行水位）—— 同卡單匯入的口徑
        status: g('hh-acct') ? '' : 'card',
    };
    body[isRepay ? 'deposit' : 'expense'] = amt;
    btn.disabled = true;
    try {
        await crmFetch('/cash-entries', { method: 'POST', body: JSON.stringify(body) });
        finToast(isRepay ? '已記收回' : '已記代墊');
        _fh.reload();
    } catch (e) {
        finToast('記帳失敗：' + e.message, 'error');
        btn.disabled = false;
    }
};
