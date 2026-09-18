/**
 * info.js — 書頁的「資訊」分頁（owner 2026-09-19：「我希望有個 tab 可以填書的基本資訊」）。
 *
 * 書名、作者、標籤本來就有（標題列與 ⋯ 那邊），但那三個以外的欄位以前沒地方放。
 * 這一頁把一本書的基本資料集中在一起：副書名、原文書名、譯者、出版社、出版年、版次、
 * ISBN、購買或借閱的連結、我為什麼讀這本、讀完的日期。全部選填。
 *
 * 🔴 欄位的**鍵**以後端 `core/knowledge_logic.INFO_FIELDS` 為正本，這裡只負責標籤與排版。
 *    兩邊對不起來的話 tests/unit/test_knowledge_info.py 會紅。
 *
 * 只 import ctx（葉節點）；同 chat.js／extend.js 的規矩。
 */
import { S, nav, hooks, api, esc, errText, alive, toast } from './ctx.js';
import { attachProjectPop } from '../shared/project-pop.js';

/** `[鍵, 顯示的標籤, 提示]`，順序就是畫面上的順序（同後端 INFO_FIELDS 的順序）。 */
export const INFO_FIELDS = [
    ['subtitle', '副書名', ''],
    ['original_title', '原文書名', '譯本的話填原文，之後討論引用得到'],
    ['translator', '譯者', ''],
    ['publisher', '出版社', ''],
    ['published', '出版年', '例如 2022 或 2022-03'],
    ['edition', '版次', '例如 初版、二版'],
    ['isbn', 'ISBN', ''],
    ['link', '連結', '買這本或借這本的網址（http 開頭）'],
    ['why', '我為什麼讀這本', '一兩句就好；討論與研究助理都讀得到'],
    ['read_at', '讀完的日期', '例如 2026-09-19'],
];

const _val = (b, k) => ((b && b.info) || {})[k] || '';

/**
 * 「掛在哪個案子」（owner 2026-09-19：「這裡也要可以連結現有的專案列表」）。
 *
 * 🔴 清單怎麼列與怎麼挑，兩邊都跟工時補登的那一格**同一支** —— owner 同一天說
 *    「列表的列出來的方式，和工作時數填寫的規則相同」：
 *      · 列法：後端 `services/project_picker.list_options`（不篩狀態、母私帳只列一個、
 *        最近有動的排前面、label＝年份 客戶 案名）
 *      · 挑法：`js/shared/project-pop.js` 那個打字浮層（進行中展開／已結案收著）
 *    自己再寫一個下拉的話，兩邊會慢慢長歪。
 */
function _projectRowHtml(b) {
    const proj = (b && b.project) || {};
    if (!S.infoEdit) {
        return `<div class="kb-info-row"><span class="k">案子</span><span class="v">${proj.id
            ? `<a href="/project.html?id=${esc(proj.id)}" target="_blank" rel="noopener">${esc(proj.label || proj.id)}</a>`
            : '<span class="none">還沒掛案子</span>'}</span></div>`;
    }
    return `<label class="kb-info-row edit"><span class="k">案子</span>
        <span class="v"><input type="text" class="kb-input" id="kb-proj" data-proj-pick
            value="${esc(proj.label || '')}" data-pid="${esc(proj.id || '')}" data-pname="${esc(proj.label || '')}"
            placeholder="打幾個字找案子（清空就是不掛）"></span></label>`;
}

/** 浮層每次打開都問一次；抓過就留著（同一頁不會一直打）。DB 不通就是空清單。
 *  🔴 這支**不在知識庫的 API 底下**（知識庫那支 router 不碰 DB）—— 它是專案清單
 *  通用的那一支，備份頁與工時補登用的是同一份規則。 */
async function _projectOptions() {
    if (S.projects) return S.projects;
    try {
        const r = await hooks.fetch('/api/v1/projects/picker');
        // 抓失敗（DB 斷線）不要記起來 —— 記了的話這一頁到關掉為止都只會看到空清單
        if (r && r.projects && r.projects.length) S.projects = r.projects;
        return (r && r.projects) || [];
    } catch (_) {
        return [];
    }
}

