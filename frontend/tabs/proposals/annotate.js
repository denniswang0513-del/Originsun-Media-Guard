/**
 * annotate.js — 截圖標示（docs/REFERENCE_LIBRARY.md 階段 2）
 * ---
 * 圖上疊一層 SVG，標示存「圖形清單」不是燒進圖：隨時可改、可刪單一標示、原圖不失真。
 * 座標一律 0~1 相對值 → 圖片縮放/換螢幕都不跑位（後端 _norm_annotations 也照這個契約驗）。
 *
 * 形狀：{type:'rect'|'arrow'|'text'|'pen', x, y, w, h, x2, y2, points:[[x,y]…], text, color, width}
 *
 * 匯出：
 *   renderShapes(svg, annotations)              // 唯讀渲染（縮圖牆/lightbox 共用）
 *   openAnnotator({imageUrl, annotations, readonly, onSave})   // 全螢幕編輯器
 *   flattenToBlob(imageUrl, annotations)        // 圖+標示壓平成 WebP（工具列「下載」用）
 */

import { ensureStyle } from '../../js/shared/utils.js';

const NS = 'http://www.w3.org/2000/svg';
const DEFAULT_COLOR = '#e05252';
const COLORS = [DEFAULT_COLOR, '#f5a524', '#3b82f6', '#22c55e', '#ffffff', '#111111'];
// 箭頭端點 marker 的 id（defs 與繪製兩處共用一份組法 —— 分岔過一次：
// shape 沒帶 color 時 defs 產 'ah-'、line 指 'ah-e05252' → 箭頭不見）
const arrowId = (color) => 'ah-' + String(color || DEFAULT_COLOR).replace(/[^a-z0-9]/gi, '');
const STYLE_ID = 'anno-style';

const CSS = `
.anno-ov { position: fixed; inset: 0; z-index: 9000; background: rgba(10,10,10,.94);
  display: flex; flex-direction: column; }
.anno-bar { display: flex; gap: 6px; align-items: center; flex-wrap: wrap;
  padding: 10px 14px; border-bottom: 1px solid #2a2a2a; color: #e8e8e8; font-size: 12px; }
.anno-bar .grow { flex: 1; }
.anno-bar button { background: #1f1f1f; border: 1px solid #333; border-radius: 2px; color: #e8e8e8;
  font-size: 12px; padding: 5px 10px; cursor: pointer; font-family: inherit; }
.anno-bar button:hover { border-color: #3b82f6; }
.anno-bar button.on { border-color: #3b82f6; color: #3b82f6; }
.anno-bar button.primary { background: #1f538d; border-color: #1f538d; }
.anno-bar button.primary:hover { background: #2563eb; }
.anno-sw { width: 18px; height: 18px; border-radius: 50%; border: 2px solid #333; cursor: pointer; padding: 0; }
.anno-sw.on { border-color: #fff; }
.anno-stage { flex: 1; display: flex; align-items: center; justify-content: center;
  padding: 14px; overflow: auto; }
.anno-wrap { position: relative; line-height: 0; max-width: 100%; max-height: 100%; }
.anno-wrap img { display: block; max-width: 100%; max-height: calc(100vh - 120px); }
.anno-wrap svg { position: absolute; inset: 0; width: 100%; height: 100%; }
.anno-wrap.edit svg { cursor: crosshair; }
.anno-hint { color: #8b8b8b; font-size: 11.5px; padding: 0 14px 10px; }
`;

const el = (name, attrs) => {
    const n = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
    return n;
};

