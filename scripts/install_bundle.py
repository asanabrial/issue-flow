#!/usr/bin/env python3
"""Install and activate complete issue-flow Git trees as immutable bundles."""

from __future__ import annotations

import argparse
import configparser
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
import unicodedata
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
REFERENCE_LINK = re.compile(r"\[([^\]]+)\]\[([^\]]*)\]")
REFERENCE_DEFINITION = re.compile(r"(?m)^\s{0,3}\[([^\]]+)\]:\s*(\S+)")
TEMPORARY_NAME = re.compile(r"^\..+\.[0-9a-f]{32}\.tmp$")
DISCARD_NAME = re.compile(r"^\.discard-([0-9a-f]{40}|[0-9a-f]{64})-[0-9a-f]{32}$")
WINDOWS_RESERVED = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
        *(f"COM{index}" for index in "¹²³"),
        *(f"LPT{index}" for index in "¹²³"),
    }
)
MINIMUM_GIT_VERSION = (2, 36)
_VERIFIED_GIT_EXECUTABLE: str | None = None


class InstallError(RuntimeError):
    """A fail-closed installer error with operator-safe recovery text."""


@dataclass(frozen=True)
class Paths:
    home: Path
    skills: Path
    canonical: Path
    state: Path
    repository: Path
    bundles: Path
    legacy: Path
    local: Path
    policies: Path
    config: Path
    current: Path
    attachments: Path
    transaction: Path
    policy_transaction: Path
    lock: Path
    hooks: Path
    template: Path

    @classmethod
    def for_home(cls, home: Path) -> "Paths":
        home = Path(os.path.realpath(home))
        skills = home / ".agents" / "skills"
        state = skills / ".issue-flow"
        return cls(
            home=home,
            skills=skills,
            canonical=skills / SKILL_NAME,
            state=state,
            repository=state / "repository.git",
            bundles=state / "bundles",
            legacy=state / "legacy",
            local=state / "local",
            policies=state / "policies",
            config=state / CONFIG_FILE,
            current=state / "current.json",
            attachments=state / "attachments.json",
            transaction=state / "transaction.json",
            policy_transaction=state / "policy-transaction.json",
            lock=skills / ".issue-flow.sync.lock",
            hooks=state / "empty-hooks",
            template=state / "empty-template",
        )


def fail(message: str) -> None:
    raise InstallError(message)


def path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def validate_tree_path(name: str, casefolded: dict[str, str] | None = None) -> PurePosixPath:
    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        fail(f"the target tree contains an unsafe path: {name!r}")
    if "\\" in name or unicodedata.normalize("NFC", name) != name:
        fail(f"the target tree contains a non-portable path: {name!r}")
    for part in pure.parts:
        if any(ord(character) < 32 or character in ':<>"|?*' for character in part) or part.endswith(
            (".", " ")
        ):
            fail(f"the target tree contains a Windows-unsafe path: {name!r}")
        if part.split(".", 1)[0].upper() in WINDOWS_RESERVED:
            fail(f"the target tree contains a Windows-reserved path: {name!r}")
        if part.casefold() == ".git":
            fail(f"the target tree attempts to materialize Git metadata: {name!r}")
    if casefolded is not None:
        for index in range(1, len(pure.parts) + 1):
            prefix = "/".join(pure.parts[:index])
            folded = unicodedata.normalize("NFC", prefix).casefold()
            if folded in casefolded and casefolded[folded] != prefix:
                fail(f"the target tree has a portable-name collision: {casefolded[folded]!r} and {prefix!r}")
            casefolded[folded] = prefix
    return pure


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
    chain = [path, *path.parents]
    for directory in reversed(chain):
        if directory == Path():
            continue
        if path_exists(directory):
            if is_pointer(directory) or not directory.is_dir():
                fail(f"{directory} must be a real directory, not a link or file")
            continue
        try:
            directory.mkdir(mode=0o700)
            fsync_directory(directory.parent)
        except FileExistsError:
            pass
        if is_pointer(directory) or not directory.is_dir():
            fail(f"{directory} must be a real directory, not a link or file")


def validate_real_ancestors(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError:
        fail(f"installer path escapes the resolved home: {path}")
    current = root
    if is_pointer(current) or not current.is_dir():
        fail(f"resolved installer home is not a real directory: {current}")
    for part in relative.parts:
        current = current / part
        if not path_exists(current):
            return
        if is_pointer(current) or not current.is_dir():
            fail(f"installer path ancestor is not a real directory: {current}")


def fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        fsync_descriptor(descriptor)
    finally:
        os.close(descriptor)


def fsync_descriptor(descriptor: int) -> None:
    os.fsync(descriptor)
    if sys.platform == "darwin":
        import fcntl

        fcntl.fcntl(descriptor, 51)  # F_FULLFSYNC reaches stable storage on macOS.


def fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDWR if os.name == "nt" else os.O_RDONLY)
    try:
        fsync_descriptor(descriptor)
    finally:
        os.close(descriptor)


def replace_path(source: Path, destination: Path) -> None:
    source_parent = source.parent
    destination_parent = destination.parent
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        move = kernel32.MoveFileExW
        move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        move.restype = ctypes.c_int
        if not move(str(source), str(destination), 0x1 | 0x8):
            fail(
                f"durable replace failed from {source} to {destination} "
                f"(Windows error {ctypes.get_last_error()})"
            )
    else:
        os.replace(source, destination)
        if destination_parent != source_parent:
            fsync_directory(destination_parent)
        fsync_directory(source_parent)


def remove_tree(path: Path, ignore_errors: bool = False) -> None:
    def make_writable(function, target, _error) -> None:
        details = os.lstat(target)
        if stat.S_ISREG(details.st_mode) and details.st_nlink != 1:
            fail(f"refusing to chmod externally hard-linked temporary file: {target}")
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        function(target)

    try:
        shutil.rmtree(path, onerror=make_writable)
    except OSError:
        if not ignore_errors:
            raise


def write_bytes(path: Path, content: bytes, exclusive: bool = False) -> None:
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            fsync_descriptor(handle.fileno())
    finally:
        os.close(descriptor)


def write_bytes_atomic(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(temporary, flags, mode)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
        if os.name != "nt":
            os.fchmod(descriptor, mode)
        fsync_descriptor(descriptor)
        details = os.fstat(descriptor)
        listed = os.lstat(temporary)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            fail(f"atomic publication temporary is not a private regular file: {temporary}")
        if (listed.st_dev, listed.st_ino) != (details.st_dev, details.st_ino):
            fail(f"atomic publication temporary changed identity: {temporary}")
        os.close(descriptor)
        descriptor = -1
        replace_path(temporary, path)
        fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: object) -> None:
    write_bytes_atomic(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def remove_state_file(path: Path, missing_ok: bool = False) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        if not missing_ok:
            raise
    fsync_directory(path.parent)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        fail(f"cannot read trusted installer state {path}: {error}")
    if not isinstance(value, dict):
        fail(f"trusted installer state {path} is not an object")
    return value


class InstallerLock:
    def __init__(self, paths: Paths) -> None:
        self.paths = paths
        self.handle = None

    def __enter__(self) -> "InstallerLock":
        self.handle = open_safe_lock(self.paths.lock)
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0, os.SEEK_END)
                if self.handle.tell() == 0:
                    self.handle.write(b"\0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            fail(f"another installer holds the operating-system lock {self.paths.lock}")
        owner = (json.dumps({"pid": os.getpid(), "started": int(time.time()), "schema": STATE_SCHEMA}) + "\n").encode()
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(owner)
        self.handle.flush()
        os.fsync(self.handle.fileno())
        return self

    def __exit__(self, *_: object) -> None:
        if not self.handle:
            return
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
        self.handle = None


def open_safe_lock(path: Path):
    if os.name != "nt":
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as error:
            fail(f"cannot open installer lock safely: {error}")
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            os.close(descriptor)
            fail(f"installer lock must be a private regular file: {path}")
        return os.fdopen(descriptor, "r+b", closefd=True)

    import msvcrt

    class FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    class FileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", ctypes.c_uint32),
            ("creation", FileTime),
            ("access", FileTime),
            ("write", FileTime),
            ("volume", ctypes.c_uint32),
            ("size_high", ctypes.c_uint32),
            ("size_low", ctypes.c_uint32),
            ("links", ctypes.c_uint32),
            ("index_high", ctypes.c_uint32),
            ("index_low", ctypes.c_uint32),
        ]

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
        0x80000000 | 0x40000000,
        0x00000001,
        None,
        4,
        0x00000080 | 0x00200000,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        fail(f"cannot open installer lock safely (Windows error {ctypes.get_last_error()})")
    information = FileInformation()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        fail(f"cannot inspect installer lock (Windows error {error})")
    if information.attributes & 0x400 or information.links != 1:
        kernel32.CloseHandle(handle)
        fail(f"installer lock must be a private regular file: {path}")
    descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR)
    return os.fdopen(descriptor, "r+b", closefd=True)


def clean_git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("GIT_") or name in {"SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE"}:
            environment.pop(name, None)
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "",
            "GIT_CONFIG_COUNT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_ATTR_NOSYSTEM": "1",
        }
    )
    return environment


def disabled_hooks_path() -> str:
    return "NUL" if os.name == "nt" else "/dev/null"


