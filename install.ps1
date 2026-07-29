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
    if ($python) {
        & $python.Source -I -c 'import sys;raise SystemExit(sys.version_info < (3, 10))' 2>$null
        if ($LASTEXITCODE -eq 0) { return @{ Executable = $python.Source; Prefix = @() } }
    }
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -3 -I -c 'import sys;raise SystemExit(sys.version_info < (3, 10))' 2>$null
        if ($LASTEXITCODE -eq 0) { return @{ Executable = $launcher.Source; Prefix = @('-3') } }
    }
    throw 'Python 3.10 or newer is required; install it and retry.'
}

function Invoke-Helper {
    param([string]$Path, [hashtable]$Python)
    $arguments = @($Python.Prefix) + @('-I', $Path, $Command)
    if ($From) { $arguments += @('--from', $From) }
    if ($Set) { $arguments += @('--set', $Set) }
    if ($DryRun) { $arguments += '--dry-run' }
    & $Python.Executable @arguments
    $script:HelperResult = $LASTEXITCODE
}

function Test-LocalHelper {
    param([string]$Path, [string]$Receipt, [string]$Repository, [string]$Current, [string]$Git, [hashtable]$Python)
    $code = @'
import json
import os
import re
import subprocess
import sys

helper, receipt_path, repository, current_path, git = sys.argv[1:]
try:
    version = subprocess.check_output([git, "--version"], text=True)
    match = re.search(r"\b(\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < (2, 36):
        raise ValueError("old Git")
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
'@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($code))
    $arguments = @($Python.Prefix) + @(
        '-I', '-c', 'import base64,sys;code=base64.b64decode(sys.argv[1]);del sys.argv[1];exec(code)',
        $encoded, $Path, $Receipt, $Repository, $Current, $Git
    )
    & $Python.Executable @arguments
    return $LASTEXITCODE -eq 0
}

$python = Get-PythonCommand
$invocationPath = $MyInvocation.MyCommand.Path
$canonicalScript = Join-Path $HOME '.agents\skills\issue-flow\install.ps1'
$isCanonicalInvocation = $invocationPath -and (
    [IO.Path]::GetFullPath($invocationPath) -eq [IO.Path]::GetFullPath($canonicalScript)
)
$helper = if ($isCanonicalInvocation) {
    Join-Path ([IO.Path]::GetDirectoryName($invocationPath)) 'scripts\install_bundle.py'
} else { $null }
$gitCommand = $null
if ($helper -and (Test-Path -LiteralPath $helper -PathType Leaf)) {
    $gitCommand = Get-Command git -ErrorAction SilentlyContinue
    $receipt = Join-Path ([IO.Path]::GetDirectoryName($invocationPath)) '.issue-flow-bundle.json'
    $repository = Join-Path $HOME '.agents\skills\.issue-flow\repository.git'
    $current = Join-Path $HOME '.agents\skills\.issue-flow\current.json'
    if ($gitCommand -and (Test-LocalHelper -Path $helper -Receipt $receipt -Repository $repository -Current $current -Git $gitCommand.Source -Python $python)) {
        Invoke-Helper -Path $helper -Python $python
        exit $script:HelperResult
    }
    Write-Warning 'Local installer helper failed Git-object verification; reacquiring canonical main.'
}

if ($From) {
    throw 'single-file sync is retired; run sync without -From.'
}

$destination = Join-Path $HOME '.agents\skills\issue-flow'
if ($DryRun -and -not (Test-Path -LiteralPath $destination)) {
    Write-Host "would   install one complete Git tree at $destination"
    return
}
if (-not $gitCommand) { $gitCommand = Get-Command git -ErrorAction SilentlyContinue }
if (-not $gitCommand) {
    throw 'git is required; install it and retry.'
}

$bootstrap = Join-Path ([IO.Path]::GetTempPath()) ('issue-flow-bootstrap-' + [guid]::NewGuid().ToString('N'))
$bootstrapSource = Join-Path $bootstrap 'source'
$bootstrapRepository = Join-Path $bootstrap 'repository.git'
New-Item -ItemType Directory -Path $bootstrap | Out-Null

$gitNames = @(
    'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
    'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_COMMON_DIR', 'GIT_TEMPLATE_DIR', 'GIT_EXEC_PATH',
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
import re
import subprocess
import sys

repository, source, bare, git_executable = sys.argv[1:]
environment = os.environ.copy()
for name in tuple(environment):
    if name.startswith("GIT_"):
        environment.pop(name, None)
environment.update({
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_COUNT": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
})
file_protocol = "always" if repository.lower().startswith("file:") else "never"
disabled_hooks = "NUL" if os.name == "nt" else "/dev/null"
version = subprocess.check_output([git_executable, "--version"], env=environment, text=True)
match = re.search(r"\b(\d+)\.(\d+)", version)
if not match or tuple(map(int, match.groups())) < (2, 36):
    raise RuntimeError(f"Git 2.36 or newer is required, got {version.strip()}")
os.makedirs(os.path.join(source, "scripts"))
common = [git_executable, "--no-replace-objects", "-c", f"core.hooksPath={disabled_hooks}", "-c", "credential.helper="]
subprocess.run([
    *common,
    "-c", "protocol.allow=never",
    "-c", "protocol.ext.allow=never",
    "-c", f"protocol.file.allow={file_protocol}",
    "-c", "protocol.https.allow=always",
    "clone", "-q", "--bare", "--no-tags", "--single-branch", "--branch", "main",
    repository, bare,
], env=environment, check=True)
helper = subprocess.run([
    git_executable, "--no-replace-objects", f"--git-dir={bare}",
    "-c", f"core.hooksPath={disabled_hooks}", "-c", "core.fsmonitor=false",
    "show", "refs/heads/main:scripts/install_bundle.py",
], env=environment, check=True, stdout=subprocess.PIPE).stdout
with open(os.path.join(source, "scripts", "install_bundle.py"), "xb") as handle:
    handle.write(helper)
'@
    # Base64 keeps Windows PowerShell 5.1 from stripping quotes in the multiline `-c` argument.
    $encodedBootstrap = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bootstrapCode))
    $bootstrapArguments = @($python.Prefix) + @(
        '-I', '-c', 'import base64,sys;code=base64.b64decode(sys.argv[1]);del sys.argv[1];exec(code)',
        $encodedBootstrap, $RepositoryUrl, $bootstrapSource, $bootstrapRepository, $gitCommand.Source
    )
    & $python.Executable @bootstrapArguments
    if ($LASTEXITCODE -ne 0) { throw "bootstrap acquisition failed ($LASTEXITCODE)." }
    Invoke-Helper -Path (Join-Path $bootstrapSource 'scripts\install_bundle.py') -Python $python
    $result = $script:HelperResult
} finally {
    foreach ($name in $gitNames) {
        [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
    }
    Remove-Item -LiteralPath $bootstrap -Recurse -Force -ErrorAction SilentlyContinue
}
if ($result -ne 0) { throw "issue-flow installer failed ($result)." }
return
