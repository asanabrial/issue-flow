#!/usr/bin/env python3
"""Cross-shell acceptance tests for immutable, commit-bound skill bundles."""

from __future__ import annotations

import os
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = "https://github.com/asanabrial/issue-flow.git"
LEGACY_BASE = "20cc138b7a82790048a3e913413dc6b674314a84"
LOCAL_POLICY = (
    "<!-- issue-flow:config:start -->\r\n"
    "| Setting | Value here | Skill default |\r\n"
    "|---|---|---|\r\n"
    "| Tracker | github | github |\r\n"
    "<!-- issue-flow:config:end -->\r\n"
).encode("utf-8")
FAILURES: list[str] = []


def check(name: str, condition: bool, result: subprocess.CompletedProcess | None = None) -> None:
    if not condition:
        FAILURES.append(name)
        if result:
            print(result.stdout.encode("ascii", "backslashreplace").decode("ascii"))
            print(result.stderr.encode("ascii", "backslashreplace").decode("ascii"))
    print(f"{'OK  ' if condition else 'FAIL'} {name}")


def run(
    command: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check_result: bool = False,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check_result and result.returncode:
        raise RuntimeError(f"{command} failed: {result.stderr}\n{result.stdout}")
    return result


def git(cwd: Path, *args: str) -> str:
    return run(["git", *args], cwd, check_result=True).stdout.strip()


def shells() -> list[tuple[str, str]]:
    pwsh = shutil.which("pwsh")
    posix = shutil.which("sh")
    if not posix and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/sh.exe"
        posix = str(candidate) if candidate.is_file() else None
    if not pwsh or not posix:
        raise RuntimeError(f"pwsh and POSIX sh are required: pwsh={pwsh!r}, sh={posix!r}")
    windows = [("Windows PowerShell", shutil.which("powershell"))] if shutil.which("powershell") else []
    return [("PowerShell", pwsh), *windows, ("POSIX", posix)]


def replace_upstream(text: str, repository: str) -> str:
    if UPSTREAM not in text:
        raise RuntimeError("installer source no longer contains the canonical repository URL")
    return text.replace(UPSTREAM, repository)


def legacy_file(name: str, repository: str) -> str:
    if run(["git", "cat-file", "-e", f"{LEGACY_BASE}:{name}"], ROOT).returncode:
        git(ROOT, "fetch", "-q", "origin", LEGACY_BASE)
    return replace_upstream(git(ROOT, "show", f"{LEGACY_BASE}:{name}"), repository)


def copy_candidate(author: Path, repository: str) -> None:
    for name in ("install.sh", "install.ps1", "scripts/install_bundle.py"):
        source = ROOT / name
        target = author / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(replace_upstream(source.read_text(encoding="utf-8"), repository), encoding="utf-8")


def write_contract(repository: Path, version: str, companion: str) -> None:
    (repository / "SKILL.md").write_text(
        "---\nname: issue-flow\nmetadata:\n"
        f"  version: \"{version}\"\n---\n\n"
        "Load [runtime notes](references/runtime-notes.md).\n\n"
        "<!-- issue-flow:config:start -->\n"
        "| Setting | Value here | Skill default |\n"
        "|---|---|---|\n"
        "| Tracker | github | github |\n"
        "<!-- issue-flow:config:end -->\n",
        encoding="utf-8",
    )
    references = repository / "references"
    references.mkdir(exist_ok=True)
    (references / "runtime-notes.md").write_text(companion, encoding="utf-8")


def repositories(root: Path) -> tuple[Path, Path, str, str, str]:
    author = root / "author"
    remote = root / "remote.git"
    repository = remote.as_uri()
    author.mkdir()
    git(author, "init", "-b", "main")
    git(author, "config", "user.email", "test@example.com")
    git(author, "config", "user.name", "Installer Test")

    (author / "install.sh").write_text(legacy_file("install.sh", repository), encoding="utf-8")
    (author / "install.ps1").write_text(legacy_file("install.ps1", repository), encoding="utf-8")
    (author / ".gitignore").write_text("operator.local.md\n.codegraph/\n", encoding="utf-8")
    (author / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8")
    write_contract(author, "1.11.0", "legacy companion\n")
    (author / "removed.md").write_text("remove me\n", encoding="utf-8")
    git(author, "add", "-f", ".")
    git(author, "commit", "-m", "legacy")
    legacy = git(author, "rev-parse", "HEAD")

    copy_candidate(author, repository)
    write_contract(author, "1.12.0", "candidate companion\n")
    git(author, "add", "-f", ".")
    git(author, "commit", "-m", "candidate installers")
    candidate = git(author, "rev-parse", "HEAD")

    write_contract(author, "1.12.1", "new companion\n")
    (author / "added.md").write_text("new file\n", encoding="utf-8")
    (author / "removed.md").unlink()
    git(author, "add", "-A")
    git(author, "commit", "-m", "new contract")
    new = git(author, "rev-parse", "HEAD")

    git(root, "clone", "--bare", author.as_posix(), remote.as_posix())
    git(remote, "update-ref", "refs/heads/main", candidate)
    git(author, "remote", "add", "origin", repository)
    return author, remote, legacy, candidate, new


def publish(author: Path, revision: str) -> None:
    git(author, "push", "--force", "origin", f"{revision}:refs/heads/main")


def clone_legacy(remote: Path, target: Path, revision: str) -> None:
    target.parent.mkdir(parents=True)
    git(target.parent, "clone", remote.as_uri(), target.as_posix())
    git(target, "config", "core.autocrlf", "false")
    git(target, "reset", "--hard", revision)
    (target / "operator.local.md").write_bytes(LOCAL_POLICY)
    cache = target / ".codegraph"
    cache.mkdir()
    (cache / "index").write_bytes(b"local-index\x00")


def external_installer(root: Path, kind: str, repository: str) -> Path:
    name = "install.sh" if kind == "POSIX" else "install.ps1"
    path = root / name
    path.write_text(replace_upstream((ROOT / name).read_text(encoding="utf-8"), repository), encoding="utf-8")
    return path


def command(
    kind: str,
    executable: str,
    script: Path,
    args: list[str],
    home: Path,
) -> subprocess.CompletedProcess:
    invocation = (
        [
            executable,
            "-NoProfile",
            *(["-ExecutionPolicy", "Bypass"] if kind == "Windows PowerShell" else []),
            "-File",
            str(script),
            *args,
        ]
        if kind != "POSIX"
        else [executable, str(script), *args]
    )
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    return run(invocation, env=env)


def current_revision(home: Path) -> str | None:
    current = home / ".agents/skills/issue-flow"
    if not current.exists():
        return None
    receipt = current / ".issue-flow-bundle.json"
    if not receipt.is_file():
        return None
    import json

    return json.loads(receipt.read_text(encoding="utf-8"))["commit"]


def shell_args(kind: str, command_name: str, *extra: str) -> list[str]:
    if kind == "POSIX":
        return [command_name, *extra]
    translated = {"--dry-run": "-DryRun", "--from": "-From", "--set": "-Set"}
    return [command_name, *(translated.get(item, item) for item in extra)]


for kind, executable in shells():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        author, remote, legacy, candidate, new = repositories(root)
        home = root / "home with spaces"
        installed = home / ".agents/skills/issue-flow"
        external = external_installer(root, kind, remote.as_uri())

        fresh_home = root / "fresh dry-run home"
        result = command(kind, executable, external, shell_args(kind, "sync", "--dry-run"), fresh_home)
        check(
            f"{kind} fresh dry-run returns before bootstrap mutation",
            result.returncode == 0 and not (fresh_home / ".agents").exists(),
            result,
        )

        clone_legacy(remote, installed, legacy)

        result = command(kind, executable, external, shell_args(kind, "sync"), home)
        check(
            f"{kind} migrates a legacy clone only after preparing a complete bundle",
            result.returncode == 0
            and current_revision(home) == candidate
            and (installed / "references/runtime-notes.md").read_text(encoding="utf-8") == "candidate companion\n"
            and (installed / "operator.local.md").read_bytes() == LOCAL_POLICY
            and (installed / ".codegraph/index").read_bytes() == b"local-index\x00",
            result,
        )

        result = command(
            kind,
            executable,
            installed / ("install.sh" if kind == "POSIX" else "install.ps1"),
            shell_args(kind, "config", "--set", "Tracker=linear"),
            home,
        )
        configured_policy = (installed / "operator.local.md").read_bytes()
        check(
            f"{kind} updates stable policy through the active hard link",
            result.returncode == 0
            and b"| Tracker | linear | github |" in configured_policy
            and configured_policy.endswith(b"\r\n"),
            result,
        )

        publish(author, new)
        active_script = installed / ("install.sh" if kind == "POSIX" else "install.ps1")
        result = command(kind, executable, active_script, shell_args(kind, "sync"), home)
        check(
            f"{kind} atomically activates the complete next Git tree",
            result.returncode == 0
            and current_revision(home) == new
            and (installed / "references/runtime-notes.md").read_text(encoding="utf-8") == "new companion\n"
            and (installed / "added.md").is_file()
            and not (installed / "removed.md").exists()
            and (installed / "operator.local.md").read_bytes() == configured_policy,
            result,
        )

        (installed / "references/runtime-notes.md").write_bytes(b"drifted\n")
        result = command(kind, executable, active_script, shell_args(kind, "sync"), home)
        check(
            f"{kind} refuses stale active companion bytes",
            result.returncode != 0 and current_revision(home) == new,
            result,
        )
        (installed / "references/runtime-notes.md").write_bytes(b"new companion\n")

        receipt_path = installed / ".issue-flow-bundle.json"
        original_receipt = receipt_path.read_bytes()
        (installed / "references/runtime-notes.md").write_bytes(b"forged drift\n")
        forged = json.loads(original_receipt)
        forged_file = forged["files"]["references/runtime-notes.md"]
        forged_file["size"] = len(b"forged drift\n")
        forged_file["sha256"] = hashlib.sha256(b"forged drift\n").hexdigest()
        receipt_path.write_bytes((json.dumps(forged) + "\n").encode("utf-8"))
        result = command(kind, executable, active_script, shell_args(kind, "sync"), home)
        check(
            f"{kind} cannot authorize drift with a forged local receipt",
            result.returncode != 0 and current_revision(home) == new,
            result,
        )
        receipt_path.write_bytes(original_receipt)
        (installed / "references/runtime-notes.md").write_bytes(b"new companion\n")

        result = command(kind, executable, active_script, shell_args(kind, "rollback"), home)
        check(
            f"{kind} rolls back by switching to the retained previous bundle",
            result.returncode == 0
            and current_revision(home) == candidate
            and (installed / "references/runtime-notes.md").read_text(encoding="utf-8") == "candidate companion\n"
            and (installed / "operator.local.md").read_bytes() == configured_policy,
            result,
        )

        supplied = root / "newer-SKILL.md"
        supplied.write_text("partial\n", encoding="utf-8")
        result = command(kind, executable, active_script, shell_args(kind, "sync", "--from", str(supplied)), home)
        check(
            f"{kind} retires single-file sync before mutation",
            result.returncode != 0 and current_revision(home) == candidate,
            result,
        )

        result = command(kind, executable, active_script, shell_args(kind, "sync", "--dry-run"), home)
        check(
            f"{kind} dry-run validates without activating the target",
            result.returncode == 0 and current_revision(home) == candidate,
            result,
        )
        result = command(kind, executable, active_script, shell_args(kind, "sync"), home)
        check(f"{kind} can reactivate the published target", result.returncode == 0 and current_revision(home) == new, result)

        stale_lock = home / ".agents/skills/.issue-flow/sync.lock"
        state_root = stale_lock.parent
        (state_root / "transaction.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "phase": "prepared",
                    "previous": candidate,
                    "target": new,
                }
            ),
            encoding="utf-8",
        )
        (state_root / "current.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "repository": remote.as_uri(),
                    "current": candidate,
                    "previous": legacy,
                    "activated_at": 0,
                }
            ),
            encoding="utf-8",
        )
        stale_lock.mkdir()
        (stale_lock / "owner.json").write_text(
            json.dumps({"schema": 1, "pid": 99999999, "started": 0}),
            encoding="utf-8",
        )
        result = command(kind, executable, active_script, shell_args(kind, "recover"), home)
        check(
            f"{kind} recovers an abandoned lock and post-switch receipt write",
            result.returncode == 0
            and not stale_lock.exists()
            and not (state_root / "transaction.json").exists()
            and current_revision(home) == new
            and json.loads((state_root / "current.json").read_text(encoding="utf-8"))["previous"] == candidate,
            result,
        )

        git(author, "reset", "--hard", new)
        (author / "references/runtime-notes.md").unlink()
        git(author, "add", "-A")
        git(author, "commit", "-m", "missing required companion")
        missing = git(author, "rev-parse", "HEAD")
        publish(author, missing)
        result = command(kind, executable, active_script, shell_args(kind, "sync"), home)
        check(
            f"{kind} rejects a target with a missing contract link",
            result.returncode != 0 and current_revision(home) == new,
            result,
        )

        git(author, "reset", "--hard", new)
        (author / "Operator.Local.md").write_text("published secret\n", encoding="utf-8")
        git(author, "add", "-f", "Operator.Local.md")
        git(author, "commit", "-m", "track local policy")
        unsafe = git(author, "rev-parse", "HEAD")
        publish(author, unsafe)
        result = command(kind, executable, active_script, shell_args(kind, "sync"), home)
        check(
            f"{kind} rejects a target that versions operator policy",
            result.returncode != 0
            and current_revision(home) == new
            and (installed / "operator.local.md").read_bytes() == configured_policy,
            result,
        )


print()
print(f"{len(FAILURES)} failure(s)" + (f": {FAILURES}" if FAILURES else ""))
raise SystemExit(1 if FAILURES else 0)
