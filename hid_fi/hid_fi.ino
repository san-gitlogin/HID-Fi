/*
  ============================================================================
  HID-Fi v3.8 — USB HID trackpad + remote + WiFi dashboard

  Board:  ESP32-S3-N16R8 (YD-ESP32-S3, 16MB Flash, 8MB PSRAM)

  Command inputs (same JSON protocol, same HID output):
    1. Serial (primary)      — UART0 via CH343 COM port. Never PIN-gated.
    2. WebSocket :81         — low-latency channel used by the dashboard.
                               Binary opcodes for pointer, JSON text for the rest.
    3. HTTP :80 /api/command — unchanged from v3.1, kept as a fallback.

  Why the WebSocket exists: this core's WebServer has no HTTP keep-alive, so a
  POST per pointer event paid a full TCP handshake, a header parse and a serial
  echo of the reply. That was the cursor lag. Binary frames on a persistent
  socket skip all three.

  Wiring: BOTH USB-C ports connect to the same PC
    "COM" port  → CH343 UART → JSON commands (COMx)
    "USB" port  → Native USB OTG → HID keyboard + mouse + media + system

  *** IMPORTANT HARDWARE STEPS ***
  1. Bridge the "USB-OTG" pads on the underside of the board!
  2. Disconnect the "USB" port when flashing via the "COM" port!

  Arduino IDE / arduino-cli settings (CRITICAL):
    Board:       ESP32S3 Dev Module
    USB Mode:    USB-OTG (TinyUSB)       ← REQUIRED for HID
    USB CDC On Boot: Disabled            ← firmware uses UART0 via CH343
    Flash 16MB · PSRAM OPI · Partition huge_app · Upload 921600

  Extra libraries: ArduinoJson, WebSockets (Links2004 arduinoWebSockets).

  Dashboard: connect to AP "ESP32-HID-XXXXXX" / "hid12345" → http://192.168.4.1/
             or http://esp-hid-xxxxxx.local/ once mDNS resolves.

  Serial / HTTP / WS JSON protocol (newline-delimited on serial):
    Unlock:   {"cmd":"unlock","password":"...","ctrl_alt_del":true}
    Lock:     {"cmd":"lock"} / {"cmd":"set_lock_state","state":"locked"}
    Keys:     {"cmd":"type","text":"...","enter":true} / {"cmd":"press","keys":"CTRL+ALT+DELETE"}
    Mouse:    {"cmd":"mouse_move","dx":10,"dy":0,"wheel":0,"pan":0}
              {"cmd":"mouse_click","button":"left|right|middle","count":1,"modifiers":"CTRL"}
              {"cmd":"mouse_press"|"mouse_release","button":"left"}
              {"cmd":"mouse_scroll","amount":-3,"pan":0}
              {"cmd":"mouse_abs","x":16384,"y":16384}      (absolute mode only)
    Wiggle:   {"cmd":"wiggle","mode":"single|interval|timed",...} / {"cmd":"wiggle_stop"}
    Media:    {"cmd":"media","key":"volume_up|mute|play_pause|next|prev|brightness_up|..."}
    System:   {"cmd":"system","action":"sleep|wake|power_off"}
    Gesture:  {"cmd":"gesture","name":"task_view|desktop_left|zoom_in|..."}
    Present:  {"cmd":"presenter","action":"next|prev|start|end|black|white"}
    Macros:   {"cmd":"macro_save","slot":0,"name":"...","steps":["key CTRL+S","delay 200"]}
              {"cmd":"macro_run"|"macro_get"|"macro_delete","slot":0} / {"cmd":"macro_list"}
    Modes:    {"cmd":"pointer_mode","mode":"relative|absolute"}   (reboots)
              {"cmd":"gamepad_enable","on":true}                  (reboots)
              {"cmd":"gamepad","button":0,"pressed":true,"hat":0,"lx":0,"ly":0}
    Security: {"cmd":"set_auth","token":"1234"} / {"cmd":"auth","token":"1234"}
    LED:      {"cmd":"led_set"|"led_blink"|"led_status",...}
    WiFi:     {"cmd":"wifi_configure"|"wifi_status"|"wifi_disconnect"|"ap_configure",...}
    Misc:     ping, status, echo, test_hid, set_verbose, set_host_os

  WebSocket binary opcodes (little-endian), client -> board:
    0x01 MOVE [i16 dx][i16 dy][i8 wheel][i8 pan]
    0x02 BTN  [u8 mask][u8 action 0=release 1=press 2=click][u8 count]
    0x03 ABS  [u16 x][u16 y]        (0..32767, absolute mode)
    0x04 PING [u32 id]              -> 0x81 PONG [u32 id]
  ============================================================================
*/

#include <ArduinoJson.h>
#include "USB.h"
#include "USBHIDKeyboard.h"
#include "USBHIDMouse.h"
#include "USBHIDConsumerControl.h"
#include "USBHIDSystemControl.h"
#include "USBHIDGamepad.h"
#include <WiFi.h>
#include <WebServer.h>
#include <WebSocketsServer.h>
#include <ESPmDNS.h>
#include <Preferences.h>
// Station list and NVS statistics are not exposed by the Arduino layer, so these
// come straight from the IDF.
#include "esp_wifi.h"
#include "esp_netif.h"
#include "lwip/etharp.h"  // MAC -> IP for AP clients
#include "nvs.h"

// Dashboard markup lives in web_ui.h (defines WEB_INTERFACE[] in PROGMEM).
#include "web_ui.h"

// Declared here, above everything, because arduino-cli hoists generated function
// prototypes to the top of the file and several of them name this enum in their
// signature (hostOs() returns it, hostOsName() takes it). An enum declared
// mid-file is invisible to those prototypes -- the same reason WiggleMode and the
// other enums exist where they do. See the HOST OS DETECTION block for the rest.
enum HostOS : uint8_t { OS_UNKNOWN, OS_WINDOWS, OS_MAC, OS_LINUX };

// TinyUSB is linked into the core, but the Arduino USB layer never exposes these
// and never calls them. A suspended host ignores HID reports completely, so
// without remote wakeup nothing we send can rouse a sleeping PC - not a key, not
// a click, not the System Wake usage.
extern "C" bool tud_suspended(void);
extern "C" bool tud_remote_wakeup(void);
extern "C" bool tud_mounted(void);

// Returns true if the bus was suspended and a resume was requested.
//
// This sits on the pointer hot path, so it must not sleep. The previous version
// did -- delay(120) on every report once a host suspended the bus, which is the
// one thing loop() is never allowed to do. The resume completes on the wire
// asynchronously; losing the report that triggered it is invisible, a cursor
// that stalls for an eighth of a second is not. Rate limited so a host that
// never armed us as a wake source cannot turn every pointer event into a
// rejected control transfer. system/wake does its own blocking wait, because
// there the user is explicitly asking and has nothing to feel lag in.
static bool usbWakeHost() {
    if (!tud_suspended()) return false;
    static uint32_t lastWakeReq = 0;
    uint32_t now = millis();
    if (now - lastWakeReq < 500) return false;
    lastWakeReq = now;
    return tud_remote_wakeup();           // false if the host never armed us as a wake source
}

// ==================== SERIAL PORT ====================
HardwareSerial ComSerial(0);  // UART0 (TX=GPIO43, RX=GPIO44) → CH343 COM port
#define COM_SERIAL ComSerial

// ==================== USB HID ====================
// Composite HID: keyboard + ONE mouse + consumer control + system control,
// plus an optional gamepad. USBHIDMouseBase registers its report descriptor in
// its CONSTRUCTOR and a class-wide `static bool initialized` means only the
// first instance ever registers -- so exactly one mouse object may be created,
// which is why the mouse is heap-allocated in setup() from the saved mode.
USBHIDKeyboard         Keyboard;
USBHIDConsumerControl  Consumer;
USBHIDSystemControl    SystemCtl;
USBHIDRelativeMouse   *mouseRel = nullptr;
USBHIDAbsoluteMouse   *mouseAbs = nullptr;
USBHIDGamepad         *Gamepad  = nullptr;
bool hidReady = false;

bool    absoluteMode   = false;          // persisted; changing it reboots (USB re-enumerates)
bool    gamepadEnabled = false;          // persisted; changing it reboots
int32_t absX = 16384, absY = 16384;      // virtual cursor for absolute mode, 0..32767

// ==================== WiFi + WEB SERVER ====================
#define WS_MAX_CLIENTS 8
WebServer        webServer(80);
WebSocketsServer webSocket(81);
String  mdnsName  = "";
String  authToken = "";                  // empty = no PIN; WiFi clients unrestricted
// Wrong-PIN lockout is kept PER DEVICE (by IP address), so a stranger guessing
// PINs locks out only themselves, not the owner. A budget across the whole home
// network stops the same guessing from being spread over many addresses; the
// board's own access point and the USB cable are never subject to it.
struct PinGuard { uint32_t ip; uint8_t fails; uint32_t lockUntil; uint32_t lastAt; };
#define PIN_GUARDS        8
#define LAN_FAIL_BUDGET   20                 // wrong PINs from the home network...
#define LAN_FAIL_WINDOW   600000UL           // ...within ten minutes...
#define LAN_LOCK_MS       600000UL           // ...lock the home network out for ten
PinGuard pinGuards[PIN_GUARDS];
uint16_t lanFails       = 0;
uint32_t lanWindowStart = 0;
uint32_t lanLockUntil   = 0;
IPAddress pinPeer;                           // who is asking, set before every PIN check
// A socket that has not signed in by this deadline is closed, so connections
// that never authenticate cannot hold every slot and keep the owner out.
#define WS_AUTH_DEADLINE_MS 20000UL
// Vault state lives up here because set_auth and storage_info both consult it,
// and those run well before the vault's own section. See PASSWORD VAULT.
#define VAULT_SLOTS      12
#define VAULT_LABEL_MAX  48
#define VAULT_SECRET_MAX 128
enum VaultKind : uint8_t { VK_PASSWORD = 0, VK_PIN = 1 };
String   vaultPinCode  = "";             // empty = the access PIN guards the vault
uint8_t  vaultFails    = 0;
uint32_t vaultLockUntil = 0;
bool    wsAuthed[WS_MAX_CLIENTS];
// Reached over the home network rather than the board's own access point. Set
// once per connection - the client's address cannot change while it is open -
// so the binary hot path pays for a bool, never an address lookup.
bool    wsFromLan[WS_MAX_CLIENTS];
uint32_t wsOpenedAt[WS_MAX_CLIENTS];
unsigned long pendingRestartAt   = 0;    // deferred reboot so the reply can flush first
unsigned long consumerReleaseAt  = 0;    // deferred media-key release, keeps the socket free
Preferences preferences;

// Where the command being handled came from. Decides where the reply goes and
// whether it is echoed to the UART -- echoing a ~70 byte JSON line at 115200
// costs ~6 ms, which is far too much on the pointer path.
enum CmdSource { SRC_SERIAL = 0, SRC_HTTP = 1, SRC_WS = 2 };
CmdSource cmdSource   = SRC_SERIAL;
int       wsClientNum = -1;
bool      verboseLog  = false;           // true = echo every source to serial, as v3.3 did
bool wifiConnected = false;    // STA connected to external network
bool wifiEnabled = false;       // STA attempted / in use
String wifiIP = "";             // STA IP
String apIP = "192.168.4.1";   // AP IP (always this)
String apSSID = "ESP32-HID";   // AP SSID (configurable)
String apPassword = "hid12345"; // AP password (min 8 chars for WPA2); replaced at first start
bool   apPassGenerated = false;  // still the password this board made for itself
String apGenPassword;            // the one it made, so that is a fact, not a flag
unsigned long lastWifiCheck = 0;
#define WIFI_CHECK_INTERVAL 10000   // check connection every 10s
#define WIFI_CONNECT_TIMEOUT 15000  // 15s connection timeout

// ---- STA join state machine -------------------------------------------------
// Joining a network can take 15 seconds to resolve. Doing that in a while-loop
// blocks loop(), and loop() is what services the web server, the WebSocket
// heartbeat and the pointer stream. Blocking there makes the dashboard freeze at
// the exact moment the user is watching it for feedback, and can drop the socket
// outright.
//
// So the join is a state machine ticked from loop(). handleWifiConfigure()
// returns immediately with "connecting", and the dashboard polls wifi_status to
// watch it progress. Nothing blocks.
enum StaPhase : uint8_t {
    STA_IDLE,        // nothing in progress
    STA_CONNECTING,  // association under way
    STA_ONLINE,      // associated and holding an IP
    STA_FAILED,      // gave up - staError says why
    STA_RECONNECTING // a network that has worked before is gone; retrying until it is back
};
StaPhase staPhase   = STA_IDLE;
uint32_t staPhaseAt = 0;          // millis() when this phase started
String   staError   = "";         // human readable reason for STA_FAILED
String   staSsid    = "";         // network we are joining or joined

// Requested static addressing. Empty strings mean "use DHCP".
String staWantIp   = "";
String staWantGw   = "";
String staWantMask = "";
String staWantDns  = "";

// How we actually ended up addressed, which is not always what was asked for:
//   dhcp          - router assigned it
//   static        - we asked for a fixed address and got exactly that
//   dhcp_fallback - a static address was asked for, did not work, DHCP rescued us
String staIpMode      = "dhcp";
bool   staStaticTried = false;    // a static attempt is in flight, fallback still possible

// ---- Keeping the home network -------------------------------------------------
// Routers reboot, kick clients, and hand out a different address after a power
// cut. The core library retries some of that by itself and not the rest - and a
// join that timed out (a board that booted before the router did) ended with
// disconnect(), which it reads as "stay off". So one supervisor owns it: while a
// network is saved and we are not on it, try again - at once, then backing off
// to every 30 s - forever for a network that has worked before. The core's own
// auto-reconnect is switched off so the two never fight over the radio.
#define STA_RETRY_FIRST     1000     // first retry after a drop
#define STA_RETRY_MAX       30000    // back-off ceiling for a network that has worked
#define STA_RETRY_BADPASS   300000   // a worked-before network now refusing the password
#define AP_FALLBACK_MS      20000    // home network gone this long: bring the AP back
String   staSavedSsid = "", staSavedPass = "";   // the network being joined: one row of the table below
// Saved networks, like a phone's: up to NET_MAX, most recently used first. The
// supervisor joins the strongest one in range. Stored in wifi_cfg as n0..n4
// (JSON); the old single-network keys (ssid, pass, ip... sta_ok) keep mirroring
// row 0, so a downgrade to v3.10 still finds its network.
#define NET_MAX 5
String   netSsid[NET_MAX], netPass[NET_MAX], netIp[NET_MAX], netGw[NET_MAX], netMask[NET_MAX], netDns[NET_MAX];
bool     netOk[NET_MAX];              // has been online with these credentials
bool     netBad[NET_MAX];             // the router refused this new password: skip until re-entered
uint32_t netCoolUntil[NET_MAX];       // a worked-before network refusing its password: rest until then
uint8_t  netCount = 0;
int8_t   netCurrent = -1;             // the row being joined or joined
bool     staScanPending = false;      // the supervisor started a scan and is waiting for it
bool     staAway = false;             // the last scan found no saved network in range
uint32_t staScans = 0;
uint8_t  staBlindRound = 0;           // every few rounds, try a proven network unseen (hidden SSIDs)
#define  STA_AWAY_GAP_SOON 30000      // no saved network in range: scan again in 30 s...
#define  STA_AWAY_GAP_LATE 120000     // ...and every 2 minutes once away for 10, to keep the AP smooth
uint32_t staRetryAt   = 0;
uint32_t staRetryGap  = STA_RETRY_FIRST;
bool     staSupervised = false;   // the join in flight was started by the supervisor or at boot
bool     staProven     = false;   // these credentials have been online at least once
uint32_t staDrops      = 0;       // times the home network went away since boot
uint32_t staTries      = 0;       // rejoin attempts since boot
uint8_t  staAuthFails  = 0;       // consecutive refusals of the password
uint32_t staDownSince  = 0;       // millis() it went away; 0 while online or never joined
uint32_t staOnlineSince = 0;
String   staLastIp     = "";      // the last address we held - kept while offline, and across reboots
String   staPrevIp     = "";      // the one before it, once it changes
uint32_t staIpChanges  = 0;
volatile uint8_t staLastReason = 0;   // 802.11 reason the router gave for letting us go
// Test fault (serial only, net_fault): until this time the network "vanishes" -
// the supervisor joins a name that does not exist, which is exactly what a
// powered-off router looks like. The boot variant survives the restart in RTC RAM.
uint32_t netFaultUntil = 0;
String   netFaultSsid = "";           // vanish one network only (empty: all of them)
// Sharing the home internet with phones on the board's own WiFi (NAPT). Off by
// default: it lets anything on the board's WiFi reach the home network, and the
// traffic shares the radio with the pointer. Active only while both are up.
bool apShare = false;
bool apShareActive = false;
RTC_NOINIT_ATTR uint32_t rtcFaultMagic;
RTC_NOINIT_ATTR uint32_t rtcFaultSecs;
#define NET_FAULT_MAGIC 0x4E46A17Cu
#define NET_FAULT_SSID  "hidfi-test-no-such-network"

// ---- Power ------------------------------------------------------------------
// Nearly all of this board's draw is the radio, not the processor: roughly
// 100-120 mA with WiFi awake against 30-40 mA with modem sleep on. CPU frequency
// scaling is deliberately NOT used, even though it looks like free savings -- it
// changes the clock the UART is derived from, and the UART is the link the PC
// service depends on. Saving 15 mA is not worth risking that.
//
// The tension is real and cannot be designed away. Modem sleep parks the radio
// between router beacons, which puts tens of milliseconds of jitter on the one
// path that has to feel instant. So instead of making the user choose once and
// live with it, BALANCED decides per moment: awake while someone is actually
// driving the pointer, asleep once nobody is. Any packet wakes it inside a
// single pass of loop().
enum PowerMode : uint8_t {
    PWR_PERFORMANCE,  // radio always awake - best feel, highest draw
    PWR_BALANCED,     // awake while in use, sleeps when idle  (default)
    PWR_SAVER         // radio sleep always on - lowest draw, jitter you can feel
};
PowerMode powerMode   = PWR_BALANCED;
bool      radioAwake  = true;      // what the driver was last told
uint32_t  lastInputAt = 0;         // millis() of the last client packet
#define   POWER_IDLE_MS 45000      // quiet for this long -> let the radio sleep

// The access point costs power even with nobody on it, because an AP has to keep
// beaconing whether or not anyone is listening. Shutting it down once the board
// is reachable over the home network is the only change here that saves a large
// amount, and it shrinks the attack surface at the same time. Opt-in, because it
// removes a way in. Holding BOOT for two seconds always brings it back.
bool     apAutoOff   = false;
bool     apRunning   = true;
uint32_t apIdleSince = 0;
uint32_t pendingApRestartAt = 0;   // deferred so an ap_configure reply can flush
#define  AP_IDLE_MS 600000         // 10 minutes with no AP client

// ---- Health -----------------------------------------------------------------
// A remote control that quietly runs out of memory looks exactly like bad WiFi,
// so the number worth reporting is the worst the heap has ever been, not what it
// is right now -- the current value recovers and hides a leak that already
// happened. The IDF allocator maintains that low-water mark itself, exactly, so
// it is read rather than sampled. Sampling would miss the short spikes that are
// precisely the thing being looked for.
bool heapWarned = false;
#define  HEAP_WARN 40000           // below this, say so rather than misbehave

// A WebSocket text frame is a JSON command, and commands are small. Anything
// much larger is a bug or an attempt to exhaust the heap.
#define  WS_TEXT_MAX  2048
#define  SERIAL_LINE_MAX 2048
// One definition. The boot event and the status reply both send it, and two
// literals would eventually disagree about what is running.
#define  FW_VERSION "hid_fi_v3.11"

// ==================== HARDWARE PINS ====================
#define BOOT_BUTTON    0    // GPIO0 — BOOT button
#define RGB_LED_PIN    48   // GPIO48 — WS2812 RGB LED

// ==================== STATE ====================
unsigned long commandCount = 0;
unsigned long wifiCommandCount = 0;

// ==================== MOUSE WIGGLE STATE (Phase 3.2) ====================
// Declared here (not at the appended block) so status handlers defined earlier
// in the file can reference them. In Arduino .ino only function prototypes are
// auto-hoisted — globals and enums are not.
enum WiggleMode { WIGGLE_NONE, WIGGLE_INTERVAL, WIGGLE_TIMED };
WiggleMode    wiggleMode      = WIGGLE_NONE;
int           wiggleAmplitude = 5;      // pixels per half-jiggle (net-zero)
unsigned long wiggleInterval  = 1000;   // ms between jiggles
unsigned long wiggleLastMove  = 0;
unsigned long wiggleStartMs   = 0;
unsigned long wiggleEndMs     = 0;      // TIMED mode only
unsigned long wiggleCount     = 0;

// ==================== LED STATE (WS2812 RGB on GPIO48) ====================
uint8_t ledR = 0, ledG = 0, ledB = 0;
bool    ledOn = false;
int           ledBlinkRemaining = 0;    // remaining ON phases
bool          ledBlinkPhaseOn   = false;
unsigned long ledBlinkLast      = 0;
int           ledBlinkOnMs       = 200;
int           ledBlinkOffMs      = 200;
uint8_t       ledBlinkR = 0, ledBlinkG = 0, ledBlinkB = 0;

// ==================== PC LOCK STATE TRACKING ====================
enum PCLockState { PC_STATE_UNKNOWN, PC_STATE_LOCKED, PC_STATE_UNLOCKED };
PCLockState pcLockState = PC_STATE_UNKNOWN;

const char* lockStateStr() {
    switch (pcLockState) {
        case PC_STATE_LOCKED:   return "locked";
        case PC_STATE_UNLOCKED: return "unlocked";
        default:                return "unknown";
    }
}

// ==================== HOST OS DETECTION ====================
// Which computer the board is plugged into steers the lock shortcut, the gesture
// combos, the app-switch modifier and the keyboard layout, so getting it wrong is
// immediately visible to whoever is holding the phone.
//
// It used to be probed by tapping Num Lock and timing the host's LED reply. That
// was wrong in three ways, all of them measured on real hardware: it changed
// state on the user's PC, the "no reply" branch never undid the toggle it had
// just caused, and an 800 ms window turned any slow host into a Mac. The same
// board against one Windows laptop resolved in 5 s on one boot and was still
// undecided 9 s into the next.
//
// Detection is now passive. A host identifies itself during enumeration without
// being asked anything, by the shape of its own descriptor reads:
//
//   String reads  the discriminator. macOS reads a string descriptor twice -
//                 two bytes first to learn its length, then the whole thing -
//                 so the same index arrives back to back. Windows and Linux ask
//                 once with a 255 byte read and never repeat an index. This is
//                 the signal the keyboardio FingerprintUSBHost work found and
//                 QMK's os_detection ships; there it is read off the setup
//                 packet's wLength, which the Arduino layer does not expose, so
//                 the back-to-back repeat is used as the same evidence.
//   SET_IDLE      NOT a discriminator. v3.7 assumed only Windows and Linux sent
//                 it; measured on a real Mac, macOS sends it too. Still counted
//                 and still reported, because it is evidence a host is there.
//   SET_PROTOCOL  every host sends it, so it only means "a host is enumerating
//                 us" and is what opens the decision window.
//   LED report    an unsolicited lock-key report. Windows sends one at
//                 enumeration to sync the lock LEDs, so it corroborates a PC,
//                 but it is corroboration only - macOS sends one whenever Caps
//                 Lock changes.
//
// Nothing is typed, so there is nothing to undo, and it re-arms whenever the bus
// drops - which is what makes carrying the board from a Mac to a PC work without
// a reboot.
//
// Deliberately NOT used: the MS OS string descriptor at index 0xEE, which only
// Windows requests. Windows caches that answer per VID/PID/bcdDevice under
// HKLM\SYSTEM\CurrentControlSet\Control\usbflags and never asks again. Verified
// on the development PC, where this board was already recorded as osvc=00 00. It
// would fire once on a fresh machine and then make that same machine look like a
// Mac on every plug-in afterwards.
HostOS hostOsDetected = OS_UNKNOWN;   // what this enumeration implied
HostOS hostOsManual   = OS_UNKNOWN;   // user override; OS_UNKNOWN = trust detection
// Which host OSes the user has set up for Unicode typing, as bits (1 << HostOS).
// A keyboard sends key positions, never characters, so emoji and accents need
// the host's own hex-entry method - and that method types junk on a host that
// was not set up for it. Hence per OS, off until the user says it is ready.
uint8_t unicodeHosts = 0;

const char* hostOsName(HostOS o) {
    switch (o) {
        case OS_WINDOWS: return "windows";
        case OS_MAC:     return "mac";
        case OS_LINUX:   return "linux";
        default:         return "unknown";
    }
}

// All four counters are written only from the USB event task and read only from
// loop(); a plain volatile is enough for that one-writer/one-reader hand-off.
volatile uint32_t ledEventCount   = 0;
volatile uint8_t  ledStateBits    = 0;
volatile uint32_t usbSetIdleCount = 0;   // SET_IDLE only - the Mac-vs-PC signal
volatile uint32_t usbCtrlCount    = 0;   // any HID control request - "a host is there"

void keyboardLedCb(void* /*arg*/, esp_event_base_t /*base*/, int32_t /*id*/, void* data) {
    arduino_usb_hid_keyboard_event_data_t* d = (arduino_usb_hid_keyboard_event_data_t*)data;
    ledStateBits = d->leds;
    ledEventCount++;
}

// Declared in USB.cpp with C++ linkage and no public header, so it is forward
// declared here exactly as the core's own .cpp files do. The event loop it posts
// to is created in the ESPUSB constructor, which runs before setup().
esp_err_t arduino_usb_event_handler_register_with(esp_event_base_t event_base, int32_t event_id,
                                                  esp_event_handler_t event_handler, void* event_handler_arg);

void usbHidCtrlCb(void* /*arg*/, esp_event_base_t /*base*/, int32_t id, void* /*data*/) {
    if (id == ARDUINO_USB_HID_SET_IDLE_EVENT) usbSetIdleCount++;
    usbCtrlCount++;
}

// How the host reads our string descriptors, which is the one signal that tells
// macOS from Windows. The core defines this callback __attribute__((weak)) in
// esp32-hal-tinyusb.c, so this definition replaces it at link time.
//
// It has to rebuild the descriptor itself, because the core's string table is
// file-static and unreachable from here. The strings come straight back out of
// the USB object, which is the same place the core got them, so the board
// enumerates byte for byte as it did before - same manufacturer, same product,
// same MAC-derived serial, so the host does not see a new device.
volatile uint32_t usbStrReqCount    = 0;  // GET_DESCRIPTOR(String) requests
volatile uint32_t usbStrRereadCount = 0;  // ...that asked for the same index again
#define USB_STR_SEQ_LEN 16
volatile uint8_t  usbStrSeq[USB_STR_SEQ_LEN] = {0};  // the last few indices asked for
volatile uint8_t  usbStrSeqHead = 0;
extern "C" uint16_t const* tud_descriptor_string_cb(uint8_t index, uint16_t /*langid*/) {
    static uint16_t desc[127];
    static uint8_t  lastIndex = 0xFF;   // only ever touched from the USB task

    usbStrReqCount++;
    if (index == lastIndex) usbStrRereadCount++;
    lastIndex = index;
    // Kept so the raw fingerprint can be read off a phone. An enumeration is a
    // dozen requests at most, so the last sixteen are the whole of it.
    usbStrSeq[usbStrSeqHead] = index;
    usbStrSeqHead = (usbStrSeqHead + 1) % USB_STR_SEQ_LEN;

    uint8_t chrCount;
    if (index == 0) {
        desc[1] = 0x0409;               // LANGID: English (United States)
        chrCount = 1;
    } else {
        const char* str;
        switch (index) {
            case 1:  str = USB.manufacturerName(); break;
            case 2:  str = USB.productName();      break;
            case 3:  str = USB.serialNumber();     break;
            // Interface names the core adds for itself. Cosmetic, but they must
            // resolve: a stalled string request during enumeration is the kind
            // of thing a host holds against a device.
            default: str = "TinyUSB Device";       break;
        }
        if (!str) return NULL;
        size_t len = strlen(str);
        if (len > 126) len = 126;
        chrCount = (uint8_t)len;
        for (uint8_t i = 0; i < chrCount; i++) desc[1 + i] = str[i];
    }
    desc[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * chrCount + 2));
    return desc;
}

// Ticked from loop(), so nothing here blocks.
enum DetectPhase : uint8_t {
    DET_IDLE,      // no host enumerating; counters baselined, waiting
    DET_WATCHING,  // a host turned up, collecting signals
    DET_SETTLED    // verdict reached for this enumeration
};
DetectPhase detectPhase = DET_IDLE;
uint32_t detectAt       = 0;      // millis() the window opened
bool     detectMounted  = false;  // last tud_mounted(), to spot the bus dropping
uint32_t detectIdleBase = 0;      // counter values when this enumeration began
uint32_t detectCtrlBase = 0;
uint32_t detectLedBase  = 0;
uint32_t detectStrBase  = 0;
uint32_t detectRerdBase = 0;
#define DETECT_WINDOW_MS 3000     // enumeration chatter is over well inside this

// The OS the rest of the firmware acts on, and where it came from.
HostOS hostOs() { return hostOsManual != OS_UNKNOWN ? hostOsManual : hostOsDetected; }
const char* hostOsSource() {
    if (hostOsManual   != OS_UNKNOWN) return "manual";
    if (hostOsDetected != OS_UNKNOWN) return "auto";
    // A host that enumerated us but said nothing we recognise. Saying so lets the
    // dashboard ask, instead of spinning on "Detecting..." forever the way the
    // Num Lock probe used to when its reply never came.
    return (detectPhase == DET_SETTLED) ? "undetermined" : "pending";
}
// True when the effective OS is a Mac - the one branch nearly every mapping needs.
inline bool hostIsMac() { return hostOs() == OS_MAC; }

const char* detectPhaseStr() {
    switch (detectPhase) {
        case DET_WATCHING: return "watching";
        case DET_SETTLED:  return "settled";
        default:           return "idle";
    }
}

void announceHostOs() {
    StaticJsonDocument<192> doc;
    doc["event"]            = "host_os";
    doc["host_os"]          = hostOsName(hostOs());
    doc["host_os_source"]   = hostOsSource();
    // Sent even when a manual pin is in force, so the dashboard can point out
    // that the pinned OS and the computer actually attached disagree.
    doc["host_os_detected"] = hostOsName(hostOsDetected);
    String out;
    serializeJson(doc, out);
    // Push it so an open dashboard reskins immediately rather than on its next poll.
    if (wifiEnabled) webSocket.broadcastTXT(out);
    COM_SERIAL.println(out);
}

// Back to waiting for a host. Baselines the counters so only the next
// enumeration is judged, and leaves hostOsDetected alone on purpose: holding the
// last answer on screen until a new one is reached means a momentary bus glitch
// does not blank the UI, while a genuine move to another computer replaces it a
// few seconds after that computer enumerates us.
void detectArm() {
    detectIdleBase = usbSetIdleCount;
    detectCtrlBase = usbCtrlCount;
    detectLedBase  = ledEventCount;
    detectStrBase  = usbStrReqCount;
    detectRerdBase = usbStrRereadCount;
    detectPhase    = DET_IDLE;
}

