#!/bin/sh
# Install, link and upgrade the issue-flow skill. POSIX sh, no dependencies.
#
# The versioned skill contains portable defaults. Operator values live in the ignored
# operator.local.md beside it, so upgrades and publications cannot disclose local permissions,
# machine paths or tracker identifiers.
#
# Mirror of install.ps1. Keep the two in step: they share the marker contract, not code.
#
#   curl -fsSL https://raw.githubusercontent.com/asanabrial/issue-flow/main/install.sh | sh
#   ./install.sh status
#   ./install.sh install [--dry-run]
#   ./install.sh sync --from <bundle-directory-or-SKILL.md> [--dry-run]
#   ./install.sh uninstall [--dry-run]
#   ./install.sh config [--set '<Setting>=<value>'] [--dry-run]

set -eu

SKILL_NAME='issue-flow'
SKILL_FILE='SKILL.md'
CONFIG_FILE='operator.local.md'
BUNDLE_FILE='bundle.manifest'
START='<!-- issue-flow:config:start -->'
END='<!-- issue-flow:config:end -->'

# The skill's real home is wherever this script sits.
CANONICAL=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd -P) || CANONICAL=''

# Piped (`curl | sh`) or run from outside a checkout, there is no skill next to this script.
# Then the installer acquires itself - clone on first contact, upgrade after - and hands over to
# the on-disk copy, so everything of substance always executes from files you can read.
if [ ! -f "$CANONICAL/SKILL.md" ] || [ ! -f "$CANONICAL/install.sh" ]; then
    REPO='https://github.com/asanabrial/issue-flow.git'
    DEST="$HOME/.agents/skills/issue-flow"
    command -v git >/dev/null 2>&1 || {
        printf 'error: git is required - install it and re-run.
' >&2; exit 1; }
    if [ -e "$DEST" ] && [ ! -e "$DEST/.git" ]; then
        printf 'error: %s exists and is not a git clone - move it aside and re-run.
' "$DEST" >&2
        exit 1
    fi
    if [ ! -e "$DEST" ]; then
        printf 'installing into %s
' "$DEST"
        git clone -q --depth 1 "$REPO" "$DEST"
    else
        printf 'upgrading %s
' "$DEST"
        git -C "$DEST" fetch -q origin
        git -C "$DEST" checkout -q origin/main -- .
        git -C "$DEST" reset -q origin/main
    fi
    exec sh "$DEST/install.sh" "${1:-install}"
fi

# Per-runtime skill directories that must point at the canonical one. `.agents/skills/` is the
# cross-runtime convention; Claude Code does NOT read it (anthropics/claude-code#31005), so for that
# runtime the link is the mechanism rather than a convenience.
RUNTIME_DIRS="$HOME/.claude/skills $HOME/.codex/skills"

DRY_RUN=0
FROM=''
SET=''

die() { printf 'error: %s\n' "$1" >&2; exit 1; }

# --- config block -------------------------------------------------------------------------------

has_config() {
    grep -qF "$START" "$1" 2>/dev/null && grep -qF "$END" "$1" 2>/dev/null
}

# Print the config block of $1, markers included.
extract_block() {
    awk -v s="$START" -v e="$END" '
        index($0, s) { f = 1 }
        f            { print }
        index($0, e) { f = 0 }
    ' "$1"
}

# Print $1 (the newer skill) with $2 (a block file) spliced in place of its own block.
splice_block() {
    awk -v s="$START" -v e="$END" -v bf="$2" '
        index($0, s) && !done {
            while ((getline line < bf) > 0) print line
            close(bf); done = 1; skip = 1; next
        }
        skip { if (index($0, e)) skip = 0; next }
        { print }
    ' "$1"
}

# --- backups ------------------------------------------------------------------------------------

# Cheap, and the difference between a bug and a loss.
backup_file() {
    dir=$(dirname -- "$1")/.backups
    mkdir -p -- "$dir"
    dest="$dir/$(basename -- "$1").$(date +%Y%m%d-%H%M%S)"
    cp -p -- "$1" "$dest"
    printf '%s\n' "$dest"
}

# --- linking ------------------------------------------------------------------------------------

link_kind() {
    if [ -L "$1" ]; then printf 'symlink\n'
    elif [ -d "$1" ]; then printf 'directory\n'
    elif [ -e "$1" ]; then printf 'file\n'
    else printf 'absent\n'
    fi
}

# Point $1 at $2. A symlink normally just works here; the copy fallback exists for filesystems that
# refuse them (some container mounts, anything FAT-backed), and it stops being a link at all.
make_link() {
    mkdir -p -- "$(dirname -- "$1")"
    if ln -s -- "$2" "$1" 2>/dev/null; then
        printf 'symlink\n'
    else
        cp -R -- "$2" "$1"
        printf 'copy\n'
    fi
}

# --- commands -----------------------------------------------------------------------------------

cmd_install() {
    [ -f "$CANONICAL/$SKILL_FILE" ] ||
        die "$CANONICAL/$SKILL_FILE not found - run this script from inside the skill directory."

    degraded=''
    for base in $RUNTIME_DIRS; do
        # Only wire up runtimes that actually exist on this machine.
        [ -d "$(dirname -- "$base")" ] || continue

        link="$base/$SKILL_NAME"
        kind=$(link_kind "$link")

        case "$kind" in
            symlink)
                printf 'ok      %s  already linked (symlink)\n' "$link"; continue ;;
            directory|file)
                # Never clobber something real: it may be a hand-made copy carrying local edits.
                printf 'SKIP    %s  exists as a real %s - remove it first if you meant to link\n' \
                    "$link" "$kind"; continue ;;
        esac

        if [ "$DRY_RUN" -eq 1 ]; then
            printf 'would   %s  ->  %s\n' "$link" "$CANONICAL"; continue
        fi

        made=$(make_link "$link" "$CANONICAL")
        printf 'linked  %s  ->  %s  (%s)\n' "$link" "$CANONICAL" "$made"
        [ "$made" = 'copy' ] && degraded="$degraded $link"
    done

    if [ -n "$degraded" ]; then
        printf '\nWARNING: fell back to copying - this filesystem refused a symlink.\n'
        printf '         These are now INDEPENDENT copies. Editing the canonical skill will\n'
        printf "         NOT update them, and 'sync' only touches the canonical one:\n"
        for d in $degraded; do printf '           %s\n' "$d"; done
    fi
}

