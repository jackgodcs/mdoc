[CmdletBinding()]
param(
  [string]$Python,
  [string]$Toolkit,
  [ValidateSet('Full', 'Core', 'Existing', 'Offline')] [string]$Profile = 'Full',
  [string]$RuntimeRoot = (Join-Path $env:LOCALAPPDATA 'mdoc'),
  [string]$Installation = (Join-Path $HOME '.codex\skills\mdoc'),
  [string]$Proxy,
  [switch]$AllowNetworkDownload,
  [switch]$SkipPathUpdate
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$packageRoot = $PSScriptRoot
$bootstrapPath = Join-Path $packageRoot 'bootstrap\toolchain-bootstrap.json'
if (-not (Test-Path -LiteralPath $bootstrapPath -PathType Leaf)) { throw 'MDOC-RUNTIME-BOOTSTRAP-MISSING: Toolchain Bootstrap is missing.' }
$bootstrap = Get-Content -LiteralPath $bootstrapPath -Raw | ConvertFrom-Json

function Get-Sha256([string]$Path) {
  $algorithm = [Security.Cryptography.SHA256]::Create()
  $stream = [IO.File]::OpenRead($Path)
  try {
    return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
  } finally {
    $stream.Dispose()
    $algorithm.Dispose()
  }
}
function Assert-Sha256([string]$Path, [string]$Expected, [string]$Code) {
  if ((Get-Sha256 $Path) -ne $Expected.ToLowerInvariant()) { throw "$Code`: $Path" }
}
function Expand-SafeZip([string]$Archive, [string]$Destination) {
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $root = [IO.Path]::GetFullPath($Destination) + [IO.Path]::DirectorySeparatorChar
  $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
  try {
    foreach ($entry in $zip.Entries) {
      $target = [IO.Path]::GetFullPath((Join-Path $Destination $entry.FullName))
      if (-not $target.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw "MDOC-RUNTIME-ARCHIVE-UNSAFE: $($entry.FullName)" }
    }
  } finally { $zip.Dispose() }
  New-Item -ItemType Directory -Path $Destination -Force | Out-Null
  [IO.Compression.ZipFile]::ExtractToDirectory($Archive, $Destination)
}
function Receive-MdocFile([string]$Uri, [string]$Destination) {
  if ($Proxy -and $Proxy.StartsWith('socks', [StringComparison]::OrdinalIgnoreCase)) {
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if (-not $curl) { throw 'MDOC-RUNTIME-SOCKS-CURL-MISSING: SOCKS5 downloads require Windows curl.exe.' }
    & $curl.Source --fail --location --silent --show-error --proxy $Proxy --output $Destination $Uri
    if ($LASTEXITCODE -ne 0) { throw "MDOC-RUNTIME-DOWNLOAD-FAILED: $Uri" }
    return
  }
  $parameters = @{UseBasicParsing=$true; Uri=$Uri; OutFile=$Destination}
  if ($Proxy) { $parameters.Proxy = $Proxy; $parameters.ProxyUseDefaultCredentials = $false }
  Invoke-WebRequest @parameters
}
function Test-MdocPython([string]$Executable) {
  if (-not $Executable -or -not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $null }
  $probe = 'import json,platform,struct,sys,venv,tkinter; print(json.dumps({"executable":sys.executable,"version":list(sys.version_info[:3]),"implementation":platform.python_implementation(),"bits":struct.calcsize("P")*8,"platform":sys.platform}))'
  $result = & $Executable -I -S -c $probe 2>$null
  if ($LASTEXITCODE -ne 0) { return $null }
  try { $identity = $result | ConvertFrom-Json } catch { return $null }
  if ($identity.implementation -ne 'CPython' -or $identity.version[0] -ne 3 -or $identity.version[1] -ne 12 -or $identity.bits -ne 64 -or $identity.platform -ne 'win32') { return $null }
  return $identity
}
function Find-MdocPython {
  $candidates = @($Python, (Join-Path $RuntimeRoot 'python\python.exe'), (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'), (Join-Path $HOME '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'))
  $py = Get-Command py.exe -ErrorAction SilentlyContinue
  if ($py) {
    $resolved = & $py.Source -3.12 -c 'import sys; print(sys.executable)' 2>$null
    if ($LASTEXITCODE -eq 0) { $candidates += $resolved }
  }
  foreach ($name in @('python.exe', 'python3.exe')) { $command = Get-Command $name -ErrorAction SilentlyContinue; if ($command) { $candidates += $command.Source } }
  foreach ($candidate in $candidates | Where-Object { $_ } | Select-Object -Unique) { $identity = Test-MdocPython $candidate; if ($identity) { return $identity } }
  return $null
}

$work = Join-Path $RuntimeRoot '.repair'
if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
New-Item -ItemType Directory -Path $work -Force | Out-Null
try {
  $toolkitArchive = $Toolkit
  if (-not $toolkitArchive) {
    if (-not $AllowNetworkDownload) { throw 'MDOC-RUNTIME-NETWORK-CONSENT-REQUIRED: Toolchain download requires explicit consent.' }
    $toolkitArchive = Join-Path $work 'toolchain.zip'
    Receive-MdocFile $bootstrap.toolkit_url $toolkitArchive
  }
  if (-not (Test-Path -LiteralPath $toolkitArchive -PathType Leaf)) { throw "MDOC-RUNTIME-TOOLKIT-MISSING: $toolkitArchive" }
  Assert-Sha256 $toolkitArchive $bootstrap.toolkit_sha256 'MDOC-RUNTIME-TOOLKIT-SHA256-MISMATCH'
  $toolkitRoot = Join-Path $work 'toolchain'
  Expand-SafeZip $toolkitArchive $toolkitRoot
  $catalogPath = Join-Path $toolkitRoot 'catalog-v1.json'
  Assert-Sha256 $catalogPath $bootstrap.catalog_sha256 'MDOC-RUNTIME-CATALOG-SHA256-MISMATCH'
  $catalog = Get-Content -LiteralPath $catalogPath -Raw | ConvertFrom-Json
  if ($catalog.schema_version -ne 1 -or $catalog.catalog_version -ne $bootstrap.catalog_version -or $catalog.platform -ne 'windows-x86_64') { throw 'MDOC-RUNTIME-CATALOG-INCOMPATIBLE: Catalog schema, version, or platform mismatch.' }
  foreach ($component in $catalog.components) {
    $asset = Join-Path $toolkitRoot (Join-Path 'components' $component.distribution.asset)
    Assert-Sha256 $asset $component.distribution.sha256 'MDOC-RUNTIME-COMPONENT-SHA256-MISMATCH'
  }

  $selected = Find-MdocPython
  $managedPythonRoot = [IO.Path]::GetFullPath((Join-Path $RuntimeRoot 'python')) + [IO.Path]::DirectorySeparatorChar
  $ownership = if ($selected -and [IO.Path]::GetFullPath([string]$selected.executable).StartsWith($managedPythonRoot, [StringComparison]::OrdinalIgnoreCase)) { 'managed-by-mdoc' } else { 'external' }
  if (-not $selected) {
    if ($Profile -eq 'Existing') { throw 'MDOC-RUNTIME-PYTHON-MISSING: Existing mode requires CPython 3.12 x64.' }
    $pythonComponent = $catalog.components | Where-Object id -eq 'python-runtime' | Select-Object -First 1
    $pythonBundle = Join-Path $toolkitRoot (Join-Path 'components' $pythonComponent.distribution.asset)
    $pythonFiles = Join-Path $work 'python-component'
    Expand-SafeZip $pythonBundle $pythonFiles
    $installer = Join-Path $pythonFiles 'python-3.12.10-amd64.exe'
    $pythonRoot = Join-Path $RuntimeRoot 'python'
    $process = Start-Process -FilePath $installer -ArgumentList @('/quiet','InstallAllUsers=0',"TargetDir=$pythonRoot",'Include_pip=1','Include_launcher=0','Include_tcltk=1','Include_test=0','PrependPath=0','Shortcuts=0') -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0) { throw "MDOC-RUNTIME-PYTHON-INSTALL-FAILED: $($process.ExitCode)" }
    $selected = Test-MdocPython (Join-Path $pythonRoot 'python.exe')
    if (-not $selected) { throw 'MDOC-RUNTIME-PYTHON-PROBE-FAILED: Python capability probe failed after installation.' }
    $ownership = 'managed-by-mdoc'
    $installerStore = Join-Path $RuntimeRoot 'installers'; New-Item -ItemType Directory -Path $installerStore -Force | Out-Null
    Copy-Item -LiteralPath $installer -Destination (Join-Path $installerStore 'python-3.12.10-amd64.exe') -Force
  }

  $wheelComponent = $catalog.components | Where-Object id -eq 'wheelhouse' | Select-Object -First 1
  $wheelBundle = Join-Path $toolkitRoot (Join-Path 'components' $wheelComponent.distribution.asset)
  $wheelRoot = Join-Path $work 'wheelhouse'
  Expand-SafeZip $wheelBundle $wheelRoot
  $lockName = if ($Profile -eq 'Core') { 'core.txt' } else { 'full.txt' }
  $stage = Join-Path $RuntimeRoot 'runtime.new'
  if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
  & $selected.executable -m venv $stage
  if ($LASTEXITCODE -ne 0) { throw 'MDOC-RUNTIME-VENV-CREATE-FAILED: Failed to create the isolated environment.' }
  $runtimePython = Join-Path $stage 'Scripts\python.exe'
  & $runtimePython -m pip install --disable-pip-version-check --no-index --require-hashes --find-links (Join-Path $wheelRoot 'wheels') -r (Join-Path $wheelRoot (Join-Path 'locks' $lockName))
  if ($LASTEXITCODE -ne 0) { throw 'MDOC-RUNTIME-WHEEL-INSTALL-FAILED: Offline dependency installation failed.' }
  $probe = if ($Profile -eq 'Core') { 'import jsonschema,ruamel.yaml' } else { 'import jsonschema,ruamel.yaml,pdfplumber,pypdf,pypdfium2,PIL,tkinter; from PIL import ImageTk,ImageGrab' }
  & $runtimePython -c $probe
  if ($LASTEXITCODE -ne 0) { throw 'MDOC-RUNTIME-CAPABILITY-PROBE-FAILED: Runtime capability probe failed.' }
  $current = Join-Path $RuntimeRoot 'runtime'
  $old = Join-Path $RuntimeRoot 'runtime.old'
  if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force }
  if (Test-Path -LiteralPath $current) { Move-Item -LiteralPath $current -Destination $old }
  try {
    Move-Item -LiteralPath $stage -Destination $current
  } catch {
    if (-not (Test-Path -LiteralPath $current) -and (Test-Path -LiteralPath $old)) { Move-Item -LiteralPath $old -Destination $current }
    throw
  }
  if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force }

  $bin = Join-Path $RuntimeRoot 'bin'; New-Item -ItemType Directory -Path $bin -Force | Out-Null
  $launcher = Join-Path $bin 'mdoc.cmd'
  $launcherText = "@echo off`r`n`"$(Join-Path $current 'Scripts\python.exe')`" `"$(Join-Path $Installation 'scripts\mdoc.py')`" %*`r`n"
  [IO.File]::WriteAllText($launcher, $launcherText, [Text.UTF8Encoding]::new($false))
  if (-not $SkipPathUpdate) {
    $userPath = [Environment]::GetEnvironmentVariable('Path','User')
    $parts = @($userPath -split ';' | Where-Object { $_ })
    if ($parts -notcontains $bin) { [Environment]::SetEnvironmentVariable('Path', (($parts + $bin) -join ';'), 'User') }
  }
  $startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\mdoc'; New-Item -ItemType Directory -Path $startMenu -Force | Out-Null
  Copy-Item -LiteralPath $launcher -Destination (Join-Path $startMenu 'mdoc 命令行.cmd') -Force
  $stateRoot = Join-Path $RuntimeRoot 'state'; New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
  $pythonSource = if ($ownership -eq 'managed-by-mdoc') { 'mdoc-managed' } elseif ([string]$selected.executable -like '*\.cache\codex-runtimes\*') { 'codex-runtime' } else { 'system-or-user' }
  $requirementsHash = Get-Sha256 (Join-Path $packageRoot 'runtime\requirements-v1.json')
  $state = [ordered]@{schema_version=1; status='ready'; profile=$Profile; catalog_version=$catalog.catalog_version; toolchain_version=$catalog.catalog_version; python_contract='>=3.12.0,<3.13.0'; requirements_sha256=$requirementsHash; capability_probe='ready'; python_source=$pythonSource; python_base=$selected.executable; python_ownership=$ownership; python_installer=if($ownership -eq 'managed-by-mdoc'){(Join-Path $RuntimeRoot 'installers\python-3.12.10-amd64.exe')}else{$null}; runtime_python=(Join-Path $current 'Scripts\python.exe'); path_entry=$bin; start_menu=$startMenu; capabilities=if($Profile -eq 'Core'){@('core')}else{@('core','pdf-check','screenshot-assistant')}}
  $state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $stateRoot 'installed-runtime.json') -Encoding utf8
  $state | ConvertTo-Json -Depth 5
} finally {
  if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
}