// The verdict for the enumeration currently being judged, from the deltas since
// detectArm(). Split out because DET_SETTLED runs it again for a host that was
// still enumerating when the window closed.
HostOS detectVerdict() {
    uint32_t rereads = usbStrRereadCount - detectRerdBase;
    uint32_t strReqs = usbStrReqCount    - detectStrBase;
    uint32_t leds    = ledEventCount     - detectLedBase;

    // Positive Mac evidence: a host that reads a string's length before reading
    // the string itself, for string after string. It is a ratio rather than a
    // count because Windows does it too, just not as a habit - measured on
    // Windows 11 against this board, 11 string requests of which 2 were repeats.
    // macOS repeats every index, so half its requests or more are repeats.
    if (rereads >= 3 && rereads * 2 >= strReqs) return OS_MAC;
    // A host that read our strings straight through, or synced the lock LEDs at
    // enumeration. Linux does both exactly as Windows does and cannot be told
    // apart this way, so it reads as windows and a Linux user pins it by hand.
    if (strReqs >= 3 || leds > 0) return OS_WINDOWS;
    // Windows caches string descriptors per device, so a board it already knows
    // may say almost nothing on a replug. Guessing from silence is what made
    // v3.7 call every Mac a PC, so silence is reported as silence.
    return OS_UNKNOWN;
}

void updateHostDetect() {
    if (!hidReady) return;
    uint32_t now = millis();

    // Detection keeps running even when the OS is pinned by hand. The pin still
    // wins everywhere it matters, but knowing what was actually detected is what
    // lets the dashboard flag a pin left over from a different computer.
    bool mounted = tud_mounted();
    if (detectMounted && !mounted) detectArm();
    detectMounted = mounted;

    switch (detectPhase) {
        case DET_IDLE:
            if (mounted || usbCtrlCount != detectCtrlBase || ledEventCount != detectLedBase) {
                detectAt    = now;
                detectPhase = DET_WATCHING;
            }
            break;

        case DET_WATCHING: {
            if (now - detectAt < DETECT_WINDOW_MS) break;
            HostOS verdict = detectVerdict();
            detectPhase = DET_SETTLED;
            if (verdict != hostOsDetected) {
                hostOsDetected = verdict;
                announceHostOs();
            }
            break;
        }

        case DET_SETTLED:
            // A host slower than the window is what broke the old probe: it was
            // called a Mac and never reconsidered. Evidence arriving late now
            // answers an open question - but only an open one. A settled verdict
            // stands until the bus drops and a new enumeration replaces it,
            // because the host's own later traffic (a Caps Lock LED report, a
            // Device Manager refresh) is not another enumeration.
            if (hostOsDetected == OS_UNKNOWN) {
                HostOS late = detectVerdict();
                if (late != OS_UNKNOWN) {
                    hostOsDetected = late;
                    announceHostOs();
                }
            }
            break;

        default: break;
    }
}

// ==================== TIMING DEFAULTS (ms) ====================
#define WAKE_DELAY      1500  // after pressing Esc, wait for password field
#define CAD_DELAY       2000  // after Ctrl+Alt+Del, wait for password field
#define POST_TYPE_DELAY  200  // after typing password, before Enter
#define CHAR_DELAY        10  // between individual keystrokes

// ==================== RESPONSE BUFFERING ====================
// When a command arrives via WiFi, we execute the same handler as serial
// but also need to send the final JSON back over HTTP. We buffer it here.
String lastJsonResponse = "";

// ==================== FUNCTION PROTOTYPES ====================
void processCommand(const String& line, CmdSource src);
void handleUnlock(const JsonDocument& inDoc);
void handleType(const JsonDocument& inDoc);
void handlePress(const JsonDocument& inDoc);
void handleLock(const JsonDocument& inDoc);
void handleSetLockState(const JsonDocument& inDoc);
void handleSetHostOs(const JsonDocument& inDoc);
void handleTestHid();
void handlePing(bool fromWifi);
void handleDeviceStatus(bool fromWifi);
void handleEcho(const JsonDocument& inDoc);
void handleWifiConfigure(const JsonDocument& inDoc);
void handleWifiStatus();
void handleWifiDisconnect();
void handleApConfigure(const JsonDocument& inDoc);
void pressKeyCombo(const char* comboStr);
uint8_t mapKeyName(const String& name);
int typeString(const char* text);
void handleSetUnicode(const JsonDocument& inDoc);
void handleUnicodeTest(const JsonDocument& inDoc);
void sendJson(const JsonDocument& doc);
void sendWifiResponse();
void setupWifi();
void startAP();
void staBeginJoin(const String& ssid, const String& pass, bool useStatic);
void updateStaJoin();
const char* staPhaseStr();
const char* wlStatusStr(int s);
void setupWebRoutes();
void checkWifiConnection();
void staCameOnline(const String& ip);
void onStaEvent(arduino_event_id_t event, arduino_event_info_t info);
void staWentAway();
void staScheduleRetry();
void staSuperviseScan();
void netSelect(int i);
void netToFront(int i);
void netRemove(int i);
int netFind(const String& ssid);
void netSave();
void netLoad(const String& legacySsid, const String& legacyPass);
void handleWifiNetworks();
void handleWifiForget(const JsonDocument& inDoc);
void handleNetFault(const JsonDocument& inDoc);
void handleInternetShare(const JsonDocument& inDoc);
void applyInternetShare();
void handleWifiScan();

// Power + exposure (v3.4)
const char* powerModeStr();
void setRadioAwake(bool awake);
void noteActivity();
void applyPowerMode();
void updatePower();
void stopAP();
void restartAP();
void handlePowerMode(const JsonDocument& inDoc);
bool clientIsAp(const IPAddress& ip);
const char* linkQuality(int rssi);
bool validUtf8(const char* s, size_t len);
bool hasControlChars(const char* s, size_t len);
void handleStorageInfo();
void handleClients();

// Mouse / wiggle / LED (Phase 3.2)
void handleWiggle(const JsonDocument& inDoc);
void handleWiggleStop(const JsonDocument& inDoc);
void updateWiggle();
void doJiggle();
const char* wiggleModeStr();
void handleMouseMove(const JsonDocument& inDoc);
void handleMouseClick(const JsonDocument& inDoc);
void handleMousePress(const JsonDocument& inDoc);
void handleMouseRelease(const JsonDocument& inDoc);
void handleMouseScroll(const JsonDocument& inDoc);
uint8_t mapMouseButton(const char* name);
void pressModifiersOnly(const char* comboStr);
void handleLedSet(const JsonDocument& inDoc);
void handleLedBlink(const JsonDocument& inDoc);
void handleLedStatus();
void updateLedBlink();
void ledApply(uint8_t r, uint8_t g, uint8_t b);
bool colorNameToRGB(const char* name, uint8_t& r, uint8_t& g, uint8_t& b);

// Trackpad / remote-control additions (v3.4)
USBHIDMouseBase* activeMouse();
void hidMouseMove(int32_t dx, int32_t dy, int32_t wheel, int32_t pan);
void hidMouseAbs(int32_t x, int32_t y);
void hidMouseButtons(uint8_t mask, uint8_t action, uint8_t count);
void replyRaw(String out);
void onWsEvent(uint8_t num, WStype_t type, uint8_t* payload, size_t len);
void startWebSocket();
void startMdns(const String& suffix);
void handleMedia(const JsonDocument& inDoc);
void handleSystemCtl(const JsonDocument& inDoc);
void handleGesture(const JsonDocument& inDoc);
void handlePresenter(const JsonDocument& inDoc);
void handleMouseAbs(const JsonDocument& inDoc);
void handlePointerMode(const JsonDocument& inDoc);
void handleGamepadEnable(const JsonDocument& inDoc);
void handleGamepadCmd(const JsonDocument& inDoc);
void handleAuth(const JsonDocument& inDoc);
bool pinCheck(const char* candidate);
bool pinLocked();
uint32_t pinLockRemaining();
void handleSetAuth(const JsonDocument& inDoc);
void handleApPassword();
String makeApPassword();
void handleSetVerbose(const JsonDocument& inDoc);
void handleMacroSave(const JsonDocument& inDoc);
void handleMacroGet(const JsonDocument& inDoc);
void handleMacroList();
void handleMacroRun(const JsonDocument& inDoc);
void handleMacroDelete(const JsonDocument& inDoc);
void handleDo(const JsonDocument& inDoc);
void handleActions(const JsonDocument& inDoc);
void handleOpen(const JsonDocument& inDoc);
void handleSteps(const JsonDocument& inDoc);
uint16_t mediaUsage(const char* key);
void buildStatusDoc(JsonDocument& doc);
void handleUiSave(const JsonDocument& inDoc);
void handleUiGet();
void handleKeyDown(const JsonDocument& inDoc);
void handleKeyUp(const JsonDocument& inDoc);
void handleKeyReleaseAll();
void handlePcSave(const JsonDocument& inDoc);
void handlePcList();
void handlePcDelete(const JsonDocument& inDoc);
HostOS pcOs(int slot);
void handleVaultList();
void handleVaultSave(const JsonDocument& inDoc);
void handleVaultGet(const JsonDocument& inDoc);
void handleVaultType(const JsonDocument& inDoc);
void handleVaultDelete(const JsonDocument& inDoc);
void handleVaultWipe();
void handleVaultPin(const JsonDocument& inDoc);
void vaultWipeAll();
int  vaultCount();
String pcPassword(int slot);
uint8_t hidKeyFor(const char* name);

// ==================== SETUP ====================
void setup() {
    COM_SERIAL.begin(115200);
    
    unsigned long start = millis();
    while (!COM_SERIAL && millis() - start < 3000) {
        delay(10);
    }
    delay(500);
    
    pinMode(BOOT_BUTTON, INPUT_PULLUP);
    
    COM_SERIAL.println();
    COM_SERIAL.println("========================================");
    COM_SERIAL.printf("HID-Fi %s — ESP32-S3 USB HID + WiFi\n", FW_VERSION + 7);   // "v3.9", not "hid_fi_v3.9"
    COM_SERIAL.println("========================================");

    for (int i = 0; i < WS_MAX_CLIENTS; i++) { wsAuthed[i] = false; wsFromLan[i] = false; wsOpenedAt[i] = 0; }

    preferences.begin("hid_cfg", true);
    absoluteMode   = preferences.getBool("abs", false);
    gamepadEnabled = preferences.getBool("gpad", false);
    authToken      = preferences.getString("auth", "");
    verboseLog     = preferences.getBool("verbose", false);
    powerMode      = (PowerMode)preferences.getUChar("pwr", PWR_BALANCED);
    apAutoOff      = preferences.getBool("apoff", false);
    hostOsManual   = (HostOS)preferences.getUChar("os", OS_UNKNOWN);
    unicodeHosts   = preferences.getUChar("uni", 0);
    preferences.end();
    if (powerMode > PWR_SAVER) powerMode = PWR_BALANCED;   // guard against a bad stored value
    if (hostOsManual > OS_LINUX) hostOsManual = OS_UNKNOWN;

    preferences.begin("vault", true);
    vaultPinCode = preferences.getString("pin", "");
    preferences.end();

    // Exactly one mouse object may exist -- see the note on the globals.
    COM_SERIAL.printf("[HID] Initializing USB HID (pointer=%s, gamepad=%s)...\n",
                      absoluteMode ? "absolute" : "relative", gamepadEnabled ? "on" : "off");
    Keyboard.begin();
    // The host reports its lock-key LEDs here; Windows sends one at enumeration,
    // which corroborates a PC. Registered before USB.begin() so none is missed.
    Keyboard.onEvent(ARDUINO_USB_HID_KEYBOARD_LED_EVENT, keyboardLedCb);
    // SET_IDLE and SET_PROTOCOL only say a host is enumerating us - neither tells
    // a Mac from a PC, which the string-descriptor reads do. All of it is passive:
    // the board never types anything to find out. See HOST OS DETECTION.
    arduino_usb_event_handler_register_with(ARDUINO_USB_HID_EVENTS, ARDUINO_USB_HID_SET_IDLE_EVENT,
                                            usbHidCtrlCb, NULL);
    arduino_usb_event_handler_register_with(ARDUINO_USB_HID_EVENTS, ARDUINO_USB_HID_SET_PROTOCOL_EVENT,
                                            usbHidCtrlCb, NULL);
    if (absoluteMode) { mouseAbs = new USBHIDAbsoluteMouse(); mouseAbs->begin(); }
    else              { mouseRel = new USBHIDRelativeMouse(); mouseRel->begin(); }
    Consumer.begin();
    SystemCtl.begin();
    if (gamepadEnabled) { Gamepad = new USBHIDGamepad(); Gamepad->begin(); }
    USB.begin();
    delay(2000);  // PC needs time to enumerate the USB HID device
    hidReady = true;
    COM_SERIAL.println("[HID] USB HID ready (keyboard + mouse + media + system)");

    // Initialize on-board RGB LED (WS2812 on GPIO48; requires the 'RGB' pad bridged)
    ledApply(0, 0, 0);
    
    // Initialize WiFi (auto-connect if credentials saved)
    setupWifi();
    
    COM_SERIAL.println("========================================");
    COM_SERIAL.println("Inputs: UART0 + HTTP :80 + WebSocket :81");
    COM_SERIAL.printf("AP:   %s (pw: %s) → http://%s/\n", apSSID.c_str(), apPassword.c_str(), apIP.c_str());
    COM_SERIAL.printf("STA:  %s\n", wifiConnected ? wifiIP.c_str() : "not connected — use dashboard or wifi_configure");
    if (mdnsName.length()) COM_SERIAL.printf("mDNS: http://%s.local/\n", mdnsName.c_str());
    COM_SERIAL.printf("PIN:  %s\n", authToken.length() ? "set" : "not set (WiFi clients unrestricted)");
    COM_SERIAL.println("========================================");
    
    // Boot event JSON
    StaticJsonDocument<384> doc;
    doc["event"] = "boot";
    doc["chip"] = "ESP32-S3";
    doc["firmware"] = FW_VERSION;
    doc["hid_ready"] = hidReady;
    doc["wifi_connected"] = wifiConnected;
    if (wifiConnected) doc["wifi_ip"] = wifiIP;
    doc["ap_ssid"] = apSSID;
    doc["ap_ip"] = apIP;
    doc["pc_state"] = lockStateStr();
    doc["pointer_mode"] = absoluteMode ? "absolute" : "relative";
    doc["host_os"] = hostOsName(hostOs());
    doc["host_os_source"] = hostOsSource();
    doc["uptime"] = 0;
    sendJson(doc);
}

// ==================== LOOP ====================
// No fixed delay here: the pointer stream arrives on the WebSocket and every
// millisecond spent sleeping in loop() is a millisecond of cursor lag.
// Once a second: a socket that has not signed in within WS_AUTH_DEADLINE_MS of
// opening is closed. Only while a PIN is set - without one every socket is
// signed in from the start. The dashboard reconnects by itself and re-sends a
// kept PIN, so a person typing a PIN slowly only sees a reconnect.
void dropUnsignedSockets() {
    static uint32_t last = 0;
    if (millis() - last < 1000) return;
    last = millis();
    if (authToken.length() == 0) return;
    for (int i = 0; i < WS_MAX_CLIENTS; i++) {
        if (wsAuthed[i] || !wsOpenedAt[i]) continue;
        if (millis() - wsOpenedAt[i] > WS_AUTH_DEADLINE_MS) {
            wsOpenedAt[i] = 0;
            webSocket.disconnect(i);
        }
    }
}

void loop() {
    // Handle serial commands (primary)
    if (COM_SERIAL.available()) {
        String line = COM_SERIAL.readStringUntil('\n');
        line.trim();
        // A line this long is line noise or a stuck sender, not a command.
        // Dropping it keeps a garbage stream from growing the heap.
        if (line.length() > SERIAL_LINE_MAX) {
            COM_SERIAL.println("{\"status\":\"error\",\"message\":\"Line too long\"}");
        } else if (line.length() > 0) {
            processCommand(line, SRC_SERIAL);
        }
    }

    if (wifiEnabled) {
        webSocket.loop();          // input hot path first
        dropUnsignedSockets();
        webServer.handleClient();
        updateStaJoin();           // advances a join in progress, never blocks
        checkWifiConnection();
        updatePower();
    }

    // BOOT button. A quick tap runs the HID test; holding it for two seconds
    // brings the access point back if power saving switched it off. That hold is
    // the guaranteed way back in, so it works no matter what the network is
    // doing. Polled at 20 ms - the pin does not need 1 kHz.
    static unsigned long lastBtnPoll = 0;
    static bool lastButtonState = HIGH;
    static unsigned long btnDownAt = 0;
    static bool btnHoldDone = false;
    if (millis() - lastBtnPoll >= 20) {
        lastBtnPoll = millis();
        bool currentState = digitalRead(BOOT_BUTTON);

        if (currentState == LOW && lastButtonState == HIGH) {
            btnDownAt = millis();
            btnHoldDone = false;
        }
        if (currentState == LOW && !btnHoldDone && millis() - btnDownAt >= 2000) {
            btnHoldDone = true;   // fires once per hold, not every poll
            if (!apRunning) {
                restartAP();
                StaticJsonDocument<160> doc;
                doc["event"] = "ap_restored";
                doc["ap_ssid"] = apSSID;
                doc["ap_ip"] = apIP;
                sendJson(doc);
            }
        }
        if (currentState == HIGH && lastButtonState == LOW && !btnHoldDone) {
            if (hidReady) {
                COM_SERIAL.println("[HID] Button press → typing test string");
                Keyboard.println("HID OK");
            }
            StaticJsonDocument<128> doc;
            doc["event"] = "button_press";
            doc["hid_test"] = "typed 'HID OK'";
            doc["uptime"] = millis() / 1000;
            sendJson(doc);
        }
        lastButtonState = currentState;
    }

    // Non-blocking background operations (mouse wiggle + LED blink + OS probe)
    updateWiggle();
    updateLedBlink();
    updateHostDetect();

    // The allocator's own low-water mark is exact. This tick only exists to say
    // something on the serial log the first time it goes low.
    static unsigned long lastHeapCheck = 0;
    if (millis() - lastHeapCheck >= 1000) {
        lastHeapCheck = millis();
        if (!heapWarned && ESP.getMinFreeHeap() < HEAP_WARN) {
            heapWarned = true;
            COM_SERIAL.printf("[MEM] Free heap has been as low as %u bytes\n",
                              (unsigned)ESP.getMinFreeHeap());
        }
    }

    if (consumerReleaseAt && millis() >= consumerReleaseAt) {
        Consumer.release();
        consumerReleaseAt = 0;
    }

    // Deferred so the ap_configure reply reaches the phone before the network it
    // arrived on disappears underneath it.
    if (pendingApRestartAt && (long)(millis() - pendingApRestartAt) >= 0) {
        pendingApRestartAt = 0;
        restartAP();
    }

    if (pendingRestartAt && (long)(millis() - pendingRestartAt) >= 0) {
        COM_SERIAL.println("[SYS] Restarting to apply USB device change...");
        delay(50);
        ESP.restart();
    }

    delay(1);
}

// ==================== WiFi SETUP ====================
// Twelve characters from 31 that cannot be confused when read off a terminal
// (no 0/o, 1/l/i), grouped in fours: about 59 bits, easy to type on a phone.
String makeApPassword() {
    static const char A[] = "23456789abcdefghjkmnpqrstuvwxyz";
    String out;
    for (int g = 0; g < 3; g++) {
        if (g) out += '-';
        for (int i = 0; i < 4; i++) out += A[esp_random() % (sizeof(A) - 1)];
    }
    return out;
}

void setupWifi() {
    // Load saved AP config
    preferences.begin("wifi_cfg", true);
    String savedSSID = preferences.getString("ssid", "");
    String savedPass = preferences.getString("pass", "");
    String savedApSSID = preferences.getString("ap_ssid", "");
    String savedApPass = preferences.getString("ap_pass", "");
    // "No password" is a choice, and an empty string cannot tell it apart from
    // "never configured". Without this flag a deliberately open AP came back
    // password-protected after a reboot, and the phone could no longer join.
    bool savedApOpen = preferences.getBool("ap_open", false);
    // Static addressing is remembered too, so a reboot comes back on the same IP.
    staWantIp   = preferences.getString("ip", "");
    staWantGw   = preferences.getString("gw", "");
    staWantMask = preferences.getString("mask", "");
    staWantDns  = preferences.getString("dns", "");
    preferences.end();
    
    // AP SSID: a user-set custom name (via ap_configure) wins; otherwise default to
    // "ESP32-HID-XXXXXX" where XXXXXX is the last 3 octets of this chip's MAC, so
    // multiple boards in the same area each get a UNIQUE, identifiable network name.
    uint64_t efuse = ESP.getEfuseMac();  // bytes: (efuse>>0)=mac[0] ... (efuse>>40)=mac[5]
    char suffix[7];
    snprintf(suffix, sizeof(suffix), "%02X%02X%02X",
             (uint8_t)(efuse >> 24), (uint8_t)(efuse >> 32), (uint8_t)(efuse >> 40));
    if (savedApSSID.length() > 0) {
        apSSID = savedApSSID;
    } else {
        apSSID = "ESP32-HID-" + String(suffix);
    }
    if (savedApOpen)               apPassword = "";
    else if (savedApPass.length() >= 8) apPassword = savedApPass;
    else {
        // No password was ever set: make one for this board alone. A password
        // shared by every board (the old "hid12345") let anyone in range join,
        // and UK PSTI / ETSI EN 303 645 forbid universal defaults. It is random
        // from the hardware RNG - never derived from the MAC, which the board
        // broadcasts - and readable only over the USB cable (ap_password).
        apPassword = makeApPassword();
        preferences.begin("wifi_cfg", false);
        preferences.putString("ap_pass", apPassword);
        preferences.putBool("ap_open", false);
        preferences.putString("ap_genpw", apPassword);
        preferences.end();
        COM_SERIAL.println("[AP] This board made its own WiFi password - read it with ap_password over USB");
    }
    {
        // "Generated" means the password in use IS the one this board made, so it
        // stays true when someone changes it and puts it back. Boards from the first
        // v3.10 builds kept only a flag; their password then is the generated one.
        preferences.begin("wifi_cfg", false);
        apGenPassword = preferences.getString("ap_genpw", "");
        if (!apGenPassword.length() && preferences.getBool("ap_gen", false) && apPassword.length() >= 8) {
            apGenPassword = apPassword;
            preferences.putString("ap_genpw", apGenPassword);
        }
        preferences.remove("ap_gen");
        preferences.end();
        apPassGenerated = apGenPassword.length() >= 8 && apPassword == apGenPassword;
    }
    
    // Always start AP first — this is the primary access point
    startAP();
    
    // Then try STA if credentials are saved. This starts the join and returns -
    // boot used to sit in a busy-wait here, which left the access point and the
    // web server up but unable to answer anything until the join timed out.
    WiFi.onEvent(onStaEvent);
    WiFi.setAutoReconnect(false);       // the supervisor owns reconnecting (checkWifiConnection)
    netLoad(savedSSID, savedPass);
    {
        preferences.begin("wifi_cfg", true);
        apShare   = preferences.getBool("ap_share", false);
        staLastIp = preferences.getString("last_ip", "");
        preferences.end();
    }
    if (rtcFaultMagic == NET_FAULT_MAGIC) {     // net_fault at_boot: the router is "off" as we start
        rtcFaultMagic = 0;
        netFaultUntil = millis() + constrain(rtcFaultSecs, 5u, 600u) * 1000UL;
        COM_SERIAL.printf("[NET] Test fault: home network gone for %us from boot\n", (unsigned)rtcFaultSecs);
    }
    if (netCount > 0) {
        // Look first, then join the strongest saved network in range.
        COM_SERIAL.printf("[WIFI] %u saved network(s) - looking for one in range\n", (unsigned)netCount);
        staRetryAt = millis();
        if (!staRetryAt) staRetryAt = 1;
    } else {
        COM_SERIAL.println("[WIFI] No STA credentials. AP-only mode.");
        COM_SERIAL.println("[WIFI] Connect to AP and use dashboard to configure STA network.");
    }

    startMdns(String(suffix));
}

void startAP() {
    WiFi.mode(WIFI_AP_STA);  // Always AP+STA so we can scan and connect later

    // Radio sleep is decided by the power policy, not hard-coded here. See the
    // PowerMode notes with the globals for why this matters so much for feel.
    applyPowerMode();

    WiFi.softAP(apSSID.c_str(), apPassword.c_str());
    delay(100);  // softAP needs a moment
    
    apIP = WiFi.softAPIP().toString();
    apRunning = true;
    apIdleSince = millis();
    
    COM_SERIAL.println("[AP] ==============================");
    COM_SERIAL.printf("[AP] SSID: %s\n", apSSID.c_str());
    COM_SERIAL.printf("[AP] Password: %s\n", apPassword.c_str());
    COM_SERIAL.printf("[AP] IP: %s\n", apIP.c_str());
    COM_SERIAL.printf("[AP] Dashboard: http://%s/\n", apIP.c_str());
    COM_SERIAL.println("[AP] ==============================");
    
    // Start web server immediately (AP is always available)
    setupWebRoutes();
    webServer.begin();
    startWebSocket();
    wifiEnabled = true;
    COM_SERIAL.println("[AP] HTTP server on :80, WebSocket on :81");
}

// Persistent socket for the pointer stream. HTTP on this core has no keep-alive,
// so a per-event POST costs a whole TCP handshake -- that was the old lag.
void startWebSocket() {
    webSocket.begin();
    webSocket.onEvent(onWsEvent);
    webSocket.enableHeartbeat(15000, 4000, 2);
}

void startMdns(const String& suffix) {
    mdnsName = "esp-hid-" + suffix;
    mdnsName.toLowerCase();
    if (MDNS.begin(mdnsName.c_str())) {
        MDNS.addService("http", "tcp", 80);
        MDNS.addService("ws", "tcp", 81);
        MDNS.addService("hidfi", "tcp", 80);     // tools/hidfi.py find looks for this
        COM_SERIAL.printf("[MDNS] http://%s.local/\n", mdnsName.c_str());
    } else {
        COM_SERIAL.println("[MDNS] start failed");
        mdnsName = "";
    }
}

// ==================== POWER ====================
const char* powerModeStr() {
    switch (powerMode) {
        case PWR_PERFORMANCE: return "performance";
        case PWR_SAVER:       return "saver";
        default:              return "balanced";
    }
}

// Only touch the driver on a real change. Calling into the WiFi stack is not
// free, and this is reachable from the pointer path.
void setRadioAwake(bool awake) {
    if (awake == radioAwake) return;
    radioAwake = awake;
    WiFi.setSleep(!awake);
    COM_SERIAL.printf("[PWR] Radio %s\n", awake ? "awake" : "sleeping");
}

// Called for every client packet, including every pointer move. In the common
// case this is one store and one already-true comparison.
void noteActivity() {
    lastInputAt = millis();
    if (!radioAwake && powerMode != PWR_SAVER) setRadioAwake(true);
}

void applyPowerMode() {
    // BALANCED starts awake and is put to sleep later by updatePower(), so the
    // first thing a user does after boot always feels right.
    //
    // This writes to the driver unconditionally rather than going through
    // setRadioAwake(). At boot the core has already defaulted the S3 to
    // WIFI_PS_MIN_MODEM, so our idea of the state and the driver's disagree, and
    // a change-detecting call would skip the one write that actually matters.
    bool want = (powerMode != PWR_SAVER);
    radioAwake = want;
    WiFi.setSleep(!want);
    lastInputAt = millis();
    COM_SERIAL.printf("[PWR] Mode %s, radio %s\n", powerModeStr(), want ? "awake" : "sleeping");
}

// Ticked from loop(). Never blocks.
void updatePower() {
    if (powerMode == PWR_BALANCED) {
        // A connected dashboard is a dashboard someone may touch in the next
        // second, so being connected is enough to hold the radio awake. Driving
        // this from state rather than only from packets is what makes the radio
        // wake the moment a client appears, instead of on its first move.
        bool inUse = webSocket.connectedClients() > 0 ||
                     (millis() - lastInputAt <= POWER_IDLE_MS);
        setRadioAwake(inUse);
    }

    if (!apAutoOff || !apRunning) return;
    // Never drop the AP unless the board is provably reachable another way.
    if (!wifiConnected || WiFi.softAPgetStationNum() > 0 || webSocket.connectedClients() > 0) {
        apIdleSince = millis();
        return;
    }
    if (millis() - apIdleSince > AP_IDLE_MS) stopAP();
}

void stopAP() {
    WiFi.softAPdisconnect(true);
    apRunning = false;
    apShareActive = false;
    COM_SERIAL.println("[PWR] AP stopped to save power. Hold BOOT for 2s to bring it back.");
}

void restartAP() {
    WiFi.mode(WIFI_AP_STA);   // softAPdisconnect(true) can drop us out of AP mode
    if (apPassword.length() >= 8) WiFi.softAP(apSSID.c_str(), apPassword.c_str());
    else                          WiFi.softAP(apSSID.c_str());
    delay(100);
    apIP = WiFi.softAPIP().toString();
    apRunning = true;
    apIdleSince = millis();
    apShareActive = false;      // a fresh AP: sharing is applied again on the next tick
    COM_SERIAL.printf("[AP] Back up: %s at %s\n", apSSID.c_str(), apIP.c_str());
}

// Everything on the board's own access point sits on the softAP subnet.
// Anything else reached us across the home network.
bool clientIsAp(const IPAddress& ip) {
    IPAddress ap = WiFi.softAPIP();
    return ip[0] == ap[0] && ip[1] == ap[1] && ip[2] == ap[2];
}

// dBm is meaningless to most people, and the thresholds are what actually
// predict whether the pointer will feel bad.
const char* linkQuality(int rssi) {
    if (rssi >= -55) return "strong";
    if (rssi >= -67) return "good";
    if (rssi >= -75) return "weak";
    return "very weak";
}

// An SSID is arbitrary bytes on the wire, so emoji work - but only if they are
// well formed. A truncated sequence would render as garbage on every phone that
// sees the network, and would break the JSON this board emits.
bool validUtf8(const char* s, size_t len) {
    size_t i = 0;
    while (i < len) {
        uint8_t c = (uint8_t)s[i];
        size_t n;
        if      (c < 0x80)           n = 0;
        else if ((c & 0xE0) == 0xC0) n = 1;
        else if ((c & 0xF0) == 0xE0) n = 2;
        else if ((c & 0xF8) == 0xF0) n = 3;
        else return false;
        if (n > 0 && i + n >= len) return false;          // truncated sequence
        for (size_t k = 1; k <= n; k++)
            if (((uint8_t)s[i + k] & 0xC0) != 0x80) return false;
        i += n + 1;
    }
    return true;
}

bool hasControlChars(const char* s, size_t len) {
    for (size_t i = 0; i < len; i++) {
        uint8_t c = (uint8_t)s[i];
        if (c < 0x20 || c == 0x7F) return true;
    }
    return false;
}

const char* staPhaseStr() {
    switch (staPhase) {
        case STA_CONNECTING: return "connecting";
        case STA_ONLINE:     return "online";
        case STA_FAILED:     return "failed";
        case STA_RECONNECTING: return "reconnecting";
        default:             return "idle";
    }
}

// The ESP32's status codes are approximate - a bad password often surfaces as
// WL_DISCONNECTED rather than WL_CONNECT_FAILED - so these are worded as
// likelihoods rather than verdicts.
const char* wlStatusStr(int s) {
    switch (s) {
        case WL_NO_SSID_AVAIL:  return "Network not found. Check the name, and that it is 2.4 GHz.";
        case WL_CONNECT_FAILED: return "The router refused the connection. The password is the usual cause.";
        case WL_CONNECTION_LOST:return "Connection lost during the join.";
        case WL_IDLE_STATUS:    return "Radio went idle before associating.";
        case WL_DISCONNECTED:   return "Could not associate. Usually a wrong password, or the network is out of range.";
        default:                return "Timed out waiting for the router.";
    }
}

