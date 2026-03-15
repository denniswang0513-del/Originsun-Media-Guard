import subprocess
import time

ps_script = """
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$app = New-Object -ComObject Shell.Application
foreach ($win in $app.Windows()) {
    try {
        $sel = $win.Document.SelectedItems()
        if ($sel -ne $null) {
            foreach ($item in $sel) { Write-Output $item.Path }
        }
    } catch {}
}
"""

try:
    t0 = time.time()
    out = subprocess.check_output(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script], 
        encoding="utf-8", 
        errors="replace"
    )
    print("Time taken:", time.time() - t0)
    print("Output:", out)
except Exception as e:
    print("Error:", e)
