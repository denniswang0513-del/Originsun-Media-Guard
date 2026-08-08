# -*- coding: utf-8 -*-
"""core/chunked_upload.py — 分塊上傳落地層。

這一層唯一的責任是「把很多塊拼回一個檔而且不弄壞它」，所以測試集中在會弄壞
檔案的那幾條路：位移對不上、重複送同一塊、超過上限、收尾時長度不足。
"""
import os

import pytest

from core import chunked_upload as cu


@pytest.fixture
def root(tmp_path):
    return str(tmp_path)


UID_PARTS = ("tok", "browser-1", "拍攝素材.mp4", 12345, 1700000000000)


def _uid():
    return cu.upload_id(*UID_PARTS)


# ── upload_id ────────────────────────────────────────────────

def test_upload_id_is_stable_and_hex():
    """同樣的輸入永遠同一個 id —— 續傳整個機制建立在這上面。"""
    assert cu.upload_id(*UID_PARTS) == cu.upload_id(*UID_PARTS)
    assert cu._ID_RE.match(cu.upload_id(*UID_PARTS))


def test_upload_id_separates_fields():
    """欄位邊界不可被內容偽造 —— 否則檔名裡放分隔字元就能撞進別人的半成品。"""
    assert cu.upload_id("a", "bc") != cu.upload_id("ab", "c")


def test_upload_id_varies_by_every_input():
    base = cu.upload_id(*UID_PARTS)
    for i in range(len(UID_PARTS)):
        changed = list(UID_PARTS)
        changed[i] = "different"
        assert cu.upload_id(*changed) != base, f"第 {i} 個輸入沒進 id"


# ── 路徑防護 ─────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "../../etc/passwd", "abc", "", "..", "/absolute",
    "a" * 39, "a" * 41, "A" * 40, "g" * 40,
])
def test_part_path_rejects_non_id(root, bad):
    """🔴 id 來自 HTTP 路徑參數 → 白名單比對格式，不合就不要碰檔案系統。"""
    with pytest.raises(cu.ChunkError) as ei:
        cu.part_path(root, bad)
    assert ei.value.kind == "bad-id"


def test_part_path_lands_in_staging(root):
    p = cu.part_path(root, _uid())
    assert os.path.dirname(p) == cu.staging_dir(root)


def test_staging_dir_is_hidden_from_folder_listings():
    """🔴 這不是美觀問題：半成品**靠這條規則**才不會出現在資料夾總覽、也不會被
    reconcile 當成照片匯進資料庫。改了 STAGING_DIRNAME 或那份前綴集合都要在這裡炸。"""
    from core.project_folders import is_hidden_name
    assert is_hidden_name(cu.STAGING_DIRNAME)
    assert not is_hidden_name("小飛俠劇照")      # 一般資料夾不受影響


# ── 續傳主流程 ───────────────────────────────────────────────

def test_append_and_finish_reassembles_exactly(root, tmp_path):
    uid = _uid()
    data = os.urandom(5000)
    cu.begin(root, uid)
    sent = 0
    for i in range(0, len(data), 1000):
        block = data[i:i + 1000]
        sent = cu.append_bytes(root, uid, [block], len(block), offset=sent, max_bytes=10 ** 7)
    assert sent == len(data)

    dest = str(tmp_path / "out.bin")
    assert cu.finish(root, uid, dest, expect_bytes=len(data)) == len(data)
    with open(dest, "rb") as fp:
        assert fp.read() == data
    assert not os.path.exists(cu.part_path(root, uid))   # 半成品沒有留下


def test_begin_reports_existing_progress(root):
    """重開分頁重選同一個檔 → begin 要說出已經傳到哪（否則從 0 重來）。"""
    uid = _uid()
    cu.begin(root, uid)
    cu.append_bytes(root, uid, [b"x" * 300], 300, offset=0, max_bytes=10 ** 7)
    assert cu.begin(root, uid) == 300


def test_received_bytes_zero_when_absent(root):
    assert cu.received_bytes(root, _uid()) == 0


# ── 會弄壞檔案的路徑 ─────────────────────────────────────────

def test_duplicate_chunk_is_rejected_not_appended(root):
    """逾時重試時第一次其實寫進去了 —— 再寫一次就是壞檔。"""
    uid = _uid()
    cu.begin(root, uid)
    cu.append_bytes(root, uid, [b"a" * 100], 100, offset=0, max_bytes=10 ** 7)
    with pytest.raises(cu.ChunkError) as ei:
        cu.append_bytes(root, uid, [b"a" * 100], 100, offset=0, max_bytes=10 ** 7)
    assert ei.value.kind == "offset"
    assert ei.value.received == 100          # 客戶端據此重新對齊
    assert cu.received_bytes(root, uid) == 100


