param([string]$InstallRoot = (Join-Path $env:USERPROFILE 'OpenCore'))
$ErrorActionPreference = 'Stop'
$visionDir = Join-Path $InstallRoot 'vision'
$projector = Join-Path $visionDir 'mmproj-BF16.gguf'
$expected = '302b92d565080b9cc0281186979ae75a7429ec23d14f6f7607a035539b21f3a6'
if (-not (Test-Path -LiteralPath $projector)) {
    $cli = Join-Path $InstallRoot 'speech\venv\Scripts\hf.exe'
    if (-not (Test-Path -LiteralPath $cli)) { $cli = (Get-Command hf -ErrorAction Stop).Source }
    & $cli download unsloth/Qwen3.5-4B-GGUF mmproj-BF16.gguf --revision e87f176479d0855a907a41277aca2f8ee7a09523 --local-dir $visionDir
    if ($LASTEXITCODE -ne 0) { throw 'Vision projector download failed' }
}
if ((Get-FileHash -LiteralPath $projector -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'Vision projector checksum mismatch' }
Write-Output 'Qwen3.5-4B BF16 vision projector verified.'
