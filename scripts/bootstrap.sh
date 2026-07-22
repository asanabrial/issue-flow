#!/bin/sh
# issue-flow bootstrap - the thing `curl | sh` runs.
#
# Deliberately tiny and boring: it clones the skill (or upgrades an existing clone) and then runs
# the real installer FROM DISK. Nothing of substance executes out of the pipe - by the time
# install.sh runs, every file it touches is on your machine where you can read it.
#
#   curl -fsSL https://raw.githubusercontent.com/asanabrial/issue-flow/main/scripts/bootstrap.sh | sh
#
# Run it again later to upgrade: the operator configuration block in SKILL.md survives, because the
# upgrade goes through `install.sh sync`, whose whole job is preserving it.

set -eu

REPO='https://github.com/asanabrial/issue-flow.git'
DEST="$HOME/.agents/skills/issue-flow"

command -v git >/dev/null 2>&1 || {
    printf 'error: git is required - install it from your package manager and re-run.\n' >&2
    exit 1
}

if [ -e "$DEST" ] && [ ! -e "$DEST/.git" ]; then
    printf 'error: %s exists but is not a git clone - move it aside and re-run.\n' "$DEST" >&2
    exit 1
fi

if [ ! -e "$DEST" ]; then
    printf 'installing into %s\n' "$DEST"
    git clone -q --depth 1 "$REPO" "$DEST"
    # The operator will edit the config block inside SKILL.md; skip-worktree tells git that this
    # file is local-on-purpose, so status stays clean and pulls never clobber the settings.
    git -C "$DEST" update-index --skip-worktree SKILL.md 2>/dev/null || true
else
    printf 'upgrading %s\n' "$DEST"
    TMP=$(mktemp -d); trap 'rm -rf -- "$TMP"' EXIT
    cp "$DEST/SKILL.md" "$TMP/local.md"                     # settings-bearing copy, byte-exact
    git -C "$DEST" fetch -q origin
    git -C "$DEST" update-index --no-skip-worktree SKILL.md 2>/dev/null || true
    git -C "$DEST" checkout -q origin/main -- .
    git -C "$DEST" reset -q origin/main
    cp "$DEST/SKILL.md" "$TMP/upstream.md"                  # upstream's SKILL.md, byte-exact
    cp "$TMP/local.md" "$DEST/SKILL.md"                     # put the local one back...
    sh "$DEST/install.sh" sync --from "$TMP/upstream.md"    # ...and merge: new prose, old settings
    git -C "$DEST" update-index --skip-worktree SKILL.md 2>/dev/null || true
fi

sh "$DEST/install.sh" install
printf 'done - `%s/install.sh status` shows what is linked.\n' "$DEST"
