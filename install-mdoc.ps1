[CmdletBinding()]
param(
  [ValidateSet('Full', 'Core', 'Existing', 'Offline')] [string]$Profile = 'Full',
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
$manifestPath = Join-Path $packageRoot 'PACKAGE-MANIFEST.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'MDOC-INSTALL-MANIFEST-MISSING: PACKAGE-MANIFEST.json 不存在。请先完整解压安装 ZIP。' }
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.product -ne 'mdoc' -or $manifest.platform -ne 'windows-x86_64') { throw 'MDOC-INSTALL-PACKAGE-INVALID: 安装包产品或平台不匹配。' }
foreach ($file in $manifest.files) {
  $candidate = [IO.Path]::GetFullPath((Join-Path $packageRoot ([string]$file.path)))
  if (-not $candidate.StartsWith([IO.Path]::GetFullPath($packageRoot) + [IO.Path]::DirectorySeparatorChar)) { throw 'MDOC-INSTALL-PACKAGE-UNSAFE: Manifest 包含越界路径。' }
  if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw "MDOC-INSTALL-FILE-MISSING: $($file.path)" }
  $actual = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actual -ne [string]$file.sha256) { throw "MDOC-INSTALL-SHA256-MISMATCH: $($file.path)" }
}
$source = Join-Path $packageRoot 'skill\mdoc'
if (-not (Test-Path -LiteralPath (Join-Path $source 'SKILL.md'))) { throw 'MDOC-INSTALL-PACKAGE-INVALID: 缺少 skill/mdoc/SKILL.md。' }
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
  & $repair -Profile $Profile -Python $Python -Toolkit $Toolkit -Installation $Destination -RuntimeRoot $RuntimeRoot -Proxy $Proxy -AllowNetworkDownload:$AllowNetworkDownload | Write-Host
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
