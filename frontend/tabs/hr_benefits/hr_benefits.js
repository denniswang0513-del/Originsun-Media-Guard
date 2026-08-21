// hr_benefits.js — 人事管理 › 福委會（docs/BENEFIT_POOL_PLAN.md）
// owner：「幾個福利池（快樂／進修），員工登記 → 我審核通過 → 進公司請款；
//          每年撥一筆錢進池。」UI 無 emoji（owner 鐵則）。
// API: /api/v1/crm/benefits/*
//
// 這一頁**不自己算餘額**。funded / used / pending / balance / over 全部由後端給
// （core.hr_logic.benefit_pool_balance，有單元測試）。前端自己算一份的話，
// 「待審算不算」這種判定就會有兩個答案，而畫面上那個一定是錯的那個。

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const el = (id) => document.getElementById(id);
const fmt = (n) => '$' + Number(n || 0).toLocaleString('en-US');

const STATUS_PILL = {
    '待審': 'pending', '已核准': 'approved', '已付款': 'paid', '退回': 'rejected',
};

let _pools = [];
let _staff = [];
let _sel = null;        // 目前選中的池
let _detail = null;     // { pool, fundings, entries }
let _pending = [];      // 全部待審（跨池）

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
async function call(path, opts) {
    const r = await bfetch(path, opts);
    if (!r.ok) {
        let detail = r.status;
        try { detail = (await r.json()).detail || detail; } catch (_) { /* 非 JSON */ }
        alert('操作失敗：' + detail);
        return null;
    }
    return r.json().catch(() => ({}));
}

async function _load() {
    const [pr, sr, qr] = await Promise.all([
        bfetch('/api/v1/crm/benefits/pools'),
        bfetch('/api/v1/crm/staff?limit=200'),
        bfetch('/api/v1/crm/benefits/entries?status=' + encodeURIComponent('待審')),
    ]);
    if (!pr.ok) {
        el('hb-content').innerHTML =
            `<div class="hb-empty">載入失敗（${pr.status}）— 需要「福委會」與「金額檢視」權限</div>`;
        return;
    }
    _pools = (await pr.json()).items || [];
    if (sr.ok) {
        const sd = await sr.json();
        _staff = sd.items || sd.staff || [];
    }
    _pending = qr.ok ? (await qr.json()).items || [] : [];
    if (!_pools.some(p => p.id === _sel)) _sel = _pools[0] ? _pools[0].id : null;
    await _loadDetail();
}

async function _loadDetail() {
    if (!_sel) { _detail = null; render(); return; }
    const r = await bfetch('/api/v1/crm/benefits/pools/' + _sel);
    _detail = r.ok ? await r.json() : null;
    render();
}

/** 單據／心得（owner 2026-08-21：「有心得跟有單據讓我知道就好」、
 *  「需要有地方可以上傳單據寫心得」）。
 *  🔴 兩者都是**選填** —— 沒有不是錯誤，所以「補」是淡色的次要動作。
 *  管理端**不限狀態**都能補：匯進來的歷史紀錄（已付款）本來就沒有這兩樣。 */
function _proof(e) {
    const receipt = e.has_receipt
        ? `<a class="hb-proof ok" href="/api/v1/crm/receipt-file?path=${encodeURIComponent(e.receipt_url)}"
              target="_blank" rel="noopener" title="開啟單據">單據</a>
           <button class="hb-proof act" title="換一張"
                   onclick="window._hbPickReceipt('${esc(e.id)}')">換</button>`
        : `<button class="hb-proof act" title="上傳單據"
                   onclick="window._hbPickReceipt('${esc(e.id)}')">＋單據</button>`;
    const note = e.has_reflection
        ? `<button class="hb-proof ok" title="${esc(e.reflection)}"
                   onclick="window._hbEditNote('${esc(e.id)}')">心得</button>`
        : `<button class="hb-proof act" title="寫心得"
                   onclick="window._hbEditNote('${esc(e.id)}')">＋心得</button>`;
    return receipt + ' ' + note;
}

