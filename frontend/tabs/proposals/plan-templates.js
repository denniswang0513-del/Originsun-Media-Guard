/**
 * plan-templates.js — 提案企劃方法論模板（純資料，無邏輯）
 *
 * 單一事實來源：docs/PROPOSAL_PLANNER.md（§2 方法論全文、§5/§8 決策紀錄）。
 * 模板改動時 version 進版並保留舊版（plan 依 template_version 取版渲染）；
 * 純顯示用資料放前端（後端只存 blob）— 日後要後端匯出再搬 core/。
 *
 * 結構鐵則（違反=推翻已定決策，見 doc）：
 *  - hows 標籤全域統一，不隨視角改（alt_label 只是投影片版說法，預設不顯示）
 *  - 「四個方向」的提問＝拍什麼欄四格的提示問句（owner 2026-07-28 確認），
 *    不是矩陣之外的獨立區塊 — 擬腳本的思考就發生在拍什麼欄裡
 *  - premises 有 kind（psychology/decision）且張數不固定
 *  - 沒有 include/exclude、沒有完成度檢核 — 空白格是合法狀態
 */

export const PLAN_TEMPLATES = {
    child_program_huang: {
        id: 'child_program_huang',
        label: '兒童節目的製作方法（黃鴻儒）',
        source: '2026-07-28 課堂分享「以兒童為中心的思考」',
        version: '1.0.0',
        theme_label: 'THEME',
        theme_placeholder: '例：六歲小孩去溪裡玩紙船／登山總動員（10歲）— 建議帶上年齡',
        hows_label: '四個提問',
        hows: [
            { key: 'fit',       label: '找到合適的兒童', alt_label: '介紹合適的兒童', hint: '選角 — 這件事需要誰' },
            { key: 'goal',      label: '建立共同目標',   alt_label: '說明共同目標',   hint: '動機 — 讓他覺得這是「他的」目標' },
            { key: 'process',   label: '發展流程',       alt_label: '呈現流程',       hint: '執行 — 他能自己決定什麼、哪裡會失去耐性' },
            { key: 'highlight', label: '亮點',           alt_label: '營造亮點',       hint: '高潮 — 最棒的一刻' },
        ],
        lenses: [
            {
                key: 'participant',
                label: '參與的兒童',
                lead: '參與的兒童會想什麼呢？',
                motto: '只想一件事：怎麼讓這個孩子帶著熱情，真的產生行動',
                premises: [
                    { kind: 'psychology', title: '只做想做的事', body: '他只想做感興趣，或是被誘發興趣的事情。' },
                    { kind: 'psychology', title: '想被肯定', body: '他希望有自信，由自己作主，而不是被指揮。' },
                    { kind: 'psychology', title: '討厭等待／無聊', body: '等待、重來、反覆講解，都是他放棄的時刻。' },
                ],
                fields: [],
                prompts: {
                    fit: '這件事需要什麼樣的孩子？\n你打算怎麼找？\n如何確認他適合？',
                    goal: '什麼是兒童能處理的目標？\n怎麼讓他覺得這是「他的目標」？\n他想完成或證明什麼？',
                    process: '環境能提供什麼？\n有什麼是他能自己決定、自己動手？\n流程哪一段最可能讓他不耐煩？',
                    highlight: '他覺得最棒的一刻會是什麼？\n什麼能讓他真正興奮？',
                },
            },
            {
                key: 'viewer',
                label: '觀看的兒童',
                lead: '螢幕前的兒童想看什麼？',
                motto: '當然，說一個好故事，是必須的',
                premises: [
                    { kind: 'psychology', title: '想看沒看過的', body: '陌生的地方、沒見過的做法，才留得住他的眼睛。' },
                    { kind: 'psychology', title: '想知道為什麼', body: '他要理由，不要結論。過程比答案更有吸引力。' },
                    { kind: 'psychology', title: '想「如果我也可以」', body: '他要能想像自己站在那裡。' },
                ],
                fields: [],
                prompts: {
                    fit: '同齡觀眾會好奇他什麼？\n他擁有什麼吸引人的特質？',
                    goal: '這個目標如何簡單說清楚？\n如何讓觀眾跟著期待？',
                    process: '流程裡哪些是觀眾沒看過的？\n哪裡讓他想知道「為什麼」？',
                    highlight: '哪一刻會讓觀眾想「如果是我，也想要…」？\n什麼時刻會讓觀眾驚呼？',
                },
            },
            {
                key: 'content',
                label: '拍什麼',
                alt_label: '腳本 × 拍攝',
                lead: '拍攝內容要怎麼設計？',
                motto: '每一題都不是取捨，是要想清楚的問題 — 答案匯集，形成這個節目的樣貌',
                premises: [
                    { kind: 'decision', title: '主角在意什麼？觀眾在意什麼？', body: '兩者不一定一致。當它們衝突時，你站哪一邊？這個選擇會決定整集的語氣。' },
                    { kind: 'decision', title: '百分百真實重要嗎？底限是什麼？', body: '重來、引導、設計，哪些可以做、哪些不能做？先把底限講清楚，現場才不會臨時鬆動。' },
                ],
                fields: [
                    { key: 'minutes', label: '影片長度', placeholder: '例：6 mins（選填）' },
                    { key: 'tone', label: '基調', placeholder: '整集的語氣（選填）' },
                ],
                // 拍什麼欄的提示問句＝擬腳本「四個方向」的提問（owner 確認）。
                // 每題都不是取捨，是要想清楚的問題 — 答案匯集形成節目的樣貌。
                prompts: {
                    fit: '你需要建立這個角色嗎？',
                    goal: '你需要讓觀眾清晰了解你的目標嗎？',
                    process: '需要清晰的講解說明流程嗎？',
                    highlight: '這一集經營的亮點，是觀眾想看、現場小朋友也做得很開心？',
                },
            },
        ],
    },
};

