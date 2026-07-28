#!/usr/bin/env python3
"""Cross-shell acceptance tests for commit-bound skill upgrades."""
from __future__ import annotations
import os, shutil, subprocess, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = "https://github.com/asanabrial/issue-flow.git"
LEGACY_BASE = "df7082935f7f0f03a460879327ebb1a3ea9a466e"
FAILURES: list[str] = []
def check(name: str, condition: bool) -> None:
    if not condition: FAILURES.append(name)
    print(f"{'OK  ' if condition else 'FAIL'} {name}")
def run(command: list[str], cwd: Path | None = None, env: dict | None = None, check_result: bool = False) -> subprocess.CompletedProcess:
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check_result and result.returncode: raise RuntimeError(f"{command} failed: {result.stderr}\n{result.stdout}")
    return result
def git(cwd: Path, *args: str) -> str:
    return run(["git", *args], cwd, check_result=True).stdout.strip()
def shells() -> list[tuple[str, str]]:
    pwsh, sh = shutil.which("pwsh"), shutil.which("sh")
    if not sh and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/sh.exe"
        sh = str(candidate) if candidate.is_file() else None
    if not pwsh or not sh: raise RuntimeError(f"pwsh and POSIX sh are required: pwsh={pwsh!r}, sh={sh!r}")
    windows = [("Windows PowerShell", shutil.which("powershell"))] if shutil.which("powershell") else []
    return [("PowerShell", pwsh), *windows, ("POSIX", sh)]
def installer(repo: Path, name: str, upstream: str, legacy: bool = False) -> None:
    if legacy and run(["git", "cat-file", "-e", f"{LEGACY_BASE}:{name}"], ROOT).returncode: git(ROOT, "fetch", "-q", "origin", LEGACY_BASE)
    text = git(ROOT, "show", f"{LEGACY_BASE}:{name}") if legacy else (ROOT / name).read_text(encoding="utf-8")
    (repo / name).write_text(text.replace(UPSTREAM, upstream), encoding="utf-8")
def write_skill(repo: Path, version: str, companion: str) -> None:
    (repo / "SKILL.md").write_text(
        f"---\nmetadata:\n  version: \"{version}\"\n---\n\n"
        "<!-- issue-flow:config:start -->\n| Setting | Value here | Skill default |\n"
        "|---|---|---|\n| Tracker | github | github |\n<!-- issue-flow:config:end -->\n", encoding="utf-8")
    (repo / "references").mkdir(exist_ok=True)
    (repo / "references/runtime-notes.md").write_text(companion, encoding="utf-8")
def repositories(root: Path) -> tuple[Path, Path, str, str, str]:
    author, remote = root / "author", root / "remote.git"; upstream = remote.as_posix()
    author.mkdir(); git(author, "init", "-b", "main")
    git(author, "config", "user.email", "test@example.com"); git(author, "config", "user.name", "Installer Test")
    installer(author, "install.sh", upstream, legacy=True); installer(author, "install.ps1", upstream, legacy=True)
    (author / ".gitignore").write_text("operator.local.md\n", encoding="utf-8")
    write_skill(author, "1.10.0", "old companion\n")
    (author / "removed.md").write_text("remove me\n", encoding="utf-8")
    git(author, "add", "."); git(author, "commit", "-m", "old")
    legacy = git(author, "rev-parse", "HEAD")
    installer(author, "install.sh", upstream); installer(author, "install.ps1", upstream)
    git(author, "add", "."); git(author, "commit", "-m", "candidate installers")
    old = git(author, "rev-parse", "HEAD"); git(root, "clone", "--bare", author.as_posix(), upstream)
    git(author, "remote", "add", "origin", upstream)
    write_skill(author, "1.10.1", "new companion\n")
    (author / "added.md").write_text("new file\n", encoding="utf-8"); (author / "removed.md").unlink()
    git(author, "add", "-A"); git(author, "commit", "-m", "new")
    new = git(author, "rev-parse", "HEAD"); git(author, "push", "origin", "main")
    return author, remote, legacy, old, new
def clone_at(remote: Path, target: Path, revision: str) -> None:
    git(target.parent, "clone", remote.as_posix(), target.as_posix()); git(target, "reset", "--hard", revision)
    (target / "operator.local.md").write_bytes(b"local-secret-policy\r\n")
