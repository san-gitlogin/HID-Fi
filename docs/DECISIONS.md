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
3. **That network wins while it is workable** (−75 dBm or better), even against
   a stronger network nobody uses. Weaker than that, or gone, the board falls
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
