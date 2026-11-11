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