// Starts a join and returns immediately. useStatic applies staWantIp if one is
// set; the fallback path calls this again with false.
void staBeginJoin(const String& ssid, const String& pass, bool useStatic) {
    staSsid = ssid;
    staError = "";
    staPhase = STA_CONNECTING;
    staPhaseAt = millis();

    WiFi.disconnect(false);   // drop any previous association, keep the AP up

    staStaticTried = false;
    staIpMode = "dhcp";

    if (useStatic && staWantIp.length()) {
        IPAddress ip, gw, mask, dns;
        bool ok = ip.fromString(staWantIp) &&
                  gw.fromString(staWantGw.length() ? staWantGw : "0.0.0.0") &&
                  mask.fromString(staWantMask.length() ? staWantMask : "255.255.255.0");
        if (!dns.fromString(staWantDns.length() ? staWantDns : staWantGw)) dns = gw;
        // WiFi.config() only fails on malformed input. It cannot tell us the
        // address is already in use on the network - nothing on the ESP32 can,
        // because a static client never asks anyone's permission. The timeout
        // below is what actually catches a bad address.
        if (ok && WiFi.config(ip, gw, mask, dns)) {
            staStaticTried = true;
            staIpMode = "static";
            COM_SERIAL.printf("[STA] Requesting static %s via gw %s\n",
                              staWantIp.c_str(), gw.toString().c_str());
        } else {
            staError = "Static address details were not valid, using DHCP instead.";
            COM_SERIAL.println("[STA] Static config rejected, falling back to DHCP");
        }
    } else {
        // 0.0.0.0 everywhere puts the interface back on DHCP after a static try.
        WiFi.config(INADDR_NONE, INADDR_NONE, INADDR_NONE);
    }

    WiFi.begin(ssid.c_str(), pass.c_str());
    COM_SERIAL.printf("[STA] Joining %s (%s)...\n", ssid.c_str(), staIpMode.c_str());
}

// Ticked from loop(). Advances or ends the join without ever blocking.
void updateStaJoin() {
    if (staPhase != STA_CONNECTING) return;

    if (WiFi.status() == WL_CONNECTED) {
        wifiConnected = true;
        wifiIP = WiFi.localIP().toString();

        // Association succeeding does not prove the static address was honoured.
        // Some routers hand out their own regardless, so compare what we got.
        if (staStaticTried && wifiIP != staWantIp) {
            staIpMode = "dhcp_fallback";
            staError  = "The router did not honour " + staWantIp + ", it assigned " + wifiIP + " instead.";
        }
        staCameOnline(wifiIP);
        COM_SERIAL.printf("[STA] Online as %s (%s), RSSI %d dBm\n",
                          wifiIP.c_str(), staIpMode.c_str(), WiFi.RSSI());
        return;
    }

    if (millis() - staPhaseAt < WIFI_CONNECT_TIMEOUT) return;

    // Timed out. A static address that cannot reach its gateway looks exactly
    // like this, so before giving up entirely, try once more on DHCP.
    if (staStaticTried) {
        COM_SERIAL.println("[STA] Static attempt timed out, retrying with DHCP");
        String pass;
        preferences.begin("wifi_cfg", true);
        pass = preferences.getString("pass", "");
        preferences.end();
        staBeginJoin(staSsid, pass, false);
        staIpMode = "dhcp_fallback";
        staError  = "Could not come up on " + staWantIp + ". It may already be in use, or outside the router's subnet.";
        return;
    }

    wifiConnected = false;
    wifiIP = "";
    // A network that has worked before is not "failed" because it is away: the
    // supervisor keeps trying. A new one the user just typed in is.
    staPhase = (staSupervised && staProven) ? STA_RECONNECTING : STA_FAILED;
    if (staAway) staError = "No saved network in range.";
    else if (staError.length() == 0 || staPhase == STA_RECONNECTING) staError = wlStatusStr(WiFi.status());
    COM_SERIAL.printf("[STA] Join %s: %s\n", staPhase == STA_FAILED ? "failed" : "attempt missed", staError.c_str());
    if (!staDownSince) staDownSince = millis();
    // Decide on the router's reason before our own disconnect replaces it.
    staScheduleRetry();
    WiFi.disconnect(false);   // keep the AP running no matter what
}

// The router told us why it let go. Runs on the WiFi task: store, nothing else.
void onStaEvent(arduino_event_id_t event, arduino_event_info_t info) {
    if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) staLastReason = info.wifi_sta_disconnected.reason;
}

static bool staReasonIsPassword(uint8_t r) {
    return r == WIFI_REASON_AUTH_FAIL || r == WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT ||
           r == WIFI_REASON_HANDSHAKE_TIMEOUT || r == WIFI_REASON_MIC_FAILURE;
}

void staScheduleRetry() {
    if (!netCount) return;
    if (staReasonIsPassword(staLastReason)) {
        if (staAuthFails < 255) staAuthFails++;
    } else {
        staAuthFails = 0;
    }
    uint32_t gap;
    if (!staProven && staAuthFails) {
        // Never worked, and the router says the password is wrong: never try it
        // again until it is re-entered, and say so. Other saved networks still
        // get their turn.
        if (netCurrent >= 0) netBad[netCurrent] = true;
        staPhase = STA_FAILED;
        staError = "The router refused the password for " + staSavedSsid + ". Enter it again in Settings.";
        gap = STA_RETRY_FIRST * 5;
    } else if (!staProven && netCurrent >= 0) {
        // A new network that failed for another reason (out of range, no address):
        // rest it, so a network that has worked is preferred meanwhile.
        netCoolUntil[netCurrent] = millis() + STA_RETRY_BADPASS;
        gap = staRetryGap;
        staRetryGap = min<uint32_t>(staRetryGap * 2 + 1000, STA_RETRY_MAX);
    } else if (staProven && staAuthFails >= 3) {
        // Routers misreport, so it is not dropped - it rests, and the others are tried.
        if (netCurrent >= 0) netCoolUntil[netCurrent] = millis() + STA_RETRY_BADPASS;
        staError = "The router no longer accepts the saved password for " + staSavedSsid + ". If you changed it, enter it again in Settings.";
        staAuthFails = 0;
        gap = STA_RETRY_FIRST * 5;
    } else {
        gap = staRetryGap;
        staRetryGap = min<uint32_t>(staRetryGap * 2 + 1000, STA_RETRY_MAX);
    }
    staRetryAt = millis() + gap;
    if (!staRetryAt) staRetryAt = 1;
}

static void broadcastJson(const JsonDocument& doc) {
    String out;
    serializeJson(doc, out);
    if (wifiEnabled) webSocket.broadcastTXT(out);
    COM_SERIAL.println(out);
}

// On the network: either a join just finished, or the address changed under us.
void staCameOnline(const String& ip) {
    bool changed = staLastIp.length() && ip != staLastIp;
    if (changed) {
        staPrevIp = staLastIp;
        staIpChanges++;
        COM_SERIAL.printf("[NET] Address changed: %s -> %s\n", staPrevIp.c_str(), ip.c_str());
    }
    bool wasDown = staDownSince != 0;
    wifiConnected  = true;
    wifiIP         = ip;
    staPhase       = STA_ONLINE;
    staDownSince   = 0;
    staOnlineSince = millis();
    staRetryAt     = 0;
    staRetryGap    = STA_RETRY_FIRST;
    staAuthFails   = 0;
    if (!staStaticTried && staIpMode != "dhcp_fallback") staError = "";
    // Remember the address across reboots too: after a power cut the router may
    // hand out a different one, and that is exactly when the owner needs telling.
    if (ip != staLastIp) {
        preferences.begin("wifi_cfg", false);
        preferences.putString("last_ip", ip);
        preferences.end();
    }
    staLastIp = ip;
    staProven = true;
    staAway = false;
    staScanPending = false;
    staBlindRound = 0;                     // a new outage counts its rounds from the start
    // The network that worked goes to the front, proven - as on a phone.
    if (netCurrent >= 0 && (!netOk[netCurrent] || netCurrent != 0)) {
        netOk[netCurrent] = true;
        netBad[netCurrent] = false;
        netToFront(netCurrent);
        netSave();
    }
    // mDNS answers on the new address, not the old one.
    if (changed && mdnsName.length()) {
        MDNS.end();
        if (MDNS.begin(mdnsName.c_str())) {
            MDNS.addService("http", "tcp", 80);
            MDNS.addService("ws", "tcp", 81);
            MDNS.addService("hidfi", "tcp", 80);
        }
    }
    if (wasDown || changed) {
        StaticJsonDocument<256> ev;
        ev["event"]   = "sta_online";
        ev["ip"]      = ip;
        ev["changed"] = changed;
        if (changed) ev["prev_ip"] = staPrevIp;
        ev["mdns"]    = mdnsName;
        broadcastJson(ev);
    }
}

void staWentAway() {
    wifiConnected = false;
    wifiIP = "";
    staDrops++;
    staDownSince = millis();
    staPhase = staProven ? STA_RECONNECTING : STA_FAILED;
    staRetryGap = STA_RETRY_FIRST;
    COM_SERIAL.printf("[NET] Home network lost (reason %u) - access point still up, rejoining\n",
                      (unsigned)staLastReason);
    staScheduleRetry();
}

// Bring sharing in line with what is wanted and what is possible. The DHCP
// server must offer the home network's DNS, or phones connect and resolve nothing.
void applyInternetShare() {
    bool want = apShare && wifiConnected && apRunning;
    if (want == apShareActive) return;
    IPAddress apip(192, 168, 4, 1), mask(255, 255, 255, 0);
    if (want) {
        WiFi.softAPConfig(apip, apip, mask, IPAddress(192, 168, 4, 2), WiFi.dnsIP());
        apShareActive = WiFi.AP.enableNAPT(true);
        COM_SERIAL.printf("[NET] Sharing the home internet on the board's WiFi: %s\n", apShareActive ? "on" : "FAILED");
    } else {
        if (apRunning) {
            WiFi.AP.enableNAPT(false);
            WiFi.softAPConfig(apip, apip, mask);           // no DNS offered: nothing to resolve with
        }
        apShareActive = false;
        COM_SERIAL.println("[NET] Sharing the home internet: off");
    }
}

void handleInternetShare(const JsonDocument& inDoc) {
    StaticJsonDocument<320> d;
    if (!inDoc["on"].isNull()) {
        bool on = inDoc["on"].as<bool>();
        // Sharing opens the home network to anyone on the board's WiFi, so it is
        // never switched on over WiFi by someone who could not set a PIN.
        if (on && cmdSource != SRC_SERIAL && authToken.length() == 0) {
            d["status"]="error"; d["reply"]="pin_required";
            d["message"]="Set an access PIN first: sharing lets anything on the board's WiFi reach your home network.";
            sendJson(d); return;
        }
        apShare = on;
        preferences.begin("wifi_cfg", false);
        preferences.putBool("ap_share", apShare);
        preferences.end();
        applyInternetShare();
    }
    d["status"]="ok"; d["reply"]="internet_share";
    d["on"] = apShare;
    d["active"] = apShareActive;
    if (apShare && !apShareActive) d["waiting_for"] = wifiConnected ? "the access point" : "the home network";
    sendJson(d);
}

void checkWifiConnection() {
    if (millis() - lastWifiCheck < 500) return;
    lastWifiCheck = millis();

    if (netFaultUntil && (long)(millis() - netFaultUntil) >= 0) {
        netFaultUntil = 0;
        netFaultSsid = "";
        staRetryAt = millis();                 // the "router" is back: try now
        staRetryGap = STA_RETRY_FIRST;
        COM_SERIAL.println("[NET] Test fault over - the network is back");
    }
    applyInternetShare();
    // The board must never be unreachable: with the home network gone, the access
    // point comes back even if it was switched off to save power. Checked before
    // the join guard below - a rejoin attempt lasts 15 s and must not hold it up.
    if (apAutoOff && !apRunning && staDownSince && millis() - staDownSince > AP_FALLBACK_MS) {
        restartAP();
        StaticJsonDocument<160> ev;
        ev["event"] = "ap_restored"; ev["ap_ssid"] = apSSID; ev["ap_ip"] = apIP; ev["why"] = "home network lost";
        broadcastJson(ev);
    }
    // A join in flight owns the radio; updateStaJoin() resolves it.
    if (staPhase == STA_CONNECTING) return;

    bool up = WiFi.status() == WL_CONNECTED && (uint32_t)WiFi.localIP() != 0 &&
              !(netFaultUntil && (!netFaultSsid.length() || WiFi.SSID() == netFaultSsid));
    if (up) {
        String ip = WiFi.localIP().toString();
        if (!wifiConnected || ip != wifiIP) staCameOnline(ip);
        return;
    }
    if (wifiConnected) staWentAway();
    if (!netCount) return;

    if (staScanPending) { staSuperviseScan(); return; }
    if (!staRetryAt || (long)(millis() - staRetryAt) < 0) return;
    staRetryAt = 0;
    // Look before joining: a blind join of a network that is not there costs a
    // 15 s attempt that disturbs the board's own WiFi; a scan costs about two.
    int16_t r = WiFi.scanNetworks(true);
    if (r == WIFI_SCAN_FAILED) { staRetryAt = millis() + STA_RETRY_FIRST * 5; return; }
    staScanPending = true;
    staScans++;
}

// The scan the supervisor started has finished (or not yet): pick the strongest
// saved network in range and join it; with none in range, wait - longer the
// longer the board has been away - and now and then try a proven one unseen,
// because a hidden network never shows up in a scan.
void staSuperviseScan() {
    int16_t n = WiFi.scanComplete();
    if (n == WIFI_SCAN_RUNNING) return;
    staScanPending = false;
    int best = -1, bestRssi = -1000;
    for (int i = 0; n > 0 && i < netCount; i++) {
        if (netFaultUntil && (!netFaultSsid.length() || netSsid[i] == netFaultSsid)) continue;   // staged absence
        if (netBad[i] || (netCoolUntil[i] && (long)(millis() - netCoolUntil[i]) < 0)) continue;
        for (int k = 0; k < n; k++) {
            if (WiFi.SSID(k) == netSsid[i] && WiFi.RSSI(k) > bestRssi) { best = i; bestRssi = WiFi.RSSI(k); }
        }
    }
    if (!staDownSince) staDownSince = millis();
    if (best < 0) {
        staAway = true;
        int proven = -1;
        for (int i = 0; i < netCount && proven < 0; i++) if (netOk[i] && !netBad[i]) proven = i;
        if (proven >= 0 && ++staBlindRound >= 4) {
            staBlindRound = 0;
            best = proven;                                  // maybe hidden: try it unseen
        } else {
            staPhase = proven >= 0 ? STA_RECONNECTING : (staPhase == STA_FAILED ? STA_FAILED : STA_RECONNECTING);
            if (!staError.length() || staError.startsWith("No saved network"))
                staError = "No saved network in range.";
            staRetryAt = millis() + ((millis() - staDownSince) > 600000UL ? STA_AWAY_GAP_LATE : STA_AWAY_GAP_SOON);
            if (!staRetryAt) staRetryAt = 1;
            return;
        }
    } else {
        staAway = false;
    }
    netSelect(best);
    staTries++;
    staSupervised = true;
    bool staged = netFaultUntil && (!netFaultSsid.length() || staSavedSsid == netFaultSsid);
    staBeginJoin(staged ? String(NET_FAULT_SSID) : staSavedSsid, staSavedPass, true);
    staSsid = staSavedSsid;
    // staBeginJoin() clears the error; while away, the status keeps saying why.
    if (staAway) staError = "No saved network in range - trying " + staSavedSsid + " unseen, in case it is hidden.";
}

// ---- the saved-network table ------------------------------------------------
void netSelect(int i) {
    netCurrent   = i;
    staSavedSsid = netSsid[i];
    staSavedPass = netPass[i];
    staProven    = netOk[i];
    staWantIp = netIp[i]; staWantGw = netGw[i]; staWantMask = netMask[i]; staWantDns = netDns[i];
}

void netToFront(int i) {
    if (i <= 0) return;
    String s = netSsid[i], p = netPass[i], ip = netIp[i], gw = netGw[i], m = netMask[i], d = netDns[i];
    bool ok = netOk[i], bad = netBad[i]; uint32_t cool = netCoolUntil[i];
    for (int k = i; k > 0; k--) {
        netSsid[k] = netSsid[k-1]; netPass[k] = netPass[k-1]; netIp[k] = netIp[k-1]; netGw[k] = netGw[k-1];
        netMask[k] = netMask[k-1]; netDns[k] = netDns[k-1]; netOk[k] = netOk[k-1]; netBad[k] = netBad[k-1];
        netCoolUntil[k] = netCoolUntil[k-1];
    }
    netSsid[0] = s; netPass[0] = p; netIp[0] = ip; netGw[0] = gw; netMask[0] = m; netDns[0] = d;
    netOk[0] = ok; netBad[0] = bad; netCoolUntil[0] = cool;
    if (netCurrent == i) netCurrent = 0; else if (netCurrent >= 0 && netCurrent < i) netCurrent++;
}

void netRemove(int i) {
    for (int k = i; k < netCount - 1; k++) {
        netSsid[k] = netSsid[k+1]; netPass[k] = netPass[k+1]; netIp[k] = netIp[k+1]; netGw[k] = netGw[k+1];
        netMask[k] = netMask[k+1]; netDns[k] = netDns[k+1]; netOk[k] = netOk[k+1]; netBad[k] = netBad[k+1];
        netCoolUntil[k] = netCoolUntil[k+1];
    }
    netCount--;
    netSsid[netCount] = netPass[netCount] = "";
    if (netCurrent == i) netCurrent = -1; else if (netCurrent > i) netCurrent--;
}

int netFind(const String& ssid) {
    for (int i = 0; i < netCount; i++) if (netSsid[i] == ssid) return i;
    return -1;
}

void netSave() {
    preferences.begin("wifi_cfg", false);
    for (int i = 0; i < NET_MAX; i++) {
        char key[4]; snprintf(key, sizeof(key), "n%d", i);
        if (i >= netCount) { preferences.remove(key); continue; }
        StaticJsonDocument<384> d;
        d["s"] = netSsid[i]; d["p"] = netPass[i]; d["ok"] = netOk[i];
        if (netIp[i].length()) { d["ip"] = netIp[i]; d["gw"] = netGw[i]; d["mask"] = netMask[i]; d["dns"] = netDns[i]; }
        String out; serializeJson(d, out);
        preferences.putString(key, out);
    }
    // Row 0 mirrored to the v3.10 keys, so a downgrade still has its network.
    if (netCount) {
        preferences.putString("ssid", netSsid[0]); preferences.putString("pass", netPass[0]);
        preferences.putString("ip", netIp[0]); preferences.putString("gw", netGw[0]);
        preferences.putString("mask", netMask[0]); preferences.putString("dns", netDns[0]);
        preferences.putBool("sta_ok", netOk[0]);
    } else {
        preferences.remove("ssid"); preferences.remove("pass"); preferences.remove("ip"); preferences.remove("gw");
        preferences.remove("mask"); preferences.remove("dns"); preferences.remove("sta_ok");
    }
    preferences.end();
}

// Read the table; a board coming from v3.10 has only the single-network keys,
// which become row 0 - proven if they were.
void netLoad(const String& legacySsid, const String& legacyPass) {
    netCount = 0;
    preferences.begin("wifi_cfg", true);
    for (int i = 0; i < NET_MAX; i++) {
        char key[4]; snprintf(key, sizeof(key), "n%d", i);
        String raw = preferences.getString(key, "");
        if (!raw.length()) break;
        StaticJsonDocument<384> d;
        if (deserializeJson(d, raw) || !(d["s"] | "")[0]) break;
        netSsid[netCount] = d["s"].as<String>(); netPass[netCount] = d["p"] | "";
        netOk[netCount] = d["ok"] | false;
        netIp[netCount] = d["ip"] | ""; netGw[netCount] = d["gw"] | "";
        netMask[netCount] = d["mask"] | ""; netDns[netCount] = d["dns"] | "";
        netBad[netCount] = false; netCoolUntil[netCount] = 0;
        netCount++;
    }
    bool legacyOk = preferences.getBool("sta_ok", false);
    preferences.end();
    if (netCount == 0 && legacySsid.length()) {
        netSsid[0] = legacySsid; netPass[0] = legacyPass; netOk[0] = legacyOk;
        netIp[0] = staWantIp; netGw[0] = staWantGw; netMask[0] = staWantMask; netDns[0] = staWantDns;
        netBad[0] = false; netCoolUntil[0] = 0;
        netCount = 1;
        netSave();
        COM_SERIAL.printf("[WIFI] Saved network %s carried over from the single-network settings\n", legacySsid.c_str());
    }
    if (netCount) netSelect(0);
}

// The saved networks, never their passwords.
void handleWifiNetworks() {
    DynamicJsonDocument d(1536);
    d["status"] = "ok"; d["reply"] = "wifi_networks"; d["max"] = NET_MAX;
    JsonArray a = d.createNestedArray("networks");
    for (int i = 0; i < netCount; i++) {
        JsonObject o = a.createNestedObject();
        o["ssid"] = netSsid[i];
        o["proven"] = netOk[i];
        o["static"] = netIp[i].length() > 0;
        o["current"] = (i == netCurrent && wifiConnected);
        if (netBad[i]) o["refused"] = true;
    }
    sendJson(d);
}

void handleWifiForget(const JsonDocument& inDoc) {
    String ssid = inDoc["ssid"] | "";
    int i = netFind(ssid);
    StaticJsonDocument<192> d;
    if (i < 0) { d["status"] = "error"; d["message"] = "Not a saved network"; sendJson(d); return; }
    bool wasCurrent = (i == netCurrent);
    netRemove(i);
    netSave();
    COM_SERIAL.printf("[WIFI] Forgot %s\n", ssid.c_str());
    if (wasCurrent) {
        // Off this one; the supervisor looks for another saved network at once.
        staSavedSsid = staSavedPass = ""; staProven = false;
        if (wifiConnected) WiFi.disconnect(false);
        staRetryAt = millis(); if (!staRetryAt) staRetryAt = 1;
    }
    d["status"] = "ok"; d["reply"] = "wifi_forgotten"; d["ssid"] = ssid; d["left"] = netCount;
    sendJson(d);
}

// Serial only: rehearse a bad night for the home network without touching the
// router. drop - the router lets us go; vanish - it is off for s seconds;
// at_boot - and it was off when the board started; with ssid, only that network
// vanishes (one home gone, another still there); ap_off - auto-off fired;
// ip_was - pretend we last
// held that address, so the next join proves the change is noticed; clear.
void handleNetFault(const JsonDocument& inDoc) {
    StaticJsonDocument<224> d;
    if (cmdSource != SRC_SERIAL) {
        d["status"]="error"; d["reply"]="usb_only"; d["message"]="Network tests run over the USB cable only";
        sendJson(d); return;
    }
    const char* kind = inDoc["kind"] | "";
    uint32_t secs = constrain((uint32_t)(inDoc["s"] | 30), 5u, 600u);
    if (!strcmp(kind, "drop")) {
        WiFi.disconnect(false);
    } else if (!strcmp(kind, "vanish")) {
        if (inDoc["at_boot"] | false) {
            rtcFaultMagic = NET_FAULT_MAGIC; rtcFaultSecs = secs;
            pendingRestartAt = millis() + 300;
        } else {
            netFaultUntil = millis() + secs * 1000UL;
            netFaultSsid = String(inDoc["ssid"] | "");     // one network, or all of them
            if (!netFaultSsid.length() || WiFi.SSID() == netFaultSsid) WiFi.disconnect(false);
        }
    } else if (!strcmp(kind, "ap_off")) {
        stopAP();                                   // as auto-off would, without the 10 minute wait
    } else if (!strcmp(kind, "ip_was")) {
        staLastIp = String(inDoc["ip"] | "");
    } else if (!strcmp(kind, "clear")) {
        netFaultUntil = 0; netFaultSsid = ""; rtcFaultMagic = 0; staRetryAt = millis();
    } else {
        d["status"]="error"; d["message"]="kind is drop, vanish, ap_off, ip_was or clear"; sendJson(d); return;
    }
    d["status"]="ok"; d["reply"]="net_fault"; d["kind"]=kind;
    if (!strcmp(kind, "vanish")) d["s"] = secs;
    sendJson(d);
}

// ==================== WEB ROUTES ====================
// /api/scan is a plain GET, so it never reached the gate in processCommand and
// any device on the home network could list the networks around you without a
// PIN. It answers to the same rules as everything else now: on the board's own
// access point radio range is the limit, on a LAN a PIN is required, and the
// dashboard sends it in a header rather than the URL so it stays out of logs.
bool scanAllowed() {
    pinPeer = webServer.client().remoteIP();
    if (clientIsAp(pinPeer)) return true;
    if (authToken.length()) {
        String tok = webServer.header("X-Auth");
        return tok.length() && !pinLocked() && pinCheck(tok.c_str());
    }
    return !wifiConnected;   // not on a network at all, so nothing to expose it to
}

void setupWebRoutes() {
    // WebServer discards every header it was not told to keep.
    static const char* keepHeaders[] = { "X-Auth" };
    webServer.collectHeaders(keepHeaders, 1);
    // Serve dashboard at root
    webServer.on("/", HTTP_GET, []() {
        webServer.send_P(200, "text/html", WEB_INTERFACE);
    });
    
    // POST /api/command — accepts same JSON as serial
    webServer.on("/api/command", HTTP_POST, []() {
        if (!webServer.hasArg("plain")) {
            webServer.send(400, "application/json", "{\"status\":\"error\",\"message\":\"No body\"}");
            return;
        }
        String body = webServer.arg("plain");
        if (verboseLog) {
            COM_SERIAL.printf("[HTTP] POST /api/command from %s\n",
                              webServer.client().remoteIP().toString().c_str());
        }
        processCommand(body, SRC_HTTP);
    });
    
    // GET /api/status — quick status (includes AP info)
    webServer.on("/api/status", HTTP_GET, []() {
        DynamicJsonDocument doc(2560);
        buildStatusDoc(doc);
        String resp;
        serializeJson(doc, resp);
        webServer.send(200, "application/json", resp);
    });
    
    // GET /api/scan - scan WiFi networks
    webServer.on("/api/scan", HTTP_GET, []() {
        if (!scanAllowed()) {
            webServer.send(403, "application/json",
                "{\"status\":\"error\",\"reply\":\"lan_locked\",\"message\":\""
                "Set an access PIN before scanning over your home network.\"}");
            return;
        }
        if (verboseLog) COM_SERIAL.println("[HTTP] GET /api/scan - scanning networks...");
        handleWifiScan();
    });
    
    // GET /api/ping — lightweight health check
    webServer.on("/api/ping", HTTP_GET, []() {
        webServer.send(200, "application/json",
                      "{\"status\":\"ok\",\"reply\":\"pong\"}");
    });
    
    // 404
    webServer.onNotFound([]() {
        webServer.send(404, "application/json",
                      "{\"status\":\"error\",\"message\":\"Not found. Dashboard at / or API at /api/command\"}");
    });
}

// ==================== WIFI SCAN ====================
// WiFi.scanNetworks() blocks for two to four seconds. That is the same freeze the
// join used to cause: loop() stops, so the web server, the socket heartbeat and
// the pointer stream all stop with it. The scan is started and then polled
// instead, and the dashboard asks again until results appear.
void handleWifiScan() {
    int n = WiFi.scanComplete();   // -1 running, -2 none started, >= 0 result count

    if (n == WIFI_SCAN_RUNNING) {
        webServer.send(200, "application/json",
                      "{\"status\":\"ok\",\"reply\":\"scan\",\"scanning\":true}");
        return;
    }

    if (n == WIFI_SCAN_FAILED) {
        // The radio is shared with the AP and with any join in flight. Scanning
        // on top of an association attempt disturbs it, so refuse politely
        // rather than sabotage the thing the user asked for first.
        if (staPhase == STA_CONNECTING) {
            webServer.send(200, "application/json",
                "{\"status\":\"ok\",\"reply\":\"scan\",\"scanning\":false,\"busy\":true,"
                "\"message\":\"Still joining a network. Scanning will work once that finishes.\"}");
            return;
        }
        WiFi.scanNetworks(true);   // async - returns immediately
        webServer.send(200, "application/json",
                      "{\"status\":\"ok\",\"reply\":\"scan\",\"scanning\":true}");
        return;
    }

    DynamicJsonDocument doc(3072);
    doc["status"] = "ok";
    doc["reply"] = "scan";
    doc["scanning"] = false;
    doc["count"] = n;
    JsonArray networks = doc.createNestedArray("networks");
    
    for (int i = 0; i < n && i < 16; i++) {
        JsonObject net = networks.createNestedObject();
        net["ssid"] = WiFi.SSID(i);
        net["rssi"] = WiFi.RSSI(i);
        net["quality"] = linkQuality(WiFi.RSSI(i));
        net["channel"] = WiFi.channel(i);
        
        switch (WiFi.encryptionType(i)) {
            case WIFI_AUTH_OPEN:           net["encryption"] = "Open"; break;
            case WIFI_AUTH_WEP:            net["encryption"] = "WEP"; break;
            case WIFI_AUTH_WPA_PSK:        net["encryption"] = "WPA"; break;
            case WIFI_AUTH_WPA2_PSK:       net["encryption"] = "WPA2"; break;
            case WIFI_AUTH_WPA_WPA2_PSK:   net["encryption"] = "WPA/WPA2"; break;
            case WIFI_AUTH_WPA2_ENTERPRISE:net["encryption"] = "WPA2-Ent"; break;
            case WIFI_AUTH_WPA3_PSK:       net["encryption"] = "WPA3"; break;
            default:                       net["encryption"] = "Unknown"; break;
        }
    }
    
    // Frees the driver's copy. It also makes the next call start a fresh scan,
    // which is exactly what should happen when the user taps Scan again.
    WiFi.scanDelete();
    
    String resp;
    serializeJson(doc, resp);
    webServer.send(200, "application/json", resp);
}

