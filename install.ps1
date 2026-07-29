<#
.SYNOPSIS
    Bootstrap issue-flow, then delegate to the shared immutable-bundle installer.

.EXAMPLE
    .\install.ps1 install
    .\install.ps1 sync
    .\install.ps1 rollback
    .\install.ps1 recover
    .\install.ps1 config -Set 'Tracker=linear'

.PARAMETER From
    Retired. This option always fails because one file cannot prove a complete runtime bundle.
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
$fromSpecified = $PSBoundParameters.ContainsKey('From')
$setSpecified = $PSBoundParameters.ContainsKey('Set')

function Get-PythonCommand {
    $expectedPlatform = if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) { 'win32' } else { '' }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        & $python.Source -X utf8 -I -c 'import sys;raise SystemExit(sys.version_info < (3, 10) or (sys.argv[1] and sys.platform != sys.argv[1]))' $expectedPlatform 2>$null
        if ($LASTEXITCODE -eq 0) { return @{ Executable = $python.Source; Prefix = @() } }
    }
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -3 -X utf8 -I -c 'import sys;raise SystemExit(sys.version_info < (3, 10) or (sys.argv[1] and sys.platform != sys.argv[1]))' $expectedPlatform 2>$null
        if ($LASTEXITCODE -eq 0) { return @{ Executable = $launcher.Source; Prefix = @('-3') } }
    }
    throw 'A host-native Python 3.10 or newer is required; install it and retry.'
}

function Get-InstallerArguments {
    $arguments = @($Command)
    if ($fromSpecified) { $arguments += @('--from', $From) }
    if ($setSpecified) { $arguments += @('--set', $Set) }
    if ($DryRun) { $arguments += '--dry-run' }
    return $arguments
}

