/**
 * meeting-view.js — 提案的「會議記錄」分頁（多筆，逐欄自動儲存）。
 *
 * **沒有版本**：會議記錄是創意發想與企劃書的輸入素材，不是產出物。AI 只做
 * **輸入輔助**：每筆可傳一個會議錄音檔 → 後端 whisper 逐字稿 → claude 整理
 * （content 空才代填；人寫過的字後端絕不覆蓋）。
 *
 * 兩個介面共用（後台提案庫的詳情、獨立企劃頁），所以照 brief-view / quote-view
 * 那三條：只 import js/shared 與同目錄、自帶 --mv-* 變數、外殼由呼叫端給。
 *
 * 🔴 內部資料：公開 ?t= 訪客模式**不掛**這個元件 —— 呼叫端連分頁鈕都不建
 * （比照企劃書/報價單；唯讀不是靠隱藏元素，是靠沒建出來）。
 */

import { autosaveDelegated, syncBaseline } from '../../js/shared/autosave.js';
import { pollJob } from '../../js/shared/poll-job.js';
import { fmtSize } from '../../js/shared/clip_utils.js';
import { authDownload, autoGrow, bearerHeader, ensureStyle, esc, proxyBodyLimit,
         uploadWithProgress } from '../../js/shared/utils.js';
import { tfetch } from './prop-fetch.js';

const API = '/api/v1/crm';
const _base = (pid) => `${API}/proposals/${encodeURIComponent(pid)}/meetings`;

/**
 * @param host  掛載容器（會被清空）
 * @param opts.proposalId
 */
export async function renderMeetings(host, { proposalId }) {
    ensureStyle('mv-style', STYLE);
    host.classList.add('mv');
    // 刻意**不**在這裡收上一輪的錄音：兩個呼叫端都不會對同一個 host 重掛
    // （詳情視窗每次開都是新的、獨立頁換提案＝整頁重載），而真的重掛時
    // `_stopRecording` 只是「開始停」，下一行就把 __mv 換掉了 —— 收尾會拿到
    // 新的 proposalId，把舊提案的錄音 POST 到新提案去。畫面被拆掉那條由
    // `_tick` 的 isConnected 接。
    host.__mv = { proposalId, notes: [], polling: new Set(), rec: null, starting: false };
    // 逐欄自動儲存走共用件（debounce + 失敗退基準重試 + 重畫不重綁）。
    // 委派綁在 host 上一次就好 —— 清單每次動作都整塊重畫，逐顆綁必漏。
    autosaveDelegated(host, '[data-f]', (v, el) =>
        tfetch(`${_base(proposalId)}/${encodeURIComponent(el.dataset.id)}`,
               { method: 'PATCH', json: { [el.dataset.f]: v } }),
        { onError: (e) => alert('儲存失敗：' + (e.message || e)) });
    await _load(host);
}

async function _load(host) {
    const s = host.__mv;
    try {
        s.notes = (await tfetch(_base(s.proposalId))).notes || [];
    } catch (e) {
        host.innerHTML = `<div class="mv-note mv-err">載入失敗：${esc(e.message || e)}</div>`;
        return;
    }
    _render(host);
}

function _render(host) {
    const s = host.__mv;
    host.innerHTML = `
        <div class="mv-bar">
            <button class="mv-btn primary" data-add>新增會議記錄</button>
            <span class="mv-note">共 ${s.notes.length} 筆；打完字自動儲存。</span>
        </div>
        ${s.notes.map(_cardHtml).join('') ||
          '<div class="mv-note">還沒有會議記錄 —— 開會前先按上面新增一筆，邊聽邊記。</div>'}`;
    _wire(host);
}

