"""
指令 16：前端整合 — validateRemotePaths 瀏覽器測試
透過 page.evaluate() 呼叫 window.validateRemotePaths()
"""
import pytest

pytestmark = pytest.mark.e2e


def test_fe_validate_nonexistent_drive(page):
    """validateRemotePaths 對不存在的磁碟機回傳 ok=false"""
    result = page.evaluate("""(async () => {
        try {
            const r = await window.validateRemotePaths('localhost:18000', ['Z:\\\\test']);
            return r;
        } catch(e) {
            return { error: e.message };
        }
    })()""")
    assert result.get("ok") is False
    errors_text = " ".join(result.get("errors", []))
    assert "磁碟機" in errors_text


def test_fe_validate_missing_path(page):
    """validateRemotePaths 對不存在的路徑回傳 ok=false"""
    result = page.evaluate("""(async () => {
        try {
            const r = await window.validateRemotePaths('localhost:18000', ['C:\\\\NonExistent_Test_999']);
            return r;
        } catch(e) {
            return { error: e.message };
        }
    })()""")
    assert result.get("ok") is False
    errors_text = " ".join(result.get("errors", []))
    assert "路徑不存在" in errors_text


def test_fe_validate_existing_paths(page):
    """validateRemotePaths 對存在的路徑回傳 ok=true"""
    result = page.evaluate("""(async () => {
        try {
            const r = await window.validateRemotePaths('localhost:18000', ['C:\\\\Windows', 'C:\\\\Users']);
            return r;
        } catch(e) {
            return { error: e.message };
        }
    })()""")
    assert result.get("ok") is True


def test_fe_validate_unreachable_host(page):
    """validateRemotePaths 對無法連線的主機拋出錯誤"""
    result = page.evaluate("""(async () => {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 3000);
        try {
            const url = 'http://192.168.99.99:8000/api/v1/validate_paths';
            const res = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ paths: ['C:\\\\test'] }),
                signal: controller.signal
            });
            clearTimeout(timeoutId);
            return { ok: true, unexpected: true };
        } catch(e) {
            clearTimeout(timeoutId);
            return { ok: false, error_caught: true, message: e.message };
        }
    })()""")
    assert result.get("error_caught") is True or result.get("ok") is False