// ==================== COMMAND DISPATCH ====================
void processCommand(const String& line, CmdSource src) {
    cmdSource = src;
    pinPeer = (src == SRC_WS && wsClientNum >= 0) ? webSocket.remoteIP((uint8_t)wsClientNum)
            : (src == SRC_HTTP) ? webServer.client().remoteIP() : IPAddress((uint32_t)0);
    const bool fromWifi = (src != SRC_SERIAL);

    commandCount++;
    if (fromWifi) wifiCommandCount++;
    
    lastJsonResponse = "";  // clear buffer
    
    StaticJsonDocument<1024> inDoc;
    DeserializationError error = deserializeJson(inDoc, line);
    
    if (error) {
        StaticJsonDocument<256> errDoc;
        errDoc["status"] = "error";
        errDoc["message"] = "Invalid JSON";
        errDoc["parse_error"] = error.c_str();
        if (!fromWifi) errDoc["received"] = line.substring(0, 100);
        
        if (fromWifi) {
            String resp;
            serializeJson(errDoc, resp);
            replyRaw(resp);
        } else {
            sendJson(errDoc);
        }
        return;
    }
    
    const char* cmd = inDoc["cmd"] | "unknown";
    if (!fromWifi || verboseLog) {
        COM_SERIAL.printf("%s cmd=%s\n", fromWifi ? "[WIFI]" : "[UART]", cmd);
    }

    // Exposure gate. Joining a home network changes who can reach this board in a
    // way that is easy to miss: on the access point an attacker has to be in
    // radio range, but on a LAN every device on that network can reach it -
    // including anything on it that is already compromised, and anything a
    // guest brought in. This board types into a logged-in PC, so it is not
    // something to leave open to a whole subnet.
    //
    // Without a PIN, LAN clients are told to set one instead of being served.
    // Read-only and recovery commands stay available so that is actually
    // possible from where they are standing. The access point is unaffected, and
    // USB serial is never gated.
    if (fromWifi && authToken.length() == 0 && wifiConnected) {
        IPAddress rip = (src == SRC_WS && wsClientNum >= 0)
                        ? webSocket.remoteIP((uint8_t)wsClientNum)
                        : webServer.client().remoteIP();
        bool safe = !strcmp(cmd, "status")      || !strcmp(cmd, "ping") ||
                    !strcmp(cmd, "wifi_status") || !strcmp(cmd, "set_auth") ||
                    !strcmp(cmd, "auth")        || !strcmp(cmd, "ui_get");
        if (!safe && !clientIsAp(rip)) {
            StaticJsonDocument<320> doc;
            doc["status"]  = "error";
            doc["reply"]   = "lan_locked";
            doc["message"] = "Set an access PIN before using this board over your home network. "
                             "Every device on that network can reach it, not just people in radio range.";
            sendJson(doc);
            sendWifiResponse();
            return;
        }
    }

    // PIN gate. Only WiFi transports are gated -- the USB serial link is physical
    // access and is what the PC auto-unlock service uses, so it stays open.
    if (fromWifi && authToken.length() && strcmp(cmd, "auth") != 0 && strcmp(cmd, "ping") != 0) {
        bool ok = (src == SRC_WS && wsClientNum >= 0 && wsClientNum < WS_MAX_CLIENTS && wsAuthed[wsClientNum]);
        // An HTTP request may carry the PIN inline. Only count it as an attempt
        // when one was actually supplied, or an ordinary unauthenticated request
        // would lock out the legitimate user.
        if (!ok && src == SRC_HTTP) {
            const char* tok = inDoc["token"] | "";
            if (tok[0]) ok = !pinLocked() && pinCheck(tok);
        }
        if (!ok) {
            StaticJsonDocument<192> doc;
            doc["status"]  = "error";
            doc["reply"]   = "auth_required";
            doc["message"] = "Access PIN required";
            if (pinLocked()) doc["retry_in"] = (int)pinLockRemaining();
            sendJson(doc);
            sendWifiResponse();
            return;
        }
    }
    
    // --- Ping / Status need special handling for direct HTTP response ---
    if (strcmp(cmd, "ping") == 0) {
        handlePing(fromWifi);
        return;
    }
    if (strcmp(cmd, "status") == 0) {
        handleDeviceStatus(fromWifi);
        return;
    }
    
    // --- All other commands: execute, then send buffered response ---
    if (strcmp(cmd, "echo") == 0) {
        handleEcho(inDoc);
    }
    else if (strcmp(cmd, "unlock") == 0) {
        handleUnlock(inDoc);
    }
    else if (strcmp(cmd, "type") == 0) {
        handleType(inDoc);
    }
    else if (strcmp(cmd, "press") == 0) {
        handlePress(inDoc);
    }
    else if (strcmp(cmd, "lock") == 0) {
        handleLock(inDoc);
    }
    else if (strcmp(cmd, "test_hid") == 0) {
        handleTestHid();
    }
    else if (strcmp(cmd, "wifi_configure") == 0) {
        handleWifiConfigure(inDoc);
    }
    else if (strcmp(cmd, "wifi_status") == 0) {
        handleWifiStatus();
    }
    else if (strcmp(cmd, "wifi_disconnect") == 0) {
        handleWifiDisconnect();
    }
    else if (strcmp(cmd, "ap_configure") == 0) {
        handleApConfigure(inDoc);
    }
    else if (strcmp(cmd, "power_mode") == 0) {
        handlePowerMode(inDoc);
    }
    else if (strcmp(cmd, "storage_info") == 0) {
        handleStorageInfo();
    }
    else if (strcmp(cmd, "clients") == 0) {
        handleClients();
    }
    else if (strcmp(cmd, "set_lock_state") == 0) {
        handleSetLockState(inDoc);
    }
    else if (strcmp(cmd, "set_host_os") == 0) {
        handleSetHostOs(inDoc);
    }
    else if (strcmp(cmd, "set_unicode") == 0) {
        handleSetUnicode(inDoc);
    }
    else if (strcmp(cmd, "unicode_test") == 0) {
        handleUnicodeTest(inDoc);
    }
    else if (strcmp(cmd, "wiggle") == 0) {
        handleWiggle(inDoc);
    }
    else if (strcmp(cmd, "wiggle_stop") == 0) {
        handleWiggleStop(inDoc);
    }
    else if (strcmp(cmd, "mouse_move") == 0) {
        handleMouseMove(inDoc);
    }
    else if (strcmp(cmd, "mouse_click") == 0) {
        handleMouseClick(inDoc);
    }
    else if (strcmp(cmd, "mouse_press") == 0) {
        handleMousePress(inDoc);
    }
    else if (strcmp(cmd, "mouse_release") == 0) {
        handleMouseRelease(inDoc);
    }
    else if (strcmp(cmd, "mouse_scroll") == 0) {
        handleMouseScroll(inDoc);
    }
    else if (strcmp(cmd, "led_set") == 0) {
        handleLedSet(inDoc);
    }
    else if (strcmp(cmd, "led_blink") == 0) {
        handleLedBlink(inDoc);
    }
    else if (strcmp(cmd, "led_status") == 0) {
        handleLedStatus();
    }
    else if (strcmp(cmd, "media") == 0) {
        handleMedia(inDoc);
    }
    else if (strcmp(cmd, "system") == 0) {
        handleSystemCtl(inDoc);
    }
    else if (strcmp(cmd, "gesture") == 0) {
        handleGesture(inDoc);
    }
    else if (strcmp(cmd, "presenter") == 0) {
        handlePresenter(inDoc);
    }
    else if (strcmp(cmd, "mouse_abs") == 0) {
        handleMouseAbs(inDoc);
    }
    else if (strcmp(cmd, "pointer_mode") == 0) {
        handlePointerMode(inDoc);
    }
    else if (strcmp(cmd, "gamepad_enable") == 0) {
        handleGamepadEnable(inDoc);
    }
    else if (strcmp(cmd, "gamepad") == 0) {
        handleGamepadCmd(inDoc);
    }
    else if (strcmp(cmd, "auth") == 0) {
        handleAuth(inDoc);
    }
    else if (strcmp(cmd, "set_auth") == 0) {
        handleSetAuth(inDoc);
    }
    else if (strcmp(cmd, "ap_password") == 0) {
        handleApPassword();
    }
    else if (strcmp(cmd, "set_verbose") == 0) {
        handleSetVerbose(inDoc);
    }
    else if (strcmp(cmd, "macro_save") == 0) {
        handleMacroSave(inDoc);
    }
    else if (strcmp(cmd, "macro_get") == 0) {
        handleMacroGet(inDoc);
    }
    else if (strcmp(cmd, "macro_list") == 0) {
        handleMacroList();
    }
    else if (strcmp(cmd, "macro_run") == 0) {
        handleMacroRun(inDoc);
    }
    else if (strcmp(cmd, "do") == 0)      handleDo(inDoc);
    else if (strcmp(cmd, "net_fault") == 0) handleNetFault(inDoc);
    else if (strcmp(cmd, "wifi_networks") == 0) handleWifiNetworks();
    else if (strcmp(cmd, "wifi_forget") == 0) handleWifiForget(inDoc);
    else if (strcmp(cmd, "internet_share") == 0) handleInternetShare(inDoc);
    else if (strcmp(cmd, "actions") == 0) handleActions(inDoc);
    else if (strcmp(cmd, "open") == 0)    handleOpen(inDoc);
    else if (strcmp(cmd, "steps") == 0)   handleSteps(inDoc);
    else if (strcmp(cmd, "macro_delete") == 0) {
        handleMacroDelete(inDoc);
    }
    else if (strcmp(cmd, "ui_save") == 0) {
        handleUiSave(inDoc);
    }
    else if (strcmp(cmd, "ui_get") == 0) {
        handleUiGet();
    }
    else if (strcmp(cmd, "key_down") == 0) {
        handleKeyDown(inDoc);
    }
    else if (strcmp(cmd, "key_up") == 0) {
        handleKeyUp(inDoc);
    }
    else if (strcmp(cmd, "key_release_all") == 0) {
        handleKeyReleaseAll();
    }
    else if (strcmp(cmd, "pc_save") == 0) {
        handlePcSave(inDoc);
    }
    else if (strcmp(cmd, "pc_list") == 0) {
        handlePcList();
    }
    else if (strcmp(cmd, "pc_delete") == 0) {
        handlePcDelete(inDoc);
    }
    else if (strcmp(cmd, "vault_list") == 0) {
        handleVaultList();
    }
    else if (strcmp(cmd, "vault_save") == 0) {
        handleVaultSave(inDoc);
    }
    else if (strcmp(cmd, "vault_get") == 0) {
        handleVaultGet(inDoc);
    }
    else if (strcmp(cmd, "vault_type") == 0) {
        handleVaultType(inDoc);
    }
    else if (strcmp(cmd, "vault_delete") == 0) {
        handleVaultDelete(inDoc);
    }
    else if (strcmp(cmd, "vault_wipe") == 0) {
        handleVaultWipe();
    }
    else if (strcmp(cmd, "vault_pin") == 0) {
        handleVaultPin(inDoc);
    }
    else {
        StaticJsonDocument<256> doc;
        doc["status"] = "error";
        doc["message"] = "Unknown command";
        doc["cmd"] = cmd;
        sendJson(doc);
    }
    
    // If WiFi request, send buffered response
    if (fromWifi) {
        sendWifiResponse();
    }
}

// ==================== RESPONSE HELPERS ====================
void sendJson(const JsonDocument& doc) {
    String output;
    serializeJson(doc, output);
    lastJsonResponse = output;        // buffer for the WiFi reply
    // Echo only what the serial user asked for. A ~70 byte line costs ~6 ms at
    // 115200, which would otherwise be paid on every WiFi pointer command.
    if (cmdSource == SRC_SERIAL || verboseLog) COM_SERIAL.println(output);
}

// Taken by value: WebSocketsServer::sendTXT wants a non-const String&.
void replyRaw(String out) {
    if (cmdSource == SRC_WS) {
        if (wsClientNum >= 0) webSocket.sendTXT((uint8_t)wsClientNum, out);
    } else if (cmdSource == SRC_HTTP) {
        webServer.send(200, "application/json", out);
    }
}

void sendWifiResponse() {
    replyRaw(lastJsonResponse.length() > 0 ? lastJsonResponse : String("{\"status\":\"ok\"}"));
}

// ==================== UNLOCK ====================
void handleUnlock(const JsonDocument& inDoc) {
    if (!hidReady) {
        StaticJsonDocument<192> doc;
        doc["status"] = "error";
        doc["message"] = "USB HID not initialized";
        doc["pc_state"] = lockStateStr();
        sendJson(doc);
        return;
    }
    
    const char* password = inDoc["password"] | "";
    // A saved profile can stand in for a typed password. The password itself is
    // only ever read out of NVS here, never sent anywhere.
    String pwBuf(password);
    int profile = inDoc["profile"] | -1;
    if (pwBuf.length() == 0 && profile >= 0) pwBuf = pcPassword(profile);
    password = pwBuf.c_str();
    if (pwBuf.length() == 0) {
        StaticJsonDocument<192> doc;
        doc["status"] = "error";
        doc["message"] = "Password is required";
        doc["pc_state"] = lockStateStr();
        sendJson(doc);
        return;
    }
    
    bool force = inDoc["force"] | false;
    
    if (!force && pcLockState == PC_STATE_UNLOCKED) {
        COM_SERIAL.println("[UNLOCK] PC already unlocked — skipping");
        StaticJsonDocument<192> doc;
        doc["status"] = "ok";
        doc["reply"] = "already_unlocked";
        doc["pc_state"] = "unlocked";
        doc["message"] = "PC is already in unlocked state";
        sendJson(doc);
        return;
    }
    
    bool useCtrlAltDel = inDoc["ctrl_alt_del"] | false;
    // A saved slot carries the OS of the computer it belongs to, which is not
    // always the one attached now. Esc into a Mac login window collapses the
    // password field, so getting this from the slot rather than from detection
    // is what makes a board that travels between machines safe to use.
    HostOS slotOs = (profile >= 0) ? pcOs(profile) : OS_UNKNOWN;
    bool mac = (slotOs != OS_UNKNOWN) ? (slotOs == OS_MAC) : hostIsMac();
    // macOS is fussier than Windows here: pressing Esc at the login window collapses
    // the password field, and the field can take longer to appear and take focus,
    // so a Mac gets a gentler wake and a longer wait by default.
    int defWake = useCtrlAltDel ? CAD_DELAY : (mac ? 2500 : WAKE_DELAY);
    int wakeDelay = inDoc["wake_delay"] | defWake;
    int postTypeDelay = inDoc["post_type_delay"] | POST_TYPE_DELAY;

    COM_SERIAL.printf("[UNLOCK] Starting unlock sequence (os=%s prev_state=%s)...\n",
                      hostOsName(slotOs != OS_UNKNOWN ? slotOs : hostOs()), lockStateStr());

    // Step 1: Wake
    if (useCtrlAltDel) {
        COM_SERIAL.println("[UNLOCK] Step 1: Ctrl+Alt+Delete");
        pressKeyCombo("CTRL+ALT+DELETE");
    } else if (mac) {
        // Esc would dismiss the Mac login field. A net-zero mouse jiggle wakes the
        // display without cancelling anything, then a Shift tap surfaces the
        // password field and focuses it, ready for typing.
        COM_SERIAL.println("[UNLOCK] Step 1: jiggle + Shift to wake the Mac login window");
        usbWakeHost();
        hidMouseMove(4, 0, 0, 0);  delay(60);
        hidMouseMove(-4, 0, 0, 0); delay(60);
        Keyboard.press(KEY_LEFT_SHIFT); delay(60); Keyboard.releaseAll();
    } else {
        COM_SERIAL.println("[UNLOCK] Step 1: Esc to wake/clear");
        Keyboard.press(KEY_ESC);
        delay(50);
        Keyboard.releaseAll();
    }
    
    // Step 2: Wait for password field
    COM_SERIAL.printf("[UNLOCK] Step 2: Waiting %dms\n", wakeDelay);
    delay(wakeDelay);
    
    // Step 3: Type password
    COM_SERIAL.printf("[UNLOCK] Step 3: Typing (%d chars)\n", strlen(password));
    typeString(password);
    
    // Step 4: Enter
    delay(postTypeDelay);
    COM_SERIAL.println("[UNLOCK] Step 4: Enter");
    Keyboard.press(KEY_RETURN);
    delay(50);
    Keyboard.releaseAll();
    
    pcLockState = PC_STATE_UNLOCKED;
    COM_SERIAL.println("[UNLOCK] Done — state set to unlocked");
    
    StaticJsonDocument<384> doc;
    doc["status"] = "ok";
    doc["reply"] = "unlock_done";
    doc["pc_state"] = "unlocked";
    doc["note"] = "State set optimistically - ESP32 cannot verify password acceptance";
    JsonArray steps = doc.createNestedArray("steps");
    steps.add(useCtrlAltDel ? "ctrl_alt_del" : "wake");
    steps.add("wait");
    steps.add("type");
    steps.add("enter");
    sendJson(doc);
}

// ==================== TYPE ====================
void handleType(const JsonDocument& inDoc) {
    if (!hidReady) {
        StaticJsonDocument<128> doc;
        doc["status"] = "error";
        doc["message"] = "USB HID not initialized";
        sendJson(doc);
        return;
    }
    
    const char* text = inDoc["text"] | "";
    if (strlen(text) == 0) {
        StaticJsonDocument<128> doc;
        doc["status"] = "error";
        doc["message"] = "No text provided";
        sendJson(doc);
        return;
    }
    
    bool pressEnter = inDoc["enter"] | false;
    
    COM_SERIAL.printf("[TYPE] Typing %d characters\n", strlen(text));
    int skipped = typeString(text);
    
    if (pressEnter) {
        delay(100);
        Keyboard.press(KEY_RETURN);
        delay(50);
        Keyboard.releaseAll();
    }
    
    StaticJsonDocument<256> doc;
    doc["status"] = "ok";
    doc["reply"] = "typed";
    doc["length"] = strlen(text);
    doc["skipped"] = skipped;
    doc["enter"] = pressEnter;
    sendJson(doc);
}

// ==================== PRESS ====================
void handlePress(const JsonDocument& inDoc) {
    if (!hidReady) {
        StaticJsonDocument<128> doc;
        doc["status"] = "error";
        doc["message"] = "USB HID not initialized";
        sendJson(doc);
        return;
    }
    
    const char* keys = inDoc["keys"] | "";
    if (strlen(keys) == 0) {
        StaticJsonDocument<128> doc;
        doc["status"] = "error";
        doc["message"] = "No keys specified";
        sendJson(doc);
        return;
    }
    
    COM_SERIAL.printf("[PRESS] Pressing: %s\n", keys);
    pressKeyCombo(keys);
    
    StaticJsonDocument<256> doc;
    doc["status"] = "ok";
    doc["reply"] = "pressed";
    doc["keys"] = keys;
    sendJson(doc);
}

// ==================== KEY COMBO PARSER ====================
// Two-pass: modifiers first (with delay), then regular keys.
void pressKeyCombo(const char* comboStr) {
    usbWakeHost();
    String combo = String(comboStr);
    combo.toUpperCase();
    
    String tokens[8];
    int tokenCount = 0;
    int startIdx = 0;
    
    while (startIdx <= (int)combo.length() && tokenCount < 8) {
        int plusIdx = combo.indexOf('+', startIdx);
        if (plusIdx == -1) {
            tokens[tokenCount] = combo.substring(startIdx);
            tokens[tokenCount].trim();
            if (tokens[tokenCount].length() > 0) tokenCount++;
            break;
        } else {
            tokens[tokenCount] = combo.substring(startIdx, plusIdx);
            tokens[tokenCount].trim();
            if (tokens[tokenCount].length() > 0) tokenCount++;
            startIdx = plusIdx + 1;
        }
    }
    
    // Pass 1 — modifiers (0x80–0x87)
    bool hasModifier = false;
    for (int i = 0; i < tokenCount; i++) {
        uint8_t keyCode = mapKeyName(tokens[i]);
        if (keyCode >= 0x80 && keyCode <= 0x87) {
            Keyboard.press(keyCode);
            hasModifier = true;
        }
    }
    if (hasModifier) delay(30);  // host must see modifier in its own HID report
    
    // Pass 2 — regular keys
    for (int i = 0; i < tokenCount; i++) {
        uint8_t keyCode = mapKeyName(tokens[i]);
        if (keyCode != 0 && !(keyCode >= 0x80 && keyCode <= 0x87)) {
            Keyboard.press(keyCode);
        } else if (keyCode == 0 && tokens[i].length() == 1) {
            char c = tokens[i].charAt(0);
            if (c >= 'A' && c <= 'Z') c += 32;  // lowercase for HID
            if ((uint8_t)c < 0x80) Keyboard.press(c);  // >= 0x80 is a raw keycode to press()
        }
    }
    
    delay(120);  // hold combo
    Keyboard.releaseAll();
}

uint8_t mapKeyName(const String& name) {
    // Modifiers
    if (name == "CTRL" || name == "CONTROL")    return KEY_LEFT_CTRL;
    if (name == "ALT" || name == "OPTION")      return KEY_LEFT_ALT;
    if (name == "SHIFT")                        return KEY_LEFT_SHIFT;
    if (name == "GUI" || name == "WIN" || name == "WINDOWS" || name == "META"
        || name == "CMD" || name == "COMMAND" || name == "SUPER")
                                                return KEY_LEFT_GUI;
    // Right-hand modifiers. pressKeyCombo treats 0x80-0x87 as modifiers, and
    // these land in that range, so they chord correctly. ALTGR is Right Alt.
    if (name == "RCTRL"  || name == "RIGHTCTRL")  return KEY_RIGHT_CTRL;
    if (name == "RSHIFT" || name == "RIGHTSHIFT") return KEY_RIGHT_SHIFT;
    if (name == "RALT"   || name == "ALTGR")      return KEY_RIGHT_ALT;
    if (name == "RGUI"   || name == "RIGHTGUI")   return KEY_RIGHT_GUI;
    // Common keys
    if (name == "ENTER" || name == "RETURN")    return KEY_RETURN;
    if (name == "ESC" || name == "ESCAPE")      return KEY_ESC;
    if (name == "TAB")                          return KEY_TAB;
    if (name == "SPACE")                        return ' ';
    if (name == "BACKSPACE" || name == "BS")    return KEY_BACKSPACE;
    if (name == "DELETE" || name == "DEL")       return KEY_DELETE;
    if (name == "INSERT" || name == "INS")       return KEY_INSERT;
    if (name == "HOME")                         return KEY_HOME;
    if (name == "END")                          return KEY_END;
    if (name == "PAGEUP" || name == "PGUP")     return KEY_PAGE_UP;
    if (name == "PAGEDOWN" || name == "PGDN")   return KEY_PAGE_DOWN;
    if (name == "CAPSLOCK" || name == "CAPS")   return KEY_CAPS_LOCK;
    // Arrow keys
    if (name == "UP")                           return KEY_UP_ARROW;
    if (name == "DOWN")                         return KEY_DOWN_ARROW;
    if (name == "LEFT")                         return KEY_LEFT_ARROW;
    if (name == "RIGHT")                        return KEY_RIGHT_ARROW;
    // Function keys
    if (name == "F1")  return KEY_F1;   if (name == "F2")  return KEY_F2;
    if (name == "F3")  return KEY_F3;   if (name == "F4")  return KEY_F4;
    if (name == "F5")  return KEY_F5;   if (name == "F6")  return KEY_F6;
    if (name == "F7")  return KEY_F7;   if (name == "F8")  return KEY_F8;
    if (name == "F9")  return KEY_F9;   if (name == "F10") return KEY_F10;
    if (name == "F11") return KEY_F11;  if (name == "F12") return KEY_F12;
    // System keys used by real shortcuts: PrtSc is how both Windows and Linux
    // take a screenshot, Menu is the context-menu key, Pause is Win+Pause.
    if (name == "PRINTSCREEN" || name == "PRTSC" || name == "PRTSCN" || name == "SYSRQ")
                                                return KEY_PRINT_SCREEN;
    if (name == "MENU" || name == "APPS" || name == "CONTEXT")
                                                return KEY_MENU;
    if (name == "PAUSE" || name == "BREAK")     return KEY_PAUSE;
    if (name == "SCROLLLOCK")                   return KEY_SCROLL_LOCK;
    if (name == "NUMLOCK")                      return KEY_NUM_LOCK;
    // '+' cannot survive the tokeniser, which splits on it, so it needs a name.
    // The keypad one is what browsers accept for zoom in.
    if (name == "PLUS")                         return KEY_KP_PLUS;
    if (name == "MINUS")                        return '-';
    if (name == "EQUALS" || name == "EQUAL")    return '=';
    // Keypad. These are distinct HID usages from the number row, and a real
    // numpad has to send them: applications that separate the two (Excel, CAD,
    // games bound to KP keys) see a different key. Without a name here they
    // would fall through hidKeyFor()'s single-character path and send the
    // top-row digit instead, which looks right and is not.
    if (name == "KP0") return KEY_KP_0;   if (name == "KP1") return KEY_KP_1;
    if (name == "KP2") return KEY_KP_2;   if (name == "KP3") return KEY_KP_3;
    if (name == "KP4") return KEY_KP_4;   if (name == "KP5") return KEY_KP_5;
    if (name == "KP6") return KEY_KP_6;   if (name == "KP7") return KEY_KP_7;
    if (name == "KP8") return KEY_KP_8;   if (name == "KP9") return KEY_KP_9;
    if (name == "KPDOT" || name == "KPPERIOD")  return KEY_KP_DOT;
    if (name == "KPPLUS")                       return KEY_KP_PLUS;
    if (name == "KPMINUS")                      return KEY_KP_MINUS;
    if (name == "KPSTAR" || name == "KPASTERISK") return KEY_KP_ASTERISK;
    if (name == "KPSLASH")                      return KEY_KP_SLASH;
    if (name == "KPENTER")                      return KEY_KP_ENTER;
    return 0;
}

// ==================== TYPE STRING ====================
// Text arrives as UTF-8, but Keyboard.press() reads every byte >= 0x88 as a raw
// HID keycode and 0x80-0x87 as a modifier. Fed a phone's smart quote (E2 80 99)
// it typed Keypad-2, tapped Ctrl, then 'n' - and a degree sign (C2 B0) pressed
// F1 then Enter. So no byte >= 0x80 may ever reach press(): decode the text,
// fold the punctuation phone keyboards substitute on their own back to the
// ASCII the user meant, and skip anything a US layout cannot produce.
static const char* asciiFold(uint32_t cp) {
    switch (cp) {
        case 0x00A0: case 0x2002: case 0x2003: case 0x2004: case 0x2005:
        case 0x2006: case 0x2007: case 0x2008: case 0x2009: case 0x200A:
        case 0x202F: case 0x205F: case 0x3000:
            return " ";
        case 0x2018: case 0x2019: case 0x201A: case 0x201B:
        case 0x2032: case 0x02BC: case 0x00B4:
            return "'";
        case 0x201C: case 0x201D: case 0x201E: case 0x201F:
        case 0x2033: case 0x00AB: case 0x00BB:
            return "\"";
        case 0x2010: case 0x2011: case 0x2012: case 0x2013:
        case 0x2014: case 0x2015: case 0x2212:
            return "-";
        case 0x2026:
            return "...";
        case 0x200B: case 0x200C: case 0x200D: case 0x2060: case 0xFEFF:
            return "";                    // invisible - nothing to type, nothing lost
        default:
            return nullptr;
    }
}

static void typeAscii(char c) {
    Keyboard.press(c);
    delay(CHAR_DELAY);
    Keyboard.release(c);
    delay(CHAR_DELAY);
}

// Unicode entry through the host's own input method. Only macOS and Linux have
// one that needs no software installed: Windows' Alt+numpad hex is capped at 16
// bits (no emoji) and off unless a registry value is set, so it is not used.
//   macOS  - "Unicode Hex Input" selected: hold Option, type UTF-16 hex. Code
//            points above U+FFFF go in as a surrogate pair under one Option hold.
//   Linux  - IBus/GTK: Ctrl+Shift+U, hex, Space.
// A manual OS pin is saved on the board and travels with it, so "pinned to
// mac" can be plugged into a PC. There, Option is Alt: Alt+F opens menus and
// Alt+D jumps to the address bar, so the hex digits would fire commands. What
// enumeration actually saw overrides the pin for this one purpose. Windows is
// always detected (it syncs the lock LEDs at enumeration); Linux enumerates as
// Windows, so a Linux pin is only vetoed by a detected Mac.
static bool unicodeSafeOn(HostOS o) {
    if (o == OS_MAC)   return hostOsDetected != OS_WINDOWS && hostOsDetected != OS_LINUX;
    if (o == OS_LINUX) return hostOsDetected != OS_MAC;
    return false;
}

static bool unicodeReady() {
    HostOS o = hostOs();
    return (o == OS_MAC || o == OS_LINUX) && (unicodeHosts & (1 << o)) && unicodeSafeOn(o);
}

static void typeHex(uint32_t v, int digits) {
    static const char hx[] = "0123456789abcdef";
    for (int d = digits - 1; d >= 0; d--) typeAscii(hx[(v >> (4 * d)) & 0xF]);
}

static void typeUnicode(uint32_t cp, HostOS os) {
    if (os == OS_MAC) {
        Keyboard.press(KEY_LEFT_ALT);
        delay(CHAR_DELAY);
        if (cp > 0xFFFF) {
            uint32_t v = cp - 0x10000;
            typeHex(0xD800 + (v >> 10), 4);
            typeHex(0xDC00 + (v & 0x3FF), 4);
        } else {
            typeHex(cp, 4);
        }
        Keyboard.release(KEY_LEFT_ALT);   // the character commits on release
        delay(CHAR_DELAY);
    } else {
        // Caps Lock stops IBus taking Ctrl+Shift+U, so lift it for the entry
        // and put it back (QMK does the same). Release only what was pressed
        // here - releaseAll() would also drop a key the user is holding.
        bool caps = ledStateBits & 0x02;
        if (caps) { Keyboard.press(KEY_CAPS_LOCK); delay(CHAR_DELAY); Keyboard.release(KEY_CAPS_LOCK); delay(CHAR_DELAY); }
        Keyboard.press(KEY_LEFT_CTRL);
        Keyboard.press(KEY_LEFT_SHIFT);
        Keyboard.press('u');
        delay(CHAR_DELAY);
        Keyboard.release('u');
        Keyboard.release(KEY_LEFT_SHIFT);
        Keyboard.release(KEY_LEFT_CTRL);
        delay(CHAR_DELAY);
        typeHex(cp, cp > 0xFFFF ? (cp > 0xFFFFF ? 6 : 5) : 4);
        typeAscii(' ');                   // commits the character
        if (caps) { Keyboard.press(KEY_CAPS_LOCK); delay(CHAR_DELAY); Keyboard.release(KEY_CAPS_LOCK); delay(CHAR_DELAY); }
    }
}

// Returns how many characters could not be typed and were skipped.
int typeString(const char* text) {
    usbWakeHost();
    int skipped = 0;
    const uint8_t* s = (const uint8_t*)text;
    size_t i = 0;
    while (s[i]) {
        uint8_t c = s[i];
        if (c < 0x80) { typeAscii((char)c); i++; continue; }

        // Well-formed UTF-8 only (Unicode Table 3-7). The bounds on the second
        // byte reject overlong forms, UTF-16 surrogates and anything past
        // U+10FFFF - JSON can carry a lone "\ud800", and typed through a
        // host's hex entry it would land as a broken character. Each
        // ill-formed run counts once, as its maximal subpart.
        size_t n; uint32_t cp; uint8_t lo = 0x80, hi = 0xBF;
        if      (c >= 0xC2 && c <= 0xDF) { n = 1; cp = c & 0x1F; }
        else if (c >= 0xE0 && c <= 0xEF) { n = 2; cp = c & 0x0F;
                                           if (c == 0xE0) lo = 0xA0;
                                           if (c == 0xED) hi = 0x9F; }
        else if (c >= 0xF0 && c <= 0xF4) { n = 3; cp = c & 0x07;
                                           if (c == 0xF0) lo = 0x90;
                                           if (c == 0xF4) hi = 0x8F; }
        else { skipped++; i++; continue; }          // C0, C1, F5-FF, stray continuation
        size_t k = 1;
        for (; k <= n; k++) {
            uint8_t b = s[i + k];
            if (b < lo || b > hi) break;            // also stops at the NUL
            cp = (cp << 6) | (b & 0x3F);
            lo = 0x80; hi = 0xBF;
        }
        if (k <= n) { skipped++; i += k; continue; } // ill-formed or truncated
        i += n + 1;

        // The host can take the real character, so give it exactly what was
        // typed - curly quotes included - rather than an ASCII stand-in.
        if (unicodeReady()) { typeUnicode(cp, hostOs()); continue; }

        const char* fold = asciiFold(cp);
        if (!fold) { skipped++; continue; }
        for (; *fold; fold++) typeAscii(*fold);
    }
    return skipped;
}

// ==================== LOCK ====================
void handleLock(const JsonDocument& inDoc) {
    if (!hidReady) {
        StaticJsonDocument<192> doc;
        doc["status"] = "error";
        doc["message"] = "USB HID not initialized";
        doc["pc_state"] = lockStateStr();
        sendJson(doc);
        return;
    }
    
    bool force = inDoc["force"] | false;
    
    if (!force && pcLockState == PC_STATE_LOCKED) {
        COM_SERIAL.println("[LOCK] PC already locked — skipping");
        StaticJsonDocument<192> doc;
        doc["status"] = "ok";
        doc["reply"] = "already_locked";
        doc["pc_state"] = "locked";
        doc["message"] = "PC is already in locked state";
        sendJson(doc);
        return;
    }
    
    // Win+L locks Windows and most Linux desktops; macOS uses Control+Command+Q.
    // Sent through pressKeyCombo so it also wakes a suspended bus first.
    const char* combo = hostIsMac() ? "CTRL+GUI+Q" : "GUI+L";
    COM_SERIAL.printf("[LOCK] Sending %s for %s (prev_state=%s)...\n",
                      combo, hostOsName(hostOs()), lockStateStr());
    pressKeyCombo(combo);

    pcLockState = PC_STATE_LOCKED;

    StaticJsonDocument<192> doc;
    doc["status"] = "ok";
    doc["reply"] = "locked";
    doc["pc_state"] = "locked";
    doc["combo"] = combo;
    sendJson(doc);
}

