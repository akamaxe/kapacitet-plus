"""The container and the CI workflows keep their hardening, checked without Docker or GitHub.

CI's docker job builds and runs the image (.github/workflows/ci.yml); this checks, on every test
run, what a later edit could quietly undo: the examples publish the port on 127.0.0.1 only (the API
has no login), the image runs unprivileged on base images pinned by digest, every action is pinned
to a commit, Dependabot proposes the next version of each pin, and CI audits the dependencies;
the SBOM and the data attestation ask only for the permissions they need, and the codespace keeps
the sha256 as the check that decides while it reports the attestation.
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
        text = re.sub(r"\\\n\s*", " ", text)  # a command split over lines is one
        for line in (ln for ln in text.splitlines() if "docker run" in ln):
            for port in re.findall(r"(?:-p|--publish)[ =](\S+)", line):
                assert port.startswith("127.0.0.1:"), f"{rel}: {line.strip()}"
    # the same in the guides and the devcontainer scripts, and a bind mount needs the caller's uid
    others = [p.relative_to(ROOT).as_posix() for p in
              [*(ROOT / "docs").glob("*.md"), *(ROOT / ".devcontainer").glob("*.sh")]]
    for rel in ("README.md", "Dockerfile", *others):
        text = re.sub(r"\\\n\s*", " ", _read(rel))
        assert "-p 8000:8000" not in text, rel
        for line in (ln for ln in text.splitlines() if "docker run" in ln):
            for port in re.findall(r"(?:-p|--publish)[ =](\S+)", line):
                assert port.startswith("127.0.0.1:"), f"{rel}: {line.strip()}"
            for src in re.findall(r"(?:-v|--volume)[ =]([^:\s]+):", line):
                if re.match(r"[/.$~]|[A-Za-z]:", src):    # a path, not a named volume
                    assert re.search(r"(?:--user|-u)[ =]", line), f"{rel}: bind mount without --user"
    for rel in ("README.md", "Dockerfile"):
        text = " ".join(_read(rel).replace("#", " ").split())
        assert "-p 127.0.0.1:8000:8000" in text, rel
        assert "whoever reaches the port can use all of the API" in text, f"{rel} says why the port stays on this machine"
        # the image listens on 0.0.0.0, so every example says whether sign-in is wanted
        for line in (ln for ln in re.sub(r"\\\n\s*", " ", _read(rel)).splitlines() if "docker run" in ln and " -p " in ln):
            assert "KAPACITET_AUTH=" in line, f"{rel}: {line.strip()}"


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
    # the data folder is handed over before it becomes a VOLUME, or a new volume starts as root's
    assert text.index("chown 10001:10001") < text.index("VOLUME /app/data")
    assert "chown -R" not in text and "--chown" not in text
    assert not re.search(r"chown\s+--recursive", text)
    assert re.findall(r"chown\s+\S+\s+(\S+)", text) == ["/app/data"]


def test_the_devcontainer_base_image_is_pinned_by_digest():
    image = re.search(r'"image":\s*"([^"]+)"', _read(".devcontainer/devcontainer.json")).group(1)
    assert re.fullmatch(r"[\w.\-/]+:[\w.\-]+@sha256:[0-9a-f]{64}", image), image


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


def test_every_service_image_in_ci_is_pinned_by_digest():
    """CI's service containers (the postgres job's database) are pinned as the base images are."""
    images = [m.group(1) for path in WORKFLOWS for line in path.read_text(encoding="utf-8").splitlines()
              if (m := re.match(r"\s*image:\s*(\S+)\s*$", line))]
    assert images, "the postgres job's service is found"
    for image in images:
        assert re.fullmatch(r"[\w.\-/]+:[\w.\-]+@sha256:[0-9a-f]{64}", image), image


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


def _job_permissions(text: str) -> list[str]:
    """Every `permissions:` block of a workflow (top level and per job), as its indented lines."""
    blocks, lines = [], text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"(\s*)permissions:\s*$", line)
        if not m:
            continue
        indent, body = len(m.group(1)), []
        for nxt in lines[i + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            if nxt.strip():
                body.append(nxt.strip())
        blocks.append("\n".join(body))
    return blocks


def test_the_supply_chain_workflows_ask_only_for_what_they_need():
    """The SBOM and the data attestation set their permissions; only the attestation may sign."""
    sbom = _read(".github/workflows/sbom.yml")
    assert re.search(r"^permissions:\s*\n\s+contents: read\s*$", sbom, flags=re.M)
    assert "anchore/sbom-action@" in sbom and sbom.count("format: cyclonedx-json") == 2
    assert "constraints.txt" in sbom and "path: web" in sbom
    assert "branches: [main]" in sbom

    attest = _read(".github/workflows/attest-data.yml")
    assert "workflow_dispatch:" in attest and re.search(r"release:\s*\n\s+types: \[published\]", attest)
    blocks = _job_permissions(attest)
    assert blocks[0] == "contents: read", "the top level grants only reading"
    assert any({"id-token: write", "attestations: write"} <= set(b.splitlines()) for b in blocks[1:])
    assert "actions/attest-build-provenance@" in attest and "subject-path: kapacitet-data.zip" in attest
    # the asset is checked against the committed pin before it is attested
    assert "config/data-release.json" in attest and "sha256sum -c" in attest and "stat -c %s" in attest
    assert attest.index("sha256sum -c") < attest.index("actions/attest-build-provenance@")

    # no other workflow may mint an OIDC token or write attestations
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        assert re.search(r"^permissions:", text, flags=re.M), f"{path.name} sets its permissions"
        if path.name != "attest-data.yml":
            assert "id-token: write" not in text and "attestations: write" not in text, path.name


def test_the_codespace_still_requires_the_sha256_and_reports_the_attestation():
    data = _read(".devcontainer/data.sh")
    assert 'sha256sum -c --status' in data and "--expect-sha256" in data
    assert "gh attestation verify" in data and "command -v gh" in data
    # the attestation only reports: the sha256 check decides whether the file is unpacked
    attestation = data[data.index("attestation() {"):data.index("\n}\n", data.index("attestation() {"))]
    assert "exit" not in attestation and "return 1" not in attestation
    assert data.index("sha256sum -c") < data.index('attestation "$tmp')
    for rel in ("README.md", "docs/codespaces.md"):
        assert "gh attestation verify kapacitet-data.zip -R akamaxe/kapacitet-plus" in _read(rel), rel
