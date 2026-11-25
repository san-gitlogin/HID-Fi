# Decisions

Why HID-Fi behaves the way it does, so nobody has to remember. Each entry says
what was decided, the reason (often the owner's own words), and the tests that
hold it in place.

Before changing a behaviour, read its entry. If the change goes against it, don't
quietly break the test: add a new entry that **revises** the old one, says what
changed and why, and point both at the tests. `tests/static/test_decisions.py`
fails if an entry names a test that no longer exists.

Guard formats:
- `path::test_function` for Python and Rust tests;
- `path: Scenario title` for BDD scenarios.

---

## D-01 · 2026-11-05 · Which saved network the board joins: the strongest

**Revised by D-02.** Still the rule on a board that nobody has used over a
network yet, and between the access points of one network.

The board joins the strongest saved network in range. A network seen on several
access points counts its strongest. A tie goes to the one saved most recently.
Names must match exactly. Once connected, the board stays put until the network
drops, like a phone.

**Why:** the owner's hometown has several networks (ARVISANTU C6, ARVISANTU EXT,
ARVISANTU). "I think it automatically chooses between which one has high signal.
Just confirm me and make sure we have automated tests... with some dummy network
with dummy strength."

**Guarded by:**
- `tests/unit/test_net_logic.py::test_three_networks_in_one_home`
- `tests/unit/test_net_logic.py::test_a_network_on_two_access_points_counts_its_strongest`
- `tests/unit/test_net_logic.py::test_a_refused_or_resting_network_is_skipped_even_when_strongest`
- `tests/unit/test_net_logic.py::test_names_match_exactly`

## D-02 · 2026-11-06 · The network your devices use wins, not the strongest

**Revises D-01.**

At home, one board joined ARVISANTU C6 (stronger where it sat) while the Mac and
the iPad were on ARVISANTU EXT, a different router and subnet. Nobody could reach
it. "This is something that would seriously piss off users... user have to
constantly switch between networks in order to just be able to connect to the
board." Strength only matters between the access points of one network; a board
nobody can reach is useless however strong its signal.

Rules (`hid_fi/net_logic.h`):

1. **The board learns where it is used.** An owner device reaching it over a
   saved network marks that network as used:
   - a signed-in dashboard over the home network;
   - the paired Companion.

   No prompts.
2. **The paired computer anchors it.** Screen sharing only works on the same
   network, and a phone can always use the board's own WiFi. So the network the
   paired computer last reached it on ranks first. Without one, the network
   used most recently ranks first.
3. **That network wins while it is workable** (−85 dBm or better), even against
   a stronger network nobody uses. *Revised 2026-11-07:* the first cut-off was
   −75 dBm. On the real board, the owner's extender measured −80 dBm and worked
   normally, but counted as too weak, so the board fell back to the stronger
   router nobody could reach. The cut-off is now where a link actually stops
   being usable, not where it is merely weaker. Weaker than that, or gone, the board falls
   back to D-01 and stays reachable.
4. **One network, several access points.** When the board joins a network it
   records its subnet. Saved networks on the same subnet (a router and its
   extender) count as one network: using one counts for all, and the strongest
   of them wins (roaming).
5. **Finding its people.** On a network nobody has used, if no owner device has
   reached the board within 5 minutes and another saved network is in range that
   it has not tried since starting, it moves there to look. It tries each one at
   most once per start, then settles on D-01.
6. **Coming home.** On a network that isn't the preferred one, the board checks
   every 2 minutes. When the preferred one is workable and no owner device is
   connected right now, it moves back. It scans only when there is something to
   decide, so a board that has tried every network and settled stops scanning.
7. **No flip-flopping.** It never moves away from a network an owner device is
   using, and never on one visit from another network: it chooses only at the
   moments above.
8. **The owner can choose.** The Settings list of saved networks shows which one
   is preferred, and one tap makes another preferred (`wifi_prefer`).