/** 唯讀渲染：把 shapes 畫進既有的 <svg>（viewBox 0 0 100 100，靠比例縮放）。 */
export function renderShapes(svg, annotations) {
    svg.setAttribute('viewBox', '0 0 100 100');
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.innerHTML = '';
    const shapes = (annotations && annotations.shapes) || [];
    // 箭頭端點用一個 marker（每個顏色一份，id 帶顏色去重）
    const defs = el('defs', {});
    const seen = new Set();
    shapes.filter(s => s.type === 'arrow').forEach(s => {
        const c = s.color || DEFAULT_COLOR;
        const id = arrowId(c);
        if (seen.has(id)) return;
        seen.add(id);
        const m = el('marker', { id, viewBox: '0 0 10 10', refX: '8', refY: '5',
            markerWidth: '5', markerHeight: '5', orient: 'auto-start-reverse' });
        m.appendChild(el('path', { d: 'M 0 0 L 10 5 L 0 10 z', fill: c }));
        defs.appendChild(m);
    });
    svg.appendChild(defs);

    shapes.forEach(s => {
        const color = s.color || DEFAULT_COLOR;
        const w = s.width || 3;
        if (s.type === 'rect') {
            const x = Math.min(s.x, s.x + s.w) * 100, y = Math.min(s.y, s.y + s.h) * 100;
            svg.appendChild(el('rect', { x, y, width: Math.abs(s.w) * 100, height: Math.abs(s.h) * 100,
                fill: 'none', stroke: color, 'stroke-width': w * 0.25, 'vector-effect': 'non-scaling-stroke' }));
        } else if (s.type === 'arrow') {
            svg.appendChild(el('line', { x1: s.x * 100, y1: s.y * 100, x2: s.x2 * 100, y2: s.y2 * 100,
                stroke: color, 'stroke-width': w * 0.25, 'marker-end': `url(#${arrowId(color)})`,
                'vector-effect': 'non-scaling-stroke' }));
        } else if (s.type === 'pen') {
            const pts = (s.points || []).map(p => `${p[0] * 100},${p[1] * 100}`).join(' ');
            if (pts) svg.appendChild(el('polyline', { points: pts, fill: 'none', stroke: color,
                'stroke-width': w * 0.25, 'stroke-linejoin': 'round', 'stroke-linecap': 'round',
                'vector-effect': 'non-scaling-stroke' }));
        } else if (s.type === 'text') {
            const t = el('text', { x: s.x * 100, y: s.y * 100, fill: color,
                'font-size': Math.max(2, w * 1.2), 'font-family': 'inherit',
                'paint-order': 'stroke', stroke: 'rgba(0,0,0,.55)', 'stroke-width': 0.5 });
            t.textContent = s.text || '';
            svg.appendChild(t);
        }
    });
}

/**
 * 全螢幕標示編輯器。onSave(annotations) 由呼叫端負責寫後端；
 * readonly=true 只看不編（公開唯讀情境）。回 Promise（關閉時 resolve）。
 */
