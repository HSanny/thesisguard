from backend.app.db import _normalize_database_url


def test_railway_postgres_url_uses_pg8000():
    assert _normalize_database_url("postgresql://u:p@host/db") == "postgresql+pg8000://u:p@host/db"
    assert _normalize_database_url("postgres://u:p@host/db") == "postgresql+pg8000://u:p@host/db"
