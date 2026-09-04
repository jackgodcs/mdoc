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

if (-not (Test-Path -LiteralPath (Join-Path $packageRoot 'PACKAGE-MANIFEST.json') -PathType Leaf)) {
  $sourceRoot = [IO.Path]::GetFullPath($packageRoot)
  $versionPath = Join-Path $sourceRoot 'VERSION'
  $buildScript = Join-Path $sourceRoot 'scripts\build_release.py'
  if (-not (Test-Path -LiteralPath $versionPath -PathType Leaf) -or -not (Test-Path -LiteralPath $buildScript -PathType Leaf)) {
    throw 'MDOC-INSTALL-MANIFEST-MISSING: PACKAGE-MANIFEST.json 不存在。请完整解压发布 ZIP 后运行安装器。'
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
  if (-not $buildPython) { throw 'MDOC-INSTALL-SOURCE-PYTHON-MISSING: 源码目录安装需要可用的 Python。' }
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
  if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'MDOC-INSTALL-MANIFEST-MISSING: PACKAGE-MANIFEST.json 不存在。请先完整解压安装 ZIP。' }
  $manifest = Get-Content -LiteralPath $manifestPath -Encoding UTF8 -Raw | ConvertFrom-Json
  if ($manifest.product -ne 'mdoc' -or $manifest.platform -ne 'windows-x86_64') { throw 'MDOC-INSTALL-PACKAGE-INVALID: 安装包产品或平台不匹配。' }
  foreach ($file in $manifest.files) {
  $candidate = [IO.Path]::GetFullPath((Join-Path $packageRoot ([string]$file.path)))
  if (-not $candidate.StartsWith([IO.Path]::GetFullPath($packageRoot) + [IO.Path]::DirectorySeparatorChar)) { throw 'MDOC-INSTALL-PACKAGE-UNSAFE: Manifest 包含越界路径。' }
  if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw "MDOC-INSTALL-FILE-MISSING: $($file.path)" }
  $actual = Get-Sha256 $candidate
  if ($actual -ne [string]$file.sha256) { throw "MDOC-INSTALL-SHA256-MISMATCH: $($file.path)" }
  }
  $source = Join-Path $packageRoot 'skill\mdoc'
  if (-not (Test-Path -LiteralPath (Join-Path $source 'SKILL.md'))) { throw 'MDOC-INSTALL-PACKAGE-INVALID: 缺少 skill/mdoc/SKILL.md。' }
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
  if (-not (Test-Path -LiteralPath $transaction -PathType Leaf)) { throw 'MDOC-INSTALL-TRANSACTION-MISSING: 缺少共享安装事务。' }
  $pythonCommand = if ($Python) { $Python } else {
    $candidate = Join-Path $RuntimeRoot 'runtime\Scripts\python.exe'
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { $candidate } else { (Get-Command python.exe -ErrorAction Stop).Source }
  }
  & $pythonCommand $transaction --operation install --package $packageRoot --installation $Destination --runtime-root $RuntimeRoot --plan
  if ($LASTEXITCODE -ne 0) { throw "MDOC-INSTALL-PLAN-FAILED: $LASTEXITCODE" }
  & $pythonCommand $transaction --operation install --runtime-root $RuntimeRoot --apply --confirm
  if ($LASTEXITCODE -ne 0) { throw "MDOC-INSTALL-APPLY-FAILED: $LASTEXITCODE" }
  if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
  Write-Host "mdoc $($manifest.version) installed to $Destination"
} finally {
  if ($sourcePackageStaging -and (Test-Path -LiteralPath $sourcePackageStaging)) { Remove-Item -LiteralPath $sourcePackageStaging -Recurse -Force }
}
