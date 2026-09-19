/*
  ============================================================================
  HID-Fi v3.5 — USB HID trackpad + remote + WiFi dashboard

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
    Misc:     ping, status, echo, test_hid, set_verbose

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

// Returns true if the bus was suspended and a resume was requested. Costs a bool
// read when the host is awake, so it is safe on the pointer hot path.
static bool usbWakeHost() {
    if (!tud_suspended()) return false;
    bool allowed = tud_remote_wakeup();   // false if the host never armed us as a wake source
    delay(120);                           // let the bus resume before the first report
    return allowed;
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
uint8_t  authFails     = 0;              // consecutive wrong PINs, reset on success
uint32_t authLockUntil = 0;              // millis() deadline; 0 = not locked out
bool    wsAuthed[WS_MAX_CLIENTS];
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
String apPassword = "hid12345"; // AP password (min 8 chars for WPA2)
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
    STA_FAILED       // gave up - staError says why
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
#define  FW_VERSION "hid_fi_v3.5"

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
// HID is one way, but there is a single thing the host tells the keyboard back:
// which of its lock-key LEDs to light. That is enough to tell a Mac from a PC.
// Windows and Linux own a Num Lock and echo its LED the instant it is toggled;
// macOS has no Num Lock and stays silent. So the board taps Num Lock once,
// watches for the echo, and taps it straight back to undo the toggle:
//   echo seen -> Windows or Linux   (the LED alone cannot tell those two apart)
//   silence   -> macOS
// The result only steers defaults - the lock shortcut, the gesture combos, the
// app-switch modifier and the keyboard layout. A manual override always wins,
// because a user on a Linux box the probe called "windows" must be able to say so.
HostOS hostOsDetected = OS_UNKNOWN;   // what the Num Lock probe concluded
HostOS hostOsManual   = OS_UNKNOWN;   // user override; OS_UNKNOWN = trust the probe

const char* hostOsName(HostOS o) {
    switch (o) {
        case OS_WINDOWS: return "windows";
        case OS_MAC:     return "mac";
        case OS_LINUX:   return "linux";
        default:         return "unknown";
    }
}
// The OS the rest of the firmware actually acts on, and where it came from.
HostOS      hostOs()       { return hostOsManual != OS_UNKNOWN ? hostOsManual : hostOsDetected; }
const char* hostOsSource() {
    if (hostOsManual   != OS_UNKNOWN) return "manual";
    if (hostOsDetected != OS_UNKNOWN) return "auto";
    return "pending";
}
// True when the effective OS is a Mac - the one branch nearly every mapping needs.
inline bool hostIsMac() { return hostOs() == OS_MAC; }

// Written only from the USB event task, read only from loop(); a plain volatile
// is enough for that one-writer/one-reader hand-off.
volatile uint32_t ledEventCount = 0;
volatile uint8_t  ledStateBits  = 0;
void keyboardLedCb(void* /*arg*/, esp_event_base_t /*base*/, int32_t /*id*/, void* data) {
    arduino_usb_hid_keyboard_event_data_t* d = (arduino_usb_hid_keyboard_event_data_t*)data;
    ledStateBits = d->leds;
    ledEventCount++;
}

// The probe is a state machine ticked from loop() so nothing blocks. It runs
// once after the host has mounted and settled; set_host_os with "auto" can rearm it.
enum DetectPhase : uint8_t { DET_WAIT_MOUNT, DET_SETTLE, DET_PROBED, DET_DONE };
DetectPhase detectPhase     = DET_WAIT_MOUNT;
uint32_t    detectAt        = 0;    // millis() at the last phase change
uint32_t    ledCountAtProbe = 0;    // LED events seen before the Num Lock tap
#define DETECT_SETTLE_MS 1500       // after mount, let the host finish its own LED init
#define DETECT_REPLY_MS   800       // how long to wait for the Num Lock echo

void announceHostOs() {
    StaticJsonDocument<160> doc;
    doc["event"]          = "host_os";
    doc["host_os"]        = hostOsName(hostOs());
    doc["host_os_source"] = hostOsSource();
    String out;
    serializeJson(doc, out);
    // Push it so an open dashboard reskins immediately rather than on its next poll.
    if (wifiEnabled) webSocket.broadcastTXT(out);
    COM_SERIAL.println(out);
}

