// ────────────────────────────────────────────────────────────────────────────
// /showcase-edit.html 作品編輯器 —— 七階段容器 _stageWrap ＋ 整頁 _render（第 1～7 區的 html）
//
// ⚠ 這是「傳統 script」不是 module：showcase-edit.html 底部依序 <script src> 載入 js/showcase-edit/*.js，
//   六支共用同一個全域詞法環境，頂層的 const／let／function 跨檔直接看得見（原本是同一段 inline script，
//   2026-09-12 原樣切開、零改寫；同 my.html → js/my/ 的拆法）。載入順序＝原本由上而下的執行順序，不可調換。
// ────────────────────────────────────────────────────────────────────────────
// ── 7 階段容器 wrapper（inner 為空 → 整個 stage 不輸出，唯讀模式部分 stage 會缺）──
function _stageWrap(id, num, title, inner) {
    if (!inner || !inner.trim()) return '';
    return `<div class="sc-stage" id="${id}">
        <div class="sc-stage-head"><span class="sc-stage-num">${num}</span><span class="sc-stage-title">${title}</span></div>
        ${inner}
    </div>`;
}

function _render() {
    // 未存的編輯：重畫前收起來（見 _dirtyEls 的說明）；分類勾選畫之前就要回寫進 DATA（第 5 區照 DATA 畫）
    const _keep = _snapshotDirty();
    if (_keep.has('data:cats') && DATA) DATA.selected_category_ids = _keep.get('data:cats');
    // 後端 GET /public/showcase-edit/{token} 回扁平 dict（_to_showcase_dict 直接展開），
    // 不是 {showcase: {...}}。把 DATA 當 sc 用，所有 sc.xxx 引用就對得上欄位。
    const sc = DATA || {};
    const gallery = sc.gallery || [];
    const processItems = sc.process_items || [];
    const credits = sc.credits || [];
    const projectName = DATA.project_name || '';

    let html = '';

    if (!EDITABLE) html += '<div class="readonly-banner">此頁面目前為唯讀模式</div>';

    // Sticky 頂部儲存 bar — master save + dirty indicator
    if (EDITABLE) {
        html += `<div id="save-bar"
            style="position:sticky;top:0;z-index:50;background:var(--bg);padding:12px 0;
                   margin:-12px 0 16px;border-bottom:1px solid var(--border);
                   display:flex;align-items:center;gap:12px;">
            <button id="btn-master-save" class="btn btn-primary"
                style="padding:8px 18px;font-weight:500;">儲存所有變更</button>
            <span id="save-status" style="color:var(--text3);font-size:12px;">所有變更已儲存</span>
        </div>`;
    }

    // 頂部大標題吃 public_title（對外顯示）— 留空 fallback 到專案名；下方輸入框 input 即時連動
    const _displayTitle = (sc.public_title || '').trim() || projectName;
    html += `<div class="page-title" id="page-title">${_esc(_displayTitle)}</div>`;
    html += `<div class="page-sub">作品展示編輯</div>`;

    // ══ Stage 1：基本資料（標題/客戶/年份；公開三開關移至 Stage 7 發布，data-field 不變）══
    let stBasics = '';
    if (EDITABLE) {
        stBasics = `
            <!-- Row 1：標題 / 客戶（autocomplete）兩欄 -->
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">
                <div>
                    <label style="display:block;color:var(--text3);font-size:11px;margin-bottom:4px;">標題（對外顯示）</label>
                    <input type="text" id="inp-public-title" data-field="public_title"
                        value="${_esc(sc.public_title || '')}"
                        placeholder="${_esc(projectName)}（留空用專案名）" style="width:100%;">
                </div>
                <div style="position:relative;">
                    <label style="display:block;color:var(--text3);font-size:11px;margin-bottom:4px;">客戶</label>
                    <input type="text" id="inp-public-client" data-field="public_client" autocomplete="off"
                        value="${_esc(sc.public_client || '')}" placeholder="輸入或選擇客戶" style="width:100%;">
                    <div id="client-dropdown" style="display:none;position:absolute;top:100%;left:0;right:0;z-index:100;background:var(--bg2);border:1px solid var(--border2);border-radius:6px;margin-top:2px;max-height:280px;overflow-y:auto;box-shadow:0 4px 12px rgba(0,0,0,0.4);"></div>
                </div>
            </div>
            <!-- Row 2：年份（窄） -->
            <div style="display:flex;gap:18px;align-items:end;margin-top:14px;flex-wrap:wrap;">
                <div style="width:100px;flex-shrink:0;">
                    <label style="display:block;color:var(--text3);font-size:11px;margin-bottom:4px;">年份</label>
                    <input type="number" id="inp-public-year" data-field="public_year"
                        value="${sc.public_year || ''}" placeholder="2024" style="width:100%;">
                </div>
            </div>`;
    }

    // ══ Stage 2：影片與圖片 ══
    let stMedia = '<div class="sc-stage-hint">圖集＝作品頁內容／精選圖＝首頁輪播（未設則用圖集第一張）／封面＝清單縮圖（唯讀）</div>';

    // 主影片 — 支援 YouTube / Vimeo / Facebook 內嵌，其他連結 link-out（跟著 master save）
    stMedia += `<div class="section" id="sec-video">
        <div class="section-label">主影片連結</div>
        <div class="sc-sub-hint">用途：作品頁最上方的主要影片</div>
        ${EDITABLE
            ? `<input type="url" id="inp-video" data-field="video_url"
                value="${_esc(sc.video_url || '')}" placeholder="YouTube / Vimeo / Facebook 影片連結" style="width:100%;">
               <div id="video-hint" style="display:none;margin-top:6px;color:var(--text2);font-size:12px;line-height:1.6;"></div>`
            : sc.video_url ? `<a href="${_esc(sc.video_url)}" target="_blank" style="color:var(--accent);">${_esc(sc.video_url)}</a>` : '<p style="color:var(--text3);">尚未設定</p>'}
    </div>`;

    // 附加影片（一頁多影片）— 動態列，跟著 master save（空 url 列不送）
    const extraVideos = Array.isArray(sc.extra_videos) ? sc.extra_videos : [];
    stMedia += `<div class="section" id="sec-extra-videos">
        <div class="section-label">附加影片</div>
        <div class="sc-sub-hint">用途：主影片之外的其他影片（花絮、系列集數等），作品頁顯示在主影片下方</div>
        ${EDITABLE
            ? `<p style="color:var(--text3);font-size:12px;margin:0 0 8px;">點頂部「儲存所有變更」存檔；空網址的列不會儲存。</p>
               <div id="extra-videos-list">${extraVideos.map(v => _extraVideoRowHtml(v)).join('')}</div>
               <button class="btn btn-sm" id="btn-add-extra-video" type="button">+ 新增影片</button>`
            : (extraVideos.length
                ? extraVideos.map(v => `<div style="margin-bottom:6px;"><a href="${_esc(v.url)}" target="_blank" style="color:var(--accent);">${_esc(v.url)}</a>${v.caption ? `<span style="color:var(--text3);font-size:12px;margin-left:8px;">${_esc(v.caption)}</span>` : ''}</div>`).join('')
                : '<p style="color:var(--text3);">尚無附加影片</p>')}
    </div>`;

    // 精選圖（首頁輪播用）— 寫 CrmProject.public_featured_image 單一欄位（上傳/刪除即存）
    const _featImg = sc.public_featured_image || '';
    stMedia += `<div class="section" id="sec-featured">
        <div class="section-label">精選圖（首頁輪播用）${_instantPill()}</div>
        <div class="sc-sub-hint">用途：首頁精選輪播優先用這張；沒設就用成果展示第一張（YouTube 縮圖畫質太低）</div>
        ${_featImg
            ? `<div class="gallery-item" style="aspect-ratio:16/9;max-width:420px;">
                    <img src="${_esc(_featImg)}" alt="featured">
                    ${EDITABLE ? '<button class="del" data-action="del-featured">✕</button>' : ''}
               </div>`
            : '<p style="color:var(--text3);font-size:13px;">尚未設定精選圖</p>'}
        ${EDITABLE ? '<div style="margin-top:8px;"><input type="file" id="inp-featured" style="display:none"><button class="btn btn-sm" id="btn-add-featured">+ 上傳精選圖</button></div>' : ''}
    </div>`;

    // 封面（唯讀）— 清單縮圖，由後台/系統管理
    if (sc.cover_url) {
        stMedia += `<div class="section" id="sec-cover">
            <div class="section-label">封面</div>
            <div class="sc-sub-hint">用途：作品清單縮圖（唯讀，由後台管理）</div>
            <div class="gallery-item" style="aspect-ratio:21/9;max-width:100%;"><img src="${_esc(sc.cover_url)}" alt="cover"></div>
        </div>`;
    }

    // Gallery（上傳/刪除即存）
    stMedia += `<div class="section" id="sec-gallery">
        <div class="section-label">成品展示${_instantPill()}</div>
        <div class="sc-sub-hint">用途：作品頁的圖集內容</div>
        <div class="gallery-grid">${gallery.map((g, i) => `
            <div class="gallery-item">
                <img src="${_esc(g.url)}" alt="${_esc(g.caption || '')}">
                ${EDITABLE ? `<button class="del" data-action="del-gallery" data-idx="${i}">✕</button>` : ''}
            </div>`).join('')}
        </div>
        ${EDITABLE ? '<div><input type="file" id="inp-gallery" multiple style="display:none"><button class="btn btn-sm" id="btn-add-gallery">+ 上傳圖片</button></div>' : ''}
        ${gallery.length === 0 ? '<p style="color:var(--text3);font-size:13px;">尚無成品圖</p>' : ''}
    </div>`;

    // Process（上傳/刪除即存）
    stMedia += `<div class="section" id="sec-process">
        <div class="section-label">創作過程${_instantPill()}</div>
        <div class="sc-sub-hint">用途：作品頁「創作過程」段落的圖片與說明</div>
        ${processItems.length > 0 ? `<div class="process-grid">${processItems.map((p, i) => `
            <div class="process-item">
                ${p.url ? `<img src="${_esc(p.url)}" alt="">` : ''}
                ${(p.phase || p.caption || p.video_url) ? `<div class="process-text">
                    ${p.phase ? `<strong>${_esc(p.phase)}</strong> ` : ''}${_esc(p.caption || '')}
                    ${p.video_url ? `<br><a href="${_esc(p.video_url)}" target="_blank" style="color:var(--accent);font-size:12px;">影片連結</a>` : ''}
                </div>` : ''}
                ${EDITABLE ? `<button class="btn btn-danger btn-sm process-del" data-action="del-process" data-idx="${i}">✕</button>` : ''}
            </div>`).join('')}</div>` : '<p style="color:var(--text3);font-size:13px;">尚無創作過程紀錄</p>'}
        ${EDITABLE ? '<div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap;"><input type="file" id="inp-process" style="display:none"><button class="btn btn-sm" id="btn-add-process">+ 上傳過程圖</button><button class="btn btn-sm" id="btn-pick-media-log" type="button">從影像紀錄挑選</button></div>' : ''}
    </div>`;

    // ══ Stage 3：作品描述（描述 + AI 協助撰寫 + AI 參考資料 = 一張 sc-ai-card）══
    // AI 參考資料：上傳給 AI 生成描述/SEO 的參考文件 + 補充說明（僅編輯模式）
    const _aiRefFiles = Array.isArray(sc.ai_reference_files) ? sc.ai_reference_files : [];
    const _aiRefFilesHtml = _aiRefFiles.length
        ? _aiRefFiles.map(f => `
            <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 10px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;margin-bottom:6px;">
                <span style="color:var(--text2);font-size:12px;word-break:break-all;">${_esc(f.name || '')}<span style="color:var(--text3);">（${Number(f.chars || 0).toLocaleString()} 字）</span></span>
                <button class="btn btn-sm btn-danger" type="button" data-action="del-ai-ref" data-name="${_esc(f.name || '')}" style="flex-shrink:0;">✕</button>
            </div>`).join('')
        : '<div style="color:var(--text3);font-size:12px;">尚未上傳參考文件</div>';
    const _aiRefBlock = EDITABLE ? `
        <div style="margin-top:14px;padding-top:14px;border-top:1px dashed var(--border);">
            <div style="color:var(--accent);font-size:12px;font-weight:600;margin-bottom:4px;">AI 參考資料（給 AI 生成描述/SEO 參考）${_instantPill()}</div>
            <div style="color:var(--text3);font-size:11px;line-height:1.6;margin-bottom:10px;">上傳企劃/腳本/客戶資料等，AI 產生問題與描述時會參考。掃描版 PDF（無文字層）抽不到字，請改貼文字。</div>
            <div id="ai-ref-list" style="margin-bottom:10px;">${_aiRefFilesHtml}</div>
            <div style="margin-bottom:14px;">
                <input type="file" id="inp-ai-ref" accept=".txt,.md,.csv,.pdf,.docx" multiple style="display:none;">
                <button class="btn btn-sm" id="btn-add-ai-ref" type="button">＋ 上傳參考文件</button>
                <span id="ai-ref-uploading" style="display:none;color:var(--text3);font-size:12px;margin-left:8px;">上傳中…</span>
            </div>
            <div>
                <label style="display:block;color:var(--text3);font-size:11px;margin-bottom:4px;">補充說明</label>
                <textarea id="inp-ai-ref-notes" rows="3" placeholder="補充給 AI 的背景說明、語氣要求、重點…" style="width:100%;">${_esc(sc.ai_reference_notes || '')}</textarea>
                <div class="sc-sub-hint" style="margin:6px 0 0;">跟著頂部「儲存所有變更」一起存檔</div>
            </div>
        </div>` : '';
    let stContent = `<div class="section sc-ai-card" id="sec-desc">
        <div class="section-label">專案描述${EDITABLE ? ' <button class="btn btn-sm" id="btn-ai-desc" type="button" style="margin-left:8px;flex:none;">AI 協助撰寫 SEO 描述</button>' : ''}</div>
        ${EDITABLE
            ? `<textarea id="inp-desc" data-field="description">${_esc(sc.description || '')}</textarea>
               <div id="ai-desc-panel" style="display:none;margin-top:10px;padding:12px;border:1px solid var(--accent);border-radius:6px;background:var(--bg2);"></div>
               ${_aiRefBlock}`
            : `<p style="color:var(--text2);">${_esc(sc.description || '尚未填寫')}</p>`}
    </div>`;

    // ══ Stage 4：演職員表 — Credits block 結構編輯器 ══
    const blocks = _normalizeCredits(credits);
    const rolesAvail = DATA.credit_roles_available || [];
    const tplsAvail = DATA.credit_templates_available || [];
    const usedRoleIds = new Set(blocks.map(b => b.role_id).filter(x => x != null));
    const availRoleOptions = rolesAvail.filter(r => !usedRoleIds.has(r.id));

    const _renderBlock = (block, bIdx) => {
        const entries = block.entries || [];
        const rowsHtml = entries.map((entry, eIdx) => `
            <div class="credit-row" data-bidx="${bIdx}" data-eidx="${eIdx}" style="gap:8px;">
                <input type="text" class="credit-duty" data-bidx="${bIdx}" data-eidx="${eIdx}"
                       value="${_esc(entry.duty || '')}" placeholder="職務（可空）"
                       style="width:120px;${EDITABLE ? '' : 'pointer-events:none;'}">
                <div class="credit-name-cell" data-bidx="${bIdx}" data-eidx="${eIdx}"
                     style="position:relative;flex:1;min-width:100px;">
                    <input type="text" class="credit-name-input" data-bidx="${bIdx}" data-eidx="${eIdx}"
                           value="${_esc(entry.name || '')}" placeholder="姓名（搜尋既有人員...）"
                           autocomplete="off"
                           style="width:100%;${entry.staff_id ? 'padding-right:24px;' : ''}${EDITABLE ? '' : 'pointer-events:none;'}">
                    ${entry.staff_id ? `<span class="credit-link-dot" data-bidx="${bIdx}" data-eidx="${eIdx}"
                                              title="已連結人員，點擊解除"
                                              style="position:absolute;right:8px;top:50%;transform:translateY(-50%);
                                                     width:8px;height:8px;border-radius:50%;background:var(--success);
                                                     cursor:pointer;"></span>` : ''}
                </div>
                ${entry.resume_url ? `<a class="credit-link" href="${_esc(entry.resume_url)}" target="_blank">簡歷 →</a>` : ''}
                ${EDITABLE ? `<button class="btn btn-danger btn-sm" data-action="del-entry" data-bidx="${bIdx}" data-eidx="${eIdx}">✕</button>` : ''}
            </div>`).join('');
        return `
            <div class="credit-block" data-bidx="${bIdx}"
                 ${EDITABLE ? `draggable="true"` : ''}
                 style="background:var(--bg3);border:1px solid var(--border);border-radius:4px;padding:12px;margin-bottom:8px;">
                <div style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);padding-bottom:6px;margin-bottom:8px;">
                    <div>
                        <strong style="color:var(--accent);">${_esc(block.name_zh || '其他')}</strong>
                        ${block.name_en ? `<span style="color:var(--text3);font-size:12px;margin-left:6px;">${_esc(block.name_en)}</span>` : ''}
                    </div>
                    ${EDITABLE ? `<div style="display:flex;gap:6px;align-items:center;">
                        <span style="color:var(--text3);font-size:11px;cursor:grab;" title="拖曳排序">↕</span>
                        <button class="btn btn-danger btn-sm" data-action="del-block" data-bidx="${bIdx}">✕</button>
                    </div>` : ''}
                </div>
                ${rowsHtml}
                ${entries.length === 0 ? `<p style="color:var(--text3);font-size:12px;padding:6px 0;">尚無條目</p>` : ''}
                ${EDITABLE ? `<div style="margin-top:8px;text-align:right;">
                    <button class="btn btn-sm" data-action="add-entry" data-bidx="${bIdx}">+ 新增條目</button>
                </div>` : ''}
            </div>`;
    };

    const roleOptionsHtml = availRoleOptions.map(r =>
        `<option value="${r.id}">${_esc(r.name_zh)}${r.name_en ? ' / ' + _esc(r.name_en) : ''}</option>`
    ).join('');
    const tplOptionsHtml = tplsAvail.map(t => {
        const roleNames = (t.roles || []).map(r => r.name_zh).join('、');
        return `<option value="${t.id}">${_esc(t.name)}${roleNames ? ' (' + _esc(roleNames) + ')' : ''}</option>`;
    }).join('');

    // 預設純文字（PM 從劇組拿到的就是文字稿，比 block 結構化容易上手）
    const creditsMode = DATA.credits_mode === 'block' ? 'block' : 'text';
    const creditsText = DATA.credits_text || '';
    const _modeBtnCls = (active) => `btn btn-sm${active ? ' btn-primary' : ''}`;

    let stCredits = `<div class="section" id="sec-credits">
        <div class="section-label">演職員表 / Credits ${_instantPill('結構化模式的操作立即儲存')}</div>
        ${EDITABLE ? `<div style="display:flex;gap:6px;margin-bottom:12px;font-size:12px;">
            <button id="btn-mode-block" type="button" class="${_modeBtnCls(creditsMode === 'block')}"
                    style="padding:5px 12px;">結構化</button>
            <button id="btn-mode-text" type="button" class="${_modeBtnCls(creditsMode === 'text')}"
                    style="padding:5px 12px;">純文字</button>
            <span style="color:var(--text3);font-size:11px;align-self:center;margin-left:6px;">
                ${creditsMode === 'text'
                    ? '純文字：直接貼上劇組給的文字稿'
                    : '結構化：用下拉選單建立職位 + 人員（含 staff 連結）'}
            </span>
        </div>` : ''}

        <!-- 結構化模式區塊 -->
        <div id="credits-block-mode" style="display:${creditsMode === 'block' ? '' : 'none'};">
            ${EDITABLE ? `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;">
                <select id="sel-add-role" class="btn btn-sm" style="padding:6px 10px;">
                    <option value="">+ 新增職位 ▾</option>
                    ${roleOptionsHtml}
                    <option value="__custom__">+ 自訂分類…</option>
                </select>
                ${tplsAvail.length > 0 ? `<select id="sel-apply-tpl" class="btn btn-sm" style="padding:6px 10px;">
                    <option value="">套用模板 ▾</option>
                    ${tplOptionsHtml}
                </select>` : ''}
                <button id="btn-carry-in-credits" type="button" class="btn btn-sm" style="padding:6px 10px;"
                    title="從此專案的派工人員自動建立 credits 區塊">從派工帶入</button>
            </div>` : ''}
            <div id="credit-blocks-container">${blocks.map((b, i) => _renderBlock(b, i)).join('')}</div>
            ${blocks.length === 0 ? '<p style="color:var(--text3);font-size:13px;">尚無演職員資料</p>' : ''}
        </div>

        <!-- 純文字模式區塊（跟著 master save，不再有獨立儲存按鈕） -->
        <div id="credits-text-mode" style="display:${creditsMode === 'text' ? '' : 'none'};">
            ${EDITABLE ? `
                <textarea id="inp-credits-text" data-field="credits_text" rows="14"
                    style="width:100%;font-family:ui-monospace,monospace;font-size:13px;line-height:1.7;padding:12px;border:1px solid var(--border2);border-radius:4px;background:var(--bg2);color:var(--text);"
                    placeholder="直接貼上劇組給的 credits 文字稿，存檔後對外作品頁原樣顯示。例：&#10;&#10;製作 王小明&#10;導演 李大華&#10;攝影 張三&#10;演員 邱雲福 / 周大力">${_esc(creditsText)}</textarea>
                <div style="color:var(--text3);font-size:11px;margin-top:6px;">點頂部「儲存所有變更」存檔；存檔後約 1 分鐘內自動發布</div>
            ` : `<pre style="white-space:pre-wrap;font-family:ui-monospace,monospace;font-size:13px;line-height:1.8;padding:12px;background:var(--bg2);border-radius:4px;">${_esc(creditsText) || '（尚無內容）'}</pre>`}
        </div>
    </div>`;

    // Website categories / tags (kind=category | tag)
    // 共用同一張 website_categories 表，前端依 kind 分兩段渲染。
    const _allCats = DATA.categories_available || [];
    const _selIds = new Set(DATA.selected_category_ids || []);
    const _renderCatSection = (label, kind, sectionId, headerExtra = '') => {
        const items = _allCats.filter(c => (c.kind || 'category') === kind);
        if (!items.length) return '';
        const chips = items.map(c => {
            const on = _selIds.has(c.id);
            const cls = on ? 'tag tag-on' : 'tag';
            const cursor = EDITABLE ? 'cursor:pointer;' : '';
            const click = EDITABLE ? `data-action="toggle-cat" data-id="${c.id}"` : '';
            return `<span class="${cls}" ${click} style="${cursor}user-select:none;">${on ? '✓ ' : ''}${_esc(c.name_zh)}</span>`;
        }).join('');
        return `<div class="section" id="${sectionId}">
            <div class="section-label" style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;">${label}${headerExtra}</div>
            <div class="tag-list">${chips}</div>
            ${EDITABLE ? `<p style="color:var(--text3);font-size:12px;">點擊切換選取；按頂部「儲存所有變更」存檔</p>` : ''}
        </div>`;
    };
    let stArchive = _renderCatSection('作品分類', 'category', 'sec-website-cats',
        EDITABLE ? '<button id="btn-cat-quiz" class="btn" type="button" style="font-size:12px;">不知道怎麼分？答 4 題</button>' : '');
    stArchive += _renderCatSection('作品標籤', 'tag', 'sec-website-tags');

    // 作品系列（跨專案策展集合）— select/order 掛 data-field 走 master save 一次提交；
    // 系列本身在官網管理「作品系列」卡建立/排序，這裡只管歸屬。
    if (EDITABLE) {
        const _series = DATA.series_available || [];
        const serOpts = ['<option value="">— 不屬於任何系列 —</option>']
            .concat(_series.map(s =>
                `<option value="${s.id}"${DATA.series_id === s.id ? ' selected' : ''}>${_esc(s.title_zh)}${s.visible === false ? '（隱藏中）' : ''}</option>`))
            .concat(['<option value="__new__">+ 建立新系列…</option>'])
            .join('');
        stArchive += `<div class="section" id="sec-website-series">
            <div class="section-label">作品系列</div>
            <div><div style="color:var(--text3);font-size:11px;margin-bottom:4px;">所屬系列（作品牆會把同系列摺疊成一張系列卡）；找不到就選「+ 建立新系列…」</div>
                <select data-field="series_id" id="inp-series-id" class="sc-select" style="min-width:220px;">${serOpts}</select></div>
            <p style="color:var(--text3);font-size:12px;">按頂部「儲存所有變更」存檔；新掛的作品排在系列尾端 — 系列內順序、名稱/介紹/封面在官網管理後台調</p>
        </div>`;
    }

    // ══ Stage 6：SEO（301 轉址 + SEO 覆寫 + AI SEO 規劃；唯讀不顯示）══
    const stSeo = EDITABLE ? _renderSeoSection(sc) : '';

    // ══ Stage 7：發布 — 三開關集中（自基本資料搬入，data-field 不變）+ 網站連結 + 狀態時間線 ══
    const siteUrl = (DATA.public_site_url || '').replace(/\/$/, '');
    const urlPath = DATA.public_url_path || '';
    const publicUrl = (siteUrl && urlPath) ? `${siteUrl}${urlPath}` : '';
    let stPublish = '';
    if (EDITABLE) {
        const _tg = (id, field, checked, title, hint) => `
            <label class="sc-toggle-row" for="${id}">
                <input type="checkbox" id="${id}" data-field="${field}" ${checked ? 'checked' : ''}>
                <span><span class="sc-toggle-title">${title}</span>
                <span class="sc-toggle-hint">${hint}</span></span>
            </label>`;
        stPublish += _tg('inp-published', 'published', sc.published, '公開',
            '勾選並儲存後，作品約 1 分鐘內自動發布到對外網站');
        stPublish += _tg('inp-public-featured', 'public_featured', sc.public_featured, '首頁精選',
            '出現在首頁精選輪播（優先用精選圖，未設用圖集第一張）');
        stPublish += _tg('inp-public-noindex', 'public_noindex', sc.public_noindex, 'noindex',
            '不讓搜尋引擎索引此頁（特殊需求才勾）');
        stPublish += '<div class="sc-publish-warn" id="sc-publish-warn"></div>';
    }
    if (siteUrl) {
        stPublish += `<div class="section" id="sec-url" style="margin-top:16px;">
            <div class="section-label">網站連結</div>
            ${publicUrl ? `
                <div style="display:flex;gap:8px;align-items:center;">
                    <input id="preview-url-input" type="text" readonly value="${_esc(publicUrl)}"
                        onclick="this.select()" style="flex:1;font-family:ui-monospace,monospace;font-size:13px;cursor:pointer;">
                    <button id="btn-copy-url" class="btn" type="button" title="複製連結">複製</button>
                    <button class="btn" type="button" onclick="window.open('${_esc(publicUrl)}','_blank')" title="另開新分頁">開新分頁</button>
                </div>
                <div style="color:var(--text3);font-size:11px;margin-top:6px;">
                    點輸入框自動全選複製。${DATA.public_published ? '' : '此作品尚未公開（勾「公開」+ 儲存後對外可見）。'}
                </div>
            ` : `
                <div style="color:var(--text3);font-size:13px;padding:12px;background:var(--bg3);border-radius:4px;">
                    尚未產生公開 URL — 第一次勾「公開」+ 儲存後會 auto-assign 編號（或填 slug 自訂）。
                </div>
            `}
        </div>`;
    }
    if (EDITABLE) {
        stPublish += `<div id="sec-publish-status"></div>
        <div style="margin-top:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
            <button id="btn-publish-save" class="btn btn-primary" type="button">儲存並發布</button>
            <span style="color:var(--text3);font-size:11px;">與頂部「儲存所有變更」相同 — 儲存後約 1 分鐘自動發布</span>
        </div>`;
    }

    // ══ 七區組裝：左主欄（編號 stage）＋右側上架檢查清單 ══
    const stagesHtml =
        _stageWrap('stage-basics', '1', '基本資料', stBasics) +
        _stageWrap('stage-media', '2', '影片與圖片', stMedia) +
        _stageWrap('stage-content', '3', '內容與 AI', stContent) +
        _stageWrap('stage-credits', '4', '演職員 Credits', stCredits) +
        _stageWrap('stage-archive', '5', '分類與歸檔', stArchive) +
        _stageWrap('stage-seo', '6', 'SEO', stSeo) +
        _stageWrap('stage-publish', '7', '發布', stPublish);
    html += `<div class="sc-layout"><div class="sc-main">${stagesHtml}</div>` +
        (EDITABLE ? '<div class="sc-rail"><div id="sc-checklist"></div></div>' : '') + '</div>';

    html += '<div class="footer">Originsun Studio</div>';

    document.getElementById('page').innerHTML = html;
    document.getElementById('loading').classList.add('fade');
    requestAnimationFrame(() => document.getElementById('page').classList.add('visible'));

    if (EDITABLE) {
        _bindEvents();
        _restoreDirty(_keep);        // 放回使用者動過的欄位（並重新標成未儲存）
        _updateChecklist();
        _startStatusPolling();
    }

    if (EMBED) {
        _postHeight();
        setTimeout(_postHeight, 300);   // 等字型/圖片穩定後再校一次
        if (!window.__embedRO && window.ResizeObserver) {
            window.__embedRO = new ResizeObserver(() => _postHeight());
            window.__embedRO.observe(document.getElementById('page'));
        }
    }
}
