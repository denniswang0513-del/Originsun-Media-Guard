import { appendLog, pickPath, setupInputDrop, getAgentBaseUrl, resolveDropPath } from '../../js/shared/utils.js';

let _allEdgeVoices = [];
let _ttsRefAudioPath = '';
let _selectedProfileId = null;

// ─── Init ────────────────────────────────────────────────
export async function initTtsTab() {
    setupInputDrop('tts_output_dir', 'folder');
    setupInputDrop('tts_clone_output_dir', 'folder');
    setupTtsDropzone();
    await loadTtsVoices();
    await checkF5Status();

    // Listen for F5 model download progress
    const socket = window._socket || (window.io && window.io());
    if (socket) {
        socket.on('f5_model_download', (data) => {
            const el = document.getElementById('f5_status_text');
            const btn = document.getElementById('btn_f5_download');
            if (data.phase === 'done') {
                if (el) el.innerHTML = '<span class="text-green-400">模型下載完成！</span>';
                if (btn) btn.classList.add('hidden');
                checkF5Status();
            } else if (data.phase === 'error') {
                if (el) el.innerHTML = `<span class="text-red-400">${data.msg}</span>`;
                if (btn) { btn.disabled = false; btn.textContent = '⬇️ 下載模型'; btn.classList.remove('hidden'); }
            } else {
                if (el) el.innerHTML = `<span class="text-yellow-400">${data.msg || '下載中...'}</span>`;
            }
        });
        socket.on('f5_pip_install', (data) => {
            const el = document.getElementById('f5_status_text');
            const btn = document.getElementById('btn_f5_install');
            if (data.phase === 'done') {
                if (el) el.innerHTML = `<span class="text-green-400">${data.msg}</span>`;
                if (btn) btn.classList.add('hidden');
                checkF5Status();
            } else if (data.phase === 'error') {
                if (el) el.innerHTML = `<span class="text-red-400">${data.msg}</span>`;
                if (btn) { btn.disabled = false; btn.textContent = '📦 安裝套件'; }
            } else {
                if (el) el.innerHTML = `<span class="text-yellow-400">${data.msg || '安裝中...'}</span>`;
            }
        });
    }

    await loadVoiceLibrary();
    await loadDictionary();

    const textInput = document.getElementById('tts_text_input');
    if (textInput) {
        textInput.addEventListener('input', () => {
            const countEl = document.getElementById('tts_char_count');
            if (countEl) countEl.textContent = textInput.value.length;
        });
    }
}

// ─── Sub-Tab Switching ───────────────────────────────────
window.switchTtsSubTab = function(tabId, event) {
    document.querySelectorAll('.tts-subtab').forEach(el => el.classList.add('hidden'));
    document.getElementById('tts_sub_' + tabId)?.classList.remove('hidden');

    const nav = event.currentTarget.parentElement;
    nav.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    event.currentTarget.classList.add('active');
};

// ═══════════════════════════════════════════════════════════
// SUB-TAB 1: Standard TTS (Edge-TTS)
// ═══════════════════════════════════════════════════════════

const LANG_NAMES = {
    'af': '南非語 Afrikaans', 'am': '阿姆哈拉語 Amharic', 'ar': '阿拉伯語 Arabic', 'az': '亞塞拜然語 Azerbaijani',
    'bg': '保加利亞語 Bulgarian', 'bn': '孟加拉語 Bangla', 'bs': '波士尼亞語 Bosnian',
    'ca': '加泰語 Catalan', 'cs': '捷克語 Czech', 'cy': '威爾斯語 Welsh',
    'da': '丹麥語 Danish', 'de': '德語 German', 'el': '希臘語 Greek',
    'en': '英文 English', 'es': '西班牙語 Spanish', 'et': '愛沙尼亞語 Estonian',
    'fa': '波斯語 Persian', 'fi': '芬蘭語 Finnish', 'fil': '菲律賓語 Filipino',
    'fr': '法語 French', 'ga': '愛爾蘭語 Irish', 'gl': '加利西亞語 Galician',
    'gu': '古吉拉特語 Gujarati', 'he': '希伯來語 Hebrew', 'hi': '印地語 Hindi',
    'hr': '克羅埃西亞語 Croatian', 'hu': '匈牙利語 Hungarian', 'hy': '亞美尼亞語 Armenian',
    'id': '印尼語 Indonesian', 'is': '冰島語 Icelandic', 'it': '義大利語 Italian',
    'ja': '日文 Japanese', 'jv': '爪哇語 Javanese', 'ka': '格魯吉亞語 Georgian',
    'kk': '哈薩克語 Kazakh', 'km': '高棉語 Khmer', 'kn': '卡納達語 Kannada',
    'ko': '韓文 Korean', 'lo': '寮國語 Lao', 'lt': '立陶宛語 Lithuanian',
    'lv': '拉脫維亞語 Latvian', 'mk': '馬其頓語 Macedonian', 'ml': '馬拉雅拉姆語 Malayalam',
    'mn': '蒙古語 Mongolian', 'mr': '馬拉地語 Marathi', 'ms': '馬來語 Malay',
    'mt': '馬耳他語 Maltese', 'my': '緬甸語 Burmese', 'nb': '挪威語 Norwegian',
    'ne': '尼泊爾語 Nepali', 'nl': '荷蘭語 Dutch', 'pa': '旁遮普語 Punjabi',
    'pl': '波蘭語 Polish', 'ps': '普什圖語 Pashto', 'pt': '葡萄牙語 Portuguese',
    'ro': '羅馬尼亞語 Romanian', 'ru': '俄語 Russian', 'si': '僧伽羅語 Sinhala',
    'sk': '斯洛伐克語 Slovak', 'sl': '斯洛文尼亞語 Slovenian', 'so': '索馬利語 Somali',
    'sq': '阿爾巴尼亞語 Albanian', 'sr': '塞爾維亞語 Serbian', 'su': '巽他語 Sundanese',
    'sv': '瑞典語 Swedish', 'sw': '斯瓦希里語 Swahili', 'ta': '坦米爾語 Tamil',
    'te': '泰盧固語 Telugu', 'th': '泰語 Thai', 'tr': '土耳其語 Turkish',
    'uk': '烏克蘭語 Ukrainian', 'ur': '烏爾都語 Urdu', 'uz': '烏茲別克語 Uzbek',
    'vi': '越南語 Vietnamese', 'zh': '中文 Chinese', 'zu': '祖魯語 Zulu',
};

