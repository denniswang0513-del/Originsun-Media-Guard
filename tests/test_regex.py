import re

tests = ["R_drive", "Q_drive", "USB 磁碟機 (E:)"]
for t in tests:
    m = re.search(r'^([a-zA-Z])_drive$', t, re.IGNORECASE)
    if m:
        print(f"{t} -> {m.group(1).upper()}:\\")
    else:
        print(f"{t} -> No match")