**Guarded by:**
- `tests/unit/test_net_logic.py::test_the_network_you_use_wins_over_a_stronger_one`
- `tests/unit/test_net_logic.py::test_a_preferred_network_too_weak_falls_back_to_the_strongest`
- `tests/unit/test_net_logic.py::test_one_network_on_two_names_roams_to_the_strongest`
- `tests/unit/test_net_logic.py::test_the_paired_computer_anchors_over_a_phone`
- `tests/unit/test_net_logic.py::test_the_most_recently_used_wins_without_a_computer`
- `tests/unit/test_net_logic.py::test_a_board_nobody_uses_looks_for_its_people`
- `tests/unit/test_net_logic.py::test_it_comes_home_but_never_away_from_someone_using_it`
- `tests/unit/test_net_logic.py::test_random_homes_against_the_written_rules`
- `tests/unit/test_net_logic.py::test_it_scans_only_when_there_is_something_to_decide`
- `tests/unit/test_net_logic.py::test_the_board_model_chooses_as_the_firmware_does`
- `tests/ui/bdd/features/home_network.feature: Signing in over the home network marks it as where the board is used`
- `tests/ui/bdd/features/home_network.feature: The network you use is marked, and another can be preferred`
- `tests/ui/bdd/features/home_network.feature: The paired computer's network says so`

## D-03 · 2026-11-05 · Recovery is over USB, and the one button changes nothing by itself

Recovery of a forgotten PIN or WiFi password happens only over USB, because
holding the board is what proves you own it. The one button use is recovery
mode: hold BOOT for 8 seconds to open a USB session. The button alone changes
nothing.

**Why:** a board with one USB port (the HID-Fi Dongle) has no COM cable. And no
physical rituals: "How in the world would a disabled man replug 3 times within 10
seconds... Leave it to only pinhole hold."

**Guarded by:**
- `tests/unit/test_recover.py::test_recovery_mode_shows_the_wifi_password_without_a_com_cable`
- `tests/unit/test_recover.py::test_recovery_mode_is_closed_when_the_tool_is_done`
- `tests/ui/bdd/features/ownership.feature: Someone on the home network cannot claim the board`

## D-04 · 2026-11-05 · Factory reset: four proofs, never fewer, and no pin in a pinhole

A factory reset needs one of four proofs:
- `confirm` over USB;
- the access PIN;
- a paired computer erasing down its own USB cable;
- the owner approving a request on the dashboard.

The interface warns what goes and what stays, counts down, and can be cancelled.

**Why:** "must be something that doesn't involve physical interaction... doable
via HID-Fi companion only. Faster, reliable, better and cheaper... friendly UI...
warnings that factory reset will delete all memory." It must also work from a
different computer, and for people who can't handle the board.

**Guarded by:**
- `tests/ui/bdd/features/factory_reset.feature: The warning says what goes and what stays, and nothing starts until I tick the box`
- `tests/ui/bdd/features/factory_reset.feature: A computer that is not paired asks, and the owner approves`
- `tests/ui/bdd/features/factory_reset.feature: The owner declines, and nothing is erased`
- `tests/unit/test_recover.py::test_factory_reset_unattended_without_yes_changes_nothing`

## D-05 · 2026-11-05 · A new place: clear guidance, no prompt on the dashboard

When the board is somewhere new, the Companion and the docs say what to do. The
dashboard does not prompt.

**Why:** "A is just enough. B - just the first time... intrusive."

**Guarded by:**
- `companion/crates/hidfi-companion/src/menu.rs::no_pin_and_off_network_are_named`

## D-06 · 2026-11-06 · Old phones and tablets: one page, a layer only where it is needed

One dashboard for every device. An older browser gets a layer that fills in what
it lacks; a current one checks five features and skips it. The layout must match
the modern page to the pixel. Floor: Safari 12, Chrome 79, Firefox 75.

**Why:** "The idea of HID-Fi is mainly to support older devices... connect to it
from any device that has touchscreen and you can start using it... very very
careful that it should not affect any regression, and mainly not degrade
performance." Every mix of missing features is tested: "5 power 5 combination
that you have to test out thoroughly."

**Guarded by:**
- `tests/ui/legacy/test_combinations.py::test_every_combination`
- `tests/ui/legacy/test_legacy.py::test_a_current_browser_never_runs_the_layer`
- `tests/ui/legacy/test_legacy.py::test_touches_alone_drive_the_pad_keys_and_buttons`
- `tests/ui/legacy/test_legacy.py::test_the_layer_settles_on_an_idle_page`

