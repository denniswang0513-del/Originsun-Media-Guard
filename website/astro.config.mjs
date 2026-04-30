// @ts-check
import { defineConfig } from 'astro/config';

import tailwindcss from '@tailwindcss/vite';
import sitemap from '@astrojs/sitemap';
import seoAudit from './integrations/seo-audit.mjs';

// https://astro.build/config
export default defineConfig({
  // 設 site URL → 影響 canonical、OG URL、sitemap.xml 的 base URL。
  // 7/1 正式切換時改 https://originsun-studio.com 並重 build。
  site: 'https://test.originsun-studio.com',

  integrations: [
    sitemap({
      // 排除 _astro 內部資源 + showcase token-based 編輯頁 + AI/feed 端點
      // （robots.txt / llms*.txt / feed.json / rss.xml / *.md 不該出現在 sitemap）
      filter: (page) =>
        !page.includes('/_astro/') &&
        !page.includes('/showcase') &&
        !/\.(txt|json|xml|md)\/?$/.test(page),
    }),
    // SEO 鐵閘 ③：build 末段掃 dist/ HTML，缺 title/desc/canonical/JSON-LD/h1/img-alt 即 fail
    seoAudit(),
  ],

  // Astro <Image> 用 sharp 自動轉 WebP / 產 srcset（透過 SmartImage wrapper）。
  // 遠端來源必須白名單；本地 /notion-media/ 等不需設定。
  // picsum.photos 不能加（會 302 redirect → sharp 拒收）— 由 SmartImage
  // 偵測後降級到原生 <img>，仍保 width/height 防 CLS。
  image: {
    service: { entrypoint: 'astro/assets/services/sharp' },
    domains: [
      'img.youtube.com',     // YouTube 縮圖（resolveThumbnail）
      'i.ytimg.com',         // YouTube 替代 CDN
    ],
  },

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
