import sys
import re
import os

files = ['d:/Antigravity/OriginsunTranscode/Anent_MediaGuard_Pro.py', 'd:/Antigravity/OriginsunTranscode/Toolbox_Preview.py']

# Comprehensive emoji filtering
emoji_pattern = re.compile(
    '['
    '\U0001f300-\U0001f5ff'
    '\U0001f900-\U0001f9ff'
    '\U0001f600-\U0001f64f'
    '\U0001f680-\U0001f6ff'
    '\u2600-\u26ff'
    '\u2700-\u27bf'
    ']+', flags=re.UNICODE)

for file_path in files:
    if not os.path.exists(file_path): continue
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
        
    # Remove emoji followed by a space
    new_content = re.sub(emoji_pattern.pattern + ' ', '', content)
    # Remove any remaining emojis
    new_content = re.sub(emoji_pattern.pattern, '', new_content)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

print('Emoji sweep complete.')
