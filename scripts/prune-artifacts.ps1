param(
    [Parameter(Mandatory = $true)][string]$PlanPath,
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$plan = Get-Content -LiteralPath $PlanPath -Raw | ConvertFrom-Json
$comparison = [StringComparison]::OrdinalIgnoreCase
function Resolve-Absolute([string]$Path) { [IO.Path]::GetFullPath($Path).TrimEnd('\', '/') }
function Is-Within([string]$Path, [string]$Root) {
    $Path.Equals($Root, $comparison) -or $Path.StartsWith($Root + '\', $comparison)
}
function Assert-NoLinks([string]$Path) {
    $cursor = $Path
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Refusing a cleanup path through a link: $cursor"
            }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
}
function Measure-Target([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if (-not $item.PSIsContainer) { return [uint64]$item.Length }
    $entries = @(Get-ChildItem -LiteralPath $Path -Force -Recurse)
    if ($entries | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) {
        throw "Refusing a cleanup directory containing links: $Path"
    }
    return [uint64](($entries | Where-Object { -not $_.PSIsContainer } | Measure-Object Length -Sum).Sum)
}

$roots = @($plan.allowedRoots | ForEach-Object { Resolve-Absolute $_ })
$protected = @($plan.protectedPaths | ForEach-Object { Resolve-Absolute $_ })
$retainedBefore = @{}
foreach ($keep in $protected) {
    if (-not (Test-Path -LiteralPath $keep)) { throw "Retained artifact is missing before cleanup: $keep" }
    $item = Get-Item -LiteralPath $keep -Force
    if (-not $item.PSIsContainer) {
        $retainedBefore[$keep] = @($item.Length, $item.LastWriteTimeUtc.Ticks)
    }
}
# This command may contain the plan paths itself. Only exclude this process;
# other cleanup commands and every model or training process still block deletion.
$processes = @(Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID })
$checked = @()
foreach ($target in $plan.targets) {
    $path = Resolve-Absolute $target.path
    if (-not ($roots | Where-Object { (Is-Within $path $_) -and -not $path.Equals($_, $comparison) })) {
        throw "Cleanup target is outside the named project roots: $path"
    }
    foreach ($keep in $protected) {
        if ((Is-Within $keep $path) -or (Is-Within $path $keep)) {
            throw "Cleanup target overlaps a retained artifact: $path"
        }
    }
    Assert-NoLinks $path
    foreach ($process in $processes) {
        if (($process.ExecutablePath -and (Is-Within $process.ExecutablePath $path)) -or
            ($process.CommandLine -and $process.CommandLine.IndexOf($path, $comparison) -ge 0) -or
            ($target.denyWhenProcessMatches -and $process.CommandLine -match $target.denyWhenProcessMatches)) {
            throw "Cleanup target is used by process $($process.ProcessId): $path"
        }
    }
    if (-not (Test-Path -LiteralPath $path)) { continue }
    $bytes = Measure-Target $path
    if ($null -eq $target.expectedBytes -or $bytes -ne [uint64]$target.expectedBytes) {
        throw "Cleanup target changed since the inventory: $path"
    }
    $checked += [pscustomobject]@{ path = $path; bytes = $bytes; reason = $target.reason }
}

$before = (Get-PSDrive -Name C).Free
$removed = @()
foreach ($target in $checked) {
    if ($Apply) {
        Assert-NoLinks $target.path
        if ((Measure-Target $target.path) -ne $target.bytes) { throw "Cleanup target changed: $($target.path)" }
        Remove-Item -LiteralPath $target.path -Force -Recurse
        $removed += $target
    }
}
foreach ($keep in $protected) {
    if (-not (Test-Path -LiteralPath $keep)) { throw "Retained artifact is missing: $keep" }
    if ($retainedBefore.ContainsKey($keep)) {
        $item = Get-Item -LiteralPath $keep -Force
        if ($item.Length -ne $retainedBefore[$keep][0] -or $item.LastWriteTimeUtc.Ticks -ne $retainedBefore[$keep][1]) {
            throw "Retained artifact changed during cleanup: $keep"
        }
    }
}
$after = (Get-PSDrive -Name C).Free
$result = [pscustomobject]@{
    applied = [bool]$Apply
    freeGiBBefore = [math]::Round($before / 1GB, 3)
    freeGiBAfter = [math]::Round($after / 1GB, 3)
    reclaimedGiB = [math]::Round(($after - $before) / 1GB, 3)
    minimumFreeGiB = $plan.minimumFreeGiB
    minimumSatisfied = ($after -ge $plan.minimumFreeGiB * 1GB)
    checkedTargets = $checked
    removedTargets = $removed
    retainedArtifacts = $protected
}
if ($Apply) {
    $report = [IO.Path]::ChangeExtension((Resolve-Absolute $PlanPath), '.result.json')
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $report -Encoding UTF8
}
$result | ConvertTo-Json -Depth 8
