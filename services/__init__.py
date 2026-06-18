"""services/ — 業務邏輯層（Phase M 官網服務在 services/website/）。

必須是「正規套件」（保留這個 __init__.py），不能當 PEP 420 namespace 套件：
agent 端的嵌入式 Python（python_embed）不會解析沒有 __init__.py 的 `services`
namespace，導致 routers/api_crm.py 的 `from services.website import ...` 在
master 8000 上丟 ModuleNotFoundError（2026-06-18 官網作品編輯 500 根因）。
NAS 用一般 python 不受影響，但這個檔對兩邊都安全 — 請勿刪除。
"""
