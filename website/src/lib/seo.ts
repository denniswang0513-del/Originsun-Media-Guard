/**
 * seo.ts — Schema.org 結構化資料 + OG meta helpers
 */

import type { IPublicProjectDetail } from "../types/project";


export function videoObjectSchema(work: IPublicProjectDetail, siteUrl: string): Record<string, unknown> {
    const thumb = work.youtube_id
        ? `https://img.youtube.com/vi/${work.youtube_id}/maxresdefault.jpg`
        : null;
    const embedUrl = work.youtube_id
        ? `https://www.youtube-nocookie.com/embed/${work.youtube_id}`
        : null;

    return {
        "@context": "https://schema.org",
        "@type": "VideoObject",
        name: work.title,
        description: work.description || work.title,
        thumbnailUrl: thumb ? [thumb] : undefined,
        uploadDate: work.published_at || undefined,
        embedUrl: embedUrl || undefined,
        contentUrl: work.youtube_id ? `https://www.youtube.com/watch?v=${work.youtube_id}` : undefined,
        url: `${siteUrl}/works/${work.slug}`,
    };
}


export function organizationSchema(meta: {
    company_name_en: string;
    company_name_zh: string;
    address?: string;
    phone?: string;
    email?: string;
    social: Record<string, string | undefined>;
}, siteUrl: string): Record<string, unknown> {
    const sameAs = Object.values(meta.social).filter(Boolean) as string[];
    return {
        "@context": "https://schema.org",
        "@type": "Organization",
        name: meta.company_name_en || meta.company_name_zh,
        alternateName: meta.company_name_zh,
        url: siteUrl,
        email: meta.email || undefined,
        telephone: meta.phone || undefined,
        address: meta.address ? {
            "@type": "PostalAddress",
            streetAddress: meta.address,
        } : undefined,
        sameAs: sameAs.length ? sameAs : undefined,
    };
}
