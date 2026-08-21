// hr_benefits.js — 人事管理 › 福利池（docs/BENEFIT_POOL_PLAN.md）
// 三塊：池（含用量條）/ 該池的動支明細 / 會計交付包。UI 無 emoji（owner 鐵則）。
// API: /api/v1/crm/benefits/*
//
// 這一頁**不自己算餘額**。budget / used / pending / balance / over 全部由後端
// 給（core.hr_logic.benefit_pool_balance，有單元測試）。前端自己算一份的話，
// 「待審算不算」這種判定就會有兩個答案，而畫面上那個一定是錯的那個。

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const el = (id) => document.getElementById(id);
const fmt = (n) => '$' + Number(n || 0).toLocaleString('en-US');

const STATUS_PILL = {
    '草稿': 'draft', '待審': 'pending', '已核准': 'approved',
    '已付款': 'paid', '退回': 'rejected',
};

let _pools = [];
let _opts = { categories: [], kinds: [], staff: [] };
let _sel = null;        // 目前選中的池 id
let _grants = [];
let _year = new Date().getFullYear();

function bfetch(path, opts = {}) {
    const headers = Object.assign({ 'Content-Type': 'application/json' },
                                  opts.headers || {});
    const tok = localStorage.getItem('auth_token');
    if (tok) headers['Authorization'] = 'Bearer ' + tok;
    return fetch(path, Object.assign({}, opts, {
        headers,
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    }));
}

// 🔴 失敗一定要出聲。靜默吞掉錯誤是這個 repo 咬過的坑（按鈕點了沒反應那次）：
// 使用者按下去什麼都沒發生，會以為是自己操作錯。
async function call(path, opts, okMsg) {
    const r = await bfetch(path, opts);
    if (!r.ok) {
        let detail = r.status;
        try { detail = (await r.json()).detail || detail; } catch (_) { /* 非 JSON */ }
        alert('操作失敗：' + detail);
        return null;
    }
    if (okMsg) console.info(okMsg);
    return r.json().catch(() => ({}));
}

async function _load() {
    const [pr, or_] = await Promise.all([
        bfetch('/api/v1/crm/benefits/pools?year=' + _year),
        bfetch('/api/v1/crm/benefits/options'),
    ]);
    if (!pr.ok) {
        el('hb-content').innerHTML =
            `<div class="hb-empty">載入失敗（${pr.status}）— 需要「福利池」與「金額檢視」權限</div>`;
        return;
    }
    const data = await pr.json();
    _pools = data.items || [];
    if (or_.ok) _opts = await or_.json();
    // 選中的池不在這個年度就落到第一個（不是清空 —— 清空會讓下半頁整個消失）
    if (!_pools.some(p => p.id === _sel)) _sel = _pools[0] ? _pools[0].id : null;
    await _loadGrants();
}

async function _loadGrants() {
    if (!_sel) { _grants = []; render(); return; }
    const r = await bfetch('/api/v1/crm/benefits/pools/' + _sel);
    _grants = r.ok ? (await r.json()).grants || [] : [];
    render();
}

/* ── 池 ── */

function poolsHtml() {
    if (!_pools.length) {
        return '<div class="hb-empty">這個年度還沒有福利池 —— 先在下面建一個</div>';
    }
    return '<div class="hb-pools">' + _pools.map(p => {
        const pct = p.budget > 0
            ? Math.min(100, Math.round((p.used / p.budget) * 100)) : 0;
        return `
        <div class="hb-pool${p.id === _sel ? ' sel' : ''}"
             onclick="window._hbSelectPool('${esc(p.id)}')">
            <div class="n">${esc(p.name)}${p.status === 'closed' ? '（已關閉）' : ''}</div>
            <div class="m">編列 ${fmt(p.budget)}　已用 ${fmt(p.used)}</div>
            <div class="m">餘額 <b class="${p.over ? 'over' : ''}">${fmt(p.balance)}</b>${
                p.pending ? `　審核中 ${fmt(p.pending)}` : ''}</div>
            <div class="hb-bar"><i class="${p.over ? 'over' : ''}"
                 style="width:${p.over ? 100 : pct}%"></i></div>
        </div>`;
    }).join('') + '</div>';
}

function poolFormHtml() {
    return `
    <div class="hb-form" style="margin-top:12px;">
        <input id="hb-p-name" placeholder="池名稱（例：2026 年度員工福利）" style="width:260px;">
        <input id="hb-p-budget" type="number" placeholder="編列金額" style="width:130px;">
        <button class="hb-btn" onclick="window._hbCreatePool()">新增福利池</button>
        ${_sel ? `<button class="hb-btn danger" onclick="window._hbDeletePool()">刪除選中的池</button>` : ''}
    </div>
    <div class="hb-note">餘額 ＝ 編列 − 已核准 − 已付款。<b>待審不扣餘額</b>（退件後就不用回沖），
        另外顯示「審核中」。超編時餘額會是負的並轉紅 —— 那是刻意的，藏起來只會更晚發現。</div>`;
}

