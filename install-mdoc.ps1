[CmdletBinding()]
param(
  [string]$Package = (Join-Path $PSScriptRoot 'mdoc-1.0.0.zip'),
  [string]$Manifest = (Join-Path $PSScriptRoot 'RELEASE-MANIFEST.json'),
  [string]$Destination = (Join-Path $HOME '.codex\skills\mdoc')
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Package)) { throw "Package not found: $Package" }
if (-not (Test-Path -LiteralPath $Manifest)) { throw "Manifest not found: $Manifest" }
$release = Get-Content -LiteralPath $Manifest -Raw | ConvertFrom-Json
$actual = (Get-FileHash -LiteralPath $Package -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne [string]$release.sha256) { throw "SHA-256 verification failed." }
$temporary = Join-Path ([IO.Path]::GetTempPath()) ("mdoc-install-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
  Expand-Archive -LiteralPath $Package -DestinationPath $temporary -Force
  $source = Join-Path $temporary 'skill\mdoc'
  if (-not (Test-Path -LiteralPath (Join-Path $source 'SKILL.md'))) { throw 'Invalid mdoc package.' }
  $parent = Split-Path -Parent $Destination
  New-Item -ItemType Directory -Path $parent -Force | Out-Null
  $staging = $Destination + '.installing'
  if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
  Copy-Item -LiteralPath $source -Destination $staging -Recurse
  if (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Recurse -Force }
  Move-Item -LiteralPath $staging -Destination $Destination
  Write-Host "mdoc $($release.version) installed to $Destination"
} finally {
  if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force }
}