## D-07 · 2026-11-06 · Input leaves on the input path: no frame waits, no idle loops

Pointer, key and gamepad messages are sent from the touch's own event, at most
every 8 ms (the USB polling interval). Nothing runs while nobody is touching. A
key press allocates no memory.

**Why:** "Keyboard feels laggy, the pointer feels laggy... we have ultrafast
responsiveness and very less latency in the dashboard with even mid range
devices." Measured with a slow redraw: pointer movement went from 20.8 ms to
0.1 ms.

**Guarded by:**
- `tests/ui/legacy/test_latency_budget.py::test_touch_to_message_within_budget_on_a_slow_cpu`
- `tests/ui/legacy/test_input_path.py::test_nothing_runs_while_nobody_touches`
- `tests/ui/legacy/test_input_path.py::test_a_gamepad_tap_faster_than_a_frame_reaches_the_computer`

## D-08 · 2026-11-06 · A saved network whose password changed: never a reason to move

**Refines D-02.**

A network that has worked but whose router now refuses the saved password (it was
probably changed at the router):

- **Kept, not forgotten.** Routers sometimes misreport. After 3 refusals it rests
  for 5 minutes while the other saved networks are tried, and the board's own
  WiFi stays up throughout.
- **Never a reason to move.** The board doesn't leave a working network to "come
  home" to it, and doesn't scan for it. If it is the preferred network but a
  router and its extender share it, the member that still takes the password is
  home.
- **Off a network, it comes after the others,** even when it is the strongest.
  It is still tried when nothing else is in range, in case the router misreported
  or the password was changed back.
- **Settings says so on its row:** "the router no longer takes the saved password -
  changed? Scan, pick it, enter the new one". Entering it, or a join that works,
  clears it.

**Why:** "have we handled password change scenario. Where known networks got
their password changed". We hadn't, and D-02 had made it worse. A preferred
network with a changed password pulled the board off a working network every 5
minutes, for about a minute each time, until someone re-entered the password.

**Guarded by:**
- `tests/unit/test_net_logic.py::test_a_changed_password_never_pulls_it_off_a_working_network`
- `tests/unit/test_net_logic.py::test_random_homes_against_the_written_rules`
- `tests/ui/bdd/features/home_network.feature: A saved network whose password changed says so, and the board stays where it works`
- `tests/ui/bdd/features/settings.feature: Entering the new password of a network whose password changed clears it`

## D-09 · 2026-11-07 · An updated board stays on the network it was on

**Refines D-02.**

On the first start after an update from a version before D-02, no saved network
has been marked as used yet. The network the board was last on (the older
firmware always kept it first in the list) becomes the one it is used over. It
happens whenever no network is marked used, which also covers a board taken down
to an older version (which saves the list without the marks) and back up.
Seeding creates the history, so it can't repeat on its own. *Revised
2026-11-07:* v4.3 kept a "seeded once" flag. 09F7C8, flashed down to v4.2.2 and
back to v4.3, then had no history and no seed, and joined C6.

**Why:** v4.3 reaches most boards over the air. Without this, in a home with
several routers, the restart after the update joined the strongest router. If
nobody used that one, the board was out of reach until it went looking 5
minutes later. DA1A2C, the owner's own board, did exactly that when updated on
2026-11-07: it joined ARVISANTU C6, away from the Mac.

**Guarded by:**
- `tests/unit/test_net_logic.py::test_an_updated_board_stays_on_the_network_it_was_on`

## D-10 · 2026-11-07 · Reach the board by name: count only devices right on its network

*Revised by D-14 (2026-11-23):* "right on its network" also needs the computer's address
inside the board's own subnet - traffic from the extender's subnet arrived with the
Mac's own address and made a Mac there look right on the board's network.

**Refines D-02.**

A board can be on one home network at a time, and its `.local` name only works on
that network and on the board's own WiFi. People use the name and don't remember
IP addresses. The board therefore aims to be on the network the owner's devices
are **directly** on, and tells a device on the wrong side what to do.

