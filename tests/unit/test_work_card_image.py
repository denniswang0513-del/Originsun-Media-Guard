# -*- coding: utf-8 -*-
"""作品卡片取圖只有一條規則：精選圖 → 成果展示第一張 → 自訂封面 → YouTube。

同事 2026-09-11 回饋：在編輯器設了精選圖，首頁精選作品與作品牆卡片還是 YouTube 縮圖 ——
精選圖當時只餵首頁輪播（carousel_image）。這裡把「哪張圖上卡片」釘成單一規則
（`services.website.project_service.card_image`），並釘住兩個 Astro 消費者都吃它。
"""
from types import SimpleNamespace as NS

from services.website.project_service import card_image, work_cover
from tests.unit._srcscan import repo_src


def _sc(**kw):
    base = dict(featured_image=None, gallery=None, cover_url=None, youtube_id="abcdefghijk")
    base.update(kw)
    return NS(**base)


def test_featured_image_wins():
    assert card_image(_sc(featured_image="/uploads/p/showcase/f.jpg", gallery=[{"url": "/g1.jpg"}],
                          cover_url="/c.jpg")) == "/uploads/p/showcase/f.jpg"


def test_then_first_gallery_image_then_cover_then_youtube():
    assert card_image(_sc(gallery=[{"url": "/g1.jpg"}, {"url": "/g2.jpg"}], cover_url="/c.jpg")) == "/g1.jpg"
    assert card_image(_sc(cover_url="/c.jpg")) == "/c.jpg"
    assert card_image(_sc()) == "https://img.youtube.com/vi/abcdefghijk/hqdefault.jpg"


def test_blank_strings_do_not_count():
    """編輯器刪掉精選圖是 PUT 空字串，不是 NULL —— 空字串要當沒有。"""
    assert card_image(_sc(featured_image="", gallery=[{"url": "  "}], cover_url="")) \
        == "https://img.youtube.com/vi/abcdefghijk/hqdefault.jpg"


def test_no_video_no_images_is_none_not_a_fake_url():
    assert card_image(_sc(youtube_id=None)) is None


def test_og_cover_also_falls_back_to_the_featured_image():
    assert work_cover(_sc(featured_image="/f.jpg")) == "/f.jpg"
    assert work_cover(_sc(cover_url="/c.jpg", featured_image="/f.jpg")) == "/c.jpg"   # 自訂封面仍優先


def test_the_public_dict_carries_card_image_and_keeps_thumbnail_url_youtube_only():
    src = repo_src("services/website/project_service.py")
    assert '"card_image": card_image(sc),' in src
    # thumbnail_url 只能是 YT 縮圖（seo.ts 靠這個語意），不准改成也回上傳的圖
    assert '"thumbnail_url": _youtube_thumbnail(sc.youtube_id),' in src


def test_both_card_consumers_on_the_site_use_card_image():
    """首頁精選作品與作品牆卡片都要吃 card_image —— 漏一個就是「首頁對了、作品集還是 YT 縮圖」。"""
    assert "work.card_image ||" in repo_src("website/src/components/works/WorkCard.astro")
    assert "w.card_image ?" in repo_src("website/src/components/home/FeaturedWorks.astro")
    assert "card_image?: string | null" in repo_src("website/src/types/project.ts")


def test_deploy_to_prod_carries_the_astro_sources():
    r"""🔴 master 的 rebuild 在 C:\OriginsunAgent\website 跑 build，而 website/ 不在機隊 OTA
    清單裡 —— deploy_to_prod 不帶它的話，改了 .astro 發版＋rebuild 之後對外站還是舊的，
    而且完全沒有錯誤（2026-09-11 精選圖那次白等一輪）。"""
    from ota_manifest import AGENT_DIRS, DEPLOY_ONLY_PATHS
    assert "website/src" in DEPLOY_ONLY_PATHS and "website/integrations" in DEPLOY_ONLY_PATHS
    assert "website" not in AGENT_DIRS, "website/ 不准進機隊 OTA（node_modules 幾百 MB）"
    src = repo_src("routers/api_ota.py")
    assert "for rel in DEPLOY_ONLY_PATHS:" in src, "deploy_to_prod 沒有複製 DEPLOY_ONLY_PATHS"