cmd_uninstall() {
    # Removes only the links this script creates. The canonical skill is never touched.
    for base in $RUNTIME_DIRS; do
        link="$base/$SKILL_NAME"
        kind=$(link_kind "$link")
        case "$kind" in
            absent) continue ;;
            directory|file)
                printf 'SKIP    %s  is a real %s, not one of our links - left alone\n' "$link" "$kind"
                continue ;;
        esac
        if [ "$DRY_RUN" -eq 1 ]; then
            printf 'would   remove %s (symlink)\n' "$link"; continue
        fi
        rm -- "$link"
        printf 'removed %s (symlink)\n' "$link"
    done
}

rollback_sync() {
    [ "${SYNC_ACTIVE:-0}" -eq 1 ] || return 0
    while IFS= read -r rel; do
        dest="$CANONICAL/$rel"; saved="$SYNC_BACKUP/$rel"
        if [ -f "$saved" ]; then
            mkdir -p -- "$(dirname -- "$dest")" && cp -p -- "$saved" "$dest" || true
        elif grep -Fx -- "$rel" "$SYNC_CREATED" >/dev/null 2>&1; then rm -f -- "$dest" || true
        fi
    done < "$SYNC_FILES"
    SYNC_ACTIVE=0
}

cmd_sync() {
    installed="$CANONICAL/$SKILL_FILE"
    if [ -d "$FROM" ]; then source_dir=$FROM; else [ -f "$FROM" ] || die "$FROM not found."; source_dir=$(dirname -- "$FROM"); fi
    manifest="$source_dir/$BUNDLE_FILE"; incoming="$source_dir/$SKILL_FILE"
    [ -f "$manifest" ] || die "$manifest not found - sync needs the complete bundle, not one downloaded file."
    [ -f "$incoming" ] || die "$incoming not found in bundle."
    [ -f "$installed" ] || die "$installed not found - nothing to sync."
    [ -f "$CANONICAL/$BUNDLE_FILE" ] || die "installed $BUNDLE_FILE missing; use a full Git upgrade first."
    has_config "$installed" || die "config markers missing in $installed; refusing to lose operator values."
    has_config "$incoming" || die "config markers missing in incoming SKILL.md."

    SYNC_TMP=$(mktemp -d); SYNC_FILES="$SYNC_TMP/files"; SYNC_CREATED="$SYNC_TMP/created"
    trap 'rollback_sync; rm -rf -- "$SYNC_TMP"' EXIT
    trap 'exit 1' HUP INT TERM
    awk 'NR > 1 && NF && substr($0,1,1) != "#" { print }' "$manifest" > "$SYNC_FILES"
    : > "$SYNC_CREATED"; seen="$SYNC_TMP/seen"; : > "$seen"
    header=$(sed -n '1p' "$manifest"); bundle_version=${header#issue-flow-bundle-v1 }
    [ "$bundle_version" != "$header" ] || die "invalid $BUNDLE_FILE header."
    skill_version=$(sed -n 's/^  version: "\([^"]*\)"/\1/p' "$incoming")
    [ -n "$skill_version" ] && [ "$skill_version" = "$bundle_version" ] || die "bundle and SKILL.md versions differ."
    while IFS= read -r rel; do
        case "$rel" in ''|/*|*:*|*\\*|.|..|../*|*/../*|*/..) die "unsafe bundle path: $rel" ;; esac
        grep -Fxi -- "$rel" "$seen" >/dev/null 2>&1 && die "duplicate bundle path: $rel"
        printf '%s\n' "$rel" >> "$seen"; [ -f "$source_dir/$rel" ] || die "bundle file missing: $rel"
    done < "$SYNC_FILES"
    awk 'NR > 1 && NF && substr($0,1,1) != "#" { print }' "$CANONICAL/$BUNDLE_FILE" |
    while IFS= read -r required; do
        grep -Fxi -- "$required" "$SYNC_FILES" >/dev/null 2>&1 || die "incoming bundle omits required file: $required"
    done

    stage="$SYNC_TMP/stage"; mkdir -p -- "$stage"; block="$SYNC_TMP/config"; extract_block "$installed" > "$block"
    while IFS= read -r rel; do
        staged="$stage/$rel"; mkdir -p -- "$(dirname -- "$staged")"
        if [ "$rel" = "$SKILL_FILE" ]; then splice_block "$incoming" "$block" > "$staged"
        else cp -p -- "$source_dir/$rel" "$staged"; fi
    done < "$SYNC_FILES"
    chars=$(wc -c < "$block" | tr -d ' ')
    if [ "$DRY_RUN" -eq 1 ]; then printf 'would   sync bundle v%s from %s, preserving %s config chars\n' "$bundle_version" "$source_dir" "$chars"; rm -rf -- "$SYNC_TMP"; return 0; fi

    SYNC_BACKUP="$CANONICAL/.backups/bundle-$(date +%Y%m%d-%H%M%S)-$$"; mkdir -p -- "$SYNC_BACKUP"
    while IFS= read -r rel; do
        dest="$CANONICAL/$rel"; [ ! -e "$dest" ] || [ -f "$dest" ] || die "bundle target is not a file: $dest"
        if [ -f "$dest" ]; then mkdir -p -- "$SYNC_BACKUP/$(dirname -- "$rel")"; cp -p -- "$dest" "$SYNC_BACKUP/$rel"
        else printf '%s\n' "$rel" >> "$SYNC_CREATED"; fi
    done < "$SYNC_FILES"
    SYNC_ACTIVE=1
    while IFS= read -r rel; do
        dest="$CANONICAL/$rel"; mkdir -p -- "$(dirname -- "$dest")"
        temp="$dest.issue-flow-sync.$$"; cp -p -- "$stage/$rel" "$temp"; mv -f -- "$temp" "$dest"
    done < "$SYNC_FILES"
    SYNC_ACTIVE=0
    printf 'backup  %s\n' "$SYNC_BACKUP"
    printf 'synced  bundle v%s from %s  (config preserved: %s chars)\n' "$bundle_version" "$source_dir" "$chars"
}