/** 值一律走 DOM property 餵（不進模板字串）—— 這裡裝的是自由文字。 */
function _cardHtml(m) {
    const id = esc(String(m.id));
    return `
        <div class="mv-card" data-card="${id}">
            <div class="mv-head">
                <input type="date" class="mv-in mv-date" data-f="met_at" data-id="${id}">
                <input class="mv-in mv-title" data-f="title" data-id="${id}"
                       placeholder="會議主題">
                <span class="mv-gap"></span>
                <button class="mv-btn sm" data-del="${id}">刪除</button>
            </div>
            <input class="mv-in mv-att" data-f="attendees" data-id="${id}"
                   placeholder="出席者（自由填，例：客戶王經理、導演、製片）">
            <textarea class="mv-in mv-body" data-f="content" data-id="${id}"
                      placeholder="會議記錄…"></textarea>
            <div class="mv-audio"></div>
            <div class="mv-note mv-by"></div>
        </div>`;
}

// ── 錄音 → 逐字稿 → AI 整理 ─────────────────────────────────

function _audioHtml(m, rec) {
    // 錄音中是**渲染出來的一個狀態**，不是就地寫進 DOM 的東西 —— 這樣清單
    // 重畫（新增/刪除別筆）會把錄音列原樣畫回來，而不是把它洗掉
    if (rec && rec.mid === String(m.id)) {
        return `
            <div class="mv-abar">
                <span class="mv-dot"></span>
                <span class="mv-note mv-rt">錄音中 ${_clock(_elapsed(rec))}</span>
                <span class="mv-gap"></span>
                <button class="mv-btn sm" data-stop>停止並上傳</button>
                <button class="mv-btn sm" data-cancel>取消</button>
            </div>
            <div class="mv-note">錄音只在這個瀏覽器裡 —— 關掉整頁會沒有
                （關掉這個視窗會自動幫你存起來）。</div>`;
    }
    if (m.status === 'pending') {
        return `
            <div class="mv-abar">
                <span class="mv-spin"></span>
                <span class="mv-note">處理中：${esc(m.phase || '…')} ——
                    可以先離開這頁，回來再看。</span>
            </div>`;
    }
    // 一條 bar，兩種內容（結構只寫一次）
    const recBtn = _CAN_REC
        ? '<button class="mv-btn sm" data-rec>現場錄音</button>' : '';
    const parts = [`<div class="mv-abar">${m.audio_name ? `
            <span class="mv-note mv-aname">${esc(m.audio_name)}</span>
            <span class="mv-gap"></span>
            <button class="mv-btn sm" data-adl>下載錄音</button>
            ${m.has_transcript ? '<button class="mv-btn sm" data-resum>重跑 AI 整理</button>' : ''}
            <button class="mv-btn sm" data-aup>重新上傳</button>${recBtn}` : `
            <button class="mv-btn sm" data-aup>上傳會議錄音</button>${recBtn}
            <span class="mv-note">AI 會轉成逐字稿並整理成會議記錄
                （上面欄位是空的才代填，寫過的字不會被動到）。</span>`}</div>`];
    if (m.status === 'failed') {
        parts.push(`<div class="mv-note mv-err">處理失敗：${esc(m.error || '')}</div>`);
    }
    // 全文不隨清單一起載（一小時錄音幾十 KB）—— 展開時才打單筆 GET
    if (m.has_transcript) parts.push(_foldHtml('transcript', '逐字稿', m));
    if (m.has_summary) parts.push(_foldHtml('summary', 'AI 整理', m));
    return parts.join('');
}

const _FOLD_FIELD = { transcript: 'transcript', summary: 'ai_summary' };

function _foldHtml(kind, label, m) {
    const body = m[_FOLD_FIELD[kind]];      // 單筆 GET 回來的才有值
    return `
        <details class="mv-fold" data-fold="${kind}"><summary>${label}</summary>
            <div class="mv-pre">${body ? esc(body) : '載入中…'}</div>
        </details>`;
}

