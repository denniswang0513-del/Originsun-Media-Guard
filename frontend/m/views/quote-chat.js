/**
 * quote-chat.js — 手機版的報價助理（貼客戶訊息／拍截圖 → AI 整理 → 套進這張報價）。
 *
 * 規劃正本 docs/QUOTE_ASSISTANT_PLAN.md。跟桌機（crm-quotes.js 的「和 AI 一起完成」分頁）
 * 走**同一組後端**：POST/GET /quotations/{id}/chat、/price-items/match，
 * patch 套用也是同一支純函式 js/shared/quote-patch.js —— 兩邊不會漂掉。
 *
 * 🔴 **寫入者是這裡**：後端只算 patch、不改項目。套完就整包 PUT 回去。
 * 🔴 **PUT 會無條件覆寫 tax_rate／final_price／payment_stages／terms**
 *    （routers/crm/quotes.update_quotation 那幾行不看 model_fields_set）——
 *    所以送出時一定要把載進來的原值原樣帶回，少帶一個就是靜默清掉它。
 * 🔴 這是**新檔案**：Cloudflare 給 .js 4 小時快取，往 shell.js 加新 export 會讓舊分頁
 *    的 named import 整個炸掉（reference_cloudflare_js_cache）。新檔沒有這個問題。
 */
import { mfetch, toast, esc, money } from '../shell.js';
import { applyQuotePatch } from '/js/shared/quote-patch.js';
import * as _QA from '/js/shared/quote-amounts.js';
import { waitingText } from '/js/shared/quote-wait.js';

// 新 export 一律命名空間拿＋本地退路（同 crm-quotes.js／m/views/quotes.js 的慣例）
const groupQuoteItems = _QA.groupQuoteItems || ((items) => {
    const groups = [], byName = new Map();
    (items || []).forEach(it => {
        const name = String(it.group_name || '').trim();
        let g = byName.get(name);
        if (!g) { g = { name, items: [] }; byName.set(name, g); groups.push(g); }
        const row = { ...it }; delete row.group_name; g.items.push(row);
    });
    return groups;
});
const flattenQuoteGroups = _QA.flattenQuoteGroups
    || ((groups) => (groups || []).flatMap(g => g.items.map(it => ({ ...it, group_name: g.name }))));

const OV_ID = 'qt-chat';
let S = null;          // 這次對話的狀態（關掉就丟）

const flat = () => flattenQuoteGroups(S.groups);
const termsText = () => S.terms.map(t => String(t).trim()).filter(Boolean).join('\n');


// ── 版面 ────────────────────────────────────────────────────

