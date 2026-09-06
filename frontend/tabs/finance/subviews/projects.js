/**
 * projects.js — 📁 執行專案（帳本視角的逐案損益表；2026-08-24，2026-08-25 owner 定名「執行專案」——內部識別字 projects/fpl 不改）。
 *
 * owner：「我希望是這種形式的，我可以自己設定每個工項的費用」「像 crm 的專案
 * 管理表那種，打開右側有詳細的表」。所以版面＝CRM 的左列右詳情（沿用
 * crm-body / crm-list-panel / crm-detail-panel，my-ledger.html 已載 crm.css），
 * 右側詳情是**可編輯**的逐案損益表：營收、費用七欄、工項拆分十項。
 *
 * 🔴 本子視圖**固定打私帳（entity='mine'）**，在主系統也是（owner 2026-08-25
 * 「這裡的逐案損益也先都放在私帳」）——那個「先」到 2026-08-30 結束：owner
 * 「crm 的執行專案，是 for 母公司的，你現在呈現的數字與項目都是私帳的」。
 * 現在**跟著當前帳本**（finFetch 會帶 window._finEntity）：主系統的財務管理＝
 * 母公司、/my-ledger.html＝私帳（那頁把 _finEntity 釘成 mine）。
 *
 * 🔴 母公司模式要收起來的是**私帳專屬的模型**：案源／服務費率／代開發票費／
 * 個人稅款／股東往來／工項拆分／實收／檢查，還有一鍵請款（它寫死開私帳的
 * 請款單）。那些欄位母公司的專案根本沒有（ledger_detail 是空的），畫出來
 * 不只沒意義 —— 存下去就是把私帳形狀的資料寫進母公司的列。
 * 兩本帳都成立的只有：營收／應收／已收／未收／應付／未付。
 *
 * 不跟著頁面 pin 走。入口按鈕只給帳號上真的有 finance_mine 的人
 * （finance.js），後端 require_entity 是真正的牆。
 *
 * 內容＝原 Sheet「結案總表」的系統版。實收與檢查**不在前端算** ——
 * 算式正本在後端 api_finance_projects.compute()，兩份算式必然漂移。
 * 存檔後端回算好的值回來，這裡只顯示。
 */
import { finFetch, finIsMine as _isMine, esc, fmtNum, finToast, todayStr }
    from '../fin-utils.js';
import { crmFetch, setupResizeHandle } from '../../crm/crm-utils.js';

let _c = null;
let _isCurrent = () => true;
let _data = null;
let _sel = null;        // 目前選取的專案 id
let _detail = null;     // 右側載入的單案資料
let _q = '';
// 收付狀態篩選（''＝全部）。取代舊的「只看未收清」checkbox —— 那個用的是
// `payment_status` 欄，跟畫面上的未收不是同一個口徑（收齊的代開案在舊欄位裡
// 還是「部分到帳」），兩種判準並存只會讓人不知道該信哪一個。
let _settle = '';
let _fy = '';           // 會計年度篩選（''=全部、'open'=未結案、數字=FY 結束年）
let _dirty = false;
let _resizeBound = false;

/** 後端隨 /project-ledger 送來的代扣費率（同檔其他 helper 一樣直接讀 _detail，
 *  不把模組級的值繞一圈當參數傳進來再防禦一次）。 */
const _wh = () => (_detail && _detail.withhold) || {};

/** 代開發票的服務費率預設 —— 正本是後端的 `DEFAULT_FEE_PCT`。 */
const _defaultFeePct = () => (_detail && _detail.default_fee_pct) || 0;

/** 執行業務所得的源頭代扣試算 —— 演算法同後端 `core.ledger_project.withholding`。
 *  🔴 **費率吃後端回的 `withhold`**（`/project-ledger/{id}` 帶回來），這裡不寫死
 *  10／2,000／2.11％／20,000：二代健保費率是法定的、動過不只一次，寫死的那份
 *  改法後會讓畫面上的預覽跟存進去的值不一致，而預覽值一旦被使用者「確認」
 *  就會被 `manual` 清單凍住。同 `fee_pct` 的處理（cash-split-editor 那條）。 */
function _proTax(contract) {
    const c = Number(contract) || 0;
    const r = _wh();
    let tax = Math.round(c * (Number(r.tax_pct) || 0) / 100);
    if (tax <= (Number(r.tax_exempt) || 0)) { tax = 0; }
    const nhi = c >= (Number(r.nhi_min) || 0)
        ? Math.round(c * (Number(r.nhi_pct) || 0) / 100) : 0;
    return tax + nhi;
}

/** 那段試算的說明文字 —— 費率同樣從後端來，不在文案裡再抄一份數字。 */
function _proTaxTitle() {
    const r = _wh();
    return `試算：源頭代扣 ${r.tax_pct}%＋二代健保 ${r.nhi_pct}%（含免扣門檻）`
        + ' —— 可自行調整，例如客戶拆單就不用先繳';
}

// 表頭與資料列共用一份欄寬 —— 分開寫的話改一邊就整排對不齊
// 十欄：狀態／結案日／專案／客戶／營收／應收／未收／應付／未付／檢查
// owner 2026-08-29 要一眼回答五件事：① 營收 ② 客戶會匯多少（扣掉代辦費）
// ③ 我要匯出去多少 ④ 收齊了嗎還剩多少 ⑤ 付清了嗎還剩多少
// 🔴 「檢查」（實收−Σ工項）是**私帳的算式** —— 母公司的專案沒有工項拆分，
// 那一欄永遠 0，佔著一欄還讓人以為公司帳有什麼對不上。母公司模式少一欄。
const _grid = () => 'display:grid;grid-template-columns:'
    + '46px 82px 1.3fr 0.85fr 94px 94px 90px 94px 90px'
    + (_isMine() ? ' 56px' : '') + ';align-items:center;gap:8px;';

// 收付狀態（C 案，owner 2026-08-29 選）：結案＝一個綠色「結」，
// 未結才長出「收」「付」兩字各自上色。判定在後端（settle_state），這裡只畫。
// 🔴 `out === 'none'`（這案沒有要付的）**不畫那個字** —— 畫一個灰「付」會讓人
// 以為是「還沒付」；少一個字反而說得清楚。
const _ST_IN = { ok: ['收', '#86efac'], wait: ['收', '#fbbf24'], over: ['溢', '#c4b5fd'] };
const _ST_OUT = { ok: ['付', '#86efac'], wait: ['付', '#fca5a5'] };

function _stTip(p) {
    const st = p.settle || {};
    if (st.done) { return '結案 —— 該收該付都完成'; }
    const a = st.in === 'over' ? `溢收 ${fmtNum(-p.to_collect)}`
        : st.in === 'wait' ? `還有 ${fmtNum(p.to_collect)} 沒收到` : '收齊了';
    const b = st.out === 'wait' ? `還有 ${fmtNum(p.ap_open)} 沒付`
        : st.out === 'ok' ? '付清了' : '這案沒有要付的';
    return `${a}；${b}`;
}

function _stHtml(p) {
    const st = p.settle || {};
    const ch = (t, c) => `<b style="color:${c};font-weight:600;">${t}</b>`;
    const body = st.done ? ch('結', '#86efac')
        : [_ST_IN[st.in] && ch(..._ST_IN[st.in]),
           _ST_OUT[st.out] && ch(..._ST_OUT[st.out])].filter(Boolean).join('');
    return `<span style="display:inline-flex;gap:5px;font-size:12px;letter-spacing:.02em;"
                  title="${esc(_stTip(p))}">${body}</span>`;
}

/** 請款單那一列的動作鈕：已付款 → 收回請款；未付 → 標記付款。
 *
 *  owner 2026-09-01「我需要有按鈕可以收回請款」—— 應付帳款視圖早就有這顆
 *  （crm-payables 的 ↩），但專案詳情這一區沒有，於是標錯付款狀態只能跑去
 *  另一個 tab 找那張單。兩邊走**同一組端點**（batch-pay / batch-unpay），
 *  狀態機只有一份：那支會連帶處理代開發票的撥款狀態（sync_remit_status）。
 */