/* ── 動支 ── */

function grantsHtml() {
    const pool = _pools.find(p => p.id === _sel);
    if (!pool) return '';
    const rows = _grants.length ? _grants.map(g => {
        const pill = STATUS_PILL[g.status] || 'draft';
        const acts = [];
        if (g.status === '草稿' || g.status === '退回') {
            acts.push(`<button class="hb-btn" onclick="window._hbSubmit('${g.id}')">送審</button>`);
            acts.push(`<button class="hb-btn danger" onclick="window._hbDelete('${g.id}')">刪除</button>`);
        }
        if (g.status === '待審') {
            acts.push(`<button class="hb-btn ok" onclick="window._hbApprove('${g.id}')">核准</button>`);
            acts.push(`<button class="hb-btn ghost" onclick="window._hbReject('${g.id}')">退回</button>`);
        }
        if (g.status === '已核准') {
            acts.push(`<button class="hb-btn ok" onclick="window._hbPay('${g.id}')">登記匯款</button>`);
            acts.push(`<button class="hb-btn ghost" onclick="window._hbReject('${g.id}')">退回</button>`);
        }
        return `<tr>
            <td>${esc(g.grant_date)}</td>
            <td>${esc(g.staff_name)}</td>
            <td>${esc(g.category)}</td>
            <td>${esc(g.kind)}</td>
            <td class="num">${fmt(g.amount)}</td>
            <td>${g.taxable ? '<span class="hb-pill hb-tax">併入所得</span>' : ''}</td>
            <td><span class="hb-pill ${pill}">${esc(g.status)}</span></td>
            <td>${acts.join(' ')}</td>
        </tr>`;
    }).join('') : '<tr><td colspan="8" class="hb-empty">這個池還沒有動支</td></tr>';

    const staffOpts = _opts.staff.map(s =>
        `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
    const catOpts = _opts.categories.map(c =>
        `<option value="${esc(c)}">${esc(c)}</option>`).join('');
    const kindOpts = _opts.kinds.map(k =>
        `<option value="${esc(k)}">${esc(k)}</option>`).join('');

    return `
    <div class="hb-card">
        <h3>${esc(pool.name)} — 動支明細</h3>
        <table>
            <thead><tr>
                <th>日期</th><th>員工</th><th>項目</th><th>方式</th>
                <th class="num">金額</th><th>稅務</th><th>狀態</th><th>操作</th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table>
        ${pool.status === 'closed' ? '' : `
        <div class="hb-form" style="margin-top:12px;">
            <select id="hb-g-staff"><option value="">選員工</option>${staffOpts}</select>
            <select id="hb-g-cat">${catOpts}</select>
            <select id="hb-g-kind">${kindOpts}</select>
            <input id="hb-g-amount" type="number" placeholder="金額">
            <input id="hb-g-date" type="date">
            <label style="color:#c4b5fd;font-size:12px;display:flex;align-items:center;gap:5px;">
                <input id="hb-g-taxable" type="checkbox" style="width:auto;">併入個人所得
            </label>
            <button class="hb-btn" onclick="window._hbCreateGrant()">新增動支</button>
        </div>
        <div class="hb-note"><b>方式</b>：給付＝公司直接發（生日禮金／三節，沒有收據）；
            核銷＝員工先墊、憑收據。<br>
            <b>併入個人所得</b>：勾了的年底要開扣繳憑單 —— 這一欄就是會計交付包分區的依據，
            不確定的先問會計再勾。<br>
            核准後會自動產一張應付款進<b>應付帳款</b>，登記匯款時再結清成一列收支明細。</div>`}
    </div>`;
}

/* ── 會計交付 ── */

function packageHtml() {
    return `
    <div class="hb-card">
        <h3>會計交付包</h3>
        <div class="hb-form">
            <input id="hb-k-month" type="month" placeholder="月份（可留白＝整年）">
            <button class="hb-btn" onclick="window._hbPreview()">預覽</button>
            <button class="hb-btn ghost" onclick="window._hbDownload()">下載 CSV</button>
        </div>
        <div id="hb-k-out" class="hb-note">只收「已核准／已付款」——
            草稿與待審還不是帳，送過去只會讓會計對不起來。</div>
    </div>`;
}

function render() {
    const yearOpts = [_year + 1, _year, _year - 1, _year - 2].map(y =>
        `<option value="${y}"${y === _year ? ' selected' : ''}>${y} 年</option>`).join('');
    el('hb-content').innerHTML = `
        <h2>福利池</h2>
        <div class="hb-sub">員工福利的編列與動支。核准後走既有的應付帳款／收支明細管線，
            送會計的那一份在最下面。</div>
        <div class="hb-card">
            <h3>年度
                <select id="hb-year" onchange="window._hbYear(this.value)"
                        style="margin-left:8px;">${yearOpts}</select>
            </h3>
            ${poolsHtml()}
            ${poolFormHtml()}
        </div>
        ${grantsHtml()}
        ${packageHtml()}`;
}

/* ── 動作 ── */

window._hbYear = async (v) => { _year = parseInt(v, 10); await _load(); };

window._hbSelectPool = async (id) => { _sel = id; await _loadGrants(); };

window._hbCreatePool = async () => {
    const name = (el('hb-p-name').value || '').trim();
    if (!name) { alert('請填池名稱'); return; }
    const r = await call('/api/v1/crm/benefits/pools', {
        method: 'POST',
        body: { name, budget: parseInt(el('hb-p-budget').value || '0', 10), year: _year },
    });
    if (r) { _sel = r.pool.id; await _load(); }
};

window._hbDeletePool = async () => {
    if (!_sel) return;
    if (!confirm('確定刪除這個福利池？（底下有動支就刪不掉）')) return;
    const r = await call('/api/v1/crm/benefits/pools/' + _sel, { method: 'DELETE' });
    if (r) { _sel = null; await _load(); }
};

window._hbCreateGrant = async () => {
    const amount = parseInt(el('hb-g-amount').value || '0', 10);
    if (!amount) { alert('請填金額'); return; }
    const r = await call('/api/v1/crm/benefits/pools/' + _sel + '/grants', {
        method: 'POST',
        body: {
            staff_id: el('hb-g-staff').value || '',
            category: el('hb-g-cat').value,
            kind: el('hb-g-kind').value,
            amount,
            grant_date: el('hb-g-date').value || '',
            taxable: el('hb-g-taxable').checked ? 1 : 0,
        },
    });
    if (r) await _load();
};

const _act = (verb, path, confirmMsg) => async (id) => {
    if (confirmMsg && !confirm(confirmMsg)) return;
    const r = await call(`/api/v1/crm/benefits/grants/${id}/${path}`, { method: 'POST' });
    if (r) await _load();          // 重載池：餘額會跟著這個動作變
};

window._hbSubmit = _act('送審', 'submit');
window._hbApprove = _act('核准', 'approve',
    '確定核准？系統會自動產一張應付款進應付帳款。');
window._hbPay = _act('匯款', 'pay',
    '確定登記匯款？會結清成一列收支明細（重按不會重複記帳）。');

window._hbReject = async (id) => {
    const reason = prompt('退回原因（可留白）：');
    if (reason === null) return;      // 按取消
    const r = await call(
        `/api/v1/crm/benefits/grants/${id}/reject?reason=${encodeURIComponent(reason)}`,
        { method: 'POST' });
    if (r) await _load();
};

window._hbDelete = async (id) => {
    if (!confirm('確定刪除這筆動支？')) return;
    const r = await call('/api/v1/crm/benefits/grants/' + id, { method: 'DELETE' });
    if (r) await _load();
};

function _pkgQuery() {
    const m = el('hb-k-month').value || '';
    return 'year=' + _year + (m ? '&month=' + m : '');
}

window._hbPreview = async () => {
    const r = await call('/api/v1/crm/benefits/accounting-package?' + _pkgQuery(),
                         { method: 'GET' });
    if (!r) return;
    const sec = (title, d, key, label) => `
        <div style="margin-top:10px;">
            <div style="color:#ddd;font-weight:600;">${title}　合計 ${fmt(d.total)}</div>
            ${d.summary.length ? `<table style="margin-top:6px;">
                <thead><tr><th>${label}</th><th class="num">筆數</th><th class="num">金額</th></tr></thead>
                <tbody>${d.summary.map(s => `<tr>
                    <td>${esc(s[key])}</td><td class="num">${s.count}</td>
                    <td class="num">${fmt(s.total)}</td></tr>`).join('')}</tbody>
            </table>` : '<div class="hb-empty">這個區間沒有</div>'}
        </div>`;
    el('hb-k-out').innerHTML =
        sec('併入個人所得（年底開扣繳憑單）', r.personal_income, 'staff_name', '員工')
        + sec('公司費用', r.company_expense, 'category', '項目');
};

window._hbDownload = () => {
    // 走 fetch 而不是 <a href> —— 這支端點要 Authorization 標頭，
    // 直接開連結會 401（而且瀏覽器只會給一個空白頁，看不出原因）
    bfetch('/api/v1/crm/benefits/accounting-package.csv?' + _pkgQuery())
        .then(async (r) => {
            if (!r.ok) { alert('下載失敗：' + r.status); return; }
            const blob = await r.blob();
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'benefits_' + _year + '.csv';
            a.click();
            URL.revokeObjectURL(a.href);
        });
};

export async function initHrBenefitsTab() {
    await _load();
}
