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
    .\install.ps1 sync
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
$StartMark = '<!-- issue-flow:config:start -->'
$EndMark   = '<!-- issue-flow:config:end -->'
$Repo = 'https://github.com/asanabrial/issue-flow.git'

# The skill's real home is wherever this script sits.
$Canonical = $PSScriptRoot

function Invoke-Git {
    param([string]$Path, [string[]]$Arguments)
    $output = @(& git -C $Path @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "git $($Arguments -join ' ') failed ($LASTEXITCODE): $($output -join ' ')" }
    return ($output -join "`n").Trim()
}

$RuntimeDirs = @((Join-Path $HOME '.claude\skills'), (Join-Path $HOME '.codex\skills'))
function Assert-RuntimeLinks {
    param([string]$Path)
    $root = if (Test-Path -LiteralPath $Path) { Invoke-Git -Path $Path -Arguments @('rev-parse', '--show-toplevel') } else { $null }
    foreach ($base in $RuntimeDirs) { $link = Join-Path $base $SkillName
        if ((Get-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue) -and
            (-not $root -or (Invoke-Git -Path $link -Arguments @('rev-parse', '--show-toplevel')) -ne $root)) { throw 'runtime paths do not target canonical skill.' }
    }
}
function Get-CanonicalTarget {
    param([string]$Path)
    $ref = 'refs/issue-flow-sync/' + [guid]::NewGuid().ToString('N')
    [void](Invoke-Git -Path $Path -Arguments @('-c', 'core.hooksPath=NUL', 'fetch', '-q', '--no-tags', $Repo, "+refs/heads/main:$ref"))
    try { return Invoke-Git -Path $Path -Arguments @('rev-parse', $ref) }
    finally { [void](Invoke-Git -Path $Path -Arguments @('-c', 'core.hooksPath=NUL', 'update-ref', '-d', $ref)) }
}
function Test-TargetPolicy {
    param([string]$Path, [string]$Target)
    $paths = (Invoke-Git -Path $Path -Arguments @('ls-tree', '-r', '--name-only', $Target)) -split "`n"
    if ($paths | Where-Object { $_ -ieq $ConfigFile -or $_.StartsWith("$ConfigFile/", [StringComparison]::OrdinalIgnoreCase) }) { return $false }
    $entry = Invoke-Git -Path $Path -Arguments @('ls-tree', $Target, '--', '.gitignore')
    if ($entry -notmatch '^100(644|755) ') { return $false }
    $temp = Join-Path ([IO.Path]::GetTempPath()) ('issue-flow-policy-' + [guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $temp | Out-Null
    try {
        [IO.File]::WriteAllText((Join-Path $temp '.gitignore'), (Invoke-Git -Path $Path -Arguments @('show', "${Target}:.gitignore")), (New-Object Text.UTF8Encoding $false))
        $gitDir = Invoke-Git -Path $Path -Arguments @('rev-parse', '--absolute-git-dir'); & git --git-dir=$gitDir --work-tree=$temp -C $temp -c core.excludesFile=NUL check-ignore --no-index --quiet $ConfigFile
        return $LASTEXITCODE -eq 0
    } finally { Remove-Item -LiteralPath $temp -Recurse -Force }
}
function Invoke-SafeMerge {
    param([string]$Path, [string]$Target)
    if ((Invoke-Git -Path $Path -Arguments @('symbolic-ref', '--quiet', '--short', 'HEAD')) -ne 'main') { throw 'branch changed during sync.' }
    $hooks = Join-Path ([IO.Path]::GetTempPath()) ('issue-flow-hooks-' + [guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $hooks | Out-Null
    try { [void](Invoke-Git -Path $Path -Arguments @('-c', "core.hooksPath=$hooks", 'merge', '--ff-only', '--no-overwrite-ignore', '-q', $Target)) }
    finally { Remove-Item -LiteralPath $hooks -Force }
}

# Piped (`irm | iex`) there is no script location at all; run from elsewhere, no skill next to it.
# Either way the installer acquires itself - clone on first contact, upgrade after - and hands over
# to the on-disk copy, so everything of substance always executes from files you can read. All file
# shuffling uses Copy-Item and git itself, never PowerShell redirection, which on Windows
# PowerShell 5.1 re-encodes text (UTF-16, BOMs) and corrupts what it touches.
if (-not $Canonical -or -not (Test-Path (Join-Path $Canonical 'SKILL.md'))) {
    $Dest = Join-Path $HOME '.agents\skills\issue-flow'
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw 'git is required - install it (winget install Git.Git) and re-run.'
    }
    if ($From) { throw '-From is retired; sync from canonical origin/main.' }
    Assert-RuntimeLinks -Path $Dest
    if ((Test-Path $Dest) -and -not (Test-Path (Join-Path $Dest '.git'))) {
        throw "$Dest exists and is not a git clone - move it aside and re-run."
    }
    if (-not (Test-Path $Dest)) {
        if ($DryRun) { Write-Host 'would   install canonical issue-flow Git tree.'; return }
        Write-Host "installing into $Dest"
        & git clone -q --depth 1 $Repo $Dest
        if ($LASTEXITCODE -ne 0) { throw "git clone failed ($LASTEXITCODE)." }
    } else {
        Write-Host "upgrading $Dest"
        if ((Invoke-Git -Path $Dest -Arguments @('remote', 'get-url', 'origin')) -ne $Repo) { throw 'origin is not the canonical issue-flow repository.' }
        if ((Invoke-Git -Path $Dest -Arguments @('symbolic-ref', '--quiet', '--short', 'HEAD')) -ne 'main' -or
            (Invoke-Git -Path $Dest -Arguments @('status', '--porcelain', '--untracked-files=no'))) { throw 'upgrade requires clean main.' }
        $target = Get-CanonicalTarget -Path $Dest
        if (-not (Test-TargetPolicy -Path $Dest -Target $target)) { throw 'target does not safely ignore local operator policy.' }
        if ($DryRun) { Write-Host "would   upgrade Git tree to $target"; return }
        Invoke-SafeMerge -Path $Dest -Target $target
    }
    $forward = @{ Command = $Command }
    if ($From) { $forward.From = $From }
    if ($Set) { $forward.Set = $Set }
    if ($DryRun) { $forward.DryRun = $true }
    & (Join-Path $Dest 'install.ps1') @forward
    return
}

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
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if (-not $item) { return 'absent' }
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

    if ($Source) { throw '-From is retired; sync from canonical origin/main.' }
    [void](Invoke-Git -Path $Canonical -Arguments @('rev-parse', '--git-dir'))
    $branch = Invoke-Git -Path $Canonical -Arguments @('symbolic-ref', '--quiet', '--short', 'HEAD')
    if ($branch -ne 'main') { throw "sync requires main; current branch is $branch." }
    Assert-RuntimeLinks -Path $Canonical
    if (Invoke-Git -Path $Canonical -Arguments @('status', '--porcelain', '--untracked-files=no')) {
        throw 'tracked files are dirty; after an interrupted upgrade recover with git reset --hard HEAD, then retry.'
    }

    $destinationOrigin = Invoke-Git -Path $Canonical -Arguments @('remote', 'get-url', 'origin')
    if ($destinationOrigin -ne $Repo) { throw 'origin is not the canonical issue-flow repository.' }
    $target = Get-CanonicalTarget -Path $Canonical
    $old = Invoke-Git -Path $Canonical -Arguments @('rev-parse', 'HEAD')
    & git -C $Canonical merge-base --is-ancestor $old $target
    if ($LASTEXITCODE -ne 0) { throw 'target would discard or diverge from local commits.' }
    if (-not (Test-TargetPolicy -Path $Canonical -Target $target)) { throw 'target does not safely ignore local operator policy.' }
    if ($DryRun) { Write-Host "would   sync Git tree $old -> $target"; return }

    # Git owns case folding, unusual paths, ignored-file refusal, HEAD comparison, and the index lock.
    Invoke-SafeMerge -Path $Canonical -Target $target
    if ((Invoke-Git -Path $Canonical -Arguments @('rev-parse', 'HEAD')) -ne $target) {
        throw 'Git tree did not reach target commit.'
    }
    Write-Host "synced  Git tree at $target  (previous HEAD remains in git reflog)"
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
        Invoke-Sync -Source $From
    }
}