function bubble(m) {
    const who = m.role === 'user' ? '你' : 'AI';
    const at = (() => { const d = new Date(m.at || ''); return isNaN(d) ? '' : d.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', hour12: false }); })();
    const extras = [];
    if ((m.questions || []).length)
        extras.push(`<ol class="qc-q">${m.questions.map(q => `<li>${esc(q)}</li>`).join('')}</ol>`);
    if ((m.needs_price || []).length)
        extras.push(`<div class="qc-np">待定價：${m.needs_price.map(esc).join('、')}</div>`);
    if (m.applied)
        extras.push(`<div class="qc-ap">已套進這張報價：新增 ${m.applied.added}、修改 ${m.applied.updated}、刪除 ${m.applied.removed}、備註 +${m.applied.terms}</div>`);
    // 截圖 token 在手機上不渲染縮圖（圖床網址要另外拿），換成看得懂的字
    const text = String(m.text || '').replace(/!?\[[^\]]*\]\(paste:[0-9a-f]{32}\.webp\)|paste:[0-9a-f]{32}\.webp/g, '（截圖）');
    return `<div class="qc-msg ${m.role === 'user' ? 'me' : 'ai'}">
        <div class="qc-who">${who}${at ? ' · ' + esc(at) : ''}</div>
        <div class="qc-body">${esc(text)}</div>${extras.join('')}</div>`;
}

function summaryLine() {
    const rows = flat().filter(it => it.description);
    const noPrice = rows.filter(it => !it.unit_price).length;
    const total = rows.reduce((s, it) => s + (it.quantity || 0) * (it.unit_price || 0), 0);
    return `${rows.length} 項 · 小計 ${money(total)}${noPrice ? ` · <b class="qc-warn">${noPrice} 項待定價</b>` : ''}`;
}

function render() {
    const ov = document.getElementById(OV_ID);
    if (!ov) return;
    const log = S.chat.length
        ? S.chat.map(bubble).join('') + (S.busy
            ? `<div class="qc-msg ai"><div class="qc-who">AI</div><div class="qc-body">${
                S.partial ? esc(S.partial) + '<span class="qc-caret"></span>'
                    : `<span id="qc-tick">${esc(waitingText(Date.now() - S.since, S.stage))}</span>`
              }</div></div>` : '')
        : `<div class="qc-empty">把客戶的訊息整段貼進下面，或拍／選一張對話截圖。<br>
             AI 會整理成項目，不確定的地方會問你。</div>`;
    const rows = flat().filter(it => it.description);
    const noPrice = rows.filter(it => !it.unit_price).length;

    ov.querySelector('.qc-log').innerHTML = log;
    ov.querySelector('.qc-log').scrollTop = ov.querySelector('.qc-log').scrollHeight;
    ov.querySelector('.qc-sum').innerHTML = summaryLine();
    ov.querySelector('#qc-fill').hidden = !noPrice;
    ov.querySelector('#qc-send').disabled = S.busy;
    ov.querySelector('#qc-text').disabled = S.busy;
    ov.querySelector('#qc-shot').disabled = S.busy;
}

function shell(title) {
    let ov = document.getElementById(OV_ID);
    if (ov) ov.remove();
    ov = document.createElement('div');
    ov.id = OV_ID;
    ov.innerHTML = `
      <div class="qc-top">
        <div class="qc-title">${esc(title)}</div>
        <button type="button" class="m-btn sm" id="qc-close">關閉</button>
      </div>
      <div class="qc-log"></div>
      <div class="qc-foot">
        <div class="qc-sumrow"><span class="qc-sum"></span>
          <button type="button" class="m-btn sm" id="qc-fill" hidden>用價目補上</button></div>
        <textarea id="qc-text" rows="2" placeholder="貼上客戶的訊息，或回答上面的問題…"></textarea>
        <div class="qc-actions">
          <button type="button" class="m-btn sm" id="qc-shot">截圖</button>
          <button type="button" class="m-btn-primary sm" id="qc-send">送出</button>
        </div>
        <input type="file" id="qc-file" hidden>
        <div class="qc-hint">AI 只會改這張報價，不會寄出。單價它沒把握就會問你。</div>
      </div>`;
    // 🔴 accept 在這裡設，不寫進上面的樣板字串：`image` 後面接斜線星號會被
    //    tests/unit/_srcscan.js_code_only 當成區塊註解的開頭，從那裡到下一個「星號斜線」
    //    之間的程式碼會被整段吃掉（掃原始碼的測試就看不到那些函式了；CLAUDE.md 有記這個坑）。
    ov.querySelector('#qc-file').accept = 'image/' + '*';
    document.body.appendChild(ov);
    return ov;
}


// ── 存回去（單一寫入者是這裡）────────────────────────────────

async function save() {
    // 🔴 tax_rate／final_price／payment_stages／terms 後端是無條件覆寫的，原值一定要帶回
    await mfetch(`/api/v1/crm/quotations/${encodeURIComponent(S.q.id)}`, {
        method: 'PUT',
        body: {
            tax_rate: S.q.tax_rate ?? 5,
            final_price: S.q.final_price ?? null,
            payment_stages: S.q.payment_stages || [],
            terms: termsText(),
            items: flat().filter(it => it.description).map(it => ({
                group_name: it.group_name || '', description: it.description,
                unit: it.unit || '式', quantity: it.quantity || 1,
                unit_price: it.unit_price || 0, internal_cost: it.internal_cost || 0,
                note: it.note || '',
            })),
        },
    });
}

/** 把還沒套過的 AI patch 套進這張報價並存回去。同一則只套一次。 */
async function applyNew() {
    let touched = false;
    for (let i = S.applied; i < S.chat.length; i++) {
        const m = S.chat[i];
        if (m.role !== 'ai' || !m.patch) continue;
        const r = applyQuotePatch(S.groups, S.terms, { ...m.patch, terms_add: m.terms_add || [] });
        S.groups = r.groups; S.terms = r.terms; m.applied = r.applied;
        touched = true;
    }
    S.applied = S.chat.length;
    if (!touched) return;
    render();
    try { await save(); } catch (e) { toast('存回報價失敗：' + e.message, 'err'); }
}


// ── 送出與輪詢 ──────────────────────────────────────────────

async function poll(expect, gen) {
    const t0 = Date.now();
    // 後端一輪最久 _CLAUDE_TIMEOUT_SEC=180 秒，前面還可能排隊（閘門只有 2）——
    // 這裡跟 180 秒一樣長的話，一排隊就一定先在前端喊逾時，而那則回覆其實還在路上
    while (S && S.busy && gen === S.gen && Date.now() - t0 < 400000) {
        await new Promise(r => setTimeout(r, 1000));
        if (!S || gen !== S.gen) return;
        let d;
        try { d = await mfetch(`/api/v1/crm/quotations/${encodeURIComponent(S.q.id)}/chat`); }
        catch (_) { continue; }
        if (!S || gen !== S.gen) return;         // 這一趟 await 中間使用者把視窗關了（S 變 null）
        if ((d.chat || []).length >= expect) {
            S.chat = d.chat || []; S.busy = false; S.partial = '';
            await applyNew();
            render();
            return;
        }
        if (typeof d.stage === 'string') S.stage = d.stage;
        if (typeof d.partial === 'string' && d.partial !== S.partial) {
            S.partial = d.partial;           // 串流：邊產邊長出來
            render();
        } else {
            // 只換那一小段字，不整包重繪（重繪會把捲軸拉回底、也會閃）
            const tick = document.getElementById('qc-tick');
            if (tick) tick.textContent = waitingText(Date.now() - S.since, S.stage);
        }
    }
    if (S && S.busy && gen === S.gen) {
        S.busy = false; S.partial = '';
        render();
        toast('AI 沒有在時間內回覆（可能在排隊或沒登入）', 'err');
    }
}

async function send() {
    const ta = document.getElementById('qc-text');
    const text = (ta.value || '').trim();
    if (!text || S.busy) return;
    // 🔴 AI 讀的是 DB 那一份，patch 的編號＝**攤平後**的順序（js/shared/quote-patch.js）。
    //    groupQuoteItems 會把同名大項目併在一起 —— 舊資料的列在 DB 裡不見得是連著的，
    //    不先存回去的話「改第 3 項」會落在別人身上。桌機是靠送出前 flush 自動存做同一件事。
    try { await save(); } catch (e) { toast('存回報價失敗：' + e.message, 'err'); return; }
    if (!S) return;                              // await 中間視窗被關掉
    ta.value = '';
    S.busy = true; S.partial = ''; S.stage = ''; S.since = Date.now();
    render();
    const gen = S.gen;
    try {
        const r = await mfetch(`/api/v1/crm/quotations/${encodeURIComponent(S.q.id)}/chat`,
                               { method: 'POST', body: { text } });
        if (!S || gen !== S.gen) return;         // await 中間視窗被關掉
        S.chat = r.chat || S.chat;
        S.applied = S.chat.length;           // 使用者那則沒有 patch
        render();
        await poll(S.chat.length + 1, gen);
    } catch (e) {
        if (S && gen === S.gen) { S.busy = false; render(); }   // 關掉視窗時 S 已經是 null
        toast(e.message, 'err');
    }
}

/** 拍照／選圖 → 上傳圖床 → 把 token 塞進輸入框（AI 讀得到本機的高解析度那份） */
async function attach(file) {
    if (!file) return;
    const ta = document.getElementById('qc-text');
    const mark = '（圖片上傳中…）';
    ta.value = (ta.value ? ta.value + '\n' : '') + mark;
    try {
        // multipart 不能走 mfetch（它會把 body JSON.stringify）——同 js/shared/paste-image.js 的理由
        const fd = new FormData();
        fd.append('file', file, file.name || 'shot.png');
        fd.append('hires', '1');              // 長截圖縮到 1600 會糊，另存一份 4000 的給 AI 讀
        const tok = localStorage.getItem('auth_token') || '';
        const res = await fetch('/api/v1/paste_upload',
                                { method: 'POST', headers: { Authorization: 'Bearer ' + tok }, body: fd });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
        const d = await res.json();
        ta.value = ta.value.replace(mark, d.token);
    } catch (e) {
        ta.value = ta.value.replace(mark, '');
        toast('圖片上傳失敗：' + e.message, 'err');
    }
}

/** 用價目補上目前是 0 的項目。**不覆蓋你手打的價** */
async function fillPrices() {
    const rows = flat().filter(it => it.description);
    if (!rows.length) return;
    try {
        const r = await mfetch('/api/v1/crm/price-items/match', {
            method: 'POST',
            body: { items: rows.map(it => ({ description: it.description, unit: it.unit || '式' })) },
        });
        const update = (r.matches || [])
            .filter(m => !(rows[m.n - 1] || {}).unit_price)
            .map(m => ({ n: m.n, unit_price: m.unit_price }));
        if (!S) return;                          // await 中間視窗被關掉
        if (!update.length) { toast('價目裡沒有對得上、又還缺價的品項', 'err'); return; }
        const out = applyQuotePatch(S.groups, S.terms, { add: [], update, remove: [] });
        S.groups = out.groups; S.terms = out.terms;
        render();
        await save();
        toast(`已用價目補上 ${out.applied.updated} 項`, 'ok');
    } catch (e) { toast(e.message, 'err'); }
}


// ── 入口 ────────────────────────────────────────────────────

/**
 * @param {string} quotationId 要聊的那張報價
 * @param {Function} [onClose] 關掉時回呼（報價頁用它重新載入清單）
 */
export async function openQuoteChat(quotationId, onClose) {
    let q, chat = [];
    try {
        q = await mfetch(`/api/v1/crm/quotations/${encodeURIComponent(quotationId)}`);
        chat = (await mfetch(`/api/v1/crm/quotations/${encodeURIComponent(quotationId)}/chat`)).chat || [];
    } catch (e) { toast(e.message, 'err'); return; }

    S = {
        q, chat,
        applied: chat.length,          // 歷史的 patch 早就在資料裡了，不再套一次
        groups: groupQuoteItems(q.items || []),
        terms: String(q.terms || '').split('\n').map(t => t.trim()).filter(Boolean),
        busy: false, partial: '', stage: '', since: 0, gen: (S ? S.gen : 0) + 1,
    };

    const ov = shell(`${q.project_name || '（未連專案）'} v${q.version}`);
    ov.querySelector('#qc-close').onclick = () => {
        S = null;                       // 還在跑的輪詢看到 S 沒了就收工
        ov.remove();
        if (onClose) onClose();
    };
    ov.querySelector('#qc-send').onclick = send;
    ov.querySelector('#qc-fill').onclick = fillPrices;
    ov.querySelector('#qc-shot').onclick = () => ov.querySelector('#qc-file').click();
    ov.querySelector('#qc-file').onchange = (e) => attach(e.target.files && e.target.files[0]);
    render();
}
