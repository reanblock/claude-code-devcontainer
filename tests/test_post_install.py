"""Tests for post_install.setup_claude_plugins.

Run with: uv run --no-project --with pytest pytest tests
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import post_install


class FakeRun:
    """Records `claude` invocations; fails any whose args contain a string in `fail`."""

    def __init__(self, fail=()):
        self.calls = []
        self.fail = fail

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd[2:])  # drop "claude", "plugin"
        failed = any(f in cmd for f in self.fail)
        return subprocess.CompletedProcess(cmd, 1 if failed else 0, stdout="", stderr="boom" if failed else "")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_no_enabled_plugins_runs_nothing(tmp_path):
    run = FakeRun()
    post_install.setup_claude_plugins({}, claude_dir=tmp_path, run=run)
    post_install.setup_claude_plugins({"enabledPlugins": {"a@m": False}}, claude_dir=tmp_path, run=run)
    assert run.calls == []


def test_installs_plugin_from_already_known_marketplace(tmp_path):
    write_json(tmp_path / "plugins" / "known_marketplaces.json", {"official": {}})
    run = FakeRun()
    settings = {"enabledPlugins": {"mattpocock-skills@official": True, "off@official": False}}

    post_install.setup_claude_plugins(settings, claude_dir=tmp_path, run=run)

    assert run.calls == [["install", "mattpocock-skills@official"]]


def test_adds_missing_marketplace_from_host_then_installs(tmp_path):
    write_json(
        tmp_path / "host-plugins" / "known_marketplaces.json",
        {"official": {"source": {"source": "github", "repo": "anthropics/claude-plugins-official"}}},
    )
    run = FakeRun()

    post_install.setup_claude_plugins({"enabledPlugins": {"p@official": True}}, claude_dir=tmp_path, run=run)

    assert run.calls == [
        ["marketplace", "add", "anthropics/claude-plugins-official"],
        ["install", "p@official"],
    ]


def test_uses_extra_known_marketplaces_git_url(tmp_path):
    settings = {
        "enabledPlugins": {"p@team": True},
        "extraKnownMarketplaces": {"team": {"source": {"source": "git", "url": "https://example.com/m.git"}}},
    }
    run = FakeRun()

    post_install.setup_claude_plugins(settings, claude_dir=tmp_path, run=run)

    assert run.calls == [["marketplace", "add", "https://example.com/m.git"], ["install", "p@team"]]


def test_skips_plugins_whose_marketplace_is_local_or_unknown(tmp_path, capsys):
    write_json(
        tmp_path / "host-plugins" / "known_marketplaces.json",
        {"local": {"source": {"source": "directory", "path": "/Users/x/m"}}},
    )
    run = FakeRun()
    settings = {"enabledPlugins": {"a@local": True, "b@nowhere": True}}

    post_install.setup_claude_plugins(settings, claude_dir=tmp_path, run=run)

    assert run.calls == []
    err = capsys.readouterr().err
    assert "Skipping plugin a@local" in err and "Skipping plugin b@nowhere" in err


def test_failures_warn_and_continue(tmp_path, capsys):
    write_json(tmp_path / "plugins" / "known_marketplaces.json", {"m": {}})
    run = FakeRun(fail=("bad@m",))

    post_install.setup_claude_plugins({"enabledPlugins": {"bad@m": True, "good@m": True}}, claude_dir=tmp_path, run=run)

    assert run.calls == [["install", "bad@m"], ["install", "good@m"]]
    err = capsys.readouterr().err
    assert "could not install plugin bad@m: boom" in err
    assert "Installed plugin: good@m" in err


def test_missing_claude_binary_is_a_warning(tmp_path, capsys):
    write_json(tmp_path / "plugins" / "known_marketplaces.json", {"m": {}})

    def run(cmd, **kwargs):
        raise FileNotFoundError("claude")

    post_install.setup_claude_plugins({"enabledPlugins": {"p@m": True}}, claude_dir=tmp_path, run=run)

    assert "could not install plugin p@m" in capsys.readouterr().err


def test_setup_claude_settings_returns_merged_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    write_json(tmp_path / ".claude" / "host-settings.json", {"enabledPlugins": {"p@m": True}})

    settings = post_install.setup_claude_settings()

    assert settings["enabledPlugins"] == {"p@m": True}
    assert settings["permissions"]["defaultMode"] == "bypassPermissions"
