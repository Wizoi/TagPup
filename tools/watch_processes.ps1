# Log every process that appears while something runs, with its parent and
# grandparent, so a window on the desktop, a port held or a file that will not delete
# can be traced to the spawn behind it. Found the terminal-window flood in one run after
# three rounds of guessing (docs/findings.md, #124).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools/watch_processes.ps1 `
#       -Seconds 120 -Out watch.log [-Names conhost.exe,OpenConsole.exe,WindowsTerminal.exe]
#
# Start it, do the thing (run the suite, click the button), then read the log.
param(
    [int]$Seconds = 120,
    [Parameter(Mandatory = $true)][string]$Out,
    [string[]]$Names = @('conhost.exe', 'OpenConsole.exe', 'WindowsTerminal.exe', 'python.exe', 'cmd.exe')
)
$filter = ($Names | ForEach-Object { "Name='$_'" }) -join ' or '
$seen = @{}
foreach ($p in Get-CimInstance Win32_Process -Filter $filter) { $seen[$p.ProcessId] = $true }
$end = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $end) {
    foreach ($p in Get-CimInstance Win32_Process -Filter $filter) {
        if ($seen.ContainsKey($p.ProcessId)) { continue }
        $seen[$p.ProcessId] = $true
        $parent = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $p.ParentProcessId)
        $grand = $null
        if ($parent) { $grand = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $parent.ParentProcessId) }
        $line = "{0} {1} pid={2} [{3}] <- {4} [{5}] <- {6}" -f (Get-Date -Format 'HH:mm:ss'), $p.Name, $p.ProcessId,
            $p.CommandLine, $(if ($parent) { $parent.Name } else { '?' }),
            $(if ($parent) { $parent.CommandLine } else { '' }), $(if ($grand) { $grand.Name } else { '?' })
        Add-Content -Path $Out -Value $line -Encoding utf8
    }
    Start-Sleep -Milliseconds 300
}