const _payBtn = (x) => (x.payment_status === '已付款'
    ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" title="改回應付款（單子留著）"
              style="font-size:10px;padding:1px 6px;"
              onclick="window._finProjLedger.unpay('${esc(x.id)}')">改回應付</button>`
    : `<button class="crm-btn crm-btn-secondary crm-btn-sm" title="標記為已付款"
              style="font-size:10px;padding:1px 6px;color:#86efac;"
              onclick="window._finProjLedger.pay('${esc(x.id)}')">標記付款</button>
       <button class="crm-btn crm-btn-secondary crm-btn-sm" title="撤掉這張請款單（這筆不用請了）"
              style="font-size:10px;padding:1px 6px;color:#fca5a5;margin-left:4px;"
              data-id="${esc(x.id)}" data-sum="${esc(x.summary || '')}"
              onclick="window._finProjLedger.withdraw(this.dataset.id, this.dataset.sum)">收回請款</button>`);   // 摘要走 data-（內插進 JS 字串遇到 ' 會 SyntaxError）

export default async function render(container, ctx = {}) {
    _c = container;
    if (ctx.isCurrent) _isCurrent = ctx.isCurrent;
    _c.innerHTML = '<div style="color:#888;padding:40px;text-align:center;">載入專案…</div>';
    await _load();
}

/** 整份拉一次就好 —— 402 列連同工項明細約 180KB，搜尋每敲一個字重抓一次
 *  是純白工（後端還要再跑兩個 group-by 聚合）。篩選改在前端做。 */
async function _load() {
    try {
        const d = await finFetch('/project-ledger');
        if (!_isCurrent()) return;
        _data = d;
        _renderShell();
        _consumeJump();      // 從專案管理跳過來的話，清單畫好後直接開那一案
    } catch (e) {
        _c.innerHTML = `<div style="color:#f87171;padding:40px;text-align:center;">專案載入失敗：${esc(e.message)}</div>`;
    }
}

/** 結案日 → 會計年度（7/1–6/30，以結束年命名；同 fin-utils 的 FY 口徑）。 */
function _closeFY(p) {
    if (!p.close_date) return null;
    const y = +p.close_date.slice(0, 4), m = +p.close_date.slice(5, 7);
    return m >= 7 ? y + 1 : y;
}

/** 目前畫面上的列（前端篩選：402 列已經在手上，不必回伺服器）。 */
/** 狀態篩選 —— 判準與最左欄同一份（後端 settle_state 算好帶下來），
 *  這裡只是照 `settle` 挑。前端自己再判一次的話，篩出來的和看到的會不一樣。 */
function _settleMatch(p) {
    const st = p.settle || {};
    if (!_settle) { return true; }
    if (_settle === 'done') { return !!st.done; }
    if (_settle === 'in') { return st.in === 'wait'; }
    if (_settle === 'out') { return st.out === 'wait'; }
    if (_settle === 'either') { return st.in === 'wait' || st.out === 'wait'; }
    if (_settle === 'over') { return st.in === 'over'; }
    return true;
}

/** 這一案所有找得到它的名字（顯示名／私帳原名／連到的母帳案名），給 _visible 的搜尋用；
 *  tooltip（_nameTip）要帶「私帳原名：」「母帳：」標籤所以自己排，加名字來源兩邊都要補。 */
function _names(p) {
    return [p.name, p.orig_name, ...(p.parent_names || [])].filter(Boolean);
}


/** 列上的 tooltip：顯示名之外把私帳原名與母帳案名一起講清楚。 */
function _nameTip(p) {
    const t = [p.name];
    if (p.orig_name) { t.push('私帳原名：' + p.orig_name); }
    if ((p.parent_names || []).length) { t.push('母帳：' + p.parent_names.join('、')); }
    return t.join('\n');
}


/** 名字後面那顆小標。連多個母帳案時自動規則挑不出誰對（見後端
 *  linked_display_name），標出來讓人知道要自己命名；自訂過的也標一下。 */
function _nameTag(p) {
    const n = (p.parent_names || []).length;
    const tag = (t) => ` <span style="font-size:10px;color:#6b7280;">${t}</span>`;
    if (p.custom_name) { return tag('自訂'); }
    return n > 1 ? tag(`連 ${n} 案`) : '';
}


function _visible() {
    const q = _q.toLowerCase();
    return (_data.projects || []).filter(p =>
        // 🔴 搜尋要吃**三個**名字：顯示名、私帳原名、連到的母帳案名。顯示名換成
        // 母帳的之後，用舊名（「開村影片」）還是要找得到，否則這個功能會變成
        // 「東西不見了」。正本說明見 core.ledger_project.linked_display_name。
        (!q || _names(p).some((n) => n.toLowerCase().includes(q))
            || (p.client || '').toLowerCase().includes(q))
        && _settleMatch(p)
        && (!_fy || (_fy === 'open' ? !p.close_date : _closeFY(p) === +_fy)));
}

/** 合計列 —— 由**畫面上的列**即時計算（年度/搜尋/未收清篩下去合計跟著變：
 *  選 FY2026 這裡就是你年度表的「實際營收 8,103,670」那排數字）。 */
function _renderTotals() {
    const el = document.getElementById('fpl-totals');
    if (!el) return;
    const rows = _visible();
    const t = { contract: 0, outsource: 0, invoice_fee: 0, net: 0,
                received: 0, receivable: 0, ap_open: 0, spent: 0,
                client_wire: 0, payout: 0 };
    rows.forEach((p) => {
        t.contract += p.contract || 0;
        t.net += p.net || 0;
        t.received += p.received || 0;
        t.receivable += p.to_collect || 0;   // 未收走 to_collect（同列表那欄）
        t.ap_open += p.ap_open || 0;
        t.spent += p.spent || 0;
        t.client_wire += p.client_wire || 0;
        t.payout += p.payout || 0;
        t.outsource += (p.detail && p.detail.outsource) || 0;
        t.invoice_fee += (p.detail && p.detail.invoice_fee) || 0;
    });
    const cell = (label, key, color) =>
        `<span>${label} <b style="color:${color};">$${fmtNum(t[key])}</b></span>`;
    // 🔴 「淨收」以前叫「實收」—— 但 owner 2026-08-29 把列表的「實收」定義成
    // **真的收到的錢**，同一頁兩個「實收」指不同東西會看錯帳。這裡改名，
    // 詳情面板與 Sheet 維持原詞（那是結案總表的正本用語）。
    el.innerHTML = cell('營收', 'contract', '#eee')
        + cell('應收', 'client_wire', '#86efac')
        + cell('已收', 'received', '#86efac')
        + cell('未收', 'receivable', '#fbbf24')
        + cell('應付', 'payout', '#c4b5fd')
        + cell('已付', 'spent', '#c4b5fd')
        + cell('未付', 'ap_open', '#fca5a5')
        + cell('淨收', 'net', '#eee');
}

function _renderCount(n, rows) {
    const el = document.getElementById('fpl-count');
    if (!el) return;
    const unbalanced = (rows || []).reduce((a, p) => a + (p.check ? 1 : 0), 0);
    el.innerHTML = `${fmtNum(n)}${n === _data.count ? '' : ' / ' + fmtNum(_data.count)} 案`
        + (unbalanced && _isMine()
            ? `｜<span style="color:#fbbf24;">${unbalanced} 案檢查≠0</span>` : '');
}

/** 只切 selected class —— 重建整份 innerHTML 會重新解析約 2,800 個節點，
 *  而且把清單的捲動位置歸零（點第 300 列就跳回頂端）。 */
function _markSelected() {
    const body = document.getElementById('fpl-list-body');
    if (!body) return;
    body.querySelector('.crm-row.selected')?.classList.remove('selected');
    body.querySelector(`.crm-row[data-id="${_sel}"]`)?.classList.add('selected');
}

function _renderShell() {
    _c.innerHTML = `
        <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:10px;">
            <input id="fpl-q" class="crm-input" placeholder="搜尋專案 / 客戶" style="width:200px;" value="${esc(_q)}">
            <select class="crm-input" id="fpl-settle" title="收付狀態（與最左欄同一份判定）"
                    style="width:150px;" data-no-search>
                ${[['', '全部狀態'], ['either', '待收或待付'], ['in', '待收'],
                   ['out', '待付'], ['done', '已結案'], ['over', '溢收']]
                    .map(([v, t]) => `<option value="${v}"${_settle === v ? ' selected' : ''}>${t}</option>`).join('')}
            </select>
            <select class="crm-input" id="fpl-fy" title="會計年度（7/1–6/30）" style="width:170px;" data-no-search>
                <option value="">全部年度</option>
                <option value="open"${_fy === 'open' ? ' selected' : ''}>未結案</option>
                ${[...new Set((_data.projects || []).map(_closeFY).filter(Boolean))]
                    .sort((a, b) => b - a)
                    .map((y) => `<option value="${y}"${String(_fy) === String(y) ? ' selected' : ''}>${y - 1}/07 – ${y}/06</option>`).join('')}
            </select>
            <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._finProjLedger.create()">＋ 新增專案</button>
            <div style="flex:1;"></div>
            <span id="fpl-count" style="font-size:12px;color:#888;"></span>
        </div>
        <div id="fpl-create" style="display:none;background:#202020;border:1px solid #3b82f6;border-radius:8px;padding:14px;margin-bottom:10px;">
            <div style="display:grid;grid-template-columns:${
                _isMine() ? '2fr 1.4fr 1fr 1fr 1fr' : '2fr 1.4fr 1fr 1fr'};gap:8px;">
                <label style="color:#888;font-size:11px;">專案名稱*<input class="crm-input" id="fpc-name"></label>
                <label style="color:#888;font-size:11px;">客戶<select class="crm-input" id="fpc-client"><option value="">— 未定 —</option></select></label>
                <label style="color:#888;font-size:11px;">或新客戶<input class="crm-input" id="fpc-newclient" placeholder="（建 CRM 客戶）"></label>
                <label style="color:#888;font-size:11px;">案碼<input class="crm-input" id="fpc-code" placeholder="例 2026051"></label>
                <label style="color:#888;font-size:11px;">結案日<input class="crm-input" type="date" id="fpc-close"></label>
                <label style="color:#888;font-size:11px;">營收(含稅)<input class="crm-input" type="number" id="fpc-contract"></label>
                ${!_isMine() ? '' : `
                <label style="color:#888;font-size:11px;">案源<select class="crm-input" id="fpc-source">
                    <option value="">—</option>
                    <option value="源日">源日（現金收款）</option>
                    <option value="代開發票">代開發票（扣服務費）</option>
                    <option value="執行業務所得">執行業務所得（自動代扣）</option></select></label>
                <label style="color:#888;font-size:11px;" id="fpc-fee-wrap" hidden>服務費率 %<input class="crm-input" type="number" id="fpc-feepct" value="8" step="0.1"></label>`}
            </div>
            <div style="display:flex;gap:8px;margin-top:10px;align-items:center;">
                <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._finProjLedger.createSave(this)">建立</button>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="document.getElementById('fpl-create').style.display='none'">取消</button>
                <span style="color:#666;font-size:11px;">建立後直接打開詳情，工項與費用在那邊填。案碼＝你自己的案件編號（可留空；重複會擋）。</span>
            </div>
        </div>
        <div id="fpl-totals" style="display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:#ccc;
                    margin-bottom:10px;background:#202020;border:1px solid #2e2e2e;
                    border-radius:8px;padding:10px 14px;"></div>
        <div class="crm-body" id="fpl-body" style="min-height:320px;">
            <div class="crm-list-panel" id="fpl-list-panel">
                <div class="crm-list-header" style="${_grid()}">
                    <span style="text-align:center;" title="收付狀態。結＝該收該付都完成；未完成才分開標「收」「付」。這裡只看錢清了沒，跟結案日無關">狀態</span>
                    <span>結案日</span><span>專案</span><span>客戶</span>
                    <span style="text-align:right;">營收</span>
                    <span style="text-align:right;" title="客戶總共會匯給我多少＝營收 − 代辦費 − 個人稅款（源頭代扣的錢不會經過我的手）">應收</span>
                    <span style="text-align:right;" title="還沒收到的＝應收 − 已收。0 表示收齊了。舊帳的代開案收款記全額，收齊時差額會是負的代辦費 —— 那不是溢收，在源頭代扣的範圍內一律當 0；超出範圍的負數是真的溢收，照實顯示">未收</span>
                    <span style="text-align:right;" title="我總共要匯出去多少＝委外 + 行政雜支 + 稅金 + 買發票">應付</span>
                    <span style="text-align:right;" title="還沒付出去的（未付的請款單）。0 表示付清了">未付</span>
                    ${!_isMine() ? '' : '<span style="text-align:right;" title="淨收 − Σ工項；0 表示工項拆分剛好對上">檢查</span>'}
                </div>
                <div id="fpl-list-body"></div>
            </div>
            <div class="crm-resize-handle" id="fpl-resize"></div>
            <div class="crm-detail-panel" id="fpl-detail" style="display:none;width:46%;"></div>
        </div>`;
    _renderTotals();
    _renderList();
    const qEl = document.getElementById('fpl-q');
    let timer;
    qEl.addEventListener('input', (e) => {
        _q = e.target.value.trim();
        clearTimeout(timer);
        timer = setTimeout(_renderList, 150);   // 前端篩選，不用回伺服器
    });
    document.getElementById('fpl-settle').addEventListener('change', (e) => {
        _settle = e.target.value;
        _renderList();
    });
    document.getElementById('fpl-fy').addEventListener('change', (e) => {
        _fy = e.target.value;
        _renderList();
    });
    setupResizeHandle('fpl-resize', 'fpl-list-panel');
    _fitBody();
    if (!_resizeBound) {
        window.addEventListener('resize', _fitBody);
        _resizeBound = true;
    }
    if (_sel) _fp.open(_sel);
}

/** 把表格框在可視高度內 —— 這一步同時修掉兩件事（2026-08-25 實測）：
 *
 *  1. 表頭捲走：`#fpl-list-body` 吃得到 crm.css 的 `[id$="-list-body"]`
 *     （flex:1 + overflow-y:auto），但外層沒有高度限制時它會長到 16,884px、
 *     內捲永遠不發生，於是整頁一起捲、表頭跟著不見。
 *  2. 🔴 更嚴重的：詳情面板是清單的 flex 兄弟，容器 16,916px 高時，捲到第
 *     300 列點開，詳情是畫在**整個表格的頂端**（往上一萬多 px）＝看不到。
 *
 *  用量的不用寫死 px：捲動容器（my-ledger 是 #finance-content、主系統是頁面）
 *  的可視底部 − 表格頂端。視窗縮放時重算。
 */