const REGION_NAMES = {
    'AE': '阿聯 UAE', 'AF': '阿富汗 AF', 'AL': '阿爾巴尼亞 AL', 'AM': '亞美尼亞 AM', 'AR': '阿根廷 AR', 'AT': '奧地利 AT', 'AU': '澳洲 AU', 'AZ': '亞塞拜然 AZ',
    'BA': '波士尼亞 BA', 'BD': '孟加拉 BD', 'BE': '比利時 BE', 'BG': '保加利亞 BG', 'BH': '巴林 BH', 'BO': '玻利維亞 BO', 'BR': '巴西 BR',
    'CA': '加拿大 CA', 'CH': '瑞士 CH', 'CL': '智利 CL', 'CN': '中國 CN', 'CO': '哥倫比亞 CO', 'CR': '哥斯大黎加 CR', 'CU': '古巴 CU', 'CZ': '捷克 CZ',
    'DE': '德國 DE', 'DK': '丹麥 DK', 'DO': '多明尼加 DO', 'DZ': '阿爾及利亞 DZ',
    'EC': '厄瓜多 EC', 'EE': '愛沙尼亞 EE', 'EG': '埃及 EG', 'ES': '西班牙 ES', 'ET': '衣索比亞 ET',
    'FI': '芬蘭 FI', 'FR': '法國 FR',
    'GB': '英國 GB', 'GE': '喬治亞 GE', 'GQ': '赤道幾內亞 GQ', 'GR': '希臘 GR', 'GT': '瓜地馬拉 GT',
    'HK': '香港 HK', 'HN': '宏都拉斯 HN', 'HR': '克羅埃西亞 HR', 'HU': '匈牙利 HU',
    'ID': '印尼 ID', 'IE': '愛爾蘭 IE', 'IL': '以色列 IL', 'IN': '印度 IN', 'IQ': '伊拉克 IQ', 'IR': '伊朗 IR', 'IS': '冰島 IS', 'IT': '義大利 IT',
    'JO': '約旦 JO', 'JP': '日本 JP',
    'KE': '肯亞 KE', 'KH': '柬埔寨 KH', 'KR': '韓國 KR', 'KW': '科威特 KW', 'KZ': '哈薩克 KZ',
    'LA': '寮國 LA', 'LK': '斯里蘭卡 LK', 'LT': '立陶宛 LT', 'LV': '拉脫維亞 LV', 'LY': '利比亞 LY',
    'MA': '摩洛哥 MA', 'MK': '馬其頓 MK', 'MM': '緬甸 MM', 'MN': '蒙古 MN', 'MT': '馬爾他 MT', 'MX': '墨西哥 MX', 'MY': '馬來西亞 MY',
    'NE': '尼泊爾 NE', 'NG': '奈及利亞 NG', 'NI': '尼加拉瓜 NI', 'NL': '荷蘭 NL', 'NO': '挪威 NO', 'NZ': '紐西蘭 NZ',
    'OM': '阿曼 OM',
    'PA': '巴拿馬 PA', 'PE': '秘魯 PE', 'PH': '菲律賓 PH', 'PK': '巴基斯坦 PK', 'PL': '波蘭 PL', 'PR': '波多黎各 PR', 'PS': '巴勒斯坦 PS', 'PT': '葡萄牙 PT', 'PY': '巴拉圭 PY',
    'QA': '卡達 QA',
    'RO': '羅馬尼亞 RO', 'RS': '塞爾維亞 RS', 'RU': '俄羅斯 RU',
    'SA': '沙烏地 SA', 'SE': '瑞典 SE', 'SG': '新加坡 SG', 'SI': '斯洛維尼亞 SI', 'SK': '斯洛伐克 SK', 'SO': '索馬利亞 SO', 'SV': '薩爾瓦多 SV', 'SY': '敘利亞 SY',
    'TH': '泰國 TH', 'TN': '突尼西亞 TN', 'TR': '土耳其 TR', 'TW': '台灣 TW', 'TZ': '坦尚尼亞 TZ',
    'UA': '烏克蘭 UA', 'US': '美國 US', 'UY': '烏拉圭 UY', 'UZ': '烏茲別克 UZ',
    'VE': '委內瑞拉 VE', 'VN': '越南 VN',
    'YE': '葉門 YE',
    'ZA': '南非 ZA', 'ZU': '祖魯 ZU',
};

