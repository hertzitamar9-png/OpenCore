param([Parameter(Mandatory=$true)][string]$Canonical, [Parameter(Mandatory=$true)][string[]]$Copies)
$ErrorActionPreference = 'Stop'
$release = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../release'))
$canonicalPath = (Get-Item -LiteralPath $Canonical -Force).FullName
$canonicalHash = (Get-FileHash -LiteralPath $canonicalPath -Algorithm SHA256).Hash
$paths = foreach ($copy in $Copies) {
    $path = [IO.Path]::GetFullPath($copy)
    if (-not $path.StartsWith($release + '\', [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetPathRoot($path) -ne [IO.Path]::GetPathRoot($canonicalPath)) { throw 'Invalid deduplication path' }
    $item = Get-Item -LiteralPath $path -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Expected regular model file' }
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $canonicalHash) { throw "Weights differ: $path" }
    $path
}
$before = (Get-PSDrive C).Free
foreach ($path in $paths) {
    $backup = $path + '.dedup-backup'
    if (-not $backup.StartsWith($release + '\', [StringComparison]::OrdinalIgnoreCase) -or
        (Test-Path -LiteralPath $backup)) { throw 'Invalid rollback path' }
    Move-Item -LiteralPath $path -Destination $backup
    try {
        New-Item -ItemType HardLink -Path $path -Target $canonicalPath | Out-Null
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $canonicalHash) { throw 'Linked file hash differs' }
    } catch {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
        Move-Item -LiteralPath $backup -Destination $path
        throw
    }
    Remove-Item -LiteralPath $backup -Force
}
[pscustomobject]@{ canonical=$canonicalPath; sha256=$canonicalHash; paths=$paths;
    reclaimedGiB=[math]::Round(((Get-PSDrive C).Free-$before)/1GB,3) } | ConvertTo-Json -Depth 3