function Invoke-VerifiedLocalHelper {
    param([string]$Path, [string]$Receipt, [string]$Repository, [string]$Current, [string]$Transaction, [string]$Git, [hashtable]$Python, [string]$HomePath, [string[]]$InstallerArguments)
    $code = @'
import configparser
import json
import os
import re
import subprocess
import sys

helper, receipt_path, repository, current_path, transaction_path, git = sys.argv[1:7]
installer_arguments = sys.argv[7:]

def is_pointer(path):
    junction = getattr(os.path, "isjunction", lambda _path: False)
    return os.path.islink(path) or junction(path)

def validate_repository(path):
    if is_pointer(path) or not os.path.isdir(path) or os.path.lexists(os.path.join(path, "commondir")):
        raise ValueError("redirected repository")
    for name in ("objects", "refs"):
        candidate = os.path.join(path, name)
        if is_pointer(candidate) or not os.path.isdir(candidate):
            raise ValueError("linked repository directory")
    for name in ("config", "HEAD", "packed-refs"):
        candidate = os.path.join(path, name)
        if name == "packed-refs" and not os.path.lexists(candidate):
            continue
        details = os.lstat(candidate)
        if is_pointer(candidate) or not os.path.isfile(candidate) or details.st_nlink != 1:
            raise ValueError("linked repository authority")
    for directory, names, files in os.walk(os.path.join(path, "refs"), followlinks=False):
        for name in names:
            candidate = os.path.join(directory, name)
            if is_pointer(candidate) or not os.path.isdir(candidate):
                raise ValueError("linked ref directory")
        for name in files:
            candidate = os.path.join(directory, name)
            if is_pointer(candidate) or not os.path.isfile(candidate) or os.lstat(candidate).st_nlink != 1:
                raise ValueError("linked ref")
            if open(candidate, "rb").read().lstrip().startswith(b"ref:"):
                raise ValueError("symbolic ref")
    for name in ("alternates", "http-alternates"):
        if os.path.lexists(os.path.join(path, "objects", "info", name)):
            raise ValueError("alternate object database")
    parser = configparser.RawConfigParser(interpolation=None, strict=True)
    parser.read(os.path.join(path, "config"), encoding="utf-8")
    allowed = {"repositoryformatversion", "filemode", "bare", "logallrefupdates", "symlinks", "ignorecase", "precomposeunicode", "fsync", "fsyncmethod"}
    if parser.sections() != ["core"] or set(parser["core"]) - allowed:
        raise ValueError("repository config authority")
    if parser["core"].get("bare", "").casefold() != "true" or parser["core"].get("fsync", "").casefold() != "reference" or parser["core"].get("fsyncmethod", "").casefold() != "fsync":
        raise ValueError("repository config durability")

try:
    validate_repository(repository)
    environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    environment.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
    })
    version = subprocess.check_output([git, "--version"], env=environment, text=True)
    match = re.search(r"\b(\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < (2, 36):
        raise ValueError("old Git")
    receipt = json.loads(open(receipt_path, encoding="utf-8").read())
    commit = str(receipt["commit"])
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise ValueError("invalid commit")
    bundle_commit = os.path.basename(os.path.dirname(os.path.dirname(os.path.realpath(helper))))
    if bundle_commit != commit:
        raise ValueError("helper is not in its receipt-named bundle")
    authoritative = subprocess.check_output([
        git,
        "--no-replace-objects",
        f"--git-dir={repository}",
        "-c", f"core.hooksPath={os.devnull}",
        "-c", "core.fsmonitor=false",
        "cat-file", "blob", f"{commit}:scripts/install_bundle.py",
    ], env=environment)
    try:
        current = json.loads(open(current_path, encoding="utf-8").read())
        activated = subprocess.check_output([
            git,
            "--no-replace-objects",
            f"--git-dir={repository}",
            "-c", f"core.hooksPath={os.devnull}",
            "rev-parse", "--verify", f"refs/issue-flow/activated/{commit}^{{commit}}",
        ], env=environment, text=True).strip()
        normal_identity = current.get("current") == commit and activated == commit
    except (OSError, subprocess.SubprocessError, ValueError):
        normal_identity = False
    try:
        transaction = json.loads(open(transaction_path, encoding="utf-8").read())
        recovery_identity = transaction.get("schema") == 1 and commit in {
            transaction.get("previous"), transaction.get("target")
        }
    except (OSError, ValueError):
        recovery_identity = False
    if not normal_identity and not recovery_identity:
        raise ValueError("helper is neither active nor a declared recovery endpoint")
    actual = open(helper, "rb").read()
except (KeyError, OSError, subprocess.SubprocessError, ValueError):
    raise SystemExit(125)
if authoritative != actual:
    raise SystemExit(125)
os.environ["ISSUE_FLOW_GIT"] = os.path.realpath(git)
sys.argv = [helper, *installer_arguments]
namespace = {"__name__": "__main__", "__file__": helper}
exec(compile(actual, helper, "exec"), namespace)
'@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($code))
    $arguments = @($Python.Prefix) + @(
        '-X', 'utf8', '-I', '-c', 'import base64,sys;code=base64.b64decode(sys.argv[1]);del sys.argv[1];exec(code)',
        $encoded, $Path, $Receipt, $Repository, $Current, $Transaction, $Git
    ) + $InstallerArguments
    $savedHome = [Environment]::GetEnvironmentVariable('HOME', 'Process')
    $savedProfile = [Environment]::GetEnvironmentVariable('USERPROFILE', 'Process')
    $savedInstallerHome = [Environment]::GetEnvironmentVariable('ISSUE_FLOW_HOME', 'Process')
    $savedInstallerGit = [Environment]::GetEnvironmentVariable('ISSUE_FLOW_GIT', 'Process')
    try {
        [Environment]::SetEnvironmentVariable('HOME', $HomePath, 'Process')
        [Environment]::SetEnvironmentVariable('USERPROFILE', $HomePath, 'Process')
        [Environment]::SetEnvironmentVariable('ISSUE_FLOW_HOME', $HomePath, 'Process')
        [Environment]::SetEnvironmentVariable('ISSUE_FLOW_GIT', $Git, 'Process')
        & $Python.Executable @arguments
        $script:LocalHelperResult = $LASTEXITCODE
    } finally {
        [Environment]::SetEnvironmentVariable('HOME', $savedHome, 'Process')
        [Environment]::SetEnvironmentVariable('USERPROFILE', $savedProfile, 'Process')
        [Environment]::SetEnvironmentVariable('ISSUE_FLOW_HOME', $savedInstallerHome, 'Process')
        [Environment]::SetEnvironmentVariable('ISSUE_FLOW_GIT', $savedInstallerGit, 'Process')
    }
}

