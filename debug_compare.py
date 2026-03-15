import os, requests

VEXTS = {'.mov', '.mp4', '.mkv', '.mxf', '.avi', '.mts', '.m2ts', '.r3d', '.braw'}

source_dir = r'R:\ProjectYaun\20260304_幾莫_鴻海科技獎短影音\09_Export\01_Check'
output_dir = r'S:\20260225_轉檔軟體測試\20260307'

print("=== SOURCE STEMS ===")
src_stems = set()
for root, _, fnames in os.walk(source_dir):
    for f in fnames:
        ext = os.path.splitext(f)[1].lower()
        if ext in VEXTS:
            stem = os.path.splitext(f)[0].lower()
            src_stems.add(stem)
            print(f"  [{stem}]  ({f})")

print("\n=== PROXY STEMS ===")
proxy_stems = set()
for root, _, fnames in os.walk(output_dir):
    for f in fnames:
        ext = os.path.splitext(f)[1].lower()
        if ext in ('.mov', '.mp4'):
            stem = os.path.splitext(f)[0].lower()
            if stem.endswith('_proxy'):
                stem = stem[:-6]
            proxy_stems.add(stem)
            print(f"  [{stem}]  ({f})")

print("\n=== MISSING (in src but not in proxy) ===")
for s in src_stems:
    if s not in proxy_stems:
        print(f"  MISSING: [{s}]")
    else:
        print(f"  OK:      [{s}]")
