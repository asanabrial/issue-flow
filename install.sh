#!/bin/sh
# Bootstrap issue-flow, then delegate every state transition to the shared Python installer.

set -eu

REPOSITORY_URL='https://github.com/asanabrial/issue-flow.git'

find_python() {
    candidate=$(command -v python3 2>/dev/null || :)
    if [ -n "$candidate" ] && "$candidate" -X utf8 -I -c 'import sys; raise SystemExit(sys.version_info < (3, 10) or sys.platform.startswith(("cygwin", "msys")))' >/dev/null 2>&1; then printf '%s\n' "$candidate"
    else
        candidate=$(command -v python 2>/dev/null || :)
        if [ -n "$candidate" ] && "$candidate" -X utf8 -I -c 'import sys; raise SystemExit(sys.version_info < (3, 10) or sys.platform.startswith(("cygwin", "msys")))' >/dev/null 2>&1; then printf '%s\n' "$candidate"
        else
            printf 'error: native Python 3.10 or newer is required; Cygwin/MSYS Python cannot create Windows-native pointers.\n' >&2
            return 1
        fi
    fi
}

find_git() {
    candidate=$(command -v git 2>/dev/null || :)
    [ -n "$candidate" ] || return 1
    "$PYTHON" -X utf8 -I -c 'import os,re,subprocess,sys; executable=os.path.realpath(sys.argv[1]); environment={name:value for name,value in os.environ.items() if not name.startswith("GIT_")}; environment.update({"GIT_CONFIG_NOSYSTEM":"1","GIT_CONFIG_SYSTEM":os.devnull,"GIT_CONFIG_GLOBAL":os.devnull,"GIT_CONFIG_COUNT":"0"}); out=subprocess.check_output([executable, "--version"], env=environment, text=True); m=re.search(r"\b(\d+)\.(\d+)", out); bad=not m or tuple(map(int, m.groups())) < (2, 36); print(executable.replace("\\", "/")) if not bad else sys.exit(1)' "$candidate" || return 1
}

LOCAL_VERIFIER='import configparser
import json
import os
import re
import subprocess
import sys

mode, helper, receipt_path, repository, current_path, transaction_path, git = sys.argv[1:8]
installer_arguments = sys.argv[8:]

def is_pointer(path):
    junction = getattr(os.path, "isjunction", lambda _path: False)
    return os.path.islink(path) or junction(path)

def validate_repository(path):
    if is_pointer(path) or not os.path.isdir(path) or os.path.lexists(os.path.join(path, "commondir")):
        raise ValueError("redirected repository")
    for name in ("objects", "refs"):
        candidate = os.path.join(path, name)
        if is_pointer(candidate) or not os.path.isdir(candidate):
            raise ValueError("linked repository directory")
    for name in ("config", "HEAD", "packed-refs"):
        candidate = os.path.join(path, name)
        if name == "packed-refs" and not os.path.lexists(candidate):
            continue
        details = os.lstat(candidate)
        if is_pointer(candidate) or not os.path.isfile(candidate) or details.st_nlink != 1:
            raise ValueError("linked repository authority")
    for directory, names, files in os.walk(os.path.join(path, "refs"), followlinks=False):
        for name in names:
            candidate = os.path.join(directory, name)
            if is_pointer(candidate) or not os.path.isdir(candidate):
                raise ValueError("linked ref directory")
        for name in files:
            candidate = os.path.join(directory, name)
            if is_pointer(candidate) or not os.path.isfile(candidate) or os.lstat(candidate).st_nlink != 1:
                raise ValueError("linked ref")
            if open(candidate, "rb").read().lstrip().startswith(b"ref:"):
                raise ValueError("symbolic ref")
    for name in ("alternates", "http-alternates"):
        if os.path.lexists(os.path.join(path, "objects", "info", name)):
            raise ValueError("alternate object database")
    parser = configparser.RawConfigParser(interpolation=None, strict=True)
    parser.read(os.path.join(path, "config"), encoding="utf-8")
    allowed = {"repositoryformatversion", "filemode", "bare", "logallrefupdates", "symlinks", "ignorecase", "precomposeunicode", "fsync", "fsyncmethod"}
    if parser.sections() != ["core"] or set(parser["core"]) - allowed:
        raise ValueError("repository config authority")
    if parser["core"].get("bare", "").casefold() != "true" or parser["core"].get("fsync", "").casefold() != "reference" or parser["core"].get("fsyncmethod", "").casefold() != "fsync":
        raise ValueError("repository config durability")

