"""Tests that the devcontainer is named and pathed after the host project folder.

Run with: uv run --no-project --with pytest pytest tests
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROJECT_DIR = "/workspaces/${localWorkspaceFolderBasename}"


def load_config():
    return json.loads((ROOT / "devcontainer.json").read_text())


def test_name_is_host_folder_name():
    assert load_config()["name"] == "${localWorkspaceFolderBasename}"


def test_workspace_path_includes_project_name():
    cfg = load_config()
    assert cfg["workspaceFolder"] == PROJECT_DIR
    assert f"target={PROJECT_DIR}," in cfg["workspaceMount"]


def test_devcontainer_dir_mounted_readonly_inside_workspace():
    mounts = [m for m in load_config()["mounts"] if "/.devcontainer," in m]
    assert mounts == [
        f"source=${{localWorkspaceFolder}}/.devcontainer,target={PROJECT_DIR}/.devcontainer,type=bind,readonly"
    ]


@pytest.mark.skipif(not shutil.which("jq"), reason="jq not installed")
def test_install_sh_drops_old_and_new_devcontainer_mounts_but_keeps_custom(tmp_path):
    custom = "source=/host/data,target=/data,type=bind"
    dc = tmp_path / "devcontainer.json"
    dc.write_text(json.dumps({"mounts": [
        "source=x/.devcontainer,target=/workspace/.devcontainer,type=bind,readonly",
        f"source=x/.devcontainer,target={PROJECT_DIR}/.devcontainer,type=bind,readonly",
        custom,
    ]}))
    # Load install.sh's functions without running its trailing `main "$@"`.
    lib = tmp_path / "install_lib.sh"
    lib.write_text((ROOT / "install.sh").read_text().rsplit('main "$@"', 1)[0])
    out = subprocess.run(
        ["bash", "-c", 'source "$1"; cat "$(extract_mounts_to_file "$2")"', "_", str(lib), str(dc)],
        capture_output=True, text=True, check=True,
        env={**os.environ, "TMPDIR": str(tmp_path)},
    ).stdout
    assert json.loads(out) == [custom]
