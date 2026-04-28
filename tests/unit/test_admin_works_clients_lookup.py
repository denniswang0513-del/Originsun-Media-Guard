"""Pin the Client model columns used by /api/website/admin/clients/lookup.

該端點曾經引用不存在的 `Client.name`，runtime 才炸 AttributeError → 500，
前端 silently catch 之後 dropdown 顯示空。把假設釘住，未來欄位重命名/移除
立刻在單元測試擋下，不要等到上線才被使用者抓包。
"""
from db.models import Client


def test_admin_works_lookup_columns_exist_on_client():
    for col in ("id", "short_name", "full_name"):
        assert hasattr(Client, col), (
            f"routers/website/admin_works.py:lookup_clients selects Client.{col}; "
            "model lost the column — update both together."
        )


def test_admin_works_lookup_query_compiles():
    """Make sure the actual SELECT compiles against the live model."""
    from sqlalchemy import select
    stmt = (
        select(Client.id, Client.short_name, Client.full_name)
        .order_by(Client.short_name, Client.full_name)
        .limit(500)
    )
    str(stmt.compile(compile_kwargs={"literal_binds": True}))
