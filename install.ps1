<#
.SYNOPSIS
    Install, link and upgrade the issue-flow skill. No dependencies beyond Windows itself.

.DESCRIPTION
    The versioned skill contains portable defaults. Operator values live in the ignored
    operator.local.md beside it, so upgrades and publications cannot disclose local permissions,
    machine paths or tracker identifiers.

    Mirror of install.sh. Keep the two in step: they share the marker contract, not code.

.EXAMPLE
    .\install.ps1 status
    .\install.ps1 install -DryRun
    .\install.ps1 sync -From .\bundle-or-SKILL.md
    .\install.ps1 config -Set 'Tracker=linear'
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'sync', 'uninstall', 'status', 'config')]
    [string]$Command = 'install',

    [string]$From,

    [string]$Set,

    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$SkillName = 'issue-flow'
$SkillFile = 'SKILL.md'
$ConfigFile = 'operator.local.md'
$BundleFile = 'bundle.manifest'
$StartMark = '<!-- issue-flow:config:start -->'
$EndMark   = '<!-- issue-flow:config:end -->'

# The skill's real home is wherever this script sits.
$Canonical = $PSScriptRoot

# Piped (`irm | iex`) there is no script location at all; run from elsewhere, no skill next to it.
# Either way the installer acquires itself - clone on first contact, upgrade after - and hands over
# to the on-disk copy, so everything of substance always executes from files you can read. All file
# shuffling uses Copy-Item and git itself, never PowerShell redirection, which on Windows
# PowerShell 5.1 re-encodes text (UTF-16, BOMs) and corrupts what it touches.
if (-not $Canonical -or -not (Test-Path (Join-Path $Canonical 'SKILL.md'))) {
    $Repo = 'https://github.com/asanabrial/issue-flow.git'
    $Dest = Join-Path $HOME '.agents\skills\issue-flow'
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw 'git is required - install it (winget install Git.Git) and re-run.'
    }
    if ((Test-Path $Dest) -and -not (Test-Path (Join-Path $Dest '.git'))) {
        throw "$Dest exists and is not a git clone - move it aside and re-run."
    }
    if (-not (Test-Path $Dest)) {
        Write-Host "installing into $Dest"
        git clone -q --depth 1 $Repo $Dest
    } else {
        Write-Host "upgrading $Dest"
        git -C $Dest fetch -q origin
        git -C $Dest checkout -q origin/main -- .
        git -C $Dest reset -q origin/main
    }
    & (Join-Path $Dest 'install.ps1') $Command
    return
}

# Per-runtime skill directories that must point at the canonical one. `.agents/skills/` is
# the cross-runtime convention; Claude Code does NOT read it (anthropics/claude-code#31005),
# so for that runtime the link is the mechanism rather than a convenience.
$RuntimeDirs = @(
    (Join-Path $HOME '.claude\skills'),
    (Join-Path $HOME '.codex\skills')
)

# --- config block ---------------------------------------------------------------------

function Split-Config {
    <#  Returns @{Before; Block; After}. Refuses rather than guesses: a sync that cannot
        locate the block would silently drop the operator's settings, and they would find
        out the next time an agent asked for a confirmation it should not have needed.     #>
    param([string]$Text, [string]$Origin)

    $i = $Text.IndexOf($StartMark)
    $j = $Text.IndexOf($EndMark)
    if ($i -lt 0 -or $j -lt 0) {
        throw "config markers not found in $Origin.`n" +
              "       expected $StartMark ... $EndMark`n" +
              "       refusing to sync - resolve by hand so no settings are lost."
    }
    if ($j -lt $i) { throw "config end marker precedes the start marker in $Origin; file is corrupt." }

    $end = $j + $EndMark.Length
    return @{
        Before = $Text.Substring(0, $i)
        Block  = $Text.Substring($i, $end - $i)
        After  = $Text.Substring($end)
    }
}

function Test-HasConfig { param([string]$Text)
    return ($Text.Contains($StartMark) -and $Text.Contains($EndMark))
}

# --- backups --------------------------------------------------------------------------

function Backup-File {
    # Cheap, and the difference between a bug and a loss.
    param([string]$Path)
    $dir = Join-Path (Split-Path $Path -Parent) '.backups'
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $dest = Join-Path $dir ((Split-Path $Path -Leaf) + ".$stamp")
    Copy-Item -LiteralPath $Path -Destination $dest -Force
    return $dest
}