- **Only devices directly on the board's network count as using it.** They count
  towards choosing its network, and towards "someone is using it now", which
  blocks any move:
  - a page opened by the board's `.local` name (the name never crosses a router);
  - a page opened by address that then reaches the name (`net_here`);
  - a paired Companion whose own address (`via`) is the one the request came from.

  A device routed in from another network (one router behind another) can still
  open the board by IP. It never counts and never pins the board.
- **A device on the wrong side is told what to do, without addresses.** A page
  opened by IP that can't reach the name shows a small, dismissible banner.
  - **Bring the board here:** you pick which WiFi this device is on, and the board
    moves there. If someone else is directly using the board, it asks first.
  - **Use the board's own WiFi:** the same `.local` address works there.

  This is not the "new place" prompt D-05 rejected. It shows only when this
  device demonstrably can't reach the board by name.
- **Signal strength decides only within one network.**
  - When joining, the board takes the strongest access point of the chosen name
    (all channels scanned, sorted by signal); the core's default was the first
    one found.
  - While connected, it roams to an access point of the same network (same name
    or same subnet) when the current one is weaker than −70 dBm and another is at
    least 10 dB stronger, never while an owner device is using it.
  - It never hops between networks on strength.

**Why:** in the owner's hometown, C6 sits behind ARVISANTU. The owner's Mac and
phones moved to C6, while the board had learned ARVISANTU from overnight tests,
including visits routed through C6's router, which counted as "used here". The
phones could not open `esp-hid-09f7c8.local`, and the dashboard was slow over two
hops. "As an end user, user would not be willing to visit webpages via IP and
that's why we had the .local URL... What if multiple devices try to connect to
the board from different network but within range."

**Guarded by:**
- `tests/unit/test_net_logic.py::test_it_roams_within_one_network_and_never_between_networks`
- `tests/unit/test_net_logic.py::test_random_homes_roam_by_the_written_rule`
- `tests/ui/bdd/features/home_network.feature: Opened by address from another network, the page says so and the visit does not count`
- `tests/ui/bdd/features/home_network.feature: Opened by address right on the board's network, the page checks the name and counts`
- `tests/ui/bdd/features/home_network.feature: Bring the board to this device's WiFi - pick it, and the board moves`
- `tests/ui/bdd/features/home_network.feature: Someone using the board on its network is asked about before it moves`
- `tests/ui/bdd/features/home_network.feature: The board's own WiFi is offered, with the same address`
- `tests/ui/bdd/features/home_network.feature: A paired computer behind another router does not anchor the board`
- `tests/board/test_networks_rehearsed.py::test_joined_where_nobody_is_it_comes_home`
- `tests/board/test_networks_rehearsed.py::test_a_stronger_network_nearby_never_pulls_it_away`
- `tests/board/test_networks_rehearsed.py::test_restarted_it_goes_straight_to_the_preferred_network`
- `tests/board/test_networks_rehearsed.py::test_the_owner_moves_and_their_hotspot_goes_it_finds_them_again`
- `tests/board/test_networks_rehearsed.py::test_brought_to_a_network_out_of_range_it_comes_back`
- `tests/board/test_networks_rehearsed.py::test_a_weak_access_point_roams_only_when_nobody_uses_it`
- `tests/board/test_networks_rehearsed.py::test_an_extender_with_another_name_is_the_same_network`
- `tests/board/test_networks_rehearsed.py::test_asked_to_move_during_a_join_it_goes_where_asked`
- `tests/board/test_reach_by_name_live.py::test_from_another_network_the_banner_shows_and_the_visit_does_not_count`
- `tests/board/test_reach_by_name_live.py::test_bring_the_board_here_asks_first_when_someone_is_using_it`

