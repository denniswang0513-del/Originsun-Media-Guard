// @ts-check
import { defineConfig } from 'astro/config';

import tailwindcss from '@tailwindcss/vite';
import sitemap from '@astrojs/sitemap';
import seoAudit from './integrations/seo-audit.mjs';
import { fetchRedirects } from './integrations/build-redirects.mjs';
import { fetchLastmod } from './integrations/build-lastmod.mjs';

// Build init：fetch redirect map from NAS website-api。離線時回 {}，build 不中斷。
const redirects = await fetchRedirects();

// Build init：每頁最後更新時間 → sitemap <lastmod>。只含作品/文章詳情頁 + 三個索引頁，
// 其餘靜態頁刻意不給（謊報 lastmod 會讓 Google 整站忽略此訊號）。離線時回 {}。
const lastmodMap = await fetchLastmod();

// https://astro.build/config
export default defineConfig({
  redirects,    // {"/old/path": "/news/11", ...} → 軟 301（meta refresh + canonical）
  // 設 site URL → 影響 canonical、OG URL、sitemap.xml 的 base URL。
  // 2026-07-03 正式上線：canonical 定為 www（舊站在 www、SEO 權重轉移最乾淨）。
  site: 'https://www.originsun-studio.com',

  integrations: [
    sitemap({
      // 排除 _astro 內部資源 + showcase token-based 編輯頁 + AI/feed 端點
      // （robots.txt / llms*.txt / feed.json / rss.xml / *.md 不該出現在 sitemap）
      filter: (page) =>
        !page.includes('/_astro/') &&
        !page.includes('/showcase') &&
        !/\.(txt|json|xml|md)\/?$/.test(page),
      // 只在有真實時間可依據時附 <lastmod>（map 由 build-lastmod.mjs 建）。
      // 查無 → 不設，Astro 就不會輸出該欄位。
      serialize: (item) => {
        const lastmod = lastmodMap[new URL(item.url).pathname];
        return lastmod ? { ...item, lastmod } : item;
      },
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