function Write-Utf8NoBom {
    <#  Set-Content -Encoding UTF8 writes a BOM on Windows PowerShell 5.1, which lands three bytes
        in front of the frontmatter delimiter and makes the file's first line something no YAML
        parser expects. Write the bytes ourselves instead.                                        #>
    param([string]$Path, [string]$Text)
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding $false))
}

# --- linking --------------------------------------------------------------------------

function Get-LinkKind {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return 'absent' }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.LinkType) { return $item.LinkType.ToLower() }   # SymbolicLink | Junction
    return 'directory'
}

function New-SkillLink {
    <#  Point $Link at $Target, degrading only as far as Windows forces.

        Symlink first because it is the most faithful. Junction second because it needs NO
        elevation — that single fact is why Windows is not a dead end here. Copy last: it
        works, but it stops being a link, and from then on the copies drift.               #>
    param([string]$Link, [string]$Target)

    $parent = Split-Path $Link -Parent
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }

    try {
        New-Item -ItemType SymbolicLink -Path $Link -Target $Target -ErrorAction Stop | Out-Null
        return 'symlink'
    } catch { }

    try {
        New-Item -ItemType Junction -Path $Link -Target $Target -ErrorAction Stop | Out-Null
        return 'junction'
    } catch { }

    Copy-Item -LiteralPath $Target -Destination $Link -Recurse -Force
    return 'copy'
}

# --- commands -------------------------------------------------------------------------

function Invoke-Install {
    $skill = Join-Path $Canonical $SkillFile
    if (-not (Test-Path -LiteralPath $skill)) {
        throw "$skill not found - run this script from inside the skill directory."
    }

    $degraded = @()
    foreach ($base in $RuntimeDirs) {
        # Only wire up runtimes that actually exist on this machine.
        if (-not (Test-Path (Split-Path $base -Parent))) { continue }

        $link = Join-Path $base $SkillName
        $kind = Get-LinkKind $link

        if ($kind -in @('symboliclink', 'junction')) {
            Write-Host "ok      $link  already linked ($kind)"
            continue
        }
        if ($kind -eq 'directory') {
            # Never clobber a real directory: it may be a hand-made copy carrying local edits.
            Write-Host "SKIP    $link  exists as a real directory - remove it first if you meant to link"
            continue
        }
        if ($DryRun) { Write-Host "would   $link  ->  $Canonical"; continue }

        $made = New-SkillLink -Link $link -Target $Canonical
        Write-Host "linked  $link  ->  $Canonical  ($made)"
        if ($made -eq 'copy') { $degraded += $link }
    }

    if ($degraded.Count -gt 0) {
        Write-Host ""
        Write-Warning "Fell back to copying - neither a symlink nor a junction was possible."
        Write-Host "         These are now INDEPENDENT copies. Editing the canonical skill will"
        Write-Host "         NOT update them, and 'sync' only touches the canonical one:"
        $degraded | ForEach-Object { Write-Host "           $_" }
    }
}

function Invoke-Uninstall {
    # Removes only the links this script creates. The canonical skill is never touched.
    foreach ($base in $RuntimeDirs) {
        $link = Join-Path $base $SkillName
        $kind = Get-LinkKind $link
        if ($kind -eq 'absent') { continue }
        if ($kind -eq 'directory') {
            Write-Host "SKIP    $link  is a real directory, not one of our links - left alone"
            continue
        }
        if ($DryRun) { Write-Host "would   remove $link ($kind)"; continue }
        # .Delete() removes the reparse point without following it into the target.
        (Get-Item -LiteralPath $link -Force).Delete()
        Write-Host "removed $link ($kind)"
    }
}