function _fitBody() {
    const body = document.getElementById('fpl-body');
    if (!body) return;
    const scroller = document.getElementById('finance-content');
    const bottom = scroller && getComputedStyle(scroller).overflowY === 'auto'
        ? scroller.getBoundingClientRect().bottom
        : window.innerHeight;
    const avail = bottom - body.getBoundingClientRect().top - 12;
    body.style.height = Math.max(320, Math.round(avail)) + 'px';
}

/** 金額格：0 畫成灰破折號 —— 406 列裡多數是 0，一整片「0」會把真正有數字的
 *  那幾列淹掉（看不出哪案還欠錢，正是這幾欄要回答的問題）。 */
const _amt = (v, color) => `<span style="text-align:right;color:${
    v ? color : '#3f3f46'};">${v ? fmtNum(v) : '—'}</span>`;

function _renderList() {
    const body = document.getElementById('fpl-list-body');
    const keepScroll = body.scrollTop;
    const rows = _visible();          // 篩一次就好（_renderCount 原本又篩一次）
    body.innerHTML =
        rows.map((p) => `
        <div class="crm-row${p.id === _sel ? ' selected' : ''}" data-id="${p.id}"
             style="${_grid()}"
             onclick="window._finProjLedger.open('${p.id}')">
            <span style="text-align:center;">${_stHtml(p)}</span>
            <span style="color:${p.close_date ? '#9ca3af' : '#6b7280'};white-space:nowrap;">${esc(p.close_date || '未結案')}</span>
            <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#e0e0e0;"
                  title="${esc(_nameTip(p))}">${esc(p.name)}${_nameTag(p)}</span>
            <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#9ca3af;"
                  title="${esc(p.client)}">${esc(p.client)}</span>
            <span style="text-align:right;">${fmtNum(p.contract)}</span>
            ${_amt(p.client_wire, '#86efac')}
            ${_amt(p.to_collect, '#fbbf24')}
            ${_amt(p.payout, '#c4b5fd')}
            ${_amt(p.ap_open, '#fca5a5')}
            ${!_isMine() ? '' : `<span style="text-align:right;color:${
                p.check ? '#fbbf24' : '#4b5563'};">${p.check ? fmtNum(p.check) : '0'}</span>`}
        </div>`).join('')
        || '<div class="crm-empty">沒有符合的專案</div>';
    body.scrollTop = keepScroll;   // 重畫不該把使用者彈回列表頂端
    _renderCount(rows.length, rows);
    _renderTotals();               // 合計＝畫面上的列（篩選後跟著變）
}

// ── 右側詳情（可編輯）──────────────────────────────────────
const _fp = (window._finProjLedger = window._finProjLedger || {});

// 兩邊互通：切回財務分頁時 finance.js 呼叫（同一列資料，「同步」=重抓）。
// 有未存編修就整段跳過 —— 使用者手上的東西優先。
_fp.refresh = async () => {
    if (_dirty || !document.getElementById('fpl-list-body')) return;
    const d = await finFetch('/project-ledger');
    if (!_isCurrent()) return;
    _data = d;
    _renderTotals();
    _renderList();
    if (_sel && _detail) {
        _detail = await finFetch(`/project-ledger/${_sel}`);
        _renderDetail();
    }
};

// 從專案管理的「逐案損益 ↗」跳過來 —— render 收尾時呼叫（清單畫好才開得了案）
function _consumeJump() {
    const jump = sessionStorage.getItem('omgJumpLedgerProject');
    if (!jump) return;
    sessionStorage.removeItem('omgJumpLedgerProject');
    _fp.open(jump);
    document.querySelector(`#fpl-list-body .crm-row[data-id="${jump}"]`)
        ?.scrollIntoView({ block: 'center' });
}

