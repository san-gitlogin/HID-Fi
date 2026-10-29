Feature: The Companion's window - a card per board
  The window on macOS (WebKit), Windows (WebView2, Chromium) and Linux (the
  browser) shows this computer and every board as a card in a grid. Each
  scenario runs in Chromium and in WebKit, with the states the app's own model
  produces (tests/golden/companion_window_states.tsv). docs/COMPANION_WINDOW.md.

  # ---- it fits, at every size ----

  Scenario Outline: "<state>" fits a <width> px window
    Given the window is <width> px wide
    When the app shows "<state>"
    Then nothing scrolls sideways and no text is cut off
    And every button is inside its card or panel and at least 34 px tall
    And the page reported no error

    Examples:
      | state            | width |
      | looking          | 380   |
      | not-plugged      | 380   |
      | not-plugged      | 1280  |
      | one-online       | 380   |
      | one-online       | 880   |
      | one-live         | 600   |
      | pairing-code     | 380   |
      | waiting-for-pair | 600   |
      | screen-recording | 380   |
      | two-boards       | 380   |
      | two-boards       | 880   |
      | mixed            | 380   |
      | mixed            | 600   |
      | mixed            | 880   |
      | mixed            | 1280  |
      | companion-ready  | 600   |
      | sound-blocked    | 380   |
      | updating         | 380   |
      | updating         | 880   |
      | fifty            | 380   |
      | fifty            | 1280  |
      | markup           | 380   |

  Scenario Outline: The grid uses the width it has
    Given the window is <width> px wide
    When the app shows "mixed"
    Then the boards are in <columns> column(s)

    Examples:
      | width | columns |
      | 380   | 1       |
      | 700   | 2       |
      | 1000  | 3       |
      | 1440  | 4       |

  # ---- what a card says ----

  Scenario: An online board shows its address, version and round trip
    When the app shows "one-online"
    Then the card "HID-Fi 0A0BB8" reads "Online"
    And the card "HID-Fi 0A0BB8" shows "192.168.0.104"
    And the card "HID-Fi 0A0BB8" shows "v4.1"
    And the round trip of "HID-Fi 0A0BB8" reads "12 ms" and is good

  Scenario: The round trip's colour follows how quick it is
    When the app shows "mixed"
    Then the round trip of "HID-Fi 0A0BB8" reads "12 ms" and is good
    And the round trip of "HID-Fi 09F7C8" reads "64 ms" and is fair
    And the round trip of "HID-Fi DA1A2C" reads "184 ms" and is poor
    And the round trip of "HID-Fi 113355" reads "No reply" and is poor
    And the card "HID-Fi 113355" reads "Not answering"
    And the card "HID-Fi AA0001" has no round trip

  Scenario: A board being watched is live, with its count
    When the app shows "one-live"
    Then the card "HID-Fi 0A0BB8" reads "Live"
    And the card "HID-Fi 0A0BB8" shows "2 watching"
    And this computer reads "shown on 2 dashboards"

  Scenario: The pairing code is in the card that asks for it
    When the app shows "pairing-code"
    Then the card "HID-Fi 0A0BB8" shows the code "042 117"
    And the card "HID-Fi 0A0BB8" reads "Type the code"

  Scenario: A paired board that is not plugged in is shown, dimmed, last
    When the app shows "mixed"
    Then the last card is "HID-Fi BEEF01"
    And the card "HID-Fi BEEF01" reads "Not plugged in"
    And the card "HID-Fi BEEF01" has the button "Forget pairing"

  Scenario: What needs the person comes first
    When the app shows "mixed"
    Then the first card is "HID-Fi AA0002"
    And the counts read "1 needs you", "3 online" and "3 offline"

  Scenario: No board yet: what to do, step by step
    When the app shows "not-plugged"
    Then the window reads "Plug HID-Fi into this computer"
    And the window reads "2 HID-Fi boards are on the network"
    And there are 3 steps and no card

  Scenario: Still looking
    When the app shows "looking"
    Then the window reads "Looking for HID-Fi"

  Scenario: Names and messages are shown as text, never run
    When the app shows "markup"
    Then this computer reads "<script>window.__pwned=1</script>Mac"
    And the card "HID-Fi 0A0BB8" shows "<img src=x onerror=window.__pwned=1>"
    And nothing in the page ran from a name

  # ---- buttons do what they say ----

  Scenario: Open the dashboard
    When the app shows "two-boards"
    And I click "Open dashboard" on "HID-Fi 0A0BB8"
    Then the app is asked "act:open:DCB4D90A0BB8"
    And a note says "Opening the dashboard"

  Scenario: Pair again after a request ran out
    When the app shows "mixed"
    And I click "Pair again" on "HID-Fi AA0002"
    Then the app is asked "act:pair:DCB4D9AA0002"

  Scenario: Forgetting is under Details and asks first
    When the app shows "one-online"
    Then the card "HID-Fi 0A0BB8" has no button "Forget pairing" in view
    When I open the details of "HID-Fi 0A0BB8"
    And I click "Forget pairing" on "HID-Fi 0A0BB8"
    Then a dialog asks "Forget this pairing?" naming "HID-Fi 0A0BB8"
    And the app was asked nothing
    When I answer "Cancel"
    Then the app was asked nothing
    And the focus is back on "Forget pairing"
    When I click "Forget pairing" on "HID-Fi 0A0BB8"
    And I answer "Forget"
    Then the app is asked "act:forget:DCB4D90A0BB8"

  Scenario: Escape closes the dialog without forgetting
    When the app shows "mixed"
    And I click "Forget pairing" on "HID-Fi BEEF01"
    And I press "Escape"
    Then no dialog is open
    And the app was asked nothing

  Scenario: Updating a board, and how far it is
    When the app shows "mixed"
    Then the card "HID-Fi 0A0BB8" shows "Update 4.2"
    And the card "HID-Fi 0A0BB8" shows "from 4.1 · 1.8 MB · over WiFi"
    And the card "HID-Fi 09F7C8" shows "Downloading update · 42%"
    And the card "HID-Fi DA1A2C" shows "Security update 4.2"
    When I click "Update to 4.2" on "HID-Fi 0A0BB8"
    Then the app is asked "act:update:DCB4D90A0BB8"
    When I click "Update to 4.2 by cable" on "HID-Fi DA1A2C"
    Then the app is asked "act:update-cable:DCB4D9DA1A2C"
    When I click "Cancel update" on "HID-Fi 09F7C8"
    Then the app is asked "act:cancel:DCB4D909F7C8"

  Scenario: A downloaded update: restart now, or keep this version
    When the app shows "updating"
    And I click "Restart now" on "HID-Fi 0A0BB8"
    Then the app is asked "act:restart:DCB4D90A0BB8"
    When I click "Keep this version" on "HID-Fi 0A0BB8"
    Then the app is asked "act:cancel:DCB4D90A0BB8"
    When I click "Resume update" on "HID-Fi 09F7C8"
    Then the app is asked "act:update-cable:DCB4D909F7C8"

  Scenario: This computer's sound and microphone switches
    When the app shows "one-online"
    Then the switch "Sound" is on and "Microphone" is off
    When I click the switch "Sound"
    Then the app is asked "act:sound-system"
    When I click the switch "Microphone"
    Then the app is asked "act:sound-mic"

  Scenario: Why sound cannot be shared, and the way to fix it
    When the app shows "sound-blocked"
    Then the window reads "The microphone cannot be shared"
    When I click "Allow microphone access"
    Then the app is asked "act:allow-mic"

  Scenario: Checking for updates now
    When the app shows "one-online"
    And I click "Check for updates"
    Then the app is asked "act:check-updates"
    When the app shows "checking"
    Then "Checking…" cannot be clicked

  Scenario: What concerns the computer is a banner, once
    When the app shows "screen-recording"
    Then there is a banner "Allow Screen Recording"
    And no card mentions "Screen Recording"
    When I click "Allow Screen Recording"
    Then the app is asked "act:allow-screen"
    When the app shows "mixed"
    And I click "Open network settings"
    Then the app is asked "act:net-settings"

  Scenario: The Companion's own update
    When the app shows "mixed"
    And I click "Download Companion 4.2.0"
    Then the app is asked "act:companion-get"
    When the app shows "companion-ready"
    And I click "Install 4.2.0 and restart"
    Then the app is asked "act:companion-install"

  Scenario: Copying a board's address from its details
    When the app shows "one-online"
    And I open the details of "HID-Fi 0A0BB8"
    And I copy "MAC" of "HID-Fi 0A0BB8"
    Then a note says "Copied DC:B4:D9:0A:0B:B8"

  Scenario: In a browser (Linux), copying uses the browser's own clipboard
    Given the window is open in a browser, not the app
    When the app shows "one-online"
    And I open the details of "HID-Fi 0A0BB8"
    And I copy "Address" of "HID-Fi 0A0BB8"
    Then a note says "Copied 192.168.0.104"
    And the clipboard holds "192.168.0.104" where the browser lets a test read it
    And the app was asked nothing

  Scenario: Details show the round trip and when it last answered
    Given the clock is 3 seconds after the last answer
    When the app shows "one-online"
    And I open the details of "HID-Fi 0A0BB8"
    Then the details of "HID-Fi 0A0BB8" read "Round trip" as "12 ms (median of the last 5; last 15 ms, spread 4 ms)"
    And the details of "HID-Fi 0A0BB8" read "Answer time" as "28 ms for a whole request"
    And the details of "HID-Fi 0A0BB8" read "Last answer" as "3 s ago"
    When 5 seconds pass
    Then the details of "HID-Fi 0A0BB8" read "Last answer" as "8 s ago"
    When I close the details of "HID-Fi 0A0BB8"
    Then the details of "HID-Fi 0A0BB8" are closed

  # ---- selecting, one or several ----

  Scenario: A click selects one card, a second click lets it go
    When the app shows "two-boards"
    And I click the card "HID-Fi 0A0BB8"
    Then only "HID-Fi 0A0BB8" is selected
    And the selection bar reads "1 selected"
    When I click the card "HID-Fi 09F7C8"
    Then only "HID-Fi 09F7C8" is selected
    When I click the card "HID-Fi 09F7C8"
    Then nothing is selected
    And the selection bar is hidden

  Scenario: Command or Control adds a card, Shift selects a range, the box toggles
    When the app shows "mixed"
    And I click the card "HID-Fi AA0002"
    And I click the card "HID-Fi 09F7C8" holding the add key
    Then "HID-Fi AA0002" and "HID-Fi 09F7C8" are selected
    When I click the card "HID-Fi 113355" holding Shift
    Then 4 cards are selected
    When I tick the box of "HID-Fi DA1A2C"
    Then 3 cards are selected
    When I click "Clear" in the selection bar
    Then nothing is selected

  Scenario: The selection bar offers only what every selected board can do
    When the app shows "mixed"
    And I click the card "HID-Fi 0A0BB8"
    And I click the card "HID-Fi 09F7C8" holding the add key
    Then the selection bar offers "Open dashboards" and "Forget"
    When I click the card "HID-Fi BEEF01" holding the add key
    Then the selection bar offers "Forget" only
    When I click "Forget" in the selection bar
    Then a dialog asks "Forget 3 pairings?" naming "HID-Fi 0A0BB8", "HID-Fi 09F7C8" and "HID-Fi BEEF01"
    When I answer "Forget"
    Then the app is asked "act:forget:DCB4D90A0BB8", "act:forget:DCB4D909F7C8" and "act:forget:DCB4D9BEEF01"
    And nothing is selected

  Scenario: Opening several dashboards at once, and updating several boards
    When the app shows "fifty"
    And I select every card with Command or Control A
    Then 50 cards are selected
    When I click "Clear" in the selection bar
    And I click the card "HID-Fi 000002"
    And I click the card "HID-Fi 000003" holding the add key
    And I click "Open dashboards" in the selection bar
    Then the app is asked "act:open:DCB4D9000002" and "act:open:DCB4D9000003"
    When the app shows "updating"
    And I click the card "HID-Fi DA1A2C"
    And I click the card "HID-Fi 113355" holding the add key
    And I click "Update" in the selection bar
    Then the app is asked "act:update:DCB4D9DA1A2C" and "act:update:DCB4D9113355"
    When I click the card "HID-Fi 0A0BB8" holding the add key
    Then the selection bar offers "Open dashboards" and "Forget"

  Scenario: Pairing several boards again at once
    When the app shows "pair-again-two"
    And I click the card "HID-Fi 0A0BB8"
    And I click the card "HID-Fi 09F7C8" holding the add key
    Then the selection bar offers "Open dashboards" and "Pair again"
    When I click "Pair again" in the selection bar
    Then the app is asked "act:pair:DCB4D90A0BB8" and "act:pair:DCB4D909F7C8"
    And a note says "Pairing again"

  Scenario: The keyboard does everything the mouse does
    Given the window is 1280 px wide
    When the app shows "mixed"
    And I click the card "HID-Fi AA0002"
    And I press "ArrowRight"
    Then "HID-Fi 0A0BB8" has the focus
    When I press "ArrowDown"
    Then the card below has the focus
    When I press "Home"
    Then "HID-Fi AA0002" has the focus
    When I press "End"
    Then "HID-Fi BEEF01" has the focus
    When I press "ArrowLeft"
    And I press "Space"
    Then "HID-Fi AA0002" and "HID-Fi AA0001" are selected
    When I press "Shift+ArrowLeft"
    Then 3 cards are selected
    When I press "Enter"
    Then the details of "HID-Fi 113355" are open
    When I press "Escape"
    Then nothing is selected
    When I press "Escape"
    Then the details of "HID-Fi 113355" are closed
    When I press "Escape"
    Then the app is asked "close"
    When I press "ArrowUp"
    And I press "Shift+Space"
    And I press "Meta+A"
    Then 7 cards are selected

  # ---- many boards ----

  Scenario: With four boards or more, a filter with counts
    When the app shows "fifty"
    Then the filter reads "All 50", "Needs you 8", "Online 35" and "Offline 7"
    When I filter "Needs you"
    Then 8 cards are shown
    When I filter "Offline"
    Then 7 cards are shown
    When I filter "Online"
    Then 35 cards are shown
    When I filter "All"
    Then 50 cards are shown

  Scenario: A selection survives a filter that hides it, and a filter can match nothing
    When the app shows "mixed"
    And I click the card "HID-Fi AA0002"
    And I filter "Online"
    Then 3 cards are shown
    And the selection bar reads "1 selected"
    When the app shows "mixed-no-attention"
    And I filter "Needs you"
    Then the window reads "No board matches this filter"

  Scenario: Two boards need no filter
    When the app shows "two-boards"
    Then there is no filter

  # ---- updates keep what the person was doing ----

  Scenario: An update to the window keeps details open, selection and focus
    When the app shows "mixed"
    And I open the details of "HID-Fi 09F7C8"
    And I click the card "HID-Fi 0A0BB8"
    And I put the focus on "Update to 4.2" of "HID-Fi 0A0BB8"
    When the app shows "mixed" with every round trip 10 ms slower
    Then the round trip of "HID-Fi 0A0BB8" reads "22 ms" and is good
    And the details of "HID-Fi 09F7C8" are open
    And only "HID-Fi 0A0BB8" is selected
    And "Update to 4.2" of "HID-Fi 0A0BB8" has the focus
    And no card was built again

  Scenario: A board that leaves takes its selection with it
    When the app shows "two-boards"
    And I click the card "HID-Fi 09F7C8"
    And I put the focus on the card "HID-Fi 09F7C8"
    When the app shows "one-online"
    Then nothing is selected
    And the selection bar is hidden
    And "HID-Fi 0A0BB8" has the focus

  Scenario: A board that arrives slides in
    When the app shows "one-online"
    And the app shows "two-boards"
    Then the card "HID-Fi 09F7C8" arrives with an animation

  # ---- fast, accessible, calm ----

  @timing
  Scenario: Fifty boards draw and update quickly
    Given the window is 1280 px wide
    When the app shows "fifty"
    Then drawing took under 40 ms
    When the app shows "fifty" 20 times with new round trips
    Then each update took under 12 ms

  Scenario: Every control has a name a screen reader can say
    When the app shows "mixed"
    Then every button has an accessible name
    And the grid is a list where several can be selected
    And every card says its board and state
    And the switches say whether they are on

  Scenario: Reduced motion means no movement
    Given the person asked for reduced motion
    When the app shows "one-live"
    Then nothing on the page moves