function Invoke-Sync {
    param([string]$Source)

    $installed = Join-Path $Canonical $SkillFile
    if (-not (Test-Path -LiteralPath $Source)) { throw "$Source not found." }
    $sourceDir = if (Test-Path -LiteralPath $Source -PathType Container) {
        (Resolve-Path -LiteralPath $Source).Path
    } else { Split-Path (Resolve-Path -LiteralPath $Source).Path -Parent }
    $manifest = Join-Path $sourceDir $BundleFile
    $incoming = Join-Path $sourceDir $SkillFile
    if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
        throw "$manifest not found - sync needs the complete bundle, not one downloaded file."
    }
    if (-not (Test-Path -LiteralPath $incoming -PathType Leaf)) { throw "$incoming not found in bundle." }
    if (-not (Test-Path -LiteralPath $installed)) { throw "$installed not found - nothing to sync." }
    $requiredManifest = Join-Path $Canonical $BundleFile
    if (-not (Test-Path -LiteralPath $requiredManifest -PathType Leaf)) {
        throw "installed $BundleFile missing; use a full Git upgrade first."
    }

    $lines = @(Get-Content -LiteralPath $manifest -Encoding UTF8)
    if ($lines.Count -lt 2 -or $lines[0] -notmatch '^issue-flow-bundle-v1 (\S+)$') {
        throw "invalid $BundleFile header."
    }
    $bundleVersion = $Matches[1]
    $newText = Get-Content -LiteralPath $incoming -Raw -Encoding UTF8
    if ($newText -notmatch '(?m)^  version: "([^"]+)"$' -or $Matches[1] -ne $bundleVersion) {
        throw 'bundle and SKILL.md versions differ.'
    }
    $files = @($lines | Select-Object -Skip 1 | Where-Object { $_ -and -not $_.StartsWith('#') })
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($rel in $files) {
        if ([IO.Path]::IsPathRooted($rel) -or $rel.Contains('\') -or
                ($rel -split '/') -contains '..' -or -not $seen.Add($rel)) {
            throw "unsafe or duplicate bundle path: $rel"
        }
        if (-not (Test-Path -LiteralPath (Join-Path $sourceDir $rel) -PathType Leaf)) {
            throw "bundle file missing: $rel"
        }
    }
    Get-Content -LiteralPath $requiredManifest -Encoding UTF8 | Select-Object -Skip 1 |
        Where-Object { $_ -and -not $_.StartsWith('#') } | ForEach-Object {
            if (-not $seen.Contains($_)) { throw "incoming bundle omits required file: $_" }
        }

    $mine = Split-Config -Text (Get-Content -LiteralPath $installed -Raw -Encoding UTF8) -Origin 'the installed skill'
    $theirs = Split-Config -Text $newText -Origin 'the incoming skill'
    $stage = Join-Path ([IO.Path]::GetTempPath()) ("issue-flow-sync-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    foreach ($rel in $files) {
        $target = Join-Path $stage $rel; New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
        if ($rel -eq $SkillFile) { Write-Utf8NoBom -Path $target -Text ($theirs.Before + $mine.Block + $theirs.After) }
        else { Copy-Item -LiteralPath (Join-Path $sourceDir $rel) -Destination $target }
    }

    if ($DryRun) {
        Write-Host "would   sync bundle v$bundleVersion from $sourceDir, preserving $($mine.Block.Length) config chars"
        Remove-Item -LiteralPath $stage -Recurse -Force
        return
    }

    $backup = Join-Path $Canonical ('.backups\bundle-' + (Get-Date -Format 'yyyyMMdd-HHmmss') +
                                    "-$PID-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    $created = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    try {
        foreach ($rel in $files) {
            $dest = Join-Path $Canonical $rel
            if ((Test-Path -LiteralPath $dest) -and -not (Test-Path -LiteralPath $dest -PathType Leaf)) {
                throw "bundle target is not a file: $dest"
            }
            if (Test-Path -LiteralPath $dest -PathType Leaf) {
                $saved = Join-Path $backup $rel; New-Item -ItemType Directory -Path (Split-Path $saved -Parent) -Force | Out-Null
                Copy-Item -LiteralPath $dest -Destination $saved
            } else { [void]$created.Add($rel) }
        }
        foreach ($rel in $files) {
            $dest = Join-Path $Canonical $rel; New-Item -ItemType Directory -Path (Split-Path $dest -Parent) -Force | Out-Null
            $temp = "$dest.issue-flow-sync-$PID"; Copy-Item -LiteralPath (Join-Path $stage $rel) -Destination $temp
            Move-Item -LiteralPath $temp -Destination $dest -Force
        }
    } catch {
        foreach ($rel in $files) {
            $dest = Join-Path $Canonical $rel; $saved = Join-Path $backup $rel
            if (Test-Path -LiteralPath $saved -PathType Leaf) { Copy-Item -LiteralPath $saved -Destination $dest -Force }
            elseif ($created.Contains($rel)) { Remove-Item -LiteralPath $dest -Force -ErrorAction SilentlyContinue }
        }
        throw
    } finally { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }
    Write-Host "backup  $backup"
    Write-Host "synced  bundle v$bundleVersion from $sourceDir  (config preserved: $($mine.Block.Length) chars)"
}