_fp.open = async (id) => {
    if (_dirty && id !== _sel
        && !confirm('這一案有未儲存的修改，要放棄嗎？')) return;
    const same = _sel === id;
    _sel = id;
    _dirty = false;
    _markSelected();
    if (same && _detail) {
        // 同一案不用再拉一次 —— 但面板要重新打開：離開子視圖再回來時整個
        // 容器是重畫的（panel 回到 display:none），模組態的 _sel/_detail 卻
        // 還活著，走這條 early-return 就什麼都看不到（第 7 輪瀏覽器驗收實抓）。
        document.getElementById('fpl-detail').style.display = '';
        _renderDetail();
        return;
    }
    const panel = document.getElementById('fpl-detail');
    panel.style.display = '';
    panel.innerHTML = '<div style="color:#888;padding:30px;text-align:center;">載入中…</div>';
    try {
        _detail = await finFetch(`/project-ledger/${id}`);
        _renderDetail();
    } catch (e) {
        panel.innerHTML = `<div style="color:#f87171;padding:30px;">載入失敗：${esc(e.message)}</div>`;
    }
};

// 上一次畫的是哪一案 —— **同一案**重畫才保留捲動位置（換案子當然要回頂端）。
let _detailShown = null;

/** 詳情面板真正在捲的那一層。`.crm-detail-panel` 是 overflow:hidden 的 flex
 *  容器，捲的是裡面的 `.crm-detail-content` —— 量出來的，不寫死：版面改了
 *  這支就退回 null，最多是不保留，不會抓錯元素亂設 scrollTop。 */
function _detailScroller() {
    const el = document.querySelector('#fpl-detail .crm-detail-content');
    if (!el) { return null; }
    const ov = getComputedStyle(el).overflowY;
    return (ov === 'auto' || ov === 'scroll') && el.scrollHeight > el.clientHeight
        ? el : null;
}