function _rowHtml(b, [key, label, hint]) {
    const v = _val(b, key);
    if (!S.infoEdit) {
        if (!v) return '';
        const isLink = key === 'link' && /^https?:\/\//i.test(v);
        return `<div class="kb-info-row"><span class="k">${esc(label)}</span>
            <span class="v">${isLink
        ? `<a href="${esc(v)}" target="_blank" rel="noopener noreferrer">${esc(v)}</a>`
        : esc(v)}</span></div>`;
    }
    return `<label class="kb-info-row edit"><span class="k">${esc(label)}</span>
        <span class="v"><input type="text" class="kb-input" data-info="${esc(key)}"
            value="${esc(v)}" placeholder="${esc(hint)}"></span></label>`;
}

export function infoHtml() {
    const b = S.book || {};
    const rows = INFO_FIELDS.map((f) => _rowHtml(b, f)).filter(Boolean).join('');
    const head = `<div class="kb-info-head">
        <div class="n">${esc(b.title || '')}${b.author ? `　${esc(b.author)}` : ''}</div>
        <div class="b">${S.infoEdit
        ? `<button type="button" class="kb-btn pri" data-kact="info-save">儲存</button>
           <button type="button" class="kb-btn ghost" data-kact="info-cancel">取消</button>`
        : '<button type="button" class="kb-btn" data-kact="info-edit">編輯</button>'}</div>
    </div>`;
    const facts = [
        b.pages ? `${b.pages} 頁` : '',
        b.chapters ? `${b.chapters} 章` : '',
        b.uploaded_at ? `加入於 ${esc(String(b.uploaded_at).slice(0, 10))}` : '',
    ].filter(Boolean).join('　');
    return `${head}
        ${_projectRowHtml(b)}
        ${rows || (S.infoEdit ? '' : '<div class="kb-empty">還沒填。按「編輯」把這本書的出版資訊記下來。</div>')}
        ${facts ? `<div class="note">${facts}（自動帶的，改不了）</div>` : ''}
        ${_shareHtml()}`;
}

/** 公開分享（§9.9）。owner 2026-09-19：「可以有一個公開分享的連結，讓我把這本書的研究分享出去
 *  （但是點不到我其他的地方）」。開關放這裡 —— 這一頁本來就是這本書的後設資料。 */
/** 可以勾的那幾項。鍵以後端 `knowledge_logic.SHARE_PARTS` 為正本（測試會比對）。 */
export const SHARE_PARTS = [
    ['info', '書的基本資訊', '出版社、ISBN 那些，你填的'],
    ['tags', '標籤', ''],
    ['extend', '延伸研究', '網路上的公開資料，每則附出處與連結'],
    ['conclusion', '我的結論', '你自己寫的原則'],
    ['notes', '我的筆記', '你自己寫的'],
    ['skill', '骨架與速查表', '原書的內容整理，公開要注意版權'],
    ['chapters', '章節重點', '原書的內容整理，公開要注意版權'],
    ['gallery', '書裡的圖', '原書的圖，公開要注意版權'],
];

function _shareHtml() {
    const sh = S.share;
    if (!sh) return '';
    const on = new Set(sh.parts || []);
    return `<div class="kb-share">
        <label class="kb-watch"><input type="checkbox" data-kact="share-toggle"${sh.on ? ' checked' : ''}>
            開一個公開連結，把這本書分享出去</label>
        <div class="note">書名與作者一定會出去（那是在講哪一本書）。其餘自己勾。
            收到連結的人連不回這個系統的其他地方，討論內容與書的全文任何情況都不會出去。</div>
        ${sh.on ? `
        <div class="kb-share-parts">
            ${SHARE_PARTS.map(([k, label, why]) => `<label>
                <input type="checkbox" data-kact="share-part" data-k="${esc(k)}"${on.has(k) ? ' checked' : ''}>
                <span class="l">${esc(label)}</span>${why ? `<span class="w">${esc(why)}</span>` : ''}
            </label>`).join('')}
        </div>
        <div class="kb-share-link">
            <input type="text" class="kb-input" id="kb-share-url" value="${esc(sh.url)}" readonly>
            <button type="button" class="kb-btn" data-kact="share-copy">複製</button>
        </div>
        <div class="note">改勾選不會換網址；關掉再開才會換一組新的，舊的立刻失效。</div>` : ''}
    </div>`;
}

