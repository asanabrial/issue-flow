#!/bin/sh
# Bootstrap issue-flow, then delegate every state transition to the shared Python installer.

set -eu

REPOSITORY_URL='https://github.com/asanabrial/issue-flow.git'
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd -P) || SCRIPT_DIR=''
case ${0##*/} in install.sh) ;; *) SCRIPT_DIR='' ;; esac
HELPER="$SCRIPT_DIR/scripts/install_bundle.py"

find_python() {
    if command -v python3 >/dev/null 2>&1 && python3 -I -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then printf '%s\n' python3
    elif command -v python >/dev/null 2>&1 && python -I -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then printf '%s\n' python
    else
        printf 'error: Python 3.10 or newer is required; install it and retry.\n' >&2
        return 1
    fi
}

PYTHON=$(find_python) || exit 1

if [ -f "$HELPER" ]; then
    exec "$PYTHON" -I "$HELPER" "$@"
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

command -v git >/dev/null 2>&1 || {
    printf 'error: git is required; install it and retry.\n' >&2
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
        GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_GLOBAL=/dev/null \
        GIT_CONFIG_COUNT=0 GIT_ATTR_NOSYSTEM=1 GIT_NO_REPLACE_OBJECTS=1 \
        GIT_TERMINAL_PROMPT=0 GIT_ASKPASS= \
        git "$@"
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

"$PYTHON" -I "$BOOTSTRAP/source/scripts/install_bundle.py" "$@"
RESULT=$?
exit "$RESULT"
