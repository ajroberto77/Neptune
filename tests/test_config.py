"""Config resolution: a .env file with bare connection names must take effect, but the
shell environment still wins (so the test suite's DATABASE_URL stays authoritative)."""
from __future__ import annotations

from neptune.config import Settings, get_settings


def test_promoted_factors_default_empty():
    assert Settings().promoted == ()


def test_promoted_factors_parses_comma_string_normalized():
    # Env / string form: comma- or space-separated, upper-cased and de-duplicated.
    s = Settings(promoted_factors="bab, ivol bab")
    assert s.promoted == ("BAB", "IVOL")


def test_promoted_factors_accepts_list():
    assert Settings(promoted_factors=["BAB", "AMIHUD"]).promoted == ("BAB", "AMIHUD")


def test_dotenv_bare_name_is_honored(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PORTFOLIO_DATABASE_URL", raising=False)
    (tmp_path / ".env").write_text(
        "PORTFOLIO_DATABASE_URL=postgresql+psycopg://u:p@h:5432/neptune_portfolios\n"
    )
    assert get_settings().portfolio_url.endswith("/neptune_portfolios")


def test_dotenv_with_utf8_bom_is_honored(tmp_path, monkeypatch):
    """PowerShell's ``Set-Content -Encoding UTF8`` prepends a BOM; the parser must still read
    the FIRST key correctly (else the portfolio URL silently falls back to in-memory SQLite)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PORTFOLIO_DATABASE_URL", raising=False)
    monkeypatch.delenv("SECURITIES_DATABASE_URL", raising=False)
    # utf-8-sig == UTF-8 with BOM, exactly what PowerShell writes.
    (tmp_path / ".env").write_text(
        "PORTFOLIO_DATABASE_URL=postgresql+psycopg://u:p@h:5434/neptune_portfolios\n"
        "SECURITIES_DATABASE_URL=postgresql+psycopg://u:p@h:5434/neptune_securities\n",
        encoding="utf-8-sig",
    )
    s = get_settings()
    assert s.portfolio_url.endswith("/neptune_portfolios")  # first line — the BOM victim
    assert s.securities_url.endswith("/neptune_securities")


def test_shell_env_wins_over_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DATABASE_URL=sqlite+pysqlite:///from_dotenv\n")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///from_shell")
    assert "from_shell" in get_settings().database_url


def test_no_dotenv_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # empty dir, no .env
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert get_settings().database_url.startswith("sqlite")