function _renderDetail() {
    const d = _detail;
    const p = d.project;
    // 🔴 請款／存檔之後會重畫整個面板 —— 不記住捲動位置的話，畫面會跳回最上面，
    //    而那幾顆按鈕（委外人員、行政雜支的逐項請款）在面板最下面：
    //    按一次就得重新捲下去一次（owner 2026-08-29「請款完留在原本頁面」）。
    //
    //    ⚠️ 這是症狀不是病因：病因是**每次請款都整包 innerHTML 重畫**，被它
    //    毀掉的還有輸入框的游標位置與最下面 <details> 的展開狀態。下一次再有
    //    「重畫之後 X 不見了」的回報，要做的是改成只更新變動的那幾列
    //    （或請款後只打補那一行），而不是在這裡加第三個 preserve/restore。
    const _prev = _detailShown === p.id ? (_detailScroller()?.scrollTop || 0) : 0;
    const det = p.detail || {};
    // ro＝這一格由 CRM 專案帳目撐著（值是算出來的）。樣式跟著 ro 走，
    // 不另開一個 extra 參數 —— 那樣「看起來鎖住」和「真的鎖住」會各自漂。
    const money = (id, val, ro = false) => `
        <input type="number" class="crm-input fpl-num" id="${id}" value="${val || ''}"
               placeholder="0"${ro ? ' readonly' : ''}
               style="width:100%;text-align:right;${ro ? 'opacity:.75;cursor:not-allowed;' : ''}">`;
    // 由 CRM 專案帳目撐著的費用欄（行政雜支／人員費用）：顯示值＝手填＋CRM
    // （甲案，owner 2026-08-28）。輸入框裡放的是**手填那部分**，改得動 ——
    // 放合計的話一存檔就把 CRM 算出來的數字存成副本，CRM 改了這裡就走味。
    const src = p.cost_sources || {};
    const costRows = (d.cost_fields || []).map((f) => {
        const from = src[f.key];              // {crm, manual}；沒有＝純手填欄
        const label = from
            ? `${esc(f.label)}<span class="fpl-src" title="CRM 專案帳目算出 ${
                fmtNum(from.crm)}，這一格是額外手動加的，兩者相加">＋CRM ${fmtNum(from.crm)}</span>`
            : esc(f.label);
        const cell = money('fpl-c-' + f.key, from ? from.manual : det[f.key])
            + (from ? `<div style="font-size:10px;color:#666;text-align:right;margin-top:2px;">
                   合計 ${fmtNum((from.manual || 0) + from.crm)}</div>` : '');
        return `<tr><td style="color:#bbb;">${label}</td>
            <td style="width:130px;">${cell}</td></tr>`;
    }).join('');
    const splitRows = (d.income_items || []).map((it) => `
        <tr><td style="color:#bbb;">${esc(it)}</td>
            <td style="width:130px;">${money('fpl-s-' + encodeURIComponent(it), (det.split || {})[it])}</td></tr>`).join('');
    // 使用者自己加過、但不在預設清單裡的工項也要出現（否則存檔會靜默丟掉）
    const extra = Object.keys(det.split || {}).filter((k) => !(d.income_items || []).includes(k));
    const extraRows = extra.map((it) => `
        <tr><td style="color:#c4b5fd;">${esc(it)} <span style="font-size:10px;color:#666;">(自訂)</span></td>
            <td>${money('fpl-s-' + encodeURIComponent(it), det.split[it])}</td></tr>`).join('');

    document.getElementById('fpl-detail').innerHTML = `
        <div class="crm-detail-bar">
            <div class="crm-detail-bar-title">${esc(p.client)} / ${esc(p.name)}</div>
            <div class="crm-detail-bar-actions">
                ${p.crm_pushed && typeof window.switchTab === 'function'
                    ? `<button class="crm-btn crm-btn-secondary crm-btn-sm"
                               title="到專案管理開啟這一案（階段/派工/資料夾/會議記錄在那邊）"
                               onclick="window._finProjLedger.gotoCrm()">專案管理 ↗</button>`
                    : ''}
                ${!_isMine() ? '' : `
                <button class="crm-btn crm-btn-secondary crm-btn-sm"
                        title="${p.crm_pushed
                            ? '這一案已出現在專案管理的母公司管線（標「後期專案」）。再按一次取消。'
                            : '讓這一案出現在專案管理的母公司管線，標「後期專案」。錢流不變（仍在私帳，金額只有你看得到）。'}"
                        onclick="window._finProjLedger.push(this)">${p.crm_pushed ? '✓ 已在專案管理' : '⬆ 推專案管理'}</button>`}
                <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._finProjLedger.save(this)">儲存</button>
                <button class="crm-detail-close" onclick="window._finProjLedger.close()" title="關閉">✕</button>
            </div>
        </div>
        <div class="crm-detail-content" style="padding:14px 16px;">
            <div style="display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:#888;margin-bottom:12px;">
                <span>${esc(p.close_date || '未結案')}</span><span>${esc(p.status)}</span>
                <span>${esc(p.type)}</span><span>${esc(p.payment_status)}</span>
            </div>
            <div style="display:grid;grid-template-columns:${
                _isMine() ? '1fr 1fr' : '1fr'};gap:16px;">
                <div>
                    <div style="color:#ddd;font-size:12px;font-weight:600;margin-bottom:6px;">營收與費用</div>
                    <table class="crm-table" style="width:100%;font-size:12px;">
                        ${!_isMine() ? '' : `
                        <tr><td style="color:#bbb;" title="空白＝自動：連到 1 個母帳案就顯示母帳案名，否則顯示私帳原名。這裡改的是顯示名，私帳原名不動。">顯示名</td>
                            <td><input class="crm-input fpl-num" id="fpl-dispname"
                                value="${esc(p.custom_name || '')}"
                                placeholder="${esc(p.orig_name || p.name || '')}"
                                style="width:100%;"></td></tr>
                        ${!(p.parent_names || []).length ? '' : `
                        <tr><td></td><td style="font-size:11px;color:#6b7280;padding-top:0;">
                            母帳：${(p.parent_names || []).map((n, i) => `<a href="#" style="color:#7aa2f7;"
                                onclick="event.preventDefault();window._finProjLedger.useParentName(${i});">${esc(n)}</a>`).join('、')}
                            ${p.orig_name ? `<br>私帳原名：${esc(p.orig_name)}` : ''}
                        </td></tr>`}`}
                        <tr><td style="color:#bbb;">結案日</td>
                            <td style="width:130px;"><input type="date" class="crm-input fpl-num" id="fpl-close"
                                value="${esc(p.close_date || '')}" style="width:100%;"></td></tr>
                        <tr><td style="color:#bbb;">營收(含稅)</td><td>${money('fpl-contract', p.contract)}</td></tr>
                        ${!_isMine() ? '' : `
                        <tr><td style="color:#bbb;">案源</td>
                            <td><select class="crm-input fpl-num" id="fpl-source" style="width:100%;">
                                <option value="">—</option>
                                ${(() => {
                                    // 自接＝歷史值不再可選 —— 但舊案選著它時要就地補一個
                                    // 選項，否則畫面顯示成空、存檔會把值洗掉
                                    const label = (s) => s === '源日' ? '源日（現金收款）'
                                        : s === '代開發票' ? '代開發票（自動代辦費）'
                                        : s === '執行業務所得' ? '執行業務所得（自動代扣）'
                                        : s === '自接' ? '自接（歷史）' : s;
                                    const list = [...(d.sources || [])];
                                    if (det.source && !list.includes(det.source)) list.unshift(det.source);
                                    return list.map((s) => `<option value="${esc(s)}"${det.source === s ? ' selected' : ''}>${label(s)}</option>`).join('');
                                })()}
                            </select></td></tr>
                        <tr id="fpl-fee-row">
                            <td style="color:#bbb;">服務費率 %</td>
                            <td>${money('fpl-feepct', det.fee_pct || _defaultFeePct())}</td></tr>
                        ${costRows}`}
                    </table>
                    <table class="crm-table" style="width:100%;font-size:12px;margin-top:8px;">
                        ${!_isMine() ? '' : `
                        <tr><td style="color:#ddd;font-weight:600;">實收</td>
                            <td style="text-align:right;font-weight:600;color:#eee;" id="fpl-net">$${fmtNum(p.net)}</td></tr>
                        <tr><td style="color:#ddd;font-weight:600;">檢查（實收−Σ工項）</td>
                            <td style="text-align:right;font-weight:600;color:${p.check ? '#fbbf24' : '#86efac'};" id="fpl-check">${fmtNum(p.check)}</td></tr>`}
                        <tr><td style="color:#888;">已收 / 應收</td>
                            <td style="text-align:right;color:#888;">${fmtNum(p.received)} / ${fmtNum(p.receivable)}</td></tr>
                    </table>
                    ${!_isMine() ? '' : `
                    <div style="color:#666;font-size:11px;margin-top:6px;">
                        實收 = 營收 − 委外 − 代辦費 − 個人稅款 − 雜支 − 股東往來（後端算）。
                        檢查 0 表示工項拆分剛好等於實收。</div>`}
                </div>
                ${!_isMine() ? '' : `
                <div>
                    <div style="color:#ddd;font-size:12px;font-weight:600;margin-bottom:6px;">工項拆分</div>
                    <table class="crm-table" style="width:100%;font-size:12px;">
                        ${splitRows}${extraRows}
                        <tr><td style="color:#ddd;font-weight:600;">合計</td>
                            <td style="text-align:right;font-weight:600;color:#eee;" id="fpl-splitsum">$${fmtNum(Object.values(det.split || {}).reduce((a, b) => a + b, 0))}</td></tr>
                    </table>
                    <div style="display:flex;gap:6px;margin-top:8px;">
                        <input class="crm-input" id="fpl-newitem" placeholder="自訂工項名稱" style="flex:1;">
                        <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finProjLedger.addItem()">＋</button>
                    </div>
                </div>`}
            </div>
            ${_crmLinesHtml(d.crm_lines)}
            <div style="color:#ddd;font-size:12px;font-weight:600;margin:16px 0 6px;">掛在本案的收支（${(d.entries || []).length}）</div>
            <table class="crm-table" style="width:100%;font-size:12px;">
                <thead><tr><th>日期</th><th>摘要</th><th style="text-align:right;">存入</th><th style="text-align:right;">支出</th></tr></thead>
                <tbody>${(d.entries || []).map((e) => `
                    <tr><td style="white-space:nowrap;color:#888;">${esc(e.date)}</td>
                        <td>${esc(e.summary)}${e.split ? `<span title="這筆收支被拆成幾個項目，其中一項掛在本案${
                            e.fee ? `；金額是毛額（實匯 ${fmtNum(e.deposit - e.fee)} ＋ 代開費 ${fmtNum(e.fee)}）` : ''}"
                            style="margin-left:6px;font-size:10px;padding:1px 5px;border-radius:7px;background:#14351f;color:#86efac;">拆項${
                            e.fee ? '·含代開費' : ''}</span>` : ''}</td>
                        <td style="text-align:right;color:#86efac;">${e.deposit ? fmtNum(e.deposit) : ''}</td>
                        <td style="text-align:right;color:#fca5a5;">${e.expense ? fmtNum(e.expense) : ''}</td></tr>`).join('')
                    || '<tr><td colspan="4" style="color:#666;padding:10px;">（無）</td></tr>'}
                    ${(d.entries || []).length ? `<tr style="border-top:1px solid #3a3a3a;">
                        <td colspan="2" style="color:#888;">合計</td>
                        <td style="text-align:right;color:#86efac;font-weight:600;">${
                            fmtNum((d.entries || []).reduce((n, e) => n + (e.deposit || 0), 0))}</td>
                        <td style="text-align:right;color:#fca5a5;font-weight:600;">${
                            fmtNum((d.entries || []).reduce((n, e) => n + (e.expense || 0), 0))}</td></tr>` : ''}
                </tbody></table>
            <div style="display:flex;align-items:center;gap:8px;margin:16px 0 6px;">
                <span style="color:#ddd;font-size:12px;font-weight:600;">應付／請款單（${(d.payments || []).length}）</span>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-left:auto;"
                        onclick="window._finProjLedger.outsourceForm()">＋ 委外</button>
            </div>
            <div id="fpl-out-form" style="display:none;background:#202020;border:1px solid #3b82f6;border-radius:8px;padding:10px;margin-bottom:8px;">
                <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;">
                    <label style="color:#888;font-size:11px;">收款人<input class="crm-input" id="fpl-out-payee" style="width:120px;"></label>
                    <label style="color:#888;font-size:11px;">金額<input class="crm-input" type="number" id="fpl-out-amt" style="width:110px;"></label>
                    <label style="color:#888;font-size:11px;">說明<input class="crm-input" id="fpl-out-note" placeholder="（選填）" style="width:150px;"></label>
                    <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._finProjLedger.outsourceAdd(this)">加入</button>
                </div>
                <div style="color:#666;font-size:10px;margin-top:6px;">
                    加入＝建一張「專案外包」請款單（應付款）：本案的委外費用自動累加、應付帳款的匯款清單也會出現這一筆。</div>
            </div>
            <table class="crm-table" style="width:100%;font-size:12px;">
                <tbody>${(d.payments || []).map((x) => `
                    <tr><td>${esc(x.summary)}<div style="color:#666;font-size:10px;">${esc(x.category)}${x.payee ? '｜' + esc(x.payee) : ''}</div></td>
                        <td style="text-align:right;">${fmtNum(x.amount)}</td>
                        <td style="white-space:nowrap;color:${x.payment_status === '已付款' ? '#86efac' : '#fbbf24'};">${esc(x.payment_status)}</td>
                        <td style="white-space:nowrap;color:#888;font-size:11px;line-height:1.5;" title="請款日／付款日">請款 ${esc(x.request_date || '—')}<br>付款 ${esc(x.payment_date || '—')}</td>
                        <td style="text-align:right;white-space:nowrap;">${_payBtn(x)}</td></tr>`).join('')
                    || '<tr><td colspan="5" style="color:#666;padding:10px;">（無）</td></tr>'}</tbody></table>
            ${!_isMine() ? '' : `
            <details style="margin-top:14px;">
                <summary style="color:#888;font-size:12px;cursor:pointer;">匯入保留的原始備註（案碼／案源／税別／工項）</summary>
                <pre style="white-space:pre-wrap;color:#aaa;font-size:11px;background:#1a1a1a;
                            border:1px solid #2a2a2a;border-radius:6px;padding:10px;margin:6px 0 0;">${esc(p.notes || '（無）')}</pre>
            </details>`}
        </div>`;
    _detailShown = p.id;
    if (_prev) {
        const sc = _detailScroller();
        if (sc) { sc.scrollTop = _prev; }
    }
    document.querySelectorAll('#fpl-detail .fpl-num').forEach((el) => {
        el.addEventListener('input', () => { _dirty = true; _liveSum(); });
    });
    // 案源規則（正本在後端 apply_source_fee，這裡只是即時預覽同一條式子）：
    // 代開發票 → 代辦費三欄自動、鎖住；執行業務所得 → 個人稅款**試算**（可改，
    // 見下方）；其他案源恢復手填。
    // 「這一欄由人決定過」的答案在後端（落庫的 `manual` 清單）；這是它在本次
    // 編輯期間的鏡射，存檔後由後端回的值重新接手。
    // 🔴 舊資料的 `tax_manual` 布林鍵不在這裡認：API 回的一律是 norm_detail 的
    // 輸出，那一層已經把舊形狀轉成清單了。在這裡再認一次＝把過期的 schema 知識
    // 帶過語言邊界，而且永遠測不到（走不到那條路）。
    let taxManual = (det.manual || []).includes('personal_tax');
    const _syncFee = (sourceChanged = false) => {
        const src = document.getElementById('fpl-source')?.value;
        const feeEl = document.getElementById('fpl-c-invoice_fee');
        const row = document.getElementById('fpl-fee-row');
        if (!feeEl || !row) return;
        const isAgency = src === '代開發票';
        const isPro = src === '執行業務所得';
        row.style.display = isAgency ? '' : 'none';
        feeEl.disabled = isAgency;
        feeEl.title = isAgency ? '案源＝代開發票：代辦費＝營收×費率，自動計算' : '';
        const taxEl = document.getElementById('fpl-c-tax_fee');
        const buyEl = document.getElementById('fpl-c-buy_invoice');
        [taxEl, buyEl].forEach((el) => { if (el) el.disabled = isAgency; });
        const c = Number(document.getElementById('fpl-contract')?.value) || 0;
        if (isAgency) {
            // 同後端 apply_source_fee：代辦費=營收×費率；稅金=未稅×營業稅率；
            // 買發票=差額。🔴 兩個費率都吃後端回的（同 _wh() 那條）——
            // 寫死 8 與 5 的話，費率一改預覽就跟存進去的值不一致。
            const vat = Number((_detail && _detail.agency || {}).vat_pct) || 0;
            const pct = Number(document.getElementById('fpl-feepct')?.value) || _defaultFeePct();
            const fee = Math.round(c * pct / 100);
            const tax = vat ? Math.round(c / (1 + vat / 100) * (vat / 100)) : 0;
            feeEl.value = fee || '';
            if (taxEl) taxEl.value = tax || '';
            if (buyEl) buyEl.value = (fee - tax) || '';
            _liveSum();
        }
        // 個人稅款：自動值是**試算不是規定**（owner 2026-09-01「一開始先試算，
        // 但有些狀況讓我可以調整 —— 有些客戶會拆單，所以我不用先繳」）。
        // 🔴 所以這一格不鎖，而且只在兩種時候才動它：剛把案源切成執行業務所得、
        // 或它還等於上一次的試算值（＝人沒調過）。判準用「值等於試算」而不是
        // 一個 touched 旗標 —— 面板每次存檔都整個重畫，旗標活不過重畫，
        // 而使用者刻意設的 0（拆單不用先繳）會被當成「空的」再填回去。
        const ptEl = document.getElementById('fpl-c-personal_tax');
        if (ptEl) {
            ptEl.disabled = false;
            ptEl.title = isPro ? _proTaxTitle() : '';
            // 🔴「人調過了沒」不用猜：後端把它落庫成 `manual` 清單（見
            // core.ledger_project.apply_source_fee）並隨 detail 回來。這裡只
            // 在「剛換案源」或「這格還沒被人決定過」時填試算值 —— 上一版用
            // 模組級「上次試算值」做值比對，那個基準活不過面板重畫，還得在
            // 每次 render 手動重新校準。
            if (isPro && (sourceChanged || !taxManual)) {
                ptEl.value = _proTax(c) || '';
                _liveSum();
            }
        }
    };
    // 使用者一動這格就是「由人決定」（存檔後由後端回的 `manual` 清單接手）
    document.getElementById('fpl-c-personal_tax')
        ?.addEventListener('input', () => { taxManual = true; });
    // 🔴 fpl-source 只掛**一個** listener：它同時在那個 forEach 裡的話，換一次
    // 案源會跑兩三次 _syncFee，而 input 先於 change 觸發（sourceChanged=false）
    // —— 旗標的意義就變成看 listener 的註冊順序。
    document.getElementById('fpl-source')?.addEventListener('change',
        () => _syncFee(true));      // 換案源＝重新試算
    ['fpl-feepct', 'fpl-contract'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('input', () => _syncFee());
            el.addEventListener('change', () => _syncFee());
        }
    });
    _syncFee();     // 首次同步不算「換案源」—— 載入既有案時不覆寫人調過的稅款
}

