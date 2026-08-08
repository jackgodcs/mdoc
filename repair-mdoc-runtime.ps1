[CmdletBinding()]
param(
  [string]$Python,
  [string]$Toolkit,
  [ValidateSet('Full', 'Core', 'Existing', 'Offline')] [string]$Profile = 'Full',
  [switch]$AllowNetworkDownload
)
$ErrorActionPreference = 'Stop'

function Test-MdocPython {
  param([Parameter(Mandatory=$true)][string]$Executable)
  if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $null }
  $probe = 'import json,platform,struct,sys,venv; print(json.dumps({"executable":sys.executable,"version":list(sys.version_info[:3]),"implementation":platform.python_implementation(),"bits":struct.calcsize("P")*8,"platform":sys.platform}))'
  $result = & $Executable -I -S -c $probe 2>$null
  if ($LASTEXITCODE -ne 0) { return $null }
  try { $identity = $result | ConvertFrom-Json } catch { return $null }
  if ($identity.implementation -ne 'CPython' -or $identity.version[0] -ne 3 -or $identity.version[1] -ne 12 -or $identity.bits -ne 64 -or $identity.platform -ne 'win32') { return $null }
  return $identity
}

$candidates = @()
if ($Python) { $candidates += $Python }
foreach ($name in @('python.exe', 'python3.exe')) {
  $command = Get-Command $name -ErrorAction SilentlyContinue
  if ($command) { $candidates += $command.Source }
}
$selected = $null
foreach ($candidate in $candidates | Select-Object -Unique) {
  $identity = Test-MdocPython -Executable $candidate
  if ($identity) { $selected = $identity; break }
}
if (-not $selected) {
  if ($Toolkit) { throw 'MDOC-RUNTIME-OFFLINE-NOT-IMPLEMENTED: 离线工具包将在 mdoc-toolchain v2026.08.1 Stable 后启用。' }
  if (-not $AllowNetworkDownload) { throw 'MDOC-RUNTIME-PYTHON-MISSING: 未找到可用的 CPython 3.12 x64。联网修复需要用户明确授权。' }
  throw 'MDOC-RUNTIME-TOOLCHAIN-NOT-RELEASED: Toolchain Stable 尚未发布，不能从开发目录下载运行时。'
}
[pscustomobject]@{schema_version=1; status='python-validated'; profile=$Profile; executable=$selected.executable; version=($selected.version -join '.'); ownership='external'} | ConvertTo-Json -Depth 4
