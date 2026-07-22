#!/bin/sh
# Install, link and upgrade the issue-flow skill. POSIX sh, no dependencies.
#
# The design rests on one invariant: the skill file is BOTH the artifact and its own configuration
# store. Everything between the two `issue-flow:config` markers belongs to the operator; everything
# outside belongs to the skill. `sync` swaps the outside and puts the inside back, so upgrading never
# costs you your settings and there is no parallel state file to drift out of agreement with the file
# you actually read.
#
# Mirror of install.ps1. Keep the two in step: they share the marker contract, not code.
#
#   ./install.sh status
#   ./install.sh install [--dry-run]
#   ./install.sh sync --from <path/to/newer/SKILL.md> [--dry-run]
#   ./install.sh uninstall [--dry-run]
#   ./install.sh config [--set '<Setting>=<value>'] [--dry-run]

set -eu

SKILL_NAME='issue-flow'
SKILL_FILE='SKILL.md'
START='<!-- issue-flow:config:start -->'
END='<!-- issue-flow:config:end -->'

# The skill's real home is wherever this script sits.
CANONICAL=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)

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

cmd_sync() {
    installed="$CANONICAL/$SKILL_FILE"
    [ -f "$FROM" ] || die "$FROM not found."
    [ -f "$installed" ] || die "$installed not found - nothing to sync."

    # Refusing beats guessing: a sync that cannot locate the block would silently drop the operator's
    # settings, and they would find out the next time an agent asked for a confirmation it should not
    # have needed.
    if ! has_config "$installed"; then
        printf 'error: config markers not found in the installed skill.\n' >&2
        printf '       expected %s ... %s\n' "$START" "$END" >&2
        printf '       refusing to sync - resolve by hand so no settings are lost.\n' >&2
        exit 1
    fi

    tmpdir=$(mktemp -d)
    trap 'rm -rf -- "$tmpdir"' EXIT
    block="$tmpdir/block"
    merged="$tmpdir/merged"

    extract_block "$installed" > "$block"

    if has_config "$FROM"; then
        # The incoming version ships its own block; ours replaces it verbatim.
        splice_block "$FROM" "$block" > "$merged"
    else
        # A template with no block at all: append ours so the settings survive.
        { cat -- "$FROM"; printf '\n'; cat -- "$block"; } > "$merged"
    fi

    chars=$(wc -c < "$block" | tr -d ' ')
    if [ "$DRY_RUN" -eq 1 ]; then
        printf 'would   sync %s from %s, preserving %s chars of config\n' "$installed" "$FROM" "$chars"
        return 0
    fi

    saved=$(backup_file "$installed")
    cat -- "$merged" > "$installed"
    printf 'backup  %s\n' "$saved"
    printf 'synced  %s  (config preserved: %s chars)\n' "$installed" "$chars"
}

cmd_config() {
    # Read or write one row of the operator configuration table.
    #
    # Deliberately generic: it matches a row by its NAME and never carries a list of known settings.
    # Add a row to the skill and this keeps working - a config tool that has to be taught every new
    # setting is a second place to forget one.
    installed="$CANONICAL/$SKILL_FILE"
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
    if [ -f "$CANONICAL/$SKILL_FILE" ]; then
        if has_config "$CANONICAL/$SKILL_FILE"; then
            printf 'config     present\n'
        else
            printf 'config     MISSING - sync would refuse\n'
        fi
    fi
    for base in $RUNTIME_DIRS; do
        link="$base/$SKILL_NAME"
        printf 'target     %s  [%s]\n' "$link" "$(link_kind "$link")"
    done
}

# --- entry --------------------------------------------------------------------------------------

[ $# -ge 1 ] || die 'usage: install.sh install|sync|uninstall|status|config [--from <path>] [--set <k=v>] [--dry-run]'
COMMAND=$1; shift

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
    sync)      [ -n "$FROM" ] || die 'sync needs --from <path to the newer SKILL.md>'; cmd_sync ;;
    *)         die "unknown command: $COMMAND" ;;
esac
