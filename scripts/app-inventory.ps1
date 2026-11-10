param([string]$CloseTargets = '')
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$names = @('notepad','CalculatorApp','Calculator','mspaint','Taskmgr','Code','WhatsApp','Spotify','Telegram','Instagram')
if ($CloseTargets) {
    # Recheck the exact process before requesting a normal window close; never force-kill it.
    $targets = ConvertFrom-Json -InputObject $CloseTargets
    if (!$targets) { ConvertTo-Json -InputObject @() -Compress; exit }
    if ($targets.Count -gt 40) { throw 'Too many close targets.' }
    $results = @()
    foreach ($target in $targets) {
        $process = Get-Process -Id ([int]$target.pid) -ErrorAction SilentlyContinue
        if (!$process) { $results += @{pid=$target.pid; state='already exited'}; continue }
        if ($names -notcontains $process.ProcessName -or $process.ProcessName -ne $target.name -or $process.StartTime.ToUniversalTime().Ticks.ToString() -ne $target.started) { throw 'Process identity changed; request approval again.' }
        $accepted = $process.CloseMainWindow()
        $results += @{pid=$target.pid; state=$(if ($accepted) {'close requested'} else {'no closable main window'})}
    }
    ConvertTo-Json -InputObject $results -Compress
    exit
}
$startApps = @()
$paths = @{}
$knownPaths = @{
    'notepad.exe' = @("$env:SystemRoot\System32\notepad.exe")
    'calc.exe' = @("$env:SystemRoot\System32\calc.exe")
    'mspaint.exe' = @("$env:SystemRoot\System32\mspaint.exe")
    'explorer.exe' = @("$env:SystemRoot\explorer.exe")
    'Taskmgr.exe' = @("$env:SystemRoot\System32\Taskmgr.exe")
    'Code.exe' = @("$env:LOCALAPPDATA\Programs\Microsoft VS Code\Code.exe", "$env:ProgramFiles\Microsoft VS Code\Code.exe")
    'Spotify.exe' = @("$env:APPDATA\Spotify\Spotify.exe")
    'Telegram.exe' = @("$env:APPDATA\Telegram Desktop\Telegram.exe", "$env:ProgramFiles\Telegram Desktop\Telegram.exe")
    'WhatsApp.exe' = @("$env:LOCALAPPDATA\WhatsApp\WhatsApp.exe")
}
foreach ($binary in $knownPaths.Keys) {
    foreach ($candidate in $knownPaths[$binary]) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { $paths[$binary] = $candidate; break }
    }
}
foreach ($binary in @('Code.exe','Spotify.exe','Telegram.exe','WhatsApp.exe')) {
    foreach ($root in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths','HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths')) {
        try {
            $value = (Get-Item -LiteralPath "$root\$binary" -ErrorAction Stop).GetValue('')
            if ($value -and (Test-Path -LiteralPath $value.Trim('"') -PathType Leaf)) { $paths[$binary] = $value.Trim('"'); break }
        } catch {}
    }
}
$startCoverage = 'available'
try { $startApps = @(Get-StartApps | Select-Object Name, AppID) } catch { $startCoverage = 'unavailable' }
$processes = @(Get-Process | Where-Object { $names -contains $_.ProcessName } | ForEach-Object {
    try { @{pid=$_.Id; name=$_.ProcessName; started=$_.StartTime.ToUniversalTime().Ticks.ToString(); visible=($_.MainWindowHandle -ne [IntPtr]::Zero)} } catch {}
})
$defaultBrowser = ''
try {
    $progId = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice').ProgId
    if ($progId -match '^MSEdgeHTM') { $defaultBrowser = 'msedge' }
    elseif ($progId -match '^ChromeHTML') { $defaultBrowser = 'chrome' }
    else { $defaultBrowser = 'other' }
} catch { $defaultBrowser = 'unknown' }
@{apps=$startApps; paths=$paths; processes=$processes; startCoverage=$startCoverage; defaultBrowser=$defaultBrowser} | ConvertTo-Json -Depth 5 -Compress
