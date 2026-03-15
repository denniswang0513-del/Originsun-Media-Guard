import subprocess
import os

for name in ["literal_slash_n", "double_slash_n", "actual_newline", "vertical_tab"]:
    in_vid = f"out_{name}.mp4"
    out_img = f"frame_{name}.jpg"
    if os.path.exists(in_vid):
        subprocess.run(["ffmpeg", "-y", "-i", in_vid, "-vframes", "1", out_img], capture_output=True)
        print(f"Extracted {out_img}")