try:
    validate_repository(repository)
    receipt = json.loads(open(receipt_path, encoding="utf-8").read())
    commit = str(receipt["commit"])
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise ValueError("invalid commit")
    bundle_commit = os.path.basename(os.path.dirname(os.path.dirname(os.path.realpath(helper))))
    if bundle_commit != commit:
        raise ValueError("helper is not in its receipt-named bundle")
    environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    environment.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
    })
    authoritative = subprocess.check_output([
        git,
        "--no-replace-objects",
        f"--git-dir={repository}",
        "-c", f"core.hooksPath={os.devnull}",
        "-c", "core.fsmonitor=false",
        "cat-file", "blob", f"{commit}:scripts/install_bundle.py",
    ], env=environment)
    try:
        current = json.loads(open(current_path, encoding="utf-8").read())
        activated = subprocess.check_output([
            git,
            "--no-replace-objects",
            f"--git-dir={repository}",
            "-c", f"core.hooksPath={os.devnull}",
            "rev-parse", "--verify", f"refs/issue-flow/activated/{commit}^{{commit}}",
        ], env=environment, text=True).strip()
        normal_identity = current.get("current") == commit and activated == commit
    except (OSError, subprocess.SubprocessError, ValueError):
        normal_identity = False
    try:
        transaction = json.loads(open(transaction_path, encoding="utf-8").read())
        recovery_identity = transaction.get("schema") == 1 and commit in {
            transaction.get("previous"), transaction.get("target")
        }
    except (OSError, ValueError):
        recovery_identity = False
    if not normal_identity and not recovery_identity:
        raise ValueError("helper is neither active nor a declared recovery endpoint")
    actual = open(helper, "rb").read()
except (KeyError, OSError, subprocess.SubprocessError, ValueError):
    raise SystemExit(125)
if authoritative != actual:
    raise SystemExit(125)
if mode == "check":
    raise SystemExit(0)
os.environ["ISSUE_FLOW_GIT"] = os.path.realpath(git)
sys.argv = [helper, *installer_arguments]
namespace = {"__name__": "__main__", "__file__": helper}
exec(compile(actual, helper, "exec"), namespace)'

verify_local_helper() {
    "$PYTHON" -X utf8 -I -c "$LOCAL_VERIFIER" check "$HELPER" "$SCRIPT_DIR/.issue-flow-bundle.json" \
        "$HOME/.agents/skills/.issue-flow/repository.git" \
        "$HOME/.agents/skills/.issue-flow/current.json" \
        "$HOME/.agents/skills/.issue-flow/transaction.json" "$GIT" "$@"
}

PYTHON=$(find_python) || exit 1
PYTHON_PLATFORM=$("$PYTHON" -X utf8 -I -c 'import sys; print(sys.platform)')
if [ "$PYTHON_PLATFORM" = win32 ] && [ -z "${MSYSTEM-}" ]; then
    printf 'error: Cygwin is not supported; use Git Bash with native Windows Python.\n' >&2
    exit 1