async function loadTtsVoices() {
    try {
        const res = await fetch(getAgentBaseUrl() + '/api/v1/tts/voices');
        const data = await res.json();
        _allEdgeVoices = data.voices || [];

        const langs = new Set();
        _allEdgeVoices.forEach(v => langs.add(v.Locale.split('-')[0]));

        const langSelect = document.getElementById('tts_filter_lang');
        if (langSelect) {
            langSelect.innerHTML = '<option value="all">全部語系 (All)</option>';
            const priority = ['zh', 'en', 'ja', 'ko'];
            Array.from(langs).sort((a, b) => {
                const idxA = priority.indexOf(a), idxB = priority.indexOf(b);
                if (idxA !== -1 && idxB !== -1) return idxA - idxB;
                if (idxA !== -1) return -1;
                if (idxB !== -1) return 1;
                return a.localeCompare(b);
            }).forEach(lang => {
                langSelect.innerHTML += `<option value="${lang}">${LANG_NAMES[lang] || lang.toUpperCase()} ${lang.toUpperCase()}</option>`;
            });
            if (langs.has('zh')) langSelect.value = 'zh';
        }
        window.updateTtsFilterRegions();
        window.updateTtsVoices();
    } catch (e) {
        console.error("Failed to load TTS voices:", e);
        const voiceSelect = document.getElementById('tts_voice');
        if (voiceSelect) voiceSelect.innerHTML = '<option value="zh-TW-HsiaoChenNeural">台灣 小晨 (HsiaoChen) — 女聲</option>';
    }
}

window.updateTtsFilterRegions = function() {
    const lang = document.getElementById('tts_filter_lang')?.value || 'all';
    const regionSelect = document.getElementById('tts_filter_region');
    if (!regionSelect) return;
    regionSelect.innerHTML = '<option value="all">全部區域</option>';
    if (lang === 'all') return;
    const regions = new Set();
    _allEdgeVoices.forEach(v => { const p = v.Locale.split('-'); if (p[0] === lang && p.length > 1) regions.add(p[1]); });
    Array.from(regions).sort().forEach(r => { regionSelect.innerHTML += `<option value="${r}">${REGION_NAMES[r] || r}</option>`; });
};

window.updateTtsVoices = function() {
    const lang = document.getElementById('tts_filter_lang')?.value || 'all';
    const region = document.getElementById('tts_filter_region')?.value || 'all';
    const gender = document.getElementById('tts_filter_gender')?.value || 'all';
    const voiceSelect = document.getElementById('tts_voice');
    if (!voiceSelect) return;
    let filtered = _allEdgeVoices;
    if (lang !== 'all') filtered = filtered.filter(v => v.Locale.startsWith(lang + '-'));
    if (region !== 'all') filtered = filtered.filter(v => v.Locale.endsWith('-' + region));
    if (gender !== 'all') filtered = filtered.filter(v => v.Gender === gender);
    voiceSelect.innerHTML = '';
    if (filtered.length === 0) { voiceSelect.innerHTML = '<option value="">(無符合條件的聲音)</option>'; return; }
    filtered.forEach(v => {
        const shortName = (v.FriendlyName || v.ShortName).replace(/^Microsoft\s+/, '').replace(/\s+Online\s+\(Natural\).*$/, '').trim();
        const rc = v.Locale.split('-')[1] || '';
        voiceSelect.innerHTML += `<option value="${v.ShortName}">${REGION_NAMES[rc] || rc} - ${shortName} (${v.Gender === 'Female' ? '女聲' : '男聲'})</option>`;
    });
    const twVoice = filtered.find(v => v.Locale === 'zh-TW');
    if (twVoice) voiceSelect.value = twVoice.ShortName;
};

window.previewTtsVoice = function() {
    const voice = document.getElementById('tts_voice')?.value;
    if (!voice) return;
    const btn = document.getElementById('btn_tts_preview');
    if (btn) { btn.disabled = true; btn.textContent = '載入中'; }
    const audio = new Audio(getAgentBaseUrl() + `/api/v1/tts/preview?voice=${voice}&text=${encodeURIComponent("您好，這是語音試聽。")}`);
    audio.play().then(() => {
        if (btn) btn.textContent = '播放中';
        audio.onended = () => { if (btn) { btn.textContent = '試聽'; btn.disabled = false; } };
    }).catch(e => {
        alert("試聽失敗: " + e.message);
        if (btn) { btn.textContent = '試聽'; btn.disabled = false; }
    });
};

