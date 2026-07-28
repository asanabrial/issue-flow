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

from install_bundle import InstallerLock, InstallError, Paths, validate_tree_path


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

for unsafe_path in (
    r"..\outside",
    r"C:\outside",
    "payload:stream",
    "trailing. ",
    "CON/file.md",
    "decomposed-e\u0301.md",
):
    try:
        validate_tree_path(unsafe_path)
    except InstallError:
        continue
    FAILURES.append(f"unsafe tree path accepted: {unsafe_path!r}")


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
        "Load [runtime notes][runtime].\n\n"
        "[runtime]: references/runtime-notes.md\n\n"
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


def piped_posix(executable: str, script: Path, args: list[str], home: Path, cwd: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    return subprocess.run(
        [executable, "-s", "--", *args],
        cwd=cwd,
        env=env,
        input=script.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def invoked_powershell(kind: str, executable: str, script: Path, home: Path) -> subprocess.CompletedProcess:
    escaped = str(script).replace("'", "''")
    invocation = [
        executable,
        "-NoProfile",
        *(["-ExecutionPolicy", "Bypass"] if kind == "Windows PowerShell" else []),
        "-Command",
        f"try {{ Invoke-Expression (Get-Content -LiteralPath '{escaped}' -Raw) }} catch {{ 'caught' }}; 'after-iex'",
    ]
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

        if kind == "POSIX":
            hostile = root / "hostile cwd"
            (hostile / "scripts").mkdir(parents=True)
            sentinel = hostile / "sentinel"
            (hostile / "scripts/install_bundle.py").write_text(
                f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n",
                encoding="utf-8",
            )
            piped_home = root / "piped home"
            result = piped_posix(executable, external, ["sync", "--dry-run"], piped_home, hostile)
            check(
                "POSIX piped bootstrap ignores a hostile cwd helper",
                result.returncode == 0 and not sentinel.exists() and not (piped_home / ".agents").exists(),
                result,
            )

        fresh_home = root / "fresh dry-run home"
        result = command(kind, executable, external, shell_args(kind, "sync", "--dry-run"), fresh_home)
        check(
            f"{kind} fresh dry-run returns before bootstrap mutation",
            result.returncode == 0 and not (fresh_home / ".agents").exists(),
            result,
        )

        fresh_install_home = root / "fresh install home"
        (fresh_install_home / ".claude").mkdir(parents=True)
        (fresh_install_home / ".codex").mkdir()
        result = command(kind, executable, external, shell_args(kind, "install"), fresh_install_home)
        fresh_canonical = fresh_install_home / ".agents/skills/issue-flow"
        check(
            f"{kind} fresh install activates one complete tree and runtime links",
            result.returncode == 0
            and current_revision(fresh_install_home) == candidate
            and all(
                (fresh_install_home / runtime / "skills/issue-flow").resolve(strict=True)
                == fresh_canonical.resolve(strict=True)
                for runtime in (".claude", ".codex")
            ),
            result,
        )
        fresh_script = fresh_canonical / ("install.sh" if kind == "POSIX" else "install.ps1")
        result = command(kind, executable, fresh_script, shell_args(kind, "status"), fresh_install_home)
        check(
            f"{kind} status reports immutable tree and healthy runtime links",
            result.returncode == 0 and "immutable bundle" in result.stdout and "healthy" in result.stdout,
            result,
        )
        result = command(kind, executable, fresh_script, shell_args(kind, "uninstall"), fresh_install_home)
        check(
            f"{kind} uninstall removes only runtime links",
            result.returncode == 0
            and fresh_canonical.exists()
            and not (fresh_install_home / ".claude/skills/issue-flow").exists()
            and not (fresh_install_home / ".codex/skills/issue-flow").exists(),
            result,
        )

        blocked_home = root / "stale runtime home"
        (blocked_home / ".claude/skills/issue-flow").mkdir(parents=True)
        result = command(kind, executable, external, shell_args(kind, "install"), blocked_home)
        check(
            f"{kind} refuses an independent runtime copy before canonical installation",
            result.returncode != 0 and not (blocked_home / ".agents").exists(),
            result,
        )
        if kind != "POSIX":
            result = invoked_powershell(kind, executable, external, blocked_home)
            check(
                f"{kind} IEX bootstrap returns control to the caller",
                result.returncode == 0 and "after-iex" in result.stdout,
                result,
            )

        clone_legacy(remote, installed, legacy)
        (home / ".claude").mkdir()
        (home / ".codex").mkdir()

        result = command(kind, executable, external, shell_args(kind, "install", "--dry-run"), home)
        check(
            f"{kind} legacy install dry-run leaves clone and runtime paths unchanged",
            result.returncode == 0
            and git(installed, "rev-parse", "HEAD") == legacy
            and current_revision(home) is None
            and not (home / ".claude/skills/issue-flow").exists()
            and not (home / ".codex/skills/issue-flow").exists(),
            result,
        )

        result = command(kind, executable, external, shell_args(kind, "install"), home)
        check(
            f"{kind} migrates a legacy clone only after preparing a complete bundle",
            result.returncode == 0
            and current_revision(home) == candidate
            and (installed / "references/runtime-notes.md").read_text(encoding="utf-8") == "candidate companion\n"
            and (installed / "operator.local.md").read_bytes() == LOCAL_POLICY
            and (installed / ".codegraph/index").read_bytes() == b"local-index\x00",
            result,
        )

        policy_inode = (home / ".agents/skills/.issue-flow/operator.local.md").stat().st_ino
        result = command(
            kind,
            executable,
            installed / ("install.sh" if kind == "POSIX" else "install.ps1"),
            shell_args(kind, "config", "--set", "Tracker=linear"),
            home,
        )
        configured_policy = (installed / "operator.local.md").read_bytes()
        policy_files = list((home / ".agents/skills/.issue-flow/policies").iterdir())
        check(
            f"{kind} updates stable policy through the active hard link",
            result.returncode == 0
            and b"| Tracker | linear | github |" in configured_policy
            and configured_policy.endswith(b"\r\n")
            and len(policy_files) == 1
            and policy_files[0].read_bytes() == configured_policy
            and (home / ".agents/skills/.issue-flow/operator.local.md").stat().st_ino != policy_inode
            and os.path.samefile(
                installed / "operator.local.md",
                home / ".agents/skills/.issue-flow/operator.local.md",
            ),
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
        check(
            f"{kind} runtime links follow the stable canonical pointer after sync",
            all(
                (home / runtime / "skills/issue-flow").resolve(strict=True) == installed.resolve(strict=True)
                for runtime in (".claude", ".codex")
            ),
        )

        state_root = home / ".agents/skills/.issue-flow"
        state_before_dry_rollback = (state_root / "current.json").read_bytes()
        result = command(kind, executable, active_script, shell_args(kind, "rollback", "--dry-run"), home)
        check(
            f"{kind} rollback dry-run is strictly read-only",
            result.returncode == 0
            and current_revision(home) == new
            and (state_root / "current.json").read_bytes() == state_before_dry_rollback
            and not (state_root / "transaction.json").exists(),
            result,
        )

        retained = state_root / "bundles" / candidate
        retained_file = retained / "references/runtime-notes.md"
        retained_receipt = retained / ".issue-flow-bundle.json"
        retained_file_bytes = retained_file.read_bytes()
        retained_receipt_bytes = retained_receipt.read_bytes()
        retained_file.write_bytes(b"forged retained bundle\n")
        forged_retained = json.loads(retained_receipt_bytes)
        retained_metadata = forged_retained["files"]["references/runtime-notes.md"]
        retained_metadata["size"] = len(b"forged retained bundle\n")
        retained_metadata["sha256"] = hashlib.sha256(b"forged retained bundle\n").hexdigest()
        retained_receipt.write_bytes((json.dumps(forged_retained) + "\n").encode("utf-8"))
        result = command(kind, executable, active_script, shell_args(kind, "rollback"), home)
        check(
            f"{kind} rollback rejects a forged retained bundle",
            result.returncode != 0 and current_revision(home) == new,
            result,
        )
        retained_file.write_bytes(retained_file_bytes)
        retained_receipt.write_bytes(retained_receipt_bytes)

        external_hardlink = root / "tracked-hardlink"
        os.link(installed / "references/runtime-notes.md", external_hardlink)
        result = command(kind, executable, active_script, shell_args(kind, "sync"), home)
        check(
            f"{kind} refuses tracked bytes hard-linked outside the bundle",
            result.returncode != 0 and current_revision(home) == new,
            result,
        )
        external_hardlink.unlink()

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
        check(
            f"{kind} runtime links follow canonical rollback",
            all(
                (home / runtime / "skills/issue-flow").resolve(strict=True) == installed.resolve(strict=True)
                for runtime in (".claude", ".codex")
            ),
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
        check(
            f"{kind} can reactivate the published target and prunes older bundles",
            result.returncode == 0
            and current_revision(home) == new
            and len([path for path in (state_root / "bundles").iterdir() if path.is_dir()]) == 2,
            result,
        )

        lock_path = home / ".agents/skills/.issue-flow/sync.lock"
        state_root = lock_path.parent
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
        with InstallerLock(Paths.for_home(home)):
            locked = command(kind, executable, active_script, shell_args(kind, "recover"), home)
        check(
            f"{kind} recovery cannot steal a live operating-system lock",
            locked.returncode != 0,
            locked,
        )
        result = command(kind, executable, active_script, shell_args(kind, "recover"), home)
        check(
            f"{kind} recovers a post-switch receipt write after lock release",
            result.returncode == 0
            and not (state_root / "transaction.json").exists()
            and current_revision(home) == new
            and json.loads((state_root / "current.json").read_text(encoding="utf-8"))["previous"] == candidate,
            result,
        )

        outside_backup = root / "outside-backup"
        outside_backup.mkdir()
        (state_root / "transaction.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "phase": "moved",
                    "previous": candidate,
                    "target": new,
                    "backup": str(outside_backup),
                }
            ),
            encoding="utf-8",
        )
        result = command(kind, executable, active_script, shell_args(kind, "recover"), home)
        check(
            f"{kind} recovery rejects a backup path outside installer state",
            result.returncode != 0 and outside_backup.is_dir() and current_revision(home) == new,
            result,
        )
        (state_root / "transaction.json").unlink()

        repository_config = state_root / "repository.git/config"
        safe_config = repository_config.read_bytes()
        with repository_config.open("ab") as handle:
            handle.write(b"\n[url \"ext::malicious\"]\n\tinsteadOf = file://\n")
        result = command(kind, executable, active_script, shell_args(kind, "sync"), home)
        check(
            f"{kind} rejects persisted Git authority configuration",
            result.returncode != 0 and current_revision(home) == new,
            result,
        )
        repository_config.write_bytes(safe_config)

        object_store = state_root / "repository.git"
        git(state_root, f"--git-dir={object_store}", "replace", new, candidate)
        result = command(kind, executable, active_script, shell_args(kind, "sync"), home)
        check(
            f"{kind} ignores local Git replacement objects",
            result.returncode == 0 and current_revision(home) == new,
            result,
        )
        git(state_root, f"--git-dir={object_store}", "replace", "-d", new)

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