fi
HOME=$("$PYTHON" -X utf8 -I -c '
import os
import sys

path = os.path.realpath(sys.argv[1])
ancestor = path
while not os.path.exists(ancestor):
    parent = os.path.dirname(ancestor)
    if parent == ancestor:
        raise SystemExit(1)
    ancestor = parent
details = os.stat(ancestor)
owner = getattr(os, "geteuid", lambda: details.st_uid)()
if details.st_uid != owner:
    raise SystemExit(1)
print(path.replace("\\", "/"))
' "$HOME") || {
    printf 'error: installer HOME must resolve to a directory owned by the current user.\n' >&2
    exit 1
}
USERPROFILE=$HOME; ISSUE_FLOW_HOME=$HOME; export HOME USERPROFILE ISSUE_FLOW_HOME
SCRIPT_PATH=$("$PYTHON" -X utf8 -I -c 'import os,sys; print(os.path.realpath(sys.argv[1]).replace("\\", "/"))' "$0" 2>/dev/null || :)
SCRIPT_PARENT=${SCRIPT_PATH%/*}
[ "$SCRIPT_PARENT" = "$SCRIPT_PATH" ] && SCRIPT_PARENT=.
SCRIPT_DIR=$(CDPATH= cd -- "$SCRIPT_PARENT" 2>/dev/null && pwd -P) || SCRIPT_DIR=''
case ${SCRIPT_PATH##*/} in install.sh) ;; *) SCRIPT_DIR='' ;; esac
HELPER="$SCRIPT_DIR/scripts/install_bundle.py"

if [ -f "$HELPER" ]; then
    GIT=$(find_git) || {
        printf 'error: Git 2.36 or newer is required; install it and retry.\n' >&2
        exit 1
    }
    ISSUE_FLOW_GIT=$GIT; export ISSUE_FLOW_GIT
    if verify_local_helper "$@"; then
        exec "$PYTHON" -X utf8 -I -c "$LOCAL_VERIFIER" execute "$HELPER" "$SCRIPT_DIR/.issue-flow-bundle.json" \
            "$HOME/.agents/skills/.issue-flow/repository.git" \
            "$HOME/.agents/skills/.issue-flow/current.json" \
            "$HOME/.agents/skills/.issue-flow/transaction.json" "$GIT" "$@"
    else
        LOCAL_RESULT=$?
        [ "$LOCAL_RESULT" -ne 125 ] && exit "$LOCAL_RESULT"
    fi
    printf 'warning: local installer helper failed Git-object verification; reacquiring canonical main.\n' >&2
fi

# A raw/piped script has no companion files. Refuse unsafe legacy arguments and a fresh dry-run
# before network or filesystem mutation, then acquire the complete current bootstrap in quarantine.
"$PYTHON" -X utf8 -I - "$@" <<'PY'
import argparse
import sys

parser = argparse.ArgumentParser(description="Install one verified issue-flow Git tree.", allow_abbrev=False)
parser.add_argument("command", nargs="?", default="install", choices=(
    "install", "sync", "uninstall", "status", "config", "rollback", "recover"
))
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--set")
parser.add_argument("--from", dest="source", help="retired; always fails because one file cannot prove a bundle")
options = parser.parse_args(sys.argv[1:])
if options.set and options.command != "config":
    parser.error("--set is valid only with config")
PY
FRESH_DRY=0
FRESH_COMMAND=install
FRESH_SET=0
FRESH_HELP=0
for argument in "$@"; do
    case "$argument" in
        install|sync|uninstall|status|config|rollback|recover) FRESH_COMMAND=$argument ;;
        --from|--from=*)
            printf 'error: single-file sync is retired; run sync without --from.\n' >&2
            exit 1
            ;;
        --dry-run) FRESH_DRY=1 ;;
        --set|--set=*) FRESH_SET=1 ;;
        -h|--help) FRESH_HELP=1 ;;
    esac
done
[ "$FRESH_HELP" -eq 1 ] && exit 0

