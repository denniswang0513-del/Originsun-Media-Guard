/**
 * cash-split-editor.js — 收支拆項編輯器（「帳目一筆、內容拆裂」；跨 tab 共用）
 *
 * 一筆母公司匯款（+350,436）同時裝著專案款、代墊回款、薪資 —— 帳目上維持
 * 一筆，這個視窗把內容拆給：私帳的**未收案**、**未回款代墊列**、與手動分類項。
 * Σ(拆項) 必須等於帳目金額，差一塊存不了（後端 crm_logic.split_amount_error
 * 是算式正本，這裡的即時檢查只是給人看的預演）。
 *
 * 兩個掛載點共用（對帳單匯入預覽 recon.js／收支明細 crm-cashbook.js），
 * 各寫一份的下場見 cash-tax-picker 檔頭 —— 同一個教訓，不再學一次。
 * 「已拆 N 項」badge 也從這裡出（splitBadgeHtml）—— 兩個畫面各刻一顆
 * 上線第一天樣式就漂了（padding 差 1px，/simplify 重用審查抓到）。
 *
 * openCashSplitEditor({
 *   entity, amount, side,       // 帳目金額（恆正）與方向 'deposit'|'expense'
 *   taxOpts: { tree, byId },    // 分類樹（呼叫端已載好的那份，不重抓）
 *   initial: [...],             // 既有拆項（re-edit；含 advances）
 *   title,                      // 視窗標題（列的摘要）
 *   onSave(items|null),         // items＝拆項 payload；null＝解除拆項
 * })
 */
import { esc } from './dom.js';
import { authFetch } from './utils.js';
import { indexTax, taxSelects } from './cash-tax-picker.js';
import { fmtNum } from '../../tabs/crm/crm-utils.js';   // modal-styles 同款先例：shared → crm-utils

/** 「已拆 N 項」pill —— 收支明細與匯入預覽共用同一顆。 */
export function splitBadgeHtml(count) {
    return `<span style="font-size:11px;padding:2px 8px;border-radius:8px;background:#14351f;color:#86efac;">已拆 ${Number(count) || 0} 項</span>`;
}

async function _outstanding(entity) {
    // authFetch（js/shared 唯一那份）—— 自己 fetch 的話 401 會被吞成
    // 「沒有還在等錢的案子」，token 過期沒有人知道（picker-auth 事故的教訓）
    const r = await authFetch(`/api/v1/crm/cash-splits/outstanding?entity=${
        encodeURIComponent(entity)}`);
    if (!r.ok) throw new Error(`未收項目載入失敗（HTTP ${r.status}）`);
    return r.json();
}

