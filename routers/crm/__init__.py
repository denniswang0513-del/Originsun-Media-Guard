"""routers/crm — CRM API 拆分包（原 routers/api_crm.py 單檔 144 端點）。

各領域模組 import 時即透過 decorator 把 route 註冊到 _shared.router。
⚠️ import 順序 = 原檔 section 出現順序 = FastAPI 路由註冊順序（先註冊先贏），
不可重排 —— 例如 /projects/closing 必須先於 /projects/{project_id} 註冊。
"""
from . import clients    # noqa: F401  客戶管理 + CSV + users
from . import projects   # noqa: F401  專案管理 + 結案看板 + CSV
from . import quotes     # noqa: F401  報價 + 報價範本
from . import staff      # noqa: F401  人力 + 履歷 + 派工
from . import costs      # noqa: F401  雜支 + 收據 + 成本估算 + 子表
from . import finance    # noqa: F401  發票 + 請款 + 收支 + 應付/應收
from . import showcase   # noqa: F401  Showcase + token 編輯 + Site API
from . import works      # noqa: F401  1:N 作品子端點（依賴 projects/showcase，須在其後）
from . import media_log  # noqa: F401  影像紀錄（路徑獨立 /media-log，順序無關）

from ._shared import router  # noqa: F401,E402
