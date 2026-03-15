import subprocess
import os
import re

def _find_path(name: str):
    clean_name = name.strip()
    
    # 1. Regex logic for D_drive
    match = re.search(r'\(([a-zA-Z]):\)', clean_name)
    if match: return f"{match.group(1).upper()}:\\"
    match = re.search(r'^([a-zA-Z]):$', clean_name)
    if match: return f"{match.group(1).upper()}:\\"
    match = re.search(r'^([a-zA-Z])_drive$', clean_name, re.IGNORECASE)
    if match: return f"{match.group(1).upper()}:\\"

    drives = [f"{d}:\\" for d in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{d}:\\")]
    
    # 2. Volume Name lookup via VOL
    for drive in drives:
        try:
            # Try to get volume name output
            out = subprocess.check_output(f'vol {drive[:2]}', text=True, shell=True, stderr=subprocess.DEVNULL)
            # Volume in drive C is Windows
            if clean_name.lower() in out.lower():
                return drive
        except Exception:
            pass

    # 3. PowerShell Shell.Application COM Check
    try:
        ps_script = """
        $app = New-Object -ComObject Shell.Application
        foreach ($win in $app.Windows()) {
            try {
                $sel = $win.Document.SelectedItems()
                if ($sel -ne $null) {
                    foreach ($item in $sel) {
                        Write-Output $item.Path
                    }
                }
            } catch {}
        }
        """
        paths = subprocess.check_output(["powershell", "-NoProfile", "-Command", ps_script], text=True, stderr=subprocess.DEVNULL).splitlines()
        lower_name = clean_name.lower()
        for p in paths:
            p = p.strip()
            if not p: continue
            if os.path.basename(p).lower() == lower_name or p.lower().endswith(lower_name):
                return p
    except Exception as e:
        print("PS error:", e)
        pass

    # 4. Desktop/Downloads fallback
    user_profile = os.environ.get('USERPROFILE', '')
    if user_profile:
        for subdir in ["Desktop", "Downloads", "Documents"]:
            candidate = os.path.join(user_profile, subdir, clean_name)
            if os.path.exists(candidate):
                return candidate
    
    # 5. Root shallow scan
    for drive in drives:
        candidate = os.path.join(drive, clean_name)
        if os.path.exists(candidate): return candidate
        try:
            for sub in os.scandir(drive):
                if sub.is_dir():
                    c2 = os.path.join(sub.path, clean_name)
                    if os.path.exists(c2): return c2
        except: continue
        
    return clean_name

print("R_drive:", _find_path("R_drive"))
print("00_源日作品集:", _find_path("00_源日作品集"))