/** 內建範例（doc §2.5 紙船練習 / §2.6 登山總動員真實案）— 唯讀展示，不落庫 */
export const PLAN_EXAMPLES = [
    {
        label: '六歲小孩去溪裡玩紙船（課堂練習）',
        template_id: 'child_program_huang',
        template_version: '1.0.0',
        theme: '六歲小孩去溪裡玩紙船',
        field_values: { content: { minutes: '6 mins' } },
        cells: {
            participant: {
                fit: '1. 特質：喜歡大自然的孩子、願意手作、不怕髒\n2. 怎麼找（增加命中機率）：去拍攝地點找（蹲點成本大）、營隊、科展、徵選',
                goal: '如何讓小朋友的目標（內在動力）跟節目目標一致：設計目標or任務？\n觸發事件（意料&意外）\neg. 競賽',
                process: '1. 製作不同材質的紙船、競賽\n2. 認識溪邊環境：提升自主力、就地取材\n3. 輔助角色（有可能是成人）：引導衝突\n4. 節目素材是否豐富：進階版',
                highlight: '1. 船飄起來的時刻：船怎麼樣飄起來（嘗試＆失敗＆成功）\n2. 紙船抵達最終想要去的地方：成功抵達的成就感\n3. 競賽：設計紙船戰力（材質、折法、排除障礙）',
            },
            viewer: {
                fit: '1. 外顯、外表的認同：性別、穿著\n2. 內在特質：專注神情、樂於分享（率先發言）、要有不同的角色所長',
                goal: '摺紙的過程跟結果\n建立從A點到B點\n未知的結果',
                process: '折法、材質、環境、創意（特質）、示範、競賽方式（怎麼比）',
                highlight: '1. 分工：賽道、船\n2. 失敗＆成功\n3. 物理知識\n4. 船的主觀視角（船長）',
            },
            content: {
                fit: 'X（屬於前置）',
                goal: '1. 需不需要建立角色？\n2. 片頭：介紹A到B（動畫）\n3. 示範帶：船的主觀視角',
                process: '環境\n材質：葉子、紙、樹枝\n競賽（怎麼比）',
                highlight: '1. 創造緊張感、懸疑感',
            },
        },
    },
    {
        label: '登山總動員（10歲）（真實節目）',
        template_id: 'child_program_huang',
        template_version: '1.0.0',
        theme: '登山總動員（10歲）',
        cells: {
            participant: {
                fit: '以攀樹為例\n人際網絡連結：嚮導的孩子\n與山林的關係是熟悉的',
                goal: '線上聊天-實體見面（體驗與討論）-行前準備（一起練習）-線上會議（旅行團出發）',
                process: '找出敘事線：獨立找樹、架繩上樹\n找出核心：對兩人的挑戰/新的東西\n熟悉場域',
                highlight: 'sth new：獨立完成架繩並上樹-和夥伴到山林去玩（沒有大人）',
            },
            viewer: {
                fit: '',   // 原始工作表此格即空白 — 空白是合法狀態
                goal: '兩個特質相異的孩子在這趟冒險中，經過重重現實考驗（日落時間、路徑變化、實際樹種），找到屬於他們的合作方式，創造他們的chill時光？',
                process: '攀樹過程＋兩個孩子＝步驟、困難與好玩＝好奇',
                highlight: '暖身＆壓力：冒險',
            },
            content: {
                fit: '學習歷程、情緒',
                goal: '訪談取代討論拍攝現場的拍攝需求',
                process: '行前會議\n有情境的再一次\n攝影分組',
                highlight: '場勘、彩排、畫重點＝共同的圖像\n定點＝boom、移動＝mini mic',
            },
        },
    },
];