DEST="$HOME/.agents/skills/issue-flow"
STATE="$HOME/.agents/skills/.issue-flow"
CLAUDE_DEST="$HOME/.claude/skills/issue-flow"
CODEX_DEST="$HOME/.codex/skills/issue-flow"
case $FRESH_COMMAND in install|sync) FRESH_DRY_INSTALL=1 ;; *) FRESH_DRY_INSTALL=0 ;; esac
if [ "$FRESH_DRY" -eq 1 ] && [ "$FRESH_DRY_INSTALL" -eq 1 ] && [ "$FRESH_SET" -eq 0 ] \
    && [ ! -e "$DEST" ] && [ ! -L "$DEST" ] \
    && [ ! -e "$STATE" ] && [ ! -L "$STATE" ] \
    && [ ! -e "$HOME/.claude" ] && [ ! -L "$HOME/.claude" ] \
    && [ ! -e "$HOME/.codex" ] && [ ! -L "$HOME/.codex" ] \
    && [ ! -e "$CLAUDE_DEST" ] && [ ! -L "$CLAUDE_DEST" ] \
    && [ ! -e "$CODEX_DEST" ] && [ ! -L "$CODEX_DEST" ]; then
    printf 'would   install one complete Git tree at %s\n' "$DEST"
    exit 0
fi

GIT=$(find_git) || {
    printf 'error: Git 2.36 or newer is required; install it and retry.\n' >&2
    exit 1
}
ISSUE_FLOW_GIT=$GIT; export ISSUE_FLOW_GIT

USERPROFILE=$HOME; ISSUE_FLOW_HOME=$HOME; export USERPROFILE ISSUE_FLOW_HOME
exec "$PYTHON" -X utf8 -I - "$REPOSITORY_URL" "$GIT" "$@" <<'PY'
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path

repository, git, *installer_arguments = sys.argv[1:]
home = Path.home().resolve(strict=True)
if os.name != "nt" and home.stat().st_uid != os.geteuid():
    raise RuntimeError(f"installer HOME is not owned by the current user: {home}")

def lock_owner(handle, blocking):
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return True
    except BlockingIOError:
        return False

def make_writable(function, target, _error):
    details = os.lstat(target)
    if stat.S_ISREG(details.st_mode) and details.st_nlink != 1:
        raise RuntimeError(f"refusing to chmod externally hard-linked bootstrap state: {target}")
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    function(target)

