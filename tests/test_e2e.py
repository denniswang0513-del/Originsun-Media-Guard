import os
import time
import requests  # type: ignore
import subprocess
import shutil

# 1. Prepare Test Environment
base_dir = r"d:\Antigravity\OriginsunTranscode\test_zone"
os.makedirs(f"{base_dir}\\src\\ProjectA\\Card1", exist_ok=True)
os.makedirs(f"{base_dir}\\nas", exist_ok=True)
os.makedirs(f"{base_dir}\\proxy", exist_ok=True)

dummy_video = f"{base_dir}\\src\\ProjectA\\Card1\\test_vid.mp4"
print(f"[Test] Generating dummy video source: {dummy_video}")
if not os.path.exists(dummy_video):
    # Create a 2-second dummy video
    subprocess.run(["ffmpeg", "-f", "lavfi", "-i", "testsrc=duration=2:size=1280x720:rate=30", "-c:v", "libx264", dummy_video, "-y"], 
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# 2. Start the FastAPI Server
print("[Test] Starting Originsun Media Guard Server...")
log_file = open("server_test_log.txt", "w", encoding="utf-8")
server_proc = subprocess.Popen(["python", "-u", "server.py"], cwd=r"d:\Antigravity\OriginsunTranscode", stdout=log_file, stderr=subprocess.STDOUT)
time.sleep(4) # Wait for startup

try:
    # 3. Test Backup Job (includes auto-transcode and auto-concat)
    print("\n[Test] 1. Triggering Backup Job (with Proxy & Concat enabled)...")
    resp = requests.post("http://localhost:8000/api/v1/jobs", json={
        "task_type": "backup",
        "project_name": "ProjectA",
        "local_root": f"{base_dir}\\src",
        "nas_root": f"{base_dir}\\nas",
        "proxy_root": f"{base_dir}\\proxy",
        "cards": [["Card1", f"{base_dir}\\src\\ProjectA\\Card1"]],
        "do_hash": True,
        "do_transcode": True,
        "do_concat": True
    })
    print(f"API Response: {resp.json()}")
    
    print("[Test] Waiting for Backup/Transcode/Concat tasks to complete...")
    for _ in range(60):
        time.sleep(2)
        try:
            status = requests.get("http://localhost:8000/api/v1/status").json()
            if status["queue_length"] == 0 and not status["busy"]:
                break
        except:
            pass
    print("[Test] Server tasks finished processing!")

    # 4. Verify Outputs
    nas_file = f"{base_dir}\\nas\\ProjectA\\Card1\\test_vid.mp4"
    proxy_file = f"{base_dir}\\proxy\\ProjectA\\Card1\\test_vid_proxy.mov"
    concat_file = f"{base_dir}\\proxy\\ProjectA\\Card1\\ProjectA_Card1_reel.mov"

    print("\n[Test] Output Verifications:")
    print(f" - NAS Backup created: {os.path.exists(nas_file)}")
    print(f" - Proxy Video created: {os.path.exists(proxy_file)}")
    print(f" - Concat Reel created: {os.path.exists(concat_file)}")

    # 5. Test Verify Job
    print("\n[Test] 2. Triggering Independent Verify Job (XXH64)...")
    resp = requests.post("http://localhost:8000/api/v1/jobs/verify", json={
        "task_type": "verify",
        "pairs": [[f"{base_dir}\\src\\ProjectA\\Card1", f"{base_dir}\\nas\\ProjectA\\Card1"]],
        "mode": "xxh64"
    })
    print(f"API Response: {resp.json()}")

    print("[Test] Waiting for Verify task to complete...")
    for _ in range(30):
        time.sleep(2)
        try:
            status = requests.get("http://localhost:8000/api/v1/status").json()
            if status["queue_length"] == 0 and not status["busy"]:
                break
        except:
            pass
    print("[Test] Verify task finished processing!")

finally:
    print("\n[Test] Cleaning up and terminating server...")
    server_proc.terminate()
    server_proc.wait()
    log_file.close()
    print("[Test] End-to-End Verification Complete.")
    
    with open("server_test_log.txt", "r", encoding="utf-8", errors="replace") as f:
        print("\n=== SERVER LOGS ===")
        try:
            print(f.read())
        except UnicodeEncodeError:
            # Output to stdout failed due to windows console encoding, skip printing
            pass
