[CmdletBinding()]
param(
  [string]$RuntimeRoot = (Join-Path $env:LOCALAPPDATA 'mdoc'),
  [string]$Destination,
  [switch]$Confirm,
  [switch]$Json,
  [switch]$Recovery
)
$ErrorActionPreference = 'Stop'
$runtimePython = Join-Path $RuntimeRoot 'runtime\Scripts\python.exe'
$uninstaller = Join-Path $RuntimeRoot 'uninstall\mdoc_uninstall.py'
if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf) -or -not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
  $runtimePython = (Get-Command python.exe -ErrorAction Stop).Source
  $uninstaller = Join-Path $PSScriptRoot 'runtime-bootstrap\mdoc_uninstall.py'
}
$arguments = @($uninstaller, '--runtime-root', $RuntimeRoot)
if ($Destination) { $arguments += @('--destination', $Destination) }
if ($Confirm) { $arguments += '--confirm' }
if ($Json) { $arguments += '--json' }
if ($Recovery) { $arguments += '--recovery' }
& $runtimePython @arguments
exit $LASTEXITCODE