function _wireAudio(host, card, m) {
    const s = host.__mv;
    const mid = String(m.id);
    card.querySelector('[data-aup]')?.addEventListener('click', () => {
        if (!_okReplace(m, '重新上傳')) return;
        const input = document.createElement('input');
        input.type = 'file';
        // 白名單與後端一致（後端仍會再驗一次）
        input.accept = '.mp3,.wav,.m4a,.aac,.flac,.ogg,.opus,.webm,.mp4,.mov,.mkv,.mts';
        input.addEventListener('change', () => {
            if (input.files[0]) _uploadAudio(host, mid, input.files[0]);
        });
        input.click();
    });
    card.querySelector('[data-rec]')?.addEventListener('click', () => {
        // 先看有沒有人在錄，再問「要不要蓋掉」—— 反過來的話會先嚇使用者一句
        // 破壞性警告，按了確定才說「已經有一筆在錄音了」
        if (s.rec || s.starting) { alert('已經有一筆在錄音了 —— 先把那筆停掉。'); return; }
        if (_okReplace(m, '錄完上傳')) _startRecording(host, mid);
    });
    card.querySelector('[data-stop]')?.addEventListener('click',
        () => _stopRecording(host, true));
    card.querySelector('[data-cancel]')?.addEventListener('click', () => {
        if (confirm('取消就把這段錄音丟掉，不會上傳。確定？')) _stopRecording(host, false);
    });
    card.querySelector('[data-adl]')?.addEventListener('click', () => {
        authDownload(`${_base(s.proposalId)}/${encodeURIComponent(mid)}/audio/download`,
                     m.audio_name, '錄音下載');
    });
    card.querySelector('[data-resum]')?.addEventListener('click', async (e) => {
        e.target.disabled = true;
        try {
            const d = await tfetch(`${_base(s.proposalId)}/${encodeURIComponent(mid)}/summarize`,
                                   { method: 'POST' });
            _refreshCard(host, d.note);
            _poll(host, mid);           // 這裡才是「剛變成 pending」的地方
        } catch (err) { alert('重跑失敗：' + (err.message || err)); e.target.disabled = false; }
    });
    // 展開才載全文（清單那趟只給 has_*）。單筆 GET 一趟同時帶回逐字稿與
    // AI 整理 → 第二塊展開時已在手上，**只是不打 API，畫還是要畫**
    card.querySelectorAll('[data-fold]').forEach(el => {
        el.addEventListener('toggle', async () => {
            if (!el.open) return;
            const field = _FOLD_FIELD[el.dataset.fold];
            const cur = s.notes.find(n => String(n.id) === mid) || {};
            const pre = el.querySelector('.mv-pre');
            if (!cur[field]) {
                try {
                    const d = await tfetch(`${_base(s.proposalId)}/${encodeURIComponent(mid)}`);
                    Object.assign(cur, d.note || {});
                } catch (e) {
                    pre.textContent = '載入失敗：' + (e.message || e);
                    return;
                }
            }
            pre.textContent = cur[field] || '（沒有內容）';
        });
    });
}

// ── 現場錄音（MediaRecorder）─────────────────────────────────
//
// 🔴 `getUserMedia` 只在**安全內容**下存在：HTTPS 或 localhost。公司內網用
// http://192.168.1.107:8000 進來時瀏覽器根本不給麥克風 —— 所以這顆按鈕是
// **有才長出來**（`_CAN_REC`），沒有的路徑照舊用上傳，不留一顆按了會壞的
// 鈕。走 foundry.originsun-studio.com（HTTPS）進來就有。
//
// 錄音狀態是渲染的輸入（見 `_audioHtml` 開頭）；收尾只有 `_finish` 一個出口。

const _MIMES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];
// 每瀏覽器一次就定了，不必每張卡每次重畫都算
const _CAN_REC = !!(window.isSecureContext && navigator.mediaDevices?.getUserMedia
                    && window.MediaRecorder);
const _REPLACE_WARN = '會換掉這一筆的錄音、逐字稿與 AI 整理（手寫的欄位不受影響）。繼續？';
// 一次只錄一筆，所以攔截器共用一份（addEventListener 對同一個 function 會去重）
const _WARN = (e) => { e.preventDefault(); e.returnValue = ''; };

/** 已經有檔的那筆要再錄/再傳 → 先問過。 */
const _okReplace = (m, verb) => !m.audio_name || confirm(verb + _REPLACE_WARN);

