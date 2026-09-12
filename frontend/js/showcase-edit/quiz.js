// ────────────────────────────────────────────────────────────────────────────
// /showcase-edit.html 作品編輯器 —— 分類小測驗（_quiz* 家族）
//
// ⚠ 這是「傳統 script」不是 module：showcase-edit.html 底部依序 <script src> 載入 js/showcase-edit/*.js，
//   六支共用同一個全域詞法環境，頂層的 const／let／function 跨檔直接看得見（原本是同一段 inline script，
//   2026-09-12 原樣切開、零改寫；同 my.html → js/my/ 的拆法）。載入順序＝原本由上而下的執行順序，不可調換。
// ────────────────────────────────────────────────────────────────────────────
// ── 分類小測驗 ───────────────────────────────────────────────
// 心理測驗式固定問答：答 4 題 → 建議「作品分類+作品標籤」→ 按套用才覆蓋勾選。
// 對映靠 slug（website_categories.slug，改中文名不影響）；slug 不在候選清單
// （後台刪了/改了）→ 結論頁註明略過。之後加新分類/標籤 = 在這裡補一個選項即可。
const CAT_QUIZ = [
    {
        multi: true, required: true, title: '這支片是什麼？（可複選）',
        hint: '選所有符合的 — 這題決定「作品分類」，至少選一個',
        options: [
            { label: '幫品牌／客戶宣傳形象、產品或服務', cats: ['commercial'], why: '為品牌／客戶宣傳' },
            { label: '有訪談或人物故事的紀實敘事', cats: ['documentary'], why: '有訪談／人物故事' },
            { label: '記錄一場活動的過程與亮點（典禮／展演／賽事）', cats: ['event'], why: '活動過程紀實' },
            { label: '動畫／動態設計就是這支影片的重點', cats: ['animation'], why: '以動畫／動態為主體' },
        ],
    },
    {
        multi: false, title: '委託方是什麼性質的單位？',
        options: [
            { label: '一般企業／品牌', tags: [], why: '' },
            { label: '政府機關／公部門', tags: ['government'], why: '政府委託' },
            { label: '公益團體／NGO', tags: ['public-good'], why: '公益／NGO 委託' },
            { label: '藝術家／創作者個人', tags: ['artist'], why: '藝術家委託' },
        ],
    },
    {
        multi: true, title: '影片內容有出現這些嗎？（可複選）',
        hint: '都沒有就直接按「下一題」',
        options: [
            { label: '產品亮相或特寫', tags: ['product'], why: '有產品亮相' },
            { label: '名人／公眾人物', tags: ['celebrity'], why: '有名人出鏡' },
            { label: '藝術家或創作過程', tags: ['artist'], why: '有藝術家／創作內容' },
            { label: '展覽現場', tags: ['exhibition'], why: '有展覽現場' },
            { label: '週年慶／里程碑', tags: ['anniversary'], why: '週年／里程碑主題' },
        ],
    },
    {
        multi: false, title: '這支是系列作品之一嗎？',
        hint: '同客戶多支、分集、年度系列都算',
        options: [
            { label: '是，系列作之一', tags: ['series'], why: '系列作品' },
            { label: '否，單支作品', tags: [], why: '' },
        ],
    },
];

let _quiz = null;   // {step, answers: [Set<optionIdx>, ...]}；null=未開

function _quizOpen() {
    _quiz = { step: 0, answers: CAT_QUIZ.map(() => new Set()) };
    const m = document.getElementById('cat-quiz-modal');
    if (EMBED) {
        // embed 的 iframe 高度=內容高度，置中會跑到父視窗看不到的地方 → 貼著按鈕出現
        const r = document.getElementById('btn-cat-quiz')?.getBoundingClientRect();
        m.style.alignItems = 'flex-start';
        m.style.paddingTop = Math.max(16, (r ? r.top : 120) - 60) + 'px';
    }
    m.classList.add('show');
    _quizRender();
}

