# Windows, tested from a Mac

Windows builds and a Windows 11 VM on an Apple-silicon Mac, so HID-Fi Companion is
tested on Windows without a Windows PC. The VM runs Windows 11 for ARM; it runs
the x64 release through Windows' own x64 emulation, and an ARM64 build natively.
Everything big lives on an external drive (`HIDFI_WIN_VM_HOME`); only a few
private files stay on the Mac (`~/.hidfi-win-vm`: the VM's SSH key, password and
control socket).

What it found that CI on GitHub's Windows runners never did:

- **v4.0 did not start on a clean Windows** ("VCRUNTIME140_1.dll was not
  found"). GitHub's runners have Visual C++ installed; a fresh Windows does not.
  The C runtime is now linked in (`companion/.cargo/config.toml`), and CI checks
  every Windows `.exe` needs no C runtime DLL.
- **At 1920x1080 the Windows encoder ignored its bitrate**: 27-37 Mbit/s for
  busy content whatever it was asked (`examples/rate_check`). The rate control
  mode has to be set before the output type. CI now runs `rate_check --check`.
- **...and lost rate control for good when its target moved often** (as the app's
  does with a WiFi estimate): its rate buffer was smaller than one busy 1080p
  frame. `RATE_JITTER=1 rate_check --check` covers it; the buffer has a floor.
- **One encoding thread did every monitor**: with software encoding, two
  monitors shared one core. Each watched display now has its own thread.
- **The test streamer crashed when started over SSH** (Rust aborts on an
  asynchronous stdin; found from the crash offset and the PDB): tests now give
  it its own stdin.
- **Windows asks for the firewall again on a Public network**, which is what
  Windows calls a network it has just joined (`docs/COMPANION.md`).

## Build the Windows app on the Mac

```bash
cargo install --locked xwin
export HIDFI_WIN_VM_HOME="/Volumes/Extreme SSD/HIDFi-Windows"
xwin --accept-license --arch x86_64,aarch64 --cache-dir "$HIDFI_WIN_VM_HOME/xwin-cache" \
     splat --output "$HIDFI_WIN_VM_HOME/xwin"            # Microsoft's CRT + SDK, ~7 GB, once
companion/tests/windows/build.sh both                 # x64 + ARM64, ~1 min each
```

`xwin` asks you to accept Microsoft's licence for those libraries. The output
goes to `HIDFI_WIN_TARGET_DIR` (default `/Volumes/HIDFi-WinBuild/target`, an APFS
disk image): **not on exFAT** - the linker writes through memory-mapped files and
on an exFAT drive some `.exe` files came out as zeros. Make the image once:

```bash
hdiutil create -size 60g -type SPARSEBUNDLE -fs APFS -volname HIDFi-WinBuild \
        "/Volumes/Extreme SSD/HIDFi-WinBuild.sparsebundle"
hdiutil attach -nobrowse "/Volumes/Extreme SSD/HIDFi-WinBuild.sparsebundle"
```

`build.sh` checks every `.exe` it makes: a real PE file, no C runtime DLL.

## The VM

Needs `brew install qemu` (~0.7 GB on the Mac) and, in `$HIDFI_WIN_VM_HOME/iso`:

| File | From |
|---|---|
| `Win11_arm64_en-us.iso` | microsoft.com/software-download/windows11arm64 (Windows 11, multi-edition ISO for Arm64) |
| `virtio-win.iso` | fedorapeople.org/groups/virt/virtio-win (network and display drivers) |
| `OpenSSH-ARM64-*.msi` | github.com/PowerShell/Win32-OpenSSH releases |
| `python-amd64.exe`, `chrome-arm64.msi` | python.org (3.12, x64), Google (Chrome for Arm64, enterprise MSI) |

```bash
companion/tests/windows/vm.sh create      # unattended install, ~30-60 min, once
companion/tests/windows/vm.sh provision   # Python, Chrome, Playwright, once
companion/tests/windows/vm.sh sync x64    # the tests and a build into C:\hidfi
companion/tests/windows/vm.sh ssh 'cd C:/hidfi/companion; & C:/Python312x64/python.exe tests/e2e_view.py'
companion/tests/windows/vm.sh desktop 'cd C:/hidfi/companion; & C:/Python312x64/python.exe tests/overload.py'
companion/tests/windows/vm.sh shot out.png   # its screen
companion/tests/windows/vm.sh down
```

- Windows is installed by `autounattend.xml` (the TPM, Secure Boot and online
  account checks skipped - a VM has none of them) with one local admin that logs
  on by itself; `setup.ps1` adds OpenSSH with key-only login. Windows is not
  activated: fine for a test machine.
- **Run heavy tests with `desktop`, not `ssh`.** `desktop` runs in the logged-on
  session, as the app does for a user; screen capture only works there. Many
  Chrome processes started over SSH once left SSH unable to start a session
  until the VM restarted.
- Python is x64 (emulated): `cryptography` has no ARM64 Windows wheel.
- The VM has no GPU: Windows encodes in software, under x64 emulation for the
  x64 build. That is the slow end of real Windows PCs - frame rates here are a
  floor, not what a laptop with a GPU encoder gets.
