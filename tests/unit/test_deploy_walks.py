# -*- coding: utf-8 -*-
"""部署與掃描的遞迴剪枝，不准再按裸名字剪掉 db/models/。

2026-08-31 實際咬到：`EXCLUDE_DIRS` 的 'models' 指的是根目錄的 F5-TTS 模型
快取（GB 級權重），但 deploy_to_prod 的 os.walk 按**裸名字**在每一層剪 ——
db/models/（ORM 套件）整個沒被複製到生產。最陰的是 smoke check 照樣全綠：
生產上殘留的舊 db/models.py 名字一個不少，補位補得天衣無縫。發現的方式是
人去看 C:\\OriginsunAgent\\db 底下「就是沒有 models/」。

修法＝兩份語義分開：根層 listdir 檢查照用 EXCLUDE_DIRS（那裡的 'models'
真的是那顆快取）；遞迴 walk 一律用 NESTED_EXCLUDE_DIRS（去掉 'models'）。
"""
from tests.unit._srcscan import code_only, func_body, repo_src


def test_the_nested_exclusion_set_does_not_contain_models():
    from ota_manifest import EXCLUDE_DIRS, NESTED_EXCLUDE_DIRS
    assert "models" in EXCLUDE_DIRS, "根層那份要留著 —— OTA zip 探索靠它擋模型快取"
    assert "models" not in NESTED_EXCLUDE_DIRS
    assert NESTED_EXCLUDE_DIRS == EXCLUDE_DIRS - {"models"}, \
        "兩份要保持推導關係，不要各自維護"


def test_recursive_walks_use_the_nested_set():
    """🔴 兩個遞迴 walk（deploy 複製、依賴掃描）都要用 NESTED_ 版。

    用回 EXCLUDE_DIRS 的症狀是靜默的：deploy 全綠、生產跑舊 models ——
    直到有人改了某張表、部署、然後發現生產「改不動」。
    """
    dep = code_only(repo_src("routers/api_ota.py"))
    i = dep.index("def _deploy")  # deploy_to_prod 的工作函式區段
    seg = dep[i:]
    assert "x not in NESTED_EXCLUDE_DIRS" in seg, "deploy 複製的剪枝退回裸名字版了"
    scan = code_only(func_body(repo_src("ota_manifest.py"), "def scan_imports("))
    assert "NESTED_EXCLUDE_DIRS" in scan


def test_db_models_package_actually_survives_a_simulated_deploy_walk(tmp_path):
    """用真的 os.walk 走一次 db/，斷言 models/ 底下的段檔會被收進來 ——
    掃字串只證明「有寫那行」，這支證明「那行真的放行了 db/models」。"""
    import os

    from ota_manifest import NESTED_EXCLUDE_DIRS
    seen = set()
    for root, dirs, files in os.walk("db"):
        dirs[:] = [x for x in dirs if x not in NESTED_EXCLUDE_DIRS and x != "__pycache__"]
        for f in files:
            seen.add(os.path.join(root, f).replace("\\", "/"))
    for must in ("db/models/__init__.py", "db/models/_crm.py", "db/models/_finance.py"):
        assert must in seen, f"deploy 的 walk 還是看不到 {must}"
