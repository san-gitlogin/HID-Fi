<#
.SYNOPSIS
    Build, flash and verify the ESP32-S3 USB-HID firmware. One command, no setup.

.DESCRIPTION
    Everything is auto-detected: the board's COM port, esptool (the copy that ships
    with the ESP32 core, so pip is not needed) and arduino-cli.

    By default only the four firmware partitions are written, which leaves NVS
    alone - so the board keeps its WiFi settings, access PIN, custom knobs and
    quick actions across an upgrade. Use -Erase for a factory reset.

.EXAMPLE
    .\flash_esp.ps1
    Flash the existing build to the one connected board, then verify it.

.EXAMPLE
    .\flash_esp.ps1 -Compile
    Rebuild from source first, then flash.

.EXAMPLE
    .\flash_esp.ps1 -All
    Flash every connected HID-Fi board in turn.

.EXAMPLE
    .\flash_esp.ps1 -Serial 5CBC409942
    Flash the board with this USB serial number. Unlike COM numbers it does not
    change when the board is plugged into another socket.

.EXAMPLE
    .\flash_esp.ps1 -Which
    Only say which board(s) would be flashed. Opens no port.

.EXAMPLE
    .\flash_esp.ps1 -Recover
    Forgot the board's WiFi password or the access PIN? Show or set the WiFi
    password, or reset the PIN - over the USB COM cable only, which is what
    proves the board is yours. Stored passwords are kept if the vault has its
    own vault PIN; otherwise you are told first and asked to type ERASE.
    For scripts: -RecoverShow, -RecoverSetPassword <new>, -RecoverResetPin <1234|off>
    with -EraseVault.

.EXAMPLE
    .\flash_esp.ps1 -Identify
    Ask each HID-Fi board what firmware it runs. Opening a port can restart it.

.EXAMPLE
    .\flash_esp.ps1 -Erase
    Wipe the chip completely, then flash. Loses all saved settings, and asks
    first. Add -Force to skip the question in a script.

.NOTES
    Several boards plugged in: only HID-Fi boards (their COM connector is a CH343,
    USB VID 1A86 PID 55D3) are ever picked on their own; an Arduino or another ESP32
    kit is listed and left alone. With more than one HID-Fi board it asks which, or
    without a console stops with exit 10. HIDFI_PORT / HIDFI_SERIAL set a default.
    A port that is not a HID-Fi COM bridge is refused unless -ForcePort.

    Exit codes: 0 ok | 1 flash failed | 2 compile failed | 3 no firmware image
                4 no board found | 5 esptool missing | 10 several boards, pick one
                11 that port is not a HID-Fi board
#>
[CmdletBinding()]
param(
    [string]$Port = $env:HIDFI_PORT,
    [string]$Serial = $env:HIDFI_SERIAL,
    [switch]$All,
    [switch]$Which,
    [switch]$Identify,
    [switch]$ForcePort,
    [switch]$Recover,
    [switch]$RecoverShow,
    [string]$RecoverSetPassword,
    [string]$RecoverResetPin,
    [switch]$EraseVault,
    [switch]$Compile,
    [switch]$Erase,
    [switch]$Force,
    [switch]$NoVerify
)

$ErrorActionPreference = 'Stop'
# Inside a function $PSBoundParameters is that function's own; keep the script's.
$ScriptArgs = $PSBoundParameters

$base   = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$sketch = Join-Path $base 'hid_fi'
$build  = if ($env:HIDFI_BUILD_DIR) { $env:HIDFI_BUILD_DIR } else { Join-Path $sketch 'build' }
$fqbn   = 'esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=default,PSRAM=opi,FlashSize=16M,UploadSpeed=921600,PartitionScheme=custom'

