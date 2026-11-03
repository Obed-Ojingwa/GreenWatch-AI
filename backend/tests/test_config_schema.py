import re
from pathlib import Path

import pytest

from app.core.config import Settings, production_problems
from app.db.models import Base
from app.db.session import normalize_database_url

SCHEMA = (Path(__file__).resolve().parents[1] / "app" / "db" / "schema.sql").read_text()
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase_migrations"
    / "20261030164200_initial_schema.sql"
).read_text()


def _sql_columns() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for name, body in re.findall(r"create table (\w+) \(\n(.*?)\n\);", SCHEMA, flags=re.S):
        cols = set()
        for line in body.splitlines():
            m = re.match(r"^  ([a-z0-9_]+) ", line)  # two-space indent = a column (constraints are indented deeper
            if m and m.group(1) not in {"unique", "primary", "check", "constraint", "foreign"}:
                cols.add(m.group(1))
        out[name] = cols
    return out


def test_schema_sql_matches_sqlalchemy_models():
    sql = _sql_columns()
    from app.db import models  # noqa: F401
    model_tables = {t.name: {c.name for c in t.columns} for t in Base.metadata.tables.values()}
    assert set(sql) == set(model_tables), "tables differ between schema.sql and models.py"
    for table, cols in model_tables.items():
        assert sql[table] == cols, f"columns differ for {table}: sql-only={sql[table] - cols}, model-only={cols - sql[table]}"


def test_schema_enables_rls_on_every_table():
    for table in _sql_columns():
        assert f"alter table {table} enable row level security;" in SCHEMA


def test_supabase_initial_migration_matches_canonical_schema():
    sql_migration = "\n".join(line for line in MIGRATION.splitlines() if not line.startswith("--"))
    sql_schema = "\n".join(line for line in SCHEMA.splitlines() if not line.startswith("--"))
    assert sql_migration.strip() == sql_schema.strip()


def test_default_agent_call_budget_covers_three_categories_with_one_retry():
    settings = Settings(_env_file=None)
    full_run_calls = 2 + 3 * 7 + 1 + 1 + (3 * 2 * 2)
    assert settings.max_tool_calls_per_run == 40
    assert settings.max_qc_retries == 1
    assert full_run_calls <= settings.max_tool_calls_per_run


def test_render_environment_variables_override_settings_defaults(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example.invalid/greenwatch")
    monkeypatch.setenv(
        "FRONTEND_ORIGIN",
        "https://greenwatch-ai-theta.vercel.app,https://greenwatch-ai-670g.onrender.com",
    )
    settings = Settings(_env_file=None)
    assert settings.environment == "production"
    assert settings.database_url == "postgresql://db.example.invalid/greenwatch"
    assert settings.frontend_origin == (
        "https://greenwatch-ai-theta.vercel.app,https://greenwatch-ai-670g.onrender.com"
    )


def test_database_url_is_normalised_for_psycopg3():
    assert normalize_database_url("postgres://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"
    assert normalize_database_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_database_url("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_database_url("sqlite:///x.db") == "sqlite:///x.db"


def test_database_url_rejects_supabase_https_project_url():
    with pytest.raises(ValueError, match="DATABASE_URL must be a PostgreSQL connection string"):
        normalize_database_url("https://example.supabase.co")


def test_production_refuses_unsafe_config():
    base = dict(environment="production")
    bad = production_problems(Settings(**base, _env_file=None))
    assert len(bad) >= 4 and any("DEMO_ACCESS_KEY" in p for p in bad)
    good = Settings(
        environment="production", demo_access_key="x" * 20, database_url="postgresql://u:p@h/db",
        storage_backend="supabase", supabase_url="https://x.supabase.co", supabase_service_key="k",
        frontend_origin="https://greenwatch.example", _env_file=None,
        meta_api_key="k", meta_base_url="https://api.meta.ai/v1", agent_model="muse-spark-1.3",
        sam_model="sam-3.1", meta_chat_path="chat/completions", meta_segment_path="responses",
    )
    assert production_problems(good) == []
    weak_demo_key = good.model_copy(update={"demo_access_key": "change-me-to-a-long-random-string"})
    assert any("DEMO_ACCESS_KEY" in problem for problem in production_problems(weak_demo_key))
    bad_storage_url = Settings(**{
        **good.model_dump(),
        "supabase_url": 'https://x.supabase.co"',
    }, _env_file=None)
    assert any("SUPABASE_URL" in problem for problem in production_problems(bad_storage_url))
    assert production_problems(Settings(environment="development", _env_file=None)) == []