window.calculateTtsDuration = async function() {
    const textInput = document.getElementById('tts_text_input');
    const btn = document.getElementById('tts_calc_btn');
    const durSpan = document.getElementById('tts_est_duration');
    const rateSpan = document.getElementById('tts_est_rate');
    const text = textInput ? textInput.value.trim() : '';
    if (!text) { alert('請先輸入你要生成的文字！'); return; }
    const voice = document.getElementById('tts_voice')?.value || 'zh-TW-HsiaoChenNeural';
    const rateUrl = document.getElementById('tts_rate')?.value || '0';
    const pitchUrl = document.getElementById('tts_pitch')?.value || '0';
    const rateStr = (rateUrl >= 0 ? '+' : '') + rateUrl + '%';
    const pitchStr = (pitchUrl >= 0 ? '+' : '') + pitchUrl + 'Hz';
    if (btn) { btn.disabled = true; btn.innerHTML = `<svg class="animate-spin -ml-1 mr-2 h-3.5 w-3.5 text-white" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> 計算中...`; }
    try {
        const res = await fetch(getAgentBaseUrl() + '/api/v1/tts/estimate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, voice, rate: rateStr, pitch: pitchStr }) });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        const totalSecs = data.duration_seconds || 0;
        const mins = Math.floor(totalSecs / 60), secs = Math.floor(totalSecs % 60), ms = Math.floor((totalSecs - Math.floor(totalSecs)) * 10);
        if (durSpan) durSpan.textContent = `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}.${ms}`;
        if (rateSpan) rateSpan.textContent = `${data.chars_per_second} 字/秒`;
    } catch (e) { console.error("Estimation failed:", e); if (durSpan) durSpan.textContent = "Error"; }
    finally { if (btn) { btn.disabled = false; btn.innerHTML = `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg> 計算音檔時長`; } }
};

window.pickTtsOutputDir = function() { pickPath('tts_output_dir', 'folder'); };

// ─── Standard TTS Collect + Submit ───────────────────────
window.collectTtsPayload = function() {
    const text = document.getElementById('tts_text_input')?.value?.trim();
    const outputDir = document.getElementById('tts_output_dir')?.value?.trim();
    const outputName = document.getElementById('tts_output_name')?.value?.trim() || 'tts_output';
    if (!text) { alert('請輸入要生成的文字'); return { valid: false }; }
    if (!outputDir) { alert('請選擇輸出目錄'); return { valid: false }; }

    const payload = {
        text, output_dir: outputDir, output_name: outputName,
        voice: document.getElementById('tts_voice')?.value || 'zh-TW-HsiaoChenNeural',
        rate: parseInt(document.getElementById('tts_rate')?.value || '0'),
        pitch: parseInt(document.getElementById('tts_pitch')?.value || '0')
    };

    return { valid: true, payload, name: 'TTS 語音生成' };
};

window.submitTtsJob = async function() {
    const collected = window.collectTtsPayload();
    if (!collected.valid) return;
    await _runWithProgress('tts', getAgentBaseUrl() + '/api/v1/tts_jobs', collected.payload, 'btn_start_tts');
};

// ═══════════════════════════════════════════════════════════
// SUB-TAB 2: Voice Clone (F5-TTS)
// ═══════════════════════════════════════════════════════════

async function checkF5Status() {
    try {
        const res = await fetch(getAgentBaseUrl() + '/api/v1/tts/f5_status');
        const data = await res.json();
        const el = document.getElementById('f5_status_text');
        const banner = document.getElementById('f5_status_banner');
        const pkgOk = data.available;
        const modelLocal = data.model_ready;
        const modelCached = data.model_cached;

        // Ensure action buttons exist
        let btn = document.getElementById('btn_f5_download');
        if (!btn && banner) {
            btn = document.createElement('button');
            btn.id = 'btn_f5_download';
            btn.className = 'hidden bg-orange-600 hover:bg-orange-500 text-white text-xs px-3 py-1 rounded';
            btn.onclick = () => window.downloadF5Model();
            banner.appendChild(btn);
        }
        let installBtn = document.getElementById('btn_f5_install');
        if (!installBtn && banner) {
            installBtn = document.createElement('button');
            installBtn.id = 'btn_f5_install';
            installBtn.className = 'hidden bg-blue-600 hover:bg-blue-500 text-white text-xs px-3 py-1 rounded ml-2';
            installBtn.textContent = '📦 安裝套件';
            installBtn.onclick = () => window.installF5Package();
            banner.appendChild(installBtn);
        }

        // Determine banner state
        if (modelLocal && pkgOk) {
            // Fully ready — subtle inline text, no background
            if (el) el.innerHTML = '<span class="text-gray-600">● F5-TTS 已就緒</span>';
            if (btn) btn.classList.add('hidden');
            if (installBtn) installBtn.classList.add('hidden');
            if (banner) {
                banner.className = 'flex items-center justify-between text-xs mb-3 px-1';
            }
        } else {
            // Something missing — show prominent banner with action buttons
            const msgs = [];
            if (!pkgOk) msgs.push('套件未安裝');
            if (!modelLocal && modelCached) msgs.push('模型存在於快取，需複製到 models 資料夾');
            else if (!modelLocal) msgs.push('模型尚未下載（約 1.2 GB）');

            if (el) el.innerHTML = `<span class="text-orange-400">${msgs.join(' ｜ ')}</span>`;

            // Show install button if package missing
            if (!pkgOk) {
                if (installBtn) installBtn.classList.remove('hidden');
            } else {
                if (installBtn) installBtn.classList.add('hidden');
            }

            // Show download/copy button if model not in local
            if (!modelLocal) {
                if (btn) {
                    btn.classList.remove('hidden');
                    btn.textContent = modelCached ? '📂 複製到 models' : '⬇️ 下載模型';
                }
            } else {
                if (btn) btn.classList.add('hidden');
            }
            if (banner) {
                banner.className = 'bg-[#1a2a3a] border border-orange-600 rounded-lg px-4 py-3 flex items-center justify-between text-sm mb-3';
            }
        }
    } catch { const el = document.getElementById('f5_status_text'); if (el) el.textContent = '無法取得 F5-TTS 狀態'; }
}

async function downloadF5Model() {
    const btn = document.getElementById('btn_f5_download');
    const el = document.getElementById('f5_status_text');
    if (btn) { btn.disabled = true; btn.textContent = '下載中...'; }
    if (el) el.innerHTML = '<span class="text-yellow-400">正在下載 F5-TTS 模型，請稍候...</span>';
    try {
        const res = await fetch(getAgentBaseUrl() + '/api/v1/tts/f5_download', { method: 'POST' });
        if (!res.ok) {
            const err = await res.json();
            if (el) el.innerHTML = `<span class="text-red-400">下載失敗: ${err.detail || '未知錯誤'}</span>`;
            if (btn) { btn.disabled = false; btn.textContent = '⬇️ 下載模型'; }
        }
    } catch (e) {
        if (el) el.innerHTML = `<span class="text-red-400">下載請求失敗: ${e.message}</span>`;
        if (btn) { btn.disabled = false; btn.textContent = '⬇️ 下載模型'; }
    }
}
window.downloadF5Model = downloadF5Model;

async function installF5Package() {
    const btn = document.getElementById('btn_f5_install');
    const el = document.getElementById('f5_status_text');
    if (btn) { btn.disabled = true; btn.textContent = '安裝中...'; }
    if (el) el.innerHTML = '<span class="text-yellow-400">正在安裝 f5-tts 套件，這可能需要幾分鐘...</span>';
    try {
        const res = await fetch(getAgentBaseUrl() + '/api/v1/tts/f5_install', { method: 'POST' });
        if (!res.ok) {
            const err = await res.json();
            if (el) el.innerHTML = `<span class="text-red-400">安裝失敗: ${err.detail || '未知錯誤'}</span>`;
            if (btn) { btn.disabled = false; btn.textContent = '📦 安裝套件'; }
        }
    } catch (e) {
        if (el) el.innerHTML = `<span class="text-red-400">安裝請求失敗: ${e.message}</span>`;
        if (btn) { btn.disabled = false; btn.textContent = '📦 安裝套件'; }
    }
}
window.installF5Package = installF5Package;

// ─── Reference Audio ─────────────────────────────────────
async function setupTtsDropzone() {
    const zone = document.getElementById('tts_clone_dropzone');
    if (!zone) return;
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('border-blue-500'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('border-blue-500'));
    zone.addEventListener('drop', async e => {
        e.preventDefault(); zone.classList.remove('border-blue-500');
        const files = e.dataTransfer.files;
        if (files.length > 0) { const path = await resolveDropPath(e, files[0]); if (path) setTtsRefAudioPath(path); }
    });
}

function setTtsRefAudioPath(path) {
    _ttsRefAudioPath = path;
    const name = path.split(/[\\/]/).pop();
    const displayEl = document.getElementById('tts_ref_audio_path_display');
    if (displayEl) displayEl.value = path;
    const info = document.getElementById('tts_ref_info');
    const nameEl = document.getElementById('tts_ref_name');
    if (info) info.classList.remove('hidden');
    if (nameEl) { nameEl.textContent = ` ${name}`; nameEl.title = path; }
    _selectedProfileId = null;
    document.getElementById('tts_selected_profile_info')?.classList.add('hidden');
}

window.pickTtsReferenceNative = async function() {
    await pickPath('tts_ref_audio_path_display', 'file');
    const path = document.getElementById('tts_ref_audio_path_display')?.value;
    if (path) setTtsRefAudioPath(path);
};

window.clearTtsRef = function() {
    _ttsRefAudioPath = '';
    const displayEl = document.getElementById('tts_ref_audio_path_display');
    if (displayEl) displayEl.value = '';
    document.getElementById('tts_ref_info')?.classList.add('hidden');
};

// ─── Voice Library ───────────────────────────────────────
async function loadVoiceLibrary() {
    const list = document.getElementById('tts_library_list');
    if (!list) return;
    try {
        const res = await fetch(getAgentBaseUrl() + '/api/v1/voice_profiles');
        const profiles = await res.json();
        if (!profiles.length) { list.innerHTML = '<p class="text-gray-500 text-sm text-center py-4">NAS 聲音庫是空的。</p>'; return; }
        list.innerHTML = profiles.map(p => `
          <div class="flex items-center justify-between bg-[#1e1e1e] border border-[#3a3a3a] rounded-lg px-3 py-2 text-sm">
            <div>
              <span class="font-semibold text-white">${p.name}</span>
              <span class="text-gray-500 text-xs ml-2">${p.description || ''}</span>
              ${p.cached_locally ? '<span class="ml-2 text-xs text-green-400">已快取</span>' : ''}
            </div>
            <div class="flex gap-2">
              ${!p.cached_locally ? `<button onclick="cacheProfile('${p.id}')" class="bg-[#1f538d] hover:bg-[#2a6cbf] text-white text-xs py-1 px-2 rounded transition-colors">快取</button>` : ''}
              <button onclick="selectProfile('${p.id}','${p.name}')" class="bg-[#333] hover:bg-[#444] text-white text-xs py-1 px-2 rounded transition-colors">選用</button>
              <button onclick="deleteProfile('${p.id}')" class="text-red-400 hover:text-red-300 text-xs px-1">刪除</button>
            </div>
          </div>`).join('');
    } catch { list.innerHTML = '<p class="text-red-400 text-sm text-center py-4">無法連線至 NAS 聲音庫</p>'; }
}

window.selectProfile = function(id, name) {
    _selectedProfileId = id;
    const info = document.getElementById('tts_selected_profile_info');
    const nameEl = document.getElementById('tts_selected_profile_name');
    if (info) info.classList.remove('hidden');
    if (nameEl) nameEl.textContent = name;
    window.clearTtsRef();
};

window.cacheProfile = async function(id) {
    try { await fetch(getAgentBaseUrl() + `/api/v1/voice_profiles/${id}/cache`, { method: 'POST' }); await loadVoiceLibrary(); }
    catch(e) { alert('快取失敗: ' + e.message); }
};

window.deleteProfile = async function(id) {
    if (!confirm('確定要刪除這個聲音角色嗎？')) return;
    try { await fetch(getAgentBaseUrl() + `/api/v1/voice_profiles/${id}`, { method: 'DELETE' }); await loadVoiceLibrary(); }
    catch(e) { alert('刪除失敗: ' + e.message); }
};

window.saveTtsToLibrary = async function() {
    if (!_ttsRefAudioPath) { alert('請先選取參考音訊'); return; }
    const name = prompt('請為這個聲音角色命名：');
    if (!name) return;
    const desc = prompt('描述（可留空）：') || '';
    try {
        const res = await fetch(getAgentBaseUrl() + '/api/v1/voice_profiles', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, description: desc, reference_audio: _ttsRefAudioPath, language: 'zh' })
        });
        const data = await res.json();
        if (data.id) { alert(`已存入 NAS 聲音庫：${name}`); await loadVoiceLibrary(); }
    } catch(e) { alert('儲存失敗: ' + e.message); }
};

