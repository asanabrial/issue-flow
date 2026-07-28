#!/usr/bin/env python3
"""Cross-shell acceptance tests for commit-bound skill upgrades."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []
def check(name: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(name)
    print(f"{'OK  ' if condition else 'FAIL'} {name}")
def run(command: list[str], cwd: Path | None = None, env: dict | None = None,
        check_result: bool = False) -> subprocess.CompletedProcess:
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if check_result and result.returncode:
        raise RuntimeError(f"{command} failed: {result.stderr}\n{result.stdout}")
    return result
def git(cwd: Path, *args: str) -> str:
    return run(["git", *args], cwd, check_result=True).stdout.strip()
def shells() -> list[tuple[str, str]]:
    pwsh = shutil.which("pwsh")
    sh = shutil.which("sh")
    if not sh and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/sh.exe"
        sh = str(candidate) if candidate.is_file() else None
    if not pwsh or not sh:
        raise RuntimeError(f"pwsh and POSIX sh are required: pwsh={pwsh!r}, sh={sh!r}")
    return [("PowerShell", pwsh), ("POSIX", sh)]
def write_skill(repo: Path, version: str, companion: str) -> None:
    (repo / "SKILL.md").write_text(
        f"---\nmetadata:\n  version: \"{version}\"\n---\n\n"
        "<!-- issue-flow:config:start -->\n| Setting | Value here | Skill default |\n"
        "|---|---|---|\n| Tracker | github | github |\n<!-- issue-flow:config:end -->\n",
        encoding="utf-8",
    )
    (repo / "references").mkdir(exist_ok=True)
    (repo / "references/runtime-notes.md").write_text(companion, encoding="utf-8")
def repositories(root: Path) -> tuple[Path, Path, str, str]:
    author, remote = root / "author", root / "remote.git"
    author.mkdir()
    git(author, "init", "-b", "main")
    git(author, "config", "user.email", "test@example.com")
    git(author, "config", "user.name", "Installer Test")
    shutil.copy2(ROOT / "install.sh", author / "install.sh")
    shutil.copy2(ROOT / "install.ps1", author / "install.ps1")
    (author / ".gitignore").write_text("operator.local.md\n", encoding="utf-8")
    write_skill(author, "1.10.0", "old companion\n")
    (author / "removed.md").write_text("remove me\n", encoding="utf-8")
    git(author, "add", ".")
    git(author, "commit", "-m", "old")
    old = git(author, "rev-parse", "HEAD")
    git(root, "clone", "--bare", str(author), str(remote))
    git(author, "remote", "add", "origin", str(remote))
    write_skill(author, "1.10.1", "new companion\n")
    (author / "added.md").write_text("new file\n", encoding="utf-8")
    (author / "removed.md").unlink()
    git(author, "add", "-A")
    git(author, "commit", "-m", "new")
    new = git(author, "rev-parse", "HEAD")
    git(author, "push", "origin", "main")
    return author, remote, old, new
def clone_at(remote: Path, target: Path, revision: str) -> None:
    git(target.parent, "clone", str(remote), str(target))
    git(target, "reset", "--hard", revision)
    (target / "operator.local.md").write_bytes(b"local-secret-policy\r\n")
def sync(kind: str, executable: str, installed: Path, source: Path | None = None) -> subprocess.CompletedProcess:
    if kind == "PowerShell":
        command = [executable, "-NoProfile", "-File", str(installed / "install.ps1"), "sync"]
        if source is not None:
            command += ["-From", str(source)]
    else:
        command = [executable, str(installed / "install.sh"), "sync"]
        if source is not None:
            command += ["--from", str(source)]
    env = os.environ.copy()
    env["HOME"] = str(installed.parent / "home")
    env["USERPROFILE"] = env["HOME"]
    return run(command, env=env)
def bootstrap_sync(kind: str, executable: str, root: Path, remote: Path,
                   old: str, new: str, source: Path) -> bool:
    home = root / "bootstrap-home"
    installed = home / ".agents/skills/issue-flow"
    installed.parent.mkdir(parents=True)
    clone_at(remote, installed, old)
    external = root / "external"
    external.mkdir()
    shutil.copy2(ROOT / "install.sh", external / "install.sh")
    shutil.copy2(ROOT / "install.ps1", external / "install.ps1")
    env = os.environ.copy(); env["HOME"] = str(home); env["USERPROFILE"] = str(home)
    command = ([executable, "-NoProfile", "-File", str(external / "install.ps1"),
                "sync", "-From", str(source)] if kind == "PowerShell" else
               [executable, str(external / "install.sh"), "sync", "--from", str(source)])
    (installed / "references/runtime-notes.md").write_text("dirty\n", encoding="utf-8")
    blocked = run(command, env=env)
    safe = blocked.returncode != 0 and git(installed, "rev-parse", "HEAD") == old and (installed / "references/runtime-notes.md").read_text() == "dirty\n"
    git(installed, "reset", "--hard", old)
    result = run(command, env=env)
    return safe and result.returncode == 0 and git(installed, "rev-parse", "HEAD") == new
for kind, executable in shells():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        author, remote, old, new = repositories(root)
        installed = root / "installed"
        clone_at(remote, installed, old)
        (installed / "notes.local").write_text("keep\n", encoding="utf-8")
        result = sync(kind, executable, installed)
        check(f"{kind} syncs the complete origin Git tree",
              result.returncode == 0 and git(installed, "rev-parse", "HEAD") == new
              and (installed / "references/runtime-notes.md").read_text() == "new companion\n"
              and (installed / "added.md").is_file() and not (installed / "removed.md").exists()
              and (installed / "operator.local.md").read_bytes() == b"local-secret-policy\r\n"
              and (installed / "notes.local").read_text() == "keep\n")
        git(installed, "reset", "--hard", old)
        (installed / "references/runtime-notes.md").write_text("dirty\n", encoding="utf-8")
        result = sync(kind, executable, installed)
        check(f"{kind} refuses dirty tracked destination state",
              result.returncode != 0 and git(installed, "rev-parse", "HEAD") == old
              and (installed / "references/runtime-notes.md").read_text() == "dirty\n")
        git(installed, "reset", "--hard", old)
        source = root / "source"
        clone_at(remote, source, new)
        result = sync(kind, executable, installed, source)
        check(f"{kind} accepts a clean checkout of the same repository",
              result.returncode == 0 and git(installed, "rev-parse", "HEAD") == new)
        git(installed, "reset", "--hard", old)
        result = sync(kind, executable, installed, source / "SKILL.md")
        check(f"{kind} rejects a loose SKILL.md source",
              result.returncode != 0 and git(installed, "rev-parse", "HEAD") == old)
        git(installed, "switch", "-c", "feature")
        result = sync(kind, executable, installed)
        check(f"{kind} refuses to rewrite a non-main branch", result.returncode != 0)
        git(installed, "switch", "main")
        runtime_copy = installed.parent / "home/.codex/skills/issue-flow"
        runtime_copy.mkdir(parents=True)
        result = sync(kind, executable, installed)
        check(f"{kind} refuses an independent runtime copy that would stay stale",
              result.returncode != 0 and git(installed, "rev-parse", "HEAD") == old)
        shutil.rmtree(runtime_copy)
        wrong = root / "wrong"
        wrong.mkdir(); git(wrong, "init", "-b", "main")
        git(wrong, "config", "user.email", "test@example.com"); git(wrong, "config", "user.name", "Test")
        (wrong / "x").write_text("x"); git(wrong, "add", "."); git(wrong, "commit", "-m", "wrong")
        git(wrong, "remote", "add", "origin", str(root / "other.git"))
        result = sync(kind, executable, installed, wrong)
        check(f"{kind} rejects a checkout from another origin", result.returncode != 0)
        (installed / "local-commit.md").write_text("unpublished\n", encoding="utf-8")
        git(installed, "add", "local-commit.md"); git(installed, "commit", "-m", "unpublished local commit")
        local = git(installed, "rev-parse", "HEAD")
        result = sync(kind, executable, installed)
        check(f"{kind} preserves clean but divergent local commits",
              result.returncode != 0 and git(installed, "rev-parse", "HEAD") == local)
        git(installed, "reset", "--hard", old)
        check(f"{kind} bootstrap refuses dirty state, forwards source, and upgrades",
              bootstrap_sync(kind, executable, root, remote, old, new, source))
        (author / "notes.local").mkdir()
        (author / "notes.local/incoming.md").write_text("incoming\n", encoding="utf-8")
        git(author, "add", "."); git(author, "commit", "-m", "prefix collision")
        git(author, "push", "origin", "main")
        result = sync(kind, executable, installed)
        check(f"{kind} preserves local trees on prefix collisions",
              result.returncode != 0 and (installed / "notes.local").read_text() == "keep\n")

        shutil.rmtree(author / "notes.local")
        (author / "operator.local.md").write_text("incoming overwrite\n", encoding="utf-8")
        git(author, "add", "-A"); git(author, "add", "-f", "operator.local.md")
        git(author, "commit", "-m", "bad local policy")
        git(author, "push", "origin", "main")
        git(installed, "reset", "--hard", old)
        result = sync(kind, executable, installed)
        bootstrap_root = root / "operator"
        bootstrap_result = bootstrap_sync(kind, executable, bootstrap_root, remote, old, new, source)
        check(f"{kind} rejects a target that tracks local operator policy",
              result.returncode != 0 and git(installed, "rev-parse", "HEAD") == old
              and (installed / "operator.local.md").read_bytes() == b"local-secret-policy\r\n"
              and not bootstrap_result and git(bootstrap_root / "bootstrap-home/.agents/skills/issue-flow", "rev-parse", "HEAD") == old and (bootstrap_root / "bootstrap-home/.agents/skills/issue-flow/operator.local.md").read_bytes() == b"local-secret-policy\r\n")
print()
print(f"{len(FAILURES)} failure(s)" + (f": {FAILURES}" if FAILURES else ""))
raise SystemExit(1 if FAILURES else 0)
