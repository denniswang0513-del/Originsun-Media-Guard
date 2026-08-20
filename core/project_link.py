"""core/project_link.py — 「哪些會計項目的錢可以掛到專案上」的單一正本。

規則本身只有一句：**這筆錢屬於某個特定案子，才可以連結專案。** 行政／設備耗材／
業務推廣那種公司層級支出掛上去，專案毛利就會多算一筆不屬於它的錢。

但三個入口的答案不一樣，而且**是刻意的**（不是漂移，不要「順手統一」）：

    零用金   只有「專案雜支」 —— owner 2026-08-17 明確拍板：「只有勾專案雜支時，
             那筆才需要連結專案；其餘的項目不開放連結」。零用金的項目清單裡
             **有**「專案外包」，是刻意排除的，不是漏掉。
    請款單   專案外包 + 專案雜支 —— 請款單本來就是付給外包/現場的錢。
    收支明細 專案 + 專案雜支 + 專案外包 —— 這裡還會出現專案收入（category='專案'）。

以前這三份分別寫死在 routers/crm/petty.py、frontend/tabs/crm/crm-payments.js、
frontend/tabs/crm/crm-cashbook.js —— 同一個問題三個地方三種答案，而且前端那兩份
改了要發版。集中在這裡之後，差異看得見、要調整只有一個檔案要動。
"""
from __future__ import annotations

# 零用金（routers/crm/petty.py 以此為不變式強制，前端經 /petty/options 取用）
PETTY_ITEMS = ("專案雜支",)

# 請款單
PAYMENT_CATEGORIES = ("專案外包", "專案雜支")

# 收支明細（前端經 /crm/cash-entries/options 取用，後端寫入時強制）
CASH_CATEGORIES = ("專案", "專案雜支", "專案外包")
