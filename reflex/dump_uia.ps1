param(
    [Parameter(Mandatory)] [string] $Out,
    [long[]] $Handles = @()
)
# Dump UI Automation trees of visible top-level windows in the same shape as OpenCore's
# desktop_use inspect: breadth-first, depth <= 5, <= 35 children per node, <= 160 rows.
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::RootElement
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
# Only the exact windows the caller names are read; nothing else on the desktop is inspected.
$windows = @(foreach ($handle in $Handles) {
    try { [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$handle) } catch { }
})
$dumps = foreach ($window in $windows) {
    $rows = New-Object System.Collections.Generic.List[object]
    $queue = New-Object System.Collections.Generic.Queue[object]
    $queue.Enqueue(@($window, 0))
    while ($queue.Count -gt 0 -and $rows.Count -lt 160) {
        $item = $queue.Dequeue(); $element = $item[0]; $depth = $item[1]
        try {
            $current = $element.Current
            $rect = $current.BoundingRectangle
            $value = ''
            try {
                $pattern = $element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                if ($pattern) { $value = [string]$pattern.Current.Value }
            } catch { }
            $rows.Add([ordered]@{
                elementId = $rows.Count; depth = $depth
                name = if ($current.IsPassword) { '[password]' } else { ([string]$current.Name).Substring(0, [Math]::Min(160, ([string]$current.Name).Length)) }
                controlType = ($current.ControlType.ProgrammaticName -replace '^ControlType\.', '')
                enabled = $current.IsEnabled
                value = if ($current.IsPassword) { '' } else { $value.Substring(0, [Math]::Min(60, $value.Length)) }
                bounds = if ($rect.IsEmpty) { $null } else { [ordered]@{ left = [int]$rect.Left; top = [int]$rect.Top; width = [int]$rect.Width; height = [int]$rect.Height } }
            })
        } catch { continue }
        if ($depth -lt 5) {
            $count = 0
            $c = $walker.GetFirstChild($element)
            while ($c -ne $null -and $count -lt 35) { $queue.Enqueue(@($c, $depth + 1)); $count++; $c = $walker.GetNextSibling($c) }
        }
    }
    [ordered]@{ title = $window.Current.Name; className = $window.Current.ClassName; elements = $rows }
}
ConvertTo-Json -InputObject @($dumps) -Depth 6 | Set-Content -LiteralPath $Out -Encoding utf8
