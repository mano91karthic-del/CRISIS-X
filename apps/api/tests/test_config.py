"""Phase 12: covers the optional AI provider settings added to the
existing Settings system -- the application must start and behave
identically whether or not AI_PROVIDER_API_KEY is configured.
"""

from app.core.config import Settings


def test_settings_load_without_ai_key() -> None:
    settings = Settings(_env_file=None)
    assert settings.ai_provider_api_key is None
    assert settings.ai_provider_model is None
    # Unrelated existing settings are untouched by this addition.
    assert settings.database_url.startswith("postgresql")
    assert settings.data_storage_root == "./data/storage"


def test_settings_load_with_ai_key() -> None:
    settings = Settings(_env_file=None, ai_provider_api_key="sk-test-123", ai_provider_model="test-model")
    assert settings.ai_provider_api_key == "sk-test-123"
    assert settings.ai_provider_model == "test-model"
