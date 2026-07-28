<#
.SYNOPSIS
    Bootstrap issue-flow, then delegate to the shared immutable-bundle installer.

.EXAMPLE
    .\install.ps1 install
    .\install.ps1 sync
    .\install.ps1 rollback
    .\install.ps1 recover
    .\install.ps1 config -Set 'Tracker=linear'
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'sync', 'uninstall', 'status', 'config', 'rollback', 'recover')]
    [string]$Command = 'install',
    [string]$From,
    [string]$Set,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$RepositoryUrl = 'https://github.com/asanabrial/issue-flow.git'

function Get-PythonCommand {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return @{ Executable = $python.Source; Prefix = @() } }
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) { return @{ Executable = $launcher.Source; Prefix = @('-3') } }
    throw 'Python 3 is required; install it and retry.'
}

function Invoke-Helper {
    param([string]$Path, [hashtable]$Python)
    $arguments = @($Python.Prefix) + @($Path, $Command)
    if ($From) { $arguments += @('--from', $From) }
    if ($Set) { $arguments += @('--set', $Set) }
    if ($DryRun) { $arguments += '--dry-run' }
    & $Python.Executable @arguments
    return $LASTEXITCODE
}

$python = Get-PythonCommand
$helper = if ($PSScriptRoot) { Join-Path $PSScriptRoot 'scripts\install_bundle.py' } else { $null }
if ($helper -and (Test-Path -LiteralPath $helper -PathType Leaf)) {
    exit (Invoke-Helper -Path $helper -Python $python)
}

if ($From) {
    throw 'single-file sync is retired; run sync without -From.'
}

$destination = Join-Path $HOME '.agents\skills\issue-flow'
if ($DryRun -and -not (Test-Path -LiteralPath $destination)) {
    Write-Host "would   install one complete Git tree at $destination"
    return
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'git is required; install it and retry.'
}

$bootstrap = Join-Path ([IO.Path]::GetTempPath()) ('issue-flow-bootstrap-' + [guid]::NewGuid().ToString('N'))
$hooks = Join-Path $bootstrap 'hooks'
$template = Join-Path $bootstrap 'template'
$bootstrapSource = Join-Path $bootstrap 'source'
New-Item -ItemType Directory -Path $hooks, $template | Out-Null

$gitNames = @(
    'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
    'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_COMMON_DIR', 'GIT_TEMPLATE_DIR',
    'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_GLOBAL',
    'GIT_CONFIG_NOSYSTEM', 'GIT_TERMINAL_PROMPT', 'GIT_ASKPASS'
)
$saved = @{}
foreach ($name in $gitNames) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, $null, 'Process')
}

try {
    $env:GIT_CONFIG_NOSYSTEM = '1'
    $env:GIT_CONFIG_GLOBAL = 'NUL'
    $env:GIT_CONFIG_COUNT = '0'
    $env:GIT_TERMINAL_PROMPT = '0'
    $env:GIT_ASKPASS = ''
    # PowerShell 7 can pass a syntactically correct native argv while Git still receives an empty
    # worktree path under some environment combinations. Python is already required and gives both
    # PowerShell editions the same exact subprocess boundary used by the installed helper.
    $bootstrapCode = @'
import os
import subprocess
import sys

repository, source, hooks, template = sys.argv[1:]
environment = os.environ.copy()
for name in tuple(environment):
    if name.startswith("GIT_"):
        environment.pop(name, None)
environment.update({
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_COUNT": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
})
file_protocol = "always" if repository.lower().startswith("file:") else "never"
os.mkdir(source)
common = ["git", "-c", f"core.hooksPath={hooks}"]
subprocess.run([*common, "init", "-q", f"--template={template}", source], env=environment, check=True)
subprocess.run([*common, "-C", source, "-c", f"protocol.file.allow={file_protocol}", "fetch", "-q", "--depth", "1", "--no-tags", repository, "refs/heads/main"], env=environment, check=True)
subprocess.run([*common, "-C", source, "checkout", "-q", "--detach", "FETCH_HEAD"], env=environment, check=True)
'@
    # Base64 keeps Windows PowerShell 5.1 from stripping quotes in the multiline `-c` argument.
    $encodedBootstrap = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bootstrapCode))
    $bootstrapArguments = @($python.Prefix) + @(
        '-c', 'import base64,sys;code=base64.b64decode(sys.argv[1]);del sys.argv[1];exec(code)',
        $encodedBootstrap, $RepositoryUrl, $bootstrapSource, $hooks, $template
    )
    & $python.Executable @bootstrapArguments
    if ($LASTEXITCODE -ne 0) { throw "bootstrap acquisition failed ($LASTEXITCODE)." }
    $result = Invoke-Helper -Path (Join-Path $bootstrapSource 'scripts\install_bundle.py') -Python $python
} finally {
    foreach ($name in $gitNames) {
        [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
    }
    Remove-Item -LiteralPath $bootstrap -Recurse -Force -ErrorAction SilentlyContinue
}
exit $result
