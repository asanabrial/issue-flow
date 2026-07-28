#!/usr/bin/env python3
"""Cross-shell bundle-sync acceptance tests."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FILES = [
    "SKILL.md",
    "references/runtime-notes.md",
    "install.sh",
    "install.ps1",
    "bundle.manifest",
]
FAILURES: list[str] = []


def check(name: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(name)
    print(f"{'OK  ' if condition else 'FAIL'} {name}")


def shell_commands() -> list[tuple[str, list[str]]]:
    pwsh = shutil.which("pwsh")
    sh = shutil.which("sh")
    if not sh and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/sh.exe"
        sh = str(candidate) if candidate.is_file() else None
    if not pwsh or not sh:
        raise RuntimeError(f"both pwsh and POSIX sh are required, found pwsh={pwsh!r}, sh={sh!r}")
    return [("PowerShell", [pwsh]), ("POSIX", [sh])]


def fixture(base: Path, *, blocked: bool = False, missing: bool = False,
            omitted: bool = False, stale: bool = False) -> tuple[Path, Path]:
    source, installed = base / "source", base / "installed"
    version = next(line.split('"')[1] for line in (ROOT / "SKILL.md").read_text(encoding="utf-8").splitlines()
                   if line.startswith("  version: "))
    for directory in (source, installed):
        for rel in FILES[:-1]:
            target = directory / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, target)
        (directory / "bundle.manifest").write_text(
            f"issue-flow-bundle-v1 {version}\n" + "\n".join(FILES) + "\n", encoding="utf-8"
        )
    source_notes = source / "references/runtime-notes.md"
    source_notes.write_text(source_notes.read_text(encoding="utf-8") + "\nSYNC-COMPANION\n",
                            encoding="utf-8")
    installed_skill = installed / "SKILL.md"
    installed_skill.write_text(
        installed_skill.read_text(encoding="utf-8").replace(
            "| Delivery authorisation | ask | ask |",
            "| Delivery authorisation | pre-authorised | ask |",
        ),
        encoding="utf-8",
    )
    (installed / "operator.local.md").write_text("local-secret-policy\n", encoding="utf-8")
    if stale:
        manifest = source / "bundle.manifest"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace(version, "9.9.9", 1),
                            encoding="utf-8")
    if missing:
        with (source / "bundle.manifest").open("a", encoding="utf-8") as manifest:
            manifest.write("references/missing.md\n")
    if omitted:
        manifest = source / "bundle.manifest"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace(
            "references/runtime-notes.md\n", ""), encoding="utf-8")
    if blocked:
        with (source / "bundle.manifest").open("a", encoding="utf-8") as manifest:
            manifest.write("blocked/child.md\n")
        (source / "blocked").mkdir()
        (source / "blocked/child.md").write_text("new\n", encoding="utf-8")
        (installed / "blocked").write_text("not a directory\n", encoding="utf-8")
    return source, installed


def snapshot(directory: Path) -> dict[str, bytes]:
    return {rel: (directory / rel).read_bytes() for rel in FILES}


def sync_command(kind: str, executable: list[str], installed: Path, source: Path,
                 *, directory_source: bool = False) -> subprocess.CompletedProcess:
    incoming = source if directory_source else source / "SKILL.md"
    if kind == "PowerShell":
        command = [*executable, "-NoProfile", "-File", str(installed / "install.ps1"),
                   "sync", "-From", str(incoming)]
    else:
        command = [*executable, str(installed / "install.sh"), "sync", "--from", str(incoming)]
    env = os.environ.copy()
    env["HOME"] = str(installed.parent / "home")
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)


manifest_paths = set((ROOT / "bundle.manifest").read_text(encoding="utf-8").splitlines()[1:])
skill_links = set(re.findall(r"\(([^)#]+\.md)(?:#[^)]+)?\)",
                             (ROOT / "SKILL.md").read_text(encoding="utf-8")))
runtime_files = skill_links | {str(path.relative_to(ROOT)).replace("\\", "/")
                               for path in (ROOT / "bindings").glob("*.md")}
runtime_files |= {"SKILL.md", "bundle.manifest", "install.sh", "install.ps1", "scripts/github.py"}
check("the manifest covers every directly reachable runtime file", runtime_files <= manifest_paths)


for kind, executable in shell_commands():
    with tempfile.TemporaryDirectory() as root:
        source, installed = fixture(Path(root))
        result = sync_command(kind, executable, installed, source)
        check(f"{kind} syncs the companion bundle from a SKILL path",
              result.returncode == 0
              and "SYNC-COMPANION" in (installed / "references/runtime-notes.md").read_text()
              and "pre-authorised" in (installed / "SKILL.md").read_text()
              and (installed / "operator.local.md").read_text() == "local-secret-policy\n")

    with tempfile.TemporaryDirectory() as root:
        source, installed = fixture(Path(root))
        result = sync_command(kind, executable, installed, source, directory_source=True)
        check(f"{kind} accepts a bundle directory", result.returncode == 0)

    with tempfile.TemporaryDirectory() as root:
        source, installed = fixture(Path(root), missing=True)
        before = snapshot(installed)
        result = sync_command(kind, executable, installed, source)
        check(f"{kind} rejects an incomplete bundle before writes",
              result.returncode != 0 and snapshot(installed) == before)

    with tempfile.TemporaryDirectory() as root:
        source, installed = fixture(Path(root), stale=True)
        before = snapshot(installed)
        result = sync_command(kind, executable, installed, source)
        check(f"{kind} rejects a manifest/SKILL version mismatch before writes",
              result.returncode != 0 and snapshot(installed) == before)

    with tempfile.TemporaryDirectory() as root:
        source, installed = fixture(Path(root), omitted=True)
        before = snapshot(installed)
        result = sync_command(kind, executable, installed, source)
        check(f"{kind} rejects omission of an installed boundary file",
              result.returncode != 0 and snapshot(installed) == before)

    with tempfile.TemporaryDirectory() as root:
        source, installed = fixture(Path(root), blocked=True)
        before = snapshot(installed)
        result = sync_command(kind, executable, installed, source)
        check(f"{kind} rolls back a partial bundle write",
              result.returncode != 0 and snapshot(installed) == before)

print()
print(f"{len(FAILURES)} failure(s)" + (f": {FAILURES}" if FAILURES else ""))
raise SystemExit(1 if FAILURES else 0)
