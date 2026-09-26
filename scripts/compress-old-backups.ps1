param([Parameter(Mandatory = $true)][string[]]$Paths)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $env:APPDATA 'ai.opencore.control-center'))
$results = foreach ($value in $Paths) {
    $path = [IO.Path]::GetFullPath($value)
    if (-not $path.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase) -or
        ($path -notmatch '\\backup-before-[^\\]+\\[^\\]+$' -and $path -notmatch '\.before-streaming-\d+$')) {
        throw "Not an obsolete OpenCore backup path: $path"
    }
    $item = Get-Item -LiteralPath $path -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Expected a regular backup file: $path"
    }
    $archive = $path + '.zip'
    if (Test-Path -LiteralPath $archive) { throw "Archive already exists: $archive" }
    $before = @($item.Length, $item.LastWriteTimeUtc.Ticks)
    $sha = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    $zip = [IO.Compression.ZipFile]::Open($archive, [IO.Compression.ZipArchiveMode]::Create)
    try {
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $path, $item.Name,
            [IO.Compression.CompressionLevel]::Fastest) | Out-Null
    } finally { $zip.Dispose() }
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        $entry = $zip.GetEntry($item.Name)
        if ($null -eq $entry -or $entry.Length -ne $before[0]) { throw 'Archive length differs' }
        $stream = $entry.Open()
        $hasher = [Security.Cryptography.SHA256]::Create()
        try { $restoredHash = [Convert]::ToHexString($hasher.ComputeHash($stream)) }
        finally { $stream.Dispose(); $hasher.Dispose() }
    } finally { $zip.Dispose() }
    $item = Get-Item -LiteralPath $path -Force
    if ($sha -ne $restoredHash -or $item.Length -ne $before[0] -or $item.LastWriteTimeUtc.Ticks -ne $before[1]) {
        throw 'Source changed or backup verification failed; unpacked backup retained'
    }
    [pscustomobject]@{ path=$path; archive=$archive; sha256=$sha; expectedBytes=$item.Length;
        archiveBytes=(Get-Item -LiteralPath $archive).Length; verified=$true }
}
$results | ConvertTo-Json -Depth 3