const _clock = (sec) => `${String(Math.floor(sec / 60)).padStart(2, '0')}:`
                      + `${String(sec % 60).padStart(2, '0')}`;
const _elapsed = (r) => Math.floor((Date.now() - r.t0) / 1000);
const _cardOf = (host, mid) =>
    host.querySelector(`.mv-card[data-card="${CSS.escape(String(mid))}"]`);
const _noteOf = (host, mid) => host.__mv.notes.find(n => String(n.id) === String(mid));

/** 依 id 重畫那張卡的錄音區（卡或那一筆不在了就算了）。 */
function _repaint(host, mid) {
    const card = _cardOf(host, mid);
    const cur = _noteOf(host, mid);
    if (card && cur) _paintAudio(host, card, cur);
}

async function _startRecording(host, mid) {
    const s = host.__mv;
    // 位子在 **await 之前**就佔住：getUserMedia 會讓出一個 task，連按兩下的
    // 第二下會通過檢查、開出第二個 recorder，而第一個從此沒人收得掉
    // （麥克風、計時器、chunks、beforeunload 全留著）
    if (s.rec || s.starting) { alert('已經有一筆在錄音了 —— 先把那筆停掉。'); return; }
    s.starting = true;
    let stream;
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
        // 使用者按了拒絕、或根本沒有麥克風 —— 照實說，別假裝在錄
        alert('拿不到麥克風：' + (e.message || e)
              + '\n（瀏覽器網址列的權限圖示可以重新允許）');
        return;
    } finally {
        s.starting = false;
    }
    const mime = _MIMES.find(t => MediaRecorder.isTypeSupported(t)) || '';
    const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
    const r = { mid: String(mid), rec, chunks: [], t0: Date.now() };
    rec.addEventListener('dataavailable', (e) => {
        if (e.data && e.data.size) r.chunks.push(e.data);
    });
    // 唯一的收尾出口，**在建立時就掛**：瀏覽器自己停掉（裝置被拔、權限被
    // 收回）也會走到，不然那條路沒人接、計時器還在數一個死掉的錄音。
    // （規格保證 error 後面會跟一個 stop，所以不必另外掛 error —— 掛了反而
    // 會把 intent 設掉，害 _finish 那句「錄音被中斷了」永遠不會出現。）
    rec.addEventListener('stop', () => _finish(host, r), { once: true });
    window.addEventListener('beforeunload', _WARN);
    // 計時器每秒重新找那顆節點：清單重畫過後節點是新的，抓著舊的只會餵給
    // 一個已經被丟掉的 DOM（也順便當「畫面還在不在」的偵測）
    r.timer = setInterval(() => _tick(host, r), 1000);
    // 每 5 秒切一塊：中途當掉至少不是整場空的
    rec.start(5000);

    s.rec = r;
    _repaint(host, r.mid);
}

/** 每秒：更新計時，並確認這段錄音還有歸屬。 */
function _tick(host, r) {
    if (host.__mv.rec !== r) return;              // 已經在收尾了
    if (!host.isConnected) {                      // 詳情視窗被關掉 → 存起來
        _stopRecording(host, true);
        return;
    }
    // 「那一筆還在不在」問**資料**不問畫面 —— 用卡片存不存在判斷的話，
    // 一次 _load 失敗（NAS/隧道抖一下，錯誤訊息取代整個 host）就會被當成
    // 「被刪掉了」，把一整場會議的錄音丟掉
    if (!_noteOf(host, r.mid)) {
        alert('這一筆會議記錄被刪掉了 —— 錄音沒有地方可以存，只好丟掉。');
        _stopRecording(host, false);
        return;
    }
    const el = _cardOf(host, r.mid)?.querySelector('.mv-rt');
    if (el) el.textContent = '錄音中 ' + _clock(_elapsed(r));
}

