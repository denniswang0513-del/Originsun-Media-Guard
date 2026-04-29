// @ts-check
import { defineConfig } from 'astro/config';

import tailwindcss from '@tailwindcss/vite';
import sitemap from '@astrojs/sitemap';

// https://astro.build/config
export default defineConfig({
  // 設 site URL → 影響 canonical、OG URL、sitemap.xml 的 base URL。
  // 7/1 正式切換時改 https://originsun-studio.com 並重 build。
  site: 'https://test.originsun-studio.com',

  integrations: [
    sitemap({
      // 排除 _astro 內部資源 + showcase token-based 編輯頁（不該被收進搜尋）
      filter: (page) => !page.includes('/_astro/') && !page.includes('/showcase'),
    }),
  ],

  // 允許從 Cloudflare Tunnel / LAN 其他機器透過 hostname 訪問 dev server。
  // Vite 預設會擋 non-localhost hostname（防 DNS rebinding 攻擊），
  // 所以要白名單列出 tunnel 會用到的網域。
  server: {
    host: true,
  },
  // 關掉 dev 模式右下角的 Astro Dev Toolbar（Astro logo + Inspect + Audit + 設定）
  devToolbar: {
    enabled: false,
  },
  vite: {
    plugins: [tailwindcss()],
    server: {
      allowedHosts: [
        '.originsun-studio.com',
        '.trycloudflare.com',
      ],
    },
  },
});
