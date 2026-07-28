#!/bin/sh
# Bootstrap issue-flow, then delegate every state transition to the shared Python installer.

set -eu

REPOSITORY_URL='https://github.com/asanabrial/issue-flow.git'
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd -P) || SCRIPT_DIR=''
HELPER="$SCRIPT_DIR/scripts/install_bundle.py"

find_python() {
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then printf '%s\n' python3
    elif command -v python >/dev/null 2>&1 && python -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then printf '%s\n' python
    else
        printf 'error: Python 3 is required; install it and retry.\n' >&2
        return 1
    fi
}

PYTHON=$(find_python) || exit 1

if [ -f "$HELPER" ]; then
    exec "$PYTHON" "$HELPER" "$@"
fi

# A raw/piped script has no companion files. Refuse unsafe legacy arguments and a fresh dry-run
# before network or filesystem mutation, then acquire the complete current bootstrap in quarantine.
COMMAND=${1:-install}
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
mkdir -- "$BOOTSTRAP/hooks" "$BOOTSTRAP/template"

case "$REPOSITORY_URL" in file://*) FILE_PROTOCOL=always ;; *) FILE_PROTOCOL=never ;; esac
(
    unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY
    unset GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_TEMPLATE_DIR
    unset GIT_CONFIG_PARAMETERS GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0
    export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_COUNT=0
    export GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=
    mkdir -- "$BOOTSTRAP/source"
    git -c "core.hooksPath=$BOOTSTRAP/hooks" init -q \
        --template="$BOOTSTRAP/template" "$BOOTSTRAP/source"
    git -C "$BOOTSTRAP/source" -c "core.hooksPath=$BOOTSTRAP/hooks" \
        -c "protocol.file.allow=$FILE_PROTOCOL" fetch -q --depth 1 --no-tags \
        "$REPOSITORY_URL" refs/heads/main
    git -C "$BOOTSTRAP/source" -c "core.hooksPath=$BOOTSTRAP/hooks" \
        checkout -q --detach FETCH_HEAD
)

"$PYTHON" "$BOOTSTRAP/source/scripts/install_bundle.py" "$@"
RESULT=$?
exit "$RESULT"