/** 勾／取消一項要分享的內容。 */
export async function togglePart(key, on) {
    if (!S.book || !S.share) return;
    const bookId = S.book.id;
    const before = S.share.parts || [];
    const next = on ? [...new Set([...before, key])] : before.filter((k) => k !== key);
    S.share = { ...S.share, parts: next };
    editInfo(S.infoEdit);
    try {
        S.share = await api(`/${encodeURIComponent(bookId)}/share`, { method: 'PUT', body: { on: true, parts: next } });
        if (!alive() || !S.book || S.book.id !== bookId) return;
        editInfo(S.infoEdit);
    } catch (e) {
        if (!alive()) return;
        S.share = { ...S.share, parts: before };
        editInfo(S.infoEdit);
        toast('改不動：' + errText(e), true);
    }
}

export async function loadShare() {
    if (!S.book) return;
    const bookId = S.book.id;
    try {
        const sh = await api(`/${encodeURIComponent(bookId)}/share`);
        if (!alive() || !S.book || S.book.id !== bookId) return;
        S.share = sh || null;
        if (S.pane === 'info') editInfo(S.infoEdit);
    } catch (_) { /* 讀不到就不畫那一段 */ }
}

export async function toggleShare(on) {
    if (!S.book) return;
    const bookId = S.book.id;
    const before = S.share;
    S.share = { ...(S.share || {}), on: !!on };
    editInfo(S.infoEdit);
    try {
        S.share = await api(`/${encodeURIComponent(bookId)}/share`,
            { method: 'PUT', body: { on: !!on, parts: (S.share && S.share.parts) || null } });
        if (!alive() || !S.book || S.book.id !== bookId) return;
        editInfo(S.infoEdit);
        toast(on ? '公開連結開好了' : '已關掉，舊連結失效');
    } catch (e) {
        if (!alive()) return;
        S.share = before;
        editInfo(S.infoEdit);
        toast('改不動：' + errText(e), true);
    }
}

export function copyShare() {
    const el = S.root && S.root.querySelector('#kb-share-url');
    if (!el) return;
    el.select();
    navigator.clipboard?.writeText(el.value).then(() => toast('複製好了'), () => toast('複製不了，請手動選取', true));
}

export function editInfo(on) {
    S.infoEdit = !!on;
    const pane = S.root && S.root.querySelector('#kb-pane');
    if (pane) pane.innerHTML = infoHtml();
    // 委派掛在 S.root 上，重畫不用重掛（attachProjectPop 自己記得掛過了）
    if (S.root) attachProjectPop(S.root, { options: _projectOptions });
}

export async function saveInfo() {
    if (!S.book) return;
    const bookId = S.book.id;
    const info = {};
    for (const el of S.root.querySelectorAll('#kb-pane input[data-info]')) {
        const v = el.value.trim();
        if (v) info[el.dataset.info] = v;
    }
    // 案子：`data-pid` 是浮層選到的那一筆；把字清掉（或手打改過）就是不掛案子
    const el = S.root.querySelector('#kb-proj');
    const pid = el && el.value.trim() ? (el.dataset.pid || '') : '';
    const project = pid ? { id: pid, label: el.value.trim() } : {};
    const before = S.book.info || {};
    const beforeProj = S.book.project || {};
    S.book.info = info;
    S.book.project = project;
    S.infoEdit = false;
    editInfo(false);
    try {
        const b = await api(`/${encodeURIComponent(bookId)}`, { method: 'PUT', body: { info, project } });
        if (!alive() || !S.book || S.book.id !== bookId) return;
        if (b && b.info) S.book.info = b.info;      // 後端會截長、丟掉不合規的連結
        S.book.project = (b && b.project) || {};
        editInfo(false);
        if (typeof nav.renderBook === 'function') nav.renderBook();   // 表頭那一列的「案子：…」跟著換
        toast('已儲存');
    } catch (e) {
        if (!alive() || !S.book || S.book.id !== bookId) return;
        S.book.info = before;
        S.book.project = beforeProj;
        editInfo(false);
        toast('存不起來：' + errText(e), true);
    }
}
