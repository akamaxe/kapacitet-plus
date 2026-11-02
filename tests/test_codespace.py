"""The codespace opened from the README link (.devcontainer/): what it promises holds together.

The image itself is built by .github/workflows/devcontainer.yml; this checks, without Docker, the
parts a later edit could quietly break: the port it forwards is the one the server uses, it
downloads the published data and keeps the upload as the way back, the scripts it runs exist and
stay runnable on Linux, and the links point at this repository and its data release.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DC = ROOT / ".devcontainer"
LINK = "https://codespaces.new/akamaxe/kapacitet-plus?quickstart=1"
DATA_ZIP = "https://github.com/akamaxe/kapacitet-plus/releases/download/data/kapacitet-data.zip"


def _config() -> dict:
    return json.loads((DC / "devcontainer.json").read_text(encoding="utf-8"))


def test_the_codespace_forwards_the_servers_port_and_opens_it_once():
    cfg = _config()
    port = int(os.environ.get("KAPACITET_PORT", "8000"))
    assert cfg["forwardPorts"] == [port]
    attrs = cfg["portsAttributes"][str(port)]
    # once: the restart after a bundle is loaded must not open a second tab
    assert attrs["onAutoForward"] == "openBrowserOnce"


def test_the_codespace_allows_the_upload_and_runs_its_scripts():
    cfg = _config()
    assert cfg["containerEnv"]["KAPACITET_SETUP_UPLOAD"] == "1"
    assert cfg["onCreateCommand"] == "bash .devcontainer/install.sh"
    assert cfg["postCreateCommand"] == "bash .devcontainer/data.sh"
    assert cfg["postAttachCommand"] == "bash .devcontainer/start.sh"
    for name in ("install.sh", "data.sh", "start.sh"):
        body = (DC / name).read_bytes()
        assert body.startswith(b"#!/usr/bin/env bash\n"), name
        assert b"\r\n" not in body, f"{name} has Windows line endings and would not run in the codespace"
    # the server's log goes to a terminal: what keeps an idle codespace from stopping mid-use
    assert "tail -n 50 -F" in (DC / "start.sh").read_text(encoding="utf-8")


def test_the_guide_and_the_readme_link_to_this_repositorys_codespace():
    assert LINK in (ROOT / "docs" / "codespaces.md").read_text(encoding="utf-8")
    assert LINK in (ROOT / "README.md").read_text(encoding="utf-8")
    assert _config()["customizations"]["codespaces"]["openFiles"] == ["docs/codespaces.md"]


def test_the_codespace_and_the_setup_page_fetch_the_published_data_under_its_licence():
    # one address for the archive, in the script that downloads it and on the page that asks for it
    assert DATA_ZIP in (DC / "data.sh").read_text(encoding="utf-8")
    assert DATA_ZIP in (ROOT / "web" / "src" / "components" / "Setup.tsx").read_text(encoding="utf-8")
    assert DATA_ZIP in (ROOT / "README.md").read_text(encoding="utf-8")
    licence = (ROOT / "DATA-LICENSE.md").read_text(encoding="utf-8")
    assert "Open Database License" in licence and "© OpenStreetMap contributors" in licence
    assert "Elpriser tillhandahålls av Elpriset just nu.se" in licence