// ==================== SET LOCK STATE ====================
void handleSetLockState(const JsonDocument& inDoc) {
    const char* state = inDoc["state"] | "";
    
    if (strcmp(state, "locked") == 0) {
        pcLockState = PC_STATE_LOCKED;
    } else if (strcmp(state, "unlocked") == 0) {
        pcLockState = PC_STATE_UNLOCKED;
    } else if (strcmp(state, "unknown") == 0) {
        pcLockState = PC_STATE_UNKNOWN;
    } else {
        StaticJsonDocument<192> doc;
        doc["status"] = "error";
        doc["message"] = "Invalid state. Use: locked, unlocked, or unknown";
        doc["pc_state"] = lockStateStr();
        sendJson(doc);
        return;
    }
    
    COM_SERIAL.printf("[STATE] PC lock state manually set to: %s\n", lockStateStr());

    StaticJsonDocument<192> doc;
    doc["status"] = "ok";
    doc["reply"] = "state_set";
    doc["pc_state"] = lockStateStr();
    sendJson(doc);
}

// ==================== SET HOST OS ====================
// "mac" / "windows" / "linux" pin the OS by hand and persist it; "auto" clears
// the override and re-arms detection. The manual choice is what makes the feature
// trustworthy: detection cannot tell Linux from Windows, and a user who knows
// better must always be able to correct it.
void handleSetHostOs(const JsonDocument& inDoc) {
    String os = String(inDoc["os"] | "");
    os.toLowerCase();

    if (os == "auto") {
        hostOsManual = OS_UNKNOWN;
        hostOsDetected = OS_UNKNOWN;
        // Re-judge this enumeration rather than baselining the counters away. The
        // host has not changed and it only reads our descriptors once, when it
        // first sets the keyboard up - zeroing the evidence here would guarantee
        // an "undetermined" verdict until the cable was physically replugged.
        detectAt    = millis();
        detectPhase = DET_WATCHING;
    } else if (os == "mac" || os == "macos") {
        hostOsManual = OS_MAC;
    } else if (os == "windows" || os == "win") {
        hostOsManual = OS_WINDOWS;
    } else if (os == "linux") {
        hostOsManual = OS_LINUX;
    } else {
        StaticJsonDocument<192> doc;
        doc["status"] = "error";
        doc["message"] = "Invalid os. Use: mac, windows, linux, or auto";
        doc["host_os"] = hostOsName(hostOs());
        sendJson(doc);
        return;
    }

    preferences.begin("hid_cfg", false);
    preferences.putUChar("os", (uint8_t)hostOsManual);
    preferences.end();

    COM_SERIAL.printf("[OS] Host OS set to %s (source=%s)\n", hostOsName(hostOs()), hostOsSource());

    StaticJsonDocument<192> doc;
    doc["status"] = "ok";
    doc["reply"] = "host_os_set";
    doc["host_os"] = hostOsName(hostOs());
    doc["host_os_source"] = hostOsSource();
    doc["host_os_detected"] = hostOsName(hostOsDetected);
    sendJson(doc);
}

// {"cmd":"set_unicode","os":"mac","on":true} - the user has set this OS up for
// Unicode typing. "os" defaults to the computer the board is plugged into.
void handleSetUnicode(const JsonDocument& inDoc) {
    String os = String(inDoc["os"] | hostOsName(hostOs()));
    os.toLowerCase();
    HostOS o = (os == "mac" || os == "macos") ? OS_MAC : (os == "linux") ? OS_LINUX : OS_UNKNOWN;
    StaticJsonDocument<192> doc;
    if (o == OS_UNKNOWN) {
        doc["status"] = "error";
        doc["message"] = "Unicode typing works on mac or linux only";
        sendJson(doc);
        return;
    }
    if (inDoc["on"] | false) unicodeHosts |=  (1 << o);
    else                     unicodeHosts &= ~(1 << o);
    preferences.begin("hid_cfg", false);
    preferences.putUChar("uni", unicodeHosts);
    preferences.end();
    doc["status"] = "ok";
    doc["reply"] = "unicode_set";
    doc["unicode_mac"]   = (bool)(unicodeHosts & (1 << OS_MAC));
    doc["unicode_linux"] = (bool)(unicodeHosts & (1 << OS_LINUX));
    sendJson(doc);
}

// {"cmd":"unicode_test","os":"mac"} - type a known sample through that OS's
// Unicode entry whether or not it is switched on, so the user can see for
// themselves if the computer is set up. HID cannot read the answer back, so
// the dashboard asks the user, and only their yes turns the setting on.
#define UNICODE_TEST_SAMPLE "\xC3\xA9\xF0\x9F\x98\x80"   // e-acute, then U+1F600
void handleUnicodeTest(const JsonDocument& inDoc) {
    StaticJsonDocument<192> doc;
    if (!hidReady) {
        doc["status"] = "error"; doc["message"] = "USB HID not initialized"; sendJson(doc); return;
    }
    String os = String(inDoc["os"] | hostOsName(hostOs()));
    os.toLowerCase();
    HostOS o = (os == "mac" || os == "macos") ? OS_MAC : (os == "linux") ? OS_LINUX : OS_UNKNOWN;
    if (o == OS_UNKNOWN) {
        doc["status"] = "error"; doc["message"] = "Unicode typing works on mac or linux only"; sendJson(doc); return;
    }
    if (!unicodeSafeOn(o)) {
        doc["status"] = "error";
        doc["message"] = o == OS_MAC ? "This computer looks like a PC - the macOS test would press Alt shortcuts here, so it was not sent"
                                     : "This computer looks like a Mac - the Linux test was not sent";
        sendJson(doc); return;
    }
    usbWakeHost();
    typeUnicode(0x00E9, o);
    typeUnicode(0x1F600, o);
    doc["status"] = "ok";
    doc["reply"] = "unicode_test_sent";
    doc["os"] = hostOsName(o);
    doc["sample"] = UNICODE_TEST_SAMPLE;
    sendJson(doc);
}

// ==================== TEST HID ====================
void handleTestHid() {
    if (!hidReady) {
        StaticJsonDocument<128> doc;
        doc["status"] = "error";
        doc["message"] = "USB HID not initialized";
        sendJson(doc);
        return;
    }
    
    const char* testStr = "HID_OK";
    typeString(testStr);
    
    StaticJsonDocument<256> doc;
    doc["status"] = "ok";
    doc["reply"] = "test_hid";
    doc["typed"] = testStr;
    sendJson(doc);
}

// ==================== PING / STATUS / ECHO ====================
void handlePing(bool fromWifi) {
    StaticJsonDocument<320> doc;
    doc["status"] = "ok";
    doc["reply"] = "pong";
    doc["uptime"] = millis() / 1000;
    doc["hid_ready"] = hidReady;
    doc["wifi_connected"] = wifiConnected;
    doc["cmd_count"] = commandCount;
    doc["pc_state"] = lockStateStr();
    doc["wiggle_active"] = (wiggleMode != WIGGLE_NONE);
    
    String output;
    serializeJson(doc, output);
    if (!fromWifi || verboseLog) COM_SERIAL.println(output);
    
    if (fromWifi) replyRaw(output);
}

// Single source of truth for the status payload: /api/status, the `status`
// command and the dashboard all read the same fields.
void buildStatusDoc(JsonDocument& doc) {
    doc["status"] = "ok";
    doc["reply"] = "status";
    doc["chip"] = "ESP32-S3";
    doc["firmware"] = FW_VERSION;
    doc["uptime_sec"] = millis() / 1000;
    doc["free_heap"] = ESP.getFreeHeap();
    doc["heap_floor"] = ESP.getMinFreeHeap();
    doc["heap_low"] = (ESP.getMinFreeHeap() < HEAP_WARN);
    doc["total_psram"] = ESP.getPsramSize();
    doc["free_psram"] = ESP.getFreePsram();
    doc["flash_size"] = ESP.getFlashChipSize();
    doc["hid_ready"] = hidReady;
    doc["usb_mounted"] = tud_mounted();
    doc["usb_suspended"] = tud_suspended();
    // Power. radio_awake is the honest answer to "why does it feel slow".
    doc["power_mode"] = powerModeStr();
    doc["radio_awake"] = radioAwake;
    doc["ap_auto_off"] = apAutoOff;
    doc["ap_running"] = apRunning;
    doc["ap_share"] = apShare;
    doc["ap_share_active"] = apShareActive;
    // Exposure. lan_open true means the board is on a home network with no PIN,
    // which the dashboard turns into a warning rather than leaving it implicit.
    doc["lan_exposed"] = (wifiConnected && authToken.length() == 0);
    // Everything the dashboard needs to narrate a join as it happens.
    doc["sta_phase"] = staPhaseStr();
    doc["sta_error"] = staError;
    doc["sta_ip_mode"] = staIpMode;
    doc["sta_target"] = staSsid;
    if (staPhase == STA_CONNECTING) {
        uint32_t spent = millis() - staPhaseAt;
        doc["sta_elapsed_ms"] = spent;
        doc["sta_timeout_ms"] = WIFI_CONNECT_TIMEOUT;
    }
    doc["cmd_count"] = commandCount;
    doc["wifi_cmd_count"] = wifiCommandCount;
    doc["wifi_connected"] = wifiConnected;
    doc["wifi_ip"] = wifiIP;
    // How the home network has behaved: enough to answer "did it drop, and where
    // did it go" without a serial log.
    doc["sta_last_ip"] = staLastIp;
    if (staPrevIp.length()) doc["sta_prev_ip"] = staPrevIp;
    doc["sta_ip_changes"] = staIpChanges;
    doc["sta_known"] = netCount;
    doc["sta_away"] = staAway && !wifiConnected;
    doc["sta_scans"] = staScans;
    doc["sta_drops"] = staDrops;
    if (staDownSince) doc["sta_down_s"] = (millis() - staDownSince) / 1000;
    if (staRetryAt && !wifiConnected) doc["sta_retry_in_s"] = (long)(staRetryAt - millis()) > 0 ? (staRetryAt - millis()) / 1000 : 0;
    if (staLastReason && !wifiConnected) doc["sta_reason"] = WiFi.STA.disconnectReasonName((wifi_err_reason_t)staLastReason);
    doc["wifi_ssid"] = wifiConnected ? WiFi.SSID() : "";
    doc["wifi_rssi"] = wifiConnected ? WiFi.RSSI() : 0;
    doc["wifi_quality"] = wifiConnected ? linkQuality(WiFi.RSSI()) : "";
    doc["ap_ssid"] = apSSID;
    doc["ap_ip"] = apIP;
    doc["ap_clients"] = WiFi.softAPgetStationNum();
    // One radio serves both interfaces, so this is the channel for the access
    // point AND the home network. Joining a router drags the access point onto
    // that router's channel, which is why phones sometimes reconnect on a join.
    doc["channel"] = WiFi.channel();
    // Two different addresses: the station side is what a router's DHCP table
    // and MAC filter see, the AP side is what a phone sees when it joins us.
    doc["mac"] = WiFi.macAddress();
    doc["ap_mac"] = WiFi.softAPmacAddress();
    doc["ws_clients"] = webSocket.connectedClients();
    doc["mdns"] = mdnsName;
    doc["pc_state"] = lockStateStr();
    // What the board believes it is plugged into, and whether that was probed or
    // set by hand. The dashboard uses it to pick Mac vs PC shortcuts and layout.
    doc["host_os"] = hostOsName(hostOs());
    doc["host_os_source"] = hostOsSource();
    // What enumeration concluded regardless of any manual pin, so the dashboard
    // can warn that a pin is left over from a different computer. The raw signal
    // counts are what make a detection argument settleable instead of guesswork.
    doc["host_os_detected"] = hostOsName(hostOsDetected);
    doc["detect_phase"] = detectPhaseStr();
    doc["unicode_mac"]   = (bool)(unicodeHosts & (1 << OS_MAC));
    doc["unicode_linux"] = (bool)(unicodeHosts & (1 << OS_LINUX));
    doc["usb_set_idle"] = usbSetIdleCount;
    doc["usb_ctrl_reqs"] = usbCtrlCount;
    doc["usb_led_reports"] = ledEventCount;
    doc["usb_str_reqs"] = usbStrReqCount;
    doc["usb_str_rereads"] = usbStrRereadCount;
    // The raw fingerprint, oldest first, so a wrong verdict can be diagnosed from
    // the phone instead of from a rebuild with logging in it.
    {
        String seq;
        for (uint8_t i = 0; i < USB_STR_SEQ_LEN; i++) {
            uint8_t v = usbStrSeq[(usbStrSeqHead + i) % USB_STR_SEQ_LEN];
            if (seq.length()) seq += ',';
            seq += v;
        }
        doc["usb_str_seq"] = seq;
    }
    doc["pointer_mode"] = absoluteMode ? "absolute" : "relative";
    doc["gamepad"] = gamepadEnabled;
    doc["auth_set"] = (authToken.length() > 0);
    doc["ap_open"] = (apPassword.length() == 0);
    doc["ap_pass_generated"] = apPassGenerated;
    doc["ap_pass_default"] = (apPassword == "hid12345");
    doc["verbose"] = verboseLog;
    doc["wiggle_active"] = (wiggleMode != WIGGLE_NONE);
    doc["wiggle_mode"] = wiggleModeStr();
    doc["led_on"] = ledOn;
}

void handleDeviceStatus(bool fromWifi) {
    DynamicJsonDocument doc(2560);
    buildStatusDoc(doc);
    doc["boot_button"] = (digitalRead(BOOT_BUTTON) == LOW) ? "pressed" : "released";
    
    String output;
    serializeJson(doc, output);
    if (!fromWifi || verboseLog) COM_SERIAL.println(output);
    
    if (fromWifi) replyRaw(output);
}

void handleEcho(const JsonDocument& inDoc) {
    const char* data = inDoc["data"] | "";
    StaticJsonDocument<512> doc;
    doc["status"] = "ok";
    doc["reply"] = "echo";
    doc["data"] = data;
    doc["length"] = strlen(data);
    sendJson(doc);
}

// ==================== WiFi MANAGEMENT COMMANDS ====================
void handleWifiConfigure(const JsonDocument& inDoc) {
    const char* ssid = inDoc["ssid"] | "";
    const char* pass = inDoc["password"] | "";

    if (strlen(ssid) == 0) {
        StaticJsonDocument<128> doc;
        doc["status"] = "error";
        doc["message"] = "SSID is required";
        sendJson(doc);
        return;
    }

    // Optional static addressing. Absent or empty means DHCP.
    staWantIp   = String(inDoc["ip"]      | "");
    staWantGw   = String(inDoc["gateway"] | "");
    staWantMask = String(inDoc["subnet"]  | "");
    staWantDns  = String(inDoc["dns"]     | "");

    // A static address without a gateway cannot route anywhere, so reject it here
    // rather than letting the user watch a 15 second timeout for no reason.
    if (staWantIp.length()) {
        IPAddress probe;
        if (!probe.fromString(staWantIp)) {
            StaticJsonDocument<192> d;
            d["status"]="error"; d["message"]="That is not a valid IPv4 address";
            sendJson(d); return;
        }
        if (staWantGw.length() == 0) {
            StaticJsonDocument<224> d;
            d["status"]="error"; d["message"]="A static address needs a gateway, usually your router";
            sendJson(d); return;
        }
    }

    // A row in the saved-network table: a new network goes in front (the oldest
    // drops off when all NET_MAX are used); re-entering one updates it. A new
    // password is unproven until it works.
    int row = netFind(String(ssid));
    if (row < 0) {
        if (netCount == NET_MAX) { COM_SERIAL.printf("[WIFI] Forgetting %s to make room\n", netSsid[NET_MAX-1].c_str()); netRemove(NET_MAX - 1); }
        row = netCount++;
        netOk[row] = false;
    } else if (netPass[row] != pass) {
        netOk[row] = false;
    }
    netSsid[row] = ssid; netPass[row] = pass;
    netIp[row] = staWantIp; netGw[row] = staWantGw; netMask[row] = staWantMask; netDns[row] = staWantDns;
    netBad[row] = false; netCoolUntil[row] = 0;
    netToFront(row);
    netSave();
    netSelect(0);
    COM_SERIAL.printf("[STA] Saved network: %s (%u saved)\n", ssid, (unsigned)netCount);

    staAuthFails = 0; staLastReason = 0; staScanPending = false;
    staSupervised = false; staRetryAt = 0; staDownSince = 0;
    wifiConnected = false;
    wifiIP = "";
    staBeginJoin(String(ssid), String(pass), true);

    // Returns straight away. The join runs in loop() and the dashboard polls
    // wifi_status to follow it, because a 15 second block here would freeze the
    // very page that is waiting for the answer.
    StaticJsonDocument<288> doc;
    doc["status"] = "ok";
    doc["reply"] = "wifi_connecting";
    doc["ssid"] = ssid;
    doc["phase"] = staPhaseStr();
    doc["ip_mode"] = staIpMode;
    doc["timeout_ms"] = WIFI_CONNECT_TIMEOUT;
    doc["ap_still_active"] = true;   // the AP never drops, so you cannot lock yourself out
    sendJson(doc);
}

void handleWifiStatus() {
    StaticJsonDocument<512> doc;
    doc["status"] = "ok";
    doc["reply"] = "wifi_status";
    doc["sta_connected"] = wifiConnected;
    doc["sta_enabled"] = wifiEnabled;
    doc["sta_ip"] = wifiIP;
    doc["sta_phase"] = staPhaseStr();
    doc["sta_error"] = staError;
    doc["sta_ip_mode"] = staIpMode;
    doc["sta_target"] = staSsid;
    doc["want_ip"] = staWantIp;
    if (wifiConnected) {
        doc["sta_ssid"] = WiFi.SSID();
        doc["sta_rssi"] = WiFi.RSSI();
        doc["sta_channel"] = WiFi.channel();
        doc["sta_gateway"] = WiFi.gatewayIP().toString();
        doc["sta_subnet"] = WiFi.subnetMask().toString();
        doc["sta_dns"] = WiFi.dnsIP().toString();
    }
    
    doc["ap_ssid"] = apSSID;
    doc["ap_ip"] = apIP;
    doc["ap_clients"] = WiFi.softAPgetStationNum();
    doc["mac"] = WiFi.macAddress();
    doc["ap_mac"] = WiFi.softAPmacAddress();
    preferences.begin("wifi_cfg", true);
    String savedSSID = preferences.getString("ssid", "");
    preferences.end();
    doc["saved_ssid"] = savedSSID;
    
    sendJson(doc);
}

void handleWifiDisconnect() {
    WiFi.disconnect(false);  // disconnect STA but keep AP running
    wifiConnected = false;
    wifiIP = "";
    staPhase = STA_IDLE;
    staError = "";
    staSsid = "";
    staIpMode = "dhcp";
    staWantIp = staWantGw = staWantMask = staWantDns = "";
    staSavedSsid = staSavedPass = "";
    staProven = false; staRetryAt = 0; staDownSince = 0; staScanPending = false;
    netCount = 0; netCurrent = -1;
    netSave();                                  // every saved network, and the v3.10 keys
    WiFi.config(INADDR_NONE, INADDR_NONE, INADDR_NONE);   // back to DHCP for next time

    preferences.begin("wifi_cfg", false);
    preferences.remove("ssid");
    preferences.remove("pass");
    preferences.remove("ip");
    preferences.remove("gw");
    preferences.remove("mask");
    preferences.remove("dns");
    preferences.remove("sta_ok");
    preferences.end();
    
    COM_SERIAL.println("[STA] Disconnected and STA credentials cleared. AP still active.");
    
    StaticJsonDocument<128> doc;
    doc["status"] = "ok";
    doc["reply"] = "wifi_disconnected";
    doc["ap_still_active"] = true;
    sendJson(doc);
}

void handleApConfigure(const JsonDocument& inDoc) {
    const char* newSsid = inDoc["ssid"] | "";
    const char* newPass = inDoc["password"] | "";
    // Explicit, because the alternative is guessing from an empty field - and
    // guessing wrong there silently drops the password off the network.
    bool wantOpen = inDoc["open"] | false;
    size_t ssidLen = strlen(newSsid);
    size_t passLen = strlen(newPass);
    
    if (ssidLen == 0) {
        StaticJsonDocument<128> doc;
        doc["status"] = "error";
        doc["message"] = "AP SSID is required";
        sendJson(doc);
        return;
    }
    // 802.11 caps the SSID at 32 BYTES, not characters. An emoji is four bytes,
    // so a name that looks short on screen can still be too long here.
    if (ssidLen > 32) {
        StaticJsonDocument<224> doc;
        doc["status"] = "error";
        doc["message"] = "Network name is too long. The limit is 32 bytes, and an emoji uses four.";
        doc["bytes"] = (int)ssidLen;
        doc["max_bytes"] = 32;
        sendJson(doc);
        return;
    }
    // Mangled bytes would produce a name that no device can display or retype,
    // and would corrupt the JSON this board emits.
    if (!validUtf8(newSsid, ssidLen) || hasControlChars(newSsid, ssidLen)) {
        StaticJsonDocument<224> doc;
        doc["status"] = "error";
        doc["message"] = "Network name must be valid text. Control characters are not allowed.";
        sendJson(doc);
        return;
    }
    if (passLen > 0 && passLen < 8) {
        StaticJsonDocument<128> doc;
        doc["status"] = "error";
        doc["message"] = "AP password must be at least 8 characters. To remove it, send open:true.";
        sendJson(doc);
        return;
    }
    if (passLen > 63) {
        StaticJsonDocument<160> doc;
        doc["status"] = "error";
        doc["message"] = "AP password can be at most 63 bytes";
        sendJson(doc);
        return;
    }
    
    apSSID = String(newSsid);
    // Three distinct intents, and an empty field alone cannot express which:
    //   open=true      -> deliberately remove the password
    //   password given -> use it
    //   neither        -> rename only, keep the password that is already set
    if (wantOpen)          apPassword = "";
    else if (passLen > 0)  apPassword = String(newPass);
    // else: apPassword is left exactly as it was

    // Persist it. Boot reads ap_ssid/ap_pass back out of wifi_cfg, but nothing
    // used to write them, so every rename survived only until the next reboot.
    preferences.begin("wifi_cfg", false);
    preferences.putString("ap_ssid", apSSID);
    preferences.putString("ap_pass", apPassword);
    preferences.putBool("ap_open", apPassword.length() == 0);
    apPassGenerated = apGenPassword.length() >= 8 && apPassword == apGenPassword;
    preferences.end();

    // Restarting the AP drops every client on it, including the phone waiting
    // for this reply. Defer it so the answer gets out first.
    pendingApRestartAt = millis() + 300;
}

// ==================== STORAGE INVENTORY ====================
// What is actually sitting in flash, for anyone working on the board. No secret
// is ever returned - passwords and the PIN report only whether they are set and
// how long they are, which is enough to debug with and useless to steal.
void handleStorageInfo() {
    DynamicJsonDocument doc(3072);
    doc["status"] = "ok";
    doc["reply"] = "storage_info";

    nvs_stats_t st;
    if (nvs_get_stats(NULL, &st) == ESP_OK) {
        JsonObject n = doc.createNestedObject("nvs");
        n["used_entries"]  = st.used_entries;
        n["free_entries"]  = st.free_entries;
        n["total_entries"] = st.total_entries;
        n["namespaces"]    = st.namespace_count;
        n["percent_used"]  = st.total_entries ? (int)((st.used_entries * 100) / st.total_entries) : 0;
    }

    JsonObject c = doc.createNestedObject("hid_cfg");
    preferences.begin("hid_cfg", true);
    c["pointer_mode"] = preferences.getBool("abs", false) ? "absolute" : "relative";
    c["gamepad"]      = preferences.getBool("gpad", false);
    c["pin_set"]      = preferences.getString("auth", "").length() > 0;
    c["verbose"]      = preferences.getBool("verbose", false);
    c["power_mode"]   = powerModeStr();
    c["ap_auto_off"]  = preferences.getBool("apoff", false);
    c["ui_bytes"]     = (int)preferences.getString("ui", "").length();
    preferences.end();

    JsonObject v = doc.createNestedObject("vault");
    v["entries"]       = vaultCount();     // how many, never what
    v["slots"]         = VAULT_SLOTS;
    v["vault_pin_set"] = vaultPinCode.length() > 0;

    JsonObject w = doc.createNestedObject("wifi_cfg");
    preferences.begin("wifi_cfg", true);
    String sSsid = preferences.getString("ssid", "");
    w["sta_ssid"]      = sSsid;
    w["sta_pass_set"]  = preferences.getString("pass", "").length() > 0;
    w["ap_ssid"]       = preferences.getString("ap_ssid", "");
    w["ap_pass_set"]   = preferences.getString("ap_pass", "").length() >= 8;
    w["ap_open"]       = preferences.getBool("ap_open", false);
    w["static_ip"]     = preferences.getString("ip", "");
    w["static_gw"]     = preferences.getString("gw", "");
    w["static_mask"]   = preferences.getString("mask", "");
    w["static_dns"]    = preferences.getString("dns", "");
    preferences.end();

    doc["note"] = "Macros are listed by macro_list, saved PCs by pc_list and vault entries by vault_list. No password, secret or PIN is ever returned by any of them.";
    sendJson(doc);
}

// ==================== CONNECTED CLIENTS ====================
void handleClients() {
    DynamicJsonDocument doc(3072);
    doc["status"] = "ok";
    doc["reply"] = "clients";
    JsonArray arr = doc.createNestedArray("clients");

    // Which browsers are holding a live dashboard socket right now. Matched by
    // IP further down, so a client shows as "dashboard open" rather than merely
    // "associated".
    IPAddress wsPeers[WS_MAX_CLIENTS];
    int wsPeerCount = 0;
    for (int i = 0; i < WS_MAX_CLIENTS; i++) {
        IPAddress p = webSocket.remoteIP((uint8_t)i);
        if ((uint32_t)p != 0) wsPeers[wsPeerCount++] = p;
    }

    wifi_sta_list_t sta;
    if (esp_wifi_ap_get_sta_list(&sta) == ESP_OK) {
        // esp_netif_get_sta_list() was removed in the IDF this core is built on.
        // The ARP table is where that function got its answers anyway, so read it
        // directly: every station that has spoken IP to us is in here. Matching on
        // the MAC alone is enough - only AP clients carry these addresses.
        for (int i = 0; i < sta.num && i < 8; i++) {
            JsonObject o = arr.createNestedObject();
            const uint8_t* m = sta.sta[i].mac;
            char mac[18];
            snprintf(mac, sizeof(mac), "%02X:%02X:%02X:%02X:%02X:%02X",
                     m[0], m[1], m[2], m[3], m[4], m[5]);
            o["mac"]     = mac;
            o["rssi"]    = sta.sta[i].rssi;
            o["quality"] = linkQuality(sta.sta[i].rssi);

            uint32_t ipRaw = 0;
            for (size_t e = 0; e < ARP_TABLE_SIZE; e++) {
                ip4_addr_t*      eip = nullptr;
                struct netif*    eif = nullptr;
                struct eth_addr* eth = nullptr;
                if (!etharp_get_entry(e, &eip, &eif, &eth)) continue;
                if (!eth || !eip) continue;
                if (memcmp(eth->addr, m, 6) == 0) { ipRaw = eip->addr; break; }
            }
            if (ipRaw) {
                IPAddress ip(ipRaw);
                o["ip"] = ip.toString();
                bool onDash = false;
                for (int w = 0; w < wsPeerCount; w++) {
                    if ((uint32_t)wsPeers[w] == ipRaw) { onDash = true; break; }
                }
                o["dashboard"] = onDash;
            } else {
                o["ip"] = "";
                o["dashboard"] = false;
            }
        }
    }
    doc["count"]    = arr.size();
    doc["attached"] = WiFi.softAPgetStationNum();

    // Every open dashboard, from either network - the number in the dashboard's
    // header counts these, and a tab left open on a laptop over the home network
    // is never in the access-point list above. "you" is the one asking.
    JsonArray dash = doc.createNestedArray("dashboards");
    for (int i = 0; i < WS_MAX_CLIENTS; i++) {
        if (!webSocket.clientIsConnected((uint8_t)i)) continue;
        IPAddress p = webSocket.remoteIP((uint8_t)i);
        JsonObject o = dash.createNestedObject();
        o["ip"]        = p.toString();
        o["network"]   = clientIsAp(p) ? "board" : "home";
        o["signed_in"] = authToken.length() > 0 && wsAuthed[i];
        o["you"]       = (cmdSource == SRC_WS && wsClientNum == i);
    }
    // Worth stating plainly: this is not a limitation that can be fixed here.
    doc["note"] = "Device names are not shown. A WiFi client is never required to tell the access point its name, and most phones now send a random MAC as well.";
    sendJson(doc);
}

// ==================== POWER MODE COMMAND ====================
void handlePowerMode(const JsonDocument& inDoc) {
    String m = String(inDoc["mode"] | "");
    m.toLowerCase();

    if (m.length()) {
        if      (m == "performance") powerMode = PWR_PERFORMANCE;
        else if (m == "balanced")    powerMode = PWR_BALANCED;
        else if (m == "saver")       powerMode = PWR_SAVER;
        else {
            StaticJsonDocument<192> d;
            d["status"] = "error";
            d["message"] = "Unknown mode (performance|balanced|saver)";
            sendJson(d);
            return;
        }
    }

    if (!inDoc["ap_auto_off"].isNull()) {
        apAutoOff = inDoc["ap_auto_off"].as<bool>();
        apIdleSince = millis();
        // Turning the option off should give the access point back immediately,
        // not at some point in the future.
        if (!apAutoOff && !apRunning) restartAP();
    }

    preferences.begin("hid_cfg", false);
    preferences.putUChar("pwr", (uint8_t)powerMode);
    preferences.putBool("apoff", apAutoOff);
    preferences.end();

    applyPowerMode();

    StaticJsonDocument<384> doc;
    doc["status"] = "ok";
    doc["reply"] = "power_mode";
    doc["mode"] = powerModeStr();
    doc["radio_awake"] = radioAwake;
    doc["ap_auto_off"] = apAutoOff;
    doc["ap_running"] = apRunning;
    doc["idle_after_ms"] = POWER_IDLE_MS;
    sendJson(doc);
}

// ============================================================================
//                MOUSE + WIGGLE + LED  (Phase 3.2 additions)
// ============================================================================
// All handlers below use the SAME request/response contract as the existing
// commands: exactly ONE JSON object is emitted per command via sendJson()
// (buffered for WiFi, echoed to serial). Background operations (interval/timed
// wiggle, LED blink) are driven non-blocking from loop() via updateWiggle() /
// updateLedBlink() so the command reader stays responsive to a stop command.

// ==================== WIGGLE (state declared in the globals section) ====================

const char* wiggleModeStr() {
    switch (wiggleMode) {
        case WIGGLE_INTERVAL: return "interval";
        case WIGGLE_TIMED:    return "timed";
        default:              return "idle";
    }
}

// One net-zero jiggle: move right then back left → cursor returns to origin,
// so the cursor never drifts across the screen over thousands of iterations.
// Two separate HID reports are sent, so the OS idle timer is reset either way.
void doJiggle() {
    hidMouseMove(wiggleAmplitude, 0, 0, 0);
    delay(6);
    hidMouseMove(-wiggleAmplitude, 0, 0, 0);
    wiggleCount++;
}