$python = Get-PythonCommand
$homeArguments = @($python.Prefix) + @('-X', 'utf8', '-I', '-c', 'import base64,os,sys;print(base64.b64encode(os.path.realpath(sys.argv[1]).encode()).decode())', $HOME)
$resolvedHome = & $python.Executable @homeArguments
if ($LASTEXITCODE -ne 0 -or -not $resolvedHome) { throw 'Could not resolve the operator home with the selected Python.' }
$resolvedHome = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([string]$resolvedHome))
$invocationPath = $MyInvocation.MyCommand.Path
$physicalInvocation = $null
if ($invocationPath) {
    $pathArguments = @($python.Prefix) + @('-X', 'utf8', '-I', '-c', 'import base64,os,sys;print(base64.b64encode(os.path.realpath(sys.argv[1]).encode()).decode())', $invocationPath)
    $physicalInvocation = & $python.Executable @pathArguments
    if ($LASTEXITCODE -ne 0) { throw 'Could not resolve the installer path with the selected Python.' }
    $physicalInvocation = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([string]$physicalInvocation))
}
$helper = if ($physicalInvocation -and [IO.Path]::GetFileName($physicalInvocation) -eq 'install.ps1') {
    Join-Path ([IO.Path]::GetDirectoryName($physicalInvocation)) 'scripts\install_bundle.py'
} else { $null }
$gitCommand = $null
if ($helper -and (Test-Path -LiteralPath $helper -PathType Leaf)) {
    $gitCommand = Get-Command git -ErrorAction SilentlyContinue
    $receipt = Join-Path ([IO.Path]::GetDirectoryName($physicalInvocation)) '.issue-flow-bundle.json'
    $repository = Join-Path $resolvedHome '.agents\skills\.issue-flow\repository.git'
    $current = Join-Path $resolvedHome '.agents\skills\.issue-flow\current.json'
    $transaction = Join-Path $resolvedHome '.agents\skills\.issue-flow\transaction.json'
    if ($gitCommand) {
        Invoke-VerifiedLocalHelper -Path $helper -Receipt $receipt -Repository $repository -Current $current -Transaction $transaction -Git $gitCommand.Source -Python $python -HomePath $resolvedHome -InstallerArguments @(Get-InstallerArguments)
        if ($script:LocalHelperResult -ne 125) { exit $script:LocalHelperResult }
    }
    Write-Warning 'Local installer helper failed Git-object verification; reacquiring canonical main.'
}

if ($fromSpecified) {
    throw 'single-file sync is retired; run sync without -From.'
}

