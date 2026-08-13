<#
.SYNOPSIS
  Build static/css/app.css with the Tailwind standalone CLI.

.DESCRIPTION
  Uses the standalone binary so the repo needs no npm dependency tree.
  The binary is downloaded to .tools/ (gitignored) on first run.

.EXAMPLE
  ./scripts/tailwind.ps1            # one-off build
  ./scripts/tailwind.ps1 -Watch     # rebuild on change
#>
param([switch]$Watch)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$toolsDir = Join-Path $repo ".tools"
$exe = Join-Path $toolsDir "tailwindcss.exe"
$version = "v3.4.17"

if (-not (Test-Path $exe)) {
    New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
    $url = "https://github.com/tailwindlabs/tailwindcss/releases/download/$version/tailwindcss-windows-x64.exe"
    Write-Host "Downloading Tailwind CLI $version ..."
    Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing
}

$args = @("-i", "static/css/input.css", "-o", "static/css/app.css", "--minify")
if ($Watch) { $args += "--watch" }

Push-Location $repo
try { & $exe @args } finally { Pop-Location }
