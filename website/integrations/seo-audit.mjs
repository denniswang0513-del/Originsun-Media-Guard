/**
 * seo-audit.mjs — Astro integration：build 完成後掃 dist/ 全 HTML，
 * 任何頁面缺 SEO 必要元素就讓 build 失敗。
 *
 * 檢查項目：
 *   1. <title> 存在且非空
 *   2. <meta name="description"> 存在，內容 30-200 字
 *   3. <link rel="canonical"> 存在
 *   4. 至少一個 <script type="application/ld+json"> JSON-LD schema
 *   5. <h1> 標籤存在
 *   6. 所有 <img> 都有 alt 屬性
 *
 * 跳過：/_astro/、/pagefind/、/showcase、/404.html
 *
 * 用法（astro.config.mjs）：
 *   import seoAudit from './integrations/seo-audit.mjs';
 *   integrations: [sitemap(...), seoAudit()],
 *
 * 客製化：
 *   seoAudit({ skip: ['/_astro/', '/my-skip/'], minDesc: 50, maxDesc: 160 })
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const DEFAULT_SKIP = ['/_astro/', '/pagefind/', '/showcase', '/404.html'];

function walkHtml(dir) {
    const out = [];
    for (const name of readdirSync(dir)) {
        const full = join(dir, name);
        if (statSync(full).isDirectory()) {
            out.push(...walkHtml(full));
        } else if (full.endsWith('.html')) {
            out.push(full);
        }
    }
    return out;
}

function auditHtml(html, opts) {
    const issues = [];

    const titleMatch = html.match(/<title>([\s\S]*?)<\/title>/i);
    if (!titleMatch || !titleMatch[1].trim()) {
        issues.push('缺 <title> 或為空');
    }

    const descMatch = html.match(/<meta\s+name=["']description["']\s+content=["']([^"']*)["']/i);
    if (!descMatch) {
        issues.push('缺 <meta name="description">');
    } else {
        const len = descMatch[1].length;
        if (len < opts.minDesc) issues.push(`description 太短 (${len} 字，建議 ≥ ${opts.minDesc})`);
        if (len > opts.maxDesc) issues.push(`description 太長 (${len} 字，建議 ≤ ${opts.maxDesc})`);
    }

    if (!html.match(/<link\s+rel=["']canonical["']/i)) {
        issues.push('缺 <link rel="canonical">');
    }

    if (!html.match(/<script[^>]*type=["']application\/ld\+json["'][^>]*>/i)) {
        issues.push('缺 schema.org JSON-LD');
    }

    if (!html.match(/<h1\b/i)) {
        issues.push('缺 <h1>');
    }

    // 接受 alt="...", alt='...', alt=value, 以及 alt (HTML5 boolean 形式)。
    // (?:^|\s) 避免誤匹配 data-alt / xalt 等。
    const ALT_RE = /(?:^|\s)alt(?:\s*=|[\s/>])/i;
    const imgs = html.match(/<img\b[^>]*>/gi) || [];
    const noAlt = imgs.filter(img => !ALT_RE.test(img));
    if (noAlt.length) {
        issues.push(`${noAlt.length} 個 <img> 缺 alt 屬性`);
    }

    return issues;
}

export default function seoAudit(opts = {}) {
    const config = {
        skip: opts.skip || DEFAULT_SKIP,
        minDesc: opts.minDesc ?? 30,
        maxDesc: opts.maxDesc ?? 200,
    };

    return {
        name: 'seo-audit',
        hooks: {
            'astro:build:done': async ({ dir, logger }) => {
                const distDir = fileURLToPath(dir);
                const files = walkHtml(distDir);

                const errors = [];
                let audited = 0;
                let skipped = 0;

                for (const file of files) {
                    const rel = '/' + relative(distDir, file).replace(/\\/g, '/');
                    if (config.skip.some(s => rel.includes(s))) {
                        skipped++;
                        continue;
                    }
                    audited++;
                    const html = readFileSync(file, 'utf-8');
                    const issues = auditHtml(html, config);
                    if (issues.length) {
                        errors.push({ page: rel, issues });
                    }
                }

                logger.info(`SEO audit: 掃描 ${audited} 頁，跳過 ${skipped} 頁`);

                if (errors.length) {
                    logger.error(`SEO audit 失敗，${errors.length} 頁不符合：`);
                    for (const { page, issues } of errors) {
                        logger.error(`  ${page}`);
                        for (const i of issues) logger.error(`    × ${i}`);
                    }
                    throw new Error(
                        `SEO audit 失敗：${errors.length} 頁不符合 SEO 標準（修完才能 build）`
                    );
                }

                logger.info('✓ SEO audit 全數通過');
            },
        },
    };
}