// Non-blocking wiggle driver — called every loop() iteration.
void updateWiggle() {
    if (wiggleMode == WIGGLE_NONE) return;
    unsigned long now = millis();

    // TIMED auto-stop
    if (wiggleMode == WIGGLE_TIMED && now >= wiggleEndMs) {
        unsigned long total   = wiggleCount;
        unsigned long elapsed = (now - wiggleStartMs) / 1000;
        wiggleMode = WIGGLE_NONE;
        COM_SERIAL.printf("[WIGGLE] Timed run complete (%lu jiggles, %lus)\n", total, elapsed);
        // Async completion event on serial only. It has no "reply"/"status"
        // field, so existing serial readers (pc_unlocker/unlock_client) skip it.
        StaticJsonDocument<192> evt;
        evt["event"]       = "wiggle_complete";
        evt["count"]       = total;
        evt["elapsed_sec"] = elapsed;
        String out;
        serializeJson(evt, out);
        COM_SERIAL.println(out);
        return;
    }

    if (now - wiggleLastMove >= wiggleInterval) {
        if (hidReady) doJiggle();
        wiggleLastMove = now;
    }
}

void handleWiggle(const JsonDocument& inDoc) {
    if (!hidReady) {
        StaticJsonDocument<160> doc;
        doc["status"] = "error";
        doc["message"] = "USB HID not initialized";
        sendJson(doc);
        return;
    }

    const char* mode = inDoc["mode"] | "single";
    bool force       = inDoc["force"] | false;
    int amplitude    = inDoc["amplitude"] | 5;
    if (amplitude < 1)   amplitude = 1;
    if (amplitude > 100) amplitude = 100;

    bool isBackground = (strcmp(mode, "interval") == 0) || (strcmp(mode, "timed") == 0);

    // Busy check: a background wiggle is already running and no force override.
    if (isBackground && wiggleMode != WIGGLE_NONE && !force) {
        COM_SERIAL.printf("[WIGGLE] Rejected — already running (%s)\n", wiggleModeStr());
        StaticJsonDocument<256> doc;
        doc["status"]      = "error";
        doc["reply"]       = "busy";
        doc["message"]     = "A wiggle is already running — send wiggle_stop or pass force:true";
        doc["wiggle_mode"] = wiggleModeStr();
        sendJson(doc);
        return;
    }

    if (strcmp(mode, "single") == 0) {
        wiggleAmplitude = amplitude;
        doJiggle();
        COM_SERIAL.printf("[WIGGLE] Single jiggle (amplitude=%d)\n", amplitude);
        StaticJsonDocument<192> doc;
        doc["status"]    = "ok";
        doc["reply"]     = "wiggle_done";
        doc["mode"]      = "single";
        doc["amplitude"] = amplitude;
        sendJson(doc);
        return;
    }
    else if (strcmp(mode, "interval") == 0) {
        unsigned long interval = inDoc["interval_ms"] | 1000;
        if (interval < 50) interval = 50;
        wiggleAmplitude = amplitude;
        wiggleInterval  = interval;
        wiggleStartMs   = millis();
        wiggleLastMove  = 0;   // fire first jiggle on next loop() immediately
        wiggleCount     = 0;
        wiggleMode      = WIGGLE_INTERVAL;
        COM_SERIAL.printf("[WIGGLE] Interval started (amplitude=%d, every %lums)%s\n",
                          amplitude, interval, force ? " [forced restart]" : "");
        StaticJsonDocument<256> doc;
        doc["status"]      = "ok";
        doc["reply"]       = "wiggle_started";
        doc["mode"]        = "interval";
        doc["amplitude"]   = amplitude;
        doc["interval_ms"] = interval;
        sendJson(doc);
        return;
    }
    else if (strcmp(mode, "timed") == 0) {
        unsigned long interval = inDoc["interval_ms"] | 1000;
        unsigned long duration = inDoc["duration_s"] | 60;
        if (interval < 50) interval = 50;
        if (duration < 1)  duration = 1;
        wiggleAmplitude = amplitude;
        wiggleInterval  = interval;
        wiggleStartMs   = millis();
        wiggleEndMs     = wiggleStartMs + duration * 1000UL;
        wiggleLastMove  = 0;
        wiggleCount     = 0;
        wiggleMode      = WIGGLE_TIMED;
        COM_SERIAL.printf("[WIGGLE] Timed started (amplitude=%d, every %lums, for %lus)%s\n",
                          amplitude, interval, duration, force ? " [forced restart]" : "");
        StaticJsonDocument<288> doc;
        doc["status"]      = "ok";
        doc["reply"]       = "wiggle_started";
        doc["mode"]        = "timed";
        doc["amplitude"]   = amplitude;
        doc["interval_ms"] = interval;
        doc["duration_s"]  = duration;
        sendJson(doc);
        return;
    }
    else {
        StaticJsonDocument<192> doc;
        doc["status"]  = "error";
        doc["message"] = "Invalid wiggle mode (use single|interval|timed)";
        sendJson(doc);
    }
}

void handleWiggleStop(const JsonDocument& inDoc) {
    if (wiggleMode == WIGGLE_NONE) {
        // Idempotent: stopping when nothing runs is a success, not an error.
        StaticJsonDocument<160> doc;
        doc["status"]  = "ok";
        doc["reply"]   = "not_running";
        doc["message"] = "No wiggle is currently running";
        sendJson(doc);
        return;
    }
    unsigned long total   = wiggleCount;
    unsigned long elapsed = (millis() - wiggleStartMs) / 1000;
    const char* prevMode  = wiggleModeStr();
    wiggleMode = WIGGLE_NONE;
    COM_SERIAL.printf("[WIGGLE] Stopped (%s, %lu jiggles, %lus)\n", prevMode, total, elapsed);
    StaticJsonDocument<224> doc;
    doc["status"]       = "ok";
    doc["reply"]        = "wiggle_stopped";
    doc["stopped_mode"] = prevMode;
    doc["count"]        = total;
    doc["elapsed_sec"]  = elapsed;
    sendJson(doc);
}

// ==================== MOUSE MOVE / CLICK / SCROLL ====================
uint8_t mapMouseButton(const char* name) {
    String b = String(name);
    b.toLowerCase();
    if (b == "right")   return MOUSE_RIGHT;
    if (b == "middle")  return MOUSE_MIDDLE;
    if (b == "back" || b == "backward") return MOUSE_BACKWARD;
    if (b == "forward") return MOUSE_FORWARD;
    return MOUSE_LEFT;
}

static int clampInt8(int v) {
    if (v > 127)  return 127;
    if (v < -127) return -127;
    return v;
}

// Press only the keyboard modifiers from a combo string like "CTRL+SHIFT".
// Used to support modifier+click combinations (e.g. Ctrl+Click).
void pressModifiersOnly(const char* comboStr) {
    String combo = String(comboStr);
    combo.toUpperCase();
    int start = 0;
    while (start <= (int)combo.length()) {
        int plus = combo.indexOf('+', start);
        String tok = (plus == -1) ? combo.substring(start) : combo.substring(start, plus);
        tok.trim();
        if (tok.length() > 0) {
            uint8_t k = mapKeyName(tok);
            if (k >= 0x80 && k <= 0x87) Keyboard.press(k);
        }
        if (plus == -1) break;
        start = plus + 1;
    }
}

void handleMouseMove(const JsonDocument& inDoc) {
    if (!hidReady) {
        StaticJsonDocument<128> d; d["status"]="error"; d["message"]="USB HID not initialized"; sendJson(d); return;
    }
    int dx    = constrain((int)(inDoc["dx"] | 0), -2000, 2000);
    int dy    = constrain((int)(inDoc["dy"] | 0), -2000, 2000);
    int wheel = clampInt8(inDoc["wheel"] | 0);
    int pan   = clampInt8(inDoc["pan"] | 0);
    hidMouseMove(dx, dy, wheel, pan);
    if (verboseLog) COM_SERIAL.printf("[MOUSE] move dx=%d dy=%d wheel=%d pan=%d\n", dx, dy, wheel, pan);
    StaticJsonDocument<192> doc;
    doc["status"]="ok"; doc["reply"]="mouse_moved";
    doc["dx"]=dx; doc["dy"]=dy; doc["wheel"]=wheel; doc["pan"]=pan;
    sendJson(doc);
}

void handleMouseClick(const JsonDocument& inDoc) {
    if (!hidReady) {
        StaticJsonDocument<128> d; d["status"]="error"; d["message"]="USB HID not initialized"; sendJson(d); return;
    }
    const char* button    = inDoc["button"] | "left";
    const char* modifiers = inDoc["modifiers"] | "";
    int count = inDoc["count"] | 1;
    if (count < 1)  count = 1;
    if (count > 10) count = 10;
    uint8_t mask = mapMouseButton(button);

    bool hasMods = strlen(modifiers) > 0;
    if (hasMods) { pressModifiersOnly(modifiers); delay(30); }
    hidMouseButtons(mask, 2, (uint8_t)count);
    if (hasMods) { delay(30); Keyboard.releaseAll(); }

    if (verboseLog) COM_SERIAL.printf("[MOUSE] click button=%s count=%d mods=%s\n", button, count, modifiers);
    StaticJsonDocument<224> doc;
    doc["status"]="ok"; doc["reply"]="mouse_clicked";
    doc["button"]=button; doc["count"]=count;
    if (hasMods) doc["modifiers"]=modifiers;
    sendJson(doc);
}

void handleMousePress(const JsonDocument& inDoc) {
    if (!hidReady) {
        StaticJsonDocument<128> d; d["status"]="error"; d["message"]="USB HID not initialized"; sendJson(d); return;
    }
    const char* button = inDoc["button"] | "left";
    hidMouseButtons(mapMouseButton(button), 1, 1);
    if (verboseLog) COM_SERIAL.printf("[MOUSE] press button=%s\n", button);
    StaticJsonDocument<160> doc; doc["status"]="ok"; doc["reply"]="mouse_pressed"; doc["button"]=button; sendJson(doc);
}

void handleMouseRelease(const JsonDocument& inDoc) {
    if (!hidReady) {
        StaticJsonDocument<128> d; d["status"]="error"; d["message"]="USB HID not initialized"; sendJson(d); return;
    }
    const char* button = inDoc["button"] | "left";
    hidMouseButtons(mapMouseButton(button), 0, 1);
    if (verboseLog) COM_SERIAL.printf("[MOUSE] release button=%s\n", button);
    StaticJsonDocument<160> doc; doc["status"]="ok"; doc["reply"]="mouse_released"; doc["button"]=button; sendJson(doc);
}

void handleMouseScroll(const JsonDocument& inDoc) {
    if (!hidReady) {
        StaticJsonDocument<128> d; d["status"]="error"; d["message"]="USB HID not initialized"; sendJson(d); return;
    }
    int amount = clampInt8(inDoc["amount"] | 0);
    int pan    = clampInt8(inDoc["pan"] | 0);
    hidMouseMove(0, 0, amount, pan);
    if (verboseLog) COM_SERIAL.printf("[MOUSE] scroll amount=%d pan=%d\n", amount, pan);
    StaticJsonDocument<160> doc; doc["status"]="ok"; doc["reply"]="mouse_scrolled"; doc["amount"]=amount; doc["pan"]=pan; sendJson(doc);
}

// ==================== LED (WS2812 RGB on GPIO48) ====================
// State is declared in the globals section. Requires the on-board 'RGB' solder
// pad to be bridged. If it is not bridged, these writes are harmless no-ops.
// Values are 0-255 per channel; keep them modest (WS2812 full brightness is very bright).

void ledApply(uint8_t r, uint8_t g, uint8_t b) {
    ledR = r; ledG = g; ledB = b;
    ledOn = (r || g || b);
    rgbLedWrite(RGB_LED_PIN, r, g, b);
}

bool colorNameToRGB(const char* name, uint8_t& r, uint8_t& g, uint8_t& b) {
    String c = String(name);
    c.toLowerCase();
    if (c == "red")     { r=60; g=0;  b=0;  return true; }
    if (c == "green")   { r=0;  g=60; b=0;  return true; }
    if (c == "blue")    { r=0;  g=0;  b=60; return true; }
    if (c == "yellow")  { r=45; g=45; b=0;  return true; }
    if (c == "cyan")    { r=0;  g=45; b=45; return true; }
    if (c == "magenta" || c == "purple") { r=45; g=0; b=45; return true; }
    if (c == "white")   { r=40; g=40; b=40; return true; }
    if (c == "orange")  { r=60; g=25; b=0;  return true; }
    if (c == "off" || c == "black") { r=0; g=0; b=0; return true; }
    return false;
}

// Non-blocking LED blink driver — called every loop() iteration.
void updateLedBlink() {
    if (ledBlinkRemaining <= 0) return;
    unsigned long now = millis();
    if (ledBlinkPhaseOn) {
        if (now - ledBlinkLast >= (unsigned long)ledBlinkOnMs) {
            ledApply(0, 0, 0);
            ledBlinkPhaseOn = false;
            ledBlinkLast = now;
            ledBlinkRemaining--;   // one full ON pulse counted
        }
    } else {
        if (ledBlinkRemaining > 0 && now - ledBlinkLast >= (unsigned long)ledBlinkOffMs) {
            ledApply(ledBlinkR, ledBlinkG, ledBlinkB);
            ledBlinkPhaseOn = true;
            ledBlinkLast = now;
        }
    }
}

void handleLedSet(const JsonDocument& inDoc) {
    ledBlinkRemaining = 0;  // a solid set cancels any running blink
    uint8_t r, g, b;
    if (!inDoc["color"].isNull()) {
        const char* color = inDoc["color"] | "off";
        if (!colorNameToRGB(color, r, g, b)) {
            StaticJsonDocument<192> doc;
            doc["status"]  = "error";
            doc["message"] = "Unknown color name (red|green|blue|yellow|cyan|magenta|white|orange|off)";
            sendJson(doc);
            return;
        }
    } else if (inDoc["off"] | false) {
        r = g = b = 0;
    } else {
        r = (uint8_t) constrain((int)(inDoc["r"] | 0), 0, 255);
        g = (uint8_t) constrain((int)(inDoc["g"] | 0), 0, 255);
        b = (uint8_t) constrain((int)(inDoc["b"] | 0), 0, 255);
    }
    ledApply(r, g, b);
    COM_SERIAL.printf("[LED] set r=%u g=%u b=%u (on=%d)\n", r, g, b, ledOn);
    StaticJsonDocument<192> doc;
    doc["status"]="ok"; doc["reply"]="led_set";
    doc["r"]=r; doc["g"]=g; doc["b"]=b; doc["on"]=ledOn;
    sendJson(doc);
}

void handleLedBlink(const JsonDocument& inDoc) {
    int times = inDoc["times"] | 3;
    if (times < 1)   times = 1;
    if (times > 100) times = 100;
    int onMs  = inDoc["on_ms"]  | 200;
    int offMs = inDoc["off_ms"] | 200;
    if (onMs < 20)  onMs = 20;
    if (offMs < 20) offMs = 20;

    uint8_t r, g, b;
    if (!inDoc["color"].isNull()) {
        if (!colorNameToRGB(inDoc["color"] | "blue", r, g, b)) { r=0; g=0; b=60; }
    } else {
        r = (uint8_t) constrain((int)(inDoc["r"] | 0),  0, 255);
        g = (uint8_t) constrain((int)(inDoc["g"] | 0),  0, 255);
        b = (uint8_t) constrain((int)(inDoc["b"] | 0),  0, 255);
        if (!(r || g || b)) { b = 60; }   // default to blue if nothing given
    }

    ledBlinkR = r; ledBlinkG = g; ledBlinkB = b;
    ledBlinkOnMs = onMs; ledBlinkOffMs = offMs;
    ledBlinkRemaining = times;
    ledBlinkPhaseOn = true;
    ledBlinkLast = millis();
    ledApply(r, g, b);   // start with the first ON phase immediately

    COM_SERIAL.printf("[LED] blink r=%u g=%u b=%u times=%d on=%dms off=%dms\n", r, g, b, times, onMs, offMs);
    StaticJsonDocument<256> doc;
    doc["status"]="ok"; doc["reply"]="led_blink_started";
    doc["times"]=times; doc["on_ms"]=onMs; doc["off_ms"]=offMs;
    doc["r"]=r; doc["g"]=g; doc["b"]=b;
    sendJson(doc);
}

void handleLedStatus() {
    StaticJsonDocument<256> doc;
    doc["status"]="ok"; doc["reply"]="led_status";
    doc["on"]=ledOn; doc["r"]=ledR; doc["g"]=ledG; doc["b"]=ledB;
    doc["blinking"]        = (ledBlinkRemaining > 0);
    doc["blink_remaining"] = ledBlinkRemaining;
    doc["pin"]             = RGB_LED_PIN;
    doc["note"]            = "RGB LED requires the on-board 'RGB' pad to be bridged";
    sendJson(doc);
}

// ============================================================================
//         TRACKPAD / REMOTE CONTROL  (v3.4 additions)
// ============================================================================

// ==================== POINTER PLUMBING ====================
USBHIDMouseBase* activeMouse() {
    if (mouseRel) return (USBHIDMouseBase*)mouseRel;
    if (mouseAbs) return (USBHIDMouseBase*)mouseAbs;
    return nullptr;
}

// The HID relative report carries a signed byte per axis, so a fast swipe used
// to be clipped at 127 and the cursor fell behind the finger. Split the motion
// across as many reports as it needs instead.
void hidMouseMove(int32_t dx, int32_t dy, int32_t wheel, int32_t pan) {
    if (!hidReady) return;
    usbWakeHost();
    if (mouseAbs) {
        absX = constrain(absX + dx * 8, 0, 32767);
        absY = constrain(absY + dy * 8, 0, 32767);
        mouseAbs->move((int16_t)absX, (int16_t)absY,
                       (int8_t)constrain(wheel, -127, 127), (int8_t)constrain(pan, -127, 127));
        return;
    }
    if (!mouseRel) return;
    dx    = constrain(dx, -4000, 4000);
    dy    = constrain(dy, -4000, 4000);
    wheel = constrain(wheel, -127, 127);
    pan   = constrain(pan, -127, 127);
    while (dx || dy || wheel || pan) {
        int8_t sx = (int8_t)constrain(dx, -127, 127);
        int8_t sy = (int8_t)constrain(dy, -127, 127);
        int8_t sw = (int8_t)constrain(wheel, -127, 127);
        int8_t sp = (int8_t)constrain(pan, -127, 127);
        mouseRel->move(sx, sy, sw, sp);
        dx -= sx; dy -= sy; wheel -= sw; pan -= sp;
    }
}

void hidMouseAbs(int32_t x, int32_t y) {
    if (!hidReady || !mouseAbs) return;
    absX = constrain(x, 0, 32767);
    absY = constrain(y, 0, 32767);
    mouseAbs->move((int16_t)absX, (int16_t)absY, 0, 0);
}

// action: 0 = release, 1 = press, 2 = click
void hidMouseButtons(uint8_t mask, uint8_t action, uint8_t count) {
    USBHIDMouseBase* m = activeMouse();
    if (!hidReady || !m) return;
    usbWakeHost();
    if (action == 1)      m->press(mask);
    else if (action == 0) m->release(mask);
    else {
        if (count < 1) count = 1;
        if (count > 10) count = 10;
        for (uint8_t i = 0; i < count; i++) {
            m->click(mask);
            if (i + 1 < count) delay(55);
        }
    }
}

// ==================== WEBSOCKET ====================
// Binary opcodes carry the pointer stream: no HTTP headers, no JSON parse, no
// reply, no serial echo. Text frames are ordinary JSON commands.
#define WS_OP_MOVE     0x01
#define WS_OP_BTN      0x02
#define WS_OP_ABS      0x03
#define WS_OP_PING     0x04
#define WS_OP_GAMEPAD  0x05
#define WS_OP_CONSUMER 0x06
#define WS_OP_KEY      0x07
#define WS_OP_PONG     0x81

