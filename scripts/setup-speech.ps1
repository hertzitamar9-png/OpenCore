param([string]$InstallRoot = (Join-Path $env:USERPROFILE 'OpenCore'))
$ErrorActionPreference = 'Stop'
$speechRoot = Join-Path $InstallRoot 'speech'
$python = Join-Path $speechRoot 'venv/Scripts/python.exe'
New-Item -ItemType Directory -Path $speechRoot -Force | Out-Null
if (-not (Test-Path -LiteralPath $python)) {
    & py -3.12 -m venv (Join-Path $speechRoot 'venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required to install local speech.' }
}
& $python -m pip install faster-whisper==1.2.1 ctranslate2==4.8.2 huggingface-hub==1.33.0 nvidia-cublas-cu12==12.9.2.10 nvidia-cudnn-cu12==9.26.0.51
if ($LASTEXITCODE -ne 0) { throw 'Speech dependencies could not be installed.' }
$model = Join-Path $speechRoot 'large-v3'
$revision = 'edaa852ec7e145841d8ffdb056a99866b5f0a478'
& (Join-Path $speechRoot 'venv/Scripts/hf.exe') download Systran/faster-whisper-large-v3 model.bin config.json tokenizer.json vocabulary.json preprocessor_config.json --revision $revision --local-dir $model
if ($LASTEXITCODE -ne 0) { throw 'Whisper download failed.' }
$hash = (Get-FileHash -LiteralPath (Join-Path $model 'model.bin') -Algorithm SHA256).Hash.ToLowerInvariant()
if ($hash -ne '69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1') { throw 'Whisper model checksum mismatch.' }
@{ model='Systran/faster-whisper-large-v3'; revision=$revision; sha256=$hash; precision='float16'; idle='SSD'; gpu='recording and transcription only' } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $speechRoot 'manifest.json') -Encoding UTF8
Write-Output 'Whisper large-v3 is installed on SSD. No GPU worker is started by setup.'
