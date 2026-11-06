#!/usr/bin/env python3
"""Post-install configuration for Claude Code devcontainer.

Runs on container creation to set up:
- Claude settings (bypassPermissions mode)
- Tmux configuration (200k history, mouse support)
- Directory ownership fixes for mounted volumes
- PostgreSQL: start server, create `vscode` role/database, self-check
"""

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path


def setup_claude_settings():
    """Configure Claude Code with bypassPermissions enabled.

    Merges host settings (mounted read-only at ~/.claude/host-settings.json)
    with container defaults, ensuring bypassPermissions is always enabled.
    """
    claude_dir = Path.home() / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    settings_file = claude_dir / "settings.json"
    host_settings_file = claude_dir / "host-settings.json"

    # Start with host settings, ignoring any stale container settings
    settings = {}
    if host_settings_file.exists():
        with contextlib.suppress(json.JSONDecodeError):
            settings = json.loads(host_settings_file.read_text())
            print(f"[post_install] Loaded host settings from: {host_settings_file}", file=sys.stderr)

    # Always enforce bypassPermissions mode
    if "permissions" not in settings:
        settings["permissions"] = {}
    settings["permissions"]["defaultMode"] = "bypassPermissions"

    settings_file.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print(f"[post_install] Claude settings configured: {settings_file}", file=sys.stderr)


def setup_tmux_config():
    """Configure tmux with 200k history, mouse support, and vi keys."""
    tmux_conf = Path.home() / ".tmux.conf"

    if tmux_conf.exists():
        print("[post_install] Tmux config exists, skipping", file=sys.stderr)
        return

    config = """\
# 200k line scrollback history
set-option -g history-limit 200000

# Enable mouse support
set -g mouse on

# Use vi keys in copy mode
setw -g mode-keys vi

# Start windows and panes at 1, not 0
set -g base-index 1
setw -g pane-base-index 1

# Renumber windows when one is closed
set -g renumber-windows on

# Faster escape time for vim
set -sg escape-time 10

# True color support
set -g default-terminal "tmux-256color"
set -ag terminal-overrides ",xterm-256color:RGB"

# Terminal features (ghostty, cursor shape in vim)
set -as terminal-features ",xterm-ghostty:RGB"
set -as terminal-features ",xterm*:RGB"
set -ga terminal-overrides ",xterm*:colors=256"
set -ga terminal-overrides '*:Ss=\\E[%p1%d q:Se=\\E[ q'

# Status bar
set -g status-style 'bg=#333333 fg=#ffffff'
set -g status-left '[#S] '
set -g status-right '%Y-%m-%d %H:%M'
"""
    tmux_conf.write_text(config, encoding="utf-8")
    print(f"[post_install] Tmux configured: {tmux_conf}", file=sys.stderr)


def fix_directory_ownership():
    """Fix ownership of mounted volumes that may have root ownership."""
    uid = os.getuid()
    gid = os.getgid()

    dirs_to_fix = [
        Path.home() / ".claude",
        Path("/commandhistory"),
        Path.home() / ".config" / "gh",
    ]

    for dir_path in dirs_to_fix:
        if dir_path.exists():
            try:
                # Use sudo to fix ownership if needed
                stat_info = dir_path.stat()
                if stat_info.st_uid != uid:
                    subprocess.run(
                        ["sudo", "chown", "-R", f"{uid}:{gid}", str(dir_path)],
                        check=True,
                        capture_output=True,
                    )
                    print(f"[post_install] Fixed ownership: {dir_path}", file=sys.stderr)
            except (PermissionError, subprocess.CalledProcessError) as e:
                print(
                    f"[post_install] Warning: Could not fix ownership of {dir_path}: {e}",
                    file=sys.stderr,
                )