/** 所有登記的索引（待審佇列與選中池的明細合起來）—— 補心得時要拿舊值回填。 */
function _entryById(id) {
    return _pending.find(x => x.id === id)
        || ((_detail && _detail.entries) || []).find(x => x.id === id) || null;
}

/* ── 池 ── */

function poolsHtml() {
    if (!_pools.length) {
        return '<div class="hb-empty">還沒有福利池 —— 先在下面開一個（例：快樂、進修）</div>';
    }
    return '<div class="hb-pools">' + _pools.map(p => {
        const pct = p.funded > 0
            ? Math.min(100, Math.round((p.used / p.funded) * 100)) : 0;
        return `
        <div class="hb-pool${p.id === _sel ? ' sel' : ''}"
             onclick="window._hbSelectPool('${esc(p.id)}')">
            <div class="n">${esc(p.name)}${p.status === 'closed' ? '（已關閉）' : ''}</div>
            <div class="m">累計撥款 ${fmt(p.funded)}　已用 ${fmt(p.used)}</div>
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
        <input id="hb-p-name" placeholder="池名稱（例：快樂、進修）" style="width:220px;">
        <button class="hb-btn" onclick="window._hbCreatePool()">新增福利池</button>
        ${_sel ? `<button class="hb-btn danger" onclick="window._hbDeletePool()">刪除選中的池</button>` : ''}
    </div>
    <div class="hb-note">餘額 ＝ 累計撥款 − 已核准 − 已付款。<b>待審不扣餘額</b>（退件後就不用回沖），
        另外顯示「審核中」。花超了餘額會是負的並轉紅 —— 那是刻意的，藏起來只會更晚發現。</div>`;
}

/* ── 待審佇列（owner 每天真正要看的東西，放最上面）── */

function pendingHtml() {
    if (!_pending.length) return '';
    const rows = _pending.map(e => `<tr>
        <td>${esc(e.spend_date)}</td>
        <td>${esc(e.pool_name)}</td>
        <td>${esc(e.staff_name)}</td>
        <td>${esc(e.title)}</td>
        <td class="num">${fmt(e.amount)}</td>
        <td>${_proof(e)}</td>
        <td>
            <button class="hb-btn ok" onclick="window._hbApprove('${e.id}')">核准</button>
            <button class="hb-btn ghost" onclick="window._hbReject('${e.id}')">退回</button>
        </td></tr>`).join('');
    return `
    <div class="hb-card">
        <h3>待審 <span style="color:#fbbf24;">${_pending.length}</span> 筆
            <span style="color:#777;font-size:12px;font-weight:400;">
                合計 ${fmt(_pending.reduce((s, e) => s + e.amount, 0))}</span></h3>
        <table>
            <thead><tr><th>日期</th><th>池</th><th>員工</th><th>項目</th>
                <th class="num">金額</th><th>單據／心得</th><th>操作</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>
        <div class="hb-note">核准後會自動產一張應付款進<b>應付帳款</b>（＝進公司請款），
            公司匯款時再回到這裡按「登記匯款」結清成收支明細。</div>
    </div>`;
}

/* ── 選中池的明細 ── */

function detailHtml() {
    if (!_detail) return '';
    const p = _detail.pool;
    const fundRows = (_detail.fundings || []).length
        ? _detail.fundings.map(f => `<tr>
            <td>${f.year}</td><td>${esc(f.fund_date)}</td>
            <td class="num">${fmt(f.amount)}</td>
            <td>${esc(f.notes)}</td>
            <td><button class="hb-btn danger" onclick="window._hbDelFunding('${f.id}')">刪除</button></td>
          </tr>`).join('')
        : '<tr><td colspan="5" class="hb-empty">還沒有撥款紀錄</td></tr>';

    const entRows = (_detail.entries || []).length
        ? _detail.entries.map(e => {
            const acts = [];
            if (e.status === '待審') {
                acts.push(`<button class="hb-btn ok" onclick="window._hbApprove('${e.id}')">核准</button>`);
                acts.push(`<button class="hb-btn ghost" onclick="window._hbReject('${e.id}')">退回</button>`);
            }
            if (e.status === '已核准') {
                acts.push(`<button class="hb-btn ok" onclick="window._hbPay('${e.id}')">登記匯款</button>`);
                acts.push(`<button class="hb-btn ghost" onclick="window._hbReject('${e.id}')">退回</button>`);
            }
            return `<tr>
                <td>${esc(e.spend_date)}</td>
                <td>${esc(e.staff_name)}</td>
                <td>${esc(e.title)}</td>
                <td class="num">${fmt(e.amount)}</td>
                <td>${_proof(e)}</td>
                <td><span class="hb-pill ${STATUS_PILL[e.status] || ''}">${esc(e.status)}</span></td>
                <td>${acts.join(' ')}</td>
            </tr>`;
        }).join('')
        : '<tr><td colspan="7" class="hb-empty">這個池還沒有登記</td></tr>';

    const staffOpts = _staff.map(s =>
        `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');

    return `
    <div class="hb-card">
        <h3>${esc(p.name)} — 撥款</h3>
        <table>
            <thead><tr><th>年度</th><th>日期</th><th class="num">金額</th>
                <th>備註</th><th>操作</th></tr></thead>
            <tbody>${fundRows}</tbody>
        </table>
        <div class="hb-form" style="margin-top:12px;">
            <input id="hb-f-year" type="number" placeholder="年度" style="width:90px;">
            <input id="hb-f-amount" type="number" placeholder="撥款金額">
            <input id="hb-f-date" type="date">
            <button class="hb-btn" onclick="window._hbAddFunding()">撥款進池</button>
        </div>
    </div>

    <div class="hb-card">
        <h3>${esc(p.name)} — 登記明細</h3>
        <table>
            <thead><tr><th>日期</th><th>員工</th><th>項目</th>
                <th class="num">金額</th><th>單據／心得</th><th>狀態</th><th>操作</th></tr></thead>
            <tbody>${entRows}</tbody>
        </table>
        <div class="hb-form" style="margin-top:12px;">
            <select id="hb-e-staff"><option value="">選員工</option>${staffOpts}</select>
            <input id="hb-e-title" placeholder="項目（電影名／餐廳／課程名）" style="width:230px;">
            <input id="hb-e-amount" type="number" placeholder="金額">
            <input id="hb-e-date" type="date">
            <button class="hb-btn" onclick="window._hbAddEntry()">代員工登記</button>
        </div>
        <div class="hb-note">員工自己登記走 <b>/my.html</b> 的福委會卡片；這裡是代登用的。</div>
    </div>`;
}

function packageHtml() {
    const y = new Date().getFullYear();
    const opts = [y + 1, y, y - 1, y - 2, 0].map(v =>
        `<option value="${v}"${v === y ? ' selected' : ''}>${v ? v + ' 年' : '全部'}</option>`).join('');
    return `
    <div class="hb-card">
        <h3>送會計</h3>
        <div class="hb-form">
            <select id="hb-k-year">${opts}</select>
            <button class="hb-btn" onclick="window._hbPreview()">預覽</button>
            <button class="hb-btn ghost" onclick="window._hbDownload()">下載 CSV</button>
        </div>
        <div id="hb-k-out" class="hb-note">每個池的撥款／已用／餘額 ＋ 撥款逐筆 ＋ 支出逐筆。
            只收「已核准／已付款」—— 待審與退回還不是帳。</div>
    </div>`;
}

function render() {
    el('hb-content').innerHTML = `
        <h2>福委會</h2>
        <div class="hb-sub">員工登記 → 你審核通過 → 進公司請款。每年撥一筆錢進池。</div>
        ${pendingHtml()}
        <div class="hb-card">
            <h3>福利池</h3>
            ${poolsHtml()}
            ${poolFormHtml()}
        </div>
        ${detailHtml()}
        ${packageHtml()}`;
}

/* ── 動作 ── */

window._hbSelectPool = async (id) => { _sel = id; await _loadDetail(); };

/** 上傳單據：一個隱藏 input 重複用，記住是哪一筆。 */
let _upTarget = null;
window._hbPickReceipt = (id) => {
    _upTarget = id;
    let inp = el('hb-receipt-input');
    if (!inp) {
        inp = document.createElement('input');
        inp.type = 'file';
        inp.id = 'hb-receipt-input';
        inp.accept = 'image/*,.pdf';
        inp.style.display = 'none';
        inp.onchange = async () => {
            if (!inp.files[0] || !_upTarget) return;
            const fd = new FormData();
            fd.append('file', inp.files[0]);
            // 🔴 FormData 不能自己設 Content-Type（會蓋掉 multipart boundary），
            // 所以這裡不用 bfetch —— 它固定塞 application/json。
            const tok = localStorage.getItem('auth_token');
            const r = await fetch(
                `/api/v1/crm/benefits/entries/${_upTarget}/receipt`,
                { method: 'POST', body: fd,
                  headers: tok ? { Authorization: 'Bearer ' + tok } : {} });
            inp.value = '';
            if (!r.ok) {
                let msg = r.status;
                try { msg = (await r.json()).detail || msg; } catch (_) { /* 非 JSON */ }
                alert('單據上傳失敗：' + msg);
                return;
            }
            await _load();
        };
        document.body.appendChild(inp);
    }
    inp.click();
};

/** 寫／改心得。用 prompt 會把換行吃掉，所以走一個小視窗。 */
window._hbEditNote = (id) => {
    const e = _entryById(id);
    if (!e) return;
    const wrap = document.createElement('div');
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;'
        + 'display:flex;align-items:center;justify-content:center;';
    wrap.innerHTML = `
      <div style="background:#232323;border:1px solid #3a3a3a;border-radius:8px;padding:16px;width:min(560px,92vw);">
        <div style="color:#eee;font-size:14px;font-weight:600;margin-bottom:2px;">心得筆記</div>
        <div style="color:#888;font-size:12px;margin-bottom:10px;">
          ${esc(e.staff_name)}　${esc(e.title)}　${esc(e.spend_date)}（選填）</div>
        <textarea id="hb-note-text" rows="6"
                  style="width:100%;background:#1a1a1a;border:1px solid #333;color:#ddd;
                         border-radius:4px;padding:8px;font-size:12.5px;resize:vertical;"
        >${esc(e.reflection || '')}</textarea>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px;">
          <button class="hb-btn ghost" id="hb-note-cancel">取消</button>
          <button class="hb-btn" id="hb-note-save">儲存</button>
        </div>
      </div>`;
    document.body.appendChild(wrap);
    const close = () => wrap.remove();
    wrap.onclick = (ev) => { if (ev.target === wrap) close(); };
    wrap.querySelector('#hb-note-cancel').onclick = close;
    wrap.querySelector('#hb-note-save').onclick = async () => {
        const text = wrap.querySelector('#hb-note-text').value;
        const r = await call(`/api/v1/crm/benefits/entries/${id}/reflection`, {
            method: 'PUT', body: { reflection: text },
        });
        close();
        if (r) await _load();
    };
    wrap.querySelector('#hb-note-text').focus();
};

window._hbCreatePool = async () => {
    const name = (el('hb-p-name').value || '').trim();
    if (!name) { alert('請填池名稱'); return; }
    const r = await call('/api/v1/crm/benefits/pools',
                         { method: 'POST', body: { name } });
    if (r) { _sel = r.pool.id; await _load(); }
};

window._hbDeletePool = async () => {
    if (!_sel) return;
    if (!confirm('確定刪除這個福利池？（底下有撥款或登記就刪不掉）')) return;
    const r = await call('/api/v1/crm/benefits/pools/' + _sel, { method: 'DELETE' });
    if (r) { _sel = null; await _load(); }
};

window._hbAddFunding = async () => {
    const amount = parseInt(el('hb-f-amount').value || '0', 10);
    if (!amount) { alert('請填撥款金額'); return; }
    const r = await call(`/api/v1/crm/benefits/pools/${_sel}/fundings`, {
        method: 'POST',
        body: { amount, year: parseInt(el('hb-f-year').value || '0', 10),
                fund_date: el('hb-f-date').value || '' },
    });
    if (r) await _load();
};

window._hbDelFunding = async (id) => {
    if (!confirm('確定刪除這筆撥款？池的餘額會跟著少。')) return;
    const r = await call('/api/v1/crm/benefits/fundings/' + id, { method: 'DELETE' });
    if (r) await _load();
};

window._hbAddEntry = async () => {
    const amount = parseInt(el('hb-e-amount').value || '0', 10);
    const title = (el('hb-e-title').value || '').trim();
    if (!title || !amount) { alert('請填項目與金額'); return; }
    const r = await call('/api/v1/crm/benefits/entries', {
        method: 'POST',
        body: { pool_id: _sel, staff_id: el('hb-e-staff').value || '',
                title, amount, spend_date: el('hb-e-date').value || '' },
    });
    if (r) await _load();
};

const _act = (path, confirmMsg) => async (id) => {
    if (confirmMsg && !confirm(confirmMsg)) return;
    const r = await call(`/api/v1/crm/benefits/entries/${id}/${path}`,
                         { method: 'POST' });
    if (r) await _load();
};

window._hbApprove = _act('approve', '確定核准？會自動產一張應付款進應付帳款。');
window._hbPay = _act('pay', '確定登記匯款？會結清成一列收支明細（重按不會重複記帳）。');

window._hbReject = async (id) => {
    const reason = prompt('退回原因（可留白）：');
    if (reason === null) return;      // 按取消
    const r = await call(
        `/api/v1/crm/benefits/entries/${id}/reject?reason=${encodeURIComponent(reason)}`,
        { method: 'POST' });
    if (r) await _load();
};

window._hbPreview = async () => {
    const y = el('hb-k-year').value || '0';
    const r = await call('/api/v1/crm/benefits/accounting-package?year=' + y,
                         { method: 'GET' });
    if (!r) return;
    const rows = (r.summary || []).map(s => `<tr>
        <td>${esc(s.pool)}</td><td class="num">${fmt(s.funded)}</td>
        <td class="num">${fmt(s.used)}</td><td class="num">${fmt(s.balance)}</td>
        <td class="num">${s.count}</td></tr>`).join('');
    el('hb-k-out').innerHTML = `
        <table style="margin-top:6px;">
            <thead><tr><th>池</th><th class="num">撥款</th><th class="num">已用</th>
                <th class="num">餘額</th><th class="num">筆數</th></tr></thead>
            <tbody>${rows || '<tr><td colspan="5" class="hb-empty">這個區間沒有</td></tr>'}</tbody>
        </table>
        <div style="margin-top:8px;">撥款合計 ${fmt(r.total_funded)}　支出合計 ${fmt(r.total_used)}</div>`;
};

window._hbDownload = () => {
    // 走 fetch 而不是 <a href> —— 這支端點要 Authorization 標頭，
    // 直接開連結會 401（而且瀏覽器只會給一個空白頁，看不出原因）
    const y = el('hb-k-year').value || '0';
    bfetch('/api/v1/crm/benefits/accounting-package.csv?year=' + y)
        .then(async (r) => {
            if (!r.ok) { alert('下載失敗：' + r.status); return; }
            const blob = await r.blob();
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'welfare_' + y + '.csv';
            a.click();
            URL.revokeObjectURL(a.href);
        });
};

export async function initHrBenefitsTab() {
    await _load();
}
