# -*- coding: utf-8 -*-
"""下載「把公司網路磁碟掛好」的 .bat（owner 2026-09-14「有個地方可以下載一個 bat 檔案，幫我把這些路徑設好」）。"""
from core.drive_map import DEFAULT_DRIVE_MAP, map_drives_bat
from tests.unit._srcscan import code_only, func_body, repo_src


def test_bat_maps_every_letter_persistently_and_is_plain_ascii_crlf():
    s = map_drives_bat({"T": r"\192.168.1.132\Project_Longterm", "V": r"\192.168.1.130\storage 01", "Z": ""})
    assert s.startswith("@echo off\r\n") and s.isascii() and "\n" not in s.replace("\r\n", "")   # cmd 吃 CRLF、不吃中文
    assert 'net use T: /delete /y >nul 2>&1\r\nnet use T: "\\192.168.1.132\Project_Longterm" /persistent:yes' in s
    assert 'net use V: "\\192.168.1.130\storage 01" /persistent:yes' in s                    # 有空白的 share 要引號
    assert "net use Z:" not in s                                                                # 墓碑（停用）不掛
    assert s.rstrip().endswith("pause")                                                        # 點兩下跑完視窗留著看結果
    assert all(f"net use {k}:" in map_drives_bat(DEFAULT_DRIVE_MAP) for k in DEFAULT_DRIVE_MAP)


def test_download_entry_points():
    api = code_only(func_body(repo_src("routers/api_system.py"), "async def download_map_drives_bat("))
    assert "map_drives_bat(effective_map())" in api and 'attachment; filename="map_drives.bat"' in api
    assert 'href="/api/v1/drive_map/map_drives.bat" download="map_drives.bat"' in repo_src("frontend/js/shared/drive-map-modal.js")
    auth = repo_src("frontend/js/auth/auth-state.js")
    assert "'下載網路磁碟設定檔 (.bat)'" in auth and "a.href = '/api/v1/drive_map/map_drives.bat';" in auth
    assert auth.index("下載網路磁碟設定檔") < auth.index("if (window._accessLevel >= 3) {")      # 登入的人都有，不只管理員
