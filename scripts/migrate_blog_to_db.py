"""scripts/migrate_blog_to_db.py
---
一次性 migration：把 website/src/content/posts.json + categories.json 灌進 DB。

執行時機：Phase A 部署後、第一次啟用 DB-as-truth 部落格之前。
之後 admin 在 admin Tab 編輯，舊 JSON 退場。

用法：
    cd <repo root>
    python scripts/migrate_blog_to_db.py             # dry-run（預覽）
    python scripts/migrate_blog_to_db.py --apply     # 真寫入（status=draft）
    python scripts/migrate_blog_to_db.py --apply --auto-publish   # 直接上線

預設新匯入文章 status=draft（給 admin 一次過目機會）。
加 --auto-publish 才直接 status=published。
Idempotent：如果 DB 已有同 slug 的 post，skip（不覆寫 admin 後續編輯）。
--force：刪除既有 posts + categories 重新灌（危險！）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# 讓腳本能 import 上層的 db / services
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, select
from db.session import get_session_factory, init_db
from db.models_website import (
    WebsitePost, WebsitePostCategory, WebsitePostCategoryLink,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
POSTS_JSON = REPO_ROOT / "website" / "src" / "content" / "posts.json"
CATEGORIES_JSON = REPO_ROOT / "website" / "src" / "content" / "categories.json"


async def migrate(apply: bool, force: bool, auto_publish: bool) -> int:
    if not POSTS_JSON.exists():
        print(f"[ERR] {POSTS_JSON} 不存在")
        return 1
    if not CATEGORIES_JSON.exists():
        print(f"[ERR] {CATEGORIES_JSON} 不存在")
        return 1

    raw_posts = json.loads(POSTS_JSON.read_text(encoding="utf-8"))
    raw_cats = json.loads(CATEGORIES_JSON.read_text(encoding="utf-8"))
    print(f"[*] 讀到 {len(raw_posts)} 篇文章 / {len(raw_cats)} 個分類")

    await init_db()
    factory = get_session_factory()
    if not factory:
        print("[ERR] DB 連線失敗")
        return 1

    async with factory() as session:
        # Force 模式：清空既有
        if force and apply:
            print("[!] --force：清除 DB 既有 posts + categories")
            await session.execute(delete(WebsitePostCategoryLink))
            await session.execute(delete(WebsitePost))
            await session.execute(delete(WebsitePostCategory))
            await session.commit()

        # 1. Categories
        existing_cats = {c.slug: c for c in (
            await session.execute(select(WebsitePostCategory))
        ).scalars()}

        cat_inserted = 0
        cat_skipped = 0
        cat_id_by_slug: dict[str, int] = {}

        for c in raw_cats:
            slug = c.get("id")
            if not slug:
                continue
            if slug in existing_cats:
                cat_id_by_slug[slug] = existing_cats[slug].id
                cat_skipped += 1
                continue
            if apply:
                cat = WebsitePostCategory(
                    slug=slug,
                    label_zh=c.get("label_zh") or c.get("name") or slug,
                    label_en=c.get("label_en") or c.get("name"),
                    color=c.get("color"),
                )
                session.add(cat)
                await session.flush()
                cat_id_by_slug[slug] = cat.id
            cat_inserted += 1

        if apply:
            await session.commit()
        print(f"[cats] 新增 {cat_inserted} / 跳過 {cat_skipped}")

        # 2. Posts
        existing_post_slugs = {s for (s,) in await session.execute(select(WebsitePost.slug))}
        post_inserted = 0
        post_skipped = 0

        for p in raw_posts:
            slug = p.get("slug")
            if not slug:
                continue
            if slug in existing_post_slugs:
                post_skipped += 1
                continue

            published_at_iso = p.get("published_at") or ""
            try:
                published_at = (
                    datetime.fromisoformat(published_at_iso.replace("Z", "+00:00"))
                    if published_at_iso else None
                )
            except ValueError:
                published_at = None

            if apply:
                post = WebsitePost(
                    slug=slug,
                    title=p.get("title") or "Untitled",
                    excerpt=p.get("excerpt"),
                    cover_url=p.get("cover_url"),
                    body=p.get("body") or [],
                    published_at=published_at,
                    date_modified=published_at,
                    status="published" if auto_publish else "draft",
                    notion_page_id=None,                  # 舊 JSON 沒記錄 page_id
                    imported_from_notion_at=datetime.now(timezone.utc),
                )
                session.add(post)
                await session.flush()

                # link to category（單分類）
                cat_slug = p.get("category")
                if cat_slug and cat_slug in cat_id_by_slug:
                    session.add(WebsitePostCategoryLink(
                        post_id=post.id, category_id=cat_id_by_slug[cat_slug],
                    ))
            post_inserted += 1

        if apply:
            await session.commit()
        print(f"[posts] 新增 {post_inserted} / 跳過 {post_skipped}")

    if not apply:
        print("\n[*] dry-run 完成。要真的寫入請加 --apply")
    else:
        print("\n[OK] migration 完成")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="把舊 posts.json/categories.json 灌進 DB")
    parser.add_argument("--apply", action="store_true",
                        help="真的寫入 DB（不加=dry run 預覽）")
    parser.add_argument("--auto-publish", action="store_true",
                        help="新匯入的 post 直接 status=published；預設 draft 給 admin 過目")
    parser.add_argument("--force", action="store_true",
                        help="清空 DB 既有 posts + categories 重新灌（危險！）")
    args = parser.parse_args()
    return asyncio.run(migrate(
        apply=args.apply, force=args.force, auto_publish=args.auto_publish,
    ))


if __name__ == "__main__":
    sys.exit(main())