function _quizClose() {
    _quiz = null;
    document.getElementById('cat-quiz-modal').classList.remove('show');
}

/** 答案 → 建議清單 [{slug, kind, why}]（slug 去重、保留第一個理由） */
function _quizProposal() {
    const seen = new Map();
    _quiz.answers.forEach((sel, qi) => {
        sel.forEach(oi => {
            const o = CAT_QUIZ[qi].options[oi];
            (o.cats || []).forEach(s => { if (!seen.has(s)) seen.set(s, { slug: s, kind: 'category', why: o.why }); });
            (o.tags || []).forEach(s => { if (!seen.has(s)) seen.set(s, { slug: s, kind: 'tag', why: o.why }); });
        });
    });
    return [...seen.values()];
}

function _quizRender() {
    const body = document.getElementById('cat-quiz-body');
    const step = _quiz.step;
    const dots = CAT_QUIZ.map((_, i) =>
        `<span class="quiz-dot${i <= step ? ' on' : ''}"></span>`).join(' ');

    if (step >= CAT_QUIZ.length) { _quizRenderResult(body); return; }

    const q = CAT_QUIZ[step];
    const sel = _quiz.answers[step];
    body.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
            <div style="display:flex;gap:6px;">${dots}</div>
            <button class="btn" type="button" data-q="close" style="font-size:12px;">✕</button>
        </div>
        <h3 style="margin:8px 0 4px;color:var(--text);font-weight:600;">${q.title}</h3>
        ${q.hint ? `<p style="color:var(--text3);font-size:12px;margin:0 0 14px;">${q.hint}</p>` : '<div style="height:14px;"></div>'}
        ${q.options.map((o, i) =>
            `<button class="quiz-opt${sel.has(i) ? ' on' : ''}" type="button" data-q="opt" data-i="${i}">${sel.has(i) ? '✓ ' : ''}${o.label}</button>`).join('')}
        <div style="display:flex;justify-content:space-between;margin-top:14px;">
            <button class="btn" type="button" data-q="back" ${step === 0 ? 'style="visibility:hidden;"' : ''}>← 上一題</button>
            ${q.multi ? '<button class="btn btn-primary" type="button" data-q="next">下一題 →</button>' : '<span style="color:var(--text3);font-size:12px;align-self:center;">點選項直接跳下一題</span>'}
        </div>`;
    _quizBindScreen(body, q, sel);
}

function _quizBindScreen(body, q, sel) {
    body.querySelectorAll('[data-q]').forEach(el => {
        el.onclick = () => {
            const act = el.dataset.q;
            if (act === 'close') { _quizClose(); return; }
            if (act === 'back') { _quiz.step--; _quizRender(); return; }
            if (act === 'opt') {
                const i = parseInt(el.dataset.i);
                if (q.multi) {
                    if (sel.has(i)) sel.delete(i); else sel.add(i);
                    _quizRender();
                } else {
                    sel.clear(); sel.add(i);
                    _quiz.step++; _quizRender();
                }
                return;
            }
            if (act === 'next') {
                if (q.required && sel.size === 0) { _toast('這題至少要選一個'); return; }
                _quiz.step++; _quizRender();
            }
        };
    });
}

function _quizRenderResult(body) {
    const proposal = _quizProposal();
    const bySlug = new Map((DATA.categories_available || []).map(c => [c.slug, c]));
    const hits = [], missing = [];
    proposal.forEach(p => {
        const item = bySlug.get(p.slug);
        if (item) hits.push({ ...p, item }); else missing.push(p);
    });
    const row = (kind, label) => {
        const list = hits.filter(h => (h.item.kind || 'category') === kind);
        if (!list.length) return `<div style="margin-bottom:12px;"><span style="color:var(--text3);font-size:12px;">${label}：</span><span style="color:var(--text3);font-size:13px;">（無）</span></div>`;
        return `<div style="margin-bottom:12px;">
            <div style="color:var(--text3);font-size:12px;margin-bottom:6px;">${label}</div>
            ${list.map(h => `<div style="color:var(--text);font-size:14px;margin-bottom:3px;">✓ ${_esc(h.item.name_zh)}
                ${h.why ? `<span style="color:var(--text3);font-size:12px;">— ${h.why}</span>` : ''}</div>`).join('')}
        </div>`;
    };
    // Q4 答「是系列作」→ 順勢問要歸入哪個（可跳過；找不到可當場「+ 建立新系列…」）
    const seriesAvail = DATA.series_available || [];
    const askSeries = proposal.some(p => p.slug === 'series');
    body.innerHTML = `
        <h3 style="margin:0 0 14px;color:var(--text);font-weight:600;">建議結果</h3>
        ${row('category', '作品分類')}
        ${row('tag', '作品標籤')}
        ${missing.length ? `<p style="color:var(--text3);font-size:12px;">（略過已不存在的：${missing.map(p => p.slug).join('、')}）</p>` : ''}
        ${askSeries ? `<div style="margin:0 0 14px;">
            <div style="color:var(--text3);font-size:12px;margin-bottom:4px;">順便歸入系列？（決定作品牆上跟誰摺疊在一起，可跳過；找不到就選「+ 建立新系列…」）</div>
            <select id="cat-quiz-series" class="sc-select" style="min-width:220px;">
                <option value="">— 先不歸入 —</option>
                ${seriesAvail.map(s => `<option value="${s.id}">${_esc(s.title_zh)}${s.visible === false ? '（隱藏中）' : ''}</option>`).join('')}
                <option value="__new__">+ 建立新系列…</option>
            </select></div>` : ''}
        <p style="color:var(--text3);font-size:12px;margin:10px 0 16px;">按「套用」會以這個結果<b>覆蓋</b>目前的分類與標籤勾選，之後仍可手動微調；記得按頂部「儲存所有變更」。</p>
        <div style="display:flex;gap:8px;justify-content:flex-end;">
            <button class="btn" type="button" data-q="restart">↺ 重新作答</button>
            <button class="btn" type="button" data-q="close">取消</button>
            <button class="btn btn-primary" type="button" data-q="apply">套用</button>
        </div>`;
    // 「+ 建立新系列…」→ prompt 名稱 → 建立隱藏系列並選取
    const qSer = body.querySelector('#cat-quiz-series');
    qSer?.addEventListener('change', async () => {
        if (qSer.value !== '__new__') return;
        const s = await _quickCreateSeries();
        if (s) _ensureSeriesOption(qSer, s.id);
        else qSer.value = '';
    });
    body.querySelectorAll('[data-q]').forEach(el => {
        el.onclick = () => {
            const act = el.dataset.q;
            if (act === 'close') _quizClose();
            else if (act === 'restart') { _quiz = { step: 0, answers: CAT_QUIZ.map(() => new Set()) }; _quizRender(); }
            else if (act === 'apply') {
                const v = qSer?.value || '';
                _quizApply(hits.map(h => h.item.id), v === '__new__' ? '' : v);
            }
        };
    });
}

function _quizApply(ids, seriesId = '') {
    if (seriesId) {   // 測驗選了系列 → 寫進系列下拉（data-field，master save 一起送）
        // 剛在測驗裡新建的系列不在第 5 區下拉的初始選項裡 → 補上再選取
        _ensureSeriesOption(document.getElementById('inp-series-id'), seriesId);
    }
    DATA.selected_category_ids = ids;
    _catsDraft = [...ids];
    _markDirtyEl('data:cats');
    const idSet = new Set(ids);
    document.querySelectorAll('[data-action="toggle-cat"]').forEach(el =>
        _setChipState(el, idSet.has(parseInt(el.dataset.id))));
    window._scSetDirty?.('_cats');   // setDirty 是 _bindEvents 閉包，經 window 出口
    _quizClose();
    _toast('已套用 — 記得按「儲存所有變更」');
}
