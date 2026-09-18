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
import { S, api, esc, errText, alive, toast } from './ctx.js';

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
        ${rows || (S.infoEdit ? '' : '<div class="kb-empty">還沒填。按「編輯」把這本書的出版資訊記下來。</div>')}
        ${facts ? `<div class="note">${facts}（自動帶的，改不了）</div>` : ''}`;
}

export function editInfo(on) {
    S.infoEdit = !!on;
    const pane = S.root && S.root.querySelector('#kb-pane');
    if (pane) pane.innerHTML = infoHtml();
}

export async function saveInfo() {
    if (!S.book) return;
    const bookId = S.book.id;
    const info = {};
    for (const el of S.root.querySelectorAll('#kb-pane input[data-info]')) {
        const v = el.value.trim();
        if (v) info[el.dataset.info] = v;
    }
    const before = S.book.info || {};
    S.book.info = info;
    S.infoEdit = false;
    editInfo(false);
    try {
        const b = await api(`/${encodeURIComponent(bookId)}`, { method: 'PUT', body: { info } });
        if (!alive() || !S.book || S.book.id !== bookId) return;
        if (b && b.info) S.book.info = b.info;      // 後端會截長、丟掉不合規的連結
        editInfo(false);
        toast('已儲存');
    } catch (e) {
        if (!alive() || !S.book || S.book.id !== bookId) return;
        S.book.info = before;
        editInfo(false);
        toast('存不起來：' + errText(e), true);
    }
}