window.addVoiceToLibrary = function() { window.pickTtsReferenceNative(); };

// ─── Clone Collect + Submit (Socket.IO real-time progress) ─────────
window.collectClonePayload = function() {
    const text = document.getElementById('tts_clone_text_input')?.value?.trim();
    const outputDir = document.getElementById('tts_clone_output_dir')?.value?.trim();
    const outputName = document.getElementById('tts_clone_output_name')?.value?.trim() || 'clone_output';
    if (!text) { alert('請輸入要生成的文字'); return { valid: false }; }
    if (!outputDir) { alert('請選擇輸出目錄'); return { valid: false }; }
    if (!_ttsRefAudioPath && !_selectedProfileId) { alert('請先選擇參考音訊或 NAS 聲音角色'); return { valid: false }; }

    let refAudio = _ttsRefAudioPath;
    if (!refAudio && _selectedProfileId) {
        alert('請先將選取的 NAS 角色「快取至本機」，然後使用臨時複製區選取本機快取的音檔。');
        return { valid: false };
    }

    const rateVal = parseInt(document.getElementById('tts_clone_speed')?.value || '0', 10);
    const speed = 1.0 + rateVal / 100.0;
    const pitch = parseInt(document.getElementById('tts_clone_pitch')?.value || '0', 10);
    const refText = document.getElementById('tts_clone_ref_text')?.value?.trim() || null;
    const payload = { text, reference_audio: refAudio, output_dir: outputDir, output_name: outputName, speed, pitch, ref_text: refText };

    return { valid: true, payload, name: '聲音複製' };
};