/** 工項合計即時更新（實收/檢查等存檔後由後端回算 —— 前端不算第二份）。 */
function _liveSum() {
    let sum = 0;
    document.querySelectorAll('#fpl-detail [id^="fpl-s-"]').forEach((el) => {
        sum += Number(el.value) || 0;
    });
    const el = document.getElementById('fpl-splitsum');
    if (el) el.textContent = '$' + fmtNum(sum);
}

_fp.addItem = () => {
    const name = (document.getElementById('fpl-newitem').value || '').trim();
    if (!name) return finToast('請輸入工項名稱');
    if (!_detail.project.detail.split) _detail.project.detail.split = {};
    if (_detail.project.detail.split[name] !== undefined) return finToast('這個工項已經有了');
    _detail.project.detail.split[name] = 0;
    _renderDetail();
    _dirty = true;
};

_fp.close = () => {
    if (_dirty && !confirm('有未儲存的修改，要放棄嗎？')) return;
    _sel = null;
    _dirty = false;
    document.getElementById('fpl-detail').style.display = 'none';
    _markSelected();
};

let _clients = null;   // 客戶下拉（共用名錄，載一次）

_fp.create = async () => {
    const box = document.getElementById('fpl-create');
    box.style.display = box.style.display === 'none' ? '' : 'none';
    if (box.style.display === 'none') return;
    if (!_clients) {
        try {
            // 客戶主檔（統一到 CRM 之後 entity=mine 回的就是整份主檔）
            _clients = (await crmFetch('/clients?entity=mine')).clients || [];
        } catch (e) {
            _clients = [];   // 下拉是選配，載不到仍可建案 —— 但失敗要出聲
            finToast('客戶名錄載入失敗：' + e.message, 'error');
        }
        const sel = document.getElementById('fpc-client');
        sel.innerHTML = '<option value="">— 未定 —</option>' + _clients
            .map((c) => `<option value="${c.id}">${esc(c.short_name)}</option>`).join('');
    }
    document.getElementById('fpc-name').focus();
    // 費率欄只在代開發票時出現（預設 8，可逐案調）
    const srcSel = document.getElementById('fpc-source');
    if (srcSel && !srcSel._feeBound) {
        srcSel._feeBound = true;
        srcSel.addEventListener('change', () => {
            document.getElementById('fpc-fee-wrap').hidden = srcSel.value !== '代開發票';
        });
    }
};

_fp.createSave = async (btn) => {
    // 🔴 收 null：母公司模式不畫案源／費率那兩格（見檔頭）—— 裸的
    // `getElementById(id).value` 會 TypeError，整顆「建立」就啞掉
    const g = (id) => (document.getElementById(id)?.value || '').trim();
    if (!g('fpc-name')) { finToast('專案名稱必填', 'error'); return; }
    if (g('fpc-client') && g('fpc-newclient')) {
        finToast('「客戶」與「或新客戶」擇一填', 'error'); return;
    }
    btn.disabled = true;
    try {
        let clientId = g('fpc-client') || null;
        if (g('fpc-newclient')) {
            // 建客戶＝直接進 CRM 主檔（owner 2026-08-26「把私帳的客戶都整合到
            // crm 系統裡面」—— 再建私帳專屬客戶就又分裂了）
            const nc = await crmFetch('/clients', {
                method: 'POST',
                body: JSON.stringify({ short_name: g('fpc-newclient') }),
            });
            clientId = nc.client.id;
            _clients = null;      // 名錄變了，下次打開重抓
        }
        const r = await finFetch('/project-ledger', {
            method: 'POST',
            body: JSON.stringify({
                name: g('fpc-name'), client_id: clientId,
                code: g('fpc-code'), close_date: g('fpc-close'),
                contract_amount: parseInt(g('fpc-contract') || '0', 10) || null,
                source: g('fpc-source'),
                // 建立表單沒有 _detail 可讀 —— 留空讓後端套自己的預設，
                // 不在前端猜一個數字（後端 norm_detail 只在非預設時才存）
                fee_pct: parseFloat(g('fpc-feepct') || '') || undefined,
            }),
        });
        document.getElementById('fpl-create').style.display = 'none';
        ['fpc-name', 'fpc-code', 'fpc-close', 'fpc-contract', 'fpc-newclient'].forEach(
            (id) => { document.getElementById(id).value = ''; });
        finToast('已建立');
        await _load();              // 重抓整份（排序/合計由後端口徑）
        _fp.open(r.id);             // 直接打開詳情填工項
    } catch (e) {
        finToast('建立失敗：' + e.message, 'error');
    } finally { btn.disabled = false; }
};

