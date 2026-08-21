# -*- coding: utf-8 -*-
"""表單控制項一定要套專案的樣式類別。

🔴 沒套的話會退回**瀏覽器預設**——在深色介面裡就是一塊白，像貼上去的
（owner 2026-08-20 在對帳單匯入視窗看到：帳戶下拉與貼上區都是白底）。
這種錯不會報錯、不會壞功能，只有人真的看到畫面才會發現，所以用測試釘住。
"""
import re

from tests.unit._srcscan import repo_src  # noqa: E402

FILES = [
    'frontend/tabs/finance/subviews/banking.js',
    'frontend/tabs/crm/crm-cashbook.js',
    'frontend/tabs/crm/crm-payments.js',
    'frontend/tabs/crm/crm-invoices.js',
]

_TAG = re.compile(r"<(select|textarea|input)\b[^>]*>", re.I)
# 註解裡提到 <select> 不算數 —— 說明「為什麼把某個下拉換掉」的文字也會被掃到
# 🔴 DOTALL 只給區塊註解（(?s:…)）—— 整支開 re.S 的話 `//[^\n]*$` 的 `.`
# 會吃換行，一個行註解就把整個檔案吞掉，測試變成永遠通過（實測 106KB → 644B）。
_COMMENT = re.compile(r"(?s:/\*.*?\*/)|^[ \t]*//[^\n]*$", re.M)

# 勾選/選項鈕有自己的外觀；ss-input 是可搜尋下拉的內部件（元件自己畫）
_SKIP = ('type="checkbox"', 'type="radio"', 'ss-input')
_OK = ('crm-input', 'crm-select', 'crm-file', 'crm-search-input')
# 正當例外：
#  - 快速新增列（id 帶 -qa-）：整排由 .crm-row.inv-qa 的 CSS 統一畫，逐個掛 class
#    反而會跟那組規則打架
#  - display:none 的隱藏 input（只是用來觸發檔案選擇，使用者看不到）
#  - 帶內嵌 style 自己畫的（分配面板那幾個窄欄位）
_EXEMPT = ('-qa-', 'display:none', 'style="')


def test_every_form_control_has_a_style_class():
    bad = []
    for path in FILES:
        src = _COMMENT.sub('', repo_src(path))
        for m in _TAG.finditer(src):
            tag = m.group(0)
            if any(k in tag for k in _SKIP) or any(k in tag for k in _OK):
                continue
            if any(k in tag for k in _EXEMPT):
                continue
            line = src[:m.start()].count('\n') + 1
            bad.append(f"{path}:{line}  {tag[:80]}")
    assert not bad, ("這些表單控制項沒有樣式類別，會變成瀏覽器預設的白底：\n"
                     + "\n".join(bad))
