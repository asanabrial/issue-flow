#!/bin/sh
# Bootstrap issue-flow, then delegate every state transition to the shared Python installer.

set -eu

REPOSITORY_URL='https://github.com/asanabrial/issue-flow.git'
SCRIPT_PARENT=${0%/*}
[ "$SCRIPT_PARENT" = "$0" ] && SCRIPT_PARENT=.
SCRIPT_DIR=$(CDPATH= cd -- "$SCRIPT_PARENT" 2>/dev/null && pwd -P) || SCRIPT_DIR=''
case ${0##*/} in install.sh) ;; *) SCRIPT_DIR='' ;; esac
HELPER="$SCRIPT_DIR/scripts/install_bundle.py"

find_python() {
    candidate=$(command -v python3 2>/dev/null || :)
    if [ -n "$candidate" ] && "$candidate" -I -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then printf '%s\n' "$candidate"
    else
        candidate=$(command -v python 2>/dev/null || :)
        if [ -n "$candidate" ] && "$candidate" -I -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then printf '%s\n' "$candidate"
        else
            printf 'error: Python 3.10 or newer is required; install it and retry.\n' >&2
            return 1
        fi
    fi
}

find_git() {
    candidate=$(command -v git 2>/dev/null || :)
    [ -n "$candidate" ] || return 1
    "$PYTHON" -I -c 'import re,subprocess,sys; out=subprocess.check_output([sys.argv[1], "--version"], text=True); m=re.search(r"\b(\d+)\.(\d+)", out); raise SystemExit(not m or tuple(map(int, m.groups())) < (2, 36))' "$candidate" || return 1
    printf '%s\n' "$candidate"
}

verify_local_helper() {
    "$PYTHON" -I - "$HELPER" "$SCRIPT_DIR/.issue-flow-bundle.json" \
        "$HOME/.agents/skills/.issue-flow/repository.git" \
        "$HOME/.agents/skills/.issue-flow/current.json" "$GIT" <<'PY'
import json
import os
import re
import subprocess
import sys

helper, receipt_path, repository, current_path, git = sys.argv[1:]
try:
    receipt = json.loads(open(receipt_path, encoding="utf-8").read())
    commit = str(receipt["commit"])
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise ValueError("invalid commit")
    bundle_commit = os.path.basename(os.path.dirname(os.path.dirname(os.path.realpath(helper))))
    current = json.loads(open(current_path, encoding="utf-8").read())
    if bundle_commit != commit or current.get("current") != commit:
        raise ValueError("helper is not in the recorded active bundle")
    environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    environment.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
    })
    authoritative = subprocess.check_output([
        git,
        "--no-replace-objects",
        f"--git-dir={repository}",
        "-c", f"core.hooksPath={os.devnull}",
        "-c", "core.fsmonitor=false",
        "cat-file", "blob", f"{commit}:scripts/install_bundle.py",
    ], env=environment)
    activated = subprocess.check_output([
        git,
        "--no-replace-objects",
        f"--git-dir={repository}",
        "-c", f"core.hooksPath={os.devnull}",
        "rev-parse", "--verify", f"refs/issue-flow/activated/{commit}^{{commit}}",
    ], env=environment, text=True).strip()
    if activated != commit:
        raise ValueError("active bundle has no completed activation ref")
    actual = open(helper, "rb").read()
except (KeyError, OSError, subprocess.SubprocessError, ValueError):
    raise SystemExit(1)
raise SystemExit(authoritative != actual)
PY
}

PYTHON=$(find_python) || exit 1

if [ -f "$HELPER" ]; then
    GIT=$(find_git) || {
        printf 'error: Git 2.36 or newer is required; install it and retry.\n' >&2
        exit 1
    }
    if verify_local_helper; then
        USERPROFILE=$HOME; export USERPROFILE
        exec "$PYTHON" -I "$HELPER" "$@"
    fi
    printf 'warning: local installer helper failed Git-object verification; reacquiring canonical main.\n' >&2
fi

# A raw/piped script has no companion files. Refuse unsafe legacy arguments and a fresh dry-run
# before network or filesystem mutation, then acquire the complete current bootstrap in quarantine.
FRESH_DRY=0
for argument in "$@"; do
    case "$argument" in
        --from|--from=*)
            printf 'error: single-file sync is retired; run sync without --from.\n' >&2
            exit 1
            ;;
        --dry-run) FRESH_DRY=1 ;;
    esac
done

DEST="$HOME/.agents/skills/issue-flow"
if [ "$FRESH_DRY" -eq 1 ] && [ ! -e "$DEST" ] && [ ! -L "$DEST" ]; then
    printf 'would   install one complete Git tree at %s\n' "$DEST"
    exit 0
fi

GIT=$(find_git) || {
    printf 'error: Git 2.36 or newer is required; install it and retry.\n' >&2
    exit 1
}

BOOTSTRAP=$(mktemp -d "${TMPDIR:-/tmp}/issue-flow-bootstrap.XXXXXX") || exit 1
trap 'rm -rf -- "$BOOTSTRAP"' EXIT HUP INT TERM
mkdir -p -- "$BOOTSTRAP/source/scripts"

case "$REPOSITORY_URL" in file://*) FILE_PROTOCOL=always ;; *) FILE_PROTOCOL=never ;; esac
clean_git() {
    env -i \
        PATH="$PATH" HOME="${HOME-}" TMPDIR="${TMPDIR-}" TEMP="${TEMP-}" TMP="${TMP-}" \
        SYSTEMROOT="${SYSTEMROOT-}" SystemRoot="${SystemRoot-}" COMSPEC="${COMSPEC-}" PATHEXT="${PATHEXT-}" \
        LANG="${LANG-}" LC_ALL="${LC_ALL-}" \
        HTTP_PROXY="${HTTP_PROXY-}" HTTPS_PROXY="${HTTPS_PROXY-}" ALL_PROXY="${ALL_PROXY-}" NO_PROXY="${NO_PROXY-}" \
        http_proxy="${http_proxy-}" https_proxy="${https_proxy-}" all_proxy="${all_proxy-}" no_proxy="${no_proxy-}" \
        SSL_CERT_FILE="${SSL_CERT_FILE-}" SSL_CERT_DIR="${SSL_CERT_DIR-}" CURL_CA_BUNDLE="${CURL_CA_BUNDLE-}" \
        GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_GLOBAL=/dev/null \
        GIT_CONFIG_COUNT=0 GIT_ATTR_NOSYSTEM=1 GIT_NO_REPLACE_OBJECTS=1 \
        GIT_TERMINAL_PROMPT=0 GIT_ASKPASS= \
        "$GIT" "$@"
}
(
    clean_git --no-replace-objects -c core.hooksPath=/dev/null -c credential.helper= \
        -c protocol.allow=never -c protocol.ext.allow=never \
        -c "protocol.file.allow=$FILE_PROTOCOL" -c protocol.https.allow=always \
        clone -q --bare --no-tags --single-branch --branch main \
        "$REPOSITORY_URL" "$BOOTSTRAP/repository.git"
    clean_git --no-replace-objects --git-dir="$BOOTSTRAP/repository.git" \
        -c core.hooksPath=/dev/null -c core.fsmonitor=false \
        show refs/heads/main:scripts/install_bundle.py > "$BOOTSTRAP/source/scripts/install_bundle.py"
)

USERPROFILE=$HOME; export USERPROFILE
"$PYTHON" -I "$BOOTSTRAP/source/scripts/install_bundle.py" "$@"
RESULT=$?
exit "$RESULT"
