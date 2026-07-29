#!/usr/bin/env python3
"""Cross-shell acceptance tests for immutable, commit-bound skill bundles."""

from __future__ import annotations

import os
import hashlib
import json
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import install_bundle as installer
from install_bundle import (
    InstallerLock,
    InstallError,
    Paths,
    activate,
    copy_object_database,
    create_directory_pointer,
    fetch_target,
    initialize_state,
    materialize_bundle,
    policy_generation,
    pointer_targets,
    remove_directory_pointer,
    replace_hardlink,
    replace_path,
    tree_entries,
    validate_tree_path,
    write_json,
)


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


with tempfile.TemporaryDirectory() as object_test_directory:
    object_test_root = Path(object_test_directory)
    object_source = object_test_root / "source"
    object_destination = object_test_root / "destination"
    (object_source / "aa").mkdir(parents=True)
    (object_destination / "aa").mkdir(parents=True)
    (object_source / "aa/object").write_bytes(b"complete object")
    (object_destination / "aa/object").write_bytes(b"partial")
    copy_object_database(object_source, object_destination)
    check(
        "shared installer heals a torn final Git object atomically",
        (object_destination / "aa/object").read_bytes() == b"complete object",
    )

with tempfile.TemporaryDirectory() as home_alias_directory:
    alias_root = Path(home_alias_directory)
    physical_home = alias_root / "physical-home"
    physical_canonical = physical_home / ".agents/skills/issue-flow"
    physical_canonical.mkdir(parents=True)
    alias_home = alias_root / "alias-home"
    create_directory_pointer(alias_home, physical_home)
    runtime_pointer = alias_root / "runtime-pointer"
    create_directory_pointer(runtime_pointer, physical_canonical)
    check(
        "runtime ownership accepts a physical target through a symlinked home alias",
        pointer_targets(runtime_pointer, alias_home / ".agents/skills/issue-flow"),
    )
    remove_directory_pointer(runtime_pointer)
    remove_directory_pointer(alias_home)


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
    windows_powershell = shutil.which("powershell")
    if os.name == "nt" and not windows_powershell:
        raise RuntimeError("Windows PowerShell 5.1 is required for the Windows installer lane")
    windows = [("Windows PowerShell", windows_powershell)] if windows_powershell else []
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
        "Load [runtime].\n\n"
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
    hook = target / "custom-hook.sh"
    hook.write_bytes(b"#!/bin/sh\nexit 0\n")
    os.chmod(hook, 0o755)
    (target / (".cache." + "b" * 32 + ".tmp")).write_bytes(b"legitimate local temporary name\n")


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
    extra_env: dict[str, str] | None = None,
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
    env.update(extra_env or {})
    return run(invocation, env=env)


