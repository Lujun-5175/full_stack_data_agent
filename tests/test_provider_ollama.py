from full_stack_data_agent.config.settings import get_settings
from full_stack_data_agent.llm.ollama_provider import OllamaProvider


def test_health_check_returns_expected_model() -> None:
    settings = get_settings()
    provider = OllamaProvider(settings)

    health = provider.health_check()

    assert health.provider == "ollama"
    assert health.model == settings.ollama_model
