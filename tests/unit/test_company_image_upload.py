# -*- coding: utf-8 -*-
"""公司 Logo／印章上傳（設定頁「公司資訊」）：
- 存 repo 根目錄 company_assets/（gitignore；**不**掛靜態 —— /uploads 是公開的，章不該無登入就抓得到）
- 端點管理員限定、看檔頭不看副檔名、上傳完直接寫進 settings.company.<kind>_path
- 前端預覽走 fetch→blob（<img src> 直打不帶 Authorization 會 401）
"""
from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src


def test_company_assets_dir_is_private_and_ignored():
    assert "company_assets/" in repo_src(".gitignore")
    main = repo_src("main.py")
    assert 'directory="company_assets"' not in main and "company_assets" not in main, "章的資料夾不准掛成靜態"


def test_upload_endpoint_is_admin_only_and_writes_settings():
    src = repo_src("routers/api_system.py")
    up = code_only(func_body(src, "async def upload_company_image("))
    assert "_check_admin(req)" in up and "_image_ext(content)" in up
    assert 'company[f"{kind}_path"] = rel' in up and "save_settings(" in up
    assert "os.listdir(_COMPANY_ASSETS_DIR)" in up, "同 kind 要只留一份，換副檔名不殘留"
    get = code_only(func_body(src, "async def get_company_image("))
    assert "_check_admin(req)" in get and "no_store_file(" in get
    assert '_COMPANY_IMAGE_KINDS = ("logo", "seal")' in src


def test_image_ext_checks_magic_not_filename():
    from routers.api_system import _image_ext
    import pytest
    assert _image_ext(b"\x89PNG\r\n\x1a\n" + b"\0" * 8) == "png"
    assert _image_ext(b"\xff\xd8\xff\xe0" + b"\0" * 8) == "jpg"
    assert _image_ext(b"RIFF\0\0\0\0WEBPVP8 ") == "webp"
    with pytest.raises(Exception):
        _image_ext(b"RIFF\0\0\0\0WAVEfmt ")          # RIFF 但不是 WEBP
    with pytest.raises(Exception):
        _image_ext(b"<svg xmlns='http://www.w3.org/2000/svg'/>")


def test_settings_page_has_upload_buttons_and_blob_preview():
    html = repo_src("frontend/index.html")
    for kind in ("logo", "seal"):
        assert f'id="company_{kind}_upload"' in html and f'id="company_{kind}_file"' in html
        assert f'id="company_{kind}_preview"' in html and f'id="company_{kind}_path"' in html
    js = js_code_only(repo_src("frontend/js/settings/settings-modal.js"))
    assert "fetch(`/api/settings/company-image/${kind}`, { method: 'POST', body: fd })" in js
    assert "URL.createObjectURL(await r.blob())" in js, "預覽要走 fetch→blob，<img src> 直打會 401"
    assert "_bindCompanyUploads();" in js


def test_quote_toolbar_has_company_info_shortcut():
    """owner 2026-09-07：公司資訊的入口放在報價管理的範本欄旁（開系統設定並切到公司資訊分頁；只給管理員）。"""
    html = repo_src("frontend/tabs/crm/crm-quotes.html")
    assert html.index('id="quote-btn-company"') < html.index('id="quote-btn-templates"')
    js = js_code_only(repo_src("frontend/tabs/crm/crm-quotes.js"))
    assert "document.getElementById('btnOpenSettings')?.click();" in js
    assert "b.textContent.trim() === '公司資訊')?.click();" in js
    assert "(window._accessLevel || 0) >= 3" in js
    # owner 2026-09-07「請拉出公司資訊就好，其他 tab 不用」：從報價頁開＝company-only（藏分頁列、標題換）；從頭像開＝完整
    assert "modal.classList.add('company-only');" in js
    sm = js_code_only(repo_src("frontend/js/settings/settings-modal.js"))
    assert "modal.classList.remove('company-only');" in sm
    assert "#settingsModal.company-only .modal-tabs { display: none; }" in repo_src("frontend/style.css")


def test_settings_modal_scrolls_instead_of_overflowing_the_screen():
    """owner 2026-09-07：公司資訊分頁比螢幕高，整個視窗溢出、儲存鈕在畫面外 → 視窗釘 92vh、只有分頁內容捲。"""
    css = repo_src("frontend/style.css")
    block = css[css.index(".originsun-modal-content {"):css.index("#settingsModal .tab-content")]
    assert "max-height: 92vh;" in block and "flex-direction: column;" in block
    assert "#settingsModal .tab-content { flex: 1 1 auto; min-height: 0; overflow-y: auto; }" in css
