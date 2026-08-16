from pathlib import Path

from tools.integrations.google_auth import GoogleAuth


def test_google_auth_uses_environment_paths(monkeypatch):
    monkeypatch.setenv(
        "GOOGLE_CREDENTIALS_PATH",
        "/srv/jarvis/google/credentials.json",
    )
    monkeypatch.setenv(
        "GOOGLE_TOKEN_PATH",
        "/srv/jarvis/google/token.json",
    )

    auth = GoogleAuth()

    assert auth.credentials_path == Path(
        "/srv/jarvis/google/credentials.json"
    )
    assert auth.token_path == Path(
        "/srv/jarvis/google/token.json"
    )


def test_google_auth_explicit_paths_override_environment(monkeypatch):
    monkeypatch.setenv(
        "GOOGLE_CREDENTIALS_PATH",
        "/env/google/credentials.json",
    )
    monkeypatch.setenv(
        "GOOGLE_TOKEN_PATH",
        "/env/google/token.json",
    )

    auth = GoogleAuth(
        credentials_path="/explicit/credentials.json",
        token_path="/explicit/token.json",
    )

    assert auth.credentials_path == Path(
        "/explicit/credentials.json"
    )
    assert auth.token_path == Path(
        "/explicit/token.json"
    )


def test_google_auth_keeps_relative_defaults_without_environment(
    monkeypatch,
):
    monkeypatch.delenv("GOOGLE_CREDENTIALS_PATH", raising=False)
    monkeypatch.delenv("GOOGLE_TOKEN_PATH", raising=False)

    auth = GoogleAuth()

    assert auth.credentials_path == Path(
        "config/google/credentials.json"
    )
    assert auth.token_path == Path(
        "config/google/token.json"
    )


def test_env_template_has_no_committed_tavily_secret_and_google_paths():
    template = Path("deploy/env.template").read_text(
        encoding="utf-8"
    )
    values = {}

    for raw_line in template.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name] = value

    assert values["TAVILY_API_KEY"] == ""
    assert values["GOOGLE_CREDENTIALS_PATH"] == (
        "/home/ocelia/jarvis-data/google/credentials.json"
    )
    assert values["GOOGLE_TOKEN_PATH"] == (
        "/home/ocelia/jarvis-data/google/token.json"
    )


def test_google_auth_uses_google_application_credentials_fallback(
    monkeypatch,
):
    monkeypatch.delenv("GOOGLE_CREDENTIALS_PATH", raising=False)
    monkeypatch.setenv(
        "GOOGLE_APPLICATION_CREDENTIALS",
        "/legacy/google/credentials.json",
    )
    monkeypatch.delenv("GOOGLE_TOKEN_PATH", raising=False)

    auth = GoogleAuth()

    assert auth.credentials_path == Path(
        "/legacy/google/credentials.json"
    )
    assert auth.token_path == Path(
        "config/google/token.json"
    )