cmd_config() {
    # Read or write one row of the operator configuration table.
    #
    # Deliberately generic: it matches a row by its NAME and never carries a list of known settings.
    # Add a row to the skill and this keeps working - a config tool that has to be taught every new
    # setting is a second place to forget one.
    template="$CANONICAL/$SKILL_FILE"
    installed="$CANONICAL/$CONFIG_FILE"
    has_config "$template" || die "no default configuration block in $template."

    if [ ! -f "$installed" ]; then
        if [ "$DRY_RUN" -eq 1 ]; then
            printf 'would   create %s from portable defaults\n' "$installed"
            [ -z "$SET" ] && extract_block "$template" | grep '^|' || true
            [ -z "$SET" ] && return 0
            tmp_local=$(mktemp)
            extract_block "$template" > "$tmp_local"
            installed="$tmp_local"
        else
            extract_block "$template" > "$installed"
            printf 'created %s from portable defaults\n' "$installed"
        fi
    fi
    has_config "$installed" || die "no configuration block in $installed."

    if [ -z "$SET" ]; then
        extract_block "$installed" | grep '^|' || true
        return 0
    fi

    case "$SET" in *=*) ;; *) die "expected --set '<Setting>=<value>', got '$SET'" ;; esac
    name=$(printf '%s' "${SET%%=*}" | sed 's/^ *//; s/ *$//')
    value=$(printf '%s' "${SET#*=}"  | sed 's/^ *//; s/ *$//')

    # A pipe would silently split the cell and corrupt the table.
    case "$value" in *'|'*) die "value may not contain '|' - it would break the table row." ;; esac

    hits=$(awk -F'|' -v n="$name" 'NF>=4 { g=$2; gsub(/^ +| +$/,"",g); if (g==n) c++ } END { print c+0 }' "$installed")
    if [ "$hits" -eq 0 ]; then die "no setting named '$name' in the configuration block."; fi
    if [ "$hits" -gt 1 ]; then die "'$name' matches $hits rows; refusing to guess which."; fi

    was=$(awk -F'|' -v n="$name" 'NF>=4 { g=$2; gsub(/^ +| +$/,"",g); if (g==n) { v=$3; gsub(/^ +| +$/,"",v); print v; exit } }' "$installed")

    if [ "$DRY_RUN" -eq 1 ]; then
        printf 'would   set %s : %s  ->  %s
