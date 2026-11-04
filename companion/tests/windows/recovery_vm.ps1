# Recovery mode on real Windows with a real board passed through from the Mac
# (docs/RECOVERY_MODE.md). The board must already be in recovery mode and attached
# to this VM. Prints facts only: never the WiFi password.
#   vm.sh ssh 'powershell -File C:/hidfi/companion/tests/windows/recovery_vm.ps1'
$ErrorActionPreference = 'Continue'
function Say($m) { Write-Output $m }

Say '== the board as Windows sees it'
$dev = Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_303A' }
$dev | ForEach-Object { Say ("   {0,-8} {1,-12} {2}" -f $_.Status, $_.Class, $_.FriendlyName) }
$ports = @(Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match '\((COM\d+)\)' -and $_.DeviceID -match 'VID_303A' })
Say ("   serial ports from the board: {0}  ({1})" -f $ports.Count, (($ports | ForEach-Object { $_.Name }) -join '; '))
$bad = @($dev | Where-Object { $_.Status -ne 'OK' })
Say ("   devices not OK (driver missing or failed): {0}" -f $bad.Count)
$hid = @($dev | Where-Object { $_.Class -match 'HIDClass|Keyboard|Mouse' -and $_.Status -eq 'OK' })
Say ("   keyboard/mouse/HID entries working: {0}" -f $hid.Count)

Say '== flash_esp.ps1 -Recover -RecoverShow -KeepOpen (Windows port listing, .NET serial)'
$out = & powershell -NoProfile -ExecutionPolicy Bypass -File C:/hidfi/flash_esp.ps1 -Recover -RecoverShow -KeepOpen 2>&1 | Out-String
Say ("   found recovery mode: {0}" -f ($out -match 'recovery mode'))
Say ("   showed a password line: {0}" -f ($out -match 'Password:\s+\S'))
Say ("   kept open as asked: {0}" -f ($out -match 'stays open'))

Say '== the Companion''s own code (recovery_hw.exe): detection, handshake, reveal, close'
& C:/hidfi/companion/target/release/examples/recovery_hw.exe 2>&1 | ForEach-Object { Say "   $_" }
Start-Sleep 4
$after = @(Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match '\((COM\d+)\)' -and $_.DeviceID -match 'VID_303A' })
Say ("   serial ports from the board after close: {0}" -f $after.Count)
