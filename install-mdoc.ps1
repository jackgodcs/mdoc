[CmdletBinding()]
param(
  [ValidateSet('Full', 'Offline')] [string]$Profile = 'Full',
  [string]$Python,
  [string]$Toolkit,
  [string]$Destination = (Join-Path $HOME '.codex\skills\mdoc'),
  [string]$RuntimeRoot = (Join-Path $env:LOCALAPPDATA 'mdoc'),
  [string]$Proxy,
  [switch]$AllowNetworkDownload,
  [switch]$SkipRuntimeRepair
)
$ErrorActionPreference = 'Stop'
$packageRoot = $PSScriptRoot
$originalPackageRoot = [IO.Path]::GetFullPath($packageRoot)
$sourcePackageStaging = $null
$pathEntry = [IO.Path]::GetFullPath((Join-Path $RuntimeRoot 'bin'))
$userPathBefore = [Environment]::GetEnvironmentVariable('Path', 'User')
$pathWasPresent = @($userPathBefore -split ';' | Where-Object { $_ } | ForEach-Object { try { [IO.Path]::GetFullPath($_) } catch { $_ } }) -contains $pathEntry
$existingOwnershipPath = Join-Path $RuntimeRoot 'state\uninstall.json'
$existingOwnership = if (Test-Path -LiteralPath $existingOwnershipPath -PathType Leaf) { Get-Content -LiteralPath $existingOwnershipPath -Encoding UTF8 -Raw | ConvertFrom-Json } else { $null }
$trustedLegacyInstall = -not $existingOwnership -and (Test-Path -LiteralPath (Join-Path $Destination 'SKILL.md') -PathType Leaf) -and (Test-Path -LiteralPath (Join-Path $RuntimeRoot 'state\installed-runtime.json') -PathType Leaf) -and (Test-Path -LiteralPath (Join-Path $RuntimeRoot 'state\records\latest-update.json') -PathType Leaf)
if ($existingOwnership -and (([IO.Path]::GetFullPath([string]$existingOwnership.installation) -ne [IO.Path]::GetFullPath($Destination)) -or ([IO.Path]::GetFullPath([string]$existingOwnership.runtime_root) -ne [IO.Path]::GetFullPath($RuntimeRoot)))) {
  throw 'MDOC-INSTALL-LOCATION-CHANGE-REQUIRES-UNINSTALL: Uninstall the existing managed installation before changing Destination or RuntimeRoot.'
}

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
function Find-MdocLocalToolkit([string]$Root, [object]$Bootstrap) {
  $name = [IO.Path]::GetFileName(([Uri]$Bootstrap.toolkit_url).AbsolutePath)
  $candidates = @(
    (Join-Path $Root $name),
    (Join-Path $Root 'mdoc-toolchain.zip')
  )
  foreach ($candidate in $candidates | Select-Object -Unique) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { return [IO.Path]::GetFullPath($candidate) }
  }
  return $null
}
function Register-MdocUninstall([object]$PackageManifest) {
  $stateRoot = Join-Path $RuntimeRoot 'state'
  $runtimeStatePath = Join-Path $stateRoot 'installed-runtime.json'
  if (-not (Test-Path -LiteralPath $runtimeStatePath -PathType Leaf)) { throw 'MDOC-INSTALL-RUNTIME-STATE-MISSING: Runtime ownership state was not created.' }
  $runtimeState = Get-Content -LiteralPath $runtimeStatePath -Encoding UTF8 -Raw | ConvertFrom-Json
  $ownershipPath = Join-Path $stateRoot 'uninstall.json'
  $existing = if (Test-Path -LiteralPath $ownershipPath -PathType Leaf) { Get-Content -LiteralPath $ownershipPath -Encoding UTF8 -Raw | ConvertFrom-Json } else { $null }
  $installationId = if ($existing -and $existing.installation_id) { [string]$existing.installation_id } else { [guid]::NewGuid().ToString('N') }
  $uninstallRoot = Join-Path $RuntimeRoot 'uninstall'
  New-Item -ItemType Directory -Path $uninstallRoot -Force | Out-Null
  Copy-Item -LiteralPath (Join-Path $packageRoot 'uninstall-mdoc.ps1') -Destination $uninstallRoot -Force
  Copy-Item -LiteralPath (Join-Path $packageRoot 'UnInstall-mdoc.cmd') -Destination $uninstallRoot -Force
  Copy-Item -LiteralPath (Join-Path $packageRoot 'runtime-bootstrap\mdoc_uninstall.py') -Destination $uninstallRoot -Force
  $startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\mdoc'
  New-Item -ItemType Directory -Path $startMenu -Force | Out-Null
  $cliLauncher = Join-Path $RuntimeRoot 'bin\mdoc.cmd'
  if (-not (Test-Path -LiteralPath $cliLauncher -PathType Leaf)) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $cliLauncher) -Force | Out-Null
    $runtimePython = if ($runtimeState.runtime_python) { [string]$runtimeState.runtime_python } else { $pythonCommand }
    $launcherText = "@echo off`r`n`"$runtimePython`" `"$(Join-Path $Destination 'scripts\mdoc.py')`" %*`r`n"
    [IO.File]::WriteAllText($cliLauncher, $launcherText, [Text.UTF8Encoding]::new($false))
  }
  Copy-Item -LiteralPath $cliLauncher -Destination (Join-Path $startMenu 'mdoc CLI.cmd') -Force
  Copy-Item -LiteralPath (Join-Path $uninstallRoot 'UnInstall-mdoc.cmd') -Destination (Join-Path $startMenu 'UnInstall mdoc.cmd') -Force
  $registrySubKey = if ($env:MDOC_TEST_UNINSTALL_REGISTRY) { $env:MDOC_TEST_UNINSTALL_REGISTRY } else { 'Software\Microsoft\Windows\CurrentVersion\Uninstall\mdoc' }
  $registryPath = 'HKCU:\' + $registrySubKey
  New-Item -Path $registryPath -Force | Out-Null
  $uninstallCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $uninstallRoot 'uninstall-mdoc.ps1') + '"'
  New-ItemProperty -Path $registryPath -Name DisplayName -Value 'mdoc' -PropertyType String -Force | Out-Null
  New-ItemProperty -Path $registryPath -Name DisplayVersion -Value ([string]$PackageManifest.version) -PropertyType String -Force | Out-Null
  New-ItemProperty -Path $registryPath -Name Publisher -Value 'mdoc contributors' -PropertyType String -Force | Out-Null
  New-ItemProperty -Path $registryPath -Name InstallLocation -Value ([IO.Path]::GetFullPath($Destination)) -PropertyType String -Force | Out-Null
  New-ItemProperty -Path $registryPath -Name UninstallString -Value $uninstallCommand -PropertyType String -Force | Out-Null
  New-ItemProperty -Path $registryPath -Name NoModify -Value 1 -PropertyType DWord -Force | Out-Null
  New-ItemProperty -Path $registryPath -Name NoRepair -Value 1 -PropertyType DWord -Force | Out-Null
  $ownership = [ordered]@{
    schema_version = 1; installation_id = $installationId; mdoc_version = [string]$PackageManifest.version; toolchain_version = [string]$runtimeState.toolchain_version
    installation = [IO.Path]::GetFullPath($Destination); runtime_root = [IO.Path]::GetFullPath($RuntimeRoot); path_entry = $pathEntry
    path_added_by_mdoc = if ($existing) { [bool]$existing.path_added_by_mdoc } elseif ($trustedLegacyInstall) { $true } else { -not $pathWasPresent }; start_menu = [IO.Path]::GetFullPath($startMenu)
    uninstall_registry = $registrySubKey; python_ownership = [string]$runtimeState.python_ownership
    python_root = [IO.Path]::GetFullPath((Join-Path $RuntimeRoot 'python')); python_installer = if ($runtimeState.python_installer) { [IO.Path]::GetFullPath([string]$runtimeState.python_installer) } else { $null }
    managed_components = @([IO.Path]::GetFullPath($Destination), [IO.Path]::GetFullPath($RuntimeRoot), [IO.Path]::GetFullPath($startMenu))
  }
  New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
  [IO.File]::WriteAllText($ownershipPath, ($ownership | ConvertTo-Json -Depth 5) + "`n", [Text.UTF8Encoding]::new($false))
}