/** 使用者按下停止/取消（或程式判定要收）。真正的收尾在 `_finish`。 */
function _stopRecording(host, upload) {
    const r = host.__mv.rec;
    if (!r) return;
    r.intent = upload ? 'upload' : 'discard';
    // 已經 inactive（瀏覽器先停了）再呼叫 stop() 會 throw，直接收尾
    if (r.rec.state === 'inactive') _finish(host, r);
    else r.rec.stop();                            // → 'stop' → _finish
}

/** 唯一的收尾出口：收硬體、拆監聽、決定上傳還是丟掉。
 *
 * **只跑一次**：`stop()` 是同步把 state 變成 inactive、事件另外排隊，所以
 * 連按兩下「停止並上傳」時第二下會看到 inactive 而直接收尾，接著排隊的
 * `'stop'` 又送一次 —— 沒這道閘就會上傳兩份。 */
function _finish(host, r) {
    if (r.done) return;
    r.done = true;
    const s = host.__mv;
    clearInterval(r.timer);
    window.removeEventListener('beforeunload', _WARN);
    if (s.rec === r) s.rec = null;                    // 位子讓出來（下一筆能錄）
    r.rec.stream.getTracks().forEach(t => t.stop());  // 分頁上的紅點熄掉
    if (r.intent === 'discard') { _repaint(host, r.mid); return; }
    const type = r.rec.mimeType || r.chunks[0]?.type || 'audio/webm';
    const blob = new Blob(r.chunks, { type });
    if (!blob.size) {
        alert('這段錄音是空的 —— 沒有收到任何聲音。');
        _repaint(host, r.mid);                    // 不重畫的話會卡在停住的計時器上
        return;
    }
    // intent 沒設 = 瀏覽器自己停的（裝置被拔/權限被收回）——
    // 錄到的部分是好的，照樣送出，但要講一聲
    if (!r.intent) alert('錄音被中斷了（裝置或權限有變）—— 已經錄到的部分照樣上傳。');
    const ext = type.includes('mp4') ? '.mp4' : '.webm';
    _uploadAudio(host, r.mid, new File([blob], `現場錄音_${_stamp()}${ext}`, { type }));
}

