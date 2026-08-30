"""SQLAlchemy import 層＋Base（含無 SQLAlchemy 環境的 stub 退路）。

🔴 各段模型檔一律 `from ._base import ...` 而不是直接 import sqlalchemy ——
stub 退路只在這裡做一次，直接 import 的檔在沒裝 SQLAlchemy 的機器上會炸。
"""

try:
    from sqlalchemy import Column, String, Text, Boolean, Integer, BigInteger, Float, Date, DateTime, func, Index, UniqueConstraint
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.orm import DeclarativeBase
    _HAS_SQLALCHEMY = True
except ImportError:
    _HAS_SQLALCHEMY = False
    # Provide stubs so module can be imported without crashing
    class _Stub:
        def __call__(self, *a, **kw): return self
        def __getattr__(self, _): return self
    Column = String = Text = Boolean = Integer = BigInteger = Float = Date = DateTime = func = Index = UniqueConstraint = _Stub()
    JSONB = _Stub()
    class DeclarativeBase: pass


class Base(DeclarativeBase):
    pass


