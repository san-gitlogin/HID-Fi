#!/usr/bin/env bash
# A Windows 11 (ARM64) VM on an Apple-silicon Mac, for testing HID-Fi Companion
# on Windows without a Windows PC: QEMU + Apple's hypervisor, everything in
# one folder (an external SSD is fine). Windows runs the x64 app through its
# own x64 emulation; an ARM64 build runs natively.
#
#   export HIDFI_WIN_VM_HOME="/Volumes/Extreme SSD/HIDFi-Windows"
#   companion/tests/windows/vm.sh create   # once: unattended install (~30-60 min)
#   companion/tests/windows/vm.sh up | down | status
#   companion/tests/windows/vm.sh ssh [command]   # PowerShell in the VM
#   companion/tests/windows/vm.sh shot out.png    # what its screen shows
#   companion/tests/windows/vm.sh provision       # once: Python, Chrome, Playwright
#   companion/tests/windows/vm.sh sync [x64|arm64]  # tests + Windows build into C:\hidfi
#   companion/tests/windows/vm.sh desktop "cmd"   # run in the logged-on desktop
#     (SSH sessions have no desktop: screen capture only works from there)
#
# Needs: qemu (brew install qemu), in $HIDFI_WIN_VM_HOME/iso:
#   Win11_arm64_en-us.iso  - Microsoft's official Windows 11 ARM64 image
#   python-amd64.exe, chrome-arm64.msi - for `provision` (python.org, Google)
#                            (microsoft.com/software-download/windows11arm64)
#   virtio-win.iso         - the virtio drivers (fedorapeople.org virtio-win)
#   OpenSSH-ARM64-*.msi    - Microsoft's Win32-OpenSSH (github.com/PowerShell)
# Windows is not activated (a generic install key picks the edition): fine for
# a test machine. The VM's password and SSH key are made here at create time
# and stay in ~/.hidfi-win-vm (HIDFI_WIN_VM_STATE) - never in the repo.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
H="${HIDFI_WIN_VM_HOME:?set HIDFI_WIN_VM_HOME to the folder that holds the VM}"
VM="$H/vm"
ISO="$H/iso"
# The small private things (control socket, pid, SSH key, password) stay on
# this Mac: an exFAT SSD cannot hold a Unix socket, nor keep a key owner-only
# (ssh refuses it). The disk and the images go in $HIDFI_WIN_VM_HOME.
S="${HIDFI_WIN_VM_STATE:-$HOME/.hidfi-win-vm}"
SSH_PORT="${HIDFI_WIN_VM_SSH:-2222}"
FW=/opt/homebrew/share/qemu/edk2-aarch64-code.fd
die() { echo "vm.sh: $*" >&2; exit 1; }

monitor() { # a QEMU monitor command
  python3 - "$S/monitor.sock" "$*" <<'EOF'
import socket, sys, time
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); time.sleep(0.2); s.recv(65536)
s.sendall((sys.argv[2] + "\n").encode()); time.sleep(0.5)
try:
    print(s.recv(65536).decode(errors="replace"))
except Exception:
    pass
EOF
}

running() { [ -f "$S/qemu.pid" ] && kill -0 "$(cat "$S/qemu.pid")" 2>/dev/null; }

qemu() { # qemu [extra args...]
  command -v qemu-system-aarch64 >/dev/null || die "qemu is not installed (brew install qemu)"
  qemu-system-aarch64 \
    -name hidfi-win -M virt,highmem=on -accel hvf -cpu host -smp 4 -m "${HIDFI_WIN_VM_RAM:-6144}" \
    -drive if=pflash,format=raw,readonly=on,file="$FW" \
    -drive if=pflash,format=raw,file="$VM/efivars.fd" \
    -device ramfb -device qemu-xhci -device usb-kbd -device usb-tablet \
    -drive if=none,id=sys,file="$VM/disk.qcow2",format=qcow2,cache=writeback \
    -device nvme,drive=sys,serial=hidfi-sys \
    -netdev user,id=n0,hostfwd=tcp:127.0.0.1:"$SSH_PORT"-:22 -device virtio-net-pci,netdev=n0 \
    -monitor unix:"$S/monitor.sock",server,nowait \
    ${HIDFI_WIN_AUDIO:+-audiodev none,id=snd0 -device intel-hda -device hda-duplex,audiodev=snd0} \
    -vnc 127.0.0.1:5 -display none -daemonize -pidfile "$S/qemu.pid" \
    "$@"
}

CDS=()
cd_drive() { # cd_drive id file: adds a USB CD/DVD drive to CDS (paths may hold spaces)
  CDS+=(-drive "if=none,id=$1,media=cdrom,readonly=on,file=$2" -device "usb-storage,drive=$1,removable=on")
}

wait_ssh() { # wait_ssh seconds
  local end=$((SECONDS + $1))
  while [ $SECONDS -lt $end ]; do
    if ssh_vm "exit 0" >/dev/null 2>&1; then return 0; fi
    running || die "the VM stopped (see $VM/qemu.log and: vm.sh shot)"
    sleep 15
  done
  return 1
}

