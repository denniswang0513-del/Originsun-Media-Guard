/**
 * md-lite.js — 最小的 Markdown → HTML（純函式、零 import 的葉節點；知識庫獨立頁 js/knowledge/ 在用）。
 *
 * 只吃知識庫會用到的那幾種：標題（#–######）、粗體、斜體、行內 code、圍欄 code block、
 * 無序／有序清單、表格（| 分隔、第二列 ---）、連結（只准 http／https）、段落與換行。
 *
 * 🔴 **先對整段 esc 再套規則**：進來的字是 claude 回的或 owner 打的，裡面任何 `<script>` 在套規則前
 * 已經變成 `&lt;script&gt;`，之後每一條規則只會產生我們自己寫死的標籤。連結的 href 只放
 * `https?://` 開頭的字串（`javascript:` 這種不會變成 <a>，原樣留著當文字）。
 * 圖片：`![說明](相對路徑)` 會變成 `<img data-md-src="相對路徑">`，**故意不給 src** ——
 * 這支不知道也不該知道怎麼取那個檔（知識庫的圖要帶 token 去拿）。呼叫端自己決定怎麼填
 * （知識庫是 fetch 成 blob 再填）。沒人填就只顯示 alt 文字，永遠不會自己連外。
 * 相對路徑白名單：英數與 `._-/`，不准 `..`、不准開頭斜線、不准協定 —— 外部圖片一律不收。
 *
 * 沒有 HTML 直通、沒有巢狀清單 —— 要更多再加，不要換成第三方套件（外網載不到）。
 */

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/** 行內規則：code 先切開（code 裡不套粗斜體與連結），其餘套連結 → 粗體 → 斜體。輸入已 esc。 */
function inline(s) {
    return s.split(/(`[^`\n]+`)/).map((part) => {
        if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
            return `<code>${part.slice(1, -1)}</code>`;
        }
        return part
            // 圖片要排在連結前面（`![x](y)` 也符合連結的樣子）
            .replace(/!\[([^\]\n]*)\]\(([A-Za-z0-9._-]+(?:\/[A-Za-z0-9._-]+)*)\)/g,
                (m, alt, src) => (src.includes('..')
                    ? m
                    : `<img data-md-src="${src}" alt="${alt}" loading="lazy">`))
            .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
                '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
            .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
            .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, '$1<em>$2</em>')
            .replace(/(^|[^_\w])_([^_\n]+)_(?!\w)/g, '$1<em>$2</em>');
    }).join('');
}

const isTableSep = (line) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
const splitRow = (line) => {
    let t = line.trim();
    if (t.startsWith('|')) t = t.slice(1);
    if (t.endsWith('|')) t = t.slice(0, -1);
    return t.split('|').map((c) => c.trim());
};

/**
 * @param {string} md 原始 markdown（可為空）
 * @returns {string} HTML 片段（沒有外層容器；呼叫端自己包一層並給樣式）
 */
export function mdToHtml(md) {
    const lines = esc(md).replace(/\r\n?/g, '\n').split('\n');
    const out = [];
    let i = 0;
    let para = [];
    const flushPara = () => {
        if (para.length) out.push(`<p>${para.map(inline).join('<br>')}</p>`);
        para = [];
    };
    while (i < lines.length) {
        const line = lines[i];
        // 圍欄 code block：內容原樣（已 esc），不套任何行內規則
        if (/^\s*```/.test(line)) {
            flushPara();
            const buf = [];
            i += 1;
            while (i < lines.length && !/^\s*```/.test(lines[i])) { buf.push(lines[i]); i += 1; }
            i += 1;   // 跳過收尾的 ```（沒收尾就吃到底）
            out.push(`<pre><code>${buf.join('\n')}</code></pre>`);
            continue;
        }
        if (!line.trim()) { flushPara(); i += 1; continue; }
        const h = /^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
        if (h) {
            flushPara();
            out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`);
            i += 1;
            continue;
        }
        // 表格：這一列有 |，下一列是 --- 分隔列
        if (line.includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
            flushPara();
            const head = splitRow(line);
            const rows = [];
            i += 2;
            while (i < lines.length && lines[i].includes('|') && lines[i].trim()) { rows.push(splitRow(lines[i])); i += 1; }
            const th = head.map((c) => `<th>${inline(c)}</th>`).join('');
            const tb = rows.map((r) => `<tr>${head.map((_, k) => `<td>${inline(r[k] ?? '')}</td>`).join('')}</tr>`).join('');
            out.push(`<table><thead><tr>${th}</tr></thead><tbody>${tb}</tbody></table>`);
            continue;
        }
        const ul = /^\s*[-*+]\s+(.*)$/.exec(line);
        const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
        if (ul || ol) {
            flushPara();
            const tag = ul ? 'ul' : 'ol';
            const re = ul ? /^\s*[-*+]\s+(.*)$/ : /^\s*\d+[.)]\s+(.*)$/;
            const items = [];
            while (i < lines.length) {
                const m = re.exec(lines[i]);
                if (!m) break;
                items.push(m[1]);
                i += 1;
                // 接續行（縮排、不是新項目、不是空行）併進上一項
                while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !re.test(lines[i])) {
                    items[items.length - 1] += ' ' + lines[i].trim();
                    i += 1;
                }
            }
            out.push(`<${tag}>${items.map((t) => `<li>${inline(t)}</li>`).join('')}</${tag}>`);
            continue;
        }
        para.push(line.trim());
        i += 1;
    }
    flushPara();
    return out.join('\n');
}