$destination = Join-Path $resolvedHome '.agents\skills\issue-flow'
$statePath = Join-Path $resolvedHome '.agents\skills\.issue-flow'
$destinationPresent = $null -ne (Get-Item -LiteralPath $destination -Force -ErrorAction SilentlyContinue)
$statePresent = $null -ne (Get-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue)
$runtimePresent = @(
    Join-Path $resolvedHome '.claude\skills\issue-flow'
    Join-Path $resolvedHome '.codex\skills\issue-flow'
) | Where-Object { $null -ne (Get-Item -LiteralPath $_ -Force -ErrorAction SilentlyContinue) }
$runtimeRootPresent = @(
    Join-Path $resolvedHome '.claude'
    Join-Path $resolvedHome '.codex'
) | Where-Object { $null -ne (Get-Item -LiteralPath $_ -Force -ErrorAction SilentlyContinue) }
if ($DryRun -and -not $setSpecified -and -not $fromSpecified -and $Command -in @('install', 'sync') -and -not $destinationPresent -and -not $statePresent -and -not $runtimePresent -and -not $runtimeRootPresent) {
    Write-Host "would   install one complete Git tree at $destination"
    return
}
if (-not $gitCommand) { $gitCommand = Get-Command git -ErrorAction SilentlyContinue }
if (-not $gitCommand) {
    throw 'git is required; install it and retry.'
}

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
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path

repository, git_executable, home_value, *installer_arguments = sys.argv[1:]
git_executable = os.path.realpath(git_executable)
home = Path(home_value).resolve(strict=True)

def lock_owner(handle, blocking):
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return True
    except BlockingIOError:
        return False

def make_writable(function, target, _error):
    details = os.lstat(target)
    if stat.S_ISREG(details.st_mode) and details.st_nlink != 1:
        raise RuntimeError(f"refusing to chmod externally hard-linked bootstrap state: {target}")
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    function(target)

def remove_bootstrap(path):
    shutil.rmtree(path, onerror=make_writable)
    if path.exists():
        raise RuntimeError(f"bootstrap quarantine cleanup did not complete: {path}")

guard_path = home / ".issue-flow-bootstrap.lock"
if os.path.lexists(guard_path) and guard_path.is_symlink():
    raise RuntimeError(f"bootstrap guard may not be a link: {guard_path}")
guard_flags = os.O_RDWR | os.O_CREAT
if hasattr(os, "O_NOFOLLOW"):
    guard_flags |= os.O_NOFOLLOW
guard_descriptor = os.open(guard_path, guard_flags, 0o600)
guard_details = os.fstat(guard_descriptor)
if not stat.S_ISREG(guard_details.st_mode) or guard_details.st_nlink != 1:
    os.close(guard_descriptor)
    raise RuntimeError(f"bootstrap guard is not a private regular file: {guard_path}")
bootstrap_guard = os.fdopen(guard_descriptor, "r+b")
if guard_details.st_size == 0:
    bootstrap_guard.write(b"\0")
    bootstrap_guard.flush()
    os.fsync(bootstrap_guard.fileno())
if not lock_owner(bootstrap_guard, blocking=True):
    raise RuntimeError(f"could not acquire bootstrap guard: {guard_path}")

for candidate in home.iterdir():
    if not re.fullmatch(r"\.issue-flow-bootstrap-[0-9a-f]{32}", candidate.name):
        continue
    if candidate.is_symlink() or not candidate.is_dir():
        raise RuntimeError(f"installer-shaped bootstrap path is not a real directory: {candidate}")
    owner = candidate / ".issue-flow-bootstrap-owner"
    if not owner.exists():
        if not any(candidate.iterdir()):
            candidate.rmdir()
            continue
        raise RuntimeError(f"non-empty bootstrap quarantine has no owner marker: {candidate}")
    details = os.lstat(owner)
    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        raise RuntimeError(f"bootstrap owner marker is not private: {owner}")
    owner_probe = owner.open("r+b")
    if not lock_owner(owner_probe, blocking=False):
        owner_probe.close()
        continue
    unexpected = {item.name for item in candidate.iterdir()} - {owner.name, "repository.git"}
    owner_probe.close()
    if unexpected:
        raise RuntimeError(f"bootstrap quarantine contains unowned entries: {candidate}: {sorted(unexpected)}")
    if candidate.exists():
        remove_bootstrap(candidate)

bootstrap = home / f".issue-flow-bootstrap-{uuid.uuid4().hex}"
os.mkdir(bootstrap, 0o700)
details = os.lstat(bootstrap)
if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
    raise RuntimeError(f"bootstrap quarantine is not a private directory: {bootstrap}")