function Invoke-Config {
    <#  Read or write one row of the operator configuration table.

        Deliberately generic: it matches a row by its NAME and never carries a list of known
        settings. Add a row to the skill and this keeps working — a config tool that has to be
        taught every new setting is a second place to forget one.                                #>
    param([string]$Assignment)

    $template = Join-Path $Canonical $SkillFile
    $installed = Join-Path $Canonical $ConfigFile
    $templateText = Get-Content -LiteralPath $template -Raw -Encoding UTF8
    $defaultBlock = (Split-Config -Text $templateText -Origin 'the versioned skill defaults').Block

    if (-not (Test-Path -LiteralPath $installed)) {
        if ($DryRun) {
            Write-Host "would   create $installed from portable defaults"
            if (-not $Assignment) {
                $defaultBlock -split "`n" | Where-Object { $_ -match '^\|' } | ForEach-Object { Write-Host $_.TrimEnd() }
                return
            }
            $text = $defaultBlock
        } else {
            Write-Utf8NoBom -Path $installed -Text $defaultBlock
            Write-Host "created $installed from portable defaults"
            $text = $defaultBlock
        }
    } else {
        $text = Get-Content -LiteralPath $installed -Raw -Encoding UTF8
    }
    $block = (Split-Config -Text $text -Origin 'the installed skill').Block

    if (-not $Assignment) {
        # No assignment: print the table as it stands.
        $block -split "`n" | Where-Object { $_ -match '^\|' } | ForEach-Object { Write-Host $_.TrimEnd() }
        return
    }

    $i = $Assignment.IndexOf('=')
    if ($i -lt 1) { throw "expected --set '<Setting>=<value>', got '$Assignment'" }
    $name  = $Assignment.Substring(0, $i).Trim()
    $value = $Assignment.Substring($i + 1).Trim()

    # A pipe would silently split the cell and corrupt the table.
    if ($value.Contains('|')) { throw "value may not contain '|' - it would break the table row." }

    $lines = $text -split "`n"
    $hits = @()
    for ($n = 0; $n -lt $lines.Count; $n++) {
        $parts = $lines[$n] -split '\|'
        if ($parts.Count -ge 4 -and $parts[1].Trim() -eq $name) { $hits += $n }
    }
    if ($hits.Count -eq 0) { throw "no setting named '$name' in the configuration block." }
    if ($hits.Count -gt 1) { throw "'$name' matches $($hits.Count) rows; refusing to guess which." }

    $n = $hits[0]
    $parts = $lines[$n] -split '\|'
    $was = $parts[2].Trim()
    $parts[2] = " $value "
    $lines[$n] = ($parts -join '|')

    if ($DryRun) { Write-Host "would   set $name : $was  ->  $value"; return }

    $saved = Backup-File $installed
    Write-Utf8NoBom -Path $installed -Text ($lines -join "`n")
    Write-Host "backup  $saved"
    Write-Host "set     $name : $was  ->  $value"
}

function Invoke-Status {
    Write-Host "canonical  $Canonical"
    $config = Join-Path $Canonical $ConfigFile
    if (Test-Path -LiteralPath $config) {
        $text = Get-Content -LiteralPath $config -Raw -Encoding UTF8
        $state = if (Test-HasConfig $text) { "$config  [local, ignored]" } else { "INVALID - markers missing in $config" }
        Write-Host "config     $state"
    } else {
        Write-Host "config     defaults (no $config)"
    }
    foreach ($base in $RuntimeDirs) {
        $link = Join-Path $base $SkillName
        Write-Host "target     $link  [$(Get-LinkKind $link)]"
    }
}

switch ($Command) {
    'install'   { Invoke-Install }
    'uninstall' { Invoke-Uninstall }
    'status'    { Invoke-Status }
    'config'    { Invoke-Config -Assignment $Set }
    'sync'      {
        if (-not $From) { throw "sync needs -From <bundle directory or SKILL.md>" }
        Invoke-Sync -Source $From
    }
}