function Say  ($m) { Write-Host $m }
function Step ($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Ok   ($m) { Write-Host "   $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "   $m" -ForegroundColor Yellow }
function Bad  ($m) { Write-Host "   $m" -ForegroundColor Red }

# Run a native tool, show its output, and return its exit code.
#
# esptool and arduino-cli both write progress to stderr. Merging that with 2>&1
# while $ErrorActionPreference is 'Stop' makes PowerShell treat the first such
# line as a *terminating* NativeCommandError - which killed the flash partway
# through "Uploading stub flasher", after a successful build, with nothing
# written to the board. Relax the preference around the call and judge the tool
# by its exit code, which is the only thing that actually reports failure.
function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments 2>&1 | Out-Host
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
}

# ---------------------------------------------------------------- tools
# esptool ships inside the ESP32 core, so a fresh machine needs no pip install.
function Find-Esptool {
    if ($env:HIDFI_ESPTOOL) { return @{ Kind = 'exe'; Path = $env:HIDFI_ESPTOOL } }      # tests
    $root = Join-Path $env:LOCALAPPDATA 'Arduino15\packages\esp32\tools\esptool_py'
    if (Test-Path $root) {
        $exe = Get-ChildItem $root -Recurse -Filter 'esptool.exe' -ErrorAction SilentlyContinue |
               Sort-Object FullName -Descending | Select-Object -First 1
        if ($exe) { return @{ Kind = 'exe'; Path = $exe.FullName } }
    }
    foreach ($py in 'python', 'py') {
        try {
            & $py -m esptool version *> $null
            if ($LASTEXITCODE -eq 0) { return @{ Kind = 'py'; Path = $py } }
        } catch { }
    }
    return $null
}

function Find-ArduinoCli {
    $candidates = @(
        (Join-Path $env:USERPROFILE 'arduino-cli\arduino-cli.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\arduino-cli\arduino-cli.exe'),
        'arduino-cli.exe'
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    return $null
}

# boot_app0 lives in the core, not in our build folder
function Find-BootApp0 {
    if ($env:HIDFI_BOOT_APP0) { return $env:HIDFI_BOOT_APP0 }                             # tests
    $root = Join-Path $env:LOCALAPPDATA 'Arduino15\packages\esp32\hardware\esp32'
    if (-not (Test-Path $root)) { return $null }
    $f = Get-ChildItem $root -Recurse -Filter 'boot_app0.bin' -ErrorAction SilentlyContinue |
         Sort-Object FullName -Descending | Select-Object -First 1
    if ($f) { return $f.FullName }
    return $null
}

# ---------------------------------------------------------------- which board
# The same rules as tools/flash_ports.py (used by flash_esp.sh), so a desk with
# several boards behaves the same on every OS. Never guess between boards.
$HidfiCom = @{ Vid = 0x1A86; Pid = 0x55D3 }          # WCH CH343, the board's "COM" connector
$OtherBridges = @{
    '1A86:7523' = 'CH340'; '1A86:55D4' = 'CH9102'; '10C4:EA60' = 'CP210x'
    '0403:6001' = 'FTDI'; '0403:6010' = 'FTDI'; '0403:6014' = 'FTDI'; '0403:6015' = 'FTDI'
    '2341:0043' = 'Arduino Uno'; '2341:0001' = 'Arduino Uno'; '2341:0042' = 'Arduino Mega'
}

function Get-Ports {
    if ($env:HIDFI_FAKE_PORTS) {                      # tests: [{device, vid, pid, serial, ...}]
        return @(Get-Content $env:HIDFI_FAKE_PORTS -Raw | ConvertFrom-Json | ForEach-Object {
            New-PortInfo $_.device ([int]$_.vid) ([int]$_.pid) "$($_.serial)" "$($_.description)" })
    }
    @(Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '\((COM\d+)\)' } |
        ForEach-Object {
            # $Matches is overwritten by every -match, so take each part in turn
            $null = $_.Name -match '\((COM\d+)\)'; $com = $Matches[1]
            $vid = 0; $pid_ = 0; $sn = ''
            if ($_.DeviceID -match 'VID_([0-9A-F]{4})&PID_([0-9A-F]{4})') {
                $vid = [Convert]::ToInt32($Matches[1], 16); $pid_ = [Convert]::ToInt32($Matches[2], 16)
            }
            if ($_.DeviceID -match 'PID_[0-9A-F]{4}\\([^\\&]+)$') { $sn = $Matches[1] }
            New-PortInfo $com $vid $pid_ $sn ($_.Name -replace '\s*\(COM\d+\)', '')
        } | Sort-Object { [int]($_.Port -replace '\D', '') })
}

function New-PortInfo($port, [int]$vid, [int]$pid_, $serial, $desc) {
    $key = '{0:X4}:{1:X4}' -f $vid, $pid_
    $kind = if ($vid -eq $HidfiCom.Vid -and $pid_ -eq $HidfiCom.Pid) { 'hidfi' }
            elseif ($vid -eq 0x303A) { 'esp-native' }
            elseif ($OtherBridges.ContainsKey($key)) { 'other-bridge' }
            elseif ($vid) { 'other' } else { 'not-usb' }
    $what = switch ($kind) {
        'hidfi'        { 'HID-Fi COM (CH343)' }
        'esp-native'   { 'ESP32 native USB - a board in its ROM bootloader, or a different firmware' }
        'other-bridge' { "$($OtherBridges[$key]) serial - not a HID-Fi board" }
        'other'        { "$desc ($($key.ToLower()))" }
        default        { 'not a USB device' }
    }
    [pscustomobject]@{ Port = $port; Serial = $(if ($serial) { $serial } else { '?' }); Kind = $kind; What = $what }
}

function Show-Port($p) { "$($p.Port)  serial $($p.Serial)  $($p.What)" }

# Returns @{ Targets; Candidates; Lines; Code } - the same answers as flash_ports.choose().
function Select-Boards($ports) {
    $devs = @($ports | Where-Object { $_.Kind -ne 'not-usb' })
    $boards = @($devs | Where-Object { $_.Kind -eq 'hidfi' })
    $r = @{ Targets = @(); Candidates = @(); Lines = @(); Code = 0 }
    if ($Port) {
        $hit = $ports | Where-Object { $_.Port -eq $Port } | Select-Object -First 1
        if (-not $hit) {
            $r.Lines = @("$Port is not connected.") + @($devs | ForEach-Object { '  ' + (Show-Port $_) }); $r.Code = 4; return $r
        }
        if ($hit.Kind -eq 'hidfi' -or $hit.Kind -eq 'esp-native' -or $ForcePort) {
            if ($hit.Kind -eq 'esp-native') { $r.Lines = @("$Port is an ESP32's native USB port - if this is a HID-Fi board stuck in its ROM bootloader, flashing it here brings it back.") }
            elseif ($hit.Kind -ne 'hidfi') { $r.Lines = @("$Port is not a HID-Fi COM bridge ($($hit.What)); flashing it anyway because you forced it.") }
            $r.Targets = @($hit); return $r
        }
        $r.Lines = @("$Port is not a HID-Fi board's COM port: $($hit.What).",
                     'Flashing it would overwrite whatever that board runs. If you are sure, add -ForcePort.')
        $r.Code = 11; return $r
    }
    if ($Serial) {
        $hits = @($devs | Where-Object { $_.Serial -ne '?' -and $_.Serial.ToLower().StartsWith($Serial.ToLower()) })
        if ($hits.Count -eq 1) {
            if ($hits[0].Kind -ne 'hidfi' -and -not $ForcePort) {
                $r.Lines = @("Serial $Serial is $(Show-Port $hits[0]) - not a HID-Fi COM bridge. Add -ForcePort if you are sure."); $r.Code = 11; return $r
            }
            $r.Targets = $hits; return $r
        }
        if ($hits.Count -eq 0) { $r.Lines = @("No board with serial $Serial is connected.") + @($devs | ForEach-Object { '  ' + (Show-Port $_) }); $r.Code = 4; return $r }
        $r.Lines = @("Serial $Serial matches $($hits.Count) boards - give more of it:") + @($hits | ForEach-Object { '  ' + (Show-Port $_) }); $r.Code = 10; return $r
    }
    if ($All) {
        if ($boards.Count -eq 0) { $r.Lines = Get-NothingLines $devs; $r.Code = 4; return $r }
        $r.Targets = $boards; $r.Lines = @("Flashing all $($boards.Count) HID-Fi boards."); return $r
    }
    if ($boards.Count -eq 1) {
        $others = @($devs | Where-Object { $_.Port -ne $boards[0].Port })
        if ($others.Count) { $r.Lines = @("Ignoring $($others.Count) other serial device(s), not HID-Fi boards:") + @($others | ForEach-Object { '  ' + (Show-Port $_) }) }
        $r.Targets = $boards; return $r
    }
    if ($boards.Count -eq 0) { $r.Lines = Get-NothingLines $devs; $r.Code = 4; return $r }
    $i = 0
    $r.Lines = @("$($boards.Count) HID-Fi boards are connected, so none was picked:") +
               @($boards | ForEach-Object { $i++; "  $i. $(Show-Port $_)" }) +
               @('Pick one with -Port COMx or -Serial <serial>, or flash them all with -All.')
    $r.Candidates = $boards; $r.Code = 10; return $r
}

function Get-NothingLines($devs) {
    $l = @('No HID-Fi board found. Its COM connector is a CH343 bridge (USB VID 1A86 PID 55D3).')
    $native = @($devs | Where-Object { $_.Kind -eq 'esp-native' })
    if ($native.Count) { $l += "An ESP32 is connected by its native USB port - a HID-Fi board stuck in its ROM bootloader looks like this. To flash it there: -Port $($native[0].Port)" }
    $rest = @($devs | Where-Object { $_.Kind -ne 'esp-native' })
    if ($rest.Count) { $l += 'Other serial devices (never flashed unless named with -Port and -ForcePort):'; $l += @($rest | ForEach-Object { '  ' + (Show-Port $_) }) }
    $l += 'Plug the cable into the port labelled "COM", use a data cable, and install the CH343 driver if Windows shows an unknown device: https://www.wch-ic.com/downloads/CH343SER_EXE.html'
    return $l
}

# ---------------------------------------------------------------- actions
function Invoke-Compile {
    $cli = Find-ArduinoCli
    if (-not $cli) {
        Bad 'arduino-cli not found, so the firmware cannot be rebuilt.'
        Say '   Install it from https://arduino.github.io/arduino-cli/latest/installation/'
        Say '   or drop arduino-cli.exe in %USERPROFILE%\arduino-cli\.'
        Say '   You can still flash the existing build by running without -Compile.'
        exit 2
    }
    Step "Building firmware"
    Say  "   $cli"
    # build_opt.h holds compiler flags every file gets, libraries included. A
    # cached library built with other flags would disagree with the sketch about
    # its own objects' sizes (memory corruption), so a change rebuilds everything.
    $optSum = (Get-FileHash -Algorithm SHA256 (Join-Path $sketch 'build_opt.h')).Hash.ToLower()
    $stamp = Join-Path $build '.build_opt.sha256'
    New-Item -ItemType Directory -Force -Path $build | Out-Null
    # (not $args: PowerShell keeps the script's own arguments in that name)
    $compileArgs = @('compile', '--fqbn', $fqbn, '--output-dir', $build, $sketch)
    if (-not (Test-Path $stamp) -or (Get-Content $stamp -Raw).Trim() -ne $optSum) { $compileArgs = @('compile', '--clean') + $compileArgs[1..($compileArgs.Count-1)] }
    $code = Invoke-Native $cli $compileArgs
    if ($code -eq 0) { Set-Content -Path $stamp -Value $optSum -NoNewline }
    if ($code -ne 0) {
        Bad 'Compile failed. The output above says why.'
        Say '   Missing libraries? Run:'
        Say '     arduino-cli lib install ArduinoJson'
        Say '     arduino-cli lib install WebSockets'
        exit 2
    }
    Ok 'Built.'
}

function Invoke-Flash([string]$p, $tool, [string]$bootApp0) {
    $parts = @(
        @{ At = '0x0';     File = (Join-Path $build 'hid_fi.ino.bootloader.bin') },
        @{ At = '0x8000';  File = (Join-Path $build 'hid_fi.ino.partitions.bin') },
        @{ At = '0xe000';  File = $bootApp0 },
        @{ At = '0x10000'; File = (Join-Path $build 'hid_fi.ino.bin') }
    )

    $args = @('--chip', 'esp32s3', '--port', $p, '--baud', '921600')
    if ($Erase) {
        Warn 'Erasing the whole chip - saved WiFi, PIN and custom controls will be lost.'
        $eargs = $args + @('erase-flash')
        if ($tool.Kind -ne 'exe') { $eargs = @('-m', 'esptool') + $eargs }
        if ((Invoke-Native $tool.Path $eargs) -ne 0) { Bad "Erase failed on $p"; return $false }
    }

    $args += @('write-flash', '--flash-mode', 'dio', '--flash-freq', '80m', '--flash-size', '16MB')
    foreach ($part in $parts) { $args += @($part.At, $part.File) }
    if ($tool.Kind -ne 'exe') { $args = @('-m', 'esptool') + $args }

    Say "   writing 4 partitions to $p"
    $code = Invoke-Native $tool.Path $args

    if ($code -ne 0) {
        Bad "Flash failed on $p."
        Say '   Try this, in order:'
        Say '     1. Unplug the board''s second ("USB") cable, leave only the "COM" one.'
        Say '     2. Hold BOOT, tap RESET, release BOOT, then run this again.'
        Say '     3. Close anything using the port (Arduino Serial Monitor, PuTTY, a Python script).'
        return $false
    }
    Ok "Flashed $p"
    return $true
}

# Ask the board what it is running. Proves the flash actually took.
function Test-Firmware([string]$p) {
    try {
        $sp = New-Object System.IO.Ports.SerialPort $p, 115200, 'None', 8, 'One'
        $sp.DtrEnable = $false      # CH343 auto-reset would reboot the board on open
        $sp.RtsEnable = $false
        $sp.ReadTimeout = 1200
        $sp.NewLine = "`n"
        $sp.Open()
        Start-Sleep -Milliseconds 2600          # let the boot banner finish
        $sp.DiscardInBuffer()
        $sp.WriteLine('{"cmd":"status"}')
        $deadline = (Get-Date).AddSeconds(6)
        while ((Get-Date) -lt $deadline) {
            try { $line = $sp.ReadLine() } catch { continue }
            if ($line -match '"firmware"\s*:\s*"([^"]+)"') {
                $fw  = $Matches[1]
                $hid = if ($line -match '"hid_ready"\s*:\s*true') { 'ready' } else { 'NOT ready' }
                $ap  = if ($line -match '"ap_ssid"\s*:\s*"([^"]+)"') { $Matches[1] } else { '?' }
                Ok "Running $fw, USB HID $hid"
                # The board's own WiFi password, read over this cable. Each board
                # makes its own (v3.10), so this is where a person first learns it.
                $pw = $null
                try { $pw = Send-Board $sp @{ cmd = 'ap_password' } 3 } catch { }
                $sp.Close()
                if ($pw -and $pw.status -eq 'ok' -and $pw.open) { Ok "Dashboard: join WiFi '$($pw.ssid)' (open, no password), then open http://192.168.4.1/" }
                elseif ($pw -and $pw.status -eq 'ok') {
                    Ok "Dashboard: join WiFi '$($pw.ssid)' with password  $($pw.password)"
                    Ok '           Write the password down now. Forgot it later? .\flash_esp.ps1 -Recover shows it again.'
                    Ok '           then open http://192.168.4.1/'
                } else { Ok "Dashboard: join WiFi '$ap' then open http://192.168.4.1/" }
                if ($line -match '"wifi_connected"\s*:\s*true' -and $line -match '"wifi_ip"\s*:\s*"([^"]+)"') {
                    $hip = $Matches[1]
                    $nm = if ($line -match '"mdns"\s*:\s*"([^"]+)"') { "  or  http://$($Matches[1]).local/" } else { '' }
                    Ok "On your home network: http://$hip/$nm"
                }
                if ($hid -ne 'ready') {
                    Warn 'HID is not ready - check the second ("USB") cable and the USB-OTG solder pads.'
                }
                return $true
            }
        }
        $sp.Close()
        Warn 'No status reply. The board may still be booting - re-run with -NoVerify to skip this check.'
        return $false
    } catch {
        Warn "Could not open $p to verify: $($_.Exception.Message)"
        return $false
    }
}

# ---------------------------------------------------------------- recovery
# Over the USB COM cable only: holding the board is ownership. The same choices,
# words and safeguards as tools/recover.py (used by flash_esp.sh).
function Open-Board([string]$p) {
    $sp = New-Object System.IO.Ports.SerialPort $p, 115200, 'None', 8, 'One'
    $sp.DtrEnable = $false; $sp.RtsEnable = $false      # DTR/RTS drive the board's reset
    $sp.ReadTimeout = 400; $sp.NewLine = "`n"
    $sp.Open()
    $settle = if ($env:HIDFI_RECOVER_SETTLE) { [double]$env:HIDFI_RECOVER_SETTLE } else { 2.5 }
    Start-Sleep -Milliseconds ([int]($settle * 1000))
    $sp.DiscardInBuffer()
    return $sp
}

function Send-Board($sp, [hashtable]$body, [double]$timeout = 6) {
    $sp.WriteLine(($body | ConvertTo-Json -Compress))
    $deadline = (Get-Date).AddSeconds($timeout)
    while ((Get-Date) -lt $deadline) {
        try { $line = $sp.ReadLine().Trim() } catch { continue }
        if (-not $line.StartsWith('{')) { continue }
        try { $d = $line | ConvertFrom-Json } catch { continue }
        if ($null -ne $d.status -and -not $d.event) { return $d }
    }
    throw "no answer to $($body.cmd)"
}

function Get-VaultFate($vl) {
    $n = @($vl.entries).Count
    if (-not $vl.entries) { $n = 0 }
    if ($n -eq 0) { return @{ Text = 'nothing stored, nothing to lose'; Erases = $false; Count = 0 } }
    if ($vl.vault_pin_set) { return @{ Text = "$n stored password(s) kept - the vault has its own vault PIN"; Erases = $false; Count = $n } }
    return @{ Text = "$n stored password(s) WILL BE ERASED - they were guarded by the access PIN"; Erases = $true; Count = $n }
}

# Where the board is on the home network - the answer to "what was its address
# again?". Same wording as tools/recover.py.
function Get-HomeLine($st) {
    if (-not $st) { return '' }
    $name = if ($st.mdns) { "http://$($st.mdns).local/" } else { '' }
    if ($st.wifi_connected -and $st.wifi_ip) {
        $ssid = if ($st.wifi_ssid) { $st.wifi_ssid } else { 'joined' }
        return "Home network:  $ssid at http://$($st.wifi_ip)/" + $(if ($name) { "  or  $name" } else { '' })
    }
    if ($st.sta_last_ip) {
        return "Home network:  away - it was at http://$($st.sta_last_ip)/" + $(if ($name) { "; $name finds it when it is back" } else { '' })
    }
    return ''
}

function Show-ApPassword($ap, $st) {
    Say "   WiFi network:  $($ap.ssid)"
    if ($ap.open) { Say '   Password:      none - the network is open' }
    else {
        Say "   Password:      $($ap.password)"
        if ($ap.generated) { Say '   (This board made this password for itself. You can keep it or set your own.)' }
    }
    $h = Get-HomeLine $st
    if ($h) { Say "   $h" }
    return 0
}

function Set-ApPassword($sp, $ap, [string]$new) {
    $len = [Text.Encoding]::UTF8.GetByteCount($new)
    if ($len -lt 8 -or $len -gt 63) { Say '   A WiFi password needs 8 to 63 characters. Nothing changed.'; return 1 }
    # ap_configure restarts the access point and does not answer, by design.
    $sp.WriteLine((@{ cmd = 'ap_configure'; ssid = $ap.ssid; password = $new; open = $false } | ConvertTo-Json -Compress))
    Start-Sleep -Milliseconds 1500
    $again = Send-Board $sp @{ cmd = 'ap_password' }
    if ($again.password -ne $new) { Say '   The board did not take the new password. Nothing changed.'; return 1 }
    Say "   Done. Join '$($again.ssid)' with the new password. Phones on it now will drop off."
    return 0
}

function Reset-AccessPin($sp, $vl, [string]$new, [bool]$eraseOk, [bool]$interactive) {
    $off = ($new -eq '' -or $new -eq 'off')
    $pin = if ($off) { '' } else { $new }
    if ($pin -and ($pin -notmatch '^\d{4}$')) { Say '   A PIN is four digits. Nothing changed.'; return 1 }
    $fate = Get-VaultFate $vl
    Say "   Stored passwords: $($fate.Text)."
    if ($fate.Erases -and -not $eraseOk) {
        if (-not $interactive) { Say '   Refusing: this would erase the vault. Pass -EraseVault if that is what you want.'; return 1 }
        $a = Read-Host '   Type ERASE to reset the PIN and erase them, anything else to stop'
        if ($a -cne 'ERASE') { Say '   Nothing changed.'; return 1 }
    }
    $r = Send-Board $sp @{ cmd = 'set_auth'; token = $pin }
    if ($r.status -ne 'ok') { Say "   The board refused: $($r.message)"; return 1 }
    $st = Send-Board $sp @{ cmd = 'status' }
    if ([bool]$st.auth_set -ne [bool]$pin) { Say '   The board did not take it. Nothing changed.'; return 1 }
    if ($off) { Say '   Access PIN turned off.' } else { Say '   Access PIN set. Phones will ask for it on their next connection.' }
    if ($r.vault_wiped) { Say '   The stored passwords were erased, as warned.' }
    return 0
}

function Invoke-Recover {
    Say ''
    Say '== HID-Fi recovery (USB COM cable)'
    $sel = Select-Boards (Get-Ports)
    $sel.Lines | ForEach-Object { Say "   $_" }
    if ($sel.Code -eq 10 -and $sel.Candidates.Count -and -not $env:CI -and [Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
        $ans = Read-Host "   Which board? [1-$($sel.Candidates.Count), Enter = none]"
        if ($ans -match '^\d+$' -and [int]$ans -ge 1 -and [int]$ans -le $sel.Candidates.Count) {
            $sel.Targets = @($sel.Candidates[[int]$ans - 1]); $sel.Code = 0
        } else { Say '   Nothing chosen, nothing changed.'; return 10 }
    }
    if ($sel.Code -ne 0) { return $sel.Code }
    $b = $sel.Targets[0]
    Say "   Board on $($b.Port) (serial $($b.Serial))"
    try { $sp = Open-Board $b.Port }
    catch { Say "   Could not open $($b.Port): $($_.Exception.Message)"; Say '   Close anything else using it (Arduino Serial Monitor, PuTTY) and try again.'; return 1 }
    try {
        $st = Send-Board $sp @{ cmd = 'status' }
        $ap = Send-Board $sp @{ cmd = 'ap_password' }
        $vl = Send-Board $sp @{ cmd = 'vault_list' }
        if ($ap.status -ne 'ok') {
            Say '   This board''s firmware is older than v3.10 and cannot show its WiFi password.'
            Say '   Flash the current firmware first: it keeps every setting and stored password.'
            return 1
        }
        if ($RecoverShow) { return (Show-ApPassword $ap $st) }
        if ($ScriptArgs.ContainsKey('RecoverSetPassword')) { return (Set-ApPassword $sp $ap $RecoverSetPassword) }
        if ($ScriptArgs.ContainsKey('RecoverResetPin')) { return (Reset-AccessPin $sp $vl $RecoverResetPin ([bool]$EraseVault) $false) }
        if ([Console]::IsInputRedirected) { Say '   No choice given. Use -RecoverShow, -RecoverSetPassword or -RecoverResetPin, or run it in a console.'; return 1 }
        $pinState = if ($st.auth_set) { 'set' } else { 'not set' }
        Say "   Board: $($st.firmware)  WiFi: $($ap.ssid)  access PIN: $pinState"
        Say "   Stored passwords: $((Get-VaultFate $vl).Text)"
        $h = Get-HomeLine $st
        if ($h) { Say "   $h" }
        Say ''
        Say '   1  Show this board''s WiFi name and password'
        Say '   2  Set a new WiFi password'
        Say '   3  Reset the access PIN (or turn it off)'
        Say '   4  Quit'
        $c = Read-Host '   Choose 1-4'
        switch ($c) {
            '1' { return (Show-ApPassword $ap $st) }
            '2' { $a = Read-Host '   New WiFi password (8-63 characters)'; $b2 = Read-Host '   Type it again'
                  if ($a -cne $b2) { Say '   They did not match. Nothing changed.'; return 1 }
                  return (Set-ApPassword $sp $ap $a) }
            '3' { $p = Read-Host '   New 4 digit access PIN, or Enter to turn the PIN off'
                  return (Reset-AccessPin $sp $vl $p.Trim() $false $true) }
            default { Say '   Nothing changed.'; return 0 }
        }
    } catch { Say "   The board did not answer ($_). Is the cable in the board's COM port?"; return 1 }
    finally { $sp.Close() }
}

# ---------------------------------------------------------------- run
Say ''
Say 'ESP32-S3 USB-HID firmware flasher'
Say '---------------------------------'

if ($Recover -or $RecoverShow -or $ScriptArgs.ContainsKey('RecoverSetPassword') -or $ScriptArgs.ContainsKey('RecoverResetPin')) {
    exit (Invoke-Recover)
}

Step 'Finding the board'
$sel = Select-Boards (Get-Ports)
$sel.Lines | ForEach-Object { Say "   $_" }
if ($Identify) {
    $cands = @($sel.Targets) + @($sel.Candidates)
    if (-not $cands.Count) { $cands = @(Get-Ports | Where-Object { $_.Kind -in 'hidfi', 'esp-native' }) }
    Say '   Asking each board what it runs (this can restart it):'
    foreach ($c in $cands) { Say "   $($c.Port)  serial $($c.Serial)"; [void](Test-Firmware $c.Port) }
    exit 0
}
if ($sel.Code -eq 10 -and $sel.Candidates.Count -and -not $Which -and -not $env:CI -and [Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
    $ans = Read-Host "   Which one? [1-$($sel.Candidates.Count), a = all, Enter = none]"
    if ($ans -eq 'a') { $sel.Targets = $sel.Candidates; $sel.Code = 0 }
    elseif ($ans -match '^\d+$' -and [int]$ans -ge 1 -and [int]$ans -le $sel.Candidates.Count) {
        $sel.Targets = @($sel.Candidates[[int]$ans - 1]); $sel.Code = 0
    } else { Bad 'Nothing chosen, nothing flashed.'; exit 10 }
}
if ($sel.Code -ne 0) {
    if ($sel.Code -eq 10) { Bad 'Several HID-Fi boards - I will not guess.' } else { Bad 'Nothing flashed.' }
    exit $sel.Code
}
$targets = @($sel.Targets)
$targets | ForEach-Object { Ok "Using $($_.Port)  (serial $($_.Serial))" }
if ($Which) { exit 0 }

# Nothing here can be undone: the board never hands back a password or a PIN, so
# there is no way to save them first and put them back afterwards.
if ($Erase -and -not $Force) {
    Say ''
    Warn 'A full erase wipes everything the board has stored:'
    Say  '     - the access point name and its password'
    Say  '     - the saved home network and its password'
    Say  '     - the access PIN, saved PC passwords, macros and knobs'
    Say  '     - the dashboard layout'
    Say  '   Passwords and PINs cannot be read off the board, so none of this can'
    Say  '   be backed up. You will have to type it all in again.'
    Say  ''
    Say  "   This applies to $($targets.Count) board(s)."
    $answer = Read-Host '   Type ERASE to continue, anything else to stop'
    if ($answer -cne 'ERASE') { Say '   Nothing was changed.'; exit 0 }
}

if ($Compile) { Invoke-Compile }

Step 'Checking the firmware image'
$app = Join-Path $build 'hid_fi.ino.bin'
if (-not (Test-Path $app)) {
    Bad "No firmware image in $build"
    Say '   Run:  .\flash_esp.ps1 -Compile'
    exit 3
}
Ok ("{0:N0} bytes, built {1}" -f (Get-Item $app).Length, (Get-Item $app).LastWriteTime)

Step 'Finding esptool'
$tool = Find-Esptool
if (-not $tool) {
    Bad 'esptool not found.'
    Say '   It normally ships with the ESP32 board package. Either install that in'
    Say '   Arduino IDE / arduino-cli, or run:  pip install esptool'
    exit 5
}
Ok $tool.Path

$bootApp0 = Find-BootApp0
if (-not $bootApp0) {
    Bad 'boot_app0.bin not found - the ESP32 board package does not look installed.'
    exit 5
}

$failed = 0
$summary = @()
foreach ($t in $targets) {
    Step "Flashing $($t.Port)"
    if (Invoke-Flash $t.Port $tool $bootApp0) {
        $summary += "ok      $($t.Port)"
        if (-not $NoVerify) {
            Step "Verifying $($t.Port)"
            Start-Sleep -Milliseconds 900       # board reboots after flashing
            [void](Test-Firmware $t.Port)
        }
    } else { $failed++; $summary += "FAILED  $($t.Port)" }
}

Say ''
if ($targets.Count -gt 1) { Step 'Summary'; $summary | ForEach-Object { Say "   $_" } }
if ($failed -gt 0) { Bad "$failed board(s) failed."; exit 1 }
Ok 'Done.'
exit 0
