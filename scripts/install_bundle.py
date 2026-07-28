#!/usr/bin/env python3
"""Install and activate complete issue-flow Git trees as immutable bundles."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


REPOSITORY_URL = "https://github.com/asanabrial/issue-flow.git"
SKILL_NAME = "issue-flow"
CONFIG_FILE = "operator.local.md"
RECEIPT_FILE = ".issue-flow-bundle.json"
STATE_SCHEMA = 1
REQUIRED_ENTRYPOINTS = frozenset(
    {"SKILL.md", "install.sh", "install.ps1", "scripts/install_bundle.py"}
)
CONFIG_START = "<!-- issue-flow:config:start -->"
CONFIG_END = "<!-- issue-flow:config:end -->"
LOCAL_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


class InstallError(RuntimeError):
    """A fail-closed installer error with operator-safe recovery text."""


@dataclass(frozen=True)
class Paths:
    skills: Path
    canonical: Path
    state: Path
    repository: Path
    bundles: Path
    legacy: Path
    local: Path
    config: Path
    current: Path
    attachments: Path
    transaction: Path
    lock: Path
    hooks: Path
    template: Path

    @classmethod
    def for_home(cls, home: Path) -> "Paths":
        skills = home / ".agents" / "skills"
        state = skills / ".issue-flow"
        return cls(
            skills=skills,
            canonical=skills / SKILL_NAME,
            state=state,
            repository=state / "repository.git",
            bundles=state / "bundles",
            legacy=state / "legacy",
            local=state / "local",
            config=state / CONFIG_FILE,
            current=state / "current.json",
            attachments=state / "attachments.json",
            transaction=state / "transaction.json",
            lock=state / "sync.lock",
            hooks=state / "empty-hooks",
            template=state / "empty-template",
        )


def fail(message: str) -> None:
    raise InstallError(message)


def path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def is_junction(path: Path) -> bool:
    checker = getattr(os.path, "isjunction", None)
    if checker:
        return bool(checker(path))
    if os.name != "nt" or not path_exists(path):
        return False
    attributes = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    return attributes != 0xFFFFFFFF and bool(attributes & 0x400)


def is_pointer(path: Path) -> bool:
    return path.is_symlink() or is_junction(path)


def ensure_real_directory(path: Path) -> None:
    if path_exists(path):
        if is_pointer(path) or not path.is_dir():
            fail(f"{path} must be a real directory, not a link or file")
        return
    path.mkdir(parents=True)


def fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_bytes(path: Path, content: bytes, exclusive: bool = False) -> None:
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    write_bytes(temporary, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"), exclusive=True)
    os.replace(temporary, path)
    fsync_directory(path.parent)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        fail(f"cannot read trusted installer state {path}: {error}")
    if not isinstance(value, dict):
        fail(f"trusted installer state {path} is not an object")
    return value


class InstallerLock:
    def __init__(self, paths: Paths) -> None:
        self.paths = paths

    def __enter__(self) -> "InstallerLock":
        try:
            self.paths.lock.mkdir()
        except FileExistsError:
            fail(
                f"another installer owns {self.paths.lock}; if it crashed, run 'recover' "
                "after confirming no install process remains"
            )
        write_json(
            self.paths.lock / "owner.json",
            {"pid": os.getpid(), "started": int(time.time()), "schema": STATE_SCHEMA},
        )
        return self

    def __exit__(self, *_: object) -> None:
        shutil.rmtree(self.paths.lock, ignore_errors=True)


def clean_git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("GIT_"):
            environment.pop(name, None)
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "",
            "GIT_CONFIG_COUNT": "0",
        }
    )
    return environment


def git(
    paths: Paths,
    *arguments: str,
    cwd: Path | None = None,
    repository: Path | None = None,
    text: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess:
    executable = shutil.which("git")
    if not executable:
        fail("git is required; install it and retry")
    command = [
        executable,
        "-c",
        f"core.hooksPath={paths.hooks}",
        "-c",
        "core.fsmonitor=false",
    ]
    if repository:
        command.extend([f"--git-dir={repository}"])
    command.extend(arguments)
    result = subprocess.run(
        command,
        cwd=cwd,
        env=clean_git_environment(),
        capture_output=True,
        text=text,
        encoding="utf-8" if text else None,
        errors="replace" if text else None,
    )
    if check and result.returncode:
        stderr = result.stderr.strip() if text else result.stderr.decode("utf-8", "replace").strip()
        fail(f"git {' '.join(arguments)} failed ({result.returncode}): {stderr}")
    return result


def initialize_state(paths: Paths) -> None:
    ensure_real_directory(paths.skills)
    ensure_real_directory(paths.state)
    for directory in (
        paths.bundles,
        paths.legacy,
        paths.local,
        paths.hooks,
        paths.template,
    ):
        ensure_real_directory(directory)
    if not paths.repository.exists():
        git(paths, "init", "--bare", f"--template={paths.template}", str(paths.repository))
    elif is_pointer(paths.repository) or not paths.repository.is_dir():
        fail(f"{paths.repository} is not the installer-owned bare repository")
    bare = git(paths, "rev-parse", "--is-bare-repository", repository=paths.repository).stdout.strip()
    if bare != "true":
        fail(f"{paths.repository} is not a bare Git repository")


def fetch_target(paths: Paths) -> str:
    temporary_ref = "refs/issue-flow/incoming"
    allow_file = "always" if urllib.parse.urlparse(REPOSITORY_URL).scheme == "file" else "never"
    git(
        paths,
        "-c",
        f"protocol.file.allow={allow_file}",
        "fetch",
        "--force",
        "--no-tags",
        REPOSITORY_URL,
        f"+refs/heads/main:{temporary_ref}",
        repository=paths.repository,
    )
    return git(
        paths,
        "rev-parse",
        f"{temporary_ref}^{{commit}}",
        repository=paths.repository,
    ).stdout.strip()


def finish_target(paths: Paths, target: str, keep: bool) -> None:
    if keep:
        git(
            paths,
            "update-ref",
            f"refs/issue-flow/bundles/{target}",
            target,
            repository=paths.repository,
        )
    git(
        paths,
        "update-ref",
        "-d",
        "refs/issue-flow/incoming",
        repository=paths.repository,
        check=False,
    )


def tree_entries(
    paths: Paths,
    target: str,
    require_entrypoints: bool = True,
) -> list[dict[str, str | int]]:
    result = git(
        paths,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        target,
        repository=paths.repository,
        text=False,
    )
    entries: list[dict[str, str | int]] = []
    casefolded: dict[str, str] = {}
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split(" ", 2)
        try:
            name = raw_path.decode("utf-8")
        except UnicodeDecodeError:
            fail("the target tree contains a path that is not portable UTF-8")
        pure = PurePosixPath(name)
        if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
            fail(f"the target tree contains an unsafe path: {name!r}")
        if any(part.casefold() == ".git" for part in pure.parts):
            fail(f"the target tree attempts to materialize Git metadata: {name!r}")
        folded = name.casefold()
        if folded in casefolded and casefolded[folded] != name:
            fail(f"the target tree has a case-folded collision: {casefolded[folded]!r} and {name!r}")
        casefolded[folded] = name
        if kind != "blob" or mode not in ("100644", "100755"):
            fail(f"the runtime bundle permits only regular files, got {mode} {kind} at {name!r}")
        if pure.parts[0].casefold() in (CONFIG_FILE.casefold(), RECEIPT_FILE.casefold()):
            fail(f"the target tree must not version installer-local state: {name!r}")
        entries.append({"path": name, "mode": int(mode, 8), "object": object_id})
    names = {str(entry["path"]) for entry in entries}
    missing = sorted(REQUIRED_ENTRYPOINTS - names)
    if require_entrypoints and missing:
        fail(f"the target tree is missing installer entrypoints: {', '.join(missing)}")
    ignore_entry = next((entry for entry in entries if entry["path"] == ".gitignore"), None)
    if not ignore_entry:
        fail("the target tree must contain .gitignore")
    ignore_text = git(
        paths,
        "cat-file",
        "blob",
        str(ignore_entry["object"]),
        repository=paths.repository,
        text=False,
    ).stdout.decode("utf-8", "strict")
    lines = [line.strip() for line in ignore_text.splitlines()]
    try:
        policy_line = lines.index(CONFIG_FILE)
    except ValueError:
        fail(f"the target .gitignore must ignore {CONFIG_FILE} explicitly")
    if any(line.startswith("!") for line in lines[policy_line + 1 :]):
        fail(f"the target .gitignore may not negate local policy after {CONFIG_FILE}")
    return entries


def validate_markdown_links(bundle: Path, tracked: set[str]) -> None:
    for name in sorted(path for path in tracked if path.lower().endswith(".md")):
        text = (bundle / Path(*PurePosixPath(name).parts)).read_text(encoding="utf-8")
        for match in LOCAL_LINK.finditer(text):
            destination = match.group(1).strip().strip("<>").split(maxsplit=1)[0]
            if not destination or destination.startswith("#"):
                continue
            parsed = urllib.parse.urlparse(destination)
            if parsed.scheme or parsed.netloc:
                continue
            relative = urllib.parse.unquote(parsed.path)
            if not relative:
                continue
            resolved = PurePosixPath(name).parent.joinpath(relative)
            normalized: list[str] = []
            for part in resolved.parts:
                if part == ".":
                    continue
                if part == "..":
                    if not normalized:
                        fail(f"local Markdown link escapes the bundle in {name}: {destination}")
                    normalized.pop()
                else:
                    normalized.append(part)
            target = "/".join(normalized)
            if target not in tracked:
                fail(f"local Markdown link in {name} has no file in the same Git tree: {destination}")


def materialize_bundle(paths: Paths, target: str, require_entrypoints: bool = True) -> Path:
    final = paths.bundles / target
    entries = tree_entries(paths, target, require_entrypoints=require_entrypoints)
    tree = git(paths, "rev-parse", f"{target}^{{tree}}", repository=paths.repository).stdout.strip()
    if final.exists():
        verify_bundle_against_git(paths, final, target, tree, entries)
        return final
    staging = paths.bundles / f".staging-{target}-{uuid.uuid4().hex}"
    staging.mkdir()
    receipt_files: dict[str, dict[str, str | int]] = {}
    try:
        for entry in entries:
            name = str(entry["path"])
            destination = staging.joinpath(*PurePosixPath(name).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            content = git(
                paths,
                "cat-file",
                "blob",
                str(entry["object"]),
                repository=paths.repository,
                text=False,
            ).stdout
            write_bytes(destination, content, exclusive=True)
            mode = int(entry["mode"])
            os.chmod(destination, 0o755 if mode & stat.S_IXUSR else 0o644)
            receipt_files[name] = {
                "mode": f"{mode:o}",
                "object": str(entry["object"]),
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        validate_markdown_links(staging, set(receipt_files))
        write_json(
            staging / RECEIPT_FILE,
            {
                "schema": STATE_SCHEMA,
                "repository": REPOSITORY_URL,
                "commit": target,
                "tree": tree,
                "files": receipt_files,
            },
        )
        fsync_directory(staging)
        os.replace(staging, final)
        fsync_directory(paths.bundles)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    verify_bundle_against_git(paths, final, target, tree, entries)
    return final


def attachment_records(paths: Paths) -> list[dict[str, str]]:
    if not paths.attachments.exists():
        return []
    document = read_json(paths.attachments)
    if document.get("schema") != STATE_SCHEMA or not isinstance(document.get("entries"), list):
        fail(f"invalid attachment state in {paths.attachments}")
    records: list[dict[str, str]] = []
    for value in document["entries"]:
        if not isinstance(value, dict) or set(value) != {"kind", "name", "target"}:
            fail(f"invalid attachment record in {paths.attachments}")
        record = {key: str(value[key]) for key in ("kind", "name", "target")}
        name = record["name"]
        if Path(name).name != name or name in ("", ".", "..", CONFIG_FILE, RECEIPT_FILE):
            fail(f"unsafe attachment name in {paths.attachments}: {name!r}")
        if record["kind"] not in ("directory", "file") or not Path(record["target"]).is_absolute():
            fail(f"unsafe attachment target in {paths.attachments}: {record!r}")
        records.append(record)
    return records


def verify_bundle(paths: Paths, bundle: Path, expected_commit: str | None = None) -> dict:
    if is_pointer(bundle) or not bundle.is_dir():
        fail(f"bundle is not a real directory: {bundle}")
    receipt = read_json(bundle / RECEIPT_FILE)
    if receipt.get("schema") != STATE_SCHEMA or receipt.get("repository") != REPOSITORY_URL:
        fail(f"bundle receipt has the wrong schema or repository: {bundle}")
    if expected_commit and receipt.get("commit") != expected_commit:
        fail(f"bundle {bundle} claims commit {receipt.get('commit')}, expected {expected_commit}")
    files = receipt.get("files")
    if not isinstance(files, dict):
        fail(f"bundle receipt has no file map: {bundle}")
    expected = set(files)
    allowed_top = {CONFIG_FILE, RECEIPT_FILE, *(record["name"] for record in attachment_records(paths))}
    actual: set[str] = set()
    stack: list[tuple[Path, PurePosixPath]] = [(bundle, PurePosixPath())]
    while stack:
        directory, prefix = stack.pop()
        for item in os.scandir(directory):
            relative = prefix / item.name
            name = relative.as_posix()
            if not prefix.parts and item.name in allowed_top:
                continue
            if item.is_symlink() or (os.name == "nt" and is_junction(Path(item.path))):
                fail(f"unexpected link inside immutable bundle: {name}")
            if item.is_dir(follow_symlinks=False):
                stack.append((Path(item.path), relative))
            elif item.is_file(follow_symlinks=False):
                actual.add(name)
            else:
                fail(f"unexpected special file inside immutable bundle: {name}")
    if actual != expected:
        fail(f"bundle file boundary drifted: missing={sorted(expected - actual)}, extra={sorted(actual - expected)}")
    for name, metadata in files.items():
        path = bundle.joinpath(*PurePosixPath(name).parts)
        if path.is_symlink() or not path.is_file():
            fail(f"tracked bundle file is not regular: {name}")
        content = path.read_bytes()
        if len(content) != metadata.get("size") or hashlib.sha256(content).hexdigest() != metadata.get("sha256"):
            fail(f"tracked bundle bytes drifted: {name}")
    return receipt


def verify_bundle_against_git(
    paths: Paths,
    bundle: Path,
    target: str,
    tree: str,
    entries: list[dict[str, str | int]],
) -> dict:
    """Bind a local receipt to authoritative objects, not to hashes it chose itself."""
    receipt = verify_bundle(paths, bundle, target)
    if receipt.get("tree") != tree:
        fail(f"bundle receipt tree does not match commit {target}")
    files = receipt["files"]
    expected_names = {str(entry["path"]) for entry in entries}
    if set(files) != expected_names:
        fail(f"bundle receipt inventory does not match Git tree {tree}")
    for entry in entries:
        name = str(entry["path"])
        metadata = files[name]
        if metadata.get("object") != entry["object"] or metadata.get("mode") != f"{int(entry['mode']):o}":
            fail(f"bundle receipt metadata does not match Git tree for {name}")
        blob = git(
            paths,
            "cat-file",
            "blob",
            str(entry["object"]),
            repository=paths.repository,
            text=False,
        ).stdout
        if metadata.get("size") != len(blob) or metadata.get("sha256") != hashlib.sha256(blob).hexdigest():
            fail(f"bundle receipt hashes do not match Git object for {name}")
    return receipt


def same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve(strict=True) == right.resolve(strict=True)
    except OSError:
        return False


def windows_junction_buffer(target: Path) -> bytes:
    absolute = str(target.resolve(strict=False))
    substitute = ("\\??\\UNC\\" + absolute[2:]) if absolute.startswith("\\\\") else ("\\??\\" + absolute)
    substitute_bytes = substitute.encode("utf-16-le")
    print_bytes = absolute.encode("utf-16-le")
    names = substitute_bytes + b"\x00\x00" + print_bytes + b"\x00\x00"
    return struct.pack(
        "<IHHHHHH",
        0xA0000003,
        8 + len(names),
        0,
        0,
        len(substitute_bytes),
        len(substitute_bytes) + 2,
        len(print_bytes),
    ) + names


def set_windows_junction(path: Path, target: Path, create: bool) -> None:
    if create:
        path.mkdir()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        str(path),
        0x40000000,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x00200000 | 0x02000000,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        error = ctypes.get_last_error()
        if create:
            path.rmdir()
        fail(f"cannot open junction {path} for activation (Windows error {error})")
    buffer = windows_junction_buffer(target)
    returned = ctypes.c_uint32()
    try:
        ok = kernel32.DeviceIoControl(
            handle,
            0x000900A4,
            buffer,
            len(buffer),
            None,
            0,
            ctypes.byref(returned),
            None,
        )
        if not ok:
            fail(f"cannot activate junction {path} (Windows error {ctypes.get_last_error()})")
    finally:
        kernel32.CloseHandle(handle)


def create_directory_pointer(path: Path, target: Path) -> None:
    if os.name == "nt":
        set_windows_junction(path, target, create=True)
    else:
        os.symlink(target, path, target_is_directory=True)


def activate(paths: Paths, bundle: Path) -> None:
    verify_bundle(paths, bundle)
    if not path_exists(paths.canonical):
        temporary = paths.skills / f".{SKILL_NAME}.activate-{uuid.uuid4().hex}"
        create_directory_pointer(temporary, bundle)
        os.replace(temporary, paths.canonical)
    elif os.name == "nt":
        if not is_junction(paths.canonical):
            fail(f"the active Windows skill is not an installer-owned junction: {paths.canonical}")
        set_windows_junction(paths.canonical, bundle, create=False)
    else:
        if not paths.canonical.is_symlink():
            fail(f"the active POSIX skill is not an installer-owned symlink: {paths.canonical}")
        temporary = paths.skills / f".{SKILL_NAME}.activate-{uuid.uuid4().hex}"
        os.symlink(bundle, temporary, target_is_directory=True)
        os.replace(temporary, paths.canonical)
    if not same_path(paths.canonical, bundle):
        fail(f"activation read-back did not resolve {paths.canonical} to {bundle}")
    fsync_directory(paths.skills)


def active_bundle(paths: Paths) -> tuple[Path, dict]:
    if not is_pointer(paths.canonical):
        fail(f"{paths.canonical} is not an immutable-bundle pointer")
    try:
        bundle = paths.canonical.resolve(strict=True)
    except OSError as error:
        fail(f"active skill pointer is broken: {error}")
    if bundle.parent.resolve() != paths.bundles.resolve():
        fail(f"active skill points outside the installer bundle store: {bundle}")
    entries = tree_entries(paths, bundle.name, require_entrypoints=False)
    tree = git(paths, "rev-parse", f"{bundle.name}^{{tree}}", repository=paths.repository).stdout.strip()
    receipt = verify_bundle_against_git(paths, bundle, bundle.name, tree, entries)
    verify_attachments(paths, bundle)
    return bundle, receipt


def create_attachment(path: Path, record: dict[str, str]) -> None:
    target = Path(record["target"])
    if record["kind"] == "directory":
        create_directory_pointer(path, target)
    elif record["kind"] == "file":
        os.link(target, path)
    else:
        fail(f"unknown local attachment kind: {record['kind']}")


def remove_directory_pointer(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    else:
        path.rmdir()


def attach_local(paths: Paths, bundle: Path, allow_dangling: bool = False) -> None:
    if paths.config.exists():
        destination = bundle / CONFIG_FILE
        if not destination.exists():
            os.link(paths.config, destination)
        elif not os.path.samefile(paths.config, destination):
            fail(f"bundle policy is not linked to stable local state: {destination}")
    for record in attachment_records(paths):
        destination = bundle / record["name"]
        target = Path(record["target"])
        if (
            record["kind"] == "directory"
            and path_exists(destination)
            and allow_dangling
            and not target.exists()
        ):
            if not is_pointer(destination):
                fail(f"local directory attachment is not a link: {destination}")
            # A prepared legacy move can be retried with a new backup name. No reader can use a
            # dangling attachment, so replacing it here cannot expose a partial runtime.
            remove_directory_pointer(destination)
        if not path_exists(destination):
            create_attachment(destination, record)
        if record["kind"] == "directory":
            if not is_pointer(destination):
                fail(f"local directory attachment is not a link: {destination}")
            if target.exists() and not same_path(destination, target):
                fail(f"local directory attachment targets the wrong path: {destination}")
        elif not destination.exists() or not os.path.samefile(destination, target):
            fail(f"local file attachment targets the wrong path: {destination}")
        if not allow_dangling and not target.exists():
            fail(f"local attachment target is missing: {target}")


def verify_attachments(paths: Paths, bundle: Path) -> None:
    if paths.config.exists():
        candidate = bundle / CONFIG_FILE
        if not candidate.exists() or not os.path.samefile(paths.config, candidate):
            fail(f"active bundle does not expose the stable operator policy: {candidate}")
    for record in attachment_records(paths):
        candidate = bundle / record["name"]
        target = Path(record["target"])
        if record["kind"] == "directory":
            if not is_pointer(candidate) or not same_path(candidate, target):
                fail(f"local directory attachment drifted: {candidate}")
        elif not candidate.exists() or not os.path.samefile(candidate, target):
            fail(f"local file attachment drifted: {candidate}")


def validate_runtime_paths(paths: Paths) -> None:
    if not path_exists(paths.canonical):
        return
    canonical_target = paths.canonical.resolve(strict=True)
    home = paths.skills.parent.parent
    for base in (home / ".claude" / "skills", home / ".codex" / "skills"):
        runtime = base / SKILL_NAME
        if not path_exists(runtime):
            continue
        if not is_pointer(runtime):
            fail(f"runtime skill is an independent stale copy; move it aside before sync: {runtime}")
        if runtime.resolve(strict=True) != canonical_target:
            fail(f"runtime skill does not target the canonical active bundle: {runtime}")


def legacy_git(paths: Paths, *arguments: str, check: bool = True) -> subprocess.CompletedProcess:
    return git(paths, *arguments, cwd=paths.canonical, check=check)


def inspect_legacy(paths: Paths, target_entries: list[dict[str, str | int]]) -> dict:
    if is_pointer(paths.canonical) or not (paths.canonical / ".git").exists():
        fail(f"{paths.canonical} is neither a legacy Git clone nor a bundle pointer")
    top = Path(legacy_git(paths, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if top != paths.canonical.resolve():
        fail(f"legacy checkout root is {top}, expected {paths.canonical}")
    branch = legacy_git(paths, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
    if branch != "main":
        fail(f"legacy migration requires main, current branch is {branch}")
    if legacy_git(paths, "diff", "--quiet", "--", check=False).returncode or legacy_git(
        paths, "diff", "--cached", "--quiet", "--", check=False
    ).returncode:
        fail("legacy migration requires clean tracked files; preserve local edits before retrying")
    origin = legacy_git(paths, "config", "--local", "--get", "remote.origin.url").stdout.strip()
    if origin.rstrip("/") != REPOSITORY_URL.rstrip("/"):
        fail(f"legacy origin is not the canonical repository: {origin!r}")
    current = legacy_git(paths, "rev-parse", "HEAD^{commit}").stdout.strip()
    tracked_raw = legacy_git(paths, "ls-files", "-z").stdout
    tracked = {name for name in tracked_raw.split("\0") if name}
    tracked_top = {PurePosixPath(name).parts[0].casefold() for name in tracked}
    target_top = {PurePosixPath(str(entry["path"])).parts[0].casefold() for entry in target_entries}
    local_names: set[str] = set()
    for arguments in (
        ("ls-files", "--others", "--exclude-standard", "-z"),
        ("ls-files", "--others", "--ignored", "--exclude-standard", "-z"),
    ):
        for name in legacy_git(paths, *arguments).stdout.split("\0"):
            if not name:
                continue
            first = PurePosixPath(name).parts[0]
            if first.casefold() in tracked_top:
                fail(f"untracked state nested inside a tracked runtime directory cannot be migrated safely: {name}")
            local_names.add(first)
    local_names.discard(CONFIG_FILE)
    for name in local_names:
        source = paths.canonical / name
        if name.casefold() in target_top:
            fail(f"local state collides with the target contract tree: {name}")
        if is_pointer(source) or not (source.is_file() or source.is_dir()):
            fail(f"local state must be a regular top-level file or directory: {source}")
    return {"commit": current, "local_names": sorted(local_names)}


def ensure_ancestor(paths: Paths, old: str, target: str) -> None:
    result = git(
        paths,
        "merge-base",
        "--is-ancestor",
        old,
        target,
        repository=paths.repository,
        check=False,
    )
    if result.returncode != 0:
        fail(f"target {target} does not fast-forward active commit {old}")


def record_current(paths: Paths, current: str, previous: str | None) -> None:
    write_json(
        paths.current,
        {
            "schema": STATE_SCHEMA,
            "repository": REPOSITORY_URL,
            "current": current,
            "previous": previous,
            "activated_at": int(time.time()),
        },
    )


def prepare_legacy_local_state(
    paths: Paths,
    backup: Path,
    local_names: list[str],
    bundles: list[Path],
) -> None:
    records: list[dict[str, str]] = []
    for name in local_names:
        source = paths.canonical / name
        if source.is_dir():
            records.append({"kind": "directory", "name": name, "target": str(backup / name)})
        else:
            destination = paths.local / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            records.append({"kind": "file", "name": name, "target": str(destination)})
    write_json(paths.attachments, {"schema": STATE_SCHEMA, "entries": records})
    for bundle in bundles:
        attach_local(paths, bundle, allow_dangling=True)


def migrate_legacy(paths: Paths, target: str, target_entries: list[dict[str, str | int]]) -> None:
    legacy = inspect_legacy(paths, target_entries)
    current = str(legacy["commit"])
    ensure_ancestor(paths, current, target)
    current_bundle = materialize_bundle(paths, current, require_entrypoints=False)
    target_bundle = materialize_bundle(paths, target)
    if (paths.canonical / CONFIG_FILE).exists():
        config = (paths.canonical / CONFIG_FILE).read_bytes()
        if paths.config.exists() and paths.config.read_bytes() != config:
            fail(f"stable operator policy conflicts with the legacy bytes at {paths.canonical / CONFIG_FILE}")
        if not paths.config.exists():
            write_bytes(paths.config, config, exclusive=True)
    backup = paths.legacy / f"{current}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    prepare_legacy_local_state(
        paths,
        backup,
        list(legacy["local_names"]),
        [current_bundle, target_bundle],
    )
    attach_local(paths, current_bundle, allow_dangling=True)
    attach_local(paths, target_bundle, allow_dangling=True)
    write_json(
        paths.transaction,
        {
            "schema": STATE_SCHEMA,
            "phase": "prepared",
            "backup": str(backup),
            "previous": current,
            "target": target,
        },
    )
    try:
        os.replace(paths.canonical, backup)
        write_json(
            paths.transaction,
            {
                "schema": STATE_SCHEMA,
                "phase": "moved",
                "backup": str(backup),
                "previous": current,
                "target": target,
            },
        )
        activate(paths, target_bundle)
        verify_attachments(paths, target_bundle)
        record_current(paths, target, current)
        paths.transaction.unlink(missing_ok=True)
    except Exception:
        if not path_exists(paths.canonical) and backup.exists():
            os.replace(backup, paths.canonical)
        raise


def recover_transaction(paths: Paths) -> None:
    if not paths.transaction.exists():
        return
    transaction = read_json(paths.transaction)
    backup = Path(str(transaction.get("backup", "")))
    previous = str(transaction.get("previous", ""))
    target = str(transaction.get("target", ""))
    if is_pointer(paths.canonical):
        _, receipt = active_bundle(paths)
        current = str(receipt["commit"])
        record_current(paths, current, previous if current == target else target)
        paths.transaction.unlink()
        return
    if path_exists(paths.canonical):
        paths.transaction.unlink()
        return
    previous_bundle = paths.bundles / previous
    if previous and previous_bundle.exists():
        attach_local(paths, previous_bundle)
        activate(paths, previous_bundle)
        record_current(paths, previous, target or None)
        paths.transaction.unlink()
        return
    if backup.exists():
        os.replace(backup, paths.canonical)
        paths.transaction.unlink()
        return
    fail(f"incomplete migration cannot find either previous bundle or legacy backup: {paths.transaction}")


def sync_versioned(paths: Paths, target: str) -> None:
    bundle, receipt = active_bundle(paths)
    current = str(receipt["commit"])
    ensure_ancestor(paths, current, target)
    if current == target:
        print(f"ok      complete bundle already active at {target}")
        return
    target_bundle = materialize_bundle(paths, target)
    attach_local(paths, target_bundle)
    verify_attachments(paths, target_bundle)
    validate_runtime_paths(paths)
    write_json(
        paths.transaction,
        {"schema": STATE_SCHEMA, "phase": "prepared", "previous": current, "target": target},
    )
    activate(paths, target_bundle)
    verify_attachments(paths, target_bundle)
    record_current(paths, target, current)
    paths.transaction.unlink()
    print(f"synced  complete Git tree {current} -> {target}")


def transient_paths(root: Path) -> Paths:
    return Paths.for_home(root)


def dry_run_sync(paths: Paths) -> None:
    current: str | None = None
    if path_exists(paths.canonical):
        validate_runtime_paths(paths)
        if is_pointer(paths.canonical):
            _, receipt = active_bundle(paths)
            current = str(receipt["commit"])
        else:
            # Target entries are validated in the transient object store below.
            current = legacy_git(paths, "rev-parse", "HEAD^{commit}").stdout.strip()
    with tempfile.TemporaryDirectory(prefix="issue-flow-dry-run-") as temporary:
        dry = transient_paths(Path(temporary))
        initialize_state(dry)
        target = fetch_target(dry)
        try:
            entries = tree_entries(dry, target)
            materialize_bundle(dry, target)
            if current:
                ensure_ancestor(dry, current, target)
            if path_exists(paths.canonical) and not is_pointer(paths.canonical):
                inspect_legacy(paths, entries)
        finally:
            finish_target(dry, target, keep=False)
    print(f"would   activate complete Git tree {current or '<absent>'} -> {target}")


def sync(paths: Paths, dry_run: bool) -> None:
    if dry_run:
        dry_run_sync(paths)
        return
    initialize_state(paths)
    with InstallerLock(paths):
        recover_transaction(paths)
        validate_runtime_paths(paths)
        target = fetch_target(paths)
        keep = False
        try:
            entries = tree_entries(paths, target)
            materialize_bundle(paths, target)
            if not path_exists(paths.canonical):
                target_bundle = paths.bundles / target
                attach_local(paths, target_bundle)
                activate(paths, target_bundle)
                record_current(paths, target, None)
                print(f"installed complete Git tree at {target}")
            elif is_pointer(paths.canonical):
                sync_versioned(paths, target)
            else:
                migrate_legacy(paths, target, entries)
                print(f"migrated legacy clone to complete Git tree at {target}")
            keep = True
        finally:
            finish_target(paths, target, keep)


def ensure_layout(paths: Paths, dry_run: bool) -> None:
    if path_exists(paths.canonical) and is_pointer(paths.canonical):
        active_bundle(paths)
        return
    sync(paths, dry_run)


def runtime_paths(paths: Paths) -> tuple[Path, Path]:
    home = paths.skills.parent.parent
    return (
        home / ".claude" / "skills" / SKILL_NAME,
        home / ".codex" / "skills" / SKILL_NAME,
    )


def install_runtime_links(paths: Paths, dry_run: bool) -> None:
    ensure_layout(paths, dry_run)
    if dry_run and not path_exists(paths.canonical):
        return
    active, _ = active_bundle(paths)
    for runtime in runtime_paths(paths):
        if not runtime.parent.parent.exists():
            continue
        if path_exists(runtime):
            if is_pointer(runtime) and runtime.resolve(strict=True) == active:
                print(f"ok      {runtime} already targets the active bundle")
                continue
            print(f"SKIP    {runtime} exists independently; move it aside before linking")
            continue
        if dry_run:
            print(f"would   link {runtime} -> {paths.canonical}")
            continue
        runtime.parent.mkdir(parents=True, exist_ok=True)
        create_directory_pointer(runtime, paths.canonical)
        if runtime.resolve(strict=True) != active:
            fail(f"runtime link read-back failed: {runtime}")
        print(f"linked  {runtime} -> {paths.canonical}")


def remove_runtime_links(paths: Paths, dry_run: bool) -> None:
    target = paths.canonical.resolve(strict=True) if path_exists(paths.canonical) else None
    for runtime in runtime_paths(paths):
        if not path_exists(runtime):
            continue
        if not is_pointer(runtime) or target is None or runtime.resolve(strict=True) != target:
            print(f"SKIP    {runtime} is not an installer-owned link")
            continue
        if dry_run:
            print(f"would   remove {runtime}")
            continue
        if runtime.is_symlink():
            runtime.unlink()
        else:
            runtime.rmdir()
        print(f"removed {runtime}")


def config_block(text: str, origin: Path) -> tuple[int, int, str]:
    start = text.find(CONFIG_START)
    end = text.find(CONFIG_END)
    if start < 0 or end < start:
        fail(f"configuration markers are missing or reversed in {origin}")
    end += len(CONFIG_END)
    return start, end, text[start:end]


def configure(paths: Paths, assignment: str | None, dry_run: bool) -> None:
    ensure_layout(paths, dry_run)
    if dry_run and not path_exists(paths.canonical):
        return
    context = nullcontext() if dry_run else InstallerLock(paths)
    with context:
        bundle, _ = active_bundle(paths)
        template = (bundle / "SKILL.md").read_bytes().decode("utf-8")
        _, _, defaults = config_block(template, bundle / "SKILL.md")
        config_exists = paths.config.exists()
        text = paths.config.read_bytes().decode("utf-8") if config_exists else defaults
        if assignment:
            if "=" not in assignment:
                fail(f"expected '<Setting>=<value>', got {assignment!r}")
            name, value = (part.strip() for part in assignment.split("=", 1))
            if "|" in value:
                fail("configuration values may not contain '|'")
            lines = text.splitlines(keepends=True)
            matches: list[int] = []
            for index, line in enumerate(lines):
                cells = line.split("|")
                if len(cells) >= 4 and cells[1].strip() == name:
                    matches.append(index)
            if len(matches) != 1:
                fail(f"configuration setting {name!r} matched {len(matches)} rows")
            index = matches[0]
            cells = lines[index].split("|")
            old = cells[2].strip()
            cells[2] = f" {value} "
            lines[index] = "|".join(cells)
            text = "".join(lines)
            print(f"set     {name}: {old} -> {value}")
        if dry_run:
            if not assignment:
                print(config_block(text, paths.config if config_exists else bundle / "SKILL.md")[2])
            return
        if not assignment and config_exists:
            print(config_block(text, paths.config)[2])
            return
        if paths.config.exists():
            backup = paths.state / ".backups" / f"{CONFIG_FILE}.{time.time_ns()}"
            backup.parent.mkdir(exist_ok=True)
            shutil.copyfile(paths.config, backup)
        write_bytes(paths.config, text.encode("utf-8"))
        if paths.config.read_bytes() != text.encode("utf-8"):
            fail(f"operator policy write read-back failed: {paths.config}")
        for candidate in paths.bundles.iterdir():
            if candidate.is_dir() and not candidate.name.startswith(".staging-"):
                attach_local(paths, candidate)
        if not assignment:
            print(config_block(text, paths.config)[2])


def rollback(paths: Paths, dry_run: bool) -> None:
    initialize_state(paths)
    with InstallerLock(paths):
        recover_transaction(paths)
        bundle, receipt = active_bundle(paths)
        state = read_json(paths.current)
        previous = state.get("previous")
        if not isinstance(previous, str) or not previous:
            fail("no retained previous bundle is recorded")
        previous_bundle = paths.bundles / previous
        verify_bundle(paths, previous_bundle, previous)
        attach_local(paths, previous_bundle)
        if dry_run:
            print(f"would   roll back {receipt['commit']} -> {previous}")
            return
        validate_runtime_paths(paths)
        write_json(
            paths.transaction,
            {
                "schema": STATE_SCHEMA,
                "phase": "prepared",
                "previous": str(receipt["commit"]),
                "target": previous,
            },
        )
        activate(paths, previous_bundle)
        record_current(paths, previous, str(receipt["commit"]))
        paths.transaction.unlink()
        print(f"rolled  back {receipt['commit']} -> {previous}")


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def recover(paths: Paths, dry_run: bool) -> None:
    initialize_state(paths)
    if paths.lock.exists():
        owner_path = paths.lock / "owner.json"
        owner = read_json(owner_path) if owner_path.exists() else {}
        pid = owner.get("pid")
        if isinstance(pid, int) and process_alive(pid):
            fail(f"installer lock belongs to live process {pid}; recovery refused")
        if dry_run:
            print(f"would   remove stale installer lock {paths.lock}")
        else:
            shutil.rmtree(paths.lock)
    if dry_run:
        if paths.transaction.exists():
            print(f"would   recover transaction {paths.transaction}")
        return
    with InstallerLock(paths):
        recover_transaction(paths)
        for candidate in paths.bundles.glob(".staging-*"):
            shutil.rmtree(candidate, ignore_errors=True)
        if is_pointer(paths.canonical):
            bundle, receipt = active_bundle(paths)
            state = read_json(paths.current) if paths.current.exists() else {}
            previous = state.get("previous") if isinstance(state.get("previous"), str) else None
            record_current(paths, str(receipt["commit"]), previous)
            print(f"recovered active bundle {bundle.name}")
        else:
            print("recovery found no incomplete immutable-bundle activation")


def status(paths: Paths) -> None:
    print(f"canonical  {paths.canonical}")
    if not path_exists(paths.canonical):
        print("layout     absent")
        return
    if not is_pointer(paths.canonical):
        print("layout     legacy Git clone")
        config = paths.canonical / CONFIG_FILE
        print(f"config     {config if config.exists() else 'portable defaults'}")
        return
    bundle, receipt = active_bundle(paths)
    state = read_json(paths.current) if paths.current.exists() else {}
    print("layout     immutable bundle")
    print(f"active     {receipt['commit']} tree {receipt['tree']}")
    print(f"bundle     {bundle}")
    print(f"previous   {state.get('previous') or 'none'}")
    print(f"config     {paths.config if paths.config.exists() else 'portable defaults'}")
    for runtime in runtime_paths(paths):
        kind = "link" if is_pointer(runtime) else ("absent" if not path_exists(runtime) else "independent")
        print(f"target     {runtime} [{kind}]")


def parse_args(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        default="install",
        choices=("install", "sync", "uninstall", "status", "config", "rollback", "recover"),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--set")
    parser.add_argument("--from", dest="source")
    options = parser.parse_args(arguments)
    if options.source:
        fail(
            "single-file sync is retired because it cannot prove companion bytes; "
            "run sync without --from to install one verified Git tree"
        )
    if options.set and options.command != "config":
        fail("--set is valid only with config")
    return options


def main(arguments: list[str] | None = None) -> int:
    try:
        options = parse_args(sys.argv[1:] if arguments is None else arguments)
        paths = Paths.for_home(Path.home())
        # A legacy checkout cannot be renamed on Windows while it is this process's current directory.
        os.chdir(paths.skills if paths.skills.exists() else Path.home())
        if options.command == "install":
            install_runtime_links(paths, options.dry_run)
        elif options.command == "sync":
            sync(paths, options.dry_run)
        elif options.command == "uninstall":
            remove_runtime_links(paths, options.dry_run)
        elif options.command == "status":
            status(paths)
        elif options.command == "config":
            configure(paths, options.set, options.dry_run)
        elif options.command == "rollback":
            rollback(paths, options.dry_run)
        elif options.command == "recover":
            recover(paths, options.dry_run)
        return 0
    except InstallError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