static inline int16_t  rdI16(const uint8_t* p) { return (int16_t)(p[0] | (p[1] << 8)); }
static inline uint16_t rdU16(const uint8_t* p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static inline uint32_t rdU32(const uint8_t* p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

void onWsEvent(uint8_t num, WStype_t type, uint8_t* payload, size_t len) {
    switch (type) {
        case WStype_CONNECTED:
            if (num < WS_MAX_CLIENTS) {
                wsAuthed[num]  = (authToken.length() == 0);
                wsFromLan[num] = !clientIsAp(webSocket.remoteIP(num));
                wsOpenedAt[num] = millis();
            }
            noteActivity();
            COM_SERIAL.printf("[WS] client %u connected\n", num);
            if (authToken.length()) {
                webSocket.sendTXT(num, "{\"status\":\"error\",\"reply\":\"auth_required\"}");
            }
            break;

        case WStype_DISCONNECTED:
            if (num < WS_MAX_CLIENTS) { wsAuthed[num] = false; wsFromLan[num] = false; wsOpenedAt[num] = 0; }
            // A dashboard can vanish mid-chord - phone locked, tab killed, WiFi
            // dropped - while it is holding Alt for the app switcher or the left
            // button for a drag. That would leave the host unusable, so once the
            // last client is gone, let everything go.
            if (webSocket.connectedClients() <= 1) {
                Keyboard.releaseAll();
                hidMouseButtons(0x07, 0, 1);
            }
            COM_SERIAL.printf("[WS] client %u disconnected\n", num);
            break;

        case WStype_BIN: {
            if (len < 1) break;
            if (payload[0] == WS_OP_PING && len >= 5) {
                uint8_t out[5] = { WS_OP_PONG, payload[1], payload[2], payload[3], payload[4] };
                webSocket.sendBIN(num, out, 5);
                break;
            }
            if (num < WS_MAX_CLIENTS && !wsAuthed[num]) break;
            // The LAN gate in processCommand() covers JSON commands only. Without
            // this, any device on the home network could still press keys, move
            // the pointer and click while no PIN is set - exactly what the gate
            // says it refuses. Same rule: on the LAN, no PIN, board on a network.
            if (num < WS_MAX_CLIENTS && wsFromLan[num] && authToken.length() == 0 && wifiConnected) break;
            if (!hidReady) break;
            noteActivity();
            commandCount++;
            wifiCommandCount++;
            if (payload[0] == WS_OP_MOVE && len >= 7) {
                hidMouseMove(rdI16(payload + 1), rdI16(payload + 3),
                             (int8_t)payload[5], (int8_t)payload[6]);
            } else if (payload[0] == WS_OP_BTN && len >= 4) {
                hidMouseButtons(payload[1], payload[2], payload[3]);
            } else if (payload[0] == WS_OP_ABS && len >= 5) {
                hidMouseAbs(rdU16(payload + 1), rdU16(payload + 3));
            } else if (payload[0] == WS_OP_GAMEPAD && len >= 10) {
                if (Gamepad) {
                    Gamepad->send((int8_t)payload[1], (int8_t)payload[2],   // left stick
                                  (int8_t)payload[3], (int8_t)payload[4],   // right stick
                                  0, 0,                                     // triggers
                                  payload[5], rdU32(payload + 6));
                }
            } else if (payload[0] == WS_OP_CONSUMER && len >= 3) {
                // Release is deferred to loop() so a knob detent never blocks the socket.
                Consumer.press(rdU16(payload + 1));
                consumerReleaseAt = millis() + 12;
            } else if (payload[0] == WS_OP_KEY && len >= 3) {
                // [op][1=down 0=up][key name]. The NAME is sent rather than a
                // resolved HID code so mapKeyName() stays the single source of
                // truth - the dashboard never needs its own copy of the keymap.
                // Typing this way skips the JSON parse, the dispatch chain and
                // the reply frame, which is three quarters of the old cost.
                char name[24];
                size_t n = len - 2;
                if (n >= sizeof(name)) n = sizeof(name) - 1;
                memcpy(name, payload + 2, n);
                name[n] = 0;
                uint8_t k = hidKeyFor(name);
                if (k) {
                    usbWakeHost();
                    if (payload[1]) Keyboard.press(k);
                    else            Keyboard.release(k);
                }
            }
            break;
        }

        case WStype_TEXT: {
            // Refuse oversized frames before copying anything. Copying it twice to
            // find out it was too big is itself the heap exhaustion we are
            // guarding against.
            if (len == 0 || len > WS_TEXT_MAX) {
                webSocket.sendTXT(num, "{\"status\":\"error\",\"message\":\"Command too large\"}");
                break;
            }
            noteActivity();
            // One allocation. The old malloc/memcpy/String/free did the same job
            // with two, on every single command.
            String line((const char*)payload, len);
            wsClientNum = num;
            processCommand(line, SRC_WS);
            break;
        }

        default:
            break;
    }
}

// ==================== MEDIA / SYSTEM ====================
uint16_t mediaUsage(const char* key) {
    String k(key); k.toLowerCase();
    if (k == "volume_up"   || k == "vol_up")   return CONSUMER_CONTROL_VOLUME_INCREMENT;
    if (k == "volume_down" || k == "vol_down") return CONSUMER_CONTROL_VOLUME_DECREMENT;
    if (k == "mute")                           return CONSUMER_CONTROL_MUTE;
    if (k == "play_pause"  || k == "play")     return CONSUMER_CONTROL_PLAY_PAUSE;
    if (k == "next")                           return CONSUMER_CONTROL_SCAN_NEXT;
    if (k == "prev"        || k == "previous") return CONSUMER_CONTROL_SCAN_PREVIOUS;
    if (k == "stop")                           return CONSUMER_CONTROL_STOP;
    if (k == "rewind")                         return CONSUMER_CONTROL_REWIND;
    if (k == "fast_forward")                   return CONSUMER_CONTROL_FAST_FORWARD;
    if (k == "brightness_up")                  return CONSUMER_CONTROL_BRIGHTNESS_INCREMENT;
    if (k == "brightness_down")                return CONSUMER_CONTROL_BRIGHTNESS_DECREMENT;
    if (k == "browser_back")                   return CONSUMER_CONTROL_BACK;
    if (k == "browser_forward")                return CONSUMER_CONTROL_FORWARD;
    if (k == "search")                         return CONSUMER_CONTROL_SEARCH;
    if (k == "home")                           return CONSUMER_CONTROL_HOME;
    if (k == "calculator")                     return CONSUMER_CONTROL_CALCULATOR;
    if (k == "sleep")                          return CONSUMER_CONTROL_SLEEP;
    if (k == "power")                          return CONSUMER_CONTROL_POWER;
    return 0;
}

void handleMedia(const JsonDocument& inDoc) {
    if (!hidReady) {
        StaticJsonDocument<128> d; d["status"]="error"; d["message"]="USB HID not initialized"; sendJson(d); return;
    }
    const char* key = inDoc["key"] | "";
    uint16_t usage = (uint16_t)(inDoc["usage"] | 0);
    if (!usage) usage = mediaUsage(key);
    if (!usage) {
        StaticJsonDocument<224> d;
        d["status"]="error";
        d["message"]="Unknown media key (volume_up|volume_down|mute|play_pause|next|prev|stop|brightness_up|brightness_down|browser_back|browser_forward|search|home|calculator|sleep|power)";
        sendJson(d); return;
    }
    Consumer.press(usage);
    delay(12);
    Consumer.release();   // else the host repeats the key
    StaticJsonDocument<192> doc;
    doc["status"]="ok"; doc["reply"]="media_sent"; doc["key"]=key; doc["usage"]=usage;
    sendJson(doc);
}

void handleSystemCtl(const JsonDocument& inDoc) {
    String a = String(inDoc["action"] | "");
    a.toLowerCase();

    // Waking is not a HID report at all. A sleeping host has the bus suspended
    // and reads nothing we send, so the resume has to happen on the wire first.
    if (a == "wake") {
        bool wasSuspended = tud_suspended();
        bool allowed = wasSuspended ? tud_remote_wakeup() : false;
        if (wasSuspended) delay(150);
        SystemCtl.press(SYSTEM_CONTROL_WAKE_HOST);
        delay(12);
        SystemCtl.release();
        // Some machines ignore the System Wake usage but stir for any HID
        // activity, so follow it with a net-zero nudge.
        hidMouseMove(2, 0, 0, 0);
        delay(30);
        hidMouseMove(-2, 0, 0, 0);
        COM_SERIAL.printf("[WAKE] suspended=%d remote_wakeup_allowed=%d\n", wasSuspended, allowed);
        StaticJsonDocument<320> doc;
        doc["status"]="ok"; doc["reply"]="system_sent"; doc["action"]=a;
        doc["was_suspended"]=wasSuspended;
        doc["remote_wakeup"]=allowed;
        if (wasSuspended && !allowed) {
            doc["message"]="The host has not armed this device as a wake source. Enable 'Allow this device to wake the computer' on the HID keyboard in Device Manager, and allow USB wake in the BIOS.";
        }
        sendJson(doc);
        return;
    }

    uint8_t v = 0;
    if (a == "sleep" || a == "standby") v = SYSTEM_CONTROL_STANDBY;
    else if (a == "power_off")          v = SYSTEM_CONTROL_POWER_OFF;
    if (!v) {
        StaticJsonDocument<192> d;
        d["status"]="error"; d["message"]="Invalid action (sleep|wake|power_off)";
        sendJson(d); return;
    }
    SystemCtl.press(v);
    delay(12);
    SystemCtl.release();
    StaticJsonDocument<160> doc;
    doc["status"]="ok"; doc["reply"]="system_sent"; doc["action"]=a;
    sendJson(doc);
}

// ==================== GESTURES ====================
void handleGesture(const JsonDocument& inDoc) {
    if (!hidReady) {
        StaticJsonDocument<128> d; d["status"]="error"; d["message"]="USB HID not initialized"; sendJson(d); return;
    }
    String n = String(inDoc["name"] | "");
    n.toLowerCase();
    const char* combo = nullptr;
    // The same gesture is a different shortcut on macOS: Cmd where Windows uses
    // Ctrl, and its own combos for Mission Control and Spaces. mac() picks the
    // Mac string, everything else keeps the Windows/Linux one it always sent.
    bool mac = hostIsMac();
    #define GMAP(m,w) (mac ? (m) : (w))

    if      (n == "switch_app")    combo = GMAP("GUI+TAB",   "ALT+TAB");
    else if (n == "task_view")     combo = GMAP("CTRL+UP",   "GUI+TAB");        // Mission Control
    else if (n == "desktop_left")  combo = GMAP("CTRL+LEFT", "CTRL+GUI+LEFT");
    else if (n == "desktop_right") combo = GMAP("CTRL+RIGHT","CTRL+GUI+RIGHT");
    else if (n == "show_desktop")  combo = GMAP("F11",       "GUI+D");
    else if (n == "back")          combo = GMAP("GUI+LEFT",  "ALT+LEFT");
    else if (n == "forward")       combo = GMAP("GUI+RIGHT", "ALT+RIGHT");
    else if (n == "close_tab")     combo = GMAP("GUI+W",     "CTRL+W");
    else if (n == "new_tab")       combo = GMAP("GUI+T",     "CTRL+T");
    else if (n == "copy")          combo = GMAP("GUI+C",     "CTRL+C");
    else if (n == "paste")         combo = GMAP("GUI+V",     "CTRL+V");
    else if (n == "undo")          combo = GMAP("GUI+Z",     "CTRL+Z");
    else if (n == "zoom_in" || n == "zoom_out") {
        // Windows and Linux zoom with Ctrl + wheel; on macOS that is an
        // accessibility screen zoom that is off by default, so use the app-level
        // Cmd +/- that every Mac app honours.
        if (mac) {
            pressKeyCombo((n == "zoom_in") ? "GUI+PLUS" : "GUI+MINUS");
        } else {
            Keyboard.press(KEY_LEFT_CTRL);
            delay(25);
            hidMouseMove(0, 0, (n == "zoom_in") ? 1 : -1, 0);
            delay(25);
            Keyboard.releaseAll();
        }
        StaticJsonDocument<160> doc;
        doc["status"]="ok"; doc["reply"]="gesture_done"; doc["name"]=n;
        sendJson(doc);
        return;
    }
    #undef GMAP

    if (!combo) {
        const char* keys = inDoc["keys"] | "";
        if (strlen(keys) > 0) combo = keys;
    }
    if (!combo) {
        StaticJsonDocument<256> d;
        d["status"]="error";
        d["message"]="Unknown gesture (switch_app|task_view|desktop_left|desktop_right|show_desktop|back|forward|close_tab|new_tab|copy|paste|undo|zoom_in|zoom_out) or pass keys";
        sendJson(d); return;
    }
    pressKeyCombo(combo);
    StaticJsonDocument<192> doc;
    doc["status"]="ok"; doc["reply"]="gesture_done"; doc["name"]=n; doc["keys"]=combo;
    sendJson(doc);
}

void handlePresenter(const JsonDocument& inDoc) {
    if (!hidReady) {
        StaticJsonDocument<128> d; d["status"]="error"; d["message"]="USB HID not initialized"; sendJson(d); return;
    }
    String a = String(inDoc["action"] | "");
    a.toLowerCase();
    if      (a == "next")  pressKeyCombo("RIGHT");
    else if (a == "prev")  pressKeyCombo("LEFT");
    else if (a == "start") pressKeyCombo("F5");
    else if (a == "end")   pressKeyCombo("ESC");
    else if (a == "black") typeString("b");
    else if (a == "white") typeString("w");
    else {
        StaticJsonDocument<192> d;
        d["status"]="error"; d["message"]="Invalid action (next|prev|start|end|black|white)";
        sendJson(d); return;
    }
    StaticJsonDocument<160> doc;
    doc["status"]="ok"; doc["reply"]="presenter_done"; doc["action"]=a;
    sendJson(doc);
}

void handleMouseAbs(const JsonDocument& inDoc) {
    if (!mouseAbs) {
        StaticJsonDocument<224> d;
        d["status"]="error";
        d["message"]="Pointer is in relative mode - send pointer_mode absolute first (reboots)";
        sendJson(d); return;
    }
    hidMouseAbs(inDoc["x"] | 0, inDoc["y"] | 0);
    StaticJsonDocument<160> doc;
    doc["status"]="ok"; doc["reply"]="mouse_abs"; doc["x"]=absX; doc["y"]=absY;
    sendJson(doc);
}

// ==================== MODE SWITCHES (need a reboot: USB re-enumerates) ====================
void handlePointerMode(const JsonDocument& inDoc) {
    String m = String(inDoc["mode"] | "");
    m.toLowerCase();
    if (m != "relative" && m != "absolute") {
        StaticJsonDocument<192> d;
        d["status"]="error"; d["message"]="Invalid mode (relative|absolute)";
        sendJson(d); return;
    }
    bool want = (m == "absolute");
    bool changed = (want != absoluteMode);
    if (changed) {
        preferences.begin("hid_cfg", false);
        preferences.putBool("abs", want);
        preferences.end();
    }
    bool reboot = (inDoc["reboot"] | true) && changed;
    if (reboot) pendingRestartAt = millis() + 600;

    StaticJsonDocument<256> doc;
    doc["status"]="ok"; doc["reply"]="pointer_mode";
    doc["mode"]=m; doc["changed"]=changed; doc["rebooting"]=reboot;
    if (changed && !reboot) doc["note"]="Saved - takes effect on next boot";
    sendJson(doc);
}

void handleGamepadEnable(const JsonDocument& inDoc) {
    bool want = inDoc["on"] | false;
    bool changed = (want != gamepadEnabled);
    if (changed) {
        preferences.begin("hid_cfg", false);
        preferences.putBool("gpad", want);
        preferences.end();
    }
    bool reboot = (inDoc["reboot"] | true) && changed;
    if (reboot) pendingRestartAt = millis() + 600;

    StaticJsonDocument<256> doc;
    doc["status"]="ok"; doc["reply"]="gamepad_enable";
    doc["on"]=want; doc["changed"]=changed; doc["rebooting"]=reboot;
    if (changed && !reboot) doc["note"]="Saved - takes effect on next boot";
    sendJson(doc);
}

void handleGamepadCmd(const JsonDocument& inDoc) {
    if (!Gamepad) {
        StaticJsonDocument<192> d;
        d["status"]="error"; d["message"]="Gamepad disabled - send gamepad_enable {on:true} (reboots)";
        sendJson(d); return;
    }
    if (!inDoc["hat"].isNull()) Gamepad->hat((uint8_t)constrain((int)(inDoc["hat"] | 0), 0, 8));
    if (!inDoc["button"].isNull()) {
        uint8_t b = (uint8_t)constrain((int)(inDoc["button"] | 0), 0, 31);
        if (inDoc["pressed"] | false) Gamepad->pressButton(b);
        else                          Gamepad->releaseButton(b);
    }
    if (!inDoc["lx"].isNull() || !inDoc["ly"].isNull()) {
        Gamepad->leftStick((int8_t)constrain((int)(inDoc["lx"] | 0), -127, 127),
                           (int8_t)constrain((int)(inDoc["ly"] | 0), -127, 127));
    }
    if (!inDoc["rx"].isNull() || !inDoc["ry"].isNull()) {
        Gamepad->rightStick((int8_t)constrain((int)(inDoc["rx"] | 0), -127, 127),
                            (int8_t)constrain((int)(inDoc["ry"] | 0), -127, 127));
    }
    StaticJsonDocument<160> doc;
    doc["status"]="ok"; doc["reply"]="gamepad_sent";
    sendJson(doc);
}

// ==================== ACCESS PIN ====================
// A four digit PIN is only 10,000 guesses, which a script clears in minutes at
// socket speed. The lockout is what turns it into a real lock, so every path
// that tests a PIN goes through pinCheck() - including the inline HTTP token,
// which would otherwise be a way to guess around handleAuth entirely.
//
// Serial is exempt. Physical access is already game over, and it has to stay
// usable as the way back in after locking yourself out.
// Index of this address's guard, or -1. (An index rather than a pointer: the
// Arduino build writes prototypes above the type, so the signature stays plain.)
static int pinGuardFor(uint32_t ip, bool create) {
    int oldest = 0;
    for (int i = 0; i < PIN_GUARDS; i++) {
        if (pinGuards[i].ip == ip && ip) return i;
        if (pinGuards[i].lastAt < pinGuards[oldest].lastAt) oldest = i;
    }
    if (!create) return -1;
    // A full table forgets the device it heard from longest ago - never one that
    // is locked out right now, or an attacker could flush their own lock.
    for (int i = 0; i < PIN_GUARDS; i++) if (!pinGuards[i].ip) { oldest = i; break; }
    PinGuard& o = pinGuards[oldest];
    if (o.ip && o.lockUntil && (int32_t)(millis() - o.lockUntil) < 0) {
        for (int i = 0; i < PIN_GUARDS; i++) {
            PinGuard& c = pinGuards[i];
            if (!c.lockUntil || (int32_t)(millis() - c.lockUntil) >= 0) { oldest = i; break; }
        }
    }
    pinGuards[oldest] = PinGuard{ ip, 0, 0, millis() };
    return oldest;
}

static bool peerOnAp() { return (uint32_t)pinPeer != 0 && clientIsAp(pinPeer); }

static bool lanLocked() {
    return !peerOnAp() && lanLockUntil != 0 && (int32_t)(millis() - lanLockUntil) < 0;
}

bool pinLocked() {
    if (lanLocked()) return true;
    int i = pinGuardFor((uint32_t)pinPeer, false);
    return i >= 0 && pinGuards[i].lockUntil != 0 && (int32_t)(millis() - pinGuards[i].lockUntil) < 0;
}

uint32_t pinLockRemaining() {
    uint32_t r = 0;
    if (lanLocked()) r = (lanLockUntil - millis()) / 1000 + 1;
    int i = pinGuardFor((uint32_t)pinPeer, false);
    if (i >= 0 && pinGuards[i].lockUntil && (int32_t)(millis() - pinGuards[i].lockUntil) < 0) {
        uint32_t own = (pinGuards[i].lockUntil - millis()) / 1000 + 1;
        if (own > r) r = own;
    }
    return r;
}

bool pinCheck(const char* candidate) {
    PinGuard* g = &pinGuards[pinGuardFor((uint32_t)pinPeer, true)];
    g->lastAt = millis();
    if (authToken == candidate) {
        g->fails = 0;
        g->lockUntil = 0;
        return true;
    }
    if (g->fails < 250) g->fails++;
    // one free retry, then 5s, 15s, 60s, 5min for every attempt after that
    static const uint32_t backoff[] = { 0, 0, 5000, 15000, 60000, 300000 };
    uint32_t wait = backoff[g->fails < 6 ? g->fails : 5];
    if (wait) g->lockUntil = millis() + wait;
    if (!peerOnAp()) {
        if (millis() - lanWindowStart > LAN_FAIL_WINDOW) { lanWindowStart = millis(); lanFails = 0; }
        if (++lanFails >= LAN_FAIL_BUDGET) {
            lanLockUntil = millis() + LAN_LOCK_MS;
            COM_SERIAL.println("[AUTH] too many wrong PINs from the home network - locked there for 10 min (AP and USB still work)");
        }
    }
    COM_SERIAL.printf("[AUTH] wrong PIN from %s (%u in a row, lock %lus)\n",
                      pinPeer.toString().c_str(), g->fails, (unsigned long)(wait / 1000));
    return false;
}

void handleAuth(const JsonDocument& inDoc) {
    const char* tk = inDoc["token"] | "";
    StaticJsonDocument<224> doc;
    if (authToken.length() == 0) {
        doc["status"]="ok"; doc["reply"]="auth_ok"; doc["message"]="No PIN configured";
        sendJson(doc); return;
    }
    if (cmdSource != SRC_SERIAL && pinLocked()) {
        doc["status"]="error"; doc["reply"]="auth_locked";
        doc["retry_in"]=(int)pinLockRemaining();
        doc["message"]="Too many wrong PINs";
        sendJson(doc); return;
    }
    bool ok = (cmdSource == SRC_SERIAL) ? (authToken == tk) : pinCheck(tk);
    if (ok) {
        if (cmdSource == SRC_WS && wsClientNum >= 0 && wsClientNum < WS_MAX_CLIENTS) wsAuthed[wsClientNum] = true;
        doc["status"]="ok"; doc["reply"]="auth_ok";
    } else {
        doc["status"]="error"; doc["reply"]="auth_failed"; doc["message"]="Wrong PIN";
        if (pinLocked()) doc["retry_in"]=(int)pinLockRemaining();
    }
    sendJson(doc);
}

void handleSetAuth(const JsonDocument& inDoc) {
    String tk = String(inDoc["token"] | "");
    if (tk.length() > 0 && tk.length() < 4) {
        StaticJsonDocument<192> d;
        d["status"]="error"; d["message"]="PIN must be at least 4 characters (or empty to disable)";
        sendJson(d); return;
    }
    // Claiming the board. With no PIN yet, anyone who can reach it could set
    // one - including someone else on the home network - and that PIN would then
    // be theirs. So over WiFi the first PIN needs proof of ownership: the board's
    // own WiFi password, which only someone given it, or someone holding the
    // board (USB), can know. An open access point has no password to prove, so
    // its first PIN can only be set from that access point. USB needs nothing.
    if (cmdSource != SRC_SERIAL && authToken.length() == 0 && tk.length() > 0) {
        const char* given = inDoc["ap_password"] | "";
        bool ok = apPassword.length() ? (apPassword == given) : peerOnAp();
        if (!ok) {
            StaticJsonDocument<320> d;
            d["status"]="error"; d["reply"]="ap_password_required";
            d["message"] = apPassword.length()
                ? (given[0] ? "That is not this board's WiFi password."
                            : "Enter this board's WiFi password to set the first PIN.")
                : "This board's WiFi is open. Connect to it to set the first PIN.";
            sendJson(d); return;
        }
    }
    // The access PIN is what guards the vault unless a separate vault PIN was
    // set. Serial can call this without proving anything, so changing the PIN
    // has to destroy what it was guarding - otherwise anyone with the cable
    // could set a PIN of their own and then use it. Proving you knew the old
    // one keeps the vault: that is a user changing their PIN, not an attacker
    // taking it over.
    bool vaultGuardedByAccessPin = (vaultPinCode.length() == 0);
    bool provedOld = authToken.length() && (authToken == String(inDoc["old"] | ""));
    bool wipeVault = vaultGuardedByAccessPin && !provedOld && vaultCount() > 0;
    if (wipeVault) vaultWipeAll();

    authToken = tk;
    preferences.begin("hid_cfg", false);
    preferences.putString("auth", authToken);
    preferences.end();

    // Existing sockets must re-authenticate against the new PIN.
    for (int i = 0; i < WS_MAX_CLIENTS; i++) wsAuthed[i] = (authToken.length() == 0);
    if (authToken.length() && cmdSource == SRC_WS && wsClientNum >= 0 && wsClientNum < WS_MAX_CLIENTS) {
        wsAuthed[wsClientNum] = true;
    }
    if (wipeVault) COM_SERIAL.println("[AUTH] PIN replaced without the old one - vault erased");

    StaticJsonDocument<224> doc;
    doc["status"]="ok"; doc["reply"]="auth_set"; doc["auth_set"]=(authToken.length() > 0);
    doc["vault_wiped"]=wipeVault;
    sendJson(doc);
}

// The board's WiFi password, over the USB cable only. Holding the board is
// ownership; nothing that travels over WiFi ever carries this password.
void handleApPassword() {
    StaticJsonDocument<256> doc;
    if (cmdSource != SRC_SERIAL) {
        doc["status"]="error"; doc["reply"]="usb_only";
        doc["message"]="The WiFi password is only shown over the USB cable.";
        sendJson(doc); return;
    }
    doc["status"]="ok"; doc["reply"]="ap_password";
    doc["ssid"]=apSSID; doc["password"]=apPassword;
    doc["open"]=(apPassword.length() == 0); doc["generated"]=apPassGenerated;
    sendJson(doc);
}

void handleSetVerbose(const JsonDocument& inDoc) {
    verboseLog = inDoc["on"] | false;
    preferences.begin("hid_cfg", false);
    preferences.putBool("verbose", verboseLog);
    preferences.end();
    StaticJsonDocument<160> doc;
    doc["status"]="ok"; doc["reply"]="verbose_set"; doc["verbose"]=verboseLog;
    sendJson(doc);
}

// ==================== MACROS (stored in NVS, 8 slots) ====================
static bool macroSlotKeys(int slot, char* nameKey, char* stepKey) {
    if (slot < 0 || slot > 7) return false;
    snprintf(nameKey, 4, "n%d", slot);
    snprintf(stepKey, 4, "s%d", slot);
    return true;
}

void handleMacroSave(const JsonDocument& inDoc) {
    int slot = inDoc["slot"] | -1;
    char nk[4], sk[4];
    if (!macroSlotKeys(slot, nk, sk)) {
        StaticJsonDocument<160> d; d["status"]="error"; d["message"]="slot must be 0-7"; sendJson(d); return;
    }
    JsonArrayConst steps = inDoc["steps"].as<JsonArrayConst>();
    if (steps.isNull() || steps.size() == 0) {
        StaticJsonDocument<160> d; d["status"]="error"; d["message"]="steps[] is required"; sendJson(d); return;
    }
    String joined;
    for (JsonVariantConst v : steps) {
        if (joined.length()) joined += '\n';
        joined += v.as<const char*>();
    }
    if (joined.length() > 1000) {
        StaticJsonDocument<160> d; d["status"]="error"; d["message"]="macro too long (max 1000 chars)"; sendJson(d); return;
    }
    preferences.begin("macros", false);
    preferences.putString(nk, String(inDoc["name"] | "macro"));
    preferences.putString(sk, joined);
    preferences.end();

    StaticJsonDocument<192> doc;
    doc["status"]="ok"; doc["reply"]="macro_saved"; doc["slot"]=slot; doc["steps"]=(int)steps.size();
    sendJson(doc);
}

void handleMacroGet(const JsonDocument& inDoc) {
    int slot = inDoc["slot"] | -1;
    char nk[4], sk[4];
    if (!macroSlotKeys(slot, nk, sk)) {
        StaticJsonDocument<160> d; d["status"]="error"; d["message"]="slot must be 0-7"; sendJson(d); return;
    }
    preferences.begin("macros", true);
    String name = preferences.getString(nk, "");
    String body = preferences.getString(sk, "");
    preferences.end();

    DynamicJsonDocument doc(2560);
    doc["status"]="ok"; doc["reply"]="macro"; doc["slot"]=slot; doc["name"]=name;
    JsonArray arr = doc.createNestedArray("steps");
    int start = 0;
    while (start < (int)body.length()) {
        int nl = body.indexOf('\n', start);
        if (nl < 0) nl = body.length();
        arr.add(body.substring(start, nl));
        start = nl + 1;
    }
    sendJson(doc);
}

void handleMacroList() {
    preferences.begin("macros", true);
    StaticJsonDocument<768> doc;
    doc["status"]="ok"; doc["reply"]="macro_list";
    JsonArray arr = doc.createNestedArray("macros");
    for (int i = 0; i < 8; i++) {
        char nk[4], sk[4];
        macroSlotKeys(i, nk, sk);
        String name = preferences.getString(nk, "");
        if (name.length() == 0) continue;
        JsonObject o = arr.createNestedObject();
        o["slot"] = i;
        o["name"] = name;
    }
    preferences.end();
    sendJson(doc);
}

// Verbs: "key COMBO", "text literal", "delay ms", "click left|right|middle"
static void runMacroLine(const String& ln) {
    if      (ln.startsWith("key "))   pressKeyCombo(ln.substring(4).c_str());
    else if (ln.startsWith("text "))  typeString(ln.substring(5).c_str());
    else if (ln.startsWith("delay ")) delay(constrain(ln.substring(6).toInt(), 0, 5000));
    else if (ln.startsWith("click ")) hidMouseButtons(mapMouseButton(ln.substring(6).c_str()), 2, 1);
}

void handleMacroRun(const JsonDocument& inDoc) {
    if (!hidReady) {
        StaticJsonDocument<128> d; d["status"]="error"; d["message"]="USB HID not initialized"; sendJson(d); return;
    }
    int slot = inDoc["slot"] | -1;
    char nk[4], sk[4];
    if (!macroSlotKeys(slot, nk, sk)) {
        StaticJsonDocument<160> d; d["status"]="error"; d["message"]="slot must be 0-7"; sendJson(d); return;
    }
    preferences.begin("macros", true);
    String name = preferences.getString(nk, "");
    String body = preferences.getString(sk, "");
    preferences.end();
    if (body.length() == 0) {
        StaticJsonDocument<160> d; d["status"]="error"; d["message"]="slot is empty"; sendJson(d); return;
    }

    int executed = 0, start = 0;
    while (start < (int)body.length()) {
        int nl = body.indexOf('\n', start);
        if (nl < 0) nl = body.length();
        String ln = body.substring(start, nl);
        ln.trim();
        if (ln.length()) { runMacroLine(ln); executed++; delay(20); }
        start = nl + 1;
    }

    StaticJsonDocument<224> doc;
    doc["status"]="ok"; doc["reply"]="macro_done";
    doc["slot"]=slot; doc["name"]=name; doc["steps"]=executed;
    sendJson(doc);
}

// ==================== ACTIONS (for agents and scripts) ====================
// One name per thing a person does - "copy", "lock", "screenshot_area" - and the
// board presses the keys that do it on the computer it is plugged into. An agent
// (or a typed-decision model choosing from a list) sends a word, not a key combo
// it had to look up and get right per OS. {"cmd":"actions"} lists the names that
// work on this computer. Every row is also a row of the dashboard's shortcut
// table with the same label and the same keys - tests/static checks that, so the
// two never disagree. An empty column: that OS has no equivalent, and the action
// is refused there rather than faked.
// Columns: name, label (as in the dashboard), macOS, Windows, Linux (GNOME).
static const char* const ACTIONS[][5] = {
    {"copy",              "Copy",                     "GUI+C",           "CTRL+C",          "CTRL+C"},
    {"cut",               "Cut",                      "GUI+X",           "CTRL+X",          "CTRL+X"},
    {"paste",             "Paste",                    "GUI+V",           "CTRL+V",          "CTRL+V"},
    {"paste_plain",       "Paste without formatting", "GUI+ALT+SHIFT+V", "CTRL+SHIFT+V",    "CTRL+SHIFT+V"},
    {"undo",              "Undo",                     "GUI+Z",           "CTRL+Z",          "CTRL+Z"},
    {"redo",              "Redo",                     "GUI+SHIFT+Z",     "CTRL+Y",          "CTRL+SHIFT+Z"},
    {"select_all",        "Select all",               "GUI+A",           "CTRL+A",          "CTRL+A"},
    {"save",              "Save",                     "GUI+S",           "CTRL+S",          "CTRL+S"},
    {"save_as",           "Save as",                  "GUI+SHIFT+S",     "CTRL+SHIFT+S",    "CTRL+SHIFT+S"},
    {"new",               "New",                      "GUI+N",           "CTRL+N",          "CTRL+N"},
    {"open_file",         "Open",                     "GUI+O",           "CTRL+O",          "CTRL+O"},
    {"print",             "Print",                    "GUI+P",           "CTRL+P",          "CTRL+P"},
    {"find",              "Find",                     "GUI+F",           "CTRL+F",          "CTRL+F"},
    {"find_next",         "Find next",                "GUI+G",           "F3",              "CTRL+G"},
    {"find_previous",     "Find previous",            "GUI+SHIFT+G",     "SHIFT+F3",        "CTRL+SHIFT+G"},
    {"replace",           "Replace",                  "GUI+ALT+F",       "CTRL+H",          "CTRL+H"},
    {"close_window",      "Close window",             "GUI+W",           "CTRL+W",          "CTRL+W"},
    {"quit",              "Quit application",         "GUI+Q",           "ALT+F4",          "CTRL+Q"},
    {"preferences",       "Preferences",              "GUI+,",           "CTRL+,",          "CTRL+,"},
    {"bold",              "Bold",                     "GUI+B",           "CTRL+B",          "CTRL+B"},
    {"italic",            "Italic",                   "GUI+I",           "CTRL+I",          "CTRL+I"},
    {"underline",         "Underline",                "GUI+U",           "CTRL+U",          "CTRL+U"},
    {"word_left",         "Word left",                "ALT+LEFT",        "CTRL+LEFT",       "CTRL+LEFT"},
    {"word_right",        "Word right",               "ALT+RIGHT",       "CTRL+RIGHT",      "CTRL+RIGHT"},
    {"line_start",        "Start of line",            "GUI+LEFT",        "HOME",            "HOME"},
    {"line_end",          "End of line",              "GUI+RIGHT",       "END",             "END"},
    {"doc_start",         "Start of document",        "GUI+UP",          "CTRL+HOME",       "CTRL+HOME"},
    {"doc_end",           "End of document",          "GUI+DOWN",        "CTRL+END",        "CTRL+END"},
    {"select_word_left",  "Select word left",         "ALT+SHIFT+LEFT",  "CTRL+SHIFT+LEFT", "CTRL+SHIFT+LEFT"},
    {"select_word_right", "Select word right",        "ALT+SHIFT+RIGHT", "CTRL+SHIFT+RIGHT","CTRL+SHIFT+RIGHT"},
    {"select_to_line_start","Select to start of line","GUI+SHIFT+LEFT",  "SHIFT+HOME",      "SHIFT+HOME"},
    {"select_to_line_end","Select to end of line",    "GUI+SHIFT+RIGHT", "SHIFT+END",       "SHIFT+END"},
    {"delete_word_left",  "Delete word left",         "ALT+BACKSPACE",   "CTRL+BACKSPACE",  "CTRL+BACKSPACE"},
    {"delete_word_right", "Delete word right",        "ALT+DELETE",      "CTRL+DELETE",     "CTRL+DELETE"},
    {"page_up",           "Page up",                  "PAGEUP",          "PAGEUP",          "PAGEUP"},
    {"page_down",         "Page down",                "PAGEDOWN",        "PAGEDOWN",        "PAGEDOWN"},
    {"switch_app",        "Switch app",               "GUI+TAB",         "ALT+TAB",         "ALT+TAB"},
    {"switch_app_back",   "Switch app backwards",     "GUI+SHIFT+TAB",   "ALT+SHIFT+TAB",   "ALT+SHIFT+TAB"},
    {"overview",          "Mission Control / Task view","CTRL+UP",       "GUI+TAB",         "GUI"},
    {"show_desktop",      "Show desktop",             "F11",             "GUI+D",           "GUI+D"},
    {"minimise",          "Minimise window",          "GUI+M",           "GUI+DOWN",        "GUI+H"},
    {"maximise",          "Maximise / full screen",   "CTRL+GUI+F",      "GUI+UP",          "GUI+UP"},
    {"snap_left",         "Snap window left",         "",                "GUI+LEFT",        "GUI+LEFT"},
    {"snap_right",        "Snap window right",        "",                "GUI+RIGHT",       "GUI+RIGHT"},
    {"next_desktop",      "Next desktop",             "CTRL+RIGHT",      "CTRL+GUI+RIGHT",  "GUI+PAGEDOWN"},
    {"previous_desktop",  "Previous desktop",         "CTRL+LEFT",       "CTRL+GUI+LEFT",   "GUI+PAGEUP"},
    {"close_app_window",  "Close the active window",  "GUI+W",           "ALT+F4",          "ALT+F4"},
    {"lock",              "Lock the screen",          "CTRL+GUI+Q",      "GUI+L",           "GUI+L"},
    {"search",            "Search",                   "GUI+SPACE",       "GUI+S",           "GUI"},
    {"file_manager",      "File manager",             "",                "GUI+E",           "GUI+E"},
    {"settings",          "Settings",                 "",                "GUI+I",           ""},
    {"run_dialog",        "Run dialog",               "",                "GUI+R",           "ALT+F2"},
    {"task_manager",      "Force quit / Task Manager","GUI+ALT+ESC",     "CTRL+SHIFT+ESC",  ""},
    {"emoji",             "Emoji picker",             "CTRL+GUI+SPACE",  "GUI+.",           "CTRL+."},
    {"clipboard_history", "Clipboard history",        "",                "GUI+V",           ""},
    {"notifications",     "Notifications",            "",                "GUI+N",           "GUI+V"},
    {"context_menu",      "Context menu (no Menu key)","",               "SHIFT+F10",       "SHIFT+F10"},
    {"screenshot",        "Whole screen",             "GUI+SHIFT+3",     "GUI+PRINTSCREEN", "SHIFT+PRINTSCREEN"},
    {"screenshot_area",   "Selected area",            "GUI+SHIFT+4",     "GUI+SHIFT+S",     "PRINTSCREEN"},
    {"screenshot_window", "Active window",            "",                "ALT+PRINTSCREEN", "ALT+PRINTSCREEN"},
    {"new_tab",           "New tab",                  "GUI+T",           "CTRL+T",          "CTRL+T"},
    {"close_tab",         "Close tab",                "GUI+W",           "CTRL+W",          "CTRL+W"},
    {"reopen_tab",        "Reopen closed tab",        "GUI+SHIFT+T",     "CTRL+SHIFT+T",    "CTRL+SHIFT+T"},
    {"next_tab",          "Next tab",                 "CTRL+TAB",        "CTRL+TAB",        "CTRL+TAB"},
    {"previous_tab",      "Previous tab",             "CTRL+SHIFT+TAB",  "CTRL+SHIFT+TAB",  "CTRL+SHIFT+TAB"},
    {"address_bar",       "Address bar",              "GUI+L",           "CTRL+L",          "CTRL+L"},
    {"reload",            "Reload",                   "GUI+R",           "CTRL+R",          "CTRL+R"},
    {"back",              "Back",                     "GUI+LEFT",        "ALT+LEFT",        "ALT+LEFT"},
    {"forward",           "Forward",                  "GUI+RIGHT",       "ALT+RIGHT",       "ALT+RIGHT"},
    {"private_window",    "Private window",           "GUI+SHIFT+N",     "CTRL+SHIFT+N",    "CTRL+SHIFT+N"},
    {"zoom_in",           "Zoom in",                  "GUI+EQUALS",      "CTRL+EQUALS",     "CTRL+EQUALS"},
    {"zoom_out",          "Zoom out",                 "GUI+MINUS",       "CTRL+MINUS",      "CTRL+MINUS"},
    {"zoom_reset",        "Reset zoom",               "GUI+0",           "CTRL+0",          "CTRL+0"},
    {"full_screen",       "Full screen",              "CTRL+GUI+F",      "F11",             "F11"},
    {"history",           "History",                  "GUI+Y",           "CTRL+H",          "CTRL+H"},
    {"downloads",         "Downloads",                "GUI+SHIFT+J",     "CTRL+J",          "CTRL+J"},
    {"bookmark",          "Bookmark this page",       "GUI+D",           "CTRL+D",          "CTRL+D"},
    {"new_folder",        "New folder",               "GUI+SHIFT+N",     "CTRL+SHIFT+N",    "CTRL+SHIFT+N"},
    {"rename",            "Rename",                   "ENTER",           "F2",              "F2"},
    {"go_up",             "Go up a folder",           "GUI+UP",          "ALT+UP",          "ALT+UP"},
    {"trash",             "Move to trash",            "GUI+BACKSPACE",   "DELETE",          "DELETE"},
    {"properties",        "Get info / properties",    "GUI+I",           "ALT+ENTER",       "CTRL+I"},
};
static const int ACTION_COUNT = sizeof(ACTIONS) / sizeof(ACTIONS[0]);

static int actionIndex(const char* name) {
    for (int i = 0; i < ACTION_COUNT; i++) if (!strcmp(ACTIONS[i][0], name)) return i;
    return -1;
}
// The column for an OS; an unknown host gets the Windows keys, which is also
// what the rest of the firmware assumes before detection settles.
static int actionCol(HostOS os) { return os == OS_MAC ? 2 : os == OS_LINUX ? 4 : 3; }

static HostOS osFromDoc(const JsonDocument& inDoc) {
    const char* o = inDoc["os"] | "";
    if (!strcmp(o, "mac"))     return OS_MAC;
    if (!strcmp(o, "windows")) return OS_WINDOWS;
    if (!strcmp(o, "linux"))   return OS_LINUX;
    return hostOs();
}

// The one place an action becomes keys. Returns nullptr when the name is not an
// action, "" when it has no equivalent on this OS.
static const char* actionKeys(const char* name, HostOS os) {
    int i = actionIndex(name);
    return i < 0 ? nullptr : ACTIONS[i][actionCol(os)];
}

// Open an app the way a person would: the OS's own search launcher, the name,
// Enter. Windows Start and macOS Spotlight and GNOME's overview all take the name
// you see ("Calculator", "System Settings", "Text Editor"). The pauses let the
// launcher appear and find it; they are the slowest step, not the HID.
static void openApp(const char* app, HostOS os) {
    pressKeyCombo(os == OS_MAC ? "GUI+SPACE" : "GUI");
    delay(os == OS_MAC ? 500 : 700);
    typeString(app);
    delay(os == OS_WINDOWS ? 900 : 700);
    pressKeyCombo("ENTER");
}

// Run an exact command or URL through the run box (Windows Win+R, GNOME Alt+F2).
// macOS has no run box, so it is refused there.
static bool runLine(const char* line, HostOS os) {
    if (os == OS_MAC) return false;
    if (os == OS_LINUX) {
        pressKeyCombo("ALT+F2");
        delay(600);
        // A URL is not a command: GNOME's box runs commands, so hand it to xdg-open.
        if (strstr(line, "://")) typeString("xdg-open ");
        typeString(line);
    } else {
        pressKeyCombo("GUI+R");
        delay(500);
        typeString(line);
    }
    delay(150);
    pressKeyCombo("ENTER");
    return true;
}

static bool needHid() {
    if (hidReady) return true;
    StaticJsonDocument<128> d; d["status"]="error"; d["message"]="USB HID not initialized"; sendJson(d);
    return false;
}

void handleDo(const JsonDocument& inDoc) {
    if (!needHid()) return;
    const char* a = inDoc["a"] | (inDoc["action"] | "");
    HostOS os = osFromDoc(inDoc);
    const char* keys = actionKeys(a, os);
    StaticJsonDocument<256> d;
    if (!keys) {
        d["status"]="error"; d["reply"]="unknown_action";
        d["message"]="Not an action. {\"cmd\":\"actions\"} lists them.";
    } else if (!keys[0]) {
        d["status"]="error"; d["reply"]="no_equivalent";
        d["message"]="No equivalent on this computer's OS";
        d["os"]=hostOsName(os);
    } else {
        pressKeyCombo(keys);
        d["status"]="ok"; d["reply"]="done"; d["a"]=a; d["keys"]=keys; d["os"]=hostOsName(os);
    }
    sendJson(d);
}

// The names that work on this computer, as one space-separated string: about a
// kilobyte, and the cheapest form for a model to read or choose from. With
// "keys":true, name -> keys instead.
void handleActions(const JsonDocument& inDoc) {
    HostOS os = osFromDoc(inDoc);
    int col = actionCol(os);
    bool withKeys = inDoc["keys"] | false;
    DynamicJsonDocument d(withKeys ? 6144 : 2560);
    d["status"]="ok"; d["reply"]="actions"; d["os"]=hostOsName(os);
    if (withKeys) {
        JsonObject m = d.createNestedObject("a");
        for (int i = 0; i < ACTION_COUNT; i++) if (ACTIONS[i][col][0]) m[ACTIONS[i][0]] = ACTIONS[i][col];
    } else {
        String names;
        for (int i = 0; i < ACTION_COUNT; i++) {
            if (!ACTIONS[i][col][0]) continue;
            if (names.length()) names += ' ';
            names += ACTIONS[i][0];
        }
        d["a"] = names;
    }
    d["also"] = "open <app> | run <command or url> | type <text> | key <combo> | wait <ms> | click left|right|middle";
    sendJson(d);
}

void handleOpen(const JsonDocument& inDoc) {
    if (!needHid()) return;
    HostOS os = osFromDoc(inDoc);
    const char* app = inDoc["app"] | "";
    const char* run = inDoc["run"] | "";
    StaticJsonDocument<256> d;
    if (app[0]) {
        openApp(app, os);
        d["status"]="ok"; d["reply"]="opened"; d["via"]= os == OS_MAC ? "spotlight" : os == OS_LINUX ? "overview" : "start";
    } else if (run[0]) {
        if (runLine(run, os)) { d["status"]="ok"; d["reply"]="opened"; d["via"]="run"; }
        else { d["status"]="error"; d["reply"]="no_equivalent"; d["message"]="macOS has no run box - use app"; }
    } else {
        d["status"]="error"; d["message"]="Give app (a name, as in the OS's search) or run (a command or URL)";
    }
    d["os"]=hostOsName(os);
    sendJson(d);
}

// Several steps in one call, so an agent does not pay a round trip per key.
// "s" is an array of lines - an array, so text may hold any character.
//   do NAME | open APP | run LINE | type TEXT | key COMBO | wait MS | click BUTTON
// At most 32 steps and 10 s of waiting. It stops at the first step it cannot do
// and says which.
#define STEPS_MAX       32
#define STEPS_WAIT_MAX  10000
void handleSteps(const JsonDocument& inDoc) {
    if (!needHid()) return;
    JsonArrayConst s = inDoc["s"].as<JsonArrayConst>();
    HostOS os = osFromDoc(inDoc);
    StaticJsonDocument<320> d;
    if (s.isNull() || s.size() == 0 || s.size() > STEPS_MAX) {
        d["status"]="error"; d["message"]="s is an array of 1 to 32 steps"; sendJson(d); return;
    }
    uint32_t waited = 0;
    int i = 0;
    for (JsonVariantConst v : s) {
        String ln = v.as<String>();
        int sp = ln.indexOf(' ');
        String verb = sp < 0 ? ln : ln.substring(0, sp);
        String arg  = sp < 0 ? String("") : ln.substring(sp + 1);
        const char* bad = nullptr;
        if (verb == "do") {
            const char* k = actionKeys(arg.c_str(), os);
            if (!k) bad = "unknown action"; else if (!k[0]) bad = "no equivalent on this OS"; else pressKeyCombo(k);
        }
        else if (verb == "open")  { if (arg.length()) openApp(arg.c_str(), os); else bad = "open needs an app name"; }
        else if (verb == "run")   { if (!arg.length() || !runLine(arg.c_str(), os)) bad = "run needs a line, and not on macOS"; }
        else if (verb == "type")  typeString(arg.c_str());
        else if (verb == "key")   { if (arg.length()) pressKeyCombo(arg.c_str()); else bad = "key needs a combo"; }
        else if (verb == "click") hidMouseButtons(mapMouseButton(arg.length() ? arg.c_str() : "left"), 2, 1);
        else if (verb == "wait") {
            uint32_t ms = (uint32_t)constrain(arg.toInt(), 0, 5000);
            if (waited + ms > STEPS_WAIT_MAX) bad = "more than 10 s of waiting";
            else { delay(ms); waited += ms; }
        }
        else bad = "unknown step";
        if (bad) {
            d["status"]="error"; d["reply"]="steps_stopped"; d["at"]=i; d["message"]=bad; d["done"]=i;
            sendJson(d); return;
        }
        i++;
        delay(20);
    }
    d["status"]="ok"; d["reply"]="steps_done"; d["done"]=i; d["os"]=hostOsName(os);
    sendJson(d);
}

void handleMacroDelete(const JsonDocument& inDoc) {
    int slot = inDoc["slot"] | -1;
    char nk[4], sk[4];
    if (!macroSlotKeys(slot, nk, sk)) {
        StaticJsonDocument<160> d; d["status"]="error"; d["message"]="slot must be 0-7"; sendJson(d); return;
    }
    preferences.begin("macros", false);
    preferences.remove(nk);
    preferences.remove(sk);
    preferences.end();
    StaticJsonDocument<160> doc;
    doc["status"]="ok"; doc["reply"]="macro_deleted"; doc["slot"]=slot;
    sendJson(doc);
}

// ==================== DASHBOARD LAYOUT (custom buttons / knobs) ====================
// Held on the board rather than in one browser's localStorage so the same custom
// controls appear on every phone or laptop that opens the dashboard.
#define UI_CONFIG_MAX 3000

void handleUiSave(const JsonDocument& inDoc) {
    const char* data = inDoc["data"] | "";
    size_t n = strlen(data);
    if (n > UI_CONFIG_MAX) {
        StaticJsonDocument<192> d;
        d["status"]="error"; d["message"]="layout too large"; d["max"]=UI_CONFIG_MAX;
        sendJson(d); return;
    }
    preferences.begin("hid_cfg", false);
    if (n == 0) preferences.remove("ui");
    else        preferences.putString("ui", data);
    preferences.end();

    StaticJsonDocument<192> doc;
    doc["status"]="ok"; doc["reply"]="ui_saved"; doc["bytes"]=(int)n;
    sendJson(doc);
}

void handleUiGet() {
    preferences.begin("hid_cfg", true);
    String data = preferences.getString("ui", "");
    preferences.end();

    StaticJsonDocument<UI_CONFIG_MAX + 256> doc;
    doc["status"]="ok"; doc["reply"]="ui"; doc["data"]=data;
    sendJson(doc);
}

// ==================== DISCRETE KEY PRESS / RELEASE ====================
// `press` sends a whole combo and lets go. These hold a key down instead, so the
// on-screen keyboard can do real chords - hold Alt with one finger, strike Tab
// with another - the way a physical keyboard behaves.
uint8_t hidKeyFor(const char* name) {
    if (!name || !name[0]) return 0;
    String n(name);
    n.toUpperCase();
    uint8_t k = mapKeyName(n);
    if (k) return k;
    if (strlen(name) == 1) {
        char c = name[0];
        if (c >= 'A' && c <= 'Z') c += 32;   // HID wants the unshifted key
        return (uint8_t)c;
    }
    return 0;
}

void handleKeyDown(const JsonDocument& inDoc) {
    if (!hidReady) {
        StaticJsonDocument<128> d; d["status"]="error"; d["message"]="USB HID not initialized"; sendJson(d); return;
    }
    const char* key = inDoc["key"] | "";
    uint8_t k = hidKeyFor(key);
    if (!k) {
        StaticJsonDocument<192> d; d["status"]="error"; d["message"]="Unknown key"; d["key"]=key; sendJson(d); return;
    }
    usbWakeHost();
    Keyboard.press(k);
    if (verboseLog) COM_SERIAL.printf("[KEY] down %s\n", key);    StaticJsonDocument<160> doc;
    doc["status"]="ok"; doc["reply"]="key_down"; doc["key"]=key;
    sendJson(doc);
}

void handleKeyUp(const JsonDocument& inDoc) {
    if (!hidReady) {
        StaticJsonDocument<128> d; d["status"]="error"; d["message"]="USB HID not initialized"; sendJson(d); return;
    }
    const char* key = inDoc["key"] | "";
    uint8_t k = hidKeyFor(key);
    if (!k) {
        StaticJsonDocument<192> d; d["status"]="error"; d["message"]="Unknown key"; d["key"]=key; sendJson(d); return;
    }
    Keyboard.release(k);
    if (verboseLog) COM_SERIAL.printf("[KEY] up %s\n", key);
    StaticJsonDocument<160> doc;
    doc["status"]="ok"; doc["reply"]="key_up"; doc["key"]=key;
    sendJson(doc);
}

// Safety net: a dashboard that reloads mid-chord would otherwise leave a
// modifier stuck down on the host.
void handleKeyReleaseAll() {
    Keyboard.releaseAll();
    StaticJsonDocument<128> doc;
    doc["status"]="ok"; doc["reply"]="key_release_all";
    sendJson(doc);
}

// ==================== PASSWORD VAULT ====================
// Secrets the user asks the board to remember and type for them - a login
// password, a card PIN, a door code. The point is that it is typed by a device
// rather than by the user, so it is never on a keyboard somebody could be
// watching and never in a clipboard.
//
// Every rule here exists to stop one thing: a secret leaving the board without
// the PIN being presented for that specific act. See SECURITY.md.
//
//  - Nothing is stored until an access PIN exists. Without one, a stored secret
//    is a secret anybody in radio range can use.
//  - Getting a secret OUT - reading it back or typing it - needs the PIN in that
//    request, every time. There is no session that keeps the vault open, so
//    "type this into the focused window" asks again each time; that keystroke is
//    indistinguishable from the secret itself.
//  - Putting one IN, renaming it or deleting it does not, because none of those
//    discloses anything. Asking for a PIN to save a password nobody can read
//    back buys nothing and costs the user every time. Tampering is the accepted
//    price; disclosure is not.
//  - The gate is checked on EVERY transport, USB serial included. This is the
//    one exception to serial being ungated: serial is open so the board can be
//    recovered, not so it can be read. It is rate limited on serial too, because
//    an ungated 4 digit guess loop over a cable is no guess loop at all.
//  - Wiping is deliberately NOT gated. Destroying a secret discloses nothing and
//    it is the way back if the vault PIN is forgotten.
//
// The slot counts and the vault's own PIN are declared with the other auth
// globals near the top, because set_auth and storage_info need them.
static String vaultKey(char kind, int slot) { return String(kind) + String(slot); }

const char* vaultKindName(uint8_t k) { return k == VK_PIN ? "pin" : "password"; }

bool vaultLocked() {
    return vaultLockUntil != 0 && (int32_t)(millis() - vaultLockUntil) < 0;
}
uint32_t vaultLockRemaining() {
    return vaultLocked() ? ((vaultLockUntil - millis()) / 1000) + 1 : 0;
}

// Which PIN opens the vault right now. The separate one when it exists, the
// access PIN otherwise, and "" when neither is set - in which case the vault
// refuses to hold anything at all.
const String& vaultGateCode() { return vaultPinCode.length() ? vaultPinCode : authToken; }

// Same escalating backoff as the access PIN, on its own counter so guessing at
// the vault cannot lock a user out of the dashboard and vice versa.
bool vaultPinCheck(const String& candidate) {
    if (vaultGateCode().length() && vaultGateCode() == candidate) {
        vaultFails = 0;
        vaultLockUntil = 0;
        return true;
    }
    if (vaultFails < 250) vaultFails++;
    static const uint32_t backoff[] = { 0, 0, 5000, 15000, 60000, 300000 };
    uint32_t wait = backoff[vaultFails < 6 ? vaultFails : 5];
    if (wait) vaultLockUntil = millis() + wait;
    COM_SERIAL.printf("[VAULT] wrong PIN (%u consecutive, lock %lus)\n",
                      vaultFails, (unsigned long)(wait / 1000));
    return false;
}

// One door for every vault command that touches a secret. Replies with the
// reason and returns false when it is shut, so each handler is two lines.
bool vaultOpen(const JsonDocument& inDoc) {
    StaticJsonDocument<256> d;
    if (vaultGateCode().length() == 0) {
        d["status"]="error"; d["reply"]="vault_locked";
        d["message"]="Set an access PIN first. Without one the vault would guard nothing.";
        sendJson(d); return false;
    }
    if (vaultLocked()) {
        d["status"]="error"; d["reply"]="vault_locked";
        d["retry_in"]=(int)vaultLockRemaining();
        d["message"]="Too many wrong PINs";
        sendJson(d); return false;
    }
    if (!vaultPinCheck(String(inDoc["pin"] | ""))) {
        d["status"]="error"; d["reply"]="vault_denied";
        d["message"]="PIN required";
        if (vaultLocked()) d["retry_in"]=(int)vaultLockRemaining();
        sendJson(d); return false;
    }
    return true;
}

int vaultCount() {
    int n = 0;
    preferences.begin("vault", true);
    for (int i = 0; i < VAULT_SLOTS; i++) {
        if (preferences.getString(vaultKey('l', i).c_str(), "").length()) n++;
    }
    preferences.end();
    return n;
}

// Used by set_auth: the access PIN is what guards the vault unless a separate
// one was set, so changing it without proving you knew it has to destroy what
// it was guarding. Otherwise ungated serial set_auth would be a way in.
void vaultWipeAll() {
    preferences.begin("vault", false);
    preferences.clear();
    preferences.end();
    vaultPinCode = "";
    vaultFails = 0;
    vaultLockUntil = 0;
}

void handleVaultList() {
    DynamicJsonDocument doc(2048);
    doc["status"]="ok"; doc["reply"]="vault_list";
    JsonArray a = doc.createNestedArray("entries");
    preferences.begin("vault", true);
    for (int i = 0; i < VAULT_SLOTS; i++) {
        String label = preferences.getString(vaultKey('l', i).c_str(), "");
        if (label.length() == 0) continue;
        JsonObject o = a.createNestedObject();
        o["slot"]  = i;
        o["label"] = label;                // purpose and kind only - never the secret,
        o["type"]  = vaultKindName(preferences.getUChar(vaultKey('t', i).c_str(), VK_PASSWORD));
    }                                      // and not the length either
    preferences.end();
    doc["slots"]        = VAULT_SLOTS;
    doc["auth_set"]     = authToken.length() > 0;
    doc["vault_pin_set"]= vaultPinCode.length() > 0;
    doc["locked"]       = vaultLocked();
    if (vaultLocked()) doc["retry_in"] = (int)vaultLockRemaining();
    sendJson(doc);
}

void handleVaultSave(const JsonDocument& inDoc) {
    if (authToken.length() == 0) {
        StaticJsonDocument<288> d;
        d["status"]="error"; d["reply"]="vault_locked";
        d["message"]="Set an access PIN first. Without one, anybody in radio range could have these typed out.";
        sendJson(d); return;
    }

    String label = String(inDoc["label"] | "");
    label.trim();
    if (label.length() > VAULT_LABEL_MAX) label = label.substring(0, VAULT_LABEL_MAX);
    String secret = String(inDoc["secret"] | "");
    if (secret.length() > VAULT_SECRET_MAX) secret = secret.substring(0, VAULT_SECRET_MAX);

    int slot = inDoc["slot"] | -1;
    preferences.begin("vault", false);
    bool occupied = (slot >= 0 && slot < VAULT_SLOTS)
                    && preferences.getString(vaultKey('l', slot).c_str(), "").length() > 0;
    // Editing must not need the secret retyped, because the dashboard is never
    // given it to put back in the field. An empty secret keeps the stored one.
    if (label.length() == 0 || (secret.length() == 0 && !occupied)) {
        preferences.end();
        StaticJsonDocument<224> d;
        d["status"]="error"; d["message"]="label and secret are both required";
        sendJson(d); return;
    }
    if (slot < 0 || slot >= VAULT_SLOTS) {
        for (int i = 0; i < VAULT_SLOTS; i++) {
            if (preferences.getString(vaultKey('l', i).c_str(), "").length() == 0) { slot = i; break; }
        }
    }
    if (slot < 0 || slot >= VAULT_SLOTS) {
        preferences.end();
        StaticJsonDocument<192> d;
        d["status"]="error"; d["message"]="All vault slots are in use";
        sendJson(d); return;
    }
    String kindReq = String(inDoc["type"] | "");
    kindReq.toLowerCase();
    uint8_t kind = occupied ? preferences.getUChar(vaultKey('t', slot).c_str(), VK_PASSWORD) : VK_PASSWORD;
    if (kindReq == "pin")           kind = VK_PIN;
    else if (kindReq == "password") kind = VK_PASSWORD;
    // A PIN that is not digits is a password wearing the wrong label, and the
    // dashboard shows a number pad for it. Refuse rather than silently store it.
    if (kind == VK_PIN && secret.length()) {
        for (size_t i = 0; i < secret.length(); i++) {
            if (!isdigit((unsigned char)secret[i])) {
                preferences.end();
                StaticJsonDocument<224> d;
                d["status"]="error"; d["message"]="A PIN can only contain digits";
                sendJson(d); return;
            }
        }
    }

    preferences.putString(vaultKey('l', slot).c_str(), label);
    if (secret.length()) preferences.putString(vaultKey('s', slot).c_str(), secret);
    preferences.putUChar(vaultKey('t', slot).c_str(), kind);
    preferences.end();
    // Label and kind only. The secret and its length are never written to a log.
    COM_SERIAL.printf("[VAULT] saved slot %d (%s, %s)\n", slot, label.c_str(), vaultKindName(kind));

    StaticJsonDocument<256> doc;
    doc["status"]="ok"; doc["reply"]="vault_saved"; doc["slot"]=slot;
    doc["label"]=label; doc["type"]=vaultKindName(kind);
    sendJson(doc);
}

// The one command that hands a secret back, and the reason the gate above exists.
void handleVaultGet(const JsonDocument& inDoc) {
    if (!vaultOpen(inDoc)) return;
    int slot = inDoc["slot"] | -1;
    if (slot < 0 || slot >= VAULT_SLOTS) {
        StaticJsonDocument<160> d;
        d["status"]="error"; d["message"]="Invalid slot"; sendJson(d); return;
    }
    preferences.begin("vault", true);
    String label  = preferences.getString(vaultKey('l', slot).c_str(), "");
    String secret = preferences.getString(vaultKey('s', slot).c_str(), "");
    uint8_t kind  = preferences.getUChar(vaultKey('t', slot).c_str(), VK_PASSWORD);
    preferences.end();
    if (label.length() == 0) {
        StaticJsonDocument<160> d;
        d["status"]="error"; d["message"]="Empty slot"; sendJson(d); return;
    }
    COM_SERIAL.printf("[VAULT] revealed slot %d (%s)\n", slot, label.c_str());
    StaticJsonDocument<384> doc;
    doc["status"]="ok"; doc["reply"]="vault_secret"; doc["slot"]=slot;
    doc["label"]=label; doc["type"]=vaultKindName(kind);
    doc["secret"]=secret;
    sendJson(doc);
}

// Types a stored secret into whatever the host has focused. The PIN is demanded
// here exactly as it is for a reveal: the keystrokes ARE the secret, and the
// board cannot see where they are going.
void handleVaultType(const JsonDocument& inDoc) {
    if (!hidReady) {
        StaticJsonDocument<192> d;
        d["status"]="error"; d["message"]="USB HID not initialized"; sendJson(d); return;
    }
    if (!vaultOpen(inDoc)) return;
    int slot = inDoc["slot"] | -1;
    if (slot < 0 || slot >= VAULT_SLOTS) {
        StaticJsonDocument<160> d;
        d["status"]="error"; d["message"]="Invalid slot"; sendJson(d); return;
    }
    preferences.begin("vault", true);
    String label  = preferences.getString(vaultKey('l', slot).c_str(), "");
    String secret = preferences.getString(vaultKey('s', slot).c_str(), "");
    preferences.end();
    if (label.length() == 0 || secret.length() == 0) {
        StaticJsonDocument<160> d;
        d["status"]="error"; d["message"]="Empty slot"; sendJson(d); return;
    }
    typeString(secret.c_str());
    if (inDoc["enter"] | false) {
        delay(POST_TYPE_DELAY);
        Keyboard.press(KEY_RETURN);
        delay(50);
        Keyboard.releaseAll();
    }
    COM_SERIAL.printf("[VAULT] typed slot %d (%s)\n", slot, label.c_str());
    StaticJsonDocument<224> doc;
    doc["status"]="ok"; doc["reply"]="vault_typed"; doc["slot"]=slot; doc["label"]=label;
    sendJson(doc);
}

void handleVaultDelete(const JsonDocument& inDoc) {
    int slot = inDoc["slot"] | -1;
    if (slot < 0 || slot >= VAULT_SLOTS) {
        StaticJsonDocument<160> d;
        d["status"]="error"; d["message"]="Invalid slot"; sendJson(d); return;
    }
    preferences.begin("vault", false);
    preferences.remove(vaultKey('l', slot).c_str());
    preferences.remove(vaultKey('s', slot).c_str());
    preferences.remove(vaultKey('t', slot).c_str());
    preferences.end();
    COM_SERIAL.printf("[VAULT] deleted slot %d\n", slot);
    StaticJsonDocument<192> doc;
    doc["status"]="ok"; doc["reply"]="vault_deleted"; doc["slot"]=slot;
    sendJson(doc);
}

// Ungated on purpose: it only destroys. It is also the only way back when the
// vault PIN has been forgotten, which is the same bargain a browser profile
// makes - lose the password, lose what it protected.
void handleVaultWipe() {
    int had = vaultCount();
    vaultWipeAll();
    COM_SERIAL.printf("[VAULT] wiped (%d entries destroyed)\n", had);
    StaticJsonDocument<224> doc;
    doc["status"]="ok"; doc["reply"]="vault_wiped"; doc["destroyed"]=had;
    sendJson(doc);
}

// Sets, changes or resets the vault's own PIN.
//
// Two ways in, and the difference is the whole point. Knowing the current vault
// PIN changes it and keeps everything. Not knowing it, and falling back to the
// access PIN, is a *reset* - and a reset erases the vault. Otherwise anyone
// holding the access PIN could simply overwrite the vault PIN and read
// everything, which would make the separate PIN decorative.
void handleVaultPin(const JsonDocument& inDoc) {
    StaticJsonDocument<288> d;
    if (authToken.length() == 0) {
        d["status"]="error"; d["reply"]="vault_locked";
        d["message"]="Set an access PIN first.";
        sendJson(d); return;
    }
    if (vaultLocked()) {
        d["status"]="error"; d["reply"]="vault_locked";
        d["retry_in"]=(int)vaultLockRemaining();
        d["message"]="Too many wrong PINs";
        sendJson(d); return;
    }
    String next = String(inDoc["new_pin"] | "");
    bool digitsOnly = true;
    for (size_t i = 0; i < next.length(); i++) {
        if (!isdigit((unsigned char)next[i])) { digitsOnly = false; break; }
    }
    if (next.length() && (next.length() != 4 || !digitsOnly)) {
        d["status"]="error";
        d["message"]="The vault PIN is four digits (or empty to go back to the access PIN)";
        sendJson(d); return;
    }

    String byVault  = String(inDoc["pin"] | "");       // the current vault PIN
    String byAccess = String(inDoc["access"] | "");    // the access PIN, for a reset
    bool haveVaultPin = vaultPinCode.length() > 0;
    bool knowsVault   = haveVaultPin && byVault.length() && byVault == vaultPinCode;
    bool knowsAccess  = byAccess.length() && byAccess == authToken;

    if (!knowsVault && !knowsAccess) {
        // One counter for both, so guessing at either is throttled the same way.
        vaultPinCheck(byVault.length() ? byVault : byAccess);
        d["status"]="error"; d["reply"]="vault_denied";
        d["message"] = haveVaultPin ? "Wrong PIN" : "Wrong access PIN";
        if (vaultLocked()) d["retry_in"]=(int)vaultLockRemaining();
        sendJson(d); return;
    }
    vaultFails = 0;
    vaultLockUntil = 0;

    // Reset rather than change: the vault PIN is being replaced by someone who
    // could not produce it, so what it was guarding does not survive.
    bool wipe = haveVaultPin && !knowsVault;
    if (wipe) {
        int had = vaultCount();
        vaultWipeAll();
        COM_SERIAL.printf("[VAULT] PIN reset with the access PIN - %d entries destroyed\n", had);
    }

    vaultPinCode = next;
    preferences.begin("vault", false);
    if (next.length()) preferences.putString("pin", next);
    else               preferences.remove("pin");
    preferences.end();
    COM_SERIAL.printf("[VAULT] vault PIN %s\n", next.length() ? "set" : "cleared (access PIN now guards it)");

    StaticJsonDocument<224> doc;
    doc["status"]="ok"; doc["reply"]="vault_pin_set";
    doc["vault_pin_set"]=(vaultPinCode.length() > 0);
    doc["vault_wiped"]=wipe;
    sendJson(doc);
}

// ==================== SAVED PC PASSWORDS ====================
// A slot holds a name, a password and which OS that computer is. The password
// can be written and it can be used, but there is deliberately no command that
// reads one back - not over WiFi, not over serial.
//
// The OS is stored per slot because unlocking is not the same on both: a Mac
// login window is woken with a jiggle and a Shift, a PC with Esc, and Esc on a
// Mac collapses the password field. Detection describes the computer the board
// is plugged into *now*, which is not necessarily the one a saved password
// belongs to - a board carried to a second machine would otherwise type a Mac
// wake into a Windows login screen. OS_UNKNOWN means "follow the attached
// computer", which is what a slot saved before this existed does.
//
// NVS is not encrypted. Anyone holding this board can dump its flash and read
// these, and anyone in radio range could unlock the PC with a single request.
// The access PIN is what closes the second hole, so saving is refused until one
// is set. That is a real control, not a formality.
#define PC_SLOTS 8

static String pcKey(char kind, int slot) { return String(kind) + String(slot); }

// The OS saved against a slot, or OS_UNKNOWN for "whatever is attached".
HostOS pcOs(int slot) {
    if (slot < 0 || slot >= PC_SLOTS) return OS_UNKNOWN;
    preferences.begin("pcprof", true);
    uint8_t v = preferences.getUChar(pcKey('o', slot).c_str(), OS_UNKNOWN);
    preferences.end();
    return (v > OS_LINUX) ? OS_UNKNOWN : (HostOS)v;
}

void handlePcSave(const JsonDocument& inDoc) {
    if (authToken.length() == 0) {
        StaticJsonDocument<288> d;
        d["status"]="error";
        d["message"]="Set an access PIN first. Without one, anybody in radio range could unlock this PC with a saved password.";
        sendJson(d); return;
    }
    String name = String(inDoc["name"] | "");
    String pw   = String(inDoc["password"] | "");
    name.trim();
    int slot = inDoc["slot"] | -1;

    preferences.begin("pcprof", false);
    bool occupied = (slot >= 0 && slot < PC_SLOTS)
                    && preferences.getString(pcKey('n', slot).c_str(), "").length() > 0;
    // Editing an existing slot must not force the password to be typed again -
    // the dashboard cannot show it, so asking for it would mean losing it to a
    // rename. An empty password on an occupied slot keeps the stored one.
    if (name.length() == 0 || (pw.length() == 0 && !occupied)) {
        preferences.end();
        StaticJsonDocument<192> d;
        d["status"]="error"; d["message"]="name and password are both required";
        sendJson(d); return;
    }
    if (slot < 0 || slot >= PC_SLOTS) {
        for (int i = 0; i < PC_SLOTS; i++) {
            if (preferences.getString(pcKey('n', i).c_str(), "").length() == 0) { slot = i; break; }
        }
    }
    if (slot < 0 || slot >= PC_SLOTS) {
        preferences.end();
        StaticJsonDocument<192> d;
        d["status"]="error"; d["message"]="All slots are in use";
        sendJson(d); return;
    }
    // Unspecified means "the computer this was saved from", which is the answer
    // the user would have given anyway. "auto" is the explicit opt out.
    String osReq = String(inDoc["os"] | "");
    osReq.toLowerCase();
    HostOS os;
    if      (osReq == "mac")     os = OS_MAC;
    else if (osReq == "windows") os = OS_WINDOWS;
    else if (osReq == "linux")   os = OS_LINUX;
    else if (osReq == "auto")    os = OS_UNKNOWN;
    else if (occupied)           os = (HostOS)preferences.getUChar(pcKey('o', slot).c_str(), OS_UNKNOWN);
    else                         os = hostOs();

    preferences.putString(pcKey('n', slot).c_str(), name);
    if (pw.length()) preferences.putString(pcKey('p', slot).c_str(), pw);
    preferences.putUChar(pcKey('o', slot).c_str(), (uint8_t)os);
    preferences.end();
    COM_SERIAL.printf("[PC] saved profile %d (%s) os=%s, password %s\n",
                      slot, name.c_str(), hostOsName(os), pw.length() ? "updated" : "unchanged");
    StaticJsonDocument<256> doc;
    doc["status"]="ok"; doc["reply"]="pc_saved"; doc["slot"]=slot; doc["name"]=name;
    doc["os"] = hostOsName(os);
    sendJson(doc);
}

void handlePcList() {
    StaticJsonDocument<1024> doc;
    doc["status"]="ok"; doc["reply"]="pc_list";
    JsonArray a = doc.createNestedArray("profiles");
    preferences.begin("pcprof", true);
    for (int i = 0; i < PC_SLOTS; i++) {
        String n = preferences.getString(pcKey('n', i).c_str(), "");
        if (n.length() == 0) continue;
        JsonObject o = a.createNestedObject();
        o["slot"] = i;
        o["name"] = n;                 // names only - never the password
        uint8_t v = preferences.getUChar(pcKey('o', i).c_str(), OS_UNKNOWN);
        o["os"] = hostOsName(v > OS_LINUX ? OS_UNKNOWN : (HostOS)v);
    }
    preferences.end();
    doc["auth_set"] = authToken.length() > 0;
    doc["slots"] = PC_SLOTS;
    sendJson(doc);
}

void handlePcDelete(const JsonDocument& inDoc) {
    int slot = inDoc["slot"] | -1;
    if (slot < 0 || slot >= PC_SLOTS) {
        StaticJsonDocument<160> d;
        d["status"]="error"; d["message"]="Invalid slot";
        sendJson(d); return;
    }
    preferences.begin("pcprof", false);
    preferences.remove(pcKey('n', slot).c_str());
    preferences.remove(pcKey('p', slot).c_str());
    preferences.remove(pcKey('o', slot).c_str());
    preferences.end();
    COM_SERIAL.printf("[PC] deleted profile %d\n", slot);
    StaticJsonDocument<160> doc;
    doc["status"]="ok"; doc["reply"]="pc_deleted"; doc["slot"]=slot;
    sendJson(doc);
}

String pcPassword(int slot) {
    if (slot < 0 || slot >= PC_SLOTS) return String("");
    preferences.begin("pcprof", true);
    String pw = preferences.getString(pcKey('p', slot).c_str(), "");
    preferences.end();
    return pw;
}
