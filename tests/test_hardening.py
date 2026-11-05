"""The container and the CI workflows keep their hardening, checked without Docker or GitHub.

CI's docker job builds and runs the image (.github/workflows/ci.yml); this checks, on every test
run, what a later edit could quietly undo: the examples publish the port on 127.0.0.1 only (the API
has no login), the image runs unprivileged on base images pinned by digest, every action is pinned
to a commit, Dependabot proposes the next version of each pin, and CI audits the dependencies.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_the_docker_examples_publish_the_port_on_loopback_only():
    """`-p 8000:8000` publishes on every interface of the host, past its firewall."""
    for rel in ("README.md", "Dockerfile", ".github/workflows/ci.yml"):
        text = _read(rel)
        assert "-p 8000:8000" not in text, rel
        for line in (ln for ln in text.splitlines() if "docker run" in ln):
            for port in re.findall(r"(?:-p|--publish)[ =](\S+)", line):
                assert port.startswith("127.0.0.1:"), f"{rel}: {line.strip()}"
    for rel in ("README.md", "Dockerfile"):
        text = " ".join(_read(rel).split())
        assert "-p 127.0.0.1:8000:8000" in text, rel
        assert "has no login" in text, f"{rel} says why the port stays on this machine"


def _final_stage(dockerfile: str) -> list[str]:
    lines = [ln.strip() for ln in dockerfile.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    last_from = max(i for i, ln in enumerate(lines) if ln.upper().startswith("FROM "))
    return lines[last_from:]


def test_the_image_runs_unprivileged_on_pinned_base_images():
    text = _read("Dockerfile")
    froms = re.findall(r"^FROM\s+(\S+)", text, flags=re.M)
    assert len(froms) == 2
    for image in froms:
        # the tag says what it is, the digest makes it so
        assert re.fullmatch(r"[\w.\-/]+:[\w.\-]+@sha256:[0-9a-f]{64}", image), image
    stage = _final_stage(text)
    users = [ln.split(None, 1)[1] for ln in stage if ln.upper().startswith("USER ")]
    assert users, "the final stage sets a USER"
    assert users[-1].split(":")[0] not in ("root", "0"), users[-1]
    assert users[-1].startswith("10001"), "the uid the README documents"
    user_at = max(i for i, ln in enumerate(stage) if ln.upper().startswith("USER "))
    assert not any(ln.upper().startswith("CMD") for ln in stage[:user_at]), "the server starts as that user"
    # the server writes only to data/: nothing else becomes its own
    assert "chown -R" not in text
    assert re.findall(r"chown\s+\S+\s+(\S+)", text) == ["/app/data"]


def test_every_action_is_pinned_to_a_commit():
    """A tag can be moved to other code by whoever controls the action's repository; a commit cannot."""
    assert WORKFLOWS, "the workflows are found"
    for path in WORKFLOWS:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(?:-\s+)?uses:\s*(.+?)\s*$", line)
            if not m or m.group(1).startswith(("./", "docker://")):
                continue
            # owner/repo[/path]@<40 hex> # vN[.N.N]: the comment is what Dependabot updates beside it
            assert re.fullmatch(r"[\w.\-]+/[\w.\-/]+@[0-9a-f]{40} # v\d+(?:\.\d+)*", m.group(1)), \
                f"{path.name}: {line.strip()}"


def test_dependabot_proposes_the_next_version_of_every_pin():
    text = _read(".github/dependabot.yml")
    pairs = set(re.findall(r"package-ecosystem:\s*(\S+)\s*\n\s*directory:\s*(\S+)", text))
    assert {("github-actions", "/"), ("npm", "/web"), ("pip", "/"), ("docker", "/")} <= pairs
    assert text.count("interval: weekly") >= 4


def test_ci_audits_the_dependencies_and_checks_the_images_user():
    ci = _read(".github/workflows/ci.yml")
    assert re.search(r"^  audit:", ci, flags=re.M)
    assert "pip-audit -r constraints.txt" in ci
    assert "npm audit --omit=dev --audit-level=high" in ci
    docker = ci[ci.index("\n  docker:"):]
    assert "id -u" in docker and 'test "$uid" != 0' in docker
    codeql = _read(".github/workflows/codeql.yml")
    assert "python" in codeql and "javascript-typescript" in codeql
    assert "security-events: write" in codeql
