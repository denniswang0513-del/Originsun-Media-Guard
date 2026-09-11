"""core/schemas — 所有 Pydantic 請求模型（套件；原本是單檔 core/schemas.py）。

2026-09-11 拆：單檔剛好 2,000 行、185 個 class，卡在 AI 單次讀取上限（超過就會被靜默截斷）。
界畫在原本的分節註解上，零 class 搬家。**對外 import 不變**：`from core.schemas import X`。

新 schema 放哪一段：
  _jobs      任務／系統層請求（備份、轉檔、報表、TTS、OTA…）
  _hr        請假、工時手填
  _crm       CRM 全部（客戶、專案、報價、人員、發票／請款／收支、匯款通知）
  _finance   財務管理（api_finance）
  _workos    書籤、週記、工作階段、影像紀錄分塊上傳、福委會
  _mobile    CRM 手機版 BFF
要找定義用 grep，不要靠記憶猜檔名。掃原始碼的測試用 `tests/unit/_srcscan.schemas_src()`。
"""
from ._jobs import *  # noqa: F401,F403
from ._hr import *  # noqa: F401,F403
from ._crm import *  # noqa: F401,F403
from ._finance import *  # noqa: F401,F403
from ._workos import *  # noqa: F401,F403
from ._mobile import *  # noqa: F401,F403
