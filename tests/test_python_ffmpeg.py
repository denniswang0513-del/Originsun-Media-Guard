import subprocess
import time

cmd = [
    "ffmpeg", "-y", "-nostdin",
    "-f", "concat", "-safe", "0",
    "-i", "d:/Antigravity/OriginsunTranscode/test_concat/concat_list.txt",
    "-vf", "drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':text='%{pts\\:hms}':x=w-tw-20:y=20:fontsize=48:fontcolor=white@0.5:box=1:boxcolor=black@0.25:boxborderw=6",
    "-c:v", "prores_ks", "-profile:v", "1",
    "-c:a", "copy",
    "-progress", "pipe:1",
    "-nostats",
    "d:/Antigravity/OriginsunTranscode/test_concat/out3.mov"
]

print('Running ffmpeg command via python subprocess...')
try:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    
    out_lines = []
    # simulate the loop
    for cline in (proc.stdout or []):
        cline = cline.strip()
        print(f"STDOUT: {cline}")
        out_lines.append(cline)
        
    proc.wait()
    print(f"Return code: {proc.returncode}")
    err_txt = ""
    if getattr(proc, "stderr", None):
        err_txt = str(proc.stderr.read())  # type: ignore
    print(f"STDERR length: {len(err_txt)}")
    if proc.returncode != 0:
        print(f"STDERR: {err_txt[-500:]}")  # type: ignore
except Exception as e:
    print(f"Subprocess failed: {e}")

