#!/usr/bin/env python3
"""Cross-shell acceptance tests for immutable, commit-bound skill bundles."""

from __future__ import annotations

import os
import hashlib
import json
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import install_bundle as installer
from install_bundle import (
    InstallerLock,
    InstallError,
    Paths,
    activate,
    clean_git_environment,
    copy_object_database,
    create_directory_pointer,
    ensure_real_directory,
    fetch_target,
    initialize_state,
    is_pointer,
    materialize_bundle,
    normalized_repository_url,
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
CHECKS = 0

for unsafe_path in (
    r"..\outside",
    r"C:\outside",
    "payload:stream",
    "trailing. ",
    "CON/file.md",
    "COM¹.txt",
    "LPT².log",
    "control-\u0001.md",
    "decomposed-e\u0301.md",
):
    try:
        validate_tree_path(unsafe_path)
    except InstallError:
        continue
    FAILURES.append(f"unsafe tree path accepted: {unsafe_path!r}")

portable_prefixes: dict[str, str] = {}
validate_tree_path("Docs/first.md", portable_prefixes)
try:
    validate_tree_path("docs/second.md", portable_prefixes)
except InstallError:
    pass
else:
    FAILURES.append("case-only tree prefix collision accepted")


def check(name: str, condition: bool, result: subprocess.CompletedProcess | None = None) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        FAILURES.append(name)
        if result:
            print(result.stdout.encode("ascii", "backslashreplace").decode("ascii"))
            print(result.stderr.encode("ascii", "backslashreplace").decode("ascii"))
        print(f"FAIL {name}")


check(
    "documented GitHub clone URL matches the canonical legacy origin",
    normalized_repository_url("https://github.com/asanabrial/issue-flow")
    == normalized_repository_url("https://github.com/asanabrial/issue-flow.git"),
)
check(
    "credential-bearing GitHub URLs are not canonical legacy origins",
    normalized_repository_url("https://token@github.com/asanabrial/issue-flow.git")
    != normalized_repository_url("https://github.com/asanabrial/issue-flow.git"),
)
saved_ca = {name: os.environ.get(name) for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE")}
try:
    for name in saved_ca:
        os.environ[name] = "attacker-controlled-ca"
    isolated_environment = clean_git_environment()
    check(
        "authoritative Git ignores caller-controlled CA overrides",
        not set(saved_ca).intersection(isolated_environment),
    )
finally:
    for name, value in saved_ca.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

if os.name != "nt":
    with tempfile.TemporaryDirectory() as durable_directory:
        durable_root = Path(durable_directory)
        durable_target = durable_root / "first" / "second"
        fsynced_parents: list[Path] = []
        original_fsync_directory = installer.fsync_directory
        installer.fsync_directory = fsynced_parents.append
        original_umask = os.umask(0)
        try:
            ensure_real_directory(durable_target)
        finally:
            os.umask(original_umask)
            installer.fsync_directory = original_fsync_directory
        check(
            "POSIX directory creation is durable and private under a permissive umask",
            fsynced_parents == [durable_root, durable_root / "first"]
            and stat.S_IMODE((durable_root / "first").stat().st_mode) == 0o700
            and stat.S_IMODE(durable_target.stat().st_mode) == 0o700,
        )
        source_parent = durable_root / "source"
        destination_parent = durable_root / "destination"
        source_parent.mkdir()
        destination_parent.mkdir()
        source = source_parent / "entry"
        source.write_bytes(b"durable move")
        fsynced_parents.clear()
        installer.fsync_directory = fsynced_parents.append
        try:
            replace_path(source, destination_parent / "entry")
        finally:
            installer.fsync_directory = original_fsync_directory
        check(
            "cross-directory replace persists the destination before source deletion",
            fsynced_parents == [destination_parent, source_parent],
        )


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


help_result = run([sys.executable, str(ROOT / "scripts/install_bundle.py"), "--help"])
check(
    "shared installer help marks single-file sync as retired",
    help_result.returncode == 0 and "retired" in help_result.stdout and "--from" in help_result.stdout,
    help_result,
)


def git(cwd: Path, *args: str) -> str:
    return run(["git", *args], cwd, check_result=True).stdout.strip()


def shells() -> list[tuple[str, str]]:
    posix = shutil.which("sh")
    if not posix and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/sh.exe"
        posix = str(candidate) if candidate.is_file() else None
    if os.environ.get("ISSUE_FLOW_TEST_SHELL") == "POSIX":
        if not posix:
            raise RuntimeError("native POSIX sh is required for the requested lane")
        return [("POSIX", posix)]
    pwsh = shutil.which("pwsh")
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
    extra_setting = "| New setting | default | default |\n" if version not in {"1.11.0", "1.12.0"} else ""
    (repository / "SKILL.md").write_text(
        "---\nname: issue-flow\nmetadata:\n"
        f"  version: \"{version}\"\n---\n\n"
        "Load [runtime].\n\n"
        "[runtime]: references/runtime-notes.md\n\n"
        "<!-- issue-flow:config:start -->\n"
        "| Setting | Value here | Skill default |\n"
        "|---|---|---|\n"
        "| Tracker | github | github |\n"
        f"{extra_setting}"
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
    (target / ".empty-cache").mkdir()


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


def divergent_profile_powershell(
    kind: str,
    executable: str,
    script: Path,
    home: Path,
    wrong_profile: Path,
) -> subprocess.CompletedProcess:
    escaped_script = str(script).replace("'", "''")
    escaped_profile = str(wrong_profile).replace("'", "''")
    invocation = [
        executable,
        "-NoProfile",
        *(["-ExecutionPolicy", "Bypass"] if kind == "Windows PowerShell" else []),
        "-Command",
        f"$env:USERPROFILE = '{escaped_profile}'; & '{escaped_script}' status",
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


def secondary_shell_smoke(
    kind: str,
    executable: str,
    root: Path,
    remote: Path,
    author: Path,
    candidate: str,
    new: str,
    external: Path,
) -> None:
    home = root / f"{kind} smoke home"
    (home / ".claude").mkdir(parents=True)
    (home / ".codex").mkdir()
    result = command(kind, executable, external, shell_args(kind, "install", "--dry-run"), home)
    check(
        f"{kind} smoke dry-run is non-mutating",
        result.returncode == 0 and not (home / ".agents").exists(),
        result,
    )
    result = command(kind, executable, external, shell_args(kind, "install"), home)
    installed = home / ".agents/skills/issue-flow"
    script = installed / ("install.sh" if kind == "POSIX" else "install.ps1")
    check(
        f"{kind} smoke installs one complete bundle",
        result.returncode == 0 and current_revision(home) == candidate,
        result,
    )
    result = command(kind, executable, script, shell_args(kind, "config", "--set", "Tracker=linear"), home)
    check(
        f"{kind} smoke publishes configuration",
        result.returncode == 0 and b"| Tracker | linear |" in (installed / "operator.local.md").read_bytes(),
        result,
    )
    publish(author, new)
    try:
        result = command(kind, executable, script, shell_args(kind, "sync"), home)
    finally:
        publish(author, candidate)
    check(
        f"{kind} smoke atomically upgrades the bundle",
        result.returncode == 0 and current_revision(home) == new,
        result,
    )
    if kind == "Windows PowerShell":
        iex_home = root / "Windows PowerShell IEX smoke home"
        iex_home.mkdir()
        result, sentinel = outer_script_powershell(kind, executable, external, iex_home, root)
        check(
            "Windows PowerShell smoke executes through IEX and returns control",
            result.returncode == 0 and "after-outer-iex" in result.stdout and not sentinel.exists(),
            result,
        )


for kind, executable in shells():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        author, remote, legacy, candidate, new = repositories(root)
        installer.REPOSITORY_URL = remote.as_uri()
        home = root / "home with spaces"
        installed = home / ".agents/skills/issue-flow"
        external = external_installer(root, kind, remote.as_uri())

        full_kind = "PowerShell" if os.name == "nt" else "POSIX"
        if kind != full_kind:
            secondary_shell_smoke(kind, executable, root, remote, author, candidate, new, external)
            continue

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
                and current_revision(piped_install_home) == candidate
                and not list(piped_install_home.glob(".issue-flow-bootstrap-*")),
                result,
            )
            hostile_path = root / "hostile path"
            hostile_path.mkdir()
            path_sentinel = root / "path-helper-sentinel"
            for utility in ("cygpath", "mktemp", "mkdir", "env", "rm"):
                script = hostile_path / utility
                script.write_text(
                    f"#!/bin/sh\nprintf '{utility}' >> '{path_sentinel.as_posix()}'\nexit 99\n",
                    encoding="utf-8",
                )
                os.chmod(script, 0o755)
            hostile_path_home = root / "hostile path home"
            hostile_path_home.mkdir()
            result = piped_posix(
                executable,
                external,
                ["install"],
                hostile_path_home,
                hostile,
                {"PATH": str(hostile_path) + os.pathsep + os.environ["PATH"]},
            )
            check(
                "POSIX bootstrap executes only declared PATH trust roots",
                result.returncode == 0 and not path_sentinel.exists(),
                result,
            )

        fresh_home = root / "fresh dry-run home"
        (fresh_home / ".claude").mkdir(parents=True)
        (fresh_home / ".codex").mkdir()
        result = command(kind, executable, external, shell_args(kind, "sync", "--dry-run"), fresh_home)
        check(
            f"{kind} fresh dry-run returns before bootstrap mutation",
            result.returncode == 0
            and not (fresh_home / ".agents").exists()
            and not (fresh_home / ".issue-flow-bootstrap.lock").exists(),
            result,
        )
        empty_command_home = root / "empty command home"
        empty_command_home.mkdir()
        result = command(kind, executable, external, shell_args(kind, "rollback", "--dry-run"), empty_command_home)
        check(
            f"{kind} fresh rollback dry-run reports the missing predecessor",
            result.returncode != 0 and "would   install" not in result.stdout,
            result,
        )
        result = command(kind, executable, external, shell_args(kind, "uninstall", "--dry-run"), empty_command_home)
        check(
            f"{kind} fresh uninstall dry-run does not claim it would install",
            result.returncode == 0 and "would   install" not in result.stdout,
            result,
        )
        result = command(
            kind,
            executable,
            external,
            shell_args(kind, "install", "--dry-run", "--set", "Tracker=linear"),
            empty_command_home,
        )
        check(
            f"{kind} fresh dry-run still validates command-specific options",
            result.returncode != 0 and not (empty_command_home / ".agents").exists(),
            result,
        )
        result = command(
            kind,
            executable,
            external,
            shell_args(kind, "sync", "--from", ""),
            empty_command_home,
        )
        check(
            f"{kind} retired single-file sync rejects an explicitly empty source",
            result.returncode != 0 and not (empty_command_home / ".agents").exists(),
            result,
        )
        result = command(
            kind,
            executable,
            external,
            shell_args(kind, "config", "--set", ""),
            empty_command_home,
        )
        check(
            f"{kind} config rejects an explicitly empty assignment before installation",
            result.returncode != 0 and not (empty_command_home / ".agents").exists(),
            result,
        )
        for invalid_assignment in ("=linear", "Tracker=value|invalid"):
            result = command(
                kind,
                executable,
                external,
                shell_args(kind, "config", "--set", invalid_assignment),
                empty_command_home,
            )
            check(
                f"{kind} config rejects {invalid_assignment!r} before installation",
                result.returncode != 0 and not (empty_command_home / ".agents").exists(),
                result,
            )
        result = command(
            kind,
            executable,
            external,
            shell_args(kind, "install", "--unknown-option", "--dry-run"),
            empty_command_home,
        )
        check(
            f"{kind} fresh dry-run rejects unknown options before success",
            result.returncode != 0 and "would   install" not in result.stdout,
            result,
        )
        result = command(kind, executable, external, shell_args(kind, "unknown", "--dry-run"), empty_command_home)
        check(
            f"{kind} fresh dry-run rejects unknown commands before success",
            result.returncode != 0 and "would   install" not in result.stdout,
            result,
        )
        if kind == "POSIX":
            result = command(
                kind,
                executable,
                external,
                ["sync", "--fro", "payload", "--dry-run"],
                empty_command_home,
            )
            check(
                "POSIX fresh dry-run rejects abbreviated retired options",
                result.returncode != 0 and "would   install" not in result.stdout,
                result,
            )
            result = command(kind, executable, external, ["--help"], empty_command_home)
            check(
                "POSIX fresh help returns without bootstrap mutation",
                result.returncode == 0 and "usage:" in result.stdout and not (empty_command_home / ".agents").exists(),
                result,
            )

        state_only_home = root / "state-only dry-run home"
        state_only_paths = Paths.for_home(state_only_home)
        state_only_target = state_only_paths.legacy / "preserved" / "references"
        state_only_target.mkdir(parents=True)
        write_json(
            state_only_paths.attachments,
            {
                "schema": 1,
                "entries": [
                    {
                        "kind": "directory",
                        "name": "references",
                        "target": str(state_only_target),
                    }
                ],
            },
        )
        for dry_command in ("install", "sync", "config"):
            result = command(
                kind,
                executable,
                external,
                shell_args(kind, dry_command, "--dry-run"),
                state_only_home,
            )
            check(
                f"{kind} fresh {dry_command} dry-run validates preserved private state",
                result.returncode != 0
                and "would   install" not in result.stdout
                and not state_only_paths.canonical.exists(),
                result,
            )
        malformed_config_author = root / "malformed config author"
        git(root, "clone", author.as_posix(), malformed_config_author.as_posix())
        git(malformed_config_author, "config", "user.email", "test@example.com")
        git(malformed_config_author, "config", "user.name", "Installer Test")
        git(malformed_config_author, "reset", "--hard", candidate)
        malformed_skill = malformed_config_author / "SKILL.md"
        malformed_skill.write_text(
            malformed_skill.read_text(encoding="utf-8").replace("<!-- issue-flow:config:start -->", ""),
            encoding="utf-8",
        )
        git(malformed_config_author, "add", "SKILL.md")
        git(malformed_config_author, "commit", "-m", "remove config boundary")
        malformed_config = git(malformed_config_author, "rev-parse", "HEAD")
        git(remote, "fetch", malformed_config_author.as_uri(), f"{malformed_config}:refs/heads/malformed-config")
        git(remote, "update-ref", "refs/heads/main", malformed_config)
        malformed_config_home = root / "malformed config dry-run home"
        malformed_config_home.mkdir()
        try:
            result = command(
                kind,
                executable,
                external,
                shell_args(kind, "config", "--dry-run"),
                malformed_config_home,
            )
        finally:
            git(remote, "update-ref", "refs/heads/main", candidate)
            git(remote, "update-ref", "-d", "refs/heads/malformed-config")
        check(
            f"{kind} fresh config dry-run validates fetched configuration markers",
            result.returncode != 0 and not (malformed_config_home / ".agents").exists(),
            result,
        )
        fresh_install_home = root / "fresh install home \u00f1"
        (fresh_install_home / ".claude").mkdir(parents=True)
        (fresh_install_home / ".codex").mkdir()
        if os.name == "nt" and kind == "PowerShell":
            junction_home = root / "bootstrap junction home"
            junction_home.mkdir()
            junction_target = root / "bootstrap junction target"
            (junction_target / "repository.git").mkdir(parents=True)
            (junction_target / ".issue-flow-bootstrap-owner").write_bytes(b"owner")
            junction_sentinel = junction_target / "repository.git/sentinel"
            junction_sentinel.write_bytes(b"preserve")
            junction = junction_home / (".issue-flow-bootstrap-" + "a" * 32)
            create_directory_pointer(junction, junction_target)
            result = command(kind, executable, external, shell_args(kind, "install"), junction_home)
            check(
                "PowerShell bootstrap refuses a quarantine junction without deleting its target",
                result.returncode != 0 and junction_sentinel.read_bytes() == b"preserve",
                result,
            )
            remove_directory_pointer(junction)
        stale_bootstrap = fresh_install_home / (".issue-flow-bootstrap-" + "d" * 32)
        (stale_bootstrap / "repository.git").mkdir(parents=True)
        (stale_bootstrap / ".issue-flow-bootstrap-owner").write_bytes(b"\0")
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
            result.returncode == 0 and not python_sentinel.exists() and not stale_bootstrap.exists(),
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
            )
            and not list(fresh_install_home.glob(".issue-flow-bootstrap-*")),
            result,
        )
        fresh_script = fresh_canonical / ("install.sh" if kind == "POSIX" else "install.ps1")
        fresh_state = fresh_install_home / ".agents/skills/.issue-flow"
        fresh_paths = Paths.for_home(fresh_install_home)
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
        offline_remote = root / "remote-offline.git"
        remote.rename(offline_remote)
        try:
            result = command(kind, executable, fresh_script, shell_args(kind, "install"), fresh_install_home)
        finally:
            offline_remote.rename(remote)
        check(
            f"{kind} install retry recovers first activation locally while remote is unavailable",
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
        offline_status_remote = root / "healthy-status-remote-offline.git"
        remote.rename(offline_status_remote)
        try:
            result = command(kind, executable, fresh_script, shell_args(kind, "status"), fresh_install_home)
        finally:
            offline_status_remote.rename(remote)
        check(
            f"{kind} healthy installed status needs no remote reacquisition",
            result.returncode == 0 and "immutable bundle" in result.stdout,
            result,
        )
        _, _, initial_policy_text = installer.config_block(
            (fresh_canonical / "SKILL.md").read_text(encoding="utf-8"),
            fresh_canonical / "SKILL.md",
        )
        initial_generation = policy_generation(fresh_paths, initial_policy_text.encode("utf-8"))
        write_json(
            fresh_paths.policy_transaction,
            {"schema": 1, "generation": initial_generation.name, "previous": None},
        )
        result = command(kind, executable, fresh_script, shell_args(kind, "recover"), fresh_install_home)
        check(
            f"{kind} recovery completes an interrupted first policy publication",
            result.returncode == 0
            and not fresh_paths.policy_transaction.exists()
            and fresh_paths.config.read_text(encoding="utf-8") == initial_policy_text
            and os.path.samefile(fresh_canonical / "operator.local.md", initial_generation),
            result,
        )
        (fresh_canonical / "operator.local.md").unlink()
        fresh_paths.config.unlink()
        initial_generation.unlink()
        if kind != "POSIX":
            divergent_profile = root / f"{kind} divergent profile"
            divergent_profile.mkdir()
            result = divergent_profile_powershell(
                kind,
                executable,
                fresh_script,
                fresh_install_home,
                divergent_profile,
            )
            check(
                f"{kind} binds verified and mutated state to one resolved home",
                result.returncode == 0
                and "immutable bundle" in result.stdout
                and not (divergent_profile / ".agents").exists(),
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
        fresh_current = fresh_state / "current.json"
        fresh_current_bytes = fresh_current.read_bytes()
        fresh_current.unlink()
        publish(author, new)
        try:
            result = command(kind, executable, fresh_script, shell_args(kind, "install"), fresh_install_home)
        finally:
            publish(author, candidate)
        check(
            f"{kind} install fails closed when active state is missing without a journal",
            result.returncode != 0
            and not fresh_current.exists()
            and not (fresh_state / f"bundles/{new}").exists()
            and run(
                [
                    "git",
                    f"--git-dir={fresh_state / 'repository.git'}",
                    "rev-parse",
                    "--verify",
                    f"refs/issue-flow/bundles/{new}",
                ]
            ).returncode
            != 0,
            result,
        )
        result = command(
            kind,
            executable,
            fresh_script,
            shell_args(kind, "config", "--set", "Tracker=linear"),
            fresh_install_home,
        )
        check(
            f"{kind} config fails closed when active state is missing without a journal",
            result.returncode != 0 and not (fresh_state / "operator.local.md").exists(),
            result,
        )
        result = command(kind, executable, fresh_script, shell_args(kind, "status"), fresh_install_home)
        check(
            f"{kind} status fails closed when active state is missing without a journal",
            result.returncode != 0,
            result,
        )
        fresh_current.write_bytes(fresh_current_bytes)
        if kind == "PowerShell":
            failed_activation_home = root / "failed first activation home"
            failed_activation_paths = Paths.for_home(failed_activation_home)
            original_activate = installer.activate

            def fail_first_activation(_paths: Paths, _bundle: Path) -> None:
                raise InstallError("injected pre-pointer activation failure")

            installer.activate = fail_first_activation
            try:
                try:
                    installer.sync(failed_activation_paths, False)
                except InstallError:
                    pass
            finally:
                installer.activate = original_activate
            protected_transaction = json.loads(failed_activation_paths.transaction.read_text(encoding="utf-8"))
            protected_target = str(protected_transaction["target"])
            conflict_ref = f"refs/issue-flow/activated/{protected_target}"
            target_tree = git(
                failed_activation_paths.state,
                f"--git-dir={failed_activation_paths.repository}",
                "rev-parse",
                f"{protected_target}^{{tree}}",
            )
            git(
                failed_activation_paths.state,
                f"--git-dir={failed_activation_paths.repository}",
                "-c",
                "user.name=Installer Test",
                "-c",
                "user.email=test@example.com",
                "tag",
                "-a",
                "raw-ref-test",
                "-m",
                "raw ref test",
                protected_target,
            )
            tag_object = git(
                failed_activation_paths.state,
                f"--git-dir={failed_activation_paths.repository}",
                "rev-parse",
                "refs/tags/raw-ref-test",
            )
            first_activation_rejections: list[bool] = []
            for raw_target in (legacy, target_tree, tag_object):
                git(
                    failed_activation_paths.state,
                    f"--git-dir={failed_activation_paths.repository}",
                    "update-ref",
                    "--no-deref",
                    conflict_ref,
                    raw_target,
                )
                try:
                    try:
                        installer.recover(failed_activation_paths, True)
                    except InstallError:
                        first_activation_rejections.append(True)
                    else:
                        first_activation_rejections.append(False)
                finally:
                    git(
                        failed_activation_paths.state,
                        f"--git-dir={failed_activation_paths.repository}",
                        "update-ref",
                        "--no-deref",
                        "-d",
                        conflict_ref,
                        raw_target,
                    )
            git(
                failed_activation_paths.state,
                f"--git-dir={failed_activation_paths.repository}",
                "update-ref",
                "--no-deref",
                "-d",
                "refs/tags/raw-ref-test",
                tag_object,
            )
            check(
                "shared recovery dry-run rejects conflicting and non-commit first-activation refs",
                all(first_activation_rejections) and failed_activation_paths.transaction.exists(),
            )
            offline_remote = root / "failed-activation-remote-offline.git"
            remote.rename(offline_remote)
            try:
                installer.recover(failed_activation_paths, False)
            finally:
                offline_remote.rename(remote)
            check(
                "shared installer retains a journal target after handled activation failure for offline recovery",
                current_revision(failed_activation_home) == protected_target
                and not failed_activation_paths.transaction.exists(),
            )
            config_race_home = root / "config main race home"
            config_race_home.mkdir()
            config_race_paths = Paths.for_home(config_race_home)
            original_dry_run_sync = installer.dry_run_sync

            def advance_main_after_config_preflight(paths: Paths, announce: bool = True) -> tuple[str, str]:
                preflight = original_dry_run_sync(paths, announce)
                publish(author, new)
                return preflight

            installer.dry_run_sync = advance_main_after_config_preflight
            try:
                try:
                    installer.configure(config_race_paths, "Tracker=linear", False)
                except InstallError:
                    pass
            finally:
                installer.dry_run_sync = original_dry_run_sync
                publish(author, candidate)
            check(
                "shared config refuses to activate main when it changes after preflight",
                not os.path.lexists(config_race_paths.canonical),
            )
        publish(author, new)
        try:
            orphan_target = fetch_target(fresh_paths)
            orphan_bundle = materialize_bundle(fresh_paths, orphan_target)
        finally:
            publish(author, candidate)
        result = command(kind, executable, fresh_script, shell_args(kind, "status"), fresh_install_home)
        check(
            f"{kind} status exposes a fetched but never-activated target as pending recovery",
            result.returncode == 0 and f"incoming={new}" in result.stdout and "unactivated=1" in result.stdout,
            result,
        )
        result = command(kind, executable, fresh_script, shell_args(kind, "recover"), fresh_install_home)
        orphan_reference = f"refs/issue-flow/bundles/{new}"
        check(
            f"{kind} recovery sweeps a fetched bundle and its incoming ref when activation never started",
            result.returncode == 0
            and not orphan_bundle.exists()
            and run(
                ["git", f"--git-dir={fresh_state / 'repository.git'}", "rev-parse", "--verify", orphan_reference]
            ).returncode
            != 0,
            result,
        )
        check(
            f"{kind} recovery removes the abandoned incoming acquisition ref",
            run(
                [
                    "git",
                    f"--git-dir={fresh_state / 'repository.git'}",
                    "rev-parse",
                    "--verify",
                    "refs/issue-flow/incoming",
                ]
            ).returncode
            != 0,
            result,
        )
        orphan_bundle = materialize_bundle(fresh_paths, new)
        tombstone = orphan_bundle.with_name(f".discard-{new}-" + "e" * 32)
        replace_path(orphan_bundle, tombstone)
        result = command(kind, executable, fresh_script, shell_args(kind, "recover"), fresh_install_home)
        check(
            f"{kind} recovery completes an interrupted unactivated-bundle discard",
            result.returncode == 0
            and not tombstone.exists()
            and run(
                ["git", f"--git-dir={fresh_state / 'repository.git'}", "rev-parse", "--verify", orphan_reference]
            ).returncode
            != 0,
            result,
        )
        fresh_attachments = fresh_state / "attachments.json"
        fresh_attachments.write_bytes(b"not json")
        result = command(kind, executable, fresh_script, shell_args(kind, "uninstall"), fresh_install_home)
        check(
            f"{kind} uninstall removes runtime links despite corrupt attachment metadata",
            result.returncode == 0
            and fresh_canonical.exists()
            and not (fresh_install_home / ".claude/skills/issue-flow").exists()
            and not (fresh_install_home / ".codex/skills/issue-flow").exists(),
            result,
        )
        fresh_attachments.unlink()

        blocked_home = root / "stale runtime home"
        (blocked_home / ".claude/skills/issue-flow").mkdir(parents=True)
        for dry_command in ("install", "sync", "config"):
            result = command(kind, executable, external, shell_args(kind, dry_command, "--dry-run"), blocked_home)
            check(
                f"{kind} fresh {dry_command} dry-run refuses an independent runtime copy",
                result.returncode != 0
                and "would   install" not in result.stdout
                and not (blocked_home / ".agents").exists(),
                result,
            )
        result = command(kind, executable, external, shell_args(kind, "install"), blocked_home)
        check(
            f"{kind} refuses an independent runtime copy before canonical installation",
            result.returncode != 0 and not (blocked_home / ".agents").exists(),
            result,
        )
        linked_runtime_home = root / "linked runtime parent home"
        (linked_runtime_home / ".claude").mkdir(parents=True)
        external_runtime_root = root / "external runtime root"
        external_runtime_root.mkdir()
        create_directory_pointer(linked_runtime_home / ".claude/skills", external_runtime_root)
        dry_result = command(
            kind,
            executable,
            external,
            shell_args(kind, "install", "--dry-run"),
            linked_runtime_home,
        )
        result = command(kind, executable, external, shell_args(kind, "install"), linked_runtime_home)
        check(
            f"{kind} dry and live install refuse a linked runtime ancestor",
            dry_result.returncode != 0
            and result.returncode != 0
            and not (linked_runtime_home / ".agents").exists()
            and not (external_runtime_root / "issue-flow").exists(),
            result,
        )
        remove_directory_pointer(linked_runtime_home / ".claude/skills")
        linked_state_home = root / "linked state parent home"
        (linked_state_home / ".agents").mkdir(parents=True)
        external_state_root = root / "external state root"
        external_state_root.mkdir()
        create_directory_pointer(linked_state_home / ".agents/skills", external_state_root)
        dry_result = command(
            kind,
            executable,
            external,
            shell_args(kind, "install", "--dry-run"),
            linked_state_home,
        )
        result = command(kind, executable, external, shell_args(kind, "install"), linked_state_home)
        check(
            f"{kind} dry and live install refuse a linked installer-state ancestor",
            dry_result.returncode != 0
            and result.returncode != 0
            and not (external_state_root / ".issue-flow").exists(),
            result,
        )
        remove_directory_pointer(linked_state_home / ".agents/skills")
        lock_only_home = root / "lock-only home"
        lock_only_paths = Paths.for_home(lock_only_home)
        lock_only_paths.skills.mkdir(parents=True)
        with InstallerLock(lock_only_paths):
            blocked_install = command(kind, executable, external, shell_args(kind, "install"), lock_only_home)
            result = command(kind, executable, external, shell_args(kind, "uninstall"), lock_only_home)
        check(
            f"{kind} first install acquires the stable lock before creating private state",
            blocked_install.returncode != 0 and not lock_only_paths.state.exists(),
            blocked_install,
        )
        check(
            f"{kind} first uninstall shares the stable operating-system lock with first install",
            result.returncode != 0 and not lock_only_paths.state.exists(),
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

        partial_legacy_home = root / "partial legacy home"
        partial_legacy = partial_legacy_home / ".agents/skills/issue-flow"
        clone_legacy(remote, partial_legacy, legacy)
        lazy_fetch_sentinel = root / "lazy-fetch-sentinel"
        (partial_legacy / ".git/promisor.inc").write_text(
            "[protocol \"ext\"]\n\tallow = always\n"
            "[remote \"origin\"]\n\tpromisor = true\n\tpartialCloneFilter = blob:none\n"
            f"\turl = ext::touch {lazy_fetch_sentinel.as_posix()}\n",
            encoding="utf-8",
        )
        git(partial_legacy, "config", "include.path", "promisor.inc")
        result = command(kind, executable, external, shell_args(kind, "install"), partial_legacy_home)
        check(
            f"{kind} rejects legacy promisor authority before object reads",
            result.returncode != 0 and not lazy_fetch_sentinel.exists() and (partial_legacy / ".git").exists(),
            result,
        )

        worktree_config_home = root / "worktree config legacy home"
        worktree_config_legacy = worktree_config_home / ".agents/skills/issue-flow"
        clone_legacy(remote, worktree_config_legacy, legacy)
        worktree_sentinel = root / "worktree-config-sentinel"
        git(worktree_config_legacy, "config", "extensions.worktreeConfig", "true")
        (worktree_config_legacy / ".git/config.worktree").write_text(
            "[protocol \"ext\"]\n\tallow = always\n"
            "[remote \"hidden\"]\n\tpromisor = true\n\tpartialCloneFilter = blob:none\n"
            f"\turl = ext::touch {worktree_sentinel.as_posix()}\n",
            encoding="utf-8",
        )
        result = command(
            kind,
            executable,
            external,
            shell_args(kind, "install", "--dry-run"),
            worktree_config_home,
        )
        check(
            f"{kind} rejects worktree-scoped legacy Git authority before object reads",
            result.returncode != 0
            and not worktree_sentinel.exists()
            and (worktree_config_legacy / ".git").exists(),
            result,
        )

        nested_legacy_home = root / "nested empty legacy home"
        nested_legacy = nested_legacy_home / ".agents/skills/issue-flow"
        clone_legacy(remote, nested_legacy, legacy)
        nested_empty = nested_legacy / "references/.empty-local-state"
        nested_empty.mkdir()
        result = command(kind, executable, external, shell_args(kind, "install"), nested_legacy_home)
        check(
            f"{kind} legacy migration refuses an untracked empty directory nested in tracked state",
            result.returncode != 0
            and nested_empty.exists()
            and (nested_legacy / ".git").exists(),
            result,
        )
        reserved_legacy_home = root / "reserved attachment legacy home"
        reserved_legacy = reserved_legacy_home / ".agents/skills/issue-flow"
        clone_legacy(remote, reserved_legacy, legacy)
        (reserved_legacy / ".issue-flow-bundle.json").write_bytes(b"operator collision")
        reserved_paths = Paths.for_home(reserved_legacy_home)
        result = command(kind, executable, external, shell_args(kind, "install"), reserved_legacy_home)
        check(
            f"{kind} validates reserved legacy attachment names before publishing state",
            result.returncode != 0
            and (reserved_legacy / ".git").exists()
            and not reserved_paths.attachments.exists(),
            result,
        )
        if os.name != "nt":
            linked_policy_home = root / "linked legacy policy home"
            linked_policy = linked_policy_home / ".agents/skills/issue-flow"
            clone_legacy(remote, linked_policy, legacy)
            linked_policy_target = root / "external legacy policy"
            linked_policy_target.write_bytes(LOCAL_POLICY)
            (linked_policy / "operator.local.md").unlink()
            (linked_policy / "operator.local.md").symlink_to(linked_policy_target)
            linked_policy_paths = Paths.for_home(linked_policy_home)
            result = command(kind, executable, external, shell_args(kind, "install"), linked_policy_home)
            check(
                f"{kind} refuses linked legacy operator policy before promotion",
                result.returncode != 0
                and (linked_policy / ".git").exists()
                and not linked_policy_paths.config.exists(),
                result,
            )
        if os.name == "nt":
            nested_pointer_home = root / "nested pointer legacy home"
            nested_pointer_legacy = nested_pointer_home / ".agents/skills/issue-flow"
            clone_legacy(remote, nested_pointer_legacy, legacy)
            nested_pointer_target = root / "nested pointer target"
            nested_pointer_target.mkdir()
            nested_pointer = nested_pointer_legacy / "references/.empty-local-pointer"
            create_directory_pointer(nested_pointer, nested_pointer_target)
            result = command(kind, executable, external, shell_args(kind, "install"), nested_pointer_home)
            check(
                f"{kind} legacy migration refuses an empty junction nested in tracked state",
                result.returncode != 0
                and is_pointer(nested_pointer)
                and (nested_pointer_legacy / ".git").exists(),
                result,
            )
            readonly_legacy_home = root / "readonly local legacy home"
            readonly_legacy = readonly_legacy_home / ".agents/skills/issue-flow"
            clone_legacy(remote, readonly_legacy, legacy)
            readonly_local = readonly_legacy / "readonly.local"
            readonly_local.write_bytes(b"operator state")
            os.chmod(readonly_local, stat.S_IRUSR)
            result = command(kind, executable, external, shell_args(kind, "install"), readonly_legacy_home)
            check(
                f"{kind} legacy migration rejects read-only local files before attachment publication",
                result.returncode != 0
                and readonly_local.read_bytes() == b"operator state"
                and (readonly_legacy / ".git").exists(),
                result,
            )
            os.chmod(readonly_local, stat.S_IRUSR | stat.S_IWUSR)

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
        offline_remote = root / "legacy-recovery-remote-offline.git"
        remote.rename(offline_remote)
        try:
            recovery_result = command(kind, executable, recovery_script, shell_args(kind, "recover"), legacy_recovery_home)
        finally:
            offline_remote.rename(remote)
        check(
            f"{kind} retained transaction endpoint restores legacy migration while remote is unavailable",
            recovery_result.returncode == 0
            and (legacy_recovery / ".git").exists()
            and not recovery_paths.transaction.exists()
            and not (recovery_paths.bundles / candidate).exists(),
            recovery_result,
        )
        publish(author, new)
        try:
            result = command(kind, executable, external, shell_args(kind, "install"), legacy_recovery_home)
        finally:
            publish(author, candidate)
        check(
            f"{kind} install after local recovery migrates the restored clone to the newer target",
            result.returncode == 0
            and current_revision(legacy_recovery_home) == new
            and not recovery_paths.transaction.exists()
            and not (recovery_paths.bundles / candidate).exists(),
            result,
        )

        if kind == "PowerShell":
            concurrent_policy_home = root / "concurrent legacy policy home"
            concurrent_policy_legacy = concurrent_policy_home / ".agents/skills/issue-flow"
            clone_legacy(remote, concurrent_policy_legacy, legacy)
            concurrent_paths = Paths.for_home(concurrent_policy_home)
            initialize_state(concurrent_paths)
            concurrent_target = fetch_target(concurrent_paths)
            concurrent_entries = tree_entries(concurrent_paths, concurrent_target)
            concurrent_policy = LOCAL_POLICY.replace(b"| Tracker | github |", b"| Tracker | linear |")
            policy_generation(concurrent_paths, LOCAL_POLICY)
            installer.write_bytes_atomic(concurrent_paths.config, LOCAL_POLICY)
            (concurrent_policy_legacy / "operator.local.md").write_bytes(concurrent_policy)
            migration_error: Exception | None = None
            try:
                installer.migrate_legacy(concurrent_paths, concurrent_target, concurrent_entries)
            except Exception as error:
                migration_error = error
            if migration_error is None:
                installer.finish_target(concurrent_paths, concurrent_target, keep=True)
            check(
                "shared migration retry adopts policy changed after provisional state",
                migration_error is None
                and concurrent_paths.config.read_bytes() == concurrent_policy
                and (concurrent_paths.canonical / "operator.local.md").read_bytes() == concurrent_policy,
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
            == b"legitimate local temporary name\n"
            and is_pointer(installed / ".empty-cache")
            and not any((installed / ".empty-cache").iterdir()),
            result,
        )
        collision_author = root / "collision author"
        git(root, "clone", author.as_posix(), collision_author.as_posix())
        git(collision_author, "config", "user.email", "test@example.com")
        git(collision_author, "config", "user.name", "Installer Test")
        git(collision_author, "reset", "--hard", candidate)
        (collision_author / ".empty-cache").mkdir()
        (collision_author / ".empty-cache/tracked.txt").write_text("tracked collision\n", encoding="utf-8")
        git(collision_author, "add", "-f", ".empty-cache/tracked.txt")
        git(collision_author, "commit", "-m", "collide with preserved local state")
        collision = git(collision_author, "rev-parse", "HEAD")
        git(remote, "fetch", collision_author.as_uri(), f"{collision}:refs/heads/collision")
        git(remote, "update-ref", "refs/heads/main", collision)
        try:
            dry_collision = command(
                kind,
                executable,
                installed / ("install.sh" if kind == "POSIX" else "install.ps1"),
                shell_args(kind, "sync", "--dry-run"),
                home,
            )
            result = command(
                kind,
                executable,
                installed / ("install.sh" if kind == "POSIX" else "install.ps1"),
                shell_args(kind, "sync"),
                home,
            )
        finally:
            git(remote, "update-ref", "refs/heads/main", candidate)
            git(remote, "update-ref", "-d", "refs/heads/collision")
        check(
            f"{kind} sync dry-run rejects a target collision with real preserved attachments",
            dry_collision.returncode != 0
            and current_revision(home) == candidate
            and not (home / f".agents/skills/.issue-flow/bundles/{collision}").exists(),
            dry_collision,
        )
        check(
            f"{kind} rejects a target tree that collides with preserved local state before publication",
            result.returncode != 0
            and current_revision(home) == candidate
            and not (home / f".agents/skills/.issue-flow/bundles/{collision}").exists()
            and run(
                [
                    "git",
                    f"--git-dir={home / '.agents/skills/.issue-flow/repository.git'}",
                    "rev-parse",
                    "--verify",
                    f"refs/issue-flow/bundles/{collision}",
                ]
            ).returncode
            != 0,
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

        fixture_paths = Paths.for_home(home)
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
            f"{kind} updates stable policy through an immutable bundle generation",
            result.returncode == 0
            and b"| Tracker | linear | github |" in configured_policy
            and configured_policy.endswith(b"\r\n")
            and len(policy_files) == 1
            and policy_files[0].read_bytes() == configured_policy
            and (home / ".agents/skills/.issue-flow/operator.local.md").stat().st_ino != policy_inode
            and not os.path.samefile(installed / "operator.local.md", fixture_paths.config)
            and fixture_paths.config.stat().st_nlink == 1
            and os.path.samefile(installed / "operator.local.md", policy_files[0])
            and (installed / (".cache." + "b" * 32 + ".tmp")).read_bytes()
            == b"legitimate local temporary name\n",
            result,
        )

        manual_policy = configured_policy + b"\r\nLocal-only instruction: preserve this exact text.\r\n"
        visible_policy = installed / "operator.local.md"
        if os.name != "nt":
            visible_policy.unlink()
        visible_policy.write_bytes(manual_policy)
        result = command(
            kind,
            executable,
            installed / ("install.sh" if kind == "POSIX" else "install.ps1"),
            shell_args(kind, "config"),
            home,
        )
        configured_policy = (installed / "operator.local.md").read_bytes()
        policy_files = list(fixture_paths.policies.iterdir())
        check(
            f"{kind} config adopts arbitrary manual local-policy instructions",
            result.returncode == 0
            and configured_policy == manual_policy
            and fixture_paths.config.read_bytes() == manual_policy
            and fixture_paths.config.stat().st_nlink == 1
            and len(policy_files) == 1
            and os.path.samefile(installed / "operator.local.md", policy_files[0])
            and not os.path.samefile(installed / "operator.local.md", fixture_paths.config),
            result,
        )

        if os.name == "nt":
            utf16_policy_text = manual_policy.decode("utf-8") + "PowerShell 5.1 instruction: preserve this too.\r\n"
            visible_policy.write_bytes(utf16_policy_text.encode("utf-16"))
            original_complete_policy_switch = installer.complete_policy_switch

            def interrupt_utf16_policy_switch(_paths: Paths, _generation: Path) -> None:
                raise InstallError("injected UTF-16 policy publication interruption")

            installer.complete_policy_switch = interrupt_utf16_policy_switch
            try:
                try:
                    installer.configure(fixture_paths, None, False)
                except InstallError:
                    utf16_interrupted = True
                else:
                    utf16_interrupted = False
            finally:
                installer.complete_policy_switch = original_complete_policy_switch
            installer.recover_policy_transaction(fixture_paths)
            configured_policy = visible_policy.read_bytes()
            manual_policy = utf16_policy_text.encode("utf-8")
            policy_files = list(fixture_paths.policies.iterdir())
            check(
                f"{kind} config normalizes a PowerShell 5.1 UTF-16 policy edit",
                utf16_interrupted
                and configured_policy == manual_policy
                and fixture_paths.config.read_bytes() == manual_policy
                and len(policy_files) == 1
                and os.path.samefile(visible_policy, policy_files[0]),
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
        if os.name == "nt":
            (installed / "operator.local.md").write_bytes(interrupted_policy)
        else:
            fixture_paths.config.write_bytes(interrupted_policy)
        interrupted_generation = policy_generation(fixture_paths, interrupted_policy)
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
        interrupted_temporary = installed / (".operator.local.md." + "c" * 32 + ".tmp")
        os.link(interrupted_generation, interrupted_temporary)
        result = command(
            kind,
            executable,
            installed / ("install.sh" if kind == "POSIX" else "install.ps1"),
            shell_args(kind, "recover", "--dry-run"),
            home,
        )
        check(
            f"{kind} policy recovery dry-run accepts its owned replacement temporary",
            result.returncode == 0
            and interrupted_temporary.exists()
            and fixture_paths.policy_transaction.exists(),
            result,
        )
        interrupted_temporary.unlink()
        if os.name == "nt":
            installer.complete_policy_switch(fixture_paths, interrupted_generation)
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
            and not os.path.samefile(installed / "operator.local.md", fixture_paths.config)
            and os.path.samefile(installed / "operator.local.md", interrupted_generation),
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
            installer.complete_policy_switch(fixture_paths, policy_generation(fixture_paths, configured_policy))
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
        policy_before_runtime_failure = fixture_paths.config.read_bytes()
        result = command(
            kind,
            executable,
            installed / ("install.sh" if kind == "POSIX" else "install.ps1"),
            shell_args(kind, "config", "--set", "Tracker=github", "--dry-run"),
            home,
        )
        check(
            f"{kind} config dry-run refuses a runtime pointer that bypasses canonical",
            result.returncode != 0 and fixture_paths.config.read_bytes() == policy_before_runtime_failure,
            result,
        )
        result = command(
            kind,
            executable,
            installed / ("install.sh" if kind == "POSIX" else "install.ps1"),
            shell_args(kind, "config", "--set", "Tracker=github"),
            home,
        )
        check(
            f"{kind} config mutation refuses a runtime pointer that bypasses canonical",
            result.returncode != 0 and fixture_paths.config.read_bytes() == policy_before_runtime_failure,
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
        previous_policy = configured_policy
        result = command(
            kind,
            executable,
            active_script,
            shell_args(kind, "config", "--set", "New setting=custom"),
            home,
        )
        configured_policy = (installed / "operator.local.md").read_bytes()
        previous_tracker_row = next(line for line in previous_policy.splitlines() if line.startswith(b"| Tracker |"))
        check(
            f"{kind} config merges a newly shipped setting while preserving existing values",
            result.returncode == 0
            and b"| New setting | custom | default |" in configured_policy
            and previous_tracker_row in configured_policy.splitlines(),
            result,
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
        git_trace = root / "hostile-git-trace"
        git_trace2 = root / "hostile-git-trace2"
        result = command(
            kind,
            executable,
            active_script,
            shell_args(kind, "status"),
            home,
            {"GIT_TRACE": str(git_trace), "GIT_TRACE2_EVENT": str(git_trace2)},
        )
        check(
            f"{kind} sanitizes Git tracing before its first version probe",
            result.returncode == 0 and not git_trace.exists() and not git_trace2.exists(),
            result,
        )
        git_reselection_sentinel = root / "git-reselection-sentinel"
        planted_git_cmd = fixture_paths.skills / "git.cmd"
        planted_git_cmd.write_text(f'@echo attacked > "{git_reselection_sentinel}"\n', encoding="utf-8")
        planted_git_shell = fixture_paths.skills / "git"
        planted_git_shell.write_text(
            f"#!/bin/sh\nprintf attacked > '{git_reselection_sentinel.as_posix()}'\n",
            encoding="utf-8",
        )
        os.chmod(planted_git_shell, 0o755)
        result = command(
            kind,
            executable,
            active_script,
            shell_args(kind, "status"),
            home,
            {"PATH": "." + os.pathsep + os.environ["PATH"]},
        )
        check(
            f"{kind} helper keeps the wrapper-selected Git after changing directory",
            result.returncode == 0 and not git_reselection_sentinel.exists(),
            result,
        )
        planted_git_cmd.unlink()
        planted_git_shell.unlink()

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
        symbolic_probe = "refs/issue-flow/symbolic-probe"
        git(
            state_root,
            f"--git-dir={object_store}",
            "symbolic-ref",
            symbolic_probe,
            f"refs/issue-flow/activated/{new}",
        )
        try:
            installer.validate_repository_config(object_store)
        except InstallError:
            shared_symbolic_rejected = True
        else:
            shared_symbolic_rejected = False
        result = command(kind, executable, active_script, shell_args(kind, "status"), home)
        check(
            f"{kind} wrapper and shared installer reject symbolic authority refs",
            shared_symbolic_rejected and result.returncode != 0 and current_revision(home) == new,
            result,
        )
        git(
            state_root,
            f"--git-dir={object_store}",
            "update-ref",
            "--no-deref",
            "-d",
            symbolic_probe,
        )
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
        activation_reference = f"refs/issue-flow/activated/{new}"
        git(
            state_root,
            f"--git-dir={object_store}",
            "update-ref",
            "--no-deref",
            activation_reference,
            candidate,
            new,
        )
        result = command(kind, executable, retained_script, shell_args(kind, "recover", "--dry-run"), home)
        check(
            f"{kind} recovery dry-run rejects a conflicting post-switch activation ref",
            result.returncode != 0 and (state_root / "transaction.json").exists(),
            result,
        )
        git(
            state_root,
            f"--git-dir={object_store}",
            "update-ref",
            "--no-deref",
            activation_reference,
            new,
            candidate,
        )
        (state_root / "transaction.json").unlink()

        state_before_missing_canonical = (state_root / "current.json").read_bytes()
        remove_directory_pointer(fixture_paths.canonical)
        result = command(kind, executable, retained_script, shell_args(kind, "recover"), home)
        check(
            f"{kind} recovery refuses lost canonical provenance without a journal",
            result.returncode != 0
            and not os.path.lexists(fixture_paths.canonical)
            and (state_root / "current.json").read_bytes() == state_before_missing_canonical,
            result,
        )
        result = command(kind, executable, retained_script, shell_args(kind, "sync"), home)
        check(
            f"{kind} sync refuses to replace lost canonical provenance as a fresh install",
            result.returncode != 0
            and not os.path.lexists(fixture_paths.canonical)
            and (state_root / "current.json").read_bytes() == state_before_missing_canonical,
            result,
        )
        create_directory_pointer(fixture_paths.canonical, state_root / "bundles" / new)

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
        result = command(
            kind,
            executable,
            active_script,
            shell_args(kind, "config", "--set", "Tracker=github", "--dry-run"),
            home,
        )
        check(
            f"{kind} config dry-run rejects a corrupt retained policy destination",
            result.returncode != 0,
            result,
        )
        inactive_policy.rmdir()
        replace_hardlink(inactive_policy, policy_generation(fixture_paths, fixture_paths.config.read_bytes()))

        attachments_bytes = fixture_paths.attachments.read_bytes()
        fixture_paths.attachments.write_bytes(b"not json")
        result = command(kind, executable, active_script, shell_args(kind, "status"), home)
        check(
            f"{kind} status reports storage before failing on corrupt attachment metadata",
            result.returncode != 0 and "store" in result.stdout and "Traceback" not in result.stderr,
            result,
        )
        fixture_paths.attachments.write_bytes(attachments_bytes)

        fixture_paths.transaction.write_text(json.dumps({"schema": 999, "phase": "prepared"}), encoding="utf-8")
        result = command(kind, executable, active_script, shell_args(kind, "recover", "--dry-run"), home)
        check(
            f"{kind} recovery dry-run rejects a malformed activation journal",
            result.returncode != 0 and fixture_paths.transaction.exists(),
            result,
        )
        fixture_paths.transaction.unlink()

        shaped_id = "f" * 32
        unowned_activation_file = fixture_paths.skills / f".issue-flow.activate-{shaped_id}"
        unowned_activation_file.write_bytes(b"operator data")
        dry_result = command(kind, executable, active_script, shell_args(kind, "recover", "--dry-run"), home)
        result = command(kind, executable, active_script, shell_args(kind, "recover"), home)
        check(
            f"{kind} cleanup refuses an installer-shaped regular file it does not own",
            dry_result.returncode != 0
            and result.returncode != 0
            and unowned_activation_file.read_bytes() == b"operator data",
            result,
        )
        unowned_activation_file.unlink()
        unowned_runtime_directory = home / f".claude/skills/.issue-flow.runtime-{shaped_id}"
        unowned_runtime_directory.mkdir()
        (unowned_runtime_directory / "operator-data").write_bytes(b"keep")
        result = command(kind, executable, active_script, shell_args(kind, "uninstall"), home)
        check(
            f"{kind} uninstall refuses a non-empty installer-shaped directory it does not own",
            result.returncode != 0 and (unowned_runtime_directory / "operator-data").read_bytes() == b"keep",
            result,
        )
        (unowned_runtime_directory / "operator-data").unlink()
        unowned_runtime_directory.rmdir()
        foreign_activation_target = root / "foreign activation target"
        foreign_activation_target.mkdir()
        foreign_activation_pointer = fixture_paths.skills / f".issue-flow.activate-{shaped_id}"
        create_directory_pointer(foreign_activation_pointer, foreign_activation_target)
        result = command(kind, executable, active_script, shell_args(kind, "recover"), home)
        check(
            f"{kind} cleanup refuses an installer-shaped pointer to foreign state",
            result.returncode != 0 and is_pointer(foreign_activation_pointer),
            result,
        )
        remove_directory_pointer(foreign_activation_pointer)
        claude_root = home / ".claude"
        saved_claude_root = home / ".claude-safe"
        owned_runtime = claude_root / "skills/issue-flow"
        remove_directory_pointer(owned_runtime)
        claude_root.rename(saved_claude_root)
        external_linked_runtime = root / "external linked runtime"
        (external_linked_runtime / "skills").mkdir(parents=True)
        external_runtime_temporary = external_linked_runtime / f"skills/.issue-flow.runtime-{shaped_id}"
        create_directory_pointer(external_runtime_temporary, fixture_paths.canonical)
        create_directory_pointer(claude_root, external_linked_runtime)
        recover_result = command(kind, executable, active_script, shell_args(kind, "recover"), home)
        uninstall_result = command(kind, executable, active_script, shell_args(kind, "uninstall"), home)
        check(
            f"{kind} recovery validates linked runtime ancestors before external cleanup",
            recover_result.returncode != 0 and is_pointer(external_runtime_temporary),
            recover_result,
        )
        check(
            f"{kind} uninstall validates linked runtime ancestors before external cleanup",
            uninstall_result.returncode != 0 and is_pointer(external_runtime_temporary),
            uninstall_result,
        )
        remove_directory_pointer(claude_root)
        remove_directory_pointer(external_runtime_temporary)
        saved_claude_root.rename(claude_root)
        create_directory_pointer(owned_runtime, fixture_paths.canonical)

        lock_path = fixture_paths.lock
        state_root = fixture_paths.state
        lock_path.unlink()
        lock_victim = root / "lock-victim"
        lock_victim.write_bytes(b"must-survive")
        os.link(lock_victim, lock_path)
        dry_result = command(kind, executable, active_script, shell_args(kind, "recover", "--dry-run"), home)
        result = command(kind, executable, active_script, shell_args(kind, "recover"), home)
        check(
            f"{kind} lock refuses an external hard link without truncating it",
            dry_result.returncode != 0
            and result.returncode != 0
            and lock_victim.read_bytes() == b"must-survive",
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
            locked_dry = command(kind, executable, active_script, shell_args(kind, "recover", "--dry-run"), home)
        check(
            f"{kind} live and dry recovery cannot steal a live operating-system lock",
            locked.returncode != 0 and locked_dry.returncode != 0,
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
        os.link(installer.current_policy_generation(fixture_paths), abandoned_bundle_link)
        abandoned_attachment_link = installed / f".custom-hook.sh.{temporary_id}.tmp"
        os.link(fixture_paths.local / "custom-hook.sh", abandoned_attachment_link)
        abandoned_policy = fixture_paths.policies / f".policy.{temporary_id}.tmp"
        abandoned_policy.write_bytes(b"partial")
        abandoned_object = object_store / "objects/pack" / f".object.{temporary_id}.tmp"
        abandoned_object.write_bytes(b"partial")
        abandoned_ref_locks = [
            object_store / "refs/issue-flow/incoming.lock",
            object_store / f"refs/issue-flow/bundles/{new}.lock",
            object_store / f"refs/issue-flow/activated/{new}.lock",
        ]
        for ref_lock in abandoned_ref_locks:
            ref_lock.parent.mkdir(parents=True, exist_ok=True)
            ref_lock.write_bytes(b"partial ref update")
        dry_cleanup = command(kind, executable, active_script, shell_args(kind, "recover", "--dry-run"), home)
        dry_cleanup_preserved = (
            dry_cleanup.returncode == 0
            and abandoned_bundle_link.exists()
            and abandoned_attachment_link.exists()
        )
        result = command(kind, executable, active_script, shell_args(kind, "recover"), home)
        check(
            f"{kind} recovers a post-switch receipt write after lock release",
            dry_cleanup_preserved
            and result.returncode == 0
            and not (state_root / "transaction.json").exists()
            and not abandoned_repository.exists()
            and not abandoned_json.exists()
            and not os.path.lexists(abandoned_activation)
            and not abandoned_bundle_link.exists()
            and not abandoned_attachment_link.exists()
            and not abandoned_policy.exists()
            and not abandoned_object.exists()
            and not any(ref_lock.exists() for ref_lock in abandoned_ref_locks)
            and current_revision(home) == new
            and json.loads((state_root / "current.json").read_text(encoding="utf-8"))["previous"] == candidate,
            dry_cleanup if not dry_cleanup_preserved else result,
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

        common_directory = object_store / "commondir"
        common_directory.write_text("../external-common\n", encoding="utf-8")
        result = command(kind, executable, active_script, shell_args(kind, "status"), home)
        check(
            f"{kind} rejects a bare repository common-directory redirect",
            result.returncode != 0 and current_revision(home) == new,
            result,
        )
        common_directory.unlink()
        repository_refs = object_store / "refs"
        safe_repository_refs = object_store / "refs-safe"
        external_repository_refs = root / "external repository refs"
        external_repository_refs.mkdir()
        repository_refs.rename(safe_repository_refs)
        create_directory_pointer(repository_refs, external_repository_refs)
        result = command(kind, executable, active_script, shell_args(kind, "status"), home)
        check(
            f"{kind} rejects linked bare-repository refs",
            result.returncode != 0 and current_revision(home) == new,
            result,
        )
        remove_directory_pointer(repository_refs)
        safe_repository_refs.rename(repository_refs)

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
print(f"{CHECKS - len(FAILURES)}/{CHECKS} checks passed" + (f"; failures: {FAILURES}" if FAILURES else ""))
raise SystemExit(1 if FAILURES else 0)