/** 檔名用的本地時間戳（NAS 上一眼看得出哪天錄的）。 */
function _stamp() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}`
         + `_${p(d.getHours())}${p(d.getMinutes())}`;
}

/** 卡片自己找 —— 錄音收尾時詳情視窗可能已經關掉了（沒有卡就沒有進度可畫），
 *  但檔案還是要送出去：使用者只是關了視窗，沒有按取消。 */
async function _uploadAudio(host, mid, f) {
    const s = host.__mv;
    const card = _cardOf(host, mid);
    // 遠端（foundry 隧道）單一請求 100MB 硬上限 —— 與其讓 Cloudflare 回一頁
    // 空白 413，不如先講清楚
    const cap = proxyBodyLimit();
    if (cap && f.size > cap) {
        alert(`遠端連線的單次上傳上限是 ${fmtSize(cap)} —— 請在公司內網上傳`
              + '這個檔，或先把影片轉成純音檔再傳。');
        return;
    }
    const box = card?.querySelector('.mv-audio');
    if (box) {
        box.innerHTML = '<div class="mv-abar"><span class="mv-spin"></span>'
                      + '<span class="mv-note mv-uppct">上傳中…</span></div>';
    }
    const pct = box?.querySelector('.mv-uppct');
    const fd = new FormData();
    fd.append('file', f);
    try {
        const d = await uploadWithProgress(
            `${_base(s.proposalId)}/${encodeURIComponent(mid)}/audio`, fd, {
                headers: bearerHeader(),
                onProgress: (loaded, total) => {
                    if (pct && total) {
                        pct.textContent = `上傳中… ${Math.round(loaded / total * 100)}%`;
                    }
                },
                onUploaded: () => {
                    if (pct) pct.textContent = '上傳完成，等伺服器落檔…';
                },
            });
        _refreshCard(host, d.note);
        _poll(host, mid);               // 這裡才是「剛變成 pending」的地方
    } catch (e) {
        alert('上傳失敗：' + (e.message || e));   // 400 沒資產夾 / 413 超限 / 422 副檔名照實顯示
        const cur = s.notes.find(n => String(n.id) === mid);
        if (card && cur) _paintAudio(host, card, cur);
    }
}

/** 開始追一筆處理中的錄音。**只從「剛變成 pending」的地方呼叫**（上傳成功、
 *  重跑整理、初次載入時已在跑的）—— 從重畫路徑呼叫會讓上限自動續約。 */
function _poll(host, mid) {
    const s = host.__mv;
    pollJob(mid, s.polling, {
        alive: () => host.isConnected,
        // 一小時的上限：後端進度字幾十秒才換一次，15 秒問一次夠靈敏也不燒連線
        maxTicks: 240,
        tickMs: 15000,
        list: async () => {
            const d = await tfetch(`${_base(s.proposalId)}/${encodeURIComponent(mid)}`);
            return d.note ? [d.note] : [];
        },
        onSettled: (items) => {
            if (host.isConnected && items[0]) _refreshCard(host, items[0]);
        },
    });
}

/** 錄音區的畫＋綁（首次渲染、輪詢刷新、錄音狀態轉換都走這裡）。 */
function _paintAudio(host, card, m) {
    card.querySelector('.mv-audio').innerHTML = _audioHtml(m, host.__mv.rec);
    _wireAudio(host, card, m);
}

/** 只換這張卡的錄音區（整清單重畫會把別張卡打字中的欄位掀掉）。 */
function _refreshCard(host, note) {
    const s = host.__mv;
    const i = s.notes.findIndex(n => String(n.id) === String(note.id));
    if (i >= 0) s.notes[i] = note;
    const card = _cardOf(host, note.id);
    if (!card) return;
    _paintAudio(host, card, note);
    // AI 代填 content：欄位還空著、也沒人正在打，才帶上（基準一起對齊，
    // 免得 autosave 把 AI 填的那份又送回去一次）
    const ta = card.querySelector('textarea[data-f="content"]');
    if (ta && !ta.value.trim() && note.content && document.activeElement !== ta) {
        ta.value = note.content;
        ta._asSaved = ta.value;
        autoGrow(ta);
    }
}

function _wire(host) {
    const s = host.__mv;
    const byId = {};
    s.notes.forEach(m => { byId[String(m.id)] = m; });

    host.querySelector('[data-add]').addEventListener('click', async (e) => {
        e.target.disabled = true;
        try {
            await tfetch(_base(s.proposalId), { method: 'POST', json: {} });
            await _load(host);
        } catch (err) {
            alert('新增失敗：' + (err.message || err));
            e.target.disabled = false;
        }
    });

    host.querySelectorAll('[data-del]').forEach(el => {
        el.addEventListener('click', async () => {
            const m = byId[el.dataset.del];
            const what = (m.title || '').trim() || m.met_at || '這一筆';
            if (!confirm(`刪除會議記錄「${what}」？`)) return;
            try {
                await tfetch(`${_base(s.proposalId)}/${encodeURIComponent(el.dataset.del)}`,
                             { method: 'DELETE' });
                await _load(host);
            } catch (e) { alert('刪除失敗：' + (e.message || e)); }
        });
    });

    // 值與「誰建的」在重畫後填回；textarea 隨內容長高（同企劃矩陣的手感）
    host.querySelectorAll('[data-f]').forEach(el => {
        const m = byId[el.dataset.id] || {};
        el.value = m[el.dataset.f] || '';
        if (el.tagName === 'TEXTAREA') {
            autoGrow(el);
            el.addEventListener('input', () => autoGrow(el));
        }
    });
    host.querySelectorAll('.mv-card').forEach(card => {
        const m = byId[card.dataset.card] || {};
        const by = card.querySelector('.mv-by');
        by.textContent = m.created_by
            ? `由 ${m.created_by} 建立於 ${String(m.created_at || '').slice(0, 10)}` : '';
        _paintAudio(host, card, m);
        // 進來時就已經在跑的（別人傳的、或自己上次關掉分頁前傳的）
        if (m.status === 'pending') _poll(host, String(m.id));
    });
    syncBaseline(host, '[data-f]');
}

const STYLE = `
.mv { --mv-ink: #ddd; --mv-sub: #8b8b8b; --mv-line: #2a2a2a; --mv-card: #161616;
      --mv-accent: #c9372c; --mv-err: #f87171; color: var(--mv-ink); }
html.plan-theme-light .mv { --mv-ink: #262626; --mv-sub: #737373; --mv-line: #e5e5e5;
      --mv-card: #fafafa; --mv-accent: #c9372c; --mv-err: #d33; }
.mv * { box-sizing: border-box; }
.mv-gap { flex: 1; }
.mv-note { font-size: 11.5px; color: var(--mv-sub); line-height: 1.7; }
.mv-err { color: var(--mv-err); }
.mv-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.mv-btn { border: 1px solid var(--mv-line); background: none; cursor: pointer;
      color: var(--mv-ink); font: inherit; font-size: 12.5px; padding: 6px 12px; border-radius: 3px; }
.mv-btn:hover:not(:disabled) { border-color: var(--mv-accent); color: var(--mv-accent); }
.mv-btn.primary { border-color: var(--mv-accent); color: var(--mv-accent); }
.mv-btn.sm { font-size: 11.5px; padding: 3px 9px; }
.mv-btn:disabled { opacity: .4; cursor: default; }
.mv-card { border: 1px solid var(--mv-line); border-radius: 3px; background: var(--mv-card);
      padding: 10px 12px; margin-bottom: 10px; }
.mv-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
/* 可編控件：平時像文字，focus 才顯邊框（同基本資料的漸進揭露） */
.mv-in { font: inherit; color: var(--mv-ink); background: transparent;
      border: 1px solid transparent; border-radius: 2px; padding: 4px 6px; outline: none; }
.mv-in:hover { border-color: var(--mv-line); }
.mv-in:focus { border-color: var(--mv-accent); }
.mv-date { flex: none; font-size: 12.5px; color: var(--mv-sub); }
.mv-title { flex: 1; min-width: 160px; font-size: 13.5px; font-weight: 600; }
.mv-att { width: 100%; font-size: 12px; color: var(--mv-sub); }
.mv-body { width: 100%; font-size: 13px; line-height: 1.9; min-height: 88px;
      resize: none; overflow-y: hidden; }
.mv-by { margin-top: 4px; }
/* 錄音區 */
.mv-audio { margin-top: 6px; border-top: 1px dashed var(--mv-line); padding-top: 8px; }
.mv-abar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.mv-aname { word-break: break-all; min-width: 0; }
.mv-fold { margin-top: 6px; }
.mv-fold summary { font-size: 11.5px; color: var(--mv-sub); cursor: pointer;
      user-select: none; }
.mv-pre { border: 1px solid var(--mv-line); border-radius: 3px; margin-top: 4px;
      padding: 10px 12px; font-size: 12.5px; line-height: 1.9;
      white-space: pre-wrap; word-break: break-word; max-height: 320px; overflow-y: auto; }
.mv-spin { width: 12px; height: 12px; border: 2px solid var(--mv-line);
      border-top-color: var(--mv-accent); border-radius: 50%; flex: none;
      animation: mv-rot 1s linear infinite; }
@keyframes mv-rot { to { transform: rotate(360deg); } }
/* 錄音中的紅點（同錄影機的慣例） */
.mv-dot { width: 10px; height: 10px; border-radius: 50%; flex: none;
      background: var(--mv-accent); animation: mv-blink 1.2s ease-in-out infinite; }
@keyframes mv-blink { 50% { opacity: .25; } }
.mv-rt { font-variant-numeric: tabular-nums; }
/* 手機：日期與主題各佔一行，觸控目標 44px（同 proposal-plan 的既有斷點） */
@media (max-width: 720px) {
  .mv-in { font-size: 16px; }
  .mv-title { min-width: 100%; }
  .mv-btn { min-height: 44px; }
}
`;
