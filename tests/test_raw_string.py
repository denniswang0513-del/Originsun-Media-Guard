import subprocess
import os

test_dir = "d:/Antigravity/OriginsunTranscode/test_concat"
out_file = os.path.join(test_dir, "out5.mov")
if os.path.exists(out_file):
    os.remove(out_file)

# What if we use a raw string r"..."
tc = r"drawtext=fontfile='C\:/Windows/Fonts/arial.ttf':text='%{pts\:hms}':x=w-tw-20:y=20:fontsize=48:fontcolor=white@0.5:box=1:boxcolor=black@0.25:boxborderw=6"

print(f"Filter string: {tc}")
cmd = [
    "ffmpeg", "-v", "error", "-y", "-nostdin",
    "-f", "concat", "-safe", "0",
    "-i", f"{test_dir}/concat_list.txt",
    "-vf", tc,
    "-c:v", "prores_ks", "-profile:v", "1",
    "-c:a", "copy",
    "-t", "1", # just process 1 sec for fast test
    out_file
]
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
out, err = proc.communicate()
print(f"Return code: {proc.returncode}")
if proc.returncode != 0:
    print(f"Stderr: {err.strip()}")
else:
    print("Success!")
