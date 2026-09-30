"""Tests for app/core/config.py — database URL normalization."""

from app.core.config import Settings


class TestDatabaseUrlNormalization:
    """PostgreSQL URLs must use the psycopg 3 SQLAlchemy driver."""

    def test_plain_postgresql_url_is_normalized(self):
        """postgresql:// -> postgresql+psycopg://"""
        settings = Settings(database_url="postgresql://user:pass@host:5432/db")
        assert settings.database_url == "postgresql+psycopg://user:pass@host:5432/db"

    def test_postgres_scheme_is_normalized(self):
        """postgres:// -> postgresql+psycopg://"""
        settings = Settings(database_url="postgres://user:pass@host:5432/db")
        assert settings.database_url == "postgresql+psycopg://user:pass@host:5432/db"

    def test_already_correct_url_is_unchanged(self):
        """postgresql+psycopg:// stays unchanged."""
        original = "postgresql+psycopg://user:pass@host:5432/db"
        settings = Settings(database_url=original)
        assert settings.database_url == original

    def test_sqlite_url_is_unchanged(self):
        """sqlite:/// stays unchanged (local dev fallback)."""
        settings = Settings(database_url="sqlite:///./data/dev/crisisx.db")
        assert settings.database_url == "sqlite:///./data/dev/crisisx.db"

    def test_render_style_url(self):
        """Render injects postgresql:// with connection string."""
        render_url = "postgresql://crisisx:abc123@dpg-c1234567890abcdef.us-east-1.render.com:5432/crisisx"
        settings = Settings(database_url=render_url)
        assert settings.database_url.startswith("postgresql+psycopg://")
        assert "crisisx:abc123@" in settings.database_url
        assert "dpg-c1234567890abcdef.us-east-1.render.com:5432/crisisx" in settings.database_url