' "$name" "$was" "$value"
        return 0
    fi

    tmp=$(mktemp)
    awk -F'|' -v OFS='|' -v n="$name" -v v="$value" '
        NF>=4 { g=$2; gsub(/^ +| +$/,"",g); if (g==n && !done) { $3=" " v " "; done=1 } }
        { print }
    ' "$installed" > "$tmp"

    saved=$(backup_file "$installed")
    cat -- "$tmp" > "$installed"
    rm -f -- "$tmp"
    printf 'backup  %s
' "$saved"
    printf 'set     %s : %s  ->  %s
' "$name" "$was" "$value"
}

cmd_status() {
    printf 'canonical  %s\n' "$CANONICAL"
    if [ -f "$CANONICAL/$CONFIG_FILE" ]; then
        if has_config "$CANONICAL/$CONFIG_FILE"; then
            printf 'config     %s  [local, ignored]\n' "$CANONICAL/$CONFIG_FILE"
        else
            printf 'config     INVALID - markers missing in %s\n' "$CANONICAL/$CONFIG_FILE"
        fi
    else
        printf 'config     defaults (no %s)\n' "$CANONICAL/$CONFIG_FILE"
    fi
    for base in $RUNTIME_DIRS; do
        link="$base/$SKILL_NAME"
        printf 'target     %s  [%s]\n' "$link" "$(link_kind "$link")"
    done
}

# --- entry --------------------------------------------------------------------------------------

COMMAND=${1:-install}
[ $# -gt 0 ] && shift

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --from)    shift; [ $# -gt 0 ] || die '--from needs a path'; FROM=$1 ;;
        --from=*)  FROM=${1#--from=} ;;
        --set)     shift; [ $# -gt 0 ] || die '--set needs <Setting>=<value>'; SET=$1 ;;
        --set=*)   SET=${1#--set=} ;;
        *)         die "unknown argument: $1" ;;
    esac
    shift
done

case "$COMMAND" in
    install)   cmd_install ;;
    uninstall) cmd_uninstall ;;
    status)    cmd_status ;;
    config)    cmd_config ;;
    sync)      [ -n "$FROM" ] || die 'sync needs --from <bundle directory or SKILL.md>'; cmd_sync ;;
    *)         die "unknown command: $COMMAND" ;;
esac