def git_executable() -> str:
    global _VERIFIED_GIT_EXECUTABLE
    if _VERIFIED_GIT_EXECUTABLE:
        return _VERIFIED_GIT_EXECUTABLE
    configured = os.environ.get("ISSUE_FLOW_GIT")
    if configured:
        if not os.path.isabs(configured):
            fail("ISSUE_FLOW_GIT must name one absolute selected executable")
        executable = os.path.realpath(configured)
    else:
        executable = shutil.which("git")
    if not executable:
        fail("git is required; install Git 2.36 or newer and retry")
    result = subprocess.run(
        [executable, "--version"],
        env=clean_git_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    match = re.search(r"\b(\d+)\.(\d+)(?:\.\d+)?\b", result.stdout)
    if result.returncode or not match or tuple(map(int, match.groups())) < MINIMUM_GIT_VERSION:
        fail(f"Git 2.36 or newer is required, got {result.stdout.strip() or 'an unreadable version'}")
    _VERIFIED_GIT_EXECUTABLE = executable
    return executable


def git(
    paths: Paths,
    *arguments: str,
    cwd: Path | None = None,
    repository: Path | None = None,
    text: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess:
    executable = git_executable()
    if repository:
        validate_repository_config(repository)
    command = [
        executable,
        "--no-replace-objects",
        "-c",
        f"core.hooksPath={disabled_hooks_path()}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "credential.helper=",
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


def initialize_directories(paths: Paths) -> None:
    ensure_real_directory(paths.skills)
    ensure_real_directory(paths.state)
    for directory in (
        paths.bundles,
        paths.legacy,
        paths.local,
        paths.policies,
        paths.template,
    ):
        ensure_real_directory(directory)


def validate_repository_config(repository: Path) -> None:
    if is_pointer(repository) or not repository.is_dir():
        fail(f"bare repository is not a real installer-owned directory: {repository}")
    if path_exists(repository / "commondir"):
        fail(f"bare repository may not redirect its common directory: {repository / 'commondir'}")
    for directory in (repository / "objects", repository / "refs"):
        if is_pointer(directory) or not directory.is_dir():
            fail(f"bare repository control directory is missing or linked: {directory}")
    for optional in (repository / "objects" / "info", repository / "objects" / "pack"):
        if path_exists(optional) and (is_pointer(optional) or not optional.is_dir()):
            fail(f"bare repository object directory is linked or invalid: {optional}")
    for forbidden in (repository / "objects" / "info" / "alternates", repository / "objects" / "info" / "http-alternates"):
        if path_exists(forbidden):
            fail(f"bare repository must not use an alternate object database: {forbidden}")
    for directory, names, files in os.walk(repository / "refs", followlinks=False):
        root = Path(directory)
        for name in names:
            candidate = root / name
            if is_pointer(candidate) or not candidate.is_dir():
                fail(f"bare repository ref directory is linked or invalid: {candidate}")
        for name in files:
            candidate = root / name
            details = os.lstat(candidate)
            if is_pointer(candidate) or not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
                fail(f"bare repository ref is not a private regular file: {candidate}")
            if candidate.read_bytes().lstrip().startswith(b"ref:"):
                fail(f"bare repository may not use symbolic refs: {candidate}")
    config_path = repository / "config"
    for authority_file in (config_path, repository / "HEAD", repository / "packed-refs"):
        if not path_exists(authority_file):
            if authority_file.name == "packed-refs":
                continue
            fail(f"bare repository authority file is missing: {authority_file}")
        details = os.lstat(authority_file)
        if is_pointer(authority_file) or not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            fail(f"bare repository authority file is not private and regular: {authority_file}")
    parser = configparser.RawConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(config_path.read_text(encoding="utf-8"))
    except (configparser.Error, UnicodeError, OSError) as error:
        fail(f"bare repository config is invalid: {error}")
    if parser.sections() != ["core"]:
        fail(f"bare repository contains non-core authority configuration: {parser.sections()}")
    allowed = {
        "repositoryformatversion",
        "filemode",
        "bare",
        "logallrefupdates",
        "symlinks",
        "ignorecase",
        "precomposeunicode",
        "fsync",
        "fsyncmethod",
    }
    unexpected = set(parser["core"]) - allowed
    if unexpected or parser["core"].get("bare", "").casefold() != "true":
        fail(f"bare repository config is not installer-owned: unexpected={sorted(unexpected)}")
    if parser["core"].get("fsync", "").casefold() != "reference" or parser["core"].get(
        "fsyncmethod", ""
    ).casefold() != "fsync":
        fail(f"bare repository does not durably fsync references: {config_path}")


def initialize_repository(paths: Paths) -> None:
    if not paths.repository.exists():
        staging = paths.state / f".repository-{uuid.uuid4().hex}"
        try:
            git(paths, "init", "--bare", f"--template={paths.template}", str(staging))
            replace_repository_config(staging)
            validate_repository_config(staging)
            replace_path(staging, paths.repository)
            fsync_directory(paths.state)
        finally:
            if staging.exists():
                remove_tree(staging, ignore_errors=True)
    elif is_pointer(paths.repository) or not paths.repository.is_dir():
        fail(f"{paths.repository} is not the installer-owned bare repository")
    validate_repository_config(paths.repository)
    bare = git(paths, "rev-parse", "--is-bare-repository", repository=paths.repository).stdout.strip()
    if bare != "true":
        fail(f"{paths.repository} is not a bare Git repository")


def initialize_state(paths: Paths) -> None:
    initialize_directories(paths)
    initialize_repository(paths)


def fetch_target(paths: Paths) -> str:
    temporary_ref = "refs/issue-flow/incoming"
    allow_file = "always" if urllib.parse.urlparse(REPOSITORY_URL).scheme == "file" else "never"
    staging = paths.state / f".fetch-{uuid.uuid4().hex}"
    try:
        # Clone resolves the explicit URL before a destination-local config exists. The config is
        # then replaced with installer-owned core-only bytes before any local repository command.
        git(
            paths,
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.ext.allow=never",
            "-c",
            f"protocol.file.allow={allow_file}",
            "-c",
            "protocol.https.allow=always",
            "clone",
            "--bare",
            "--no-tags",
            "--single-branch",
            "--branch",
            "main",
            REPOSITORY_URL,
            str(staging),
            cwd=paths.state,
        )
        replace_repository_config(staging)
        validate_repository_config(staging)
        target = git(
            paths,
            "rev-parse",
            "refs/heads/main^{commit}",
            repository=staging,
        ).stdout.strip()
        validate_commit_id(target)
        copy_object_database(staging / "objects", paths.repository / "objects")
        git(paths, "cat-file", "-e", f"{target}^{{commit}}", repository=paths.repository)
        update_direct_ref(paths, temporary_ref, target)
        return target
    finally:
        if staging.exists():
            remove_tree(staging)
            fsync_directory(paths.state)


def replace_repository_config(repository: Path) -> None:
    content = (
        "[core]\n"
        "\trepositoryformatversion = 0\n"
        f"\tfilemode = {'false' if os.name == 'nt' else 'true'}\n"
        "\tbare = true\n"
        "\tlogallrefupdates = false\n"
        "\tfsync = reference\n"
        "\tfsyncmethod = fsync\n"
    ).encode("ascii")
    write_bytes_atomic(repository / "config", content)


def ensure_object_directory(root: Path, relative: Path) -> Path:
    current = root
    ensure_real_directory(current)
    for part in relative.parts:
        current = current / part
        if path_exists(current):
            if is_pointer(current) or not current.is_dir():
                fail(f"Git object destination parent is not a real directory: {current}")
        else:
            current.mkdir()
            fsync_directory(current.parent)
    return current


def copy_object_database(source: Path, destination: Path) -> None:
    created_directories: set[Path] = {destination}
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        target = destination / relative
        if is_pointer(item):
            fail(f"fetched object database contains a link: {relative}")
        if item.is_dir():
            ensure_object_directory(destination, relative)
            created_directories.add(target)
            continue
        content = item.read_bytes()
        ensure_object_directory(destination, relative.parent)
        created_directories.add(target.parent)
        if path_exists(target):
            if is_pointer(target) or target.is_dir():
                fail(f"Git object destination is not a regular file: {target}")
            details = os.lstat(target)
            if stat.S_ISREG(details.st_mode) and details.st_nlink == 1 and target.read_bytes() == content:
                continue
        write_bytes_atomic(target, content)
    for directory in sorted(created_directories, key=lambda item: len(item.parts), reverse=True):
        fsync_directory(directory)


def direct_ref_target(paths: Paths, reference: str) -> str | None:
    result = git(
        paths,
        "for-each-ref",
        "--format=%(refname)%00%(objectname)",
        reference,
        repository=paths.repository,
        check=False,
    )
    if result.returncode:
        fail(f"cannot inspect installer ref {reference}")
    matches = [
        line.split("\0", 1)[1]
        for line in result.stdout.splitlines()
        if line.split("\0", 1)[0] == reference
    ]
    if not matches:
        return None
    if len(matches) != 1:
        fail(f"installer ref is ambiguous: {reference}")
    target = matches[0]
    validate_commit_id(target)
    kind = git(paths, "cat-file", "-t", target, repository=paths.repository, check=False)
    if kind.returncode or kind.stdout.strip() != "commit":
        fail(f"installer ref {reference} does not point directly to a commit")
    return target


def update_direct_ref(paths: Paths, reference: str, target: str) -> None:
    validate_commit_id(target)
    current = direct_ref_target(paths, reference)
    if current is not None and current != target:
        fail(f"refusing to replace installer ref {reference}: {current} != {target}")
    expected = current or ("0" * len(target))
    git(
        paths,
        "update-ref",
        "--no-deref",
        reference,
        target,
        expected,
        repository=paths.repository,
    )


def incoming_target(paths: Paths) -> str | None:
    return direct_ref_target(paths, "refs/issue-flow/incoming")


def remove_incoming_ref(paths: Paths, expected: str | None = None) -> None:
    target = incoming_target(paths)
    if target is None:
        if expected is not None:
            fail("incoming acquisition ref disappeared before transaction completion")
        return
    if expected is not None and target != expected:
        fail(f"incoming acquisition ref changed from {expected} to {target}")
    git(
        paths,
        "update-ref",
        "--no-deref",
        "-d",
        "refs/issue-flow/incoming",
        target,
        repository=paths.repository,
    )


def finish_target(paths: Paths, target: str, keep: bool) -> None:
    if keep:
        update_direct_ref(paths, f"refs/issue-flow/bundles/{target}", target)
    remove_incoming_ref(paths, target)


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
        pure = validate_tree_path(name, casefolded)
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
    def validate_destination(name: str, destination: str) -> None:
        destination = destination.strip().strip("<>").split(maxsplit=1)[0]
        if not destination or destination.startswith("#"):
            return
        parsed = urllib.parse.urlparse(destination)
        if parsed.scheme or parsed.netloc:
            return
        relative = urllib.parse.unquote(parsed.path)
        if not relative:
            return
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

    for name in sorted(path for path in tracked if path.lower().endswith(".md")):
        text = (bundle / Path(*PurePosixPath(name).parts)).read_text(encoding="utf-8")
        for match in LOCAL_LINK.finditer(text):
            validate_destination(name, match.group(1))
        definitions = {match.group(1).strip().casefold(): match.group(2) for match in REFERENCE_DEFINITION.finditer(text)}
        for label, destination in definitions.items():
            if not label.startswith("^"):
                validate_destination(name, destination)
        for match in REFERENCE_LINK.finditer(text):
            label = (match.group(2) or match.group(1)).strip().casefold()
            if label not in definitions:
                fail(f"Markdown reference link in {name} has no definition: {label}")
            validate_destination(name, definitions[label])


def validate_attachment_collisions(paths: Paths, entries: list[dict[str, str | int]]) -> None:
    attachment_names = {record["name"].casefold() for record in attachment_records(paths)}
    for entry in entries:
        top = PurePosixPath(str(entry["path"])).parts[0]
        if top.casefold() in attachment_names:
            fail(f"target contract collides with preserved local state: {top}")


def materialize_bundle(paths: Paths, target: str, require_entrypoints: bool = True) -> Path:
    validate_commit_id(target)
    final = paths.bundles / target
    entries = tree_entries(paths, target, require_entrypoints=require_entrypoints)
    validate_attachment_collisions(paths, entries)
    tree = git(paths, "rev-parse", f"{target}^{{tree}}", repository=paths.repository).stdout.strip()
    if final.exists():
        try:
            verify_bundle_against_git(paths, final, target, tree, entries)
            update_direct_ref(paths, f"refs/issue-flow/bundles/{target}", target)
            return final
        except Exception:
            if is_activated(paths, target):
                raise
            discard_unactivated_bundle(paths, target)
    staging = paths.bundles / f".staging-{target}-{uuid.uuid4().hex}"
    staging.mkdir()
    created_directories: set[Path] = {staging}
    receipt_files: dict[str, dict[str, str | int]] = {}
    try:
        for entry in entries:
            name = str(entry["path"])
            destination = staging.joinpath(*PurePosixPath(name).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            parent = destination.parent
            while parent != staging.parent:
                created_directories.add(parent)
                if parent == staging:
                    break
                parent = parent.parent
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
            fsync_file(destination)
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
        for directory in sorted(created_directories, key=lambda item: len(item.parts), reverse=True):
            fsync_directory(directory)
        replace_path(staging, final)
        fsync_directory(paths.bundles)
    except Exception:
        remove_tree(staging, ignore_errors=True)
        raise
    try:
        verify_bundle_against_git(paths, final, target, tree, entries)
        update_direct_ref(paths, f"refs/issue-flow/bundles/{target}", target)
    except Exception:
        discard_unactivated_bundle(paths, target)
        raise
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
        target = Path(record["target"])
        if record["kind"] == "file" and target != paths.local / name:
            fail(f"local file attachment escapes its stable store: {target}")
        if record["kind"] == "directory" and (
            target.name != name or target.parent.parent != paths.legacy
        ):
            fail(f"local directory attachment escapes its legacy backup: {target}")
        if target.exists() and (is_pointer(target) or (record["kind"] == "directory") != target.is_dir()):
            fail(f"local attachment target changed type: {target}")
        records.append(record)
    return records


def verify_bundle(
    paths: Paths,
    bundle: Path,
    expected_commit: str | None = None,
    ignored_paths: frozenset[Path] = frozenset(),
) -> dict:
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
    if any(not isinstance(name, str) or not isinstance(metadata, dict) for name, metadata in files.items()):
        fail(f"bundle receipt has malformed file metadata: {bundle}")
    for name, metadata in files.items():
        if (
            not re.fullmatch(r"100(?:644|755)", str(metadata.get("mode", "")))
            or not isinstance(metadata.get("size"), int)
            or metadata["size"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}", str(metadata.get("sha256", "")))
            or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", str(metadata.get("object", "")))
        ):
            fail(f"bundle receipt metadata is malformed for {name}: {bundle}")
    expected = set(files)
    allowed_top = {RECEIPT_FILE, *(record["name"] for record in attachment_records(paths))}
    if paths.config.exists():
        allowed_top.add(CONFIG_FILE)
    actual: set[str] = set()
    stack: list[tuple[Path, PurePosixPath]] = [(bundle, PurePosixPath())]
    while stack:
        directory, prefix = stack.pop()
        for item in os.scandir(directory):
            if Path(item.path) in ignored_paths:
                continue
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
        details = path.stat()
        if details.st_nlink != 1:
            fail(f"tracked bundle file has external hard links: {name}")
        if os.name != "nt":
            actual_mode = stat.S_IMODE(details.st_mode)
            expected_mode = int(str(metadata.get("mode")), 8) & 0o777
            if actual_mode != expected_mode:
                fail(f"tracked bundle mode drifted: {name}: {actual_mode:o} != {expected_mode:o}")
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
    ignored_paths: frozenset[Path] = frozenset(),
) -> dict:
    """Bind a local receipt to authoritative objects, not to hashes it chose itself."""
    receipt = verify_bundle(paths, bundle, target, ignored_paths)
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
    # Runtime junctions intentionally target the stable canonical path. Resolving here would pin
    # them to today's bundle and make the next atomic canonical retarget invisible to that runtime.
    absolute = os.path.abspath(os.fspath(target))
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
        if not kernel32.FlushFileBuffers(handle):
            fail(f"cannot flush activated junction {path} (Windows error {ctypes.get_last_error()})")
    finally:
        kernel32.CloseHandle(handle)


def create_directory_pointer(path: Path, target: Path) -> None:
    if os.name == "nt":
        set_windows_junction(path, target, create=True)
    else:
        os.symlink(target, path, target_is_directory=True)
    fsync_directory(path.parent)


def activate(paths: Paths, bundle: Path) -> None:
    verify_stored_bundle(paths, bundle)
    if bundle.parent.resolve() != paths.bundles.resolve():
        fail(f"activation target escapes the installer bundle store: {bundle}")
    if not path_exists(paths.canonical):
        temporary = paths.skills / f".{SKILL_NAME}.activate-{uuid.uuid4().hex}"
        create_directory_pointer(temporary, bundle)
        replace_path(temporary, paths.canonical)
    elif os.name == "nt":
        if not is_junction(paths.canonical):
            fail(f"the active Windows skill is not an installer-owned junction: {paths.canonical}")
        set_windows_junction(paths.canonical, bundle, create=False)
    else:
        if not paths.canonical.is_symlink():
            fail(f"the active POSIX skill is not an installer-owned symlink: {paths.canonical}")
        temporary = paths.skills / f".{SKILL_NAME}.activate-{uuid.uuid4().hex}"
        os.symlink(bundle, temporary, target_is_directory=True)
        replace_path(temporary, paths.canonical)
    if not same_path(paths.canonical, bundle):
        fail(f"activation read-back did not resolve {paths.canonical} to {bundle}")
    verify_stored_bundle(paths, bundle)
    fsync_directory(paths.skills)
    mark_activated(paths, bundle.name)


def active_bundle(
    paths: Paths,
    verify_policy: bool = True,
    ignored_paths: frozenset[Path] = frozenset(),
) -> tuple[Path, dict]:
    if not is_pointer(paths.canonical):
        fail(f"{paths.canonical} is not an immutable-bundle pointer")
    try:
        bundle = paths.canonical.resolve(strict=True)
    except OSError as error:
        fail(f"active skill pointer is broken: {error}")
    if bundle.parent.resolve() != paths.bundles.resolve():
        fail(f"active skill points outside the installer bundle store: {bundle}")
    receipt = verify_stored_bundle(paths, bundle, ignored_paths)
    verify_attachments(paths, bundle, verify_policy=verify_policy)
    return bundle, receipt


def active_state(
    paths: Paths,
    verify_policy: bool = True,
    ignored_paths: frozenset[Path] = frozenset(),
) -> tuple[Path, dict, dict]:
    bundle, receipt = active_bundle(paths, verify_policy=verify_policy, ignored_paths=ignored_paths)
    if not paths.current.exists():
        fail(f"active bundle has no activation state: {paths.current}")
    state = read_current_state(paths)
    if state["current"] != receipt["commit"]:
        fail(f"current activation state disagrees with active bundle {bundle}: {paths.current}")
    return bundle, receipt, state


def verify_stored_bundle(
    paths: Paths,
    bundle: Path,
    ignored_paths: frozenset[Path] = frozenset(),
) -> dict:
    commit = bundle.name
    validate_commit_id(commit)
    if bundle.parent.resolve() != paths.bundles.resolve():
        fail(f"bundle escapes the installer store: {bundle}")
    entries = tree_entries(paths, commit, require_entrypoints=False)
    tree = git(paths, "rev-parse", f"{commit}^{{tree}}", repository=paths.repository).stdout.strip()
    return verify_bundle_against_git(paths, bundle, commit, tree, entries, ignored_paths)


def create_attachment(path: Path, record: dict[str, str]) -> None:
    target = Path(record["target"])
    if record["kind"] == "directory":
        create_directory_pointer(path, target)
    elif record["kind"] == "file":
        replace_hardlink(path, target)
    else:
        fail(f"unknown local attachment kind: {record['kind']}")


def remove_directory_pointer(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    else:
        path.rmdir()
    fsync_directory(path.parent)


def policy_generation(paths: Paths, content: bytes) -> Path:
    digest = hashlib.sha256(content).hexdigest()
    generation = paths.policies / digest
    if path_exists(generation):
        details = os.lstat(generation)
        if stat.S_ISREG(details.st_mode) and generation.read_bytes() == content:
            if details.st_nlink != known_policy_link_count(paths, generation):
                fail(f"policy generation has an external hard link: {generation}")
            return generation
        if stat.S_ISDIR(details.st_mode):
            fail(f"policy generation path is a directory: {generation}")
    mode = stat.S_IRUSR | (stat.S_IWUSR if os.name == "nt" else 0)
    write_bytes_atomic(generation, content, mode=mode)
    return generation


def current_policy_generation(paths: Paths) -> Path | None:
    if not paths.config.exists():
        return None
    try:
        content = paths.config.read_bytes()
        generation = paths.policies / hashlib.sha256(content).hexdigest()
        config_details = os.lstat(paths.config)
        generation_details = os.lstat(generation) if path_exists(generation) else None
    except OSError as error:
        fail(f"cannot inspect stable operator policy {paths.config}: {error}")
    if (
        not stat.S_ISREG(config_details.st_mode)
        or config_details.st_nlink != 1
        or not generation_details
        or not stat.S_ISREG(generation_details.st_mode)
        or generation.read_bytes() != content
        or generation_details.st_nlink != known_policy_link_count(paths, generation)
    ):
        fail(f"stable operator policy is not an installer-owned immutable generation: {paths.config}")
    return generation


def replace_hardlink(destination: Path, generation: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    os.link(generation, temporary)
    try:
        replace_path(temporary, destination)
        fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def is_regular_hardlink(candidate: Path, target: Path) -> bool:
    try:
        candidate_details = os.lstat(candidate)
        target_details = os.lstat(target)
        return (
            stat.S_ISREG(candidate_details.st_mode)
            and stat.S_ISREG(target_details.st_mode)
            and os.path.samefile(candidate, target)
        )
    except OSError:
        return False


def policy_destinations(paths: Paths) -> list[Path]:
    destinations: list[Path] = []
    active = paths.canonical.resolve(strict=True) if is_pointer(paths.canonical) else None
    inactive: list[Path] = []
    active_destination: Path | None = None
    for candidate in paths.bundles.iterdir():
        if not candidate.is_dir() or is_pointer(candidate) or candidate.name.startswith(".staging-"):
            continue
        destination = candidate / CONFIG_FILE
        if active and candidate.resolve() == active:
            active_destination = destination
        else:
            inactive.append(destination)
    destinations.extend(sorted(inactive))
    if active_destination:
        destinations.append(active_destination)
    return destinations


def known_policy_link_count(paths: Paths, generation: Path) -> int:
    links = 1
    for destination in policy_destinations(paths):
        if is_regular_hardlink(destination, generation):
            links += 1
    return links


def policy_link_temporaries(paths: Paths, generation: Path) -> set[Path]:
    temporaries: set[Path] = set()
    for destination in policy_destinations(paths):
        pattern = f".{destination.name}." + "?" * 32 + ".tmp"
        temporaries.update(
            candidate
            for candidate in destination.parent.glob(pattern)
            if is_regular_hardlink(candidate, generation)
        )
    return temporaries


def stable_policy_temporaries(paths: Paths) -> set[Path]:
    if not path_exists(paths.config):
        return set()
    details = os.lstat(paths.config)
    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        fail(f"stable policy is not a private regular file: {paths.config}")
    content = paths.config.read_bytes()
    generation = paths.policies / hashlib.sha256(content).hexdigest()
    if not path_exists(generation):
        return set()
    temporaries = policy_link_temporaries(paths, generation)
    if not temporaries:
        return set()
    generation_details = os.lstat(generation)
    if (
        not stat.S_ISREG(generation_details.st_mode)
        or generation.read_bytes() != content
        or generation_details.st_nlink != known_policy_link_count(paths, generation) + len(temporaries)
    ):
        fail(f"stable policy has an invalid replacement temporary: {generation}")
    return temporaries


def attachment_link_temporaries(paths: Paths) -> set[Path]:
    authorized: set[Path] = set()
    if not paths.bundles.is_dir() or is_pointer(paths.bundles):
        return authorized
    bundles = [
        bundle
        for bundle in paths.bundles.iterdir()
        if bundle.is_dir() and not is_pointer(bundle) and not bundle.name.startswith(".staging-")
    ]
    for record in attachment_records(paths):
        if record["kind"] != "file":
            continue
        target = Path(record["target"])
        expected_links = 1
        temporaries: set[Path] = set()
        for bundle in bundles:
            destination = bundle / record["name"]
            if is_regular_hardlink(destination, target):
                expected_links += 1
            pattern = f".{record['name']}." + "?" * 32 + ".tmp"
            temporaries.update(
                candidate for candidate in bundle.glob(pattern) if is_regular_hardlink(candidate, target)
            )
        if temporaries:
            details = os.lstat(target)
            if not stat.S_ISREG(details.st_mode) or details.st_nlink != expected_links + len(temporaries):
                fail(f"stable attachment has an invalid replacement temporary: {target}")
            authorized.update(temporaries)
    return authorized


def complete_policy_switch(paths: Paths, generation: Path) -> None:
    for destination in policy_destinations(paths):
        if is_regular_hardlink(destination, generation):
            continue
        replace_hardlink(destination, generation)
    content = generation.read_bytes()
    details = os.lstat(paths.config) if path_exists(paths.config) else None
    if not details or not stat.S_ISREG(details.st_mode) or details.st_nlink != 1 or paths.config.read_bytes() != content:
        write_bytes_atomic(paths.config, content, mode=stat.S_IRUSR | stat.S_IWUSR)


def retained_policy_generation(paths: Paths, edited_destination: Path | None = None) -> Path | None:
    generations: set[Path] = set()
    destinations = policy_destinations(paths)
    stable_content = paths.config.read_bytes() if paths.config.exists() else None
    if stable_content is not None:
        stable_generation = paths.policies / hashlib.sha256(stable_content).hexdigest()
        if path_exists(stable_generation):
            generations.add(stable_generation)
    for destination in destinations:
        if not path_exists(destination):
            continue
        details = os.lstat(destination)
        if not stat.S_ISREG(details.st_mode):
            fail(f"bundle policy is not a regular file: {destination}")
        linked = [candidate for candidate in paths.policies.iterdir() if is_regular_hardlink(destination, candidate)]
        if len(linked) > 1:
            fail(f"bundle policy links multiple generation names: {destination}")
        if linked:
            generations.add(linked[0])
        elif destination != edited_destination:
            fail(f"bundle policy is not linked to an immutable generation: {destination}")
    if len(generations) > 1:
        fail(f"retained bundles disagree about operator policy generations: {sorted(map(str, generations))}")
    if not generations:
        return None
    generation = next(iter(generations))
    if not re.fullmatch(r"[0-9a-f]{64}", generation.name):
        fail(f"retained policy generation has an invalid name: {generation}")
    if hashlib.sha256(generation.read_bytes()).hexdigest() != generation.name:
        if edited_destination is None or not is_regular_hardlink(edited_destination, generation):
            fail(f"retained policy generation content disagrees with its identity: {generation}")
        if stable_content is None or hashlib.sha256(stable_content).hexdigest() != generation.name:
            fail(f"edited visible policy has no unchanged stable predecessor: {edited_destination}")
    if generation.stat().st_nlink != known_policy_link_count(paths, generation):
        fail(f"policy generation has an external hard link: {generation}")
    for destination in destinations:
        if destination == edited_destination:
            details = os.lstat(destination)
            if not stat.S_ISREG(details.st_mode):
                fail(f"edited visible policy is not a regular file: {destination}")
            if not is_regular_hardlink(destination, generation) and details.st_nlink != 1:
                fail(f"edited visible policy has an external hard link: {destination}")
        elif not is_regular_hardlink(destination, generation):
            fail(f"bundle policy is not linked to the retained generation: {destination}")
    return generation


def validate_policy_destinations(
    paths: Paths,
    generation: Path | None = None,
    edited_destination: Path | None = None,
) -> None:
    expected = generation if generation is not None else current_policy_generation(paths)
    for candidate in paths.bundles.iterdir():
        if not candidate.is_dir() or is_pointer(candidate) or candidate.name.startswith(".staging-"):
            continue
        verify_stored_bundle(paths, candidate)
        verify_attachments(paths, candidate, verify_policy=False)
        destination = candidate / CONFIG_FILE
        if expected and destination != edited_destination and not is_regular_hardlink(destination, expected):
            fail(f"bundle policy is not linked to the retained generation: {destination}")
        if expected is None and path_exists(destination):
            fail(f"bundle contains operator policy without a retained generation: {destination}")


def switch_policy(paths: Paths, content: bytes, edited_destination: Path | None = None) -> None:
    previous = retained_policy_generation(paths, edited_destination=edited_destination)
    validate_policy_destinations(paths, previous, edited_destination=edited_destination)
    generation = policy_generation(paths, content)
    write_json(
        paths.policy_transaction,
        {
            "schema": STATE_SCHEMA,
            "generation": generation.name,
            "previous": previous.name if previous else None,
        },
    )
    complete_policy_switch(paths, generation)
    remove_state_file(paths.policy_transaction)
    for candidate in paths.policies.iterdir():
        if candidate != generation and candidate.is_file() and candidate.stat().st_nlink == 1:
            candidate.unlink()
    fsync_directory(paths.policies)


def clear_provisional_policy(paths: Paths) -> None:
    generation = current_policy_generation(paths)
    if generation is None:
        return
    for destination in policy_destinations(paths):
        if not path_exists(destination):
            continue
        if not is_regular_hardlink(destination, generation):
            fail(f"provisional bundle policy is outside stable state: {destination}")
        destination.unlink()
        fsync_directory(destination.parent)
    remove_state_file(paths.config)
    for candidate in paths.policies.iterdir():
        if candidate.is_file() and candidate.stat().st_nlink == 1:
            candidate.unlink()
    fsync_directory(paths.policies)


def recover_policy_transaction(paths: Paths, dry_run: bool = False) -> set[Path]:
    if not paths.policy_transaction.exists():
        return stable_policy_temporaries(paths)
    transaction = read_json(paths.policy_transaction)
    if set(transaction) != {"schema", "generation", "previous"} or transaction.get("schema") != STATE_SCHEMA:
        fail(f"invalid policy transaction shape: {paths.policy_transaction}")
    generation_name = str(transaction.get("generation", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", generation_name):
        fail(f"invalid policy transaction: {paths.policy_transaction}")
    generation = paths.policies / generation_name
    details = os.lstat(generation) if path_exists(generation) else None
    generation_temporaries = policy_link_temporaries(paths, generation)
    if (
        not details
        or not stat.S_ISREG(details.st_mode)
        or hashlib.sha256(generation.read_bytes()).hexdigest() != generation_name
        or details.st_nlink != known_policy_link_count(paths, generation) + len(generation_temporaries)
    ):
        fail(f"policy transaction generation is missing or corrupt: {generation}")
    previous_value = transaction.get("previous")
    previous_name = str(previous_value) if previous_value else None
    if previous_name and not re.fullmatch(r"[0-9a-f]{64}", previous_name):
        fail(f"invalid previous policy generation in {paths.policy_transaction}")
    stable_details = os.lstat(paths.config) if path_exists(paths.config) else None
    if stable_details:
        if not stat.S_ISREG(stable_details.st_mode) or stable_details.st_nlink != 1:
            fail(f"stable policy is not a private regular file during recovery: {paths.config}")
        stable_content = paths.config.read_bytes()
        stable_name = hashlib.sha256(stable_content).hexdigest()
        if stable_name not in {previous_name, generation_name}:
            fail(f"stable policy is outside transaction endpoints: {paths.policy_transaction}")
    elif previous_name is None:
        stable_name = None
    else:
        fail(f"stable policy disappeared during an existing-policy transaction: {paths.config}")
    previous = paths.policies / previous_name if previous_name else None
    previous_temporaries: set[Path] = set()
    if previous:
        previous_details = os.lstat(previous) if path_exists(previous) else None
        if not previous_details or not stat.S_ISREG(previous_details.st_mode):
            fail(f"previous policy transaction generation is missing or invalid: {previous}")
        previous_digest = hashlib.sha256(previous.read_bytes()).hexdigest()
        predecessor_was_edited = previous_digest != previous_name
        previous_content = previous.read_bytes()
        generation_content = generation.read_bytes()
        encoding_only_edit = decode_policy(previous_content, previous) == decode_policy(generation_content, generation)
        if predecessor_was_edited and not (
            stable_name in {previous_name, generation_name}
            and (previous_content == generation_content or encoding_only_edit)
        ):
            fail(f"previous policy generation is corrupt outside the authorized visible edit: {previous}")
        previous_temporaries = policy_link_temporaries(paths, previous)
        if previous_details.st_nlink != known_policy_link_count(paths, previous) + len(previous_temporaries):
            fail(f"previous policy generation has an external hard link: {previous}")
    for destination in policy_destinations(paths):
        if not path_exists(destination) and previous is None:
            continue
        if not (
            is_regular_hardlink(destination, generation)
            or (previous is not None and is_regular_hardlink(destination, previous))
        ):
            fail(f"bundle policy is outside transaction endpoints: {destination}")
    if not dry_run:
        complete_policy_switch(paths, generation)
        remove_state_file(paths.policy_transaction)
        for candidate in paths.policies.iterdir():
            if candidate != generation and candidate.is_file() and candidate.stat().st_nlink == 1:
                candidate.unlink()
        fsync_directory(paths.policies)
    return generation_temporaries | previous_temporaries


def generated_temporaries(
    root: Path,
    recursive: bool = False,
    excluded_names: frozenset[str] = frozenset(),
) -> list[Path]:
    if not root.is_dir() or is_pointer(root):
        return []
    matches: list[Path] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        for item in directory.iterdir():
            if TEMPORARY_NAME.fullmatch(item.name) and item.name not in excluded_names:
                matches.append(item)
            elif recursive and item.is_dir() and not is_pointer(item):
                stack.append(item)
    return matches


def git_ref_locks(paths: Paths) -> list[Path]:
    if not paths.repository.is_dir() or is_pointer(paths.repository):
        return []
    locks = [
        candidate
        for candidate in (paths.repository / "HEAD.lock", paths.repository / "packed-refs.lock")
        if path_exists(candidate)
    ]
    refs = paths.repository / "refs"
    if refs.is_dir() and not is_pointer(refs):
        locks.extend(refs.rglob("*.lock"))
    return locks


def cleanup_git_ref_locks(paths: Paths) -> None:
    for candidate in git_ref_locks(paths):
        details = os.lstat(candidate)
        if is_pointer(candidate) or not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            fail(f"stale Git ref lock is not a private regular file: {candidate}")
        candidate.unlink()
        fsync_directory(candidate.parent)


def abandoned_paths(paths: Paths, strict_attachments: bool = True) -> list[Path]:
    candidates: list[Path] = []
    try:
        attachment_names = frozenset(record["name"] for record in attachment_records(paths))
    except InstallError:
        if strict_attachments:
            raise
        attachment_names = frozenset()
    if paths.bundles.exists():
        candidates.extend(paths.bundles.glob(".staging-*-" + "?" * 32))
        candidates.extend(path for path in paths.bundles.iterdir() if DISCARD_NAME.fullmatch(path.name))
        for bundle in paths.bundles.iterdir():
            if (
                not bundle.is_dir()
                or is_pointer(bundle)
                or bundle.name.startswith(".staging-")
                or DISCARD_NAME.fullmatch(bundle.name)
            ):
                continue
            tracked: set[str] = set()
            receipt = bundle / RECEIPT_FILE
            if receipt.is_file():
                try:
                    files = read_json(receipt).get("files")
                    tracked = set(files) if isinstance(files, dict) else set()
                except InstallError:
                    pass
            candidates.extend(
                item
                for item in bundle.iterdir()
                if (
                    TEMPORARY_NAME.fullmatch(item.name)
                    and item.name not in tracked
                    and item.name not in attachment_names
                )
            )
    if paths.state.exists():
        candidates.extend(paths.state.glob(".repository-" + "?" * 32))
        candidates.extend(paths.state.glob(".fetch-" + "?" * 32))
        candidates.extend(generated_temporaries(paths.state))
    candidates.extend(git_ref_locks(paths))
    candidates.extend(generated_temporaries(paths.policies))
    candidates.extend(generated_temporaries(paths.local, excluded_names=attachment_names))
    candidates.extend(generated_temporaries(paths.repository))
    candidates.extend(generated_temporaries(paths.repository / "objects", recursive=True))
    if paths.skills.exists():
        candidates.extend(paths.skills.glob(f".{SKILL_NAME}.activate-" + "?" * 32))
    for runtime in runtime_paths(paths):
        if runtime.parent.exists():
            candidates.extend(runtime.parent.glob(f".{SKILL_NAME}.runtime-" + "?" * 32))
    return sorted(set(candidates))


def remove_pointer_temporary(paths: Paths, candidate: Path, activation: bool, dry_run: bool = False) -> None:
    if is_pointer(candidate):
        if activation:
            try:
                target = candidate.resolve(strict=True)
            except OSError as error:
                fail(f"activation temporary is broken: {candidate}: {error}")
            if target.parent.resolve() != paths.bundles.resolve():
                fail(f"activation temporary points outside the bundle store: {candidate}")
            validate_commit_id(target.name)
            receipt = read_json(target / RECEIPT_FILE)
            transaction = read_json(paths.transaction) if paths.transaction.exists() else {}
            transaction_endpoint = transaction.get("schema") == STATE_SCHEMA and target.name in {
                transaction.get("previous"),
                transaction.get("target"),
            }
            if (
                receipt.get("schema") != STATE_SCHEMA
                or receipt.get("repository") != REPOSITORY_URL
                or receipt.get("commit") != target.name
                or not (is_activated(paths, target.name) or transaction_endpoint)
            ):
                fail(f"activation temporary has no installer-owned target: {candidate}")
        elif not pointer_targets(candidate, paths.canonical):
            fail(f"runtime temporary does not target the canonical skill: {candidate}")
        if not dry_run:
            remove_directory_pointer(candidate)
        return
    if os.name == "nt" and candidate.is_dir():
        try:
            next(candidate.iterdir())
        except StopIteration:
            if not dry_run:
                candidate.rmdir()
                fsync_directory(candidate.parent)
            return
    fail(f"refusing to delete an unowned installer-shaped temporary: {candidate}")


def cleanup_pointer_temporaries(paths: Paths, activation: bool = True, runtime: bool = True) -> None:
    if activation and paths.skills.exists():
        for candidate in paths.skills.glob(f".{SKILL_NAME}.activate-" + "?" * 32):
            remove_pointer_temporary(paths, candidate, activation=True)
    if runtime:
        for runtime_path in runtime_paths(paths):
            if not runtime_path.parent.exists():
                continue
            for candidate in runtime_path.parent.glob(f".{SKILL_NAME}.runtime-" + "?" * 32):
                remove_pointer_temporary(paths, candidate, activation=False)


def cleanup_abandoned(paths: Paths) -> None:
    cleanup_git_ref_locks(paths)
    cleanup_discard_tombstones(paths)
    cleanup_pointer_temporaries(paths)
    for candidate in abandoned_paths(paths):
        if is_pointer(candidate):
            remove_directory_pointer(candidate)
        elif candidate.is_dir():
            remove_tree(candidate)
        else:
            candidate.unlink(missing_ok=True)
            fsync_directory(candidate.parent)


def validate_abandoned_paths(
    paths: Paths,
    candidates: list[Path],
    hardlink_temporaries: frozenset[Path] = frozenset(),
) -> None:
    git_locks = set(git_ref_locks(paths))
    runtime_parents = {runtime.parent for runtime in runtime_paths(paths)}
    for candidate in candidates:
        if candidate.parent == paths.skills and candidate.name.startswith(f".{SKILL_NAME}.activate-"):
            remove_pointer_temporary(paths, candidate, activation=True, dry_run=True)
            continue
        if candidate.parent in runtime_parents and candidate.name.startswith(f".{SKILL_NAME}.runtime-"):
            remove_pointer_temporary(paths, candidate, activation=False, dry_run=True)
            continue
        details = os.lstat(candidate)
        if candidate in hardlink_temporaries:
            if is_pointer(candidate) or not stat.S_ISREG(details.st_mode):
                fail(f"hard-link replacement temporary is not a regular file: {candidate}")
            continue
        if candidate in git_locks:
            if is_pointer(candidate) or not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
                fail(f"stale Git ref lock is not a private regular file: {candidate}")
            continue
        if DISCARD_NAME.fullmatch(candidate.name):
            commit = DISCARD_NAME.fullmatch(candidate.name).group(1)
            if is_pointer(candidate) or not candidate.is_dir() or is_activated(paths, commit):
                fail(f"bundle discard tombstone has no unactivated owned target: {candidate}")
            continue
        if is_pointer(candidate):
            fail(f"abandoned installer path may not redirect cleanup: {candidate}")
        if stat.S_ISREG(details.st_mode) and details.st_nlink != 1:
            fail(f"abandoned installer file has an external hard link: {candidate}")
        if not (stat.S_ISREG(details.st_mode) or stat.S_ISDIR(details.st_mode)):
            fail(f"abandoned installer path has an unsupported type: {candidate}")


def attach_local(paths: Paths, bundle: Path, allow_dangling: bool = False) -> None:
    generation = current_policy_generation(paths)
    if generation:
        destination = bundle / CONFIG_FILE
        if not destination.exists():
            replace_hardlink(destination, generation)
        elif not is_regular_hardlink(destination, generation):
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
            fsync_directory(bundle)
        if record["kind"] == "directory":
            if not is_pointer(destination):
                fail(f"local directory attachment is not a link: {destination}")
            if target.exists() and not same_path(destination, target):
                fail(f"local directory attachment targets the wrong path: {destination}")
        elif not is_regular_hardlink(destination, target):
            fail(f"local file attachment targets the wrong path: {destination}")
        if not allow_dangling and not target.exists():
            fail(f"local attachment target is missing: {target}")


def verify_attachments(paths: Paths, bundle: Path, verify_policy: bool = True) -> None:
    if verify_policy:
        generation = current_policy_generation(paths)
        if generation:
            candidate = bundle / CONFIG_FILE
            if not is_regular_hardlink(candidate, generation):
                fail(f"active bundle does not expose the stable operator policy: {candidate}")
        elif path_exists(bundle / CONFIG_FILE):
            fail(f"bundle contains operator policy without stable installer state: {bundle / CONFIG_FILE}")
    for record in attachment_records(paths):
        candidate = bundle / record["name"]
        target = Path(record["target"])
        if record["kind"] == "directory":
            if not is_pointer(candidate) or not same_path(candidate, target):
                fail(f"local directory attachment drifted: {candidate}")
        elif not is_regular_hardlink(candidate, target):
            fail(f"local file attachment drifted: {candidate}")


def validate_runtime_ancestors(paths: Paths) -> None:
    for base in (paths.home / ".claude" / "skills", paths.home / ".codex" / "skills"):
        validate_real_ancestors(paths.home, base)


def validate_runtime_paths(paths: Paths) -> None:
    canonical_target = paths.canonical.resolve(strict=True) if path_exists(paths.canonical) else None
    validate_runtime_ancestors(paths)
    for base in (paths.home / ".claude" / "skills", paths.home / ".codex" / "skills"):
        runtime = base / SKILL_NAME
        if not path_exists(runtime):
            continue
        if canonical_target is None:
            fail(f"runtime skill exists before canonical installation; move it aside first: {runtime}")
        if not is_pointer(runtime):
            fail(f"runtime skill is an independent stale copy; move it aside before sync: {runtime}")
        if not pointer_targets(runtime, paths.canonical):
            fail(f"runtime pointer bypasses the stable canonical path: {runtime}")
        if runtime.resolve(strict=True) != canonical_target:
            fail(f"runtime skill does not target the canonical active bundle: {runtime}")


def pointer_targets(pointer: Path, expected: Path) -> bool:
    try:
        raw = os.readlink(pointer)
    except OSError:
        return False
    if os.name == "nt":
        if raw.startswith("\\\\?\\UNC\\"):
            raw = "\\\\" + raw[8:]
        elif raw.startswith("\\??\\UNC\\"):
            raw = "\\\\" + raw[8:]
        elif raw.startswith(("\\\\?\\", "\\??\\")):
            raw = raw[4:]
    target = Path(raw)
    if not target.is_absolute():
        target = pointer.parent / target
    target_alias = Path(os.path.realpath(target.parent)) / target.name
    expected_alias = Path(os.path.realpath(expected.parent)) / expected.name
    return os.path.normcase(os.path.abspath(target_alias)) == os.path.normcase(os.path.abspath(expected_alias))


def legacy_git(
    paths: Paths,
    *arguments: str,
    checkout: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    return git(paths, *arguments, cwd=checkout or paths.canonical, check=check)


def validate_legacy_repository(root: Path) -> None:
    repository = root / ".git"
    if is_pointer(repository) or not repository.is_dir() or path_exists(repository / "commondir"):
        fail(f"legacy migration requires a standalone Git directory: {repository}")
    for directory in (repository / "objects", repository / "refs"):
        if is_pointer(directory) or not directory.is_dir():
            fail(f"legacy Git control directory is missing or linked: {directory}")
    for forbidden in (
        repository / "objects" / "info" / "alternates",
        repository / "objects" / "info" / "http-alternates",
    ):
        if path_exists(forbidden):
            fail(f"legacy migration refuses alternate object storage: {forbidden}")
    config = repository / "config"
    details = os.lstat(config) if path_exists(config) else None
    if not details or is_pointer(config) or not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        fail(f"legacy Git config is not a private regular file: {config}")
    parser = configparser.RawConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(config.read_text(encoding="utf-8"))
    except (configparser.Error, UnicodeError, OSError) as error:
        fail(f"legacy Git config is invalid: {error}")
    if any(section.casefold() == "include" or section.casefold().startswith('includeif "') for section in parser.sections()):
        fail("legacy migration refuses Git config includes that can hide executable authority")
    worktree_config = repository / "config.worktree"
    extension_sections = [section for section in parser.sections() if section.casefold() == "extensions"]
    if path_exists(worktree_config) or any(
        parser.getboolean(section, "worktreeConfig", fallback=False) for section in extension_sections
    ):
        fail("legacy migration refuses worktree-scoped Git authority")
    if any(parser.has_option(section, "partialclone") for section in extension_sections):
        fail("legacy migration refuses a partial-clone object authority")
    for section in parser.sections():
        if section.casefold().startswith('remote "') and (
            parser.getboolean(section, "promisor", fallback=False)
            or parser.has_option(section, "partialclonefilter")
        ):
            fail("legacy migration refuses a promisor remote that can execute during object reads")


def legacy_tracked_files(paths: Paths, root: Path, current: str) -> set[str]:
    tree_result = git(paths, "ls-tree", "-r", "-z", "--full-tree", current, cwd=root, text=False)
    tree: dict[str, tuple[str, str]] = {}
    for record in tree_result.stdout.split(b"\0"):
        if not record:
            continue
        metadata, raw_name = record.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split(" ", 2)
        name = raw_name.decode("utf-8", "strict")
        if kind != "blob" or mode not in ("100644", "100755"):
            fail(f"legacy tracked entry is not a regular runtime file: {mode} {kind} {name}")
        tree[name] = (mode, object_id)

    index_result = git(paths, "ls-files", "--stage", "-z", cwd=root, text=False)
    index: dict[str, tuple[str, str]] = {}
    for record in index_result.stdout.split(b"\0"):
        if not record:
            continue
        metadata, raw_name = record.split(b"\t", 1)
        mode, object_id, stage = metadata.decode("ascii").split(" ", 2)
        name = raw_name.decode("utf-8", "strict")
        if stage != "0":
            fail(f"legacy index has an unresolved stage for {name}")
        index[name] = (mode, object_id)
    if index != tree:
        fail("legacy migration requires its index to match HEAD exactly")

    for name, (_, expected_object) in tree.items():
        path = root.joinpath(*PurePosixPath(name).parts)
        if is_pointer(path) or not path.is_file():
            fail(f"legacy tracked file changed type: {path}")
        content = path.read_bytes()
        header = f"blob {len(content)}\0".encode("ascii")
        algorithm = hashlib.sha1 if len(expected_object) == 40 else hashlib.sha256
        if algorithm(header + content).hexdigest() != expected_object:
            fail(f"legacy migration requires clean tracked bytes: {name}")
        if os.name != "nt":
            expected_executable = bool(int(tree[name][0], 8) & stat.S_IXUSR)
            actual_executable = bool(path.stat().st_mode & stat.S_IXUSR)
            if actual_executable != expected_executable:
                fail(f"legacy migration requires clean tracked executable mode: {name}")
    return set(tree)


def inspect_legacy(
    paths: Paths,
    target_entries: list[dict[str, str | int]],
    checkout: Path | None = None,
) -> dict:
    root = checkout or paths.canonical
    if is_pointer(root) or not (root / ".git").exists():
        fail(f"{root} is not a real legacy Git clone")
    validate_legacy_repository(root)
    top = Path(legacy_git(paths, "rev-parse", "--show-toplevel", checkout=root).stdout.strip()).resolve()
    if top != root.resolve():
        fail(f"legacy checkout root is {top}, expected {root}")
    branch = legacy_git(paths, "symbolic-ref", "--quiet", "--short", "HEAD", checkout=root).stdout.strip()
    if branch != "main":
        fail(f"legacy migration requires main, current branch is {branch}")
    origin = legacy_git(paths, "config", "--local", "--get", "remote.origin.url", checkout=root).stdout.strip()
    if normalized_repository_url(origin) != normalized_repository_url(REPOSITORY_URL):
        fail("legacy origin is not the canonical repository; its value is redacted because URLs can contain credentials")
    current = legacy_git(paths, "rev-parse", "HEAD^{commit}", checkout=root).stdout.strip()
    tracked = legacy_tracked_files(paths, root, current)
    tracked_top_names = {PurePosixPath(name).parts[0] for name in tracked}
    tracked_top = {name.casefold(): name for name in tracked_top_names}
    if len(tracked_top) != len(tracked_top_names):
        fail("legacy tracked tree has a case-only top-level collision")
    tracked_directory_names = {
        parent.as_posix()
        for name in tracked
        for parent in PurePosixPath(name).parents
        if parent.parts
    }
    tracked_directories = {
        parent.as_posix().casefold(): parent.as_posix()
        for name in tracked
        for parent in PurePosixPath(name).parents
        if parent.parts
    }
    if len(tracked_directories) != len(tracked_directory_names):
        fail("legacy tracked tree has a case-only directory collision")
    stack = [
        candidate
        for name in tracked_top_names
        if (candidate := root / name).is_dir() and not is_pointer(candidate)
    ]
    while stack:
        directory = stack.pop()
        for item in os.scandir(directory):
            candidate = Path(item.path)
            if item.is_symlink() or is_junction(candidate):
                relative = candidate.relative_to(root).as_posix()
                fail(f"untracked link nested inside a tracked runtime directory cannot be migrated safely: {relative}")
            if not item.is_dir(follow_symlinks=False):
                continue
            relative = candidate.relative_to(root).as_posix()
            tracked_spelling = tracked_directories.get(relative.casefold())
            if tracked_spelling is None:
                fail(f"untracked state nested inside a tracked runtime directory cannot be migrated safely: {relative}/")
            if tracked_spelling != relative:
                fail(f"case-only directory collision inside tracked legacy state: {tracked_spelling!r} and {relative!r}")
            stack.append(candidate)
    target_top = {PurePosixPath(str(entry["path"])).parts[0].casefold() for entry in target_entries}
    local_names: set[str] = set()
    for arguments in (
        ("ls-files", "--others", "--exclude-standard", "-z"),
        ("ls-files", "--others", "--ignored", "--exclude-standard", "-z"),
    ):
        for name in legacy_git(paths, *arguments, checkout=root).stdout.split("\0"):
            if not name:
                continue
            first = PurePosixPath(name).parts[0]
            if first.casefold() in tracked_top:
                fail(f"untracked state nested inside a tracked runtime directory cannot be migrated safely: {name}")
            local_names.add(first)
    local_names.discard(CONFIG_FILE)
    for candidate in root.iterdir():
        if candidate.name == ".git" or not candidate.is_dir():
            continue
        tracked_spelling = tracked_top.get(candidate.name.casefold())
        if tracked_spelling is None:
            local_names.add(candidate.name)
        elif tracked_spelling != candidate.name:
            fail(f"case-only top-level collision in legacy state: {tracked_spelling!r} and {candidate.name!r}")
    for name in local_names:
        source = root / name
        if name.casefold() in {CONFIG_FILE.casefold(), RECEIPT_FILE.casefold()}:
            fail(f"local state uses an installer-reserved name: {source}")
        if name.casefold() in target_top:
            fail(f"local state collides with the target contract tree: {name}")
        if is_pointer(source) or not (source.is_file() or source.is_dir()):
            fail(f"local state must be a regular top-level file or directory: {source}")
        if os.name == "nt" and source.is_file() and not (source.stat().st_mode & stat.S_IWUSR):
            fail(f"read-only local files cannot be migrated safely on Windows: {source}")
    config = root / CONFIG_FILE
    if path_exists(config):
        details = os.lstat(config)
        if is_pointer(config) or not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            fail(f"legacy operator policy must be a private regular file: {config}")
    return {"commit": current, "local_names": sorted(local_names)}


def ensure_ancestor(paths: Paths, old: str, target: str) -> None:
    validate_commit_id(old)
    validate_commit_id(target)
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


def validate_commit_id(value: str) -> None:
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value):
        fail(f"invalid Git commit identity in installer state: {value!r}")


def normalized_repository_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if (
        parsed.scheme.casefold() == "https"
        and parsed.netloc.casefold() == "github.com"
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    ):
        path = parsed.path.rstrip("/")
        if path.casefold().endswith(".git"):
            path = path[:-4]
        return f"https://github.com{path}"
    return value.rstrip("/")


def activation_ref(commit: str) -> str:
    validate_commit_id(commit)
    return f"refs/issue-flow/activated/{commit}"


def mark_activated(paths: Paths, commit: str) -> None:
    update_direct_ref(paths, activation_ref(commit), commit)


def is_activated(paths: Paths, commit: str) -> bool:
    return direct_ref_target(paths, activation_ref(commit)) == commit


def require_activated(paths: Paths, commit: str) -> None:
    if not is_activated(paths, commit):
        fail(f"bundle has no completed activation provenance: {commit}")


def read_current_state(paths: Paths) -> dict:
    state = read_json(paths.current)
    if state.get("schema") != STATE_SCHEMA or state.get("repository") != REPOSITORY_URL:
        fail(f"current activation state has the wrong schema or repository: {paths.current}")
    current = str(state.get("current", ""))
    validate_commit_id(current)
    require_activated(paths, current)
    previous = state.get("previous")
    if previous is not None:
        previous = str(previous)
        validate_commit_id(previous)
        require_activated(paths, previous)
    return state


def record_current(paths: Paths, current: str, previous: str | None) -> None:
    validate_commit_id(current)
    require_activated(paths, current)
    if previous is not None:
        validate_commit_id(previous)
        require_activated(paths, previous)
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


def reconcile_retained_state(paths: Paths) -> None:
    state = read_current_state(paths)
    active, receipt = active_bundle(paths)
    if state["current"] != receipt["commit"]:
        fail(f"refusing retention reconciliation: {paths.current} disagrees with active bundle {active}")
    for bundle in paths.bundles.iterdir():
        if bundle.name.startswith(".staging-"):
            continue
        validate_commit_id(bundle.name)
        require_activated(paths, bundle.name)
        update_direct_ref(paths, f"refs/issue-flow/bundles/{bundle.name}", bundle.name)
    current_generation = current_policy_generation(paths)
    for generation in paths.policies.iterdir():
        if current_generation and os.path.samefile(generation, current_generation):
            continue
        if generation.is_file() and generation.stat().st_nlink == 1:
            generation.unlink()


def remove_bundle_ref(paths: Paths, commit: str) -> None:
    reference = f"refs/issue-flow/bundles/{commit}"
    target = direct_ref_target(paths, reference)
    if target is None:
        return
    if target != commit:
        fail(f"materialization ref disagrees with bundle identity: {reference}")
    git(
        paths,
        "update-ref",
        "--no-deref",
        "-d",
        reference,
        target,
        repository=paths.repository,
    )


def finish_discard_tombstone(paths: Paths, tombstone: Path, commit: str) -> None:
    if is_activated(paths, commit):
        fail(f"refusing to discard an activated bundle: {commit}")
    remove_bundle_ref(paths, commit)
    if path_exists(tombstone):
        if is_pointer(tombstone) or not tombstone.is_dir():
            fail(f"bundle discard tombstone changed type: {tombstone}")
        remove_tree(tombstone)
        fsync_directory(paths.bundles)


def cleanup_discard_tombstones(paths: Paths) -> None:
    if not paths.bundles.exists():
        return
    for tombstone in paths.bundles.iterdir():
        match = DISCARD_NAME.fullmatch(tombstone.name)
        if match:
            finish_discard_tombstone(paths, tombstone, match.group(1))


def discard_unactivated_bundle(paths: Paths, commit: str) -> None:
    validate_commit_id(commit)
    if is_activated(paths, commit):
        return
    bundle = paths.bundles / commit
    tombstone = paths.bundles / f".discard-{commit}-{uuid.uuid4().hex}"
    if path_exists(bundle):
        if is_pointer(bundle) or not bundle.is_dir():
            fail(f"unactivated bundle changed type before discard: {bundle}")
        replace_path(bundle, tombstone)
        fsync_directory(paths.bundles)
    finish_discard_tombstone(paths, tombstone, commit)


def discard_all_unactivated_bundles(paths: Paths) -> None:
    for bundle in list(paths.bundles.iterdir()):
        if bundle.name.startswith(".staging-") or DISCARD_NAME.fullmatch(bundle.name):
            continue
        validate_commit_id(bundle.name)
        if not is_activated(paths, bundle.name):
            discard_unactivated_bundle(paths, bundle.name)
    result = git(
        paths,
        "for-each-ref",
        "--format=%(refname)",
        "refs/issue-flow/bundles",
        repository=paths.repository,
    )
    for reference in result.stdout.splitlines():
        commit = reference.rsplit("/", 1)[-1]
        validate_commit_id(commit)
        if not is_activated(paths, commit) and not (paths.bundles / commit).exists():
            remove_bundle_ref(paths, commit)


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
            content = source.read_bytes()
            if destination.exists():
                if destination.read_bytes() != content:
                    fail(f"stable local attachment conflicts with legacy bytes: {destination}")
            else:
                write_bytes_atomic(destination, content, mode=stat.S_IMODE(source.stat().st_mode))
            records.append({"kind": "file", "name": name, "target": str(destination)})
    write_json(paths.attachments, {"schema": STATE_SCHEMA, "entries": records})
    for bundle in bundles:
        attach_local(paths, bundle, allow_dangling=True)


def migrate_legacy(paths: Paths, target: str, target_entries: list[dict[str, str | int]]) -> None:
    legacy = inspect_legacy(paths, target_entries)
    current = str(legacy["commit"])
    ensure_ancestor(paths, current, target)
    target_bundle = materialize_bundle(paths, target)
    config: bytes | None = None
    if (paths.canonical / CONFIG_FILE).exists():
        config = (paths.canonical / CONFIG_FILE).read_bytes()
        if paths.config.exists() and paths.config.read_bytes() != config:
            if paths.current.exists() or paths.transaction.exists():
                fail(f"stable operator policy conflicts with the legacy bytes at {paths.canonical / CONFIG_FILE}")
            # State without activation provenance is provisional from an interrupted migration.
            attach_local(paths, target_bundle, allow_dangling=True)
            switch_policy(paths, config)
        if not paths.config.exists():
            policy_generation(paths, config)
            write_bytes_atomic(paths.config, config, mode=stat.S_IRUSR | stat.S_IWUSR)
    elif paths.config.exists():
        if paths.current.exists() or paths.transaction.exists():
            fail("stable operator policy exists while the legacy checkout uses portable defaults")
        clear_provisional_policy(paths)
    backup = paths.legacy / f"{current}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    prepare_legacy_local_state(
        paths,
        backup,
        list(legacy["local_names"]),
        [target_bundle],
    )
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
        replace_path(paths.canonical, backup)
        if config is not None:
            moved_config = (backup / CONFIG_FILE).read_bytes()
            if moved_config != config:
                # v1.11 has no installer lock. Re-adopt a config write that won the race before
                # the clone move instead of silently activating the earlier snapshot.
                switch_policy(paths, moved_config)
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
        record_current(paths, target, None)
        remove_state_file(paths.transaction, missing_ok=True)
    except Exception:
        if not path_exists(paths.canonical) and backup.exists():
            replace_path(backup, paths.canonical)
        raise


def recover_transaction(
    paths: Paths,
    dry_run: bool = False,
    ignored_paths: frozenset[Path] = frozenset(),
    verify_policy: bool = True,
) -> None:
    if not paths.transaction.exists():
        return
    transaction = read_json(paths.transaction)
    if transaction.get("schema") != STATE_SCHEMA:
        fail(f"invalid activation transaction schema: {paths.transaction}")
    phase = transaction.get("phase")
    if phase not in ("prepared", "moved"):
        fail(f"invalid activation transaction phase in {paths.transaction}: {phase!r}")
    previous_value = transaction.get("previous")
    previous = str(previous_value) if previous_value else None
    target = str(transaction.get("target", ""))
    if previous:
        validate_commit_id(previous)
    validate_commit_id(target)
    prior_previous_value = transaction.get("prior_previous")
    prior_previous = str(prior_previous_value) if prior_previous_value else None
    if prior_previous:
        validate_commit_id(prior_previous)
    backup_value = transaction.get("backup")
    backup = Path(str(backup_value)) if backup_value else None
    if backup and (not backup.is_absolute() or backup.parent.resolve() != paths.legacy.resolve()):
        fail(f"activation transaction backup escapes the legacy store: {backup}")
    if backup and backup.exists() and (is_pointer(backup) or not backup.is_dir()):
        fail(f"activation transaction backup is not a real legacy directory: {backup}")
    if is_pointer(paths.canonical):
        _, receipt = active_bundle(paths, verify_policy=verify_policy, ignored_paths=ignored_paths)
        current = str(receipt["commit"])
        if current == target:
            # Activation may have switched the pointer immediately before interruption.
            if dry_run:
                activated = direct_ref_target(paths, activation_ref(target))
                if activated is not None and activated != target:
                    fail(f"activation ref for {target} resolves to conflicting commit {activated}")
                if backup is None and previous is not None and direct_ref_target(paths, activation_ref(previous)) != previous:
                    fail(f"previous activation ref is missing or conflicting: {previous}")
            else:
                mark_activated(paths, target)
                record_current(paths, target, None if backup else previous)
        elif previous and current == previous:
            require_activated(paths, previous)
            if not dry_run:
                record_current(paths, previous, prior_previous)
                discard_unactivated_bundle(paths, target)
        else:
            fail(f"active bundle {current} is outside transaction endpoints {previous} -> {target}")
        if not dry_run:
            remove_state_file(paths.transaction)
        return
    if path_exists(paths.canonical):
        legacy_keys = {"schema", "phase", "backup", "previous", "target"}
        if set(transaction) != legacy_keys or previous is None or backup is None:
            fail(f"non-pointer recovery requires an exact legacy migration journal: {paths.transaction}")
        if not (paths.canonical / ".git").exists() or backup.exists():
            fail(f"activation journal cannot authorize non-pointer canonical path: {paths.canonical}")
        legacy = inspect_legacy(paths, tree_entries(paths, target))
        if legacy["commit"] != previous:
            fail(f"legacy canonical path is outside transaction predecessor {previous}: {legacy['commit']}")
        if not dry_run:
            discard_unactivated_bundle(paths, target)
            remove_state_file(paths.transaction)
        return
    if backup and backup.exists():
        legacy = inspect_legacy(paths, tree_entries(paths, target), checkout=backup)
        if legacy["commit"] != previous:
            fail(f"legacy backup is outside transaction predecessor {previous}: {legacy['commit']}")
        if not dry_run:
            replace_path(backup, paths.canonical)
            discard_unactivated_bundle(paths, target)
            remove_state_file(paths.transaction)
        return
    previous_bundle = paths.bundles / previous if previous else None
    if previous_bundle and previous_bundle.exists():
        require_activated(paths, previous)
        verify_stored_bundle(paths, previous_bundle, ignored_paths)
        if not dry_run:
            attach_local(paths, previous_bundle)
            activate(paths, previous_bundle)
            record_current(paths, previous, prior_previous)
            discard_unactivated_bundle(paths, target)
            remove_state_file(paths.transaction)
        return
    target_bundle = paths.bundles / target
    if previous is None and target_bundle.exists():
        verify_stored_bundle(paths, target_bundle, ignored_paths)
        if dry_run:
            activated = direct_ref_target(paths, activation_ref(target))
            if activated is not None and activated != target:
                fail(f"activation ref for {target} resolves to conflicting commit {activated}")
        else:
            attach_local(paths, target_bundle)
            activate(paths, target_bundle)
            record_current(paths, target, None)
            remove_state_file(paths.transaction)
        return
    fail(f"incomplete activation cannot find a verified recovery bundle: {paths.transaction}")


def require_layout_provenance(paths: Paths) -> None:
    if is_pointer(paths.canonical):
        return
    evidence: list[str] = []
    if paths.current.exists():
        evidence.append(paths.current.name)
    if paths.repository.is_dir():
        result = git(
            paths,
            "for-each-ref",
            "--format=%(refname)",
            "refs/issue-flow/activated",
            repository=paths.repository,
        )
        activated = result.stdout.splitlines()
        if activated:
            evidence.append(f"{len(activated)} activation ref(s)")
    if evidence:
        kind = "absent" if not path_exists(paths.canonical) else "not an immutable pointer"
        fail(
            f"canonical layout is {kind} while durable activation state exists "
            f"({', '.join(evidence)}); recovery requires an activation journal"
        )


def sync_versioned(paths: Paths, target: str) -> None:
    bundle, receipt, state = active_state(paths)
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
        {
            "schema": STATE_SCHEMA,
            "phase": "prepared",
            "previous": current,
            "target": target,
            "prior_previous": state.get("previous"),
        },
    )
    activate(paths, target_bundle)
    verify_attachments(paths, target_bundle)
    validate_runtime_paths(paths)
    record_current(paths, target, current)
    remove_state_file(paths.transaction)
    print(f"synced  complete Git tree {current} -> {target}")


def transient_paths(root: Path) -> Paths:
    return Paths.for_home(root)


def dry_run_sync(paths: Paths, announce: bool = True) -> tuple[str, str]:
    current: str | None = None
    template = ""
    require_layout_provenance(paths)
    validate_runtime_paths(paths)
    if path_exists(paths.canonical):
        if is_pointer(paths.canonical):
            _, receipt, _ = active_state(paths)
            current = str(receipt["commit"])
        else:
            # Target entries are validated in the transient object store below.
            validate_legacy_repository(paths.canonical)
            current = legacy_git(paths, "rev-parse", "HEAD^{commit}").stdout.strip()
    with tempfile.TemporaryDirectory(prefix="issue-flow-dry-run-") as temporary:
        dry = transient_paths(Path(temporary))
        initialize_state(dry)
        target = fetch_target(dry)
        try:
            entries = tree_entries(dry, target)
            validate_attachment_collisions(paths, entries)
            target_bundle = materialize_bundle(dry, target)
            template = (target_bundle / "SKILL.md").read_text(encoding="utf-8")
            if current:
                ensure_ancestor(dry, current, target)
            if path_exists(paths.canonical) and not is_pointer(paths.canonical):
                inspect_legacy(paths, entries)
        finally:
            finish_target(dry, target, keep=False)
    if announce:
        print(f"would   activate complete Git tree {current or '<absent>'} -> {target}")
    return target, template


def sync(paths: Paths, dry_run: bool, expected_target: str | None = None) -> None:
    if dry_run:
        dry_run_sync(paths)
        return
    ensure_real_directory(paths.skills)
    with InstallerLock(paths):
        initialize_directories(paths)
        initialize_repository(paths)
        cleanup_abandoned(paths)
        recover_policy_transaction(paths)
        recover_transaction(paths)
        require_layout_provenance(paths)
        remove_incoming_ref(paths)
        if is_pointer(paths.canonical):
            active_state(paths)
        discard_all_unactivated_bundles(paths)
        validate_runtime_paths(paths)
        target = fetch_target(paths)
        keep = False
        try:
            if expected_target is not None and target != expected_target:
                fail(f"canonical main changed after preflight: {expected_target} -> {target}; retry config")
            entries = tree_entries(paths, target)
            materialize_bundle(paths, target)
            if not path_exists(paths.canonical):
                target_bundle = paths.bundles / target
                attach_local(paths, target_bundle)
                write_json(
                    paths.transaction,
                    {
                        "schema": STATE_SCHEMA,
                        "phase": "prepared",
                        "previous": None,
                        "target": target,
                        "prior_previous": None,
                    },
                )
                activate(paths, target_bundle)
                record_current(paths, target, None)
                remove_state_file(paths.transaction)
                print(f"installed complete Git tree at {target}")
            elif is_pointer(paths.canonical):
                sync_versioned(paths, target)
            else:
                migrate_legacy(paths, target, entries)
                print(f"migrated legacy clone to complete Git tree at {target}")
            keep = True
        finally:
            if not keep and not paths.transaction.exists():
                discard_unactivated_bundle(paths, target)
            finish_target(paths, target, keep)
        if keep:
            reconcile_retained_state(paths)


def ensure_layout(paths: Paths, dry_run: bool) -> None:
    if path_exists(paths.canonical) and is_pointer(paths.canonical):
        active_state(paths)
        return
    sync(paths, dry_run)


def runtime_paths(paths: Paths) -> tuple[Path, Path]:
    return (
        paths.home / ".claude" / "skills" / SKILL_NAME,
        paths.home / ".codex" / "skills" / SKILL_NAME,
    )


def install_runtime_links(paths: Paths, dry_run: bool) -> None:
    if dry_run or (
        not (path_exists(paths.canonical) and is_pointer(paths.canonical))
        and not paths.transaction.exists()
    ):
        validate_runtime_paths(paths)
    if dry_run or not (path_exists(paths.canonical) and is_pointer(paths.canonical)):
        ensure_layout(paths, dry_run)
    if dry_run and (not path_exists(paths.canonical) or not is_pointer(paths.canonical)):
        for runtime in runtime_paths(paths):
            if runtime.parent.parent.exists():
                print(f"would   link {runtime} -> {paths.canonical} after bundle activation")
        return
    if not dry_run:
        ensure_real_directory(paths.skills)
    context = nullcontext() if dry_run else InstallerLock(paths)
    with context:
        if not dry_run:
            initialize_directories(paths)
            initialize_repository(paths)
            cleanup_abandoned(paths)
            recover_policy_transaction(paths)
            recover_transaction(paths)
            remove_incoming_ref(paths)
            if is_pointer(paths.canonical):
                active_state(paths)
            discard_all_unactivated_bundles(paths)
            validate_runtime_paths(paths)
        active_state(paths)
        for runtime in runtime_paths(paths):
            if not runtime.parent.parent.exists():
                continue
            if path_exists(runtime):
                if (
                    is_pointer(runtime)
                    and pointer_targets(runtime, paths.canonical)
                    and runtime.resolve(strict=True) == paths.canonical.resolve(strict=True)
                ):
                    print(f"ok      {runtime} already targets the active bundle")
                    continue
                fail(f"runtime skill exists independently; move it aside before linking: {runtime}")
            if dry_run:
                print(f"would   link {runtime} -> {paths.canonical}")
                continue
            if not runtime.parent.exists():
                ensure_real_directory(runtime.parent)
            if os.name == "nt":
                temporary = runtime.parent / f".{SKILL_NAME}.runtime-{uuid.uuid4().hex}"
                create_directory_pointer(temporary, paths.canonical)
                replace_path(temporary, runtime)
            else:
                create_directory_pointer(runtime, paths.canonical)
            if runtime.resolve(strict=True) != paths.canonical.resolve(strict=True):
                fail(f"runtime link read-back failed: {runtime}")
            print(f"linked  {runtime} -> {paths.canonical}")


def remove_runtime_links(paths: Paths, dry_run: bool) -> None:
    if not dry_run and path_exists(paths.state) and (is_pointer(paths.state) or not paths.state.is_dir()):
        fail(f"installer state is not a real directory: {paths.state}")
    if not dry_run:
        ensure_real_directory(paths.skills)
    context = InstallerLock(paths) if not dry_run else nullcontext()
    with context:
        if not dry_run:
            cleanup_runtime_temporaries(paths)
        for runtime in runtime_paths(paths):
            if not path_exists(runtime):
                continue
            if not is_pointer(runtime) or not pointer_targets(runtime, paths.canonical):
                print(f"SKIP    {runtime} is not an installer-owned link")
                continue
            if dry_run:
                print(f"would   remove {runtime}")
                continue
            if os.name == "nt":
                temporary = runtime.parent / f".{SKILL_NAME}.runtime-{uuid.uuid4().hex}"
                replace_path(runtime, temporary)
                remove_directory_pointer(temporary)
            else:
                remove_directory_pointer(runtime)
            print(f"removed {runtime}")


def cleanup_runtime_temporaries(paths: Paths) -> None:
    cleanup_pointer_temporaries(paths, activation=False)


def config_block(text: str, origin: Path) -> tuple[int, int, str]:
    start = text.find(CONFIG_START)
    end = text.find(CONFIG_END)
    if start < 0 or end < start:
        fail(f"configuration markers are missing or reversed in {origin}")
    end += len(CONFIG_END)
    return start, end, text[start:end]


def decode_policy(content: bytes, origin: Path) -> str:
    encoding = "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        return content.decode(encoding)
    except UnicodeError as error:
        fail(f"operator policy is not valid UTF text at {origin}: {error}")


def validate_config_assignment(assignment: str | None) -> None:
    if assignment is None:
        return
    if "=" not in assignment:
        fail(f"expected '<Setting>=<value>', got {assignment!r}")
    name, value = (part.strip() for part in assignment.split("=", 1))
    if not name:
        fail("configuration setting name may not be empty")
    if "|" in value:
        fail("configuration values may not contain '|'")


def apply_config_assignment(text: str, assignment: str | None, origin: Path, announce: bool = True) -> str:
    config_block(text, origin)
    if assignment is None:
        return text
    validate_config_assignment(assignment)
    name, value = (part.strip() for part in assignment.split("=", 1))
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
    if announce:
        print(f"set     {name}: {old} -> {value}")
    return "".join(lines)


def merge_missing_config_rows(text: str, defaults: str, origin: Path) -> str:
    _, _, current_block = config_block(text, origin)
    _, _, default_block = config_block(defaults, Path("active SKILL.md"))

    def rows(block: str) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for line in block.splitlines(keepends=True):
            cells = line.split("|")
            if len(cells) < 4:
                continue
            name = cells[1].strip()
            if not name or name == "Setting" or not name.strip("-: "):
                continue
            found.append((name, line))
        return found

    current_names = {name for name, _ in rows(current_block)}
    missing = [line for name, line in rows(default_block) if name not in current_names]
    if not missing:
        return text
    newline = "\r\n" if "\r\n" in current_block else "\n"
    insertion = "".join(line.rstrip("\r\n") + newline for line in missing)
    end = text.find(CONFIG_END)
    if end < 0:
        fail(f"configuration end marker is missing in {origin}")
    return text[:end] + insertion + text[end:]


def configure(paths: Paths, assignment: str | None, dry_run: bool) -> None:
    validate_config_assignment(assignment)
    if dry_run:
        if not path_exists(paths.canonical) or not is_pointer(paths.canonical):
            _, template = dry_run_sync(paths)
            _, _, defaults = config_block(template, Path("fetched SKILL.md"))
            config_exists = paths.config.exists()
            text = decode_policy(paths.config.read_bytes(), paths.config) if config_exists else defaults
            text = merge_missing_config_rows(text, defaults, paths.config if config_exists else Path("fetched SKILL.md"))
            text = apply_config_assignment(text, assignment, paths.config if config_exists else Path("fetched SKILL.md"))
            if paths.bundles.is_dir():
                retained = retained_policy_generation(paths)
                validate_policy_destinations(paths, retained)
            if assignment is None:
                print(config_block(text, paths.config if config_exists else Path("fetched SKILL.md"))[2])
            return
        context = nullcontext()
    else:
        if not path_exists(paths.canonical) or not is_pointer(paths.canonical):
            if paths.bundles.is_dir():
                retained = retained_policy_generation(paths)
                validate_policy_destinations(paths, retained)
            expected_target, template = dry_run_sync(paths, announce=False)
            _, _, defaults = config_block(template, Path("fetched SKILL.md"))
            if paths.config.exists():
                preflight_text = decode_policy(paths.config.read_bytes(), paths.config)
                preflight_origin = paths.config
            elif path_exists(paths.canonical) and (paths.canonical / CONFIG_FILE).exists():
                preflight_text = decode_policy(
                    (paths.canonical / CONFIG_FILE).read_bytes(),
                    paths.canonical / CONFIG_FILE,
                )
                preflight_origin = paths.canonical / CONFIG_FILE
            else:
                preflight_text = defaults
                preflight_origin = Path("fetched SKILL.md")
            preflight_text = merge_missing_config_rows(preflight_text, defaults, preflight_origin)
            apply_config_assignment(preflight_text, assignment, preflight_origin, announce=False)
            sync(paths, False, expected_target=expected_target)
        ensure_real_directory(paths.skills)
        context = InstallerLock(paths)
    with context:
        if not dry_run:
            initialize_directories(paths)
            initialize_repository(paths)
            cleanup_abandoned(paths)
            recover_policy_transaction(paths)
            recover_transaction(paths)
            remove_incoming_ref(paths)
            if is_pointer(paths.canonical):
                active_state(paths, verify_policy=False)
            discard_all_unactivated_bundles(paths)
        validate_runtime_paths(paths)
        bundle, _, _ = active_state(paths, verify_policy=False)
        template = (bundle / "SKILL.md").read_bytes().decode("utf-8")
        _, _, defaults = config_block(template, bundle / "SKILL.md")
        config_exists = paths.config.exists()
        origin = paths.config if config_exists else bundle / "SKILL.md"
        text = decode_policy(paths.config.read_bytes(), paths.config) if config_exists else defaults
        edited_destination: Path | None = None
        visible_policy = bundle / CONFIG_FILE
        if config_exists and visible_policy.exists():
            visible_text = decode_policy(visible_policy.read_bytes(), visible_policy)
            if visible_text != text:
                text = visible_text
                origin = visible_policy
                edited_destination = visible_policy
        text = merge_missing_config_rows(text, defaults, origin)
        text = apply_config_assignment(text, assignment, origin)
        if dry_run:
            retained = retained_policy_generation(paths, edited_destination=edited_destination)
            validate_policy_destinations(paths, retained, edited_destination=edited_destination)
            if assignment is None:
                print(config_block(text, origin)[2])
            return
        switch_policy(paths, text.encode("utf-8"), edited_destination=edited_destination)
        if assignment is None:
            print(config_block(text, paths.config)[2])


def rollback(paths: Paths, dry_run: bool) -> None:
    if dry_run:
        _, receipt = active_bundle(paths)
        state = read_current_state(paths)
        if state["current"] != receipt["commit"]:
            fail(f"current activation receipt disagrees with the active bundle: {paths.current}")
        previous = state.get("previous")
        if not isinstance(previous, str) or not previous:
            fail("no retained previous bundle is recorded")
        require_activated(paths, previous)
        verify_stored_bundle(paths, paths.bundles / previous)
        print(f"would   roll back {receipt['commit']} -> {previous}")
        return
    ensure_real_directory(paths.skills)
    with InstallerLock(paths):
        initialize_directories(paths)
        initialize_repository(paths)
        cleanup_abandoned(paths)
        recover_policy_transaction(paths)
        recover_transaction(paths)
        require_layout_provenance(paths)
        remove_incoming_ref(paths)
        _, receipt, state = active_state(paths)
        discard_all_unactivated_bundles(paths)
        previous = state.get("previous")
        if not isinstance(previous, str) or not previous:
            fail("no retained previous bundle is recorded")
        require_activated(paths, previous)
        canonical_target = fetch_target(paths)
        try:
            ensure_ancestor(paths, previous, canonical_target)
        finally:
            finish_target(paths, canonical_target, keep=False)
        previous_bundle = paths.bundles / previous
        if not previous_bundle.is_dir():
            fail(f"recorded rollback bundle was never activated: {previous}")
        verify_stored_bundle(paths, previous_bundle)
        attach_local(paths, previous_bundle)
        validate_runtime_paths(paths)
        write_json(
            paths.transaction,
            {
                "schema": STATE_SCHEMA,
                "phase": "prepared",
                "previous": str(receipt["commit"]),
                "target": previous,
                "prior_previous": state.get("previous"),
            },
        )
        activate(paths, previous_bundle)
        validate_runtime_paths(paths)
        record_current(paths, previous, str(receipt["commit"]))
        remove_state_file(paths.transaction)
        reconcile_retained_state(paths)
        print(f"rolled  back {receipt['commit']} -> {previous}")


def recover(paths: Paths, dry_run: bool) -> None:
    if dry_run:
        policy_temporaries: set[Path] = set()
        if path_exists(paths.lock):
            lock = open_safe_lock(paths.lock)
            try:
                if os.name == "nt":
                    import msvcrt

                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            except OSError:
                fail(f"another installer holds the operating-system lock {paths.lock}")
            finally:
                lock.close()
        print(f"would   acquire operating-system lock {paths.lock}")
        if path_exists(paths.state):
            if is_pointer(paths.state) or not paths.state.is_dir():
                fail(f"installer state is not a real directory: {paths.state}")
            if paths.repository.is_dir():
                validate_repository_config(paths.repository)
            policy_temporaries = recover_policy_transaction(paths, dry_run=True)
            hardlink_temporaries = policy_temporaries | attachment_link_temporaries(paths)
            candidates = abandoned_paths(paths)
            validate_abandoned_paths(paths, candidates, frozenset(hardlink_temporaries))
            recover_transaction(
                paths,
                dry_run=True,
                ignored_paths=frozenset(candidates),
                verify_policy=not policy_temporaries,
            )
            if not paths.transaction.exists():
                if is_pointer(paths.canonical):
                    active_state(
                        paths,
                        verify_policy=not paths.policy_transaction.exists() and not policy_temporaries,
                        ignored_paths=frozenset(candidates),
                    )
                else:
                    require_layout_provenance(paths)
        else:
            candidates = []
        if paths.transaction.exists():
            print(f"would   recover transaction {paths.transaction}")
        if paths.policy_transaction.exists():
            print(f"would   recover policy transaction {paths.policy_transaction}")
        if paths.repository.is_dir():
            incoming = incoming_target(paths)
            if incoming:
                print(f"would   remove abandoned incoming acquisition ref {incoming}")
        print(f"would   remove {len(candidates)} abandoned installer temporary path(s)")
        return
    ensure_real_directory(paths.skills)
    with InstallerLock(paths):
        initialize_directories(paths)
        initialize_repository(paths)
        cleanup_abandoned(paths)
        recover_policy_transaction(paths)
        recover_transaction(paths)
        require_layout_provenance(paths)
        remove_incoming_ref(paths)
        if is_pointer(paths.canonical):
            bundle, _, _ = active_state(paths)
            discard_all_unactivated_bundles(paths)
            reconcile_retained_state(paths)
            print(f"recovered active bundle {bundle.name}")
        else:
            discard_all_unactivated_bundles(paths)
            print("recovery found no incomplete immutable-bundle activation")


def directory_usage(root: Path) -> int:
    if not root.is_dir() or is_pointer(root):
        return 0
    total = 0
    seen: set[tuple[int, int]] = set()
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for item in entries:
            try:
                path = Path(item.path)
                if item.is_symlink() or is_junction(path):
                    continue
                if item.is_dir(follow_symlinks=False):
                    stack.append(path)
                    continue
                if item.is_file(follow_symlinks=False):
                    details = item.stat(follow_symlinks=False)
                    identity = (details.st_dev, details.st_ino)
                    if identity not in seen:
                        seen.add(identity)
                        total += details.st_size
            except OSError:
                continue
    return total


def status(paths: Paths) -> None:
    print(f"canonical  {paths.canonical}")
    if path_exists(paths.state):
        if is_pointer(paths.state) or not paths.state.is_dir():
            fail(f"installer state is not a real directory: {paths.state}")
        if path_exists(paths.bundles) and (is_pointer(paths.bundles) or not paths.bundles.is_dir()):
            fail(f"bundle store is not a real directory: {paths.bundles}")
        if path_exists(paths.repository):
            validate_repository_config(paths.repository)
        try:
            transactions = [path.name for path in (paths.transaction, paths.policy_transaction) if path.exists()]
            staging = len(abandoned_paths(paths, strict_attachments=False))
            bundles = (
                [
                    path
                    for path in paths.bundles.iterdir()
                    if not path.name.startswith(".staging-") and not DISCARD_NAME.fullmatch(path.name)
                ]
                if paths.bundles.exists()
                else []
            )
            incoming = incoming_target(paths) if paths.repository.is_dir() else None
        except OSError as error:
            fail(f"cannot enumerate installer status under {paths.state}: {error}")
        corrupt_bundles = 0
        unactivated_bundles = 0
        for candidate in bundles:
            try:
                receipt = verify_stored_bundle(paths, candidate)
                verify_attachments(paths, candidate)
                if not is_activated(paths, str(receipt["commit"])):
                    unactivated_bundles += 1
            except (InstallError, OSError, AttributeError, KeyError, TypeError, ValueError):
                corrupt_bundles += 1
        store_bytes = directory_usage(paths.state)
        print(
            f"store      {len(bundles)} bundle(s), {store_bytes} bytes, "
            f"corrupt={corrupt_bundles} unactivated={unactivated_bundles}"
        )
        print(
            f"recovery   transactions={transactions or 'none'} temporaries={staging} "
            f"incoming={incoming or 'none'}"
        )
    require_layout_provenance(paths)
    if not path_exists(paths.canonical):
        print("layout     absent")
        return
    if not is_pointer(paths.canonical):
        print("layout     legacy Git clone")
        config = paths.canonical / CONFIG_FILE
        print(f"config     {config if config.exists() else 'portable defaults'}")
        return
    try:
        bundle, receipt, state = active_state(paths)
    except InstallError:
        raise
    except (OSError, AttributeError, KeyError, TypeError, ValueError) as error:
        fail(f"active bundle status verification failed: {error}")
    print("layout     immutable bundle")
    print(f"active     {receipt['commit']} tree {receipt['tree']}")
    print(f"bundle     {bundle}")
    print(f"state      {'healthy' if state.get('current') == receipt['commit'] else 'MISMATCH'}")
    print(f"previous   {state.get('previous') or 'none'}")
    print(f"config     {paths.config if paths.config.exists() else 'portable defaults'}")
    for runtime in runtime_paths(paths):
        try:
            if not path_exists(runtime):
                health = "absent"
            elif not is_pointer(runtime):
                health = "independent"
            elif not pointer_targets(runtime, paths.canonical):
                health = f"BYPASS -> {runtime.resolve(strict=True)}"
            else:
                resolved = runtime.resolve(strict=True)
                health = f"healthy -> {resolved.name}" if resolved == bundle else f"STALE -> {resolved}"
        except OSError as error:
            health = f"ERROR -> {error}"
        print(f"target     {runtime} [{health}]")


def parse_args(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "command",
        nargs="?",
        default="install",
        choices=("install", "sync", "uninstall", "status", "config", "rollback", "recover"),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--set")
    parser.add_argument(
        "--from",
        dest="source",
        help="retired; always fails because one file cannot prove a complete runtime bundle",
    )
    options = parser.parse_args(arguments)
    if options.source is not None:
        fail(
            "single-file sync is retired because it cannot prove companion bytes; "
            "run sync without --from to install one verified Git tree"
        )
    if options.set is not None and options.command != "config":
        fail("--set is valid only with config")
    return options


def main(arguments: list[str] | None = None) -> int:
    try:
        options = parse_args(sys.argv[1:] if arguments is None else arguments)
        paths = Paths.for_home(Path(os.environ.get("ISSUE_FLOW_HOME", Path.home())))
        validate_real_ancestors(paths.home, paths.skills)
        validate_runtime_ancestors(paths)
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
