param([Parameter(Mandatory = $true)][ValidateSet('pause', 'play', 'next', 'previous', 'forward', 'back')][string]$Command,
      [int]$Seconds = 10)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
[void][Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
# Choose the session the user means: the one Windows calls current, else a Playing one (pause/next/previous/seek) or a Paused one (play).
$sessions = @($mgr.GetSessions())
if ($sessions.Count -eq 0) { 'none|none|'; exit 0 }
$wanted = if ($Command -eq 'play') { 'Paused' } else { 'Playing' }
$s = $mgr.GetCurrentSession()
if ($s -eq $null -or ($Command -ne 'play' -and $s.GetPlaybackInfo().PlaybackStatus -ne 'Playing')) {
  $pick = $sessions | Where-Object { $_.GetPlaybackInfo().PlaybackStatus -eq $wanted } | Select-Object -First 1
  if ($pick -ne $null) { $s = $pick }
}
$info = $s.GetPlaybackInfo()
$controls = $info.Controls
if (($Command -eq 'next' -and -not $controls.IsNextEnabled) -or ($Command -eq 'previous' -and -not $controls.IsPreviousEnabled)) { "{0}|unsupported|" -f $s.SourceAppUserModelId; exit 0 }
if ($Command -eq 'forward' -or $Command -eq 'back') {
  if (-not $controls.IsPlaybackPositionEnabled) { "{0}|unsupported|" -f $s.SourceAppUserModelId; exit 0 }
  $tl = $s.GetTimelineProperties()
  $pos = $tl.Position
  # A browser reports the position only when its state changes: add the time it has been playing since.
  if ($info.PlaybackStatus -eq 'Playing' -and $tl.LastUpdatedTime.Year -gt 2000) { $pos = $pos + ([DateTimeOffset]::Now - $tl.LastUpdatedTime) }
  $delta = [TimeSpan]::FromSeconds($(if ($Command -eq 'forward') { $Seconds } else { -$Seconds }))
  $target = $pos + $delta
  if ($target -lt $tl.StartTime) { $target = $tl.StartTime }
  if ($tl.EndTime -gt [TimeSpan]::Zero -and $target -gt $tl.EndTime) { $target = $tl.EndTime - [TimeSpan]::FromSeconds(1) }
  $ok = Await ($s.TryChangePlaybackPositionAsync([long]$target.Ticks)) ([bool])
  "{0}|{1}|{2}|{3}" -f $s.SourceAppUserModelId, $ok, [int]$pos.TotalSeconds, [int]$target.TotalSeconds
  exit 0
}
$ok = switch ($Command) {
  'pause'    { Await ($s.TryPauseAsync()) ([bool]) }
  'play'     { Await ($s.TryPlayAsync()) ([bool]) }
  'next'     { Await ($s.TrySkipNextAsync()) ([bool]) }
  'previous' { Await ($s.TrySkipPreviousAsync()) ([bool]) }
}
"{0}|{1}|{2}" -f $s.SourceAppUserModelId, $ok, ''