/** CRM 專案帳目的明細（上面費用欄那兩個 CRM 合計的組成）。
 *  🔴 人員那張帶「請款」按鈕：一鍵建一張私帳的委外請款單，並用 cost_line_id
 *  釘住是哪一行 —— 請過的就標起來，不會重複付同一個人。 */
function _crmLinesHtml(cl) {
    if (!cl) { return ''; }
    const people = cl.people || [], misc = cl.misc || [];
    if (!people.length && !misc.length) { return ''; }
    const box = (title, sum, rows) => `
        <div style="margin-top:14px;">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
                <span style="color:#ddd;font-size:12px;font-weight:600;">${title}</span>
                <span class="fpl-src">CRM 專案帳目</span>
                <span style="margin-left:auto;color:#888;font-size:12px;">$${fmtNum(sum)}</span>
            </div>
            <table class="crm-table" style="width:100%;font-size:12px;"><tbody>${rows}</tbody></table>
        </div>`;
    const peopleRows = people.map((x) => `
        <tr><td>${esc(x.who || '（未指定人員）')}
                <div style="color:#666;font-size:10px;">${esc(x.phase)}｜${esc(x.item)}</div></td>
            <td style="text-align:right;">${fmtNum(x.amount)}</td>
            <td style="width:82px;text-align:right;">${x.claimed
                ? '<span style="color:#86efac;font-size:11px;">已請款</span>'
                : !_isMine() ? ''
                    : `<button class="crm-btn crm-btn-secondary crm-btn-sm"
                     onclick="window._finProjLedger.claimLine('${esc(x.id)}', this)">請款</button>`}</td></tr>`).join('');
    // 雜支也能逐項請款（owner 2026-08-29）。已經跟公司請過款的**不給按** ——
    // 那筆錢公司出了，私帳再請一次就是同一筆錢請兩次。
    const miscRows = misc.map((x) => `
        <tr><td>${esc(x.item || x.category)}
                <div style="color:#666;font-size:10px;">${esc(x.date)}｜${esc(x.category)}${
                    x.payee ? '｜' + esc(x.payee) : ''}${
                    x.billed_to_company ? '｜<span style="color:#fbbf24;">已跟公司請款（不計私帳成本）</span>' : ''}</div></td>
            <td style="text-align:right;color:${x.billed_to_company ? '#666' : '#ddd'};">${fmtNum(x.amount)}</td>
            <td style="width:82px;text-align:right;">${x.billed_to_company
                ? '<span style="color:#4b5563;font-size:11px;">公司出</span>'
                : x.claimed
                    ? '<span style="color:#86efac;font-size:11px;">已請款</span>'
                    : !_isMine() ? ''
                    : `<button class="crm-btn crm-btn-secondary crm-btn-sm"
                         onclick="window._finProjLedger.claimMisc('${esc(x.id)}', this)">請款</button>`}</td></tr>`).join('');
    return (people.length ? box('委外人員', people.reduce((a, b) => a + b.amount, 0), peopleRows) : '')
         + (misc.length ? box('行政雜支明細',
                misc.filter((x) => !x.billed_to_company).reduce((a, b) => a + b.amount, 0), miscRows) : '');
}

/** 行政雜支明細的「請款」：一鍵建一張私帳的專案雜支應付款。
 *  帶 expense_id 釘住是哪一行 —— 同 claimLine 的理由（同金額同項目分不出誰請過）。
 *  🔴 類別用「專案雜支」：它對映到的科目正好是 apply_ledger_project_costs
 *  會拿掉的那條現金鏡射，所以不會跟逐案的雜支重複計。 */
_fp.claimMisc = async (expenseId, btn) => {
    const row = ((_detail.crm_lines || {}).misc || []).find((x) => x.id === expenseId);
    if (!row) { return; }
    const label = row.item || row.category || '雜支';
    if (!window.confirm(`跟私帳請款：${label} $${fmtNum(row.amount)}
`
                        + `（${row.date}｜${row.category}${row.payee ? '｜' + row.payee : ''}）

`
                        + '會建一張私帳的「專案雜支」應付款，之後在應付帳款付掉。')) { return; }
    if (btn) { btn.disabled = true; }
    try {
        await crmFetch('/payments', {
            method: 'POST',
            body: JSON.stringify({
                entity: 'mine', category: '專案雜支', project_id: _sel,
                payee_name: row.payee || label, amount: row.amount, expense_id: row.id,
                summary: `雜支：${label}｜${_detail.project.name}`,
                request_date: todayStr(),
            }),
        });
        finToast(`已建立雜支請款：${label}`);
        await _fp.refresh();
    } catch (e) {
        if (btn) { btn.disabled = false; }
        finToast('請款失敗：' + e.message, 'error');
    }
};

/** 收回請款／標記付款 —— 與應付帳款視圖共用 crm 的批次端點。
 *  🔴 不自己 PUT payment_status：那支端點一次處理付款日與代開發票的撥款
 *  狀態，繞過去就會出現「請款單說沒付、發票說已撥款」的兩份答案。 */
async function _payAction(id, paid) {
    const path = paid ? '/payments/batch-pay' : '/payments/batch-unpay';
    const body = paid
        ? { payment_ids: [id], payment_date: todayStr() }
        : { payment_ids: [id] };
    try {
        await crmFetch(path, { method: 'PATCH', body: JSON.stringify(body) });
        finToast(paid ? '已標記付款' : '已收回請款');
        await _fp.open(_sel);         // 只重載這一案的詳情
    } catch (e) {
        finToast((paid ? '標記付款失敗：' : '收回失敗：') + e.message, 'error');
    }
}

_fp.pay = (id) => _payAction(id, true);

/** 收回請款＝**撤掉這張單**（owner 2026-09-01「要可以收回請款」，指的是還沒付
 *  的那張：這筆不用請了）。刪除端點會把私帳的委外費用一起沖回
 *  （_apply_outsource −1），所以逐案損益的「委外」不會留下幽靈數字。
 *  🔴 已付款的單不給這顆 —— 錢都出去了還撤單，帳上就會少一筆付款；
 *  那條路是「改回應付」再處理。 */
_fp.withdraw = async (id, summary) => {
    if (!confirm(`收回這張請款單？

${summary}

（單子會被刪掉；已付的錢請改用「改回應付」）`)) { return; }
    try {
        await crmFetch(`/payments/${id}`, { method: 'DELETE' });
        finToast('已收回請款');
        await _fp.open(_sel);
    } catch (e) {
        finToast('收回失敗：' + e.message, 'error');
    }
};
_fp.unpay = (id) => {
    if (!confirm('確定把這張請款單改回應付款？')) { return; }
    _payAction(id, false);
};

_fp.outsourceForm = () => {
    const box = document.getElementById('fpl-out-form');
    box.style.display = box.style.display === 'none' ? '' : 'none';
    if (box.style.display === '') document.getElementById('fpl-out-payee').focus();
};

/** 委外人員名單的「請款」：一鍵建一張私帳的委外請款單。
 *  帶 cost_line_id 釘住是哪一行 —— 請過的下次就顯示「已請款」，不會重複付。 */
_fp.claimLine = async (lineId, btn) => {
    const row = ((_detail.crm_lines || {}).people || []).find((x) => x.id === lineId);
    if (!row) { return; }
    const who = row.who || '';
    if (!who) {
        finToast('這一行沒有指定人員 —— 請先到 CRM 專案帳目補上收款人', 'error');
        return;
    }
    if (!window.confirm(`跟私帳請款：${who} $${fmtNum(row.amount)}
`
                        + `（${row.phase}｜${row.item}）

`
                        + '會建一張私帳的「專案外包」應付款，之後在應付帳款付掉。')) { return; }
    if (btn) { btn.disabled = true; }        // 連點兩下＝兩張單，後端 409 擋得住但別讓人看到錯誤
    try {
        await crmFetch('/payments', {
            method: 'POST',
            body: JSON.stringify({
                entity: 'mine', category: '專案外包', project_id: _sel,
                payee_name: who, amount: row.amount, cost_line_id: row.id,
                summary: `委外：${who}｜${_detail.project.name}（${row.phase}｜${row.item}）`,
                request_date: todayStr(),
            }),
        });
        finToast(`已建立委外請款：${who}`);
        await _fp.refresh();
    } catch (e) {
        if (btn) { btn.disabled = false; }
        finToast('請款失敗：' + e.message, 'error');
    }
};

