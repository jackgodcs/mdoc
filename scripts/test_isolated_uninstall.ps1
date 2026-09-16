[CmdletBinding()]
param(
  [string]$Package = (Join-Path $PSScriptRoot '..\dist\mdoc-1.5.3-windows-x64.zip'),
  [string]$Toolkit = (Join-Path $PSScriptRoot '..\..\mdoc-toolchain\dist\mdoc-toolchain-2026.09.15-windows-x64.zip')
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:TEMP ('mdoc-e2e-' + [guid]::NewGuid().ToString('N'))
$packageRoot = Join-Path $root 'package'; $testHome = Join-Path $root 'home'; $local = Join-Path $root 'local'; $app = Join-Path $root 'appdata'
$installation = Join-Path $testHome '.codex\skills\mdoc'; $runtime = Join-Path $local 'mdoc'; $workspace = Join-Path $root 'manual-workspace'
$oldPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$registrySubKey = 'Software\mdoc-tests\' + [guid]::NewGuid().ToString('N')
try {
  New-Item -ItemType Directory -Path $packageRoot,$testHome,$local,$app,$workspace -Force | Out-Null
  [IO.File]::WriteAllText((Join-Path $workspace 'keep.txt'), 'preserve', [Text.UTF8Encoding]::new($false))
  Expand-Archive -LiteralPath $Package -DestinationPath $packageRoot -Force
  Set-Item Env:HOME $testHome; Set-Item Env:USERPROFILE $testHome; Set-Item Env:LOCALAPPDATA $local; Set-Item Env:APPDATA $app; Set-Item Env:MDOC_TEST_UNINSTALL_REGISTRY $registrySubKey
  & (Join-Path $packageRoot 'install-mdoc.ps1') -Profile Offline -Toolkit $Toolkit -Destination $installation -RuntimeRoot $runtime
  if ($LASTEXITCODE -ne 0) { throw "install failed: $LASTEXITCODE" }
  $launcher = Join-Path $runtime 'bin\mdoc.cmd'
  $version = & $launcher --version
  if ($LASTEXITCODE -ne 0 -or $version -notcontains 'mdoc 1.5.3') { throw "version check failed: $version" }
  $doctor = & $launcher check doctor --json | ConvertFrom-Json
  if ($LASTEXITCODE -ne 0 -or $doctor.status -ne 'passed') { throw 'doctor failed' }
  $ownership = Get-Content -LiteralPath (Join-Path $runtime 'state\uninstall.json') -Raw | ConvertFrom-Json
  if ($ownership.installation -ne [IO.Path]::GetFullPath($installation)) { throw 'ownership installation mismatch' }
  $result = (& $launcher uninstall --confirm --json | ConvertFrom-Json)
  if ($LASTEXITCODE -notin @(0,3) -or $result.status -notin @('uninstalled','pending_cleanup')) { throw "uninstall failed: $($result | ConvertTo-Json -Compress)" }
  for ($i = 0; $i -lt 30 -and (Test-Path -LiteralPath $runtime); $i++) { Start-Sleep -Milliseconds 500 }
  if (Test-Path -LiteralPath $installation) { throw 'installation remains' }
  if (Test-Path -LiteralPath $runtime) { throw 'runtime remains' }
  if (-not (Test-Path -LiteralPath (Join-Path $workspace 'keep.txt'))) { throw 'workspace was removed' }
  if (Test-Path -LiteralPath ('HKCU:\' + $registrySubKey)) { throw 'uninstall registry remains' }
  $currentPath = [Environment]::GetEnvironmentVariable('Path', 'User')
  if (@($currentPath -split ';') -contains (Join-Path $runtime 'bin')) { throw 'PATH entry remains' }
  Write-Output "MDOC-ISOLATED-INSTALL-UNINSTALL-OK: $root"
} finally {
  [Environment]::SetEnvironmentVariable('Path', $oldPath, 'User')
  Remove-Item Env:MDOC_TEST_UNINSTALL_REGISTRY -ErrorAction SilentlyContinue
  if (Test-Path -LiteralPath ('HKCU:\' + $registrySubKey)) { Remove-Item -LiteralPath ('HKCU:\' + $registrySubKey) -Recurse -Force }
  if (Test-Path -LiteralPath $root) { cmd.exe /d /c rmdir /s /q "$root" | Out-Null }
}
