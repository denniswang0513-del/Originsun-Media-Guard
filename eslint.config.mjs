// ESLint — 最小高價值規則集（對齊 ruff.toml 哲學：只抓「真 bug」類，不管風格）。
// 原則：gate 必須從第一天就是綠的。要加規則先在本機 `npm run lint` 全過再加。
// 刻意不開 no-undef：vanilla JS 跨檔 window._* 全域是本專案既有慣例，開了全是噪音。
export default [
    {
        files: ["frontend/**/*.js"],
        languageOptions: {
            ecmaVersion: 2022,
            sourceType: "module",
        },
        rules: {
            "no-dupe-keys": "error",        // 物件重複 key — 後者靜默蓋掉前者
            "no-dupe-args": "error",
            "no-duplicate-case": "error",   // switch 重複 case — 永遠走不到
            "no-unreachable": "error",
            "no-const-assign": "error",
            "no-class-assign": "error",
            "no-self-assign": "error",
            "no-compare-neg-zero": "error",
            "use-isnan": "error",
            "valid-typeof": "error",
            "getter-return": "error",
            "no-setter-return": "error",
            "no-async-promise-executor": "error",
            "no-loss-of-precision": "error",
        },
    },
];