owner_path = bootstrap / ".issue-flow-bootstrap-owner"
owner_handle = owner_path.open("x+b")
owner_handle.write(b"\0")
owner_handle.flush()
os.fsync(owner_handle.fileno())
if not lock_owner(owner_handle, blocking=True):
    raise RuntimeError(f"could not lock bootstrap owner marker: {owner_path}")
owner_handle.seek(0)
owner_handle.write(b"issue-flow-bootstrap-v1\n")
owner_handle.truncate()
owner_handle.flush()
os.fsync(owner_handle.fileno())
if os.name != "nt":
    directory = os.open(bootstrap, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
bootstrap_guard.close()
bare = bootstrap / "repository.git"
environment = os.environ.copy()
for name in tuple(environment):
    if name.startswith("GIT_") or name in {"SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE"}:
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
    "GIT_NO_LAZY_FETCH": "1",
})
file_protocol = "always" if repository.lower().startswith("file:") else "never"
disabled_hooks = "NUL" if os.name == "nt" else "/dev/null"
version = subprocess.check_output([git_executable, "--version"], env=environment, text=True)
match = re.search(r"\b(\d+)\.(\d+)", version)
if not match or tuple(map(int, match.groups())) < (2, 36):
    raise RuntimeError(f"Git 2.36 or newer is required, got {version.strip()}")
common = [git_executable, "--no-replace-objects", "-c", f"core.hooksPath={disabled_hooks}", "-c", "credential.helper="]
try:
    subprocess.run([
        *common,
        "-c", "protocol.allow=never",
        "-c", "protocol.ext.allow=never",
        "-c", f"protocol.file.allow={file_protocol}",
        "-c", "protocol.https.allow=always",
        "clone", "-q", "--bare", "--no-tags", "--single-branch", "--branch", "main",
        repository, str(bare),
    ], env=environment, check=True)
    commit = subprocess.check_output([
        git_executable, "--no-replace-objects", f"--git-dir={bare}",
        "-c", f"core.hooksPath={disabled_hooks}", "rev-parse", "refs/heads/main^{commit}",
    ], env=environment, text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise RuntimeError("canonical main did not resolve to a commit")
    helper = subprocess.check_output([
        git_executable, "--no-replace-objects", f"--git-dir={bare}",
        "-c", f"core.hooksPath={disabled_hooks}", "-c", "core.fsmonitor=false",
        "cat-file", "blob", f"{commit}:scripts/install_bundle.py",
    ], env=environment)
    os.environ["ISSUE_FLOW_HOME"] = str(home)
    os.environ["ISSUE_FLOW_GIT"] = git_executable
    sys.argv = [f"git:{commit}:scripts/install_bundle.py", *installer_arguments]
    namespace = {"__name__": "__main__", "__file__": sys.argv[0]}
    exec(compile(helper, sys.argv[0], "exec"), namespace)
finally:
    owner_handle.close()
    if bootstrap.exists():
        remove_bootstrap(bootstrap)
'@
    # Base64 keeps Windows PowerShell 5.1 from stripping quotes in the multiline `-c` argument.
    $encodedBootstrap = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bootstrapCode))
    $installerArguments = @(Get-InstallerArguments)
    $bootstrapArguments = @($python.Prefix) + @(
        '-X', 'utf8', '-I', '-c', 'import base64,sys;code=base64.b64decode(sys.argv[1]);del sys.argv[1];exec(code)',
        $encodedBootstrap, $RepositoryUrl, $gitCommand.Source, $resolvedHome
    ) + $installerArguments
    & $python.Executable @bootstrapArguments
    if ($LASTEXITCODE -ne 0) { throw "bootstrap acquisition failed ($LASTEXITCODE)." }
} finally {
    foreach ($name in $gitNames) {
        [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
    }
}
return