scp_vm() { # scp_vm local... remote-dir
  scp -q -r -i "$S/id_ed25519" -P "$SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o LogLevel=ERROR "${@:1:$#-1}" "hidfi@127.0.0.1:${!#}"
}

ssh_vm() {
  ssh -i "$S/id_ed25519" -p "$SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o LogLevel=ERROR -o ConnectTimeout=5 -o BatchMode=yes hidfi@127.0.0.1 "$@"
}

case "${1:-}" in
  create)
    [ -f "$ISO/Win11_arm64_en-us.iso" ] || die "missing $ISO/Win11_arm64_en-us.iso"
    [ -f "$ISO/virtio-win.iso" ] || die "missing $ISO/virtio-win.iso"
    msi=$(ls "$ISO"/OpenSSH-ARM64-*.msi 2>/dev/null | head -1); [ -n "$msi" ] || die "missing $ISO/OpenSSH-ARM64-*.msi"
    [ -f "$VM/disk.qcow2" ] && die "$VM/disk.qcow2 exists: the VM is made already (vm.sh up)"
    mkdir -p "$VM" "$S" && chmod 700 "$S"
    qemu-img create -q -f qcow2 "$VM/disk.qcow2" 80G
    dd if=/dev/zero of="$VM/efivars.fd" bs=1m count=64 2>/dev/null
    ssh-keygen -q -t ed25519 -N "" -C hidfi-win-vm -f "$S/id_ed25519"
    # A random password for the VM's local account, kept with the VM only.
    python3 -c "import secrets; print(secrets.token_urlsafe(18))" > "$S/password"; chmod 600 "$S/password"
    disc="$VM/setup-disc"; rm -rf "$disc"; mkdir -p "$disc"
    sed "s/@PASSWORD@/$(cat "$S/password")/g" "$here/autounattend.xml" > "$disc/autounattend.xml"
    cp "$here/setup.ps1" "$msi" "$disc/"; cp "$S/id_ed25519.pub" "$disc/authorized_keys"
    hdiutil makehybrid -quiet -iso -joliet -default-volume-name HIDFISETUP -o "$VM/setup.iso" "$disc"
    rm -rf "$disc"
    cd_drive win "$ISO/Win11_arm64_en-us.iso"; cd_drive drv "$ISO/virtio-win.iso"; cd_drive cfg "$VM/setup.iso"
    qemu "${CDS[@]}" 2>"$VM/qemu.log"
    # "Press any key to boot from CD or DVD...": press it.
    # Only while that prompt can be up (a few seconds): later, Enter lands on
    # Setup's Cancel button.
    for _ in $(seq 1 8); do sleep 1; monitor "sendkey ret" >/dev/null 2>&1 || true; done
    echo "installing Windows unattended (watch: vm.sh shot, or VNC 127.0.0.1:5905) ..."
    if wait_ssh 5400; then
      until ssh_vm "Test-Path C:\\hidfi-setup-done.txt" 2>/dev/null | grep -q True; do sleep 10; done
      echo "Windows is installed; SSH works: vm.sh ssh"
    else
      die "no SSH after 90 min (vm.sh shot to see why)"
    fi
    ;;
  up)
    running && { echo "already running"; exit 0; }
    [ -f "$VM/disk.qcow2" ] || die "no VM yet: vm.sh create"
    qemu 2>"$VM/qemu.log"
    wait_ssh 600 || die "no SSH after 10 min (vm.sh shot)"
    echo "up: vm.sh ssh"
    ;;
  down)
    running || { echo "not running"; exit 0; }
    ssh_vm "Stop-Computer -Force" >/dev/null 2>&1 || monitor system_powerdown >/dev/null
    for _ in $(seq 1 60); do running || { echo "stopped"; exit 0; }; sleep 2; done
    monitor quit >/dev/null 2>&1 || true; echo "stopped (forced)"
    ;;
  status)
    if running; then echo "running (pid $(cat "$S/qemu.pid"))"; ssh_vm "exit 0" >/dev/null 2>&1 && echo "ssh: ok" || echo "ssh: not yet"; else echo "stopped"; fi
    ;;
  ssh) shift; ssh_vm "$@" ;;
  provision)
    # x64 Python (Windows runs it emulated): `cryptography` has no ARM64
    # Windows wheel and would need Microsoft's compiler to build.
    for f in python-amd64.exe chrome-arm64.msi; do [ -f "$ISO/$f" ] || die "missing $ISO/$f"; done
    ssh_vm "New-Item -ItemType Directory -Force C:/hidfi/setup | Out-Null"
    scp_vm "$ISO/python-amd64.exe" "$ISO/chrome-arm64.msi" "C:/hidfi/setup/"
    ssh_vm "Start-Process C:/hidfi/setup/python-amd64.exe -Wait -ArgumentList '/quiet','InstallAllUsers=1','PrependPath=0','Include_test=0','TargetDir=C:\Python312x64';
            Start-Process msiexec.exe -Wait -ArgumentList '/i','C:\hidfi\setup\chrome-arm64.msi','/qn';
            & C:/Python312x64/python.exe -m pip install --quiet --timeout 60 playwright cryptography; exit 0"
    printf 'import playwright, cryptography, os\nprint("tools ok" if os.path.exists(r"C:/Program Files/Google/Chrome/Application/chrome.exe") else "no Chrome")\n' > "$S/check.py"
    scp_vm "$S/check.py" "C:/hidfi/"; ssh_vm "& C:/Python312x64/python.exe C:/hidfi/check.py"
    ;;
  sync)
    arch="${2:-x64}"; case "$arch" in x64) tgt=x86_64-pc-windows-msvc ;; arm64) tgt=aarch64-pc-windows-msvc ;; *) die "sync x64|arm64" ;; esac
    rel="${HIDFI_WIN_TARGET_DIR:-/Volumes/HIDFi-WinBuild/target}/$tgt/release"
    [ -f "$rel/hidfi-companion.exe" ] || die "no Windows build in $rel (see companion/tests/windows/README.md)"
    repo="$(cd "$here/../../.." && pwd)"
    # Stop anything running from there first (an app started from C:\hidfi
    # registers itself to start at login, and a running .exe cannot be replaced).
    # A test copy must not start at login either (it re-points that entry to
    # itself when it runs, and a restart then locked it before the next sync).
    ssh_vm "\$run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run';
            if ((Get-ItemProperty \$run).'HID-Fi Companion' -like '*C:\hidfi\*') { Remove-ItemProperty \$run -Name 'HID-Fi Companion' };
            Get-Process | Where-Object { \$_.Path -like 'C:\hidfi\*' } | Stop-Process -Force -ErrorAction SilentlyContinue; Start-Sleep 1;
            Remove-Item -Recurse -Force C:/hidfi/companion -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Force C:/hidfi/companion/target/release/examples, C:/hidfi/companion/package | Out-Null"
    scp_vm "$repo/companion/tests" "$repo/companion/web" "$repo/companion/vectors" "C:/hidfi/companion/"
    scp_vm "$repo/companion/package/install-windows.ps1" "C:/hidfi/companion/package/"
    scp_vm "$rel/hidfi-companion.exe" "C:/hidfi/companion/target/release/"
    scp_vm "$rel/examples/serve.exe" "$rel/examples/system_app.exe" "$rel/examples/check_capture.exe" "$rel/examples/hw_app.exe" "C:/hidfi/companion/target/release/examples/"
    echo "synced the $arch build to C:\\hidfi\\companion"
    ;;
  desktop)
    shift; cmd="$*"; secs="${HIDFI_WIN_DESKTOP_SECS:-900}"
    # A one-off scheduled task in the logged-on (auto-logon) session: the script
    # goes over as a file (no quoting through ssh), and the wait has an end.
    tmp="$(mktemp)"
    printf '%s\n' '$ErrorActionPreference = "Continue"' "& { $cmd } *> C:\\hidfi\\desktop.out" \
      'New-Item C:\hidfi\desktop.done -Force | Out-Null' > "$tmp"
    ssh_vm "New-Item -ItemType Directory -Force C:/hidfi | Out-Null; Remove-Item C:/hidfi/desktop.done, C:/hidfi/desktop.out -ErrorAction SilentlyContinue; exit 0"
    scp_vm "$tmp" "C:/hidfi/desktop.ps1"; rm -f "$tmp"
    ssh_vm "\$a = New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\hidfi\desktop.ps1';
      \$p = New-ScheduledTaskPrincipal -UserId hidfi -LogonType Interactive -RunLevel Highest;
      Register-ScheduledTask -TaskName hidfi-desktop -Action \$a -Principal \$p -Force | Out-Null;
      Start-ScheduledTask -TaskName hidfi-desktop;
      \$end = (Get-Date).AddSeconds($secs);
      while (-not (Test-Path C:/hidfi/desktop.done) -and (Get-Date) -lt \$end) { Start-Sleep 1 };
      Get-Content C:/hidfi/desktop.out -ErrorAction SilentlyContinue;
      if (-not (Test-Path C:/hidfi/desktop.done)) { 'vm.sh: still running after $secs s'; exit 1 }"
    ;;
  shot)
    out="${2:?vm.sh shot out.png}"
    case "$out" in /*) ;; *) out="$PWD/$out" ;; esac
    monitor "screendump $S/shot.png -f png" >/dev/null
    for _ in 1 2 3 4 5; do [ -s "$S/shot.png" ] && break; sleep 1; done
    mv "$S/shot.png" "$out" && echo "$out"
    ;;
  *) sed -n '2,25p' "$0"; exit 2 ;;
esac