window.submitCloneJob = async function() {
    const collected = window.collectClonePayload();
    if (!collected.valid) return;

    const text = collected.payload.text;
    const outputDir = collected.payload.output_dir;
    const outputName = collected.payload.output_name;
    let refAudio = collected.payload.reference_audio;

    const btn = document.getElementById('btn_start_clone');
    const progArea = document.getElementById('tts_clone_progress_area');
    const progLabel = document.getElementById('tts_clone_prog_label');
    const progBar = document.getElementById('tts_clone_prog_bar');
    const progPct = document.getElementById('tts_clone_prog_pct');

    // Show progress area + disable button
    if (progArea) progArea.classList.remove('hidden');
    if (progLabel) progLabel.textContent = '準備中...';
    if (progBar) { progBar.style.width = '2%'; progBar.style.background = 'linear-gradient(90deg, #8b5cf6, #a78bfa)'; progBar.classList.add('animate-pulse'); }
    if (progPct) progPct.textContent = '2%';
    if (btn) { btn.disabled = true; btn.classList.add('opacity-50', 'cursor-not-allowed'); }

    // Listen for Socket.IO progress events from backend
    const socket = window._socket || io();
    const _phaseLabels = {
        preparing: '🔧 正在準備參考音訊...',
        transcribing: '🎤 正在轉錄參考音訊...',
        loading_model: '📦 正在載入 F5-TTS 模型（首次約需 30 秒）...',
        inferring: '🧠 正在生成語音，請耐心等候...',
        pitch_shift: '🎵 正在調整音高...',
        done: '✅ 生成完成！',
        error: '❌ 生成失敗'
    };

    function _onCloneProgress(data) {
        const pct = data.pct || 0;
        const label = _phaseLabels[data.phase] || data.msg || '處理中...';
        if (progBar) progBar.style.width = pct + '%';
        if (progPct) progPct.textContent = pct + '%';
        if (progLabel) progLabel.textContent = label;

        if (data.phase === 'inferring') {
            // Pulsing animation during inference (the long wait)
            if (progBar) progBar.classList.add('animate-pulse');
        }

        if (data.phase === 'done') {
            socket.off('tts_clone_progress', _onCloneProgress);
            if (progBar) { progBar.style.width = '100%'; progBar.style.background = 'linear-gradient(90deg, #059669, #10b981)'; progBar.classList.remove('animate-pulse'); }
            if (progPct) progPct.textContent = '100%';
            const outName = data.output ? data.output.split(/[\\/]/).pop() : '';
            if (progLabel) progLabel.innerHTML = `✅ 完成！儲存至: <span class="text-green-400 font-mono">${outName}</span>`;
            if (btn) { btn.disabled = false; btn.classList.remove('opacity-50', 'cursor-not-allowed'); }
        } else if (data.phase === 'error') {
            socket.off('tts_clone_progress', _onCloneProgress);
            if (progBar) { progBar.style.width = '100%'; progBar.style.background = 'linear-gradient(90deg, #ef4444, #f87171)'; progBar.classList.remove('animate-pulse'); }
            if (progPct) progPct.textContent = 'Err';
            if (progLabel) progLabel.textContent = `❌ 失敗：${data.msg || '未知錯誤'}`;
            if (btn) { btn.disabled = false; btn.classList.remove('opacity-50', 'cursor-not-allowed'); }
        }
    }

    socket.on('tts_clone_progress', _onCloneProgress);

    // POST (returns immediately with "queued" status)
    try {
        const payload = collected.payload;
        const res = await fetch(getAgentBaseUrl() + '/api/v1/tts_jobs/clone', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            socket.off('tts_clone_progress', _onCloneProgress);
            if (progBar) { progBar.style.width = '100%'; progBar.style.background = 'linear-gradient(90deg, #ef4444, #f87171)'; progBar.classList.remove('animate-pulse'); }
            if (progPct) progPct.textContent = 'Err';
            if (progLabel) progLabel.textContent = `❌ 失敗：${errData.detail || res.statusText}`;
            if (btn) { btn.disabled = false; btn.classList.remove('opacity-50', 'cursor-not-allowed'); }
        }
    } catch(e) {
        socket.off('tts_clone_progress', _onCloneProgress);
        if (progBar) { progBar.style.width = '100%'; progBar.style.background = 'linear-gradient(90deg, #ef4444, #f87171)'; progBar.classList.remove('animate-pulse'); }
        if (progPct) progPct.textContent = 'Err';
        if (progLabel) progLabel.textContent = `❌ 網路錯誤：${e.message}`;
        if (btn) { btn.disabled = false; btn.classList.remove('opacity-50', 'cursor-not-allowed'); }
    }
};

