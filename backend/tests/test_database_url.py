from backend.app.db import _normalize_database_url


def test_railway_postgres_url_uses_psycopg():
    assert _normalize_database_url("postgresql://u:p@host/db") == "postgresql+psycopg://u:p@host/db"
    assert _normalize_database_url("postgres://u:p@host/db") == "postgresql+psycopg://u:p@host/db"