def setup_global_gitignore():
    """Set up global gitignore and local git config.

    Since ~/.gitconfig is mounted read-only from host, we create a local
    config file that includes the host config and adds container-specific
    settings like core.excludesfile and delta configuration.

    GIT_CONFIG_GLOBAL env var (set in devcontainer.json) points git to this
    local config as the "global" config.
    """
    home = Path.home()
    gitignore = home / ".gitignore_global"
    local_gitconfig = home / ".gitconfig.local"
    host_gitconfig = home / ".gitconfig"

    # Create global gitignore with common patterns
    patterns = """\
# Claude Code
.claude/

# macOS
.DS_Store
.AppleDouble
.LSOverride
._*

# Python
*.pyc
*.pyo
__pycache__/
*.egg-info/
.eggs/
*.egg
.venv/
venv/
.mypy_cache/
.ruff_cache/

# Node
node_modules/
.npm/

# Editors
*.swp
*.swo
*~
.idea/
.vscode/
*.sublime-*

# Misc
*.log
.env.local
.env.*.local
"""
    gitignore.write_text(patterns, encoding="utf-8")
    print(f"[post_install] Global gitignore created: {gitignore}", file=sys.stderr)

    # Create local git config that includes host config and sets excludesfile + delta
    # Delta config is included here so it works even if host doesn't have it configured
    local_config = f"""\
# Container-local git config
# Includes host config (mounted read-only) and adds container settings

[include]
    path = {host_gitconfig}

[core]
    excludesfile = {gitignore}
    pager = delta

[interactive]
    diffFilter = delta --color-only

[delta]
    navigate = true
    light = false
    line-numbers = true
    side-by-side = false

[merge]
    conflictstyle = diff3

[diff]
    colorMoved = default
"""
    local_gitconfig.write_text(local_config, encoding="utf-8")
    print(f"[post_install] Local git config created: {local_gitconfig}", file=sys.stderr)


PG_USER = "vscode"
PG_PASSWORD = "vscode"  # Dev-only: Postgres listens on localhost inside the container.


def _psql_as_postgres(sql):
    """Run SQL as the postgres superuser over the local socket; return stdout."""
    result = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-tAc", sql],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def setup_postgres():
    """Start PostgreSQL and create a `vscode` superuser role and database.

    The container has no systemd, so the server is started explicitly here
    (postCreateCommand runs before postStartCommand on first create) and on
    every later start by postStartCommand. Idempotent.
    """
    subprocess.run(["sudo", "service", "postgresql", "start"], check=True, capture_output=True)
    subprocess.run(["pg_isready", "-h", "localhost", "-t", "30"], check=True, capture_output=True)

    _psql_as_postgres(
        "DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{PG_USER}') THEN "
        f"CREATE ROLE {PG_USER} WITH LOGIN SUPERUSER PASSWORD '{PG_PASSWORD}'; "
        "END IF; END $$;"
    )
    if _psql_as_postgres(f"SELECT 1 FROM pg_database WHERE datname = '{PG_USER}'") != "1":
        _psql_as_postgres(f"CREATE DATABASE {PG_USER} OWNER {PG_USER}")

    # Self-check: TCP + password auth, the path app clients use.
    result = subprocess.run(
        ["psql", "-h", "localhost", "-U", PG_USER, "-d", PG_USER, "-tAc", "SELECT 1"],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PGPASSWORD": PG_PASSWORD},
    )
    if result.stdout.strip() != "1":
        raise RuntimeError(f"unexpected self-check output: {result.stdout!r}")
    print(
        f"[post_install] PostgreSQL ready: postgres://{PG_USER}:{PG_PASSWORD}@localhost:5432/{PG_USER}",
        file=sys.stderr,
    )


def main():
    """Run all post-install configuration."""
    print("[post_install] Starting post-install configuration...", file=sys.stderr)

    setup_claude_settings()
    setup_tmux_config()
    fix_directory_ownership()
    setup_global_gitignore()

    try:
        setup_postgres()
    except (subprocess.CalledProcessError, RuntimeError) as e:
        stderr = getattr(e, "stderr", "") or ""
        print(f"[post_install] ERROR: PostgreSQL setup failed: {e}\n{stderr}", file=sys.stderr)
        sys.exit(1)

    print("[post_install] Configuration complete!", file=sys.stderr)


if __name__ == "__main__":
    main()