*2026-11-11:* proven on the real board without a second router or a person moving
about (`net_sim`, owner: "I want all the tests to be automated including the one you
complain of having a real device hotspot and asking me to perform actions or move
around"). The rehearsal found that "Bring the board here" during a join still on
its way was overridden by that join; the asked-for network is now joined at once.

## D-11 · 2026-11-11 · A failed update connection says where it stopped, and asks the name again

When the board cannot reach the update server, its log names the stage and the
address: `name` (the host would not resolve), `connect` (the address would not
open), `tls_timeout` (it opened, then the handshake stalled), `tls` (mbedTLS's own
error), or `no_answer` (connected, the answer never came). Example:
`[OTA] no connection (-1 after 10494 ms, attempt 1: connect
release-assets.githubusercontent.com:443 185.199.109.133)`. The host is the last
redirect's. A URL's `user:password@` never reaches the log.

When the address itself is in doubt (`connect`, `tls_timeout`), the board forgets
the name before the next try. That try asks the router again and usually gets
another of the host's addresses.

**Why:** the board's check failed from the owner's hometown network for days,
and the log said only `no connection (-1)`. On 2026-11-11 at Bangalore it failed
again. One of GitHub's four download addresses (185.199.109.133) did not answer
from the home network, while the other three connected in 10 ms. The router hands
the four out in turn, and the board kept the first one it got until it expired,
so every try went to the dead one. The Mac beside it worked because it moves on
to the next address after 7.5 s. Proven on 09F7C8: before, 4 of 4 tries failed;
after, the second try reached another address and the check passed. The owner:
"Please look at the OTA. It's very important and necessary. I don't want any
regression issues."

**Guarded by:**
- `tests/unit/test_ota_logic.py::test_a_failed_connection_says_where_it_stopped`
- `tests/unit/test_ota_logic.py::test_the_host_a_failure_names`
- `tests/unit/test_ota_logic.py::test_every_planted_bug_is_caught`


## D-12 · 2026-11-22 · The Core's base clicks shut like a remote's battery cover, sized for a coarse print

The HID-Fi Core's base hooks in at the front with two 1 mm tabs and clicks shut at
the back: two flat spring beams in the plate carry square catches into a window in
the back wall. A fingernail through the window opens it. Every mating gap is 0.3 mm,
the build fails unless a plate grown by a coarse print's error (0.24 mm sideways,
0.28 mm up and down) still meets nothing, and nothing that flexes may stand up off
the bed (it would bend across its layers). Strain stays inside PETG's repeat-use
limit (2.5%); closing takes 3-20 N, opening 10 N at most.

**Why:** the first print fitted the board perfectly, but its 0.8 mm front tongue never
went into a slot with 0.10-0.15 mm round it, and a press-fit peg snapped when the
base was pulled off. The owner: "3D printing has the swirls around each and
everything, and when we plan a design like something to be inserted, it simply
doesn't work. You know how battery compartment works in TV remotes. No screws
involved but there will be something that can snap better. That shall not be slim or
easy to break, but carefully engineered."

The parametric source stays outside this repository (it checks itself, with a
planted fault for every check); these tests measure what is published here.

**Guarded by:**
- `tests/static/test_shell_print.py::test_the_front_tabs_have_a_coarse_prints_room_all_round`
- `tests/static/test_shell_print.py::test_the_catches_click_into_the_window_with_room_to_spare`
- `tests/static/test_shell_print.py::test_the_plate_clears_the_peg_posts_above_it`
- `tests/static/test_shell_print.py::test_the_plate_itself_has_no_pegs_or_press_fits`
- `tests/static/test_shell_print.py::test_the_latch_bends_inside_petgs_repeat_use_strain`
- `tests/static/test_shell_print.py::test_the_published_design_passed_every_build_check`

## D-13 · 2026-11-22 · The board says how hot it runs, and the power card says what really keeps the radio on

The firmware reads the ESP32-S3's die sensor every 2 s. `status` carries `temp_c`
and `temp_peak_c`; `temp_log` returns the last four hours a minute at a time, with
gaps left as gaps, so a tool that was not watching can still see what happened. The
dashboard's device card shows it. `tools/thermal_watch.py` logs it for hours and
finds by itself when the board was put in its shell or taken out (a steady-
temperature shift nothing the board reports explains), and never blames the shell
for the board's own load.

The power card no longer says "Radio sleeping" while the access point is on: Espressif
documents that modem sleep "works in station-only mode" (ESP-IDF stable, ESP32-S3,
"Wi-Fi Power-saving Mode", read 2026-11-22), so the access point keeps the radio
awake whatever the power mode asks.

**Why:** the first printed Core was hot to the touch after 4-5 hours plugged in and
doing nothing, and nothing on the board could say how hot. The owner: "How would you
read temperatures?" and "which you shall not assume that the board is under shell
always, as a human I would remove it or put it back".