def piped_posix(
    executable: str,
    script: Path,
    args: list[str],
    home: Path,
    cwd: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env.update(extra_env or {})
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


def outer_script_powershell(
    kind: str,
    executable: str,
    script: Path,
    home: Path,
    root: Path,
) -> tuple[subprocess.CompletedProcess, Path]:
    outer = root / "outer caller"
    (outer / "scripts").mkdir(parents=True)
    sentinel = outer / "sentinel"
    (outer / "scripts/install_bundle.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    escaped = str(script).replace("'", "''")
    runner = outer / "install.ps1"
    runner.write_text(
        f"try {{ Invoke-Expression (Get-Content -LiteralPath '{escaped}' -Raw) }} catch {{ 'caught' }}\n'after-outer-iex'\n",
        encoding="utf-8",
    )
    invocation = [
        executable,
        "-NoProfile",
        *(["-ExecutionPolicy", "Bypass"] if kind == "Windows PowerShell" else []),
        "-File",
        str(runner),
    ]
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    return run(invocation, env=env), sentinel


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
        installer.REPOSITORY_URL = remote.as_uri()
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
            piped_install_home = root / "piped install home"
            piped_install_home.mkdir()
            hostile_bootstrap_config = root / "hostile-bootstrap-config"
            hostile_bootstrap_config.write_text(
                '[url "ext::false"]\n\tinsteadOf = file://\n[protocol "ext"]\n\tallow = always\n',
                encoding="utf-8",
            )
            result = piped_posix(
                executable,
                external,
                ["install"],
                piped_install_home,
                hostile,
                {
                    "GIT_ALLOW_PROTOCOL": "ext",
                    "GIT_SSL_NO_VERIFY": "1",
                    "GIT_CONFIG_GLOBAL": str(hostile_bootstrap_config),
                },
            )
            check(
                "POSIX piped bootstrap acquires and activates the isolated helper",
                result.returncode == 0
                and not sentinel.exists()
                and current_revision(piped_install_home) == candidate,
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
        hostile_python = root / "hostile python"
        hostile_python.mkdir()
        python_sentinel = root / "python-sentinel"
        (hostile_python / "hashlib.py").write_text(
            f"from pathlib import Path\nPath({str(python_sentinel)!r}).write_text('executed')\n",
            encoding="utf-8",
        )
        hostile_environment = {"PYTHONPATH": str(hostile_python)}
        wrong_profile = root / "wrong profile"
        if kind == "POSIX":
            wrong_profile.mkdir()
            hostile_environment["USERPROFILE"] = str(wrong_profile)
        result = command(
            kind,
            executable,
            external,
            shell_args(kind, "install"),
            fresh_install_home,
            hostile_environment,
        )
        check(
            f"{kind} isolated Python ignores hostile PYTHONPATH",
            result.returncode == 0 and not python_sentinel.exists(),
            result,
        )
        fresh_canonical = fresh_install_home / ".agents/skills/issue-flow"
        check(
            f"{kind} fresh install activates one complete tree and runtime links",
            result.returncode == 0
            and current_revision(fresh_install_home) == candidate
            and (kind != "POSIX" or not (wrong_profile / ".agents").exists())
            and all(
                (fresh_install_home / runtime / "skills/issue-flow").resolve(strict=True)
                == fresh_canonical.resolve(strict=True)
                for runtime in (".claude", ".codex")
            ),
            result,
        )
        fresh_script = fresh_canonical / ("install.sh" if kind == "POSIX" else "install.ps1")
        fresh_state = fresh_install_home / ".agents/skills/.issue-flow"
        (fresh_state / "current.json").unlink()
        (fresh_state / "transaction.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "phase": "prepared",
                    "previous": None,
                    "target": candidate,
                    "prior_previous": None,
                }
            ),
            encoding="utf-8",
        )
        result = command(kind, executable, fresh_script, shell_args(kind, "install"), fresh_install_home)
        check(
            f"{kind} install retry recovers first activation after pointer publication",
            result.returncode == 0
            and current_revision(fresh_install_home) == candidate
            and not (fresh_state / "transaction.json").exists(),
            result,
        )
        hooks = fresh_state / "empty-hooks"
        hooks.mkdir(exist_ok=True)
        hook_sentinel = root / "hook-sentinel"
        hook = hooks / "reference-transaction"
        hook.write_text(
            f"#!/bin/sh\nprintf attacked > '{hook_sentinel.as_posix()}'\n",
            encoding="utf-8",
        )
        os.chmod(hook, 0o755)
        result = command(kind, executable, fresh_script, shell_args(kind, "recover"), fresh_install_home)
        check(
            f"{kind} ignores executable state-local Git hooks",
            result.returncode == 0 and not hook_sentinel.exists(),
            result,
        )
        abandoned_runtime = fresh_install_home / ".claude/skills" / (".issue-flow.runtime-" + "a" * 32)
        create_directory_pointer(abandoned_runtime, fresh_canonical)
        result = command(kind, executable, fresh_script, shell_args(kind, "install"), fresh_install_home)
        check(
            f"{kind} install retry cleans abandoned runtime pointer moves",
            result.returncode == 0 and not os.path.lexists(abandoned_runtime),
            result,
        )
        repository_config = fresh_state / "repository.git/config"
        repository_config_bytes = repository_config.read_bytes()
        check(
            f"{kind} repository config durably fsyncs activation references",
            b"fsync = reference" in repository_config_bytes and b"fsyncmethod = fsync" in repository_config_bytes,
        )
        repository_config.write_bytes(
            repository_config_bytes.replace(b"[core]\n", b"[core]\n\tprecomposeunicode = true\n")
        )
        result = command(kind, executable, fresh_script, shell_args(kind, "status"), fresh_install_home)
        check(
            f"{kind} accepts Git's macOS precomposeunicode core setting",
            result.returncode == 0,
            result,
        )
        repository_config.write_bytes(repository_config_bytes)
        (fresh_canonical / "operator.local.md").write_bytes(b"forged local policy\n")
        result = command(kind, executable, fresh_script, shell_args(kind, "status"), fresh_install_home)
        check(
            f"{kind} rejects bundle-local policy without stable state",
            result.returncode != 0,
            result,
        )
        (fresh_canonical / "operator.local.md").unlink()
        result = command(kind, executable, fresh_script, shell_args(kind, "status"), fresh_install_home)
        check(
            f"{kind} status reports immutable tree and healthy runtime links",
            result.returncode == 0 and "immutable bundle" in result.stdout and "healthy" in result.stdout,
            result,
        )
        local_helper = fresh_canonical / "scripts/install_bundle.py"
        local_helper_bytes = local_helper.read_bytes()
        helper_sentinel = root / "helper-sentinel"
        local_helper.write_text(
            f"from pathlib import Path\nPath({str(helper_sentinel)!r}).write_text('executed')\n",
            encoding="utf-8",
        )
        result = command(kind, executable, fresh_script, shell_args(kind, "status"), fresh_install_home)
        check(
            f"{kind} wrapper never executes a helper that differs from its Git blob",
            result.returncode != 0 and not helper_sentinel.exists(),
            result,
        )
        local_helper.write_bytes(local_helper_bytes)
        fresh_receipt = fresh_canonical / ".issue-flow-bundle.json"
        fresh_receipt_bytes = fresh_receipt.read_bytes()
        git(remote, "update-ref", "refs/heads/helper-substitution", new)
        git(
            fresh_state,
            f"--git-dir={fresh_state / 'repository.git'}",
            "fetch",
            remote.as_uri(),
            "refs/heads/helper-substitution:refs/issue-flow/test-helper-substitution",
        )
        substituted_helper = subprocess.check_output(
            [
                "git",
                f"--git-dir={fresh_state / 'repository.git'}",
                "cat-file",
                "blob",
                f"{new}:scripts/install_bundle.py",
            ]
        )
        substituted_receipt = json.loads(fresh_receipt_bytes)
        substituted_receipt["commit"] = new
        local_helper.write_bytes(substituted_helper)
        fresh_receipt.write_text(json.dumps(substituted_receipt), encoding="utf-8")
        result = command(kind, executable, fresh_script, shell_args(kind, "status"), fresh_install_home)
        check(
            f"{kind} wrapper binds helper verification to the active bundle identity",
            result.returncode != 0,
            result,
        )
        local_helper.write_bytes(local_helper_bytes)
        fresh_receipt.write_bytes(fresh_receipt_bytes)
        git(remote, "update-ref", "-d", "refs/heads/helper-substitution")
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
            result, outer_sentinel = outer_script_powershell(kind, executable, external, blocked_home, root)
            check(
                f"{kind} IEX ignores an outer script's adjacent helper",
                result.returncode == 0 and "after-outer-iex" in result.stdout and not outer_sentinel.exists(),
                result,
            )

        hostile_legacy_home = root / "hostile legacy home"
        hostile_legacy = hostile_legacy_home / ".agents/skills/issue-flow"
        clone_legacy(remote, hostile_legacy, legacy)
        diff_sentinel = root / "diff-sentinel"
        diff_driver = root / "diff-driver.sh"
        diff_driver.write_text(
            f"#!/bin/sh\nprintf executed > '{diff_sentinel.as_posix()}'\nexit 0\n",
            encoding="utf-8",
        )
        os.chmod(diff_driver, 0o755)
        git(hostile_legacy, "config", "diff.external", diff_driver.as_posix())
        git(hostile_legacy, "config", "diff.trustExitCode", "true")
        with (hostile_legacy / "SKILL.md").open("a", encoding="utf-8") as handle:
            handle.write("dirty\n")
        result = command(kind, executable, external, shell_args(kind, "install"), hostile_legacy_home)
        check(
            f"{kind} legacy migration disables external diff execution",
            result.returncode != 0 and not diff_sentinel.exists() and git(hostile_legacy, "status", "--short"),
            result,
        )

        legacy_recovery_home = root / "legacy recovery home"
        legacy_recovery = legacy_recovery_home / ".agents/skills/issue-flow"
        clone_legacy(remote, legacy_recovery, legacy)
        for runtime_root in (".claude", ".codex"):
            runtime_parent = legacy_recovery_home / runtime_root / "skills"
            runtime_parent.mkdir(parents=True)
            create_directory_pointer(runtime_parent / "issue-flow", legacy_recovery.resolve())
        recovery_paths = Paths.for_home(legacy_recovery_home)
        initialize_state(recovery_paths)
        recovery_target = fetch_target(recovery_paths)
        tree_entries(recovery_paths, recovery_target)
        recovery_bundle = materialize_bundle(recovery_paths, recovery_target)
        recovery_backup = recovery_paths.legacy / f"{legacy}-recovery-test"
        write_json(
            recovery_paths.transaction,
            {
                "schema": 1,
                "phase": "moved",
                "backup": str(recovery_backup),
                "previous": legacy,
                "target": recovery_target,
            },
        )
        replace_path(legacy_recovery, recovery_backup)
        recovery_script = recovery_bundle / ("install.sh" if kind == "POSIX" else "install.ps1")
        result = command(kind, executable, recovery_script, shell_args(kind, "install"), legacy_recovery_home)
        check(
            f"{kind} install retry restores and resumes an interrupted legacy migration",
            result.returncode == 0
            and current_revision(legacy_recovery_home) == candidate
            and not recovery_paths.transaction.exists(),
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
            and (installed / ".codegraph/index").read_bytes() == b"local-index\x00"
            and (installed / "custom-hook.sh").read_bytes() == b"#!/bin/sh\nexit 0\n"
            and stat.S_IMODE((installed / "custom-hook.sh").stat().st_mode)
            == stat.S_IMODE((home / ".agents/skills/.issue-flow/local/custom-hook.sh").stat().st_mode)
            and (os.name == "nt" or stat.S_IMODE((installed / "custom-hook.sh").stat().st_mode) == 0o755)
            and (installed / (".cache." + "b" * 32 + ".tmp")).read_bytes()
            == b"legitimate local temporary name\n",
            result,
        )
        result = command(
            kind,
            executable,
            installed / ("install.sh" if kind == "POSIX" else "install.ps1"),
            shell_args(kind, "rollback"),
            home,
        )
        check(
            f"{kind} migration does not expose the in-place legacy installer as rollback",
            result.returncode != 0 and current_revision(home) == candidate,
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
            )
            and (installed / (".cache." + "b" * 32 + ".tmp")).read_bytes()
            == b"legitimate local temporary name\n",
            result,
        )

        external_policy_link = root / "external-policy-link"
        os.link(policy_files[0], external_policy_link)
        result = command(
            kind,
            executable,
            installed / ("install.sh" if kind == "POSIX" else "install.ps1"),
            shell_args(kind, "status"),
            home,
        )
        check(
            f"{kind} rejects an externally hard-linked policy generation",
            result.returncode != 0,
            result,
        )
        external_policy_link.unlink()

        fixture_paths = Paths.for_home(home)
        torn_policy = configured_policy + b"<!-- retry -->\n"
        torn_generation = fixture_paths.policies / hashlib.sha256(torn_policy).hexdigest()
        torn_generation.write_bytes(b"partial")
        healed_generation = policy_generation(fixture_paths, torn_policy)
        check(
            f"{kind} heals a torn content-addressed policy generation",
            healed_generation == torn_generation and healed_generation.read_bytes() == torn_policy,
        )
        torn_generation.unlink()

        interrupted_policy = configured_policy.replace(
            b"| Tracker | linear | github |",
            b"| Tracker | trello | github |",
        )
        configured_generation_name = hashlib.sha256(configured_policy).hexdigest()
        interrupted_generation = policy_generation(fixture_paths, interrupted_policy)
        replace_hardlink(fixture_paths.config, interrupted_generation)
        fixture_paths.policy_transaction.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "generation": interrupted_generation.name,
                    "previous": configured_generation_name,
                }
            ),
            encoding="utf-8",
        )
        result = command(
            kind,
            executable,
            installed / ("install.sh" if kind == "POSIX" else "install.ps1"),
            shell_args(kind, "config"),
            home,
        )
        configured_policy = (installed / "operator.local.md").read_bytes()
        check(
            f"{kind} config retry completes an interrupted policy generation switch",
            result.returncode == 0
            and configured_policy == interrupted_policy
            and not fixture_paths.policy_transaction.exists()
            and os.path.samefile(installed / "operator.local.md", fixture_paths.config),
            result,
        )

        if os.name != "nt":
            external_policy = root / "external-policy"
            external_policy.write_bytes(configured_policy)
            interrupted_generation.unlink()
            interrupted_generation.symlink_to(external_policy)
            fixture_paths.policy_transaction.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "generation": interrupted_generation.name,
                        "previous": configured_generation_name,
                    }
                ),
                encoding="utf-8",
            )
            result = command(
                kind,
                executable,
                installed / ("install.sh" if kind == "POSIX" else "install.ps1"),
                shell_args(kind, "recover"),
                home,
            )
            check(
                f"{kind} rejects a symlinked policy recovery generation",
                result.returncode != 0 and external_policy.read_bytes() == configured_policy,
                result,
            )
            interrupted_generation.unlink()
            os.link(fixture_paths.config, interrupted_generation)
            fixture_paths.policy_transaction.unlink()

        direct_runtime = home / ".claude/skills/issue-flow"
        remove_directory_pointer(direct_runtime)
        create_directory_pointer(direct_runtime, installed.resolve(strict=True))
        result = command(
            kind,
            executable,
            installed / ("install.sh" if kind == "POSIX" else "install.ps1"),
            shell_args(kind, "uninstall", "--dry-run"),
            home,
        )
        check(
            f"{kind} uninstall preserves a direct non-owned bundle pointer",
            result.returncode == 0 and os.path.lexists(direct_runtime) and "SKIP" in result.stdout,
            result,
        )
        result = command(
            kind,
            executable,
            installed / ("install.sh" if kind == "POSIX" else "install.ps1"),
            shell_args(kind, "install"),
            home,
        )
        check(
            f"{kind} refuses a runtime pointer that bypasses canonical",
            result.returncode != 0 and current_revision(home) == candidate,
            result,
        )
        remove_directory_pointer(direct_runtime)
        create_directory_pointer(direct_runtime, installed)

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

        hostile_git_config = root / "hostile-git-config"
        hostile_git_config.write_text(
            '[url "ext::false"]\n\tinsteadOf = file://\n[protocol "ext"]\n\tallow = always\n',
            encoding="utf-8",
        )
        result = command(
            kind,
            executable,
            active_script,
            shell_args(kind, "sync"),
            home,
            {"GIT_CONFIG_GLOBAL": str(hostile_git_config), "GIT_CONFIG_NOSYSTEM": "0"},
        )
        check(
            f"{kind} ignores inherited Git protocol and URL rewrites",
            result.returncode == 0 and current_revision(home) == new,
            result,
        )

        state_root = home / ".agents/skills/.issue-flow"
        state_before_dry_rollback = (state_root / "current.json").read_bytes()
        forged_current = json.loads(state_before_dry_rollback)
        forged_current["current"] = candidate
        (state_root / "current.json").write_text(json.dumps(forged_current), encoding="utf-8")
        result = command(kind, executable, active_script, shell_args(kind, "recover"), home)
        check(
            f"{kind} recovery refuses unexplained pointer and state drift",
            result.returncode != 0
            and current_revision(home) == new
            and json.loads((state_root / "current.json").read_text(encoding="utf-8"))["current"] == candidate,
            result,
        )
        result = command(kind, executable, active_script, shell_args(kind, "rollback"), home)
        check(
            f"{kind} rollback rejects state that disagrees with the active pointer",
            result.returncode != 0 and current_revision(home) == new,
            result,
        )
        (state_root / "current.json").write_bytes(state_before_dry_rollback)

        write_contract(author, "9.99.0", "never activated\n")
        git(author, "add", "-A")
        git(author, "commit", "-m", "unactivated rollback target")
        unactivated = git(author, "rev-parse", "HEAD")
        publish(author, unactivated)
        object_store = state_root / "repository.git"
        git(
            state_root,
            f"--git-dir={object_store}",
            "-c",
            "protocol.file.allow=always",
            "fetch",
            "--no-tags",
            remote.as_uri(),
            f"{unactivated}:refs/issue-flow/test-unactivated",
        )
        publish(author, new)
        unactivated_bundle = materialize_bundle(fixture_paths, unactivated)
        activation_probe = run(
            [
                "git",
                f"--git-dir={object_store}",
                "rev-parse",
                "--verify",
                f"refs/issue-flow/activated/{unactivated}",
            ]
        )
        forged_previous = json.loads(state_before_dry_rollback)
        forged_previous["previous"] = unactivated
        (state_root / "current.json").write_text(json.dumps(forged_previous), encoding="utf-8")
        result = command(kind, executable, active_script, shell_args(kind, "rollback"), home)
        check(
            f"{kind} rollback rejects a commit that was never activated",
            result.returncode != 0
            and current_revision(home) == new
            and unactivated_bundle.exists()
            and activation_probe.returncode != 0,
            result,
        )
        (state_root / "current.json").write_bytes(state_before_dry_rollback)
        shutil.rmtree(unactivated_bundle)
        git(
            state_root,
            f"--git-dir={object_store}",
            "update-ref",
            "-d",
            f"refs/issue-flow/bundles/{unactivated}",
        )
        git(state_root, f"--git-dir={object_store}", "update-ref", "-d", "refs/issue-flow/test-unactivated")

        legacy_bundle = materialize_bundle(fixture_paths, legacy, require_entrypoints=False)
        remove_directory_pointer(fixture_paths.canonical)
        create_directory_pointer(fixture_paths.canonical, legacy_bundle)
        (state_root / "transaction.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "phase": "prepared",
                    "previous": candidate,
                    "target": new,
                    "prior_previous": None,
                }
            ),
            encoding="utf-8",
        )
        retained_script = state_root / "bundles" / new / ("install.sh" if kind == "POSIX" else "install.ps1")
        result = command(kind, executable, retained_script, shell_args(kind, "recover"), home)
        check(
            f"{kind} recovery rejects an active bundle outside journal endpoints",
            result.returncode != 0
            and current_revision(home) == legacy
            and (state_root / "transaction.json").exists(),
            result,
        )
        (state_root / "transaction.json").unlink()
        activate(fixture_paths, state_root / "bundles" / new)
        (state_root / "current.json").write_bytes(state_before_dry_rollback)
        shutil.rmtree(legacy_bundle)
        git(
            state_root,
            f"--git-dir={object_store}",
            "update-ref",
            "-d",
            f"refs/issue-flow/bundles/{legacy}",
        )

        remove_directory_pointer(fixture_paths.canonical)
        result = command(kind, executable, retained_script, shell_args(kind, "uninstall", "--dry-run"), home)
        check(
            f"{kind} uninstall recognizes a lexically owned link when canonical is broken",
            result.returncode == 0 and "would" in result.stdout,
            result,
        )
        fixture_paths.canonical.mkdir()
        (state_root / "transaction.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "phase": "prepared",
                    "previous": candidate,
                    "target": new,
                    "prior_previous": None,
                }
            ),
            encoding="utf-8",
        )
        retained_script = state_root / "bundles" / new / ("install.sh" if kind == "POSIX" else "install.ps1")
        result = command(kind, executable, retained_script, shell_args(kind, "recover"), home)
        check(
            f"{kind} recovery retains its journal for an unrelated real directory",
            result.returncode != 0 and (state_root / "transaction.json").exists(),
            result,
        )
        fixture_paths.canonical.rmdir()
        (state_root / "transaction.json").unlink()
        activate(fixture_paths, state_root / "bundles" / new)
        (state_root / "current.json").write_bytes(state_before_dry_rollback)

        remove_directory_pointer(fixture_paths.canonical)
        (state_root / "transaction.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "phase": "prepared",
                    "previous": candidate,
                    "target": new,
                    "prior_previous": None,
                }
            ),
            encoding="utf-8",
        )
        recovery_script = state_root / "bundles" / new / ("install.sh" if kind == "POSIX" else "install.ps1")
        result = command(kind, executable, recovery_script, shell_args(kind, "recover"), home)
        recovered_state = json.loads((state_root / "current.json").read_text(encoding="utf-8"))
        check(
            f"{kind} interrupted activation restores the pre-transaction rollback chain",
            result.returncode == 0
            and current_revision(home) == candidate
            and recovered_state["previous"] is None,
            result,
        )
        result = command(kind, executable, installed / recovery_script.name, shell_args(kind, "sync"), home)
        check(
            f"{kind} sync resumes after rollback-chain recovery",
            result.returncode == 0 and current_revision(home) == new,
            result,
        )
        state_before_dry_rollback = (state_root / "current.json").read_bytes()

        pack = object_store / "objects/pack"
        saved_pack = object_store / "objects/pack-safe"
        pack.rename(saved_pack)
        external_objects = root / "external-objects"
        external_objects.mkdir()
        create_directory_pointer(pack, external_objects)
        result = command(kind, executable, active_script, shell_args(kind, "sync"), home)
        check(
            f"{kind} refuses a linked Git object destination parent",
            result.returncode != 0 and not any(external_objects.iterdir()) and current_revision(home) == new,
            result,
        )
        remove_directory_pointer(pack)
        saved_pack.rename(pack)
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
            f"{kind} can reactivate the published target and retains resolved bundles",
            result.returncode == 0
            and current_revision(home) == new
            and len([path for path in (state_root / "bundles").iterdir() if path.is_dir()]) == 2,
            result,
        )

        obsolete_file = state_root / "bundles" / candidate / "references/runtime-notes.md"
        obsolete_bytes = obsolete_file.read_bytes()
        obsolete_file.write_bytes(b"obsolete local damage\n")
        result = command(kind, executable, active_script, shell_args(kind, "sync"), home)
        check(
            f"{kind} retained bundle damage does not wedge active sync",
            result.returncode == 0 and current_revision(home) == new,
            result,
        )
        result = command(kind, executable, active_script, shell_args(kind, "status"), home)
        check(
            f"{kind} status reports retained bundle damage without failing",
            result.returncode == 0 and "corrupt=1" in result.stdout,
            result,
        )
        obsolete_file.write_bytes(obsolete_bytes)

        obsolete_receipt = state_root / "bundles" / candidate / ".issue-flow-bundle.json"
        obsolete_receipt_bytes = obsolete_receipt.read_bytes()
        obsolete_receipt.write_bytes(b"\xff\xfe")
        wrong_type_bundle = state_root / "bundles" / ("f" * 40)
        wrong_type_bundle.write_bytes(b"not a bundle")
        result = command(kind, executable, active_script, shell_args(kind, "status"), home)
        check(
            f"{kind} status counts malformed receipts and wrong-type bundle entries",
            result.returncode == 0 and "corrupt=2" in result.stdout,
            result,
        )
        wrong_type_bundle.unlink()
        obsolete_receipt.write_bytes(obsolete_receipt_bytes)

        active_receipt = installed / ".issue-flow-bundle.json"
        active_receipt_bytes = active_receipt.read_bytes()
        malformed_active = json.loads(active_receipt_bytes)
        first_metadata = next(iter(malformed_active["files"].values()))
        first_metadata["mode"] = "invalid"
        active_receipt.write_text(json.dumps(malformed_active), encoding="utf-8")
        result = command(kind, executable, active_script, shell_args(kind, "status"), home)
        check(
            f"{kind} active status reports malformed metadata without a traceback",
            result.returncode != 0 and "error:" in result.stderr and "Traceback" not in result.stderr,
            result,
        )
        active_receipt.write_bytes(active_receipt_bytes)

        bundle_store = state_root / "bundles"
        saved_bundle_store = state_root / "bundles-safe"
        bundle_store.rename(saved_bundle_store)
        bundle_store.write_bytes(b"wrong type")
        retained_status_script = saved_bundle_store / new / active_script.name
        result = command(kind, executable, retained_status_script, shell_args(kind, "status"), home)
        check(
            f"{kind} status rejects a wrong-type bundle store without a traceback",
            result.returncode != 0 and "error:" in result.stderr and "Traceback" not in result.stderr,
            result,
        )
        bundle_store.unlink()
        saved_bundle_store.rename(bundle_store)

        inactive_policy = state_root / "bundles" / candidate / "operator.local.md"
        inactive_policy.unlink()
        inactive_policy.mkdir()
        result = command(kind, executable, active_script, shell_args(kind, "status"), home)
        check(
            f"{kind} status reports corrupt retained policy attachments",
            result.returncode == 0 and "corrupt=1" in result.stdout,
            result,
        )
        inactive_policy.rmdir()
        replace_hardlink(inactive_policy, fixture_paths.config)

        lock_path = home / ".agents/skills/.issue-flow/sync.lock"
        state_root = lock_path.parent
        lock_path.unlink()
        lock_victim = root / "lock-victim"
        lock_victim.write_bytes(b"must-survive")
        os.link(lock_victim, lock_path)
        result = command(kind, executable, active_script, shell_args(kind, "recover"), home)
        check(
            f"{kind} lock refuses an external hard link without truncating it",
            result.returncode != 0 and lock_victim.read_bytes() == b"must-survive",
            result,
        )
        lock_path.unlink()
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
                    "previous": None,
                    "activated_at": 0,
                }
            ),
            encoding="utf-8",
        )
        git(
            state_root,
            f"--git-dir={object_store}",
            "update-ref",
            "-d",
            f"refs/issue-flow/bundles/{new}",
        )
        with InstallerLock(Paths.for_home(home)):
            locked = command(kind, executable, active_script, shell_args(kind, "recover"), home)
        check(
            f"{kind} recovery cannot steal a live operating-system lock",
            locked.returncode != 0,
            locked,
        )
        temporary_id = "d" * 32
        abandoned_repository = state_root / f".repository-{temporary_id}"
        abandoned_repository.mkdir()
        abandoned_json = state_root / f".current.json.{temporary_id}.tmp"
        abandoned_json.write_text("partial", encoding="utf-8")
        abandoned_activation = home / f".agents/skills/.issue-flow.activate-{temporary_id}"
        create_directory_pointer(abandoned_activation, installed.resolve(strict=True))
        abandoned_bundle_link = installed / f".operator.local.md.{temporary_id}.tmp"
        os.link(fixture_paths.config, abandoned_bundle_link)
        abandoned_policy = fixture_paths.policies / f".policy.{temporary_id}.tmp"
        abandoned_policy.write_bytes(b"partial")
        abandoned_object = object_store / "objects/pack" / f".object.{temporary_id}.tmp"
        abandoned_object.write_bytes(b"partial")
        result = command(kind, executable, active_script, shell_args(kind, "recover"), home)
        check(
            f"{kind} recovers a post-switch receipt write after lock release",
            result.returncode == 0
            and not (state_root / "transaction.json").exists()
            and not abandoned_repository.exists()
            and not abandoned_json.exists()
            and not os.path.lexists(abandoned_activation)
            and not abandoned_bundle_link.exists()
            and not abandoned_policy.exists()
            and not abandoned_object.exists()
            and current_revision(home) == new
            and json.loads((state_root / "current.json").read_text(encoding="utf-8"))["previous"] == candidate,
            result,
        )
        check(
            f"{kind} recovery re-retains active Git objects",
            git(state_root, f"--git-dir={object_store}", "rev-parse", f"refs/issue-flow/bundles/{new}") == new,
        )

        if os.name == "nt":
            linked_temporary = state_root / (".repository-" + "e" * 32)
            linked_temporary.mkdir()
            cleanup_victim = root / "cleanup-victim"
            cleanup_victim.write_bytes(b"must-remain-read-only")
            os.chmod(cleanup_victim, stat.S_IRUSR)
            os.link(cleanup_victim, linked_temporary / "victim")
            result = command(kind, executable, active_script, shell_args(kind, "recover"), home)
            check(
                f"{kind} cleanup never chmods an external hard-linked file",
                result.returncode != 0
                and cleanup_victim.read_bytes() == b"must-remain-read-only"
                and not (cleanup_victim.stat().st_mode & stat.S_IWUSR),
                result,
            )
            os.chmod(cleanup_victim, stat.S_IRUSR | stat.S_IWUSR)
            (linked_temporary / "victim").unlink()
            linked_temporary.rmdir()

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

        forged_backup = state_root / "legacy/forged"
        create_directory_pointer(forged_backup, outside_backup)
        (state_root / "transaction.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "phase": "moved",
                    "previous": candidate,
                    "target": new,
                    "backup": str(forged_backup),
                }
            ),
            encoding="utf-8",
        )
        result = command(kind, executable, active_script, shell_args(kind, "recover"), home)
        check(
            f"{kind} recovery rejects an in-store backup pointer",
            result.returncode != 0 and outside_backup.is_dir() and current_revision(home) == new,
            result,
        )
        (state_root / "transaction.json").unlink()
        remove_directory_pointer(forged_backup)

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

        retained_reader = state_root / "bundles" / candidate / "references/runtime-notes.md"
        git(author, "reset", "--hard", new)
        write_contract(author, "1.12.2", "third companion\n")
        git(author, "add", "-A")
        git(author, "commit", "-m", "third contract")
        third = git(author, "rev-parse", "HEAD")
        publish(author, third)
        result = command(kind, executable, active_script, shell_args(kind, "sync"), home)
        check(
            f"{kind} readers retain resolved bundles across multiple upgrades",
            result.returncode == 0
            and current_revision(home) == third
            and retained_reader.read_text(encoding="utf-8") == "candidate companion\n"
            and len([path for path in (state_root / "bundles").iterdir() if path.is_dir()]) == 3,
            result,
        )


print()
print(f"{len(FAILURES)} failure(s)" + (f": {FAILURES}" if FAILURES else ""))
raise SystemExit(1 if FAILURES else 0)
