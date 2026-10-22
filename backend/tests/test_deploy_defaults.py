"""Compose example defaults vs. code defaults for body compaction (#117).

docker-compose.yml ships ARTICLE_RETENTION_ENABLED on for both api and worker,
while services.retention keeps its fail-closed code default for deployments
that do not go through Compose. Text parsing only: PyYAML is not a declared
dependency.
"""
import importlib.util
import re
from pathlib import Path

import pytest

from services import retention

ROOT = Path(__file__).resolve().parents[2]


def compose_environment(service: str) -> dict[str, str]:
    lines = (ROOT / "docker-compose.yml").read_text().splitlines()
    start = lines.index(f"  {service}:")
    env: dict[str, str] = {}
    in_env = False
    for line in lines[start + 1:]:
        if re.match(r"^  \S", line):  # next service
            break
        if line == "    environment:":
            in_env = True
            continue
        if in_env:
            if re.match(r"^    \S", line):  # next service key
                break
            match = re.match(r"^      ([A-Z0-9_]+): (.*?)(\s+#.*)?$", line)
            if match:
                env[match.group(1)] = match.group(2)
    return env


@pytest.mark.parametrize("service", ["api", "worker"])
def test_compose_example_enables_retention_but_keeps_override(service):
    env = compose_environment(service)
    # `${VAR:-true}`: unset or empty → true; an explicit false in .env wins.
    assert env["ARTICLE_RETENTION_ENABLED"] == "${ARTICLE_RETENTION_ENABLED:-true}"
    # The schedule itself is unchanged by #117.
    assert env["ARTICLE_RETENTION_DAYS"] == "${ARTICLE_RETENTION_DAYS:-30}"
    assert env["ARTICLE_RETENTION_BATCH_SIZE"] == "${ARTICLE_RETENTION_BATCH_SIZE:-200}"
    assert env["ARTICLE_RETENTION_INTERVAL_MINUTES"] == "${ARTICLE_RETENTION_INTERVAL_MINUTES:-1440}"


def test_retention_switch_is_independent_of_other_compose_switches():
    worker = compose_environment("worker")
    assert worker["FEED_REFRESH_ENABLED"] == "${FEED_REFRESH_ENABLED:-true}"
    assert worker["FEED_DISCOVERY_ENABLED"] == "${FEED_DISCOVERY_ENABLED:-false}"


def test_env_example_does_not_pin_retention_value():
    # A fresh .env copied from the example must leave the Compose default in
    # charge; an active line here would be copied verbatim by gen_env.py.
    text = (ROOT / ".env.example").read_text()
    assert not re.search(r"^ARTICLE_RETENTION_ENABLED=", text, re.M)
    assert re.search(r"^# ARTICLE_RETENTION_ENABLED=true$", text, re.M)


def test_code_default_outside_compose_stays_off(monkeypatch):
    monkeypatch.delenv("ARTICLE_RETENTION_ENABLED", raising=False)
    assert retention.retention_enabled() is False


def _load_gen_env():
    spec = importlib.util.spec_from_file_location("gen_env", ROOT / "scripts" / "gen_env.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("existing, expected", [
    (None, "ARTICLE_RETENTION_ENABLED=true"),
    ("false", "已停用（ARTICLE_RETENTION_ENABLED=false）"),
    ("true", "已啟用（ARTICLE_RETENTION_ENABLED=true）"),
])
def test_gen_env_reports_but_never_writes_retention_flag(tmp_path, monkeypatch, capsys, existing, expected):
    gen_env = _load_gen_env()
    example = (ROOT / ".env.example").read_text()
    env_file = tmp_path / ".env"
    if existing is None:
        (tmp_path / ".env.example").write_text(example)
    else:
        env_file.write_text(f"ARTICLE_RETENTION_ENABLED={existing}\n")
    monkeypatch.setattr(gen_env, "ENV_FILE", str(env_file))
    monkeypatch.setattr(gen_env, "ENV_EXAMPLE", str(tmp_path / ".env.example"))

    gen_env.main()

    assert expected in capsys.readouterr().out
    lines = [l for l in env_file.read_text().splitlines() if l.startswith("ARTICLE_RETENTION_ENABLED=")]
    assert lines == ([] if existing is None else [f"ARTICLE_RETENTION_ENABLED={existing}"])
    assert "ARTICLE_RETENTION_ENABLED" not in gen_env.OPTIONAL_MISC_DEFAULTS