_fp.outsourceAdd = async (btn) => {
    const payee = document.getElementById('fpl-out-payee').value.trim();
    const amt = parseInt(document.getElementById('fpl-out-amt').value, 10) || 0;
    const note = document.getElementById('fpl-out-note').value.trim();
    if (!payee || amt <= 0) { finToast('收款人與金額必填', 'error'); return; }
    const p = _detail.project;
    btn.disabled = true;
    try {
        // 委外項目＝專案外包請款單：後端會把本案的委外費用 += 金額（增量制），
        // 同時進應付帳款的匯款清單 —— 一筆資料，三個地方同一個真相
        await crmFetch('/payments', {
            method: 'POST',
            body: JSON.stringify({
                entity: 'mine', category: '專案外包', project_id: _sel,
                payee_name: payee, amount: amt,
                summary: `委外：${payee}｜${p.name}${note ? '（' + note + '）' : ''}`,
                request_date: todayStr(),
            }),
        });
        finToast('已加入委外（應付款）');
        await _fp.refresh();            // 委外費用/未付應付都變了 → 清單＋詳情一起換新
    } catch (e) {
        finToast('委外建立失敗：' + e.message, 'error');
    } finally { btn.disabled = false; }
};

/** 「用這個母帳案名」——把母帳案名填進顯示名欄（連多個母帳案時自動規則
 *  挑不出誰對，點一下就不用自己打）。要按儲存才會存。 */
_fp.useParentName = (i) => {
    const el = document.getElementById('fpl-dispname');
    const n = ((_detail && _detail.project && _detail.project.parent_names) || [])[i];
    if (el && n) { el.value = n; el.focus(); _dirty = true; }
};


_fp.gotoCrm = () => {
    if (!_sel) return;
    sessionStorage.setItem('omgJumpCrmProject', _sel);
    window.switchTab('tab_crm_projects');   // CRM 的 tab-changed handler 接棒
};

_fp.push = async (btn) => {
    // 推送/取消推送到專案管理（owner 2026-08-25：「有一個按鈕可以讓我的私帳
    // 推送到 crm 的專案系統裡，標注後期專案」）。立即生效，不走「儲存」。
    const p = _detail && _detail.project;
    if (!p) return;
    const v = p.crm_pushed ? 0 : 1;
    btn.disabled = true;
    try {
        const r = await finFetch(`/project-ledger/${_sel}`, {
            method: 'PUT', body: JSON.stringify({ crm_pushed: v }),
        });
        p.crm_pushed = r.crm_pushed;
        const row = _data.projects.find((x) => x.id === _sel);
        if (row) {
            row.crm_pushed = r.crm_pushed;
            row.updated_at = r.updated_at || row.updated_at;   // 排序鍵跟著動，見 _sortProjects
        }
        _sortProjects();
        _renderList();
        _renderDetail();
        finToast(v ? '已推送到專案管理（標註「後期專案」）' : '已取消推送');
    } catch (e) {
        finToast('推送失敗：' + e.message, 'error');
        btn.disabled = false;
    }
};

_fp.save = async (btn) => {
    const body = { split: {} };
    (_detail.cost_fields || []).forEach((f) => {
        const el = document.getElementById('fpl-c-' + f.key);
        // 每一格送的都是**這一格裡的數字**：純手填欄就是它的值，CRM 撐著的欄
        // 那格放的是手填那部分（合計是畫在下面的小字，不是輸入值）
        if (el) { body[f.key] = Number(el.value) || 0; }
    });
    document.querySelectorAll('#fpl-detail [id^="fpl-s-"]').forEach((el) => {
        const name = decodeURIComponent(el.id.slice('fpl-s-'.length));
        body.split[name] = Number(el.value) || 0;
    });
    const cEl = document.getElementById('fpl-contract');
    if (cEl) body.contract_amount = Number(cEl.value) || 0;
    const dEl = document.getElementById('fpl-close');
    if (dEl) body.close_date = dEl.value || '';     // 空＝清成未結案
    const sEl = document.getElementById('fpl-source');
    if (sEl) body.source = sEl.value;               // 空＝清掉案源
    const nEl = document.getElementById('fpl-dispname');
    if (nEl) body.display_name = nEl.value.trim();   // 空＝清掉，退回自動規則
    const fEl = document.getElementById('fpl-feepct');
    // 空白＝用後端的預設費率（正本 DEFAULT_FEE_PCT），不在這裡寫死一個 8
    if (fEl && sEl && sEl.value === '代開發票') {
        body.fee_pct = Number(fEl.value) || _defaultFeePct() || undefined;
    }
    btn.disabled = true;
    btn.textContent = '儲存中…';
    try {
        const r = await finFetch(`/project-ledger/${_sel}`, {
            method: 'PUT', body: JSON.stringify(body),
        });
        _dirty = false;
        finToast(r.check ? `已儲存 —— 檢查 ${fmtNum(r.check)}（工項與實收對不上）` : '已儲存');
        // PUT 已經回了這一列重算後的值 —— 就地更新那一列與合計，不重抓 402 列
        _applySaved(r);
    } catch (e) {
        finToast('儲存失敗：' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '儲存';
    }
};


/** 存檔後就地更新一列與合計（PUT 已回傳重算的 detail/net/check）。
 *
 *  原本是 `await _load()`：重跑 402 列查詢 + 兩個聚合、重建整個殼，然後
 *  `_renderShell` 尾端又把詳情重拉一次 —— 一次存檔三個往返。
 */
// 結案日新→舊、未結案（空）排最前、同日再比 updated_at 新→舊。
// 🔴 必須與後端 ORDER BY 逐字對應（completion_date DESC NULLS FIRST,
// updated_at DESC）—— 少了次要鍵，同一天結案的案子就會排在跟重新載入不同的
// 位置，使用者存個檔就看到清單「自己動了一下」（第 4 輪瀏覽器驗收實抓：
// 還原日期後那列停在同日群組的尾端，重整才回到原位）。
function _sortProjects() {
    _data.projects.sort((a, b) => {
        if (!a.close_date !== !b.close_date) return a.close_date ? 1 : -1;
        // ISO 字串直接比大小就好 —— localeCompare 會照地區的排序規則走，
        // 對 '2026-08-24T19:32:37+00:00' vs '...:37.134519+00:00'（isoformat
        // 會省略 .000000）給出跟時間順序相反的答案。
        const ca = a.close_date || '', cb = b.close_date || '';
        if (ca !== cb) return ca < cb ? 1 : -1;
        const ua = a.updated_at || '', ub = b.updated_at || '';
        if (ua !== ub) return ua < ub ? 1 : -1;
        // 同批匯入 updated_at 到微秒都相同 —— id 決勝，與後端 ORDER BY 第三鍵一致
        return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    });
}


function _applySaved(r) {
    const i = (_data.projects || []).findIndex(p => p.id === _sel);
    if (i < 0) { _load(); return; }
    const old = _data.projects[i];
    const next = {
        ...old,
        detail: r.detail, net: r.net, check: r.check,
        // 顯示名由後端重算後回傳（自訂清掉時要退回自動規則 —— 前端自己推等於
        // 把 linked_display_name 抄第二份）
        name: r.name != null ? r.name : old.name,
        orig_name: r.orig_name != null ? r.orig_name : old.orig_name,
        custom_name: r.custom_name != null ? r.custom_name : old.custom_name,
        contract: r.contract != null ? r.contract : old.contract,
        close_date: r.close_date != null ? r.close_date : old.close_date,
        updated_at: r.updated_at || old.updated_at,
    };
    // 合計列由 _renderTotals 從畫面上的列即時算 —— 不再增量維護一份影子合計
    _data.projects[i] = next;
    // 存檔會動到排序的兩個鍵（結案日、updated_at）—— 一律重排，讓畫面上的
    // 順序與「現在重新載入會看到的順序」永遠一致。402 列排一次不值得省。
    _sortProjects();
    if (_detail) _detail.project = { ..._detail.project, ...next };
    _renderTotals();
    _renderList();          // 樣板本身就會把 selected 標在對的那列
    _renderDetail();
}