def remove_bootstrap(path):
    owner = path / ".issue-flow-bootstrap-owner"
    repository_path = path / "repository.git"
    unexpected = {item.name for item in path.iterdir()} - {owner.name, repository_path.name}
    if unexpected:
        raise RuntimeError(f"bootstrap quarantine contains unowned entries: {path}: {sorted(unexpected)}")
    if repository_path.exists():
        if repository_path.is_symlink() or not repository_path.is_dir():
            raise RuntimeError(f"bootstrap repository is not a real directory: {repository_path}")
        shutil.rmtree(repository_path, onerror=make_writable)
    if repository_path.exists():
        raise RuntimeError(f"bootstrap repository cleanup did not complete: {repository_path}")
    if os.name != "nt":
        directory = os.open(path, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    if owner.exists():
        details = os.lstat(owner)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise RuntimeError(f"bootstrap owner marker is not private: {owner}")
        owner.unlink()
    if os.name != "nt":
        directory = os.open(path, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    path.rmdir()
    if os.name != "nt":
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    if path.exists():
        raise RuntimeError(f"bootstrap quarantine cleanup did not complete: {path}")

guard_path = home / ".issue-flow-bootstrap.lock"
if os.path.lexists(guard_path) and guard_path.is_symlink():
    raise RuntimeError(f"bootstrap guard may not be a link: {guard_path}")
guard_flags = os.O_RDWR | os.O_CREAT
if hasattr(os, "O_NOFOLLOW"):
    guard_flags |= os.O_NOFOLLOW
guard_descriptor = os.open(guard_path, guard_flags, 0o600)
guard_details = os.fstat(guard_descriptor)
if not stat.S_ISREG(guard_details.st_mode) or guard_details.st_nlink != 1:
    os.close(guard_descriptor)
    raise RuntimeError(f"bootstrap guard is not a private regular file: {guard_path}")
bootstrap_guard = os.fdopen(guard_descriptor, "r+b")
if guard_details.st_size == 0:
    bootstrap_guard.write(b"\0")
    bootstrap_guard.flush()
    os.fsync(bootstrap_guard.fileno())
if not lock_owner(bootstrap_guard, blocking=True):
    raise RuntimeError(f"could not acquire bootstrap guard: {guard_path}")

for candidate in home.iterdir():
    if not re.fullmatch(r"\.issue-flow-bootstrap-[0-9a-f]{32}", candidate.name):
        continue
    if candidate.is_symlink() or not candidate.is_dir():
        raise RuntimeError(f"installer-shaped bootstrap path is not a real directory: {candidate}")
    owner = candidate / ".issue-flow-bootstrap-owner"
    if not owner.exists():
        if not any(candidate.iterdir()):
            candidate.rmdir()
            continue
        raise RuntimeError(f"non-empty bootstrap quarantine has no owner marker: {candidate}")
    details = os.lstat(owner)
    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        raise RuntimeError(f"bootstrap owner marker is not private: {owner}")
    owner_probe = owner.open("r+b")
    if not lock_owner(owner_probe, blocking=False):
        owner_probe.close()
        continue
    unexpected = {item.name for item in candidate.iterdir()} - {owner.name, "repository.git"}
    owner_probe.close()
    if unexpected:
        raise RuntimeError(f"bootstrap quarantine contains unowned entries: {candidate}: {sorted(unexpected)}")
    if candidate.exists():
        remove_bootstrap(candidate)

bootstrap = home / f".issue-flow-bootstrap-{uuid.uuid4().hex}"
os.mkdir(bootstrap, 0o700)
details = os.lstat(bootstrap)
if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
    raise RuntimeError(f"bootstrap quarantine is not a private directory: {bootstrap}")
owner_path = bootstrap / ".issue-flow-bootstrap-owner"
owner_handle = owner_path.open("x+b")
owner_handle.write(b"\0")
owner_handle.flush()
os.fsync(owner_handle.fileno())
if not lock_owner(owner_handle, blocking=True):
    raise RuntimeError(f"could not lock bootstrap owner marker: {owner_path}")
owner_handle.seek(0)
owner_handle.write(b"issue-flow-bootstrap-v1\n")
owner_handle.truncate()
owner_handle.flush()
os.fsync(owner_handle.fileno())
if os.name != "nt":
    directory = os.open(bootstrap, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
bare = bootstrap / "repository.git"
environment = os.environ.copy()
for name in tuple(environment):
    if name.startswith("GIT_") or name in {"SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE"}:
        environment.pop(name, None)
environment.update({
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_COUNT": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_NO_LAZY_FETCH": "1",
})
file_protocol = "always" if repository.lower().startswith("file:") else "never"
hooks = "NUL" if os.name == "nt" else "/dev/null"
common = [git, "--no-replace-objects", "-c", f"core.hooksPath={hooks}", "-c", "credential.helper="]
try:
    subprocess.run([
        *common,
        "-c", "protocol.allow=never",
        "-c", "protocol.ext.allow=never",
        "-c", f"protocol.file.allow={file_protocol}",
        "-c", "protocol.https.allow=always",
        "clone", "-q", "--bare", "--no-tags", "--single-branch", "--branch", "main",
        repository, str(bare),
    ], env=environment, check=True)
    commit = subprocess.check_output([
        git, "--no-replace-objects", f"--git-dir={bare}",
        "-c", f"core.hooksPath={hooks}", "rev-parse", "refs/heads/main^{commit}",
    ], env=environment, text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise RuntimeError("canonical main did not resolve to a commit")
    helper = subprocess.check_output([
        git, "--no-replace-objects", f"--git-dir={bare}",
        "-c", f"core.hooksPath={hooks}", "-c", "core.fsmonitor=false",
        "cat-file", "blob", f"{commit}:scripts/install_bundle.py",
    ], env=environment)
    os.environ["ISSUE_FLOW_GIT"] = os.path.realpath(git)
    sys.argv = [f"git:{commit}:scripts/install_bundle.py", *installer_arguments]
    namespace = {"__name__": "__main__", "__file__": sys.argv[0]}
    exec(compile(helper, sys.argv[0], "exec"), namespace)
finally:
    owner_handle.close()
    if bootstrap.exists():
        remove_bootstrap(bootstrap)
    bootstrap_guard.close()
PY
