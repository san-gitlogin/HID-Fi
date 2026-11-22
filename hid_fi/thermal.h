// thermal.h - how hot the chip runs, kept so a person or a tool can see it: in a shell
// or out of one, idle or busy, this hour and the last four.
//
// The first printed HID-Fi Core ran hot to the touch after 4-5 hours doing nothing
// (2026-11-22), and nothing on the board could say how hot. The ESP32-S3 has a sensor
// on the die; this file turns its readings into what `status` and `temp_log` report.
// It reads the chip, not the air: a die runs warmer than the air around it, so the
// number is for comparing (open against closed, idle against streaming) and for a
// trend, not for the module's 65 °C ambient rating by itself (docs/HARDWARE.md).
//
// Pure logic, no Arduino: tests/unit/test_thermal.py compiles this file on the
// computer, and its planted bugs (tests/unit/firmware/thermal_mutations.json) must
// fail it.
#pragma once
#include <math.h>
#include <stdint.h>

namespace thermal {

constexpr uint32_t SAMPLE_MS = 2000;     // read the sensor this often
constexpr uint32_t LOG_PERIOD_S = 60;    // one logged point per minute (the mean of its readings)...
constexpr uint16_t LOG_LEN = 240;        // ...for the last four hours
constexpr int16_t NONE = INT16_MIN;      // no reading: the sensor failed for that whole minute
constexpr uint8_t STALE_AFTER = 3;       // failed reads in a row before "now" stops being shown
constexpr float MIN_C = -40.0f, MAX_C = 125.0f;   // what the die can be; anything else is a bad read

struct Log {
    int16_t ring[LOG_LEN];               // tenths of a degree; ring[head] is the next slot written
    uint16_t head = 0, count = 0;
    int32_t accSum = 0;                  // readings in the minute being filled
    uint16_t accN = 0;
    uint32_t periodStartS = 0;           // start of the minute being filled
    bool started = false;
    int16_t nowDeci = NONE, peakDeci = NONE;
    uint8_t failRun = 0;
    uint32_t errors = 0;
};

inline int16_t toDeci(float c) { return (int16_t)lroundf(c * 10.0f); }

inline void push(Log& l, int16_t v) {
    l.ring[l.head] = v;
    l.head = (uint16_t)((l.head + 1) % LOG_LEN);
    if (l.count < LOG_LEN) l.count++;
}

// Close every minute that has ended by nowS. A minute with readings logs their mean; a
// minute with none (the sensor failing, or the loop held up) logs NONE, so a gap stays a
// gap instead of the line jumping straight across it. The log is the last LOG_LEN
// minutes, so after a stall longer than that only gaps remain: the minute before the
// stall is older than anything the log may show.
inline void closePeriods(Log& l, uint32_t nowS) {
    if (nowS - l.periodStartS < LOG_PERIOD_S) return;
    const uint32_t n = (nowS - l.periodStartS) / LOG_PERIOD_S;    // minutes that have ended
    const int16_t first = l.accN ? (int16_t)lroundf((float)l.accSum / l.accN) : NONE;
    const uint32_t keep = n < LOG_LEN ? n : LOG_LEN;
    for (uint32_t i = n - keep; i < n; i++) push(l, i == 0 ? first : NONE);
    l.accSum = 0;
    l.accN = 0;
    l.periodStartS += n * LOG_PERIOD_S;
}

inline bool plausible(float c) { return !isnan(c) && c >= MIN_C && c <= MAX_C; }

// One reading from the sensor. ok is the driver's own answer; a finite value inside
// the die's range is still required, so a garbage register never becomes a peak.
inline void reading(Log& l, float celsius, bool ok, uint32_t nowS) {
    if (!l.started) { l.started = true; l.periodStartS = nowS; }
    closePeriods(l, nowS);
    if (!ok || !plausible(celsius)) {
        l.errors++;
        if (l.failRun < 255) l.failRun++;
        if (l.failRun >= STALE_AFTER) l.nowDeci = NONE;
        return;
    }
    l.failRun = 0;
    const int16_t d = toDeci(celsius);
    l.nowDeci = d;
    if (l.peakDeci == NONE || d > l.peakDeci) l.peakDeci = d;
    l.accSum += d;
    l.accN++;
}

// Which of the driver's two bands to read in next. The ESP32-S3 sensor measures in one
// band at a time (ESP-IDF temperature_sensor_periph.c: -10..80 °C within 1 °C,
// 50..125 °C within 3 °C); outside its band it saturates rather than fails, so a hot
// die would sit at the band's edge and read cooler than it is. Move up near the top
// of the low band and back down well inside the high one; the gap stops flapping.
enum Band : uint8_t { BAND_LOW = 0, BAND_HIGH = 1 };
constexpr int16_t BAND_UP_DECI = 750, BAND_DOWN_DECI = 550;
inline Band nextBand(Band cur, int16_t deci) {
    if (deci == NONE) return cur;
    if (cur == BAND_LOW && deci >= BAND_UP_DECI) return BAND_HIGH;
    if (cur == BAND_HIGH && deci <= BAND_DOWN_DECI) return BAND_LOW;
    return cur;
}

// The guard: watching the temperature must never cost the board latency (the owner,
// 2026-11-22: "at any cost should not degrade the performance of the board and the
// latency"). The reads run in their own low-priority task (hid_fi.ino), so the WiFi
// tasks may pause one midway: that wall time costs nobody anything, and it comes and
// goes. A driver that is really slow is slow every time. So a read over TICK_BUDGET_US
// is a strike, a read inside it clears them, and STRIKES in a row switch the sensor off
// until the next start (status: temp_off "slow"). The worst read is reported
// (temp_us_max).
constexpr uint32_t TICK_BUDGET_US = 1500;
constexpr uint8_t STRIKES = 3;
struct Guard {
    uint32_t worstUs = 0;
    uint8_t strikes = 0;
    bool off = false;
};
// Returns true while the sensor may still be read.
inline bool guardTick(Guard& g, uint32_t us) {
    if (us > g.worstUs) g.worstUs = us;
    if (us <= TICK_BUDGET_US) g.strikes = 0;
    else if (g.strikes < 255) g.strikes++;
    if (g.strikes >= STRIKES) g.off = true;
    return !g.off;
}

// The log oldest first into out (LOG_LEN slots); returns how many.
inline uint16_t unroll(const Log& l, int16_t* out) {
    const uint16_t start = (uint16_t)((l.head + LOG_LEN - l.count) % LOG_LEN);
    for (uint16_t i = 0; i < l.count; i++) out[i] = l.ring[(start + i) % LOG_LEN];
    return l.count;
}

// Seconds since boot at which the newest logged point ended (its minute closed).
inline uint32_t lastLoggedAt(const Log& l) { return l.periodStartS; }

}  // namespace thermal