def test_gap_is_rejected(root):
    """跳號 → 檔案中間會缺一塊，寧可拒收。"""
    uid = _uid()
    cu.begin(root, uid)
    with pytest.raises(cu.ChunkError) as ei:
        cu.append_bytes(root, uid, [b"z" * 10], 10, offset=999, max_bytes=10 ** 7)
    assert ei.value.kind == "offset"


def test_over_limit_discards_partial(root):
    """超限的檔不可能被接受，半成品留著只是佔 NAS 空間。"""
    uid = _uid()
    cu.begin(root, uid)
    cu.append_bytes(root, uid, [b"x" * 600], 600, offset=0, max_bytes=1000)
    with pytest.raises(cu.ChunkError) as ei:
        cu.append_bytes(root, uid, [b"x" * 600], 600, offset=600, max_bytes=1000)
    assert ei.value.kind == "too-large"
    assert cu.received_bytes(root, uid) == 0


def test_finish_rejects_incomplete(root, tmp_path):
    """長度不足還照樣改名 → 使用者拿到一個「成功」的壞檔。"""
    uid = _uid()
    cu.begin(root, uid)
    cu.append_bytes(root, uid, [b"x" * 50], 50, offset=0, max_bytes=10 ** 7)
    dest = str(tmp_path / "out.bin")
    with pytest.raises(cu.ChunkError) as ei:
        cu.finish(root, uid, dest, expect_bytes=100)
    assert ei.value.kind == "offset"
    assert ei.value.received == 50
    assert not os.path.exists(dest)
    assert cu.received_bytes(root, uid) == 50   # 還能接著傳


def test_finish_without_session(root, tmp_path):
    with pytest.raises(cu.ChunkError) as ei:
        cu.finish(root, _uid(), str(tmp_path / "out.bin"), expect_bytes=-1)
    assert ei.value.kind == "missing"


def test_finish_overwrites_dest_atomically(root, tmp_path):
    """os.replace 語義：目的地已存在也要成功（撞名改名是呼叫端的事）。"""
    uid = _uid()
    cu.begin(root, uid)
    cu.append_bytes(root, uid, [b"new"], 3, offset=0, max_bytes=10 ** 7)
    dest = tmp_path / "out.bin"
    dest.write_bytes(b"old-and-longer")
    cu.finish(root, uid, str(dest), expect_bytes=-1)
    assert dest.read_bytes() == b"new"


# ── 垃圾回收 ─────────────────────────────────────────────────

def test_gc_removes_only_stale(root):
    """使用者關掉分頁就沒人收尾 —— 沒有 GC，NAS 上會積滿無主的大檔。"""
    fresh, stale = cu.upload_id("fresh"), cu.upload_id("stale")
    cu.begin(root, fresh)
    cu.append_bytes(root, fresh, [b"a"], 1, offset=0, max_bytes=10 ** 7)
    cu.append_bytes(root, stale, [b"b"], 1, offset=0, max_bytes=10 ** 7)
    old = cu.part_path(root, stale)
    os.utime(old, (0, 0))                       # 1970 → 一定過期

    assert cu.gc(root, older_than=cu.STALE_SEC) == 1
    assert not os.path.exists(old)
    assert cu.received_bytes(root, fresh) == 1


def test_gc_on_missing_root_is_quiet(tmp_path):
    """NAS 搆不到時 GC 不可以炸掉上傳流程。"""
    assert cu.gc(str(tmp_path / "nope")) == 0


def test_discard_is_idempotent(root):
    cu.discard(root, _uid())            # 不存在
    cu.discard(root, "bad-id")          # 格式不合也不該炸


# ── HTTP 線上格式（前端續傳靠這個形狀）────────────────────────

def test_chunk_http_409_carries_received():
    """🔴 這是續傳的**線上契約**，兩端各自有測試但中間沒有。

    `putChunk` 讀的是 `detail.received`；`_chunk_http` 若被「順手統一成字串
    detail」（本模組其餘 HTTPException 都是字串），重試就不再是重新對齊而是
    致命 4xx —— 每一次逾時重試都變成整個檔上傳失敗，而且沒有任何測試會紅。
    `reason` 這個 key 名同理：utils.js 的 httpError 只認 `detail.reason`。
    """
    pytest.importorskip("fastapi")
    from routers.crm.media_log import _chunk_http

    exc = _chunk_http(cu.ChunkError("offset", "位移不符", received=7))
    assert exc.status_code == 409
    assert exc.detail["received"] == 7
    assert exc.detail["reason"] == "位移不符"

    assert _chunk_http(cu.ChunkError("missing", "x")).status_code == 404
    assert _chunk_http(cu.ChunkError("bad-id", "x")).status_code == 400
    # too-large 借用 _too_large()：與另外兩個入口同一句話（字串 detail）
    assert _chunk_http(cu.ChunkError("too-large", "x")).status_code == 413
    # core 之後新增 kind 不該讓伺服器 500
    assert _chunk_http(cu.ChunkError("brand-new", "x")).status_code == 400
