#!/bin/sh
# HID-Fi Companion for Linux: install, update or remove, for this user only.
#
#   curl -fsSL https://raw.githubusercontent.com/san-gitlogin/HID-Fi/main/companion/package/install-linux.sh | sh
#   sh install-linux.sh --uninstall
#   sh install-linux.sh --from ./hidfi-companion      (a binary you built yourself)
#
# No sudo. Puts the app in ~/.local/bin, Cisco's OpenH264 in
# ~/.local/share/hidfi-companion, a menu entry in ~/.local/share/applications,
# and starts it. The app adds its own "start at login" entry (you can turn it
# off from its tray menu). --uninstall removes all of it.
set -eu

REPO="san-gitlogin/HID-Fi"
BIN="${HOME}/.local/bin/hidfi-companion"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/hidfi-companion"
MENU="${XDG_DATA_HOME:-$HOME/.local/share}/applications/hidfi-companion.desktop"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/hidfi-companion.desktop"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/hidfi-companion"

say() { printf '%s\n' "$*"; }
die() { printf 'HID-Fi Companion: %s\n' "$*" >&2; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
  pkill -x hidfi-companion 2>/dev/null || true
  rm -f "$BIN" "$MENU" "$AUTOSTART"
  rm -rf "$DATA"
  say "Removed HID-Fi Companion."
  say "Its settings and pairing are in $CONFIG - delete that folder too to forget the pairing,"
  say "and remove this computer on the dashboard's Screen tab."
  exit 0
fi

case "$(uname -m)" in
  x86_64|amd64) arch=x86_64 ;;
  aarch64|arm64) arch=aarch64 ;;
  *) die "no build for $(uname -m) yet" ;;
esac
command -v curl >/dev/null || die "needs curl (sudo apt install curl, or your distribution's equivalent)"
command -v bunzip2 >/dev/null || die "needs bunzip2 (sudo apt install bzip2)"

mkdir -p "$(dirname "$BIN")" "$DATA" "$(dirname "$MENU")"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

if [ "${1:-}" = "--from" ]; then
  [ -f "${2:-}" ] || die "--from needs the path to a hidfi-companion binary"
  cp "$2" "$tmp/hidfi-companion"
else
  url="https://github.com/$REPO/releases/latest/download/hidfi-companion-linux-$arch"
  say "Downloading HID-Fi Companion ($arch)..."
  curl -fsSL --retry 3 -o "$tmp/hidfi-companion" "$url" || die "could not download $url"
  sums="https://github.com/$REPO/releases/latest/download/SHA256SUMS"
  if curl -fsSL --retry 3 -o "$tmp/SHA256SUMS" "$sums" 2>/dev/null; then
    want="$(grep " hidfi-companion-linux-$arch\$" "$tmp/SHA256SUMS" | cut -d' ' -f1)"
    got="$(sha256sum "$tmp/hidfi-companion" | cut -d' ' -f1)"
    [ -n "$want" ] && [ "$want" = "$got" ] || die "the download does not match the release's checksum - not installed"
  fi
fi

say "Downloading OpenH264 from Cisco (H.264 encoding)..."
here="$(cd "$(dirname "$0")" 2>/dev/null && pwd || echo .)"
if [ -x "$here/fetch-openh264.sh" ]; then
  "$here/fetch-openh264.sh" "$DATA" >/dev/null
else
  curl -fsSL "https://raw.githubusercontent.com/$REPO/main/companion/package/fetch-openh264.sh" -o "$tmp/fetch.sh"
  sh "$tmp/fetch.sh" "$DATA" >/dev/null
fi

pkill -x hidfi-companion 2>/dev/null || true   # an update replaces a running copy
for _ in $(seq 1 30); do pgrep -x hidfi-companion >/dev/null || break; sleep 0.5; done   # wait until it is gone
install -m 0755 "$tmp/hidfi-companion" "$BIN"
cat > "$MENU" <<DESKTOP
[Desktop Entry]
Type=Application
Name=HID-Fi Companion
Comment=Shows this computer's screen on the HID-Fi dashboard
Exec="$BIN"
Terminal=false
Categories=Utility;Network;
DESKTOP

say ""
say "Installed $("$BIN" --version 2>/dev/null || echo HID-Fi Companion): $BIN"
if [ "${XDG_SESSION_TYPE:-}" = "wayland" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
  say "NOTE: this is a Wayland session. Screen sharing works on X11 (Xorg) sessions for now:"
  say "      at the login screen choose \"... on Xorg\", or use an X11 desktop."
fi
case "${XDG_CURRENT_DESKTOP:-}" in
  *GNOME*) say "NOTE: GNOME shows tray icons only with the AppIndicator extension"
           say "      (Ubuntu and Fedora include it; elsewhere install \"AppIndicator and KStatusNotifierItem Support\")." ;;
esac
nohup "$BIN" >/dev/null 2>&1 &
say "Started. Look for the HID-Fi icon in your tray, then open the dashboard's Screen tab and tap Pair."
