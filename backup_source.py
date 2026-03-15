import os
import zipfile

BACKUP_ZIP = r"d:\Antigravity\OriginsunTranscode_V1.4.1_SourceBackup.zip"
TARGET_DIR = r"d:\Antigravity\OriginsunTranscode"
ALLOWED_EXT = ('.py', '.js', '.html', '.css', '.json', '.txt', '.bat', '.vbs', '.md')
EXCLUDE_DIRS = ('.venv', '.git', '__pycache__', 'models', 'tests', 'test_zone', 'python_embed')

print(f"Creating source backup: {BACKUP_ZIP}...")

with zipfile.ZipFile(BACKUP_ZIP, 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(TARGET_DIR):
        # 排除不需要備份的肥大目錄
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]  # type: ignore
        
        for file in files:
            if file.endswith(ALLOWED_EXT):
                file_path = os.path.join(root, file)
                # 計算相對路徑存入 zip
                arcname = os.path.relpath(file_path, TARGET_DIR)
                zf.write(file_path, arcname)
                print(f"Added: {arcname}")

print("Backup complete!")