*Revised 2026-11-22, the same day:* **watching must never cost latency.** The owner:
"The watcher at any cost should not degrade the performance of the board and the
latency... I hope we have automated risk management too." So:

1. The sensor is read by its own task on core 0 at the lowest working priority, never
   by `loop()` (core 1, the input path); `loop()` and the handlers copy its results
   under a spinlock held for the copy alone.
2. Every read is timed. Three reads in a row over 1.5 ms switch the sensor off until
   the next start (`temp_off: "slow"`); a read paused now and then by WiFi does not.
   `temp_us_max` reports the slowest.
3. `tools/thermal_watch.py` reads `/api/status` over HTTP only, never a WebSocket,
   never faster than every 10 s, a minute or more while a dashboard is open or an
   update runs, and doubles its interval (to 5 min) while the board answers slowly.
4. On 09F7C8, A/B against v4.3.1 (no sensor code) on the same board, twice each: the
   serial round trip 18.0 ms median both; the dashboard's ping 7.8-8.8 ms against
   8.5-9.0 ms; under a 60 Hz pointer stream 10.6-11.6 ms against 10.1-10.6 ms. With a
   watcher polling five times harder than the tool may: median 7.73 against 8.18 quiet.

What the numbers mean for the shell (vents in the base, the access point's default)
waits for the measurements in docs/ROADMAP.md; this entry only makes them possible.

**Guarded by:**
- `tests/unit/test_thermal.py::test_every_planted_bug_is_caught`
- `tests/unit/test_thermal.py::test_a_minute_the_sensor_failed_is_a_gap_not_the_last_value`
- `tests/unit/test_thermal.py::test_the_firmware_feeds_the_sensor_into_this_header`
- `tests/unit/test_thermal_watch.py::test_putting_it_in_a_shell_is_found_without_anyone_saying_so`
- `tests/unit/test_thermal_watch.py::test_the_radio_waking_is_not_blamed_on_the_shell`
- `tests/unit/test_thermal.py::test_three_slow_reads_in_a_row_switch_the_sensor_off_for_good`
- `tests/unit/test_thermal.py::test_a_read_paused_now_and_then_is_not_a_fault`
- `tests/unit/test_thermal_watch.py::test_the_watcher_backs_off_whenever_the_board_has_better_things_to_do`
- `tests/unit/test_thermal_watch.py::test_the_watcher_never_opens_a_websocket_or_sends_input`
- `tests/board/test_thermal_hw.py::test_a_watcher_does_not_slow_the_dashboard`
- `tests/board/test_thermal_hw.py::test_the_sensors_reads_stay_inside_the_guards_budget`
- `tests/ui/bdd/features/settings.feature: The device card says how hot the chip runs, and its peak since start`
- `tests/ui/bdd/features/settings.feature: The radio only really sleeps with the access point off, and the card says which`

## D-14 · 2026-11-23 · Two subnets in one home: named, found, and the board brought over

**Revises D-10's "right on the board's network" and adds to D-02.**

The owner's home: "ARVISANTU" is the main router (192.168.1.x); "ARVISANTU C6" and
"ARVISANTU EXT" are extenders, and C6 runs a subnet of its own (192.168.0.x) behind the
main router. The Mac moves between ARVISANTU and C6 by itself (the macOS log shows
it hop four times in two minutes) and so does the board. The Companion then said
"not on this computer's WiFi - set it up from the board's own WiFi" while both were
on saved networks of the same home. The owner: "I have seen this problem many times,
where though I am in same network I was seeing in companion that the board is not the
same network as MAC. Please handle these" and "STRICTLY NO ASSUMPTIONS".

It was measured, not guessed: the board's mDNS answered a unicast query; mDNS just
does not cross from one router's network to the other, and the Mac had moved.

1. **The board names both networks.** It records each saved network's mask when it
   joins (raw, as the router gave it; never guessed - 192.168.1.11 is not on
   192.168.0.0 just because a /23 would say so). Each Companion heartbeat's answer says
   which network the board is on, which saved network holds the computer's own address
   ("" when none), whether they are the same, and each saved network's subnet.
2. **The Companion says so,** in place of "not on this computer's WiFi": "This computer
   is on ARVISANTU C6, the board is on ARVISANTU: two networks", with **Bring the board
   to this computer's network** - a request signed with the pairing like an update
   (`op: "bring"`), done by the dashboard's own rule: it asks first when someone is
   using the board right on its network, and refuses (`unknown_network`) when the
   computer's network is not one the board has saved. A board it cannot reach at all
   is "last on <network>", not set up from scratch - after the same 20 s whether it was
   never found or was online and then lost (that one said "Connecting to <its old
   address>" for ever, 2026-11-23). When it was last on this computer's own network,
   the card says the board left it and how to bring it back, never "join this
   computer to" the network it is already on: from the main router this computer
   cannot reach the extender's own subnet (the other way it can).
3. **The Companion finds the board without mDNS:** every address it had (the newest
   four), the OS's ARP table for its MAC after one datagram to each address of this
   computer's subnets (real netmasks, at most a /22), and the subnets of the board's
   saved networks swept for port 80 - at most every 2 minutes, and only for a board on
   this computer's USB that nothing else found. The MAC the address reports decides,
   as for every candidate.
4. **Right on the board's network** (D-10) now also needs the computer's address inside
   the board's own subnet (or its access point): traffic between the main router's
   subnet and the extender's arrived with the Mac's own address (no address
   translation on that path), so "said == came from" held for a Mac on the other
   subnet (found on 09F7C8). After the board joins a network nobody counts as
   right on it until their next heartbeat, and a heartbeat in the moment before an
   asked-for move cannot anchor the network being left.
5. **A move answers first.** `wifi_prefer now` and `bring` reply, then move 3 s later:
   a reply sent after the move never arrived, and 300 ms and 1.5 s still lost it 1 run
   in 3 just after a join (one TCP retransmission is ~1 s). A Companion that gets no
   answer says "moving" and finds the board where it went.

Proven on 09F7C8 with the real Companion service (companion/tests/two_networks.py):
moved to the other router, found again by itself in 23 s, both networks named, brought
back with one click. The board test ran 6 of 6 with every answer received.

**Guarded by:**
- `tests/unit/test_net_logic.py::test_a_mask_is_never_guessed`
- `tests/unit/test_net_logic.py::test_an_address_is_on_the_saved_network_whose_subnet_holds_it`
- `tests/unit/test_where_the_computer_is.py::test_the_heartbeat_names_both_networks_when_they_differ`
- `tests/unit/test_where_the_computer_is.py::test_a_routed_computer_carrying_its_own_address_is_not_right_on_the_network`
- `tests/unit/test_where_the_computer_is.py::test_after_a_move_nobody_counts_as_right_on_the_new_network_until_they_say_so`
- `tests/unit/test_where_the_computer_is.py::test_bring_asks_first_when_someone_is_using_the_board_on_its_network`
- `tests/unit/test_where_the_computer_is.py::test_bring_to_a_network_the_board_does_not_know_moves_nothing`
- `tests/unit/test_where_the_computer_is.py::test_a_forged_or_replayed_bring_moves_nothing`
- `tests/unit/test_where_the_computer_is.py::test_the_firmware_records_each_networks_mask_and_answers_bring_signed`
- `tests/board/test_where_the_computer_is_hw.py::test_on_two_networks_the_board_is_named_and_brought_back`
- `companion/crates/hidfi-companion/src/menu.rs::two_networks_are_named_with_a_way_to_bring_the_board_here`
- `companion/crates/hidfi-companion/src/menu.rs::a_board_last_seen_on_another_home_network_is_not_set_up_from_scratch`
- `companion/crates/hidfi-agent/src/locate.rs::a_board_that_moved_to_another_of_its_networks_is_found_in_its_subnets`
- `companion/crates/hidfi-agent/src/locate.rs::a_board_mdns_cannot_reach_is_found_through_the_arp_table`
- `tests/ui/companion/features/window.feature: On the home's other network, the board is brought to this computer's network`

## D-15 · 2026-11-23 · With the preferred network away, the one used last is home (revises D-02)

**Seen:** the owner brought the board to a hotspot (it became the computer's network),
switched the hotspot off and was home on ARVISANTU. The board fell back to the louder
saved network, ARVISANTU C6 (an extender with its own subnet, 2 dB to 19 dB louder
where the board sits), and stayed: D-02 counted any network an owner ever used as home,
so it never looked again - and the owner's computer on ARVISANTU cannot reach C6. The
rehearsal of exactly that story passed only when ARVISANTU happened to be louder (it
failed 2 full runs in 3); staged with the neighbour louder it failed every time, on
v4.3.1 as on v4.4 before this.

**Decision:** when the preferred network (the paired computer's, else the one used
most recently) is not in range or too weak, home until it is back is the network
used most recently that is in range and workable. The board moves there - at once,
as for coming home - only from a network used less recently, never between two
names of one network, and never away from an owner device using the board right
there (D-10). The preferred network back in range wins over it. It scans for that
network while one used more recently than the one it is on could be in range - also
when the preferred network is resting after failed joins, which had stopped every
scan, so nothing was ever decided (the first try of this fix, on 09F7C8). On the
most recently used network already, nothing more is scanned than before.

**Also:** the rehearsal now plays the owner's computer itself (a Companion paired over
USB, beating from this computer) and first puts the board on a network this computer
is on. It relied on the owner's real Companion without checking it was there.

**Guarded by:**
- `tests/unit/test_net_logic.py::test_with_the_preferred_network_away_the_one_used_last_is_home`
  (with three planted mutations of the new rule, each caught)
- `tests/unit/test_net_logic.py::test_random_homes_against_the_written_rules`
- `tests/unit/test_net_logic.py::test_every_planted_bug_is_caught`
- `tests/board/test_networks_rehearsed.py::test_the_hotspot_goes_and_a_stronger_neighbour_does_not_keep_it_from_the_owner`
- `tests/board/test_networks_rehearsed.py::test_the_owner_moves_and_their_hotspot_goes_it_finds_them_again`

## D-16 · 2026-11-25 · One network is one subnet behind one router (revises D-02)

**Seen:** the owner's hometown extender ARVISANTU C6 and his Bangalore router ACT are both
192.168.0.x. D-02 counted "same subnet" as one network (a router and its extender), so the
board treated two homes 350 km apart as one: asked for C6 in Bangalore it answered "on it".

**Decision:** two saved networks are one network when they share a subnet **and** a router.
After each join the board learns its router - an id of the gateway's MAC, read from its own
ARP table under the network stack's lock (one ARP request if missing), saved with the network
(`rt`), never shown (status `router_known` only says it is learned). A router not learned
yet - networks saved before this, until their next join - splits nothing: D-02 as before. A
router and its extender share the router and stay one network. Nothing is asked of anyone.

**Guarded by:**
- `tests/unit/test_net_logic.py::test_two_homes_on_one_default_subnet_are_two_networks`
- `tests/unit/test_net_logic.py::test_random_homes_against_the_written_rules`
- `tests/unit/test_net_logic.py::test_every_planted_bug_is_caught`
- `tests/board/test_networks_rehearsed.py::test_two_homes_on_one_subnet_are_two_networks`

## D-17 · 2026-11-25 · Tests never drop off or rejoin the owner's router

**Seen:** during the network rehearsal module the owner's router lost its internet and had to
be restarted by hand. Every rehearsed move had really disconnected from and rejoined it - a
made-up network "rode" the real one by a real join - dozens of associations and address
requests in minutes. The cause was not proven; the risk is not acceptable: the owner may be
away (Remote Control) and the session itself runs over that router.

**Decision:** in a rehearsal (`net_sim`, `net_fault`) with the radio associated, leaving a
network and joining the real one or a made-up one riding it happen in the board's logic only:
the link reads down, the supervisor scans, chooses and "joins" exactly as for real, and the
join completes after a moment - or times out if staged as not there. The radio stays
associated. Real joins remain where a test is about a real network; status counts them
(`radio_joins`, `radio_drops`). The test harness fails a rehearsal test that causes any, and
rations all others to 10 real joins in any 10 minutes, pausing instead of pressing on.

**Guarded by:**
- `tests/conftest.py::_the_router_is_spared` (every rehearsal test: zero real joins or drops)
- `tests/unit/test_e2e_unattended.py::test_rehearsals_touch_no_radio`
