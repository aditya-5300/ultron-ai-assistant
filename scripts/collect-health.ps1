param([switch]$Deep)
# Read available Windows measurements without collecting desktop screenshots or raw event messages.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$coverage = [ordered]@{}
$result = [ordered]@{ processes=@(); battery=$null; powerPlan=$null; volumes=@(); disks=@(); logs=@(); coverage=$coverage }
function Read-Number($Value) {
  if ($null -eq $Value) { return $null }
  try { $number = [double]$Value; if ($number -ge 0 -and $number -lt 2147483647) { return $number } } catch {}
  return $null
}
try {
  $processes = @(Get-Process | ForEach-Object {
    try {
      $started = $null
      try { $started = $_.StartTime.ToUniversalTime().ToString('o') } catch {}
      $responding = $null
      if ($_.MainWindowHandle -ne 0) { $responding = $_.Responding }
      [pscustomobject]@{ pid=$_.Id; name=$_.ProcessName; cpuSeconds=$_.CPU; workingSetBytes=$_.WorkingSet64; started=$started; responding=$responding }
    } catch {}
  })
  $result.processes = $processes
  $coverage.processes = 'ok: process names and counters only; no command lines or window titles'
} catch { $coverage.processes = 'unavailable: Windows process query failed' }
try {
  $batteries = @(Get-CimInstance Win32_Battery)
  if ($batteries.Count -eq 0) {
    $result.battery = @{ present=$false }
    $coverage.battery = 'not present: no Windows battery device'
  } else {
    $device = $batteries[0]
    $percent = Read-Number $device.EstimatedChargeRemaining
    if ($percent -gt 100) { $percent = $null }
    $battery = [ordered]@{ present=$true; count=$batteries.Count; percent=$percent; acOnline=$null; charging=$null; discharging=$null; fullCapacity=$null; designCapacity=$null; capacitySource='unavailable'; cycles=$null }
    try {
      $statuses = @(Get-CimInstance -Namespace root/wmi -ClassName BatteryStatus)
      $status = $statuses | Select-Object -First 1
      if ($status -and $batteries.Count -eq 1) { $battery.acOnline=[bool]$status.PowerOnline; $battery.charging=[bool]$status.Charging; $battery.discharging=[bool]$status.Discharging }
    } catch {}
    $result.battery = $battery
    $coverage.battery = if ($batteries.Count -eq 1) { 'ok: one battery; charge and power source may be partly unavailable' } else { 'partial: multiple batteries; first charge shown, no combined drain/capacity inference' }
  }
} catch { $coverage.battery = 'unavailable: battery provider failed' }
try {
  $power = (& "$env:SystemRoot\System32\powercfg.exe" /getactivescheme 2>$null) -join ' '
  if ($LASTEXITCODE -ne 0) { throw 'Power query failed' }
  $guidMatch = [regex]::Match($power, '[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}')
  if (-not $guidMatch.Success) { throw 'No power plan GUID' }
  $planId = $guidMatch.Value.ToLowerInvariant()
  $names = @{ '381b4222-f694-41f0-9685-ff5bb260df2e'='Balanced'; '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c'='High performance'; 'a1841308-3541-4fab-bc81-f71556f20b4a'='Power saver' }
  $planName = if ($names.ContainsKey($planId)) { $names[$planId] } else { 'Custom / OEM plan' }
  $result.powerPlan = @{ id=$planId; name=$planName }
  $coverage.powerPlan = 'ok'
} catch { $coverage.powerPlan = 'unavailable: active power plan query failed' }
if ($Deep) {
  try {
    $result.volumes = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Where-Object { $_.Size -gt 0 } | ForEach-Object { @{ drive=$_.DeviceID; totalBytes=[double]$_.Size; freeBytes=[double]$_.FreeSpace; freePct=[double]$_.FreeSpace / [double]$_.Size * 100 } })
    $coverage.volumes = 'ok'
  } catch { $coverage.volumes = 'unavailable: mounted volume query failed' }
  try {
    $result.disks = @(Get-PhysicalDisk | ForEach-Object {
      $disk = [ordered]@{ id=[string]$_.DeviceId; health=[string]$_.HealthStatus; media=[string]$_.MediaType; temperatureC=$null; wearPct=$null; uncorrectedErrors=$null }
      try {
        $reliability = $_ | Get-StorageReliabilityCounter
        $temperature = Read-Number $reliability.Temperature
        if ($temperature -gt 0 -and $temperature -lt 150) { $disk.temperatureC = $temperature }
        $wear = Read-Number $reliability.Wear
        if ($null -ne $wear -and $wear -le 100) { $disk.wearPct=$wear }
        if ($null -ne $reliability.ReadErrorsUncorrected -and $null -ne $reliability.WriteErrorsUncorrected) { $disk.uncorrectedErrors=[double]$reliability.ReadErrorsUncorrected + [double]$reliability.WriteErrorsUncorrected }
      } catch {}
      [pscustomobject]$disk
    })
    $coverage.disks = 'partial: Windows health available where supported; SMART temperature/wear/errors may need hardware support or privileges'
  } catch { $coverage.disks = 'unavailable: physical disk provider failed; no privileged retry attempted' }
  $events = @()
  $logResults = @()
  foreach ($logName in @('System','Application')) {
    try {
      $logEvents = @(Get-WinEvent -FilterHashtable @{ LogName=$logName; Level=@(1,2,3); Id=@(7,18,19,41,51,55,153,1000,1001,1002); StartTime=(Get-Date).AddHours(-1) } -MaxEvents 40 -ErrorAction Stop)
      $events += @($logEvents | ForEach-Object { @{ log=$logName; recordId=$_.RecordId; id=$_.Id; provider=$_.ProviderName; level=$_.Level; time=$_.TimeCreated.ToUniversalTime().ToString('o') } })
      $logResults += "$logName read (at most 40 relevant recent critical/error/warning records)"
    } catch {
      if ($_.FullyQualifiedErrorId -like '*NoMatchingEventsFound*') { $logResults += "$logName read (no matching errors)" }
      else { $logResults += "$logName unavailable or access denied" }
    }
  }
  $result.logs = $events
  $coverage.logs = $logResults -join '; '
  if ($result.battery -and $result.battery.present -and $result.battery.count -eq 1) {
    $temporary = $null
    try {
      $folder = Join-Path (Get-Location).Path '.ultron\health-tmp'
      [System.IO.Directory]::CreateDirectory($folder) | Out-Null
      $temporary = Join-Path $folder (([guid]::NewGuid().ToString()) + '.xml')
      $null = & "$env:SystemRoot\System32\powercfg.exe" /batteryreport /xml /output $temporary 2>$null
      if ($LASTEXITCODE -ne 0) { throw 'Battery report failed' }
      [xml]$report = Get-Content -LiteralPath $temporary -Raw
      $reported = @($report.BatteryReport.Batteries.Battery)[0]
      $result.battery.designCapacity = Read-Number $reported.DesignCapacity
      $result.battery.fullCapacity = Read-Number $reported.FullChargeCapacity
      $cycles = Read-Number $reported.CycleCount
      if ($cycles -gt 0) { $result.battery.cycles = $cycles }
      $result.battery.capacitySource = 'Windows battery report; reported capacity units'
      $coverage.capacity = 'ok: selected numeric capacity fields extracted; full temporary report deleted'
    } catch { $coverage.capacity = 'unavailable: capacity report unsupported or access denied' }
    finally { if ($temporary -and [System.IO.File]::Exists($temporary)) { [System.IO.File]::Delete($temporary) } }
  }
  $result.deepTime = [DateTime]::UtcNow.ToString('o')
}
$result | ConvertTo-Json -Depth 8 -Compress
