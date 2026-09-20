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
    Flash every connected board in turn.

.EXAMPLE
    .\flash_esp.ps1 -Erase
    Wipe the chip completely, then flash. Loses all saved settings, and asks
    first. Add -Force to skip the question in a script.

.NOTES
    Exit codes: 0 ok | 1 flash failed | 2 compile failed | 3 no firmware image
                4 no board found | 5 esptool missing | 10 several boards, pick one
#>
[CmdletBinding()]
param(
    [string]$Port,
    [switch]$All,
    [switch]$Compile,
    [switch]$Erase,
    [switch]$Force,
    [switch]$NoVerify
)

$ErrorActionPreference = 'Stop'

$base   = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$sketch = Join-Path $base 'hid_fi'
$build  = Join-Path $sketch 'build'
$fqbn   = 'esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=default,PSRAM=opi,FlashSize=16M,UploadSpeed=921600,PartitionScheme=huge_app'

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
    $root = Join-Path $env:LOCALAPPDATA 'Arduino15\packages\esp32\hardware\esp32'
    if (-not (Test-Path $root)) { return $null }
    $f = Get-ChildItem $root -Recurse -Filter 'boot_app0.bin' -ErrorAction SilentlyContinue |
         Sort-Object FullName -Descending | Select-Object -First 1
    if ($f) { return $f.FullName }
    return $null
}

# The board's "COM" cable is a CH343 bridge: VID 1A86, PID 55D3.
function Get-Boards {
    Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
        Where-Object { $_.DeviceID -match 'VID_1A86&PID_55D3' -and $_.Name -match '\(COM\d+\)' } |
        ForEach-Object {
            # $Matches is overwritten by every -match, so grab the port before matching again
            $null = $_.Name -match '\((COM\d+)\)'
            $com = $Matches[1]
            $serial = if ($_.DeviceID -match 'PID_55D3\\(.+)$') { $Matches[1] } else { '?' }
            [pscustomobject]@{ Port = $com; Serial = $serial }
        } | Sort-Object Port
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
    $code = Invoke-Native $cli @('compile', '--fqbn', $fqbn, '--output-dir', $build, $sketch)
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
                $sp.Close()
                Ok "Running $fw, USB HID $hid"
                # A factory-named access point is still on the built-in password.
                # Anything else has been renamed, so its password is the user's.
                $pw = if ($ap -match '^ESP32-HID-[0-9A-F]{6}$') { "password hid12345" }
                      else { "the password you set" }
                Ok "Dashboard: connect to WiFi '$ap' ($pw) then open http://192.168.4.1/"
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

# ---------------------------------------------------------------- run
Say ''
Say 'ESP32-S3 USB-HID firmware flasher'
Say '---------------------------------'

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

Step 'Finding the board'
if ($Port) {
    $targets = @([pscustomobject]@{ Port = $Port; Serial = 'forced' })
    Ok "Using $Port (you named it)"
} else {
    $found = @(Get-Boards)
    if ($found.Count -eq 0) {
        Bad 'No ESP32-S3 board detected.'
        Say '   Plug the cable into the port labelled "COM" (the CH343 side).'
        Say '   A charge-only cable will not work - it must carry data.'
        Say '   If Windows shows an unknown device, install the CH343 driver:'
        Say '     https://www.wch-ic.com/downloads/CH343SER_EXE.html'
        exit 4
    }
    if ($found.Count -gt 1 -and -not $All) {
        Bad "$($found.Count) boards are connected, so I will not guess."
        $found | ForEach-Object { Say "     $($_.Port)  serial $($_.Serial)" }
        Say '   Pick one:      .\flash_esp.ps1 -Port COM13'
        Say '   Or do them all: .\flash_esp.ps1 -All'
        exit 10
    }
    $targets = $found
    $targets | ForEach-Object { Ok "$($_.Port)  serial $($_.Serial)" }
}

$failed = 0
foreach ($t in $targets) {
    Step "Flashing $($t.Port)"
    if (Invoke-Flash $t.Port $tool $bootApp0) {
        if (-not $NoVerify) {
            Step "Verifying $($t.Port)"
            Start-Sleep -Milliseconds 900       # board reboots after flashing
            [void](Test-Firmware $t.Port)
        }
    } else { $failed++ }
}

Say ''
if ($failed -gt 0) { Bad "$failed board(s) failed."; exit 1 }
Ok 'Done.'
exit 0