// ═══════════════════════════════════════════════════════════
// SUB-TAB 3: Dictionary Editor
// ═══════════════════════════════════════════════════════════

let _dictData = { vocab_mapping: {}, pronunciation_hacks: {} };

window.loadDictionary = async function() {
    try {
        const res = await fetch(getAgentBaseUrl() + '/api/v1/tts/dictionary');
        _dictData = await res.json();
        _renderDictList('vocab', _dictData.vocab_mapping || {});
        _renderDictList('pron', _dictData.pronunciation_hacks || {});
        _showDictStatus('已載入字典', 'text-green-400 bg-[#1a2a1a] border border-[#3a6a3a]');
    } catch (e) {
        _showDictStatus('載入失敗: ' + e.message, 'text-red-400 bg-[#2a1a1a] border border-[#6a3a3a]');
    }
};

window.saveDictionary = async function() {
    // Collect from DOM
    const vocab = _collectDictRows('vocab');
    const pron = _collectDictRows('pron');
    const payload = { vocab_mapping: vocab, pronunciation_hacks: pron };
    try {
        const res = await fetch(getAgentBaseUrl() + '/api/v1/tts/dictionary', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.status === 'ok') {
            _dictData = payload;
            _showDictStatus('字典已儲存！', 'text-green-400 bg-[#1a2a1a] border border-[#3a6a3a]');
        } else { throw new Error(data.detail || '未知錯誤'); }
    } catch (e) { _showDictStatus('儲存失敗: ' + e.message, 'text-red-400 bg-[#2a1a1a] border border-[#6a3a3a]'); }
};

