# issue-flow bootstrap - the thing `irm | iex` runs.
#
# Deliberately tiny and boring: it clones the skill (or upgrades an existing clone) and then runs
# the real installer FROM DISK. Nothing of substance executes out of the pipe - by the time
# install.ps1 runs, every file it touches is on your machine where you can read it.
#
#   irm https://raw.githubusercontent.com/asanabrial/issue-flow/main/scripts/bootstrap.ps1 | iex
#
# Run it again later to upgrade: the operator configuration block in SKILL.md survives, because the
# upgrade goes through `install.ps1 sync`, whose whole job is preserving it.
#
# All file shuffling uses Copy-Item and git itself - never PowerShell redirection, which on
# Windows PowerShell 5.1 re-encodes text (UTF-16, BOMs) and corrupts what it touches.

$ErrorActionPreference = 'Stop'

$Repo = 'https://github.com/asanabrial/issue-flow.git'
$Dest = Join-Path $HOME '.agents\skills\issue-flow'

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'git is required - install it (winget install Git.Git) and re-run.'
}

if ((Test-Path $Dest) -and -not (Test-Path (Join-Path $Dest '.git'))) {
    throw "$Dest exists but is not a git clone - move it aside and re-run."
}

if (-not (Test-Path $Dest)) {
    Write-Host "installing into $Dest"
    git clone -q --depth 1 $Repo $Dest
    # The operator will edit the config block inside SKILL.md; skip-worktree tells git that this
    # file is local-on-purpose, so status stays clean and pulls never clobber the settings.
    git -C $Dest update-index --skip-worktree SKILL.md 2>$null
} else {
    Write-Host "upgrading $Dest"
    $Tmp = Join-Path $env:TEMP ("issue-flow-" + [guid]::NewGuid().ToString('n').Substring(0, 8))
    New-Item -ItemType Directory -Path $Tmp | Out-Null
    try {
        Copy-Item (Join-Path $Dest 'SKILL.md') (Join-Path $Tmp 'local.md')      # settings, byte-exact
        git -C $Dest fetch -q origin
        git -C $Dest update-index --no-skip-worktree SKILL.md 2>$null
        git -C $Dest checkout -q origin/main -- .
        git -C $Dest reset -q origin/main
        Copy-Item (Join-Path $Dest 'SKILL.md') (Join-Path $Tmp 'upstream.md')   # upstream, byte-exact
        Copy-Item (Join-Path $Tmp 'local.md') (Join-Path $Dest 'SKILL.md')      # local back...
        & (Join-Path $Dest 'install.ps1') sync -From (Join-Path $Tmp 'upstream.md')  # ...merge
        git -C $Dest update-index --skip-worktree SKILL.md 2>$null
    } finally {
        Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
    }
}

& (Join-Path $Dest 'install.ps1') install
Write-Host "done - '$Dest\install.ps1 status' shows what is linked."