export function openAnnotator({ imageUrl, annotations, readonly = false, onSave, title = '' }) {
    ensureStyle(STYLE_ID, CSS);
    return new Promise((resolve) => {
        let shapes = JSON.parse(JSON.stringify((annotations && annotations.shapes) || []));
        let tool = 'rect';
        let color = DEFAULT_COLOR;
        let width = 3;
        let dirty = false;

        const ov = document.createElement('div');
        ov.className = 'anno-ov';
        ov.innerHTML = `
            <div class="anno-bar">
                ${readonly ? '' : `
                <button data-tool="rect" class="on">▭ 框</button>
                <button data-tool="arrow">↗ 箭頭</button>
                <button data-tool="text">T 文字</button>
                <button data-tool="pen">✎ 手繪</button>
                <span style="width:8px;"></span>
                ${COLORS.map((c, i) => `<button class="anno-sw${i === 0 ? ' on' : ''}" data-color="${c}" style="background:${c}" title="${c}"></button>`).join('')}
                <select data-width title="線寬">
                    <option value="2">細</option><option value="3" selected>中</option><option value="6">粗</option>
                </select>
                <button data-act="undo">↶ 復原</button>
                <button data-act="clear">清空</button>`}
                <span class="grow"></span>
                <span style="color:#8b8b8b;">${(title || '').slice(0, 40)}</span>
                <button data-act="download" title="把標示壓進圖片下載（交付客戶用）">下載</button>
                ${readonly ? '' : '<button data-act="save" class="primary">儲存標示</button>'}
                <button data-act="close">關閉</button>
            </div>
            <div class="anno-stage">
                <div class="anno-wrap${readonly ? '' : ' edit'}">
                    <img alt="截圖">
                    <svg></svg>
                </div>
            </div>
            ${readonly ? '' : '<div class="anno-hint">在圖上拖曳畫框／箭頭／手繪；選「文字」後點一下再輸入。Esc 關閉、Ctrl+Z 復原。</div>'}`;
        document.body.appendChild(ov);
        const img = ov.querySelector('img');
        const svg = ov.querySelector('svg');
        img.src = imageUrl;

        const paint = () => { renderShapes(svg, { v: 1, shapes }); live = null; };
        // 拖曳中只改最後一個節點的屬性（全量 renderShapes 在 200 shapes 下會掉幀，
        // 掉幀又讓人畫更久、點更多 —— 惡性循環）
        let live = null;
        const paintLive = () => {
            const s0 = shapes[shapes.length - 1];
            if (!s0) return;
            if (!live || live.dataset.for !== String(shapes.length)) {
                paint();
                live = svg.lastElementChild;
                if (live) live.dataset.for = String(shapes.length);
                return;
            }
            if (s0.type === 'rect') {
                live.setAttribute('x', Math.min(s0.x, s0.x + s0.w) * 100);
                live.setAttribute('y', Math.min(s0.y, s0.y + s0.h) * 100);
                live.setAttribute('width', Math.abs(s0.w) * 100);
                live.setAttribute('height', Math.abs(s0.h) * 100);
            } else if (s0.type === 'arrow') {
                live.setAttribute('x2', s0.x2 * 100);
                live.setAttribute('y2', s0.y2 * 100);
            } else if (s0.type === 'pen') {
                live.setAttribute('points', s0.points.map(p => `${p[0] * 100},${p[1] * 100}`).join(' '));
            }
        };
        img.addEventListener('load', paint);
        paint();

        // ── 座標換算：滑鼠 → 0~1 相對值 ──
        const rel = (ev) => {
            const r = svg.getBoundingClientRect();
            return [Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)),
                    Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height))];
        };

        if (!readonly) {
            let drawing = null;
            svg.addEventListener('pointerdown', (ev) => {
                const [x, y] = rel(ev);
                if (tool === 'text') {
                    const text = prompt('標示文字');
                    if (text) { shapes.push({ type: 'text', x, y, text, color, width: width * 2 }); dirty = true; paint(); }
                    return;
                }
                svg.setPointerCapture(ev.pointerId);
                drawing = tool === 'pen'
                    ? { type: 'pen', x, y, points: [[x, y]], color, width }
                    : tool === 'rect'
                        ? { type: 'rect', x, y, w: 0, h: 0, color, width }
                        : { type: 'arrow', x, y, x2: x, y2: y, color, width };
                shapes.push(drawing);
            });
            svg.addEventListener('pointermove', (ev) => {
                if (!drawing) return;
                const [x, y] = rel(ev);
                if (drawing.type === 'rect') { drawing.w = x - drawing.x; drawing.h = y - drawing.y; }
                else if (drawing.type === 'arrow') { drawing.x2 = x; drawing.y2 = y; }
                else {
                    // 位移太小就不記點：200 shapes × 400 點的上限很快就被手繪吃光
                    const last = drawing.points[drawing.points.length - 1];
                    if (Math.hypot(x - last[0], y - last[1]) < 0.004) return;
                    drawing.points.push([x, y]);
                }
                paintLive();          // 只更新進行中的那一個節點，不重建整張 SVG
            });
            const finish = () => {
                if (!drawing) return;
                // 點一下沒拖曳 → 不留下無意義的零尺寸圖形
                const tiny = (drawing.type === 'rect' && Math.abs(drawing.w) < 0.01 && Math.abs(drawing.h) < 0.01)
                    || (drawing.type === 'arrow' && Math.abs(drawing.x2 - drawing.x) < 0.01 && Math.abs(drawing.y2 - drawing.y) < 0.01)
                    || (drawing.type === 'pen' && drawing.points.length < 3);
                if (tiny) shapes.pop(); else dirty = true;
                drawing = null;
                paint();
            };
            svg.addEventListener('pointerup', finish);
            svg.addEventListener('pointercancel', finish);

            ov.querySelectorAll('[data-tool]').forEach(b => b.addEventListener('click', () => {
                tool = b.dataset.tool;
                ov.querySelectorAll('[data-tool]').forEach(x => x.classList.toggle('on', x === b));
            }));
            ov.querySelectorAll('[data-color]').forEach(b => b.addEventListener('click', () => {
                color = b.dataset.color;
                ov.querySelectorAll('[data-color]').forEach(x => x.classList.toggle('on', x === b));
            }));
            ov.querySelector('[data-width]').addEventListener('change', (e) => { width = +e.target.value; });
            ov.querySelector('[data-act="undo"]').addEventListener('click', () => {
                if (shapes.pop()) { dirty = true; paint(); }
            });
            ov.querySelector('[data-act="clear"]').addEventListener('click', () => {
                if (shapes.length && confirm('清空這張圖的所有標示？')) { shapes = []; dirty = true; paint(); }
            });
            ov.querySelector('[data-act="save"]').addEventListener('click', async (e) => {
                e.target.textContent = '儲存中…';
                try {
                    await onSave({ v: 1, shapes });
                    dirty = false;
                    close(true);
                } catch (err) {
                    e.target.textContent = '儲存標示';
                    alert('儲存失敗：' + ((err && err.message) || err));
                }
            });
        }

        ov.querySelector('[data-act="download"]').addEventListener('click', async (e) => {
            const btn = e.target;
            btn.textContent = '產生中…';
            try {
                const blob = await flattenToBlob(imageUrl, { v: 1, shapes });
                const a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = ((title || 'shot').replace(/[\\/:*?"<>|]/g, '_').slice(0, 40)) + '_標示.webp';
                a.click();
                setTimeout(() => URL.revokeObjectURL(a.href), 5000);
            } catch (err) { alert('下載失敗：' + ((err && err.message) || err)); }
            btn.textContent = '下載';
        });

        function close(saved) {
            if (!saved && dirty && !confirm('標示還沒儲存，確定關閉？')) return;
            document.removeEventListener('keydown', onKey);
            ov.remove();
            resolve(!!saved);
        }
        const onKey = (e) => {
            if (e.key === 'Escape') close(false);
            else if (!readonly && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
                if (shapes.pop()) { dirty = true; paint(); }
            }
        };
        document.addEventListener('keydown', onKey);
        ov.querySelector('[data-act="close"]').addEventListener('click', () => close(false));
        ov.addEventListener('click', (e) => { if (e.target === ov) close(false); });
    });
}


/** 圖 + 標示壓平成 WebP Blob（工具列「下載」用；純前端 canvas，不動原圖）。 */
export async function flattenToBlob(imageUrl, annotations) {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = imageUrl; });
    const cv = document.createElement('canvas');
    cv.width = img.naturalWidth;
    cv.height = img.naturalHeight;
    const ctx = cv.getContext('2d');
    ctx.drawImage(img, 0, 0);
    // 標示層走同一份 renderShapes → 序列化成 SVG → 當圖畫上去（繪圖規則不分岔）
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('xmlns', NS);
    svg.setAttribute('width', cv.width);
    svg.setAttribute('height', cv.height);
    renderShapes(svg, annotations);
    const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)],
        { type: 'image/svg+xml' }));
    try {
        const overlay = new Image();
        await new Promise((res, rej) => { overlay.onload = res; overlay.onerror = rej; overlay.src = url; });
        ctx.drawImage(overlay, 0, 0, cv.width, cv.height);
    } finally { URL.revokeObjectURL(url); }
    return new Promise(res => cv.toBlob(res, 'image/webp', 0.9));
}
