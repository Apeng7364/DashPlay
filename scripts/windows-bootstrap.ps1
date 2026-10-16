# Ready a Windows PC for the installation guide without installing anything on it: a pinned standalone Python 3.12
# (astral-sh/python-build-standalone) under WORK\python, and the prebuilt spd_dump.exe and lz4.exe of tools\windows-x64
# checked against their SHA256SUMS (scripts/build-windows-tools.sh). Uses only what Windows 10 1803 and later ship
# (curl.exe, tar.exe, the OpenSSH client). Prints the PATH line for the following commands, then WINDOWS_READY.
#   powershell -ExecutionPolicy Bypass -File "$DP\scripts\windows-bootstrap.ps1" "$WORK"
param([Parameter(Mandatory = $true)][string]$Work)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $PSScriptRoot
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { Write-Output "STOP: not 64-bit x86 Windows ($env:PROCESSOR_ARCHITECTURE)"; exit 1 }
$release = '20261003'
$sum = '4b6f0beebbb695a0f3ea237b8c3eaa5bd424f47a7bc25b2fbe3a43390c770f08'
$name = "cpython-3.12.15+$release-x86_64-pc-windows-msvc-install_only.tar.gz"
New-Item -ItemType Directory -Force $Work | Out-Null
$archive = Join-Path $Work $name
if (-not (Test-Path $archive)) {
    curl.exe -fL --retry 3 -o "$archive.part" "https://github.com/astral-sh/python-build-standalone/releases/download/$release/cpython-3.12.15%2B$release-x86_64-pc-windows-msvc-install_only.tar.gz"
    if ($LASTEXITCODE -ne 0) { Write-Output 'STOP: Python download failed (retry the network)'; exit 1 }
    Move-Item "$archive.part" $archive
}
if ((Get-FileHash -Algorithm SHA256 $archive).Hash.ToLower() -ne $sum) { Write-Output 'STOP: Python download does not match its SHA-256'; exit 1 }
$python = Join-Path $Work 'python\python.exe'
if (-not (Test-Path $python)) {
    tar.exe -xzf $archive -C $Work
    if ($LASTEXITCODE -ne 0) { Write-Output 'STOP: could not unpack Python'; exit 1 }
}
& $python -c 'import sys, ssl, venv; assert sys.version_info[:2] == (3, 12)'
if ($LASTEXITCODE -ne 0) { Write-Output 'STOP: the unpacked Python does not run'; exit 1 }
$tools = Join-Path $here 'tools\windows-x64'
foreach ($line in Get-Content (Join-Path $tools 'SHA256SUMS')) {
    $hash, $file = $line -split '\s+', 2
    if ((Get-FileHash -Algorithm SHA256 (Join-Path $tools $file.Trim())).Hash.ToLower() -ne $hash) {
        Write-Output "STOP: tools\windows-x64\$($file.Trim()) does not match SHA256SUMS"; exit 1
    }
}
foreach ($tool in 'ssh.exe', 'ssh-keygen.exe') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Output "STOP: $tool missing: turn on the OpenSSH client (Settings > Apps > Optional features)"; exit 1
    }
}
Write-Output "`$env:Path = `"$(Join-Path $Work 'python');$tools;`" + `$env:Path"
Write-Output 'WINDOWS_READY'