window.addDictRow = function(type) {
    const list = document.getElementById(type === 'vocab' ? 'dict_vocab_list' : 'dict_pron_list');
    if (!list) return;
    list.insertAdjacentHTML('beforeend', _dictRowHtml(type, '', ''));
};

window.removeDictRow = function(btn) { btn.closest('.dict-row').remove(); };

function _renderDictList(type, mapping) {
    const list = document.getElementById(type === 'vocab' ? 'dict_vocab_list' : 'dict_pron_list');
    if (!list) return;
    list.innerHTML = '';
    for (const [key, val] of Object.entries(mapping)) {
        list.insertAdjacentHTML('beforeend', _dictRowHtml(type, key, val));
    }
}

function _dictRowHtml(type, key, val) {
    const label1 = type === 'vocab' ? '原詞' : '原詞';
    const label2 = type === 'vocab' ? '替換為' : '讀音';
    return `<div class="dict-row flex items-center gap-2">
      <input type="text" value="${_escHtml(key)}" placeholder="${label1}" class="dict-key flex-1 bg-[#1e1e1e] border border-[#444] rounded px-2 py-1 text-sm text-white focus:border-blue-500 focus:outline-none">
      <span class="text-gray-500 text-xs">→</span>
      <input type="text" value="${_escHtml(val)}" placeholder="${label2}" class="dict-val flex-1 bg-[#1e1e1e] border border-[#444] rounded px-2 py-1 text-sm text-white focus:border-blue-500 focus:outline-none">
      <button onclick="removeDictRow(this)" class="text-red-400 hover:text-red-300 text-xs px-1 font-bold">✕</button>
    </div>`;
}

function _collectDictRows(type) {
    const list = document.getElementById(type === 'vocab' ? 'dict_vocab_list' : 'dict_pron_list');
    const result = {};
    if (!list) return result;
    list.querySelectorAll('.dict-row').forEach(row => {
        const key = row.querySelector('.dict-key')?.value?.trim();
        const val = row.querySelector('.dict-val')?.value?.trim();
        if (key && val) result[key] = val;
    });
    return result;
}

function _escHtml(s) { return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

function _showDictStatus(msg, cls) {
    const el = document.getElementById('dict_save_status');
    if (!el) return;
    el.className = 'mt-2 text-center text-sm py-2 rounded ' + cls;
    el.textContent = msg;
    el.classList.remove('hidden');
    setTimeout(() => el.classList.add('hidden'), 3000);
}

// ═══════════════════════════════════════════════════════════
// Shared Progress Helper
// ═══════════════════════════════════════════════════════════

async function _runWithProgress(prefix, endpoint, payload, btnId) {
    const progArea = document.getElementById(prefix + '_progress_area');
    const progLabel = document.getElementById(prefix + '_prog_label');
    const progBar = document.getElementById(prefix + '_prog_bar');
    const progPct = document.getElementById(prefix + '_prog_pct');
    const btn = document.getElementById(btnId);

    if (progArea) progArea.classList.remove('hidden');
    if (progLabel) progLabel.textContent = '正在產生語音...';
    if (progBar) { progBar.style.width = '2%'; progBar.style.background = prefix === 'tts_clone' ? 'linear-gradient(90deg, #8b5cf6, #a78bfa)' : 'linear-gradient(90deg, #3b82f6, #60a5fa)'; }
    if (progPct) progPct.textContent = '2%';

    let currentPct = 2;
    const iv = setInterval(() => { if (currentPct < 95) { currentPct += (99 - currentPct) * 0.05; const d = Math.round(currentPct); if (progBar) progBar.style.width = d + '%'; if (progPct) progPct.textContent = d + '%'; } }, 500);
    if (btn) { btn.disabled = true; btn.classList.add('opacity-50', 'cursor-not-allowed'); }

    try {
        const res = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        const data = await res.json();
        clearInterval(iv);
        if (res.ok) {
            if (progBar) { progBar.style.width = '100%'; progBar.style.background = 'linear-gradient(90deg, #059669, #10b981)'; }
            if (progPct) progPct.textContent = '100%';
            if (progLabel) { const outName = data.output ? data.output.split(/[\\/]/).pop() : ''; progLabel.innerHTML = `完成！儲存至: <span class="text-green-400 font-mono">${outName}</span>`; }
        } else { throw new Error(data.detail || '未知的後端錯誤'); }
    } catch(e) {
        clearInterval(iv);
        if (progBar) { progBar.style.width = '100%'; progBar.style.background = 'linear-gradient(90deg, #ef4444, #f87171)'; }
        if (progPct) progPct.textContent = 'Err';
        if (progLabel) progLabel.textContent = `失敗：${e.message}`;
    } finally { if (btn) { btn.disabled = false; btn.classList.remove('opacity-50', 'cursor-not-allowed'); } }
}