export async function openCashSplitEditor(o) {
    const side = o.side || 'deposit';
    const target = Math.abs(Number(o.amount) || 0);
    const tree = (o.taxOpts || {}).tree || [];
    const byId = (o.taxOpts || {}).byId || indexTax(tree);
    // 未收選單只對收入側有意義（支出側的拆項＝純分類拆分）
    let out = { projects: [], advances: [], project_node_id: '', advance_node_id: '' };
    let outErr = '';
    if (side === 'deposit') {
        try { out = await _outstanding(o.entity); } catch (e) { outErr = e.message; }
    }

    // rows：編輯中的拆項。kind: 'project'（未收案）/'advance'（代墊回款）/'manual'
    const rows = (o.initial || []).map((s) => ({
        kind: s.project_id ? 'project' : ((s.advances || []).length ? 'advance' : 'manual'),
        amount: Number(s.amount) || 0,
        fee: Number(s.fee) || 0,
        feeOn: (Number(s.fee) || 0) > 0,
        taxonomy_node_id: s.taxonomy_node_id || '',
        category: s.category || '',
        sub_item: s.sub_item || '',
        project_id: s.project_id || '',
        // 🔴 名稱要跟著帶：未收案清單只有**還在等錢的**案 —— 結清的案查不到
        // 名字，那列會顯示成一串 raw id，看起來像連結壞掉（和平行動者案）
        project_name: s.project_name || '',
        note: s.note || '',
        advances: (s.advances || []).map((a) => ({ ...a })),
    }));

    // 未收案清單的搜尋詞（owner 2026-09-01「專案列表要可以勾選、搜尋」——
    // 案子多起來之後用捲的找不如打字）。已勾的列不受過濾影響，永遠看得到。
    let projQ = '';

    const wrap = document.createElement('div');
    wrap.id = 'cash-split-overlay';
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9800;'
        + 'display:flex;align-items:center;justify-content:center;';
    document.body.appendChild(wrap);
    const close = () => wrap.remove();

    const sum = () => rows.reduce((a, r) => a + (Number(r.amount) || 0), 0);
    const isOk = () => sum() === target && rows.length > 0
        && rows.every((r) => (Number(r.amount) || 0) > 0);
    const pathOf = (nid) => ((byId[nid] || []).map((n) => n.name).join(' ▸ ')) || '（未分類）';
    const projIdx = (id) => rows.findIndex((r) => r.kind === 'project' && r.project_id === id);
    const advChecked = (aid) => rows.some((r) => r.kind === 'advance'
        && (r.advances || []).some((l) => l.entry_id === aid));

    /** 🔴 金額類的輸入一律**只補會變的那幾個節點**，不整窗 render()：重畫會把
     *  194 個未收案的節點與所有 handler 重建一次，順便打掉剛 Tab 過去的焦點，
     *  逐格輸入變成每格都要重新用滑鼠點（效率審查）。
     *  patchGross：那一列的「專案結清毛額」提示。 */
    function patchGross(i) {
        const el = wrap.querySelector(`[data-gross="${i}"]`);
        if (el) {
            el.textContent = '專案結清毛額 $'
                + fmtNum((Number(rows[i].amount) || 0) + (Number(rows[i].fee) || 0));
        }
    }

    /** patchSum：合計與儲存鈕。 */
    function patchSum() {
        const total = sum();
        const diff = target - total;
        const el = wrap.querySelector('#csp-sum');
        if (el) {
            el.style.color = isOk() ? '#86efac' : '#fbbf24';
            el.textContent = `合計 $${fmtNum(total)} / $${fmtNum(target)}`
                + (diff ? `（差 ${diff > 0 ? '+' : ''}${fmtNum(diff)}）` : ' ✓');
        }
        const save = wrap.querySelector('#csp-save');
        if (save) save.disabled = !isOk();
    }

    const projListHtml = () => {
        // 已勾的一次算完：原本每案 filter 一次、checked 再一次，兩次都是
        // rows.findIndex 線性搜尋，而搜尋框每按一鍵就重跑整份清單
        const picked = new Set(rows.filter((r) => r.kind === 'project')
            .map((r) => r.project_id));
        return ((out.projects || [])
        .filter((p) => picked.has(p.id) || !projQ
            || (p.name || '').toLowerCase().includes(projQ))
        .map((p) => `
            <label style="display:flex;gap:8px;align-items:center;padding:3px 0;cursor:pointer;">
                <input type="checkbox" data-proj="${esc(p.id)}" ${picked.has(p.id) ? 'checked' : ''}>
                <span style="flex:1;color:#ddd;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(p.name)}</span>
                <span style="color:#9ca3af;font-size:11px;">未收 $${fmtNum(p.receivable)}</span>
            </label>`).join('')
        || `<div style="color:#6b7280;font-size:12px;">${projQ ? '找不到符合的未收案' : '沒有還在等錢的案子'}</div>`);
    };

    /** 勾選事件（清單重畫後要重掛 —— 搜尋只換清單那塊、不整窗重畫）。 */
    function bindProj() {
        wrap.querySelectorAll('[data-proj]').forEach((cb) => {
            cb.onchange = () => {
                const p = out.projects.find((x) => x.id === cb.dataset.proj);
                if (cb.checked && p && projIdx(p.id) < 0) {
                    rows.push({ kind: 'project', amount: p.receivable,
                                fee: 0, feeOn: false,
                                taxonomy_node_id: out.project_node_id || '',
                                category: '', sub_item: '',
                                project_id: p.id, project_name: p.name || '',
                                note: '', advances: [] });
                } else if (!cb.checked) {
                    const i = projIdx(cb.dataset.proj);
                    if (i >= 0) rows.splice(i, 1);
                }
                render();
            };
        });
    }

    function render() {
        const advList = (out.advances || []).map((a) => `
            <label style="display:flex;gap:8px;align-items:center;padding:3px 0;cursor:pointer;">
                <input type="checkbox" data-adv="${esc(a.entry_id)}" ${advChecked(a.entry_id) ? 'checked' : ''}>
                <span style="color:#9ca3af;font-size:11px;white-space:nowrap;">${esc(a.date)}</span>
                <span style="flex:1;color:#ddd;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(a.summary)}</span>
                <span style="color:#fbbf24;font-size:11px;">$${fmtNum(a.open)}</span>
            </label>`).join('')
            || '<div style="color:#6b7280;font-size:12px;">沒有未回款的代墊</div>';

        const rowHtml = rows.map((r, i) => `
            <div style="display:grid;grid-template-columns:110px minmax(0,1fr) 150px 28px;gap:6px;align-items:center;padding:4px 0;border-bottom:1px solid #262626;">
                <input type="number" value="${r.amount || ''}" data-amt="${i}" ${r.kind === 'advance' ? 'readonly title="代墊拆項的金額＝勾選列的合計"' : ''}
                    style="background:#141414;border:1px solid #333;color:#eee;border-radius:6px;padding:4px 8px;text-align:right;${r.kind === 'advance' ? 'opacity:.7;' : ''}">
                <div>
                    ${r.kind === 'project'
                        ? `<span style="color:#93c5fd;font-size:12px;">${esc(r.project_name || r.project_id)}</span>
                           <span style="color:#6b7280;font-size:11px;">（${esc(pathOf(r.taxonomy_node_id))}）</span>
                           <label title="源日代開發票、扣完費用才匯：金額填實匯淨額、這裡填被扣的代開費 —— 專案按毛額（金額＋代開費）結清，未收才會歸零"
                               style="display:inline-flex;gap:4px;align-items:center;color:#9ca3af;font-size:11px;cursor:pointer;white-space:nowrap;">
                               <input type="checkbox" data-feechk="${i}" ${r.feeOn ? 'checked' : ''}>扣代開費</label>
                           ${r.feeOn ? `<input type="number" data-fee="${i}" value="${r.fee || ''}" min="0" placeholder="代開費"
                                   style="background:#141414;border:1px solid #333;color:#eee;border-radius:6px;padding:2px 6px;width:80px;text-align:right;font-size:12px;">
                               <span data-gross="${i}" style="color:#6b7280;font-size:11px;">專案結清毛額 $${fmtNum((Number(r.amount) || 0) + r.fee)}</span>` : ''}`
                        : r.kind === 'advance'
                        ? `<span style="color:#fbbf24;font-size:12px;">沖 ${(r.advances || []).length} 筆代墊</span>
                           <span style="color:#6b7280;font-size:11px;">（${esc(pathOf(r.taxonomy_node_id))}）</span>`
                        : tree.length
                        ? `<div class="csp-tax" data-row="${i}" style="display:flex;gap:4px;flex-wrap:wrap;"></div>`
                        : `<input type="text" value="${esc(r.category || '')}" data-cat="${i}" placeholder="類別（平面科目）"
                               style="background:#141414;border:1px solid #333;color:#eee;border-radius:6px;padding:4px 8px;font-size:12px;width:100%;">`}
                </div>
                <input type="text" value="${esc(r.note || '')}" placeholder="備註" data-note="${i}"
                    style="background:#141414;border:1px solid #333;color:#ccc;border-radius:6px;padding:4px 8px;font-size:12px;">
                <button data-del="${i}" title="移除" style="background:none;border:none;color:#6b7280;cursor:pointer;font-size:14px;">✕</button>
            </div>`).join('');

        wrap.innerHTML = `<div style="background:#1b1b1b;border:1px solid #333;border-radius:12px;
                width:min(880px,94vw);max-height:92vh;overflow:auto;padding:18px 20px;">
            <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:4px;">
                <div style="font-size:15px;font-weight:600;color:#eee;">拆內容 — 帳目一筆、內容拆裂</div>
                <div style="color:#9ca3af;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(o.title || '')}</div>
            </div>
            <div style="color:#6b7280;font-size:12px;margin-bottom:10px;">
                帳目維持一筆（對帳照樣 1↔1），內容拆給各個項目 —— 合計必須等於
                <b style="color:#eee;">$${fmtNum(target)}</b>，差一塊都不能存。每個拆項都要有分類。</div>
            ${outErr ? `<div style="color:#fca5a5;font-size:12px;margin-bottom:8px;">${esc(outErr)} —— 手動拆項照常可用</div>` : ''}
            ${side === 'deposit' ? `
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px;">
                <div style="background:#151515;border:1px solid #2a2a2a;border-radius:8px;padding:10px 12px;max-height:180px;overflow:auto;">
                    <div style="color:#93c5fd;font-size:12px;margin-bottom:4px;">連結未收案（勾了自動帶未收額，金額可改）</div>
                    <input data-projq type="search" value="${esc(projQ)}" placeholder="搜尋未收案…" autocomplete="off"
                        style="background:#141414;border:1px solid #333;color:#eee;border-radius:6px;padding:3px 8px;font-size:12px;width:100%;margin-bottom:4px;">
                    <div id="csp-projlist">${projListHtml()}</div>
                </div>
                <div style="background:#151515;border:1px solid #2a2a2a;border-radius:8px;padding:10px 12px;max-height:180px;overflow:auto;">
                    <div style="color:#fbbf24;font-size:12px;margin-bottom:4px;">沖未回款代墊（逐筆結清；金額＝勾選合計）</div>
                    ${advList}
                    <div style="color:#4b5563;font-size:11px;margin-top:4px;">歷史回款未逐筆連結 —— 總額以 1300 科目餘額為準</div>
                </div>
            </div>` : ''}
            <div style="display:grid;grid-template-columns:110px minmax(0,1fr) 150px 28px;gap:6px;color:#6b7280;font-size:11px;">
                <div style="text-align:right;">金額</div><div>分類／連結</div><div>備註</div><div></div>
            </div>
            ${rowHtml || '<div style="color:#4b5563;font-size:12px;padding:8px 0;">還沒有拆項 —— 勾上面的未收項目，或按「＋ 手動一項」</div>'}
            <div style="display:flex;gap:8px;align-items:center;margin-top:10px;">
                <button id="csp-add" class="crm-btn crm-btn-secondary" style="font-size:12px;">＋ 手動一項</button>
                <div style="flex:1;"></div>
                <div id="csp-sum" style="font-size:13px;"></div>
            </div>
            <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px;">
                ${(o.initial || []).length ? '<button id="csp-clear" class="crm-btn crm-btn-secondary" style="color:#fca5a5;">解除拆項</button>' : ''}
                <button id="csp-cancel" class="crm-btn crm-btn-secondary">取消</button>
                <button id="csp-save" class="crm-btn crm-btn-primary">儲存拆項</button>
            </div>
        </div>`;
        patchSum();

        // ── 事件 ──
        wrap.onclick = (ev) => { if (ev.target === wrap) close(); };
        wrap.querySelector('#csp-cancel').onclick = close;
        const clearBtn = wrap.querySelector('#csp-clear');
        if (clearBtn) clearBtn.onclick = () => { o.onSave(null); close(); };
        wrap.querySelector('#csp-save').onclick = () => {
            if (!isOk()) return;
            o.onSave(rows.map((r) => ({
                amount: Number(r.amount) || 0,
                fee: r.kind === 'project' ? (Number(r.fee) || 0) : 0,
                taxonomy_node_id: r.taxonomy_node_id || '',
                category: r.taxonomy_node_id ? '' : (r.category || ''),
                sub_item: r.taxonomy_node_id ? '' : (r.sub_item || ''),
                project_id: r.project_id || '',
                note: r.note || '',
                advances: (r.advances || []).map((l) => ({ entry_id: l.entry_id, amount: l.amount })),
            })));
            close();
        };
        wrap.querySelector('#csp-add').onclick = () => {
            rows.push({ kind: 'manual', amount: Math.max(0, target - sum()) || 0,
                        taxonomy_node_id: '', category: '', sub_item: '',
                        project_id: '', note: '', advances: [] });
            render();
        };
        wrap.querySelectorAll('[data-del]').forEach((b) => {
            b.onclick = () => { rows.splice(Number(b.dataset.del), 1); render(); };
        });
        wrap.querySelectorAll('[data-amt]').forEach((inp) => {
            inp.onchange = () => {           // 只補合計與儲存鈕，不整窗重畫（保焦點）
                const i = Number(inp.dataset.amt);
                rows[i].amount = Number(inp.value) || 0;
                patchGross(i);
                patchSum();
            };
        });
        wrap.querySelectorAll('[data-note]').forEach((inp) => {
            inp.onchange = () => { rows[Number(inp.dataset.note)].note = inp.value; };
        });
        wrap.querySelectorAll('[data-cat]').forEach((inp) => {
            inp.onchange = () => { rows[Number(inp.dataset.cat)].category = inp.value.trim(); };
        });
        // 扣代開費（源日代開發票、扣完費用才匯）：金額欄恆為實匯淨額，
        // 代開費外加 —— 勾了先猜「未收 − 目前金額」（實匯已填好的常見流程），
        // 改代開費則反推金額 = 未收 − 費（兩個方向都不用使用者自己算）。
        // 勾「扣代開費」與改代開費金額是同一件事的兩個入口 —— 一支處理：
        // 勾了先猜「未收 − 目前金額」，改費用則反推金額 = 未收 − 費。
        const setFee = (i, fee, on) => {
            const r = rows[i];
            const p = out.projects.find((x) => x.id === r.project_id);
            r.feeOn = on;
            r.fee = on ? Math.max(0, fee) : 0;
            if (p) {
                r.amount = on ? Math.max(0, p.receivable - r.fee) : p.receivable;
            }
            render();
        };
        wrap.querySelectorAll('[data-feechk]').forEach((cb) => {
            cb.onchange = () => {
                const i = Number(cb.dataset.feechk);
                const p = out.projects.find((x) => x.id === rows[i].project_id);
                setFee(i, p ? p.receivable - (Number(rows[i].amount) || 0) : 0,
                       cb.checked);
            };
        });
        wrap.querySelectorAll('[data-fee]').forEach((inp) => {
            inp.onchange = () => setFee(Number(inp.dataset.fee),
                                        Number(inp.value) || 0, true);
        });
        bindProj();
        const pq = wrap.querySelector('[data-projq]');
        if (pq) {
            pq.oninput = () => {   // 只換清單那塊 —— 整窗重畫會把打字焦點打掉
                projQ = pq.value.trim().toLowerCase();
                const box = wrap.querySelector('#csp-projlist');
                if (box) { box.innerHTML = projListHtml(); bindProj(); }
            };
        }
        wrap.querySelectorAll('[data-adv]').forEach((cb) => {
            cb.onchange = () => {
                const a = out.advances.find((x) => x.entry_id === cb.dataset.adv);
                let r = rows.find((x) => x.kind === 'advance');
                if (cb.checked && a) {
                    if (!r) {
                        r = { kind: 'advance', amount: 0,
                              taxonomy_node_id: out.advance_node_id || '',
                              category: '', sub_item: '',
                              project_id: '', note: '', advances: [] };
                        rows.push(r);
                    }
                    if (!r.advances.some((l) => l.entry_id === a.entry_id)) {
                        r.advances.push({ entry_id: a.entry_id, amount: a.open });
                    }
                } else if (!cb.checked && r) {
                    r.advances = r.advances.filter((l) => l.entry_id !== cb.dataset.adv);
                    if (!r.advances.length) rows.splice(rows.indexOf(r), 1);
                }
                if (r && r.advances && r.advances.length) {
                    r.amount = r.advances.reduce((s2, l) => s2 + l.amount, 0);
                }
                render();
            };
        });
        // 手動列的分類樹（同 cash-tax-picker 的一排會長的下拉）
        wrap.querySelectorAll('.csp-tax').forEach((box) => {
            const i = Number(box.dataset.row);
            const draw = () => taxSelects(box, {
                tree,
                chain: byId[rows[i].taxonomy_node_id] || [],
                cls: 'crm-select crm-select-sm',
                style: 'max-width:130px;',
                blank: (lv) => (lv === 0 ? '（選分類）' : '（不細分）'),
                keepOne: true,
                onPick: (lv, v) => {
                    const chain = byId[rows[i].taxonomy_node_id] || [];
                    rows[i].taxonomy_node_id = v || (lv > 0 ? (chain[lv - 1] || {}).id || '' : '');
                    draw();
                },
            });
            draw();
        });
    }
    render();
}