void updateHostDetect() {
    if (detectPhase == DET_DONE) return;
    // A manual choice makes the probe pointless - record it as settled and stop.
    if (hostOsManual != OS_UNKNOWN) { detectPhase = DET_DONE; return; }
    if (!hidReady) return;
    uint32_t now = millis();
    switch (detectPhase) {
        case DET_WAIT_MOUNT:
            if (tud_mounted() && !tud_suspended()) { detectPhase = DET_SETTLE; detectAt = now; }
            break;
        case DET_SETTLE:
            if (now - detectAt >= DETECT_SETTLE_MS) {
                if (tud_suspended()) return;        // never type into a sleeping host
                ledCountAtProbe = ledEventCount;
                Keyboard.press(KEY_NUM_LOCK); delay(5); Keyboard.release(KEY_NUM_LOCK);
                detectPhase = DET_PROBED; detectAt = now;
            }
            break;
        case DET_PROBED:
            if (ledEventCount > ledCountAtProbe) {
                // The host toggled a Num Lock LED for us: it has one, so it is a PC.
                hostOsDetected = OS_WINDOWS;
                Keyboard.press(KEY_NUM_LOCK); delay(5); Keyboard.release(KEY_NUM_LOCK); // undo the toggle
                detectPhase = DET_DONE;
                announceHostOs();
            } else if (now - detectAt >= DETECT_REPLY_MS) {
                // No echo: no Num Lock, so a Mac.
                hostOsDetected = OS_MAC;
                detectPhase = DET_DONE;
                announceHostOs();
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
void typeString(const char* text);
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
void handleSetVerbose(const JsonDocument& inDoc);
void handleMacroSave(const JsonDocument& inDoc);
void handleMacroGet(const JsonDocument& inDoc);
void handleMacroList();
void handleMacroRun(const JsonDocument& inDoc);
void handleMacroDelete(const JsonDocument& inDoc);
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
    COM_SERIAL.println("HID-Fi v3.5 — ESP32-S3 USB HID + WiFi");
    COM_SERIAL.println("========================================");

    for (int i = 0; i < WS_MAX_CLIENTS; i++) wsAuthed[i] = false;

    preferences.begin("hid_cfg", true);
    absoluteMode   = preferences.getBool("abs", false);
    gamepadEnabled = preferences.getBool("gpad", false);
    authToken      = preferences.getString("auth", "");
    verboseLog     = preferences.getBool("verbose", false);
    powerMode      = (PowerMode)preferences.getUChar("pwr", PWR_BALANCED);
    apAutoOff      = preferences.getBool("apoff", false);
    hostOsManual   = (HostOS)preferences.getUChar("os", OS_UNKNOWN);
    preferences.end();
    if (powerMode > PWR_SAVER) powerMode = PWR_BALANCED;   // guard against a bad stored value
    if (hostOsManual > OS_LINUX) hostOsManual = OS_UNKNOWN;

    // Exactly one mouse object may exist -- see the note on the globals.
    COM_SERIAL.printf("[HID] Initializing USB HID (pointer=%s, gamepad=%s)...\n",
                      absoluteMode ? "absolute" : "relative", gamepadEnabled ? "on" : "off");
    Keyboard.begin();
    // The host reports its lock-key LEDs here; that echo is how the board tells a
    // Mac from a PC. Registered before USB.begin() so no early report is missed.
    Keyboard.onEvent(ARDUINO_USB_HID_KEYBOARD_LED_EVENT, keyboardLedCb);
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
    
    // Always start AP first — this is the primary access point
    startAP();
    
    // Then try STA if credentials are saved. This starts the join and returns -
    // boot used to sit in a busy-wait here, which left the access point and the
    // web server up but unable to answer anything until the join timed out.
    if (savedSSID.length() > 0) {
        COM_SERIAL.printf("[WIFI] Saved network: %s - joining in the background\n", savedSSID.c_str());
        staBeginJoin(savedSSID, savedPass, true);
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
        staPhase = STA_ONLINE;
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
    staPhase = STA_FAILED;
    if (staError.length() == 0) staError = wlStatusStr(WiFi.status());
    COM_SERIAL.printf("[STA] Join failed: %s\n", staError.c_str());
    WiFi.disconnect(false);   // keep the AP running no matter what
}

void checkWifiConnection() {
    // A join in flight owns the radio - let updateStaJoin() finish before the
    // watchdog starts second-guessing it, or the two fight each other.
    if (staPhase == STA_CONNECTING) return;
    if (millis() - lastWifiCheck < WIFI_CHECK_INTERVAL) return;
    lastWifiCheck = millis();
    
    // Only check STA connection — AP is always running
    if (WiFi.status() != WL_CONNECTED) {
        if (wifiConnected) {
            COM_SERIAL.println("[STA] Connection lost — AP still active, auto-reconnecting...");
            wifiConnected = false;
            wifiIP = "";
        }
        // Try reconnecting if we have saved creds
        preferences.begin("wifi_cfg", true);
        String ssid = preferences.getString("ssid", "");
        preferences.end();
        if (ssid.length() > 0 && WiFi.status() == WL_DISCONNECTED) {
            WiFi.reconnect();
        }
    } else if (!wifiConnected) {
        wifiConnected = true;
        wifiIP = WiFi.localIP().toString();
        COM_SERIAL.printf("[STA] Reconnected! IP: %s\n", wifiIP.c_str());
    }
}

// ==================== WEB ROUTES ====================
// /api/scan is a plain GET, so it never reached the gate in processCommand and
// any device on the home network could list the networks around you without a
// PIN. It answers to the same rules as everything else now: on the board's own
// access point radio range is the limit, on a LAN a PIN is required, and the
// dashboard sends it in a header rather than the URL so it stays out of logs.
bool scanAllowed() {
    if (clientIsAp(webServer.client().remoteIP())) return true;
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
        StaticJsonDocument<1024> doc;
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
    bool mac = hostIsMac();
    // macOS is fussier than Windows here: pressing Esc at the login window collapses
    // the password field, and the field can take longer to appear and take focus,
    // so a Mac gets a gentler wake and a longer wait by default.
    int defWake = useCtrlAltDel ? CAD_DELAY : (mac ? 2500 : WAKE_DELAY);
    int wakeDelay = inDoc["wake_delay"] | defWake;
    int postTypeDelay = inDoc["post_type_delay"] | POST_TYPE_DELAY;

    COM_SERIAL.printf("[UNLOCK] Starting unlock sequence (os=%s prev_state=%s)...\n",
                      hostOsName(hostOs()), lockStateStr());

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
    typeString(text);
    
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
            Keyboard.press(c);
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
    return 0;
}

// ==================== TYPE STRING ====================
void typeString(const char* text) {
    usbWakeHost();
    for (int i = 0; text[i] != '\0'; i++) {
        Keyboard.press(text[i]);
        delay(CHAR_DELAY);
        Keyboard.release(text[i]);
        delay(CHAR_DELAY);
    }
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
// the override and re-arms the Num Lock probe. The manual choice is what makes
// the feature trustworthy: the probe cannot tell Linux from Windows, and a user
// who knows better must always be able to correct it.
void handleSetHostOs(const JsonDocument& inDoc) {
    String os = String(inDoc["os"] | "");
    os.toLowerCase();

    if (os == "auto") {
        hostOsManual = OS_UNKNOWN;
        hostOsDetected = OS_UNKNOWN;
        detectPhase = DET_WAIT_MOUNT;      // rerun the probe
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
    doc["pointer_mode"] = absoluteMode ? "absolute" : "relative";
    doc["gamepad"] = gamepadEnabled;
    doc["auth_set"] = (authToken.length() > 0);
    doc["verbose"] = verboseLog;
    doc["wiggle_active"] = (wiggleMode != WIGGLE_NONE);
    doc["wiggle_mode"] = wiggleModeStr();
    doc["led_on"] = ledOn;
}

void handleDeviceStatus(bool fromWifi) {
    StaticJsonDocument<1024> doc;
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

    preferences.begin("wifi_cfg", false);
    preferences.putString("ssid", ssid);
    preferences.putString("pass", pass);
    preferences.putString("ip",   staWantIp);
    preferences.putString("gw",   staWantGw);
    preferences.putString("mask", staWantMask);
    preferences.putString("dns",  staWantDns);
    preferences.end();
    COM_SERIAL.printf("[STA] Credentials saved for: %s\n", ssid);

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
    WiFi.config(INADDR_NONE, INADDR_NONE, INADDR_NONE);   // back to DHCP for next time

    preferences.begin("wifi_cfg", false);
    preferences.remove("ssid");
    preferences.remove("pass");
    preferences.remove("ip");
    preferences.remove("gw");
    preferences.remove("mask");
    preferences.remove("dns");
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

    doc["note"] = "Macros are listed by macro_list and saved PCs by pc_list. No password or PIN is ever returned by any command.";
    sendJson(doc);
}

// ==================== CONNECTED CLIENTS ====================
void handleClients() {
    DynamicJsonDocument doc(2048);
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
            if (num < WS_MAX_CLIENTS) wsAuthed[num] = (authToken.length() == 0);
            noteActivity();
            COM_SERIAL.printf("[WS] client %u connected\n", num);
            if (authToken.length()) {
                webSocket.sendTXT(num, "{\"status\":\"error\",\"reply\":\"auth_required\"}");
            }
            break;

        case WStype_DISCONNECTED:
            if (num < WS_MAX_CLIENTS) wsAuthed[num] = false;
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
bool pinLocked() {
    return authLockUntil != 0 && (int32_t)(millis() - authLockUntil) < 0;
}

uint32_t pinLockRemaining() {
    return pinLocked() ? ((authLockUntil - millis()) / 1000) + 1 : 0;
}

bool pinCheck(const char* candidate) {
    if (authToken == candidate) {
        authFails = 0;
        authLockUntil = 0;
        return true;
    }
    if (authFails < 250) authFails++;
    // one free retry, then 5s, 15s, 60s, 5min for every attempt after that
    static const uint32_t backoff[] = { 0, 0, 5000, 15000, 60000, 300000 };
    uint32_t wait = backoff[authFails < 6 ? authFails : 5];
    if (wait) authLockUntil = millis() + wait;
    COM_SERIAL.printf("[AUTH] wrong PIN (%u consecutive, lock %lus)\n",
                      authFails, (unsigned long)(wait / 1000));
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
    authToken = tk;
    preferences.begin("hid_cfg", false);
    preferences.putString("auth", authToken);
    preferences.end();

    // Existing sockets must re-authenticate against the new PIN.
    for (int i = 0; i < WS_MAX_CLIENTS; i++) wsAuthed[i] = (authToken.length() == 0);
    if (authToken.length() && cmdSource == SRC_WS && wsClientNum >= 0 && wsClientNum < WS_MAX_CLIENTS) {
        wsAuthed[wsClientNum] = true;
    }

    StaticJsonDocument<192> doc;
    doc["status"]="ok"; doc["reply"]="auth_set"; doc["auth_set"]=(authToken.length() > 0);
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

    StaticJsonDocument<1536> doc;
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

// ==================== SAVED PC PASSWORDS ====================
// A slot holds a name and a password. The password can be written and it can be
// used, but there is deliberately no command that reads one back - not over
// WiFi, not over serial.
//
// NVS is not encrypted. Anyone holding this board can dump its flash and read
// these, and anyone in radio range could unlock the PC with a single request.
// The access PIN is what closes the second hole, so saving is refused until one
// is set. That is a real control, not a formality.
#define PC_SLOTS 8

static String pcKey(char kind, int slot) { return String(kind) + String(slot); }

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
    if (name.length() == 0 || pw.length() == 0) {
        StaticJsonDocument<192> d;
        d["status"]="error"; d["message"]="name and password are both required";
        sendJson(d); return;
    }
    int slot = inDoc["slot"] | -1;
    preferences.begin("pcprof", false);
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
    preferences.putString(pcKey('n', slot).c_str(), name);
    preferences.putString(pcKey('p', slot).c_str(), pw);
    preferences.end();
    COM_SERIAL.printf("[PC] saved profile %d (%s), %u char password\n",
                      slot, name.c_str(), (unsigned)pw.length());
    StaticJsonDocument<224> doc;
    doc["status"]="ok"; doc["reply"]="pc_saved"; doc["slot"]=slot; doc["name"]=name;
    sendJson(doc);
}

void handlePcList() {
    StaticJsonDocument<640> doc;
    doc["status"]="ok"; doc["reply"]="pc_list";
    JsonArray a = doc.createNestedArray("profiles");
    preferences.begin("pcprof", true);
    for (int i = 0; i < PC_SLOTS; i++) {
        String n = preferences.getString(pcKey('n', i).c_str(), "");
        if (n.length() == 0) continue;
        JsonObject o = a.createNestedObject();
        o["slot"] = i;
        o["name"] = n;                 // names only - never the password
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
