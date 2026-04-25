// @ts-check
import { defineConfig } from 'astro/config';

import tailwindcss from '@tailwindcss/vite';

// https://astro.build/config
export default defineConfig({
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