if (-not (Test-Path -LiteralPath (Join-Path $packageRoot 'PACKAGE-MANIFEST.json') -PathType Leaf)) {
  $sourceRoot = [IO.Path]::GetFullPath($packageRoot)
  $versionPath = Join-Path $sourceRoot 'VERSION'
  $buildScript = Join-Path $sourceRoot 'scripts\build_release.py'
  if (-not (Test-Path -LiteralPath $versionPath -PathType Leaf) -or -not (Test-Path -LiteralPath $buildScript -PathType Leaf)) {
    throw 'MDOC-INSTALL-MANIFEST-MISSING: PACKAGE-MANIFEST.json is missing. Extract the complete release ZIP before running the installer.'
  }
  $buildPython = $Python
  if (-not $buildPython) {
    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyLauncher) {
      $resolved = & $pyLauncher.Source -3.12 -c 'import sys; print(sys.executable)' 2>$null
      if ($LASTEXITCODE -eq 0 -and $resolved) { $buildPython = [string]$resolved }
    }
  }
  if (-not $buildPython) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) { $buildPython = $pythonCommand.Source }
  }
  if (-not $buildPython) { throw 'MDOC-INSTALL-SOURCE-PYTHON-MISSING: Installing from source requires Python.' }
  & $buildPython $buildScript
  if ($LASTEXITCODE -ne 0) { throw "MDOC-INSTALL-SOURCE-BUILD-FAILED: $LASTEXITCODE" }
  $version = (Get-Content -LiteralPath $versionPath -Raw).Trim()
  $sourcePackage = Join-Path $sourceRoot ("dist\mdoc-{0}-windows-x64.zip" -f $version)
  if (-not (Test-Path -LiteralPath $sourcePackage -PathType Leaf)) { throw "MDOC-INSTALL-SOURCE-PACKAGE-MISSING: $sourcePackage" }
  $sourcePackageStaging = Join-Path $env:TEMP ("mdoc-install-package-" + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $sourcePackageStaging -Force | Out-Null
  Expand-Archive -LiteralPath $sourcePackage -DestinationPath $sourcePackageStaging -Force
  $packageRoot = $sourcePackageStaging
}
try {
  $manifestPath = Join-Path $packageRoot 'PACKAGE-MANIFEST.json'
  if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'MDOC-INSTALL-MANIFEST-MISSING: PACKAGE-MANIFEST.json is missing. Extract the complete release ZIP.' }
  $manifest = Get-Content -LiteralPath $manifestPath -Encoding UTF8 -Raw | ConvertFrom-Json
  if ($manifest.product -ne 'mdoc' -or $manifest.platform -ne 'windows-x86_64') { throw 'MDOC-INSTALL-PACKAGE-INVALID: Package product or platform does not match.' }
  foreach ($file in $manifest.files) {
  $candidate = [IO.Path]::GetFullPath((Join-Path $packageRoot ([string]$file.path)))
  if (-not $candidate.StartsWith([IO.Path]::GetFullPath($packageRoot) + [IO.Path]::DirectorySeparatorChar)) { throw 'MDOC-INSTALL-PACKAGE-UNSAFE: The manifest contains a path outside the package.' }
  if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw "MDOC-INSTALL-FILE-MISSING: $($file.path)" }
  $actual = Get-Sha256 $candidate
  if ($actual -ne [string]$file.sha256) { throw "MDOC-INSTALL-SHA256-MISMATCH: $($file.path)" }
  }
  $source = Join-Path $packageRoot 'skill\mdoc'
  if (-not (Test-Path -LiteralPath (Join-Path $source 'SKILL.md'))) { throw 'MDOC-INSTALL-PACKAGE-INVALID: skill/mdoc/SKILL.md is missing.' }
  if (-not $Toolkit) {
    $bootstrapPath = Join-Path $packageRoot 'bootstrap\toolchain-bootstrap.json'
    $bootstrap = Get-Content -LiteralPath $bootstrapPath -Encoding UTF8 -Raw | ConvertFrom-Json
    $Toolkit = Find-MdocLocalToolkit $packageRoot $bootstrap
    if (-not $Toolkit -and [IO.Path]::GetFullPath($packageRoot) -ne $originalPackageRoot) {
      $Toolkit = Find-MdocLocalToolkit $originalPackageRoot $bootstrap
    }
    if ($Toolkit) { Write-Host "Using local mdoc Toolchain bundle: $Toolkit" }
  }
  $parent = Split-Path -Parent $Destination
  New-Item -ItemType Directory -Path $parent -Force | Out-Null
  $staging = $Destination + '.installing'
  if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
  Copy-Item -LiteralPath $source -Destination $staging -Recurse
  $support = Join-Path $staging 'runtime-support'
  New-Item -ItemType Directory -Path $support -Force | Out-Null
  Copy-Item -LiteralPath (Join-Path $packageRoot 'repair-mdoc-runtime.ps1') -Destination $support
  Copy-Item -LiteralPath (Join-Path $packageRoot 'bootstrap') -Destination $support -Recurse
  Copy-Item -LiteralPath (Join-Path $packageRoot 'runtime') -Destination $support -Recurse
  if (-not $SkipRuntimeRepair) {
    $repair = Join-Path $packageRoot 'repair-mdoc-runtime.ps1'
    & $repair -Python $Python -Toolkit $Toolkit -Installation $Destination -RuntimeRoot $RuntimeRoot -Proxy $Proxy -AllowNetworkDownload:$AllowNetworkDownload | Write-Host
    if ($LASTEXITCODE -ne 0) { throw "MDOC-INSTALL-RUNTIME-REPAIR-FAILED: $LASTEXITCODE" }
  }
  $transaction = Join-Path $packageRoot 'runtime-bootstrap\mdoc_install_transaction.py'
  if (-not (Test-Path -LiteralPath $transaction -PathType Leaf)) { throw 'MDOC-INSTALL-TRANSACTION-MISSING: The shared installation transaction is missing.' }
  $pythonCommand = if ($Python) { $Python } else {
    $candidate = Join-Path $RuntimeRoot 'runtime\Scripts\python.exe'
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { $candidate } else { (Get-Command python.exe -ErrorAction Stop).Source }
  }
  & $pythonCommand $transaction --operation install --package $packageRoot --installation $Destination --runtime-root $RuntimeRoot --plan
  if ($LASTEXITCODE -ne 0) { throw "MDOC-INSTALL-PLAN-FAILED: $LASTEXITCODE" }
  & $pythonCommand $transaction --operation install --runtime-root $RuntimeRoot --apply --confirm
  if ($LASTEXITCODE -ne 0) { throw "MDOC-INSTALL-APPLY-FAILED: $LASTEXITCODE" }
  Register-MdocUninstall $manifest
  if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
  Write-Host "mdoc $($manifest.version) installed to $Destination"
} finally {
  if ($sourcePackageStaging -and (Test-Path -LiteralPath $sourcePackageStaging)) { Remove-Item -LiteralPath $sourcePackageStaging -Recurse -Force }
}
