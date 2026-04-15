from __future__ import annotations

from types import SimpleNamespace

from full_stack_data_agent.ui import app


class _SessionState(dict):
    def __getattr__(self, item):
        return self[item]

    def __setattr__(self, key, value):
        self[key] = value


def test_imported_ui_app_does_not_set_page_config(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(app.st, "set_page_config", lambda **kwargs: calls.append("set_page_config"))

    assert calls == []


def test_main_configures_page_at_entrypoint(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(app.st, "set_page_config", lambda **kwargs: calls.append("set_page_config"))
    monkeypatch.setattr(app.st, "markdown", lambda *args, **kwargs: calls.append("markdown"))
    monkeypatch.setattr(app, "_init_session_state", lambda: calls.append("init"))
    monkeypatch.setattr(app, "_sync_uploaded_contexts", lambda: calls.append("sync"))
    monkeypatch.setattr(app, "_apply_pending_ui_resets", lambda: calls.append("reset"))
    monkeypatch.setattr(app, "_render_sidebar", lambda status: calls.append("sidebar"))
    monkeypatch.setattr(app, "_render_status_notice", lambda status: calls.append("status"))
    monkeypatch.setattr(app, "_render_conversation", lambda mount, force_scroll=False: calls.append("conversation"))
    monkeypatch.setattr(app, "_render_composer", lambda: (False, False))
    monkeypatch.setattr(app.st, "columns", lambda *args, **kwargs: [SimpleNamespace(), SimpleNamespace()])
    monkeypatch.setattr(app.st, "container", lambda: SimpleNamespace(empty=lambda: SimpleNamespace(), container=lambda: SimpleNamespace()))
    monkeypatch.setattr(
        app,
        "get_service",
        lambda: SimpleNamespace(provider_status=lambda: SimpleNamespace(provider="deepseek", connected=True, fallback_connected=True, fallback_provider="ollama", model="deepseek-chat")),
    )
    monkeypatch.setattr(
        app.st,
        "session_state",
        _SessionState(sidebar_open=False, last_result=None, conversation_state=SimpleNamespace(debug_state={}), uploaded_contexts=[]),
    )

    class _Column:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(app.st, "columns", lambda *args, **kwargs: [_Column(), _Column()])
    monkeypatch.setattr(app.st, "container", lambda: SimpleNamespace(empty=lambda: SimpleNamespace(), container=lambda: _Column()))

    app.main()

    assert calls[0] == "set_page_config"
    assert "markdown" in calls