def command(kind: str, executable: str, script: Path, args: list[str], home: Path) -> subprocess.CompletedProcess:
    invocation = ([executable, "-NoProfile", *(["-ExecutionPolicy", "Bypass"] if kind == "Windows PowerShell" else []), "-File", str(script), *args] if kind != "POSIX"
                  else [executable, str(script), *args])
    env = os.environ.copy(); env["HOME"] = str(home); env["USERPROFILE"] = str(home)
    return run(invocation, env=env)
def runtime_copy(home: Path) -> Path:
    path = home / ".codex/skills/issue-flow"; path.mkdir(parents=True)
    return path
def junction(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True); run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check_result=True)
for kind, executable in shells():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); author, remote, legacy, old, new = repositories(root)
        installed, home = root / "installed", root / "home with spaces"
        clone_at(remote, installed, old)
        (installed / "Notes.Local").write_text("keep\n", encoding="utf-8")
        hook_marker = root / "hook-ran"; hook = installed / ".git/hooks/post-merge"
        hook.write_text(f"#!/bin/sh\nprintf ran > '{hook_marker.as_posix()}'\n"); hook.chmod(0o755)
        script = installed / ("install.ps1" if kind != "POSIX" else "install.sh")
        result = command(kind, executable, script, ["sync"], home)
        check(f"{kind} syncs the complete origin Git tree", result.returncode == 0 and git(installed, "rev-parse", "HEAD") == new and (installed / "references/runtime-notes.md").read_text() == "new companion\n" and (installed / "added.md").is_file() and not (installed / "removed.md").exists() and (installed / "operator.local.md").read_bytes() == b"local-secret-policy\r\n" and not hook_marker.exists())
        git(installed, "reset", "--hard", "HEAD@{1}")
        check(f"{kind} upgrade has a usable reflog rollback", git(installed, "rev-parse", "HEAD") == old and (installed / "removed.md").is_file() and not (installed / "added.md").exists())
        result = command(kind, executable, script, ["sync", "-DryRun"] if kind != "POSIX" else ["sync", "--dry-run"], home); check(f"{kind} dry-run leaves HEAD unchanged", result.returncode == 0 and git(installed, "rev-parse", "HEAD") == old)
        (installed / "references/runtime-notes.md").write_text("dirty\n", encoding="utf-8")
        result = command(kind, executable, script, ["sync"], home); check(f"{kind} refuses dirty tracked state", result.returncode != 0 and git(installed, "rev-parse", "HEAD") == old)
        git(installed, "reset", "--hard", old)
        source = root / "source"; clone_at(remote, source, new)
        args = ["sync", "-From", str(source)] if kind != "POSIX" else ["sync", "--from", str(source)]
        result = command(kind, executable, script, args, home); check(f"{kind} retires --from before mutation", result.returncode != 0 and git(installed, "rev-parse", "HEAD") == old)
        git(installed, "switch", "-c", "feature")
        result = command(kind, executable, script, ["sync"], home); check(f"{kind} refuses non-main branches", result.returncode != 0)
        git(installed, "switch", "main")
        copy = runtime_copy(home); result = command(kind, executable, script, ["sync"], home)
        check(f"{kind} refuses stale runtime copies", result.returncode != 0 and git(installed, "rev-parse", "HEAD") == old); shutil.rmtree(copy)
        junction(copy, installed); result = command(kind, executable, script, ["sync", "-DryRun"] if kind != "POSIX" else ["sync", "--dry-run"], home)
        check(f"{kind} accepts installer-created runtime links", result.returncode == 0 and git(installed, "rev-parse", "HEAD") == old); os.rmdir(copy)
        git(installed, "remote", "set-url", "origin", (root / "other.git").as_posix())
        result = command(kind, executable, script, ["sync"], home); check(f"{kind} binds upgrades to the canonical origin", result.returncode != 0 and git(installed, "rev-parse", "HEAD") == old)
        git(installed, "remote", "set-url", "origin", remote.as_posix())
        bootstrap_home = root / "bootstrap-home"; bootstrap_installed = bootstrap_home / ".agents/skills/issue-flow"
        bootstrap_installed.parent.mkdir(parents=True); clone_at(remote, bootstrap_installed, legacy)
        external = root / ("install.ps1" if kind != "POSIX" else "install.sh"); shutil.copy2(author / external.name, external)
        fresh_home = root / "fresh-home"
        result = command(kind, executable, external, ["sync", "-DryRun"] if kind != "POSIX" else ["sync", "--dry-run"], fresh_home); check(f"{kind} fresh bootstrap honors dry-run before clone", result.returncode == 0 and not (fresh_home / ".agents/skills/issue-flow").exists())
        fresh_from = root / "fresh-from"; result = command(kind, executable, external, args, fresh_from); check(f"{kind} fresh bootstrap retires --from before clone", result.returncode != 0 and not (fresh_from / ".agents/skills/issue-flow").exists())
        fresh_runtime = root / "fresh-runtime"; runtime_copy(fresh_runtime)
        result = command(kind, executable, external, ["sync"], fresh_runtime); check(f"{kind} fresh bootstrap checks runtime paths before clone", result.returncode != 0 and not (fresh_runtime / ".agents/skills/issue-flow").exists())
        (bootstrap_installed / "references/runtime-notes.md").write_text("dirty\n", encoding="utf-8")
        result = command(kind, executable, external, ["sync"], bootstrap_home); check(f"{kind} bootstrap refuses dirty legacy installs", result.returncode != 0 and git(bootstrap_installed, "rev-parse", "HEAD") == legacy)
        git(bootstrap_installed, "reset", "--hard", legacy)
        dry_args = ["sync", "-DryRun"] if kind != "POSIX" else ["sync", "--dry-run"]
        result = command(kind, executable, external, dry_args, bootstrap_home); check(f"{kind} bootstrap honors dry-run before mutation", result.returncode == 0 and git(bootstrap_installed, "rev-parse", "HEAD") == legacy)
        result = command(kind, executable, external, args, bootstrap_home); check(f"{kind} bootstrap retires --from before mutation", result.returncode != 0 and git(bootstrap_installed, "rev-parse", "HEAD") == legacy)
        copy = runtime_copy(bootstrap_home); result = command(kind, executable, external, ["sync"], bootstrap_home)
        check(f"{kind} bootstrap checks runtime copies before mutation", result.returncode != 0 and git(bootstrap_installed, "rev-parse", "HEAD") == legacy); shutil.rmtree(copy)
        result = command(kind, executable, external, ["sync"], bootstrap_home); check(f"{kind} safely upgrades a real legacy installation", result.returncode == 0 and git(bootstrap_installed, "rev-parse", "HEAD") == new)
        git(installed, "reset", "--hard", old)
        (author / "notes.local").mkdir(); (author / "notes.local/incoming.md").write_text("incoming\n")
        git(author, "add", "."); git(author, "commit", "-m", "case collision"); git(author, "push", "origin", "main")
        result = command(kind, executable, script, ["sync"], home); check(f"{kind} preserves case-folded local path collisions", result.returncode != 0 and (installed / "Notes.Local").read_text() == "keep\n" and git(installed, "rev-parse", "HEAD") == old)
        shutil.rmtree(author / "notes.local")
        (author / "Operator.Local.md").write_text("incoming\n"); git(author, "add", "-A"); git(author, "add", "-f", "Operator.Local.md")
        git(author, "commit", "-m", "bad policy"); git(author, "push", "origin", "main")
        result = command(kind, executable, script, ["sync"], home); check(f"{kind} rejects targets that track operator policy", result.returncode != 0 and (installed / "operator.local.md").read_bytes() == b"local-secret-policy\r\n" and git(installed, "rev-parse", "HEAD") == old)
        protected_home = root / "protected-home"; protected = protected_home / ".agents/skills/issue-flow"
        protected.parent.mkdir(parents=True); clone_at(remote, protected, legacy); result = command(kind, executable, external, ["sync"], protected_home)
        check(f"{kind} bootstrap preserves policy on invalid targets", result.returncode != 0 and git(protected, "rev-parse", "HEAD") == legacy and (protected / "operator.local.md").read_bytes() == b"local-secret-policy\r\n")
        (author / "Operator.Local.md").unlink(); (author / ".gitignore").write_text("operator.local.md\n!operator.local.md\n")
        git(author, "add", "-A"); git(author, "commit", "-m", "unignore policy"); git(author, "push", "origin", "main")
        result = command(kind, executable, script, ["sync"], home); check(f"{kind} rejects targets that expose operator policy", result.returncode != 0 and git(installed, "rev-parse", "HEAD") == old)
print(); print(f"{len(FAILURES)} failure(s)" + (f": {FAILURES}" if FAILURES else ""))
raise SystemExit(1 if FAILURES else 0)
