import sys
import os
import time

if len(sys.argv) < 2:
    print("Usage: python download_model.py <model_size>")
    sys.exit(1)

model_size = sys.argv[1]
models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
os.makedirs(models_dir, exist_ok=True)

print(f"[{model_size}] Preparing to download to {models_dir}...")
sys.stdout.flush()

# [CRITICAL WINDOWS FIX] Disable HuggingFace symlinks which cause WinError 32 Access Denied
# when not running as Administrator, or when AV blocks the pointer file creation.
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS"] = "1"

try:
    from faster_whisper import download_model  # type: ignore
    # Add retry logic for Windows file lock errors (WinError 32)
    max_retries = 3
    for attempt in range(max_retries):
        try:
            path = download_model(model_size, cache_dir=models_dir, local_files_only=False)
            print(f"\n[{model_size}] Download complete! Path: {path}")
            sys.exit(0)
        except PermissionError as e:
            if "WinError 32" in str(e) and attempt < max_retries - 1:
                print(f"[Warning] File locked by another process. Retrying in 5 seconds... ({attempt+1}/{max_retries})")
                sys.stdout.flush()
                time.sleep(5)
            else:
                raise e
except Exception as e:
    print(f"\n[{model_size}] Download failed: {e}")
    sys.exit(1)
