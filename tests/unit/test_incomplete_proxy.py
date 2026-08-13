# -*- coding: utf-8 -*-
"""半成品產出不准被當成「已經有了」。

2026-08-13 事故：一台轉檔中途掛掉，交付夾裡留下一個 6MB 的 moov-less 壞檔，
檔名卻是正式名 —— 所有「檔案在不在」的驗收都說沒事，補轉也就不補它。
修法是兩段：轉檔先寫 `*_proxy.part.mov` 成功才 rename，驗收把半成品當缺件。
這裡把「驗收看得懂半成品」釘住。
"""
import os

from core_engine import is_junk_file
from routers.api_proxy import compare_source, is_incomplete_proxy, merge_host_outputs
from core.schemas import CompareSourceRequest, MergeHostOutputsRequest


def _touch(path, size=1024):
    with open(path, "wb") as f:
        f.write(b"\0" * size)
    return path


class TestIsIncompleteProxy:
    def test_part_file_is_incomplete(self, tmp_path):
        p = _touch(str(tmp_path / "A001_proxy.part.mov"))
        assert is_incomplete_proxy(p) is True

    def test_zero_byte_is_incomplete(self, tmp_path):
        p = _touch(str(tmp_path / "A001_proxy.mov"), size=0)
        assert is_incomplete_proxy(p) is True

    def test_normal_proxy_is_complete(self, tmp_path):
        p = _touch(str(tmp_path / "A001_proxy.mov"))
        assert is_incomplete_proxy(p) is False

    def test_missing_file_counts_as_incomplete(self, tmp_path):
        assert is_incomplete_proxy(str(tmp_path / "nope.mov")) is True


class TestJunkFile:
    def test_part_output_is_junk(self):
        # 掃來源 / 備份都不該撿到轉檔中的暫存檔
        assert is_junk_file("A001_proxy.part.mov") is True
        assert is_junk_file(r"C:\proxy\A001_proxy.PART.MOV") is True

    def test_real_proxy_is_not_junk(self):
        assert is_junk_file("A001_proxy.mov") is False


class TestCompareSourceIgnoresHalfBaked:
    async def _compare(self, src, out):
        return await compare_source(CompareSourceRequest(
            source_dir=str(src), output_dir=str(out), flat_proxy=True))

    async def test_part_file_counts_as_missing(self, tmp_path):
        src, out = tmp_path / "src", tmp_path / "out"
        src.mkdir(); out.mkdir()
        _touch(str(src / "A001.MP4"))
        _touch(str(out / "A001_proxy.part.mov"))       # 還在轉

        d = await self._compare(src, out)
        assert d["status"] == "ok"
        assert d["missing_count"] == 1
        assert os.path.basename(d["missing"][0]) == "A001.MP4"

    async def test_zero_byte_proxy_counts_as_missing(self, tmp_path):
        src, out = tmp_path / "src", tmp_path / "out"
        src.mkdir(); out.mkdir()
        _touch(str(src / "A002.MP4"))
        _touch(str(out / "A002_proxy.mov"), size=0)    # 建了檔就被砍斷

        d = await self._compare(src, out)
        assert d["missing_count"] == 1

    async def test_complete_proxy_is_not_missing(self, tmp_path):
        src, out = tmp_path / "src", tmp_path / "out"
        src.mkdir(); out.mkdir()
        _touch(str(src / "A003.MP4"))
        _touch(str(out / "A003_proxy.mov"))

        d = await self._compare(src, out)
        assert d["missing_count"] == 0
        assert d["proxy_count"] == 1

    async def test_part_alongside_finished_does_not_double_count(self, tmp_path):
        # rename 前的一瞬間兩個都在 —— 不能把它算成兩支 proxy
        src, out = tmp_path / "src", tmp_path / "out"
        src.mkdir(); out.mkdir()
        _touch(str(src / "A004.MP4"))
        _touch(str(out / "A004_proxy.mov"))
        _touch(str(out / "A004_proxy.part.mov"))

        d = await self._compare(src, out)
        assert d["proxy_count"] == 1
        assert d["missing_count"] == 0


class TestMergeLeavesHalfBakedBehind:
    """合併不准把半成品搬進交付夾 —— 搬過去就會佔著正式檔名騙過驗收。
    而且那個 HostDispatch 夾還有東西沒處理完，就不准刪。"""

    async def test_part_stays_and_folder_survives(self, tmp_path):
        proxy_root = tmp_path / "99_Transcode"
        base = proxy_root / "PROJ"
        hd = base / "HostDispatch_SOCA" / "card1"
        hd.mkdir(parents=True)
        _touch(str(hd / "A001_proxy.mov"))            # 轉好了
        _touch(str(hd / "A002_proxy.part.mov"))       # 死在半路

        d = await merge_host_outputs(MergeHostOutputsRequest(
            proxy_root=str(proxy_root), project_name="PROJ"))

        assert d["status"] == "ok"
        assert d["merged"] == 1
        assert len(d["errors"]) == 1                   # 半成品要被講出來
        assert os.path.isfile(str(base / "card1" / "A001_proxy.mov"))
        # 半成品留在原地，來源夾不准被清掉
        assert os.path.isfile(str(hd / "A002_proxy.part.mov"))
        assert os.path.isdir(str(base / "HostDispatch_SOCA"))

    async def test_clean_folder_is_removed(self, tmp_path):
        proxy_root = tmp_path / "99_Transcode"
        base = proxy_root / "PROJ"
        hd = base / "HostDispatch_SOCA" / "card1"
        hd.mkdir(parents=True)
        _touch(str(hd / "A001_proxy.mov"))

        d = await merge_host_outputs(MergeHostOutputsRequest(
            proxy_root=str(proxy_root), project_name="PROJ"))

        assert d["merged"] == 1 and d["errors"] == []
        assert not os.path.exists(str(base / "HostDispatch_SOCA"))
