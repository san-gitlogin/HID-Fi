Feature: Screen - seeing the computer's own picture
  The HID-Fi Companion app on the computer streams its screen straight to the
  dashboard; the board only pairs it and hands out a one-use ticket. These run
  against the board model and a stand-in computer (tests/support/fake_pc.py)
  that streams a real WebRTC picture.

  Scenario: Nothing to show until a computer is paired
    Given the dashboard is open and signed in with "4826"
    When I open the "Screen" tab
    Then the screen says "See a computer’s screen here"
    And "#scrHelp" contains "Install HID-Fi Companion"
    And the trackpad is in the Screen tab

  Scenario: Without an access PIN no screen is shared
    Given the board has no access PIN
    And the dashboard is open
    When I open the "Screen" tab
    Then the screen says "Set an access PIN to share screens"
    When I tap the "#scrToSetup" control
    Then the "Settings" tab is shown
    And the trackpad is back in the Trackpad tab

  Scenario: Pairing a computer with the code it shows
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is asking to pair
    When I open the "Screen" tab
    Then "#scrList" contains "wants to share its screen"
    When I tap "Pair" for "Studio PC"
    Then the board receives {"cmd": "companion_pair"}
    When "Studio PC" shows its pairing code
    And I type the code "Studio PC" shows
    And I confirm the code for "Studio PC"
    Then I see the message "Paired with Studio PC"
    And "#scrList" contains "Studio PC"

  Scenario: A wrong code says how many tries are left, Enter confirms
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is asking to pair
    When I open the "Screen" tab
    And I tap "Pair" for "Studio PC"
    And "Studio PC" shows its pairing code
    And I type a wrong code for "Studio PC" and press Enter
    Then "#scrList" contains "2 tries left"

  Scenario: A code that is too short is caught before it is sent
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is asking to pair
    When I open the "Screen" tab
    And I tap "Pair" for "Studio PC"
    And "Studio PC" shows its pairing code
    And I type "123" as the code for "Studio PC"
    And I confirm the code for "Studio PC"
    Then "#scrList" contains "The code has six digits"

  Scenario: Typing the code before the computer shows it
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is asking to pair
    When I open the "Screen" tab
    And I tap "Pair" for "Studio PC"
    And I type "123456" as the code for "Studio PC"
    And I confirm the code for "Studio PC"
    Then "#scrList" contains "has not shown its code yet"

  Scenario: Three wrong codes end the attempt
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is asking to pair
    When I open the "Screen" tab
    And I tap "Pair" for "Studio PC"
    And "Studio PC" shows its pairing code
    And I type a wrong code for "Studio PC" and press Enter
    And I type a wrong code for "Studio PC" and press Enter
    And I type a wrong code for "Studio PC" and press Enter
    Then I see the message "Wrong code three times"
    And "#scrList" contains "Three wrong codes"
    When I tap "Dismiss" for "Studio PC"
    Then the board receives {"cmd": "companion_unpair"}

  Scenario: Not now turns a request away
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is asking to pair
    When I open the "Screen" tab
    And I tap "Not now" for "Studio PC"
    Then the board receives {"cmd": "companion_unpair"}
    And "#scrList" does not contain "Studio PC"

  Scenario: A request is announced on whatever tab is open
    Given the dashboard is open and signed in with "4826"
    When a computer "Studio PC" asks to pair now
    Then I see the message "Studio PC wants to share its screen"

  Scenario: Watching a paired computer
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    When I open the "Screen" tab
    Then the screen shows "Studio PC" live
    And "#scrList" contains "on screen"
    And the trackpad is in the Screen tab

  Scenario: Zooming with the buttons, a pinch, a double tap and Ctrl+wheel
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    When I open the "Screen" tab
    Then the screen shows "Studio PC" live
    When I tap the "#scrIn" control
    Then the screen is zoomed to 150%
    When I drag the zoomed picture
    And I tap the "#scrOut" control
    Then the screen fits
    When I pinch outwards on the screen
    Then the screen is zoomed in
    When I tap the "#scrFit" control
    Then the screen fits
    When I double tap the screen
    Then the screen is zoomed to 250%
    When I double tap the screen
    Then the screen fits
    When I hold Ctrl and scroll up on the screen
    Then the screen is zoomed in

  Scenario: Floating the picture over other tabs
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    When I open the "Screen" tab
    Then the screen shows "Studio PC" live
    When I tap the "#scrPop" control
    And I open the "Media" tab
    Then the floating window is shown, still live
    And the floating window is inside the screen
    And the trackpad is back in the Trackpad tab
    When I drag the floating window by its title
    Then the floating window is inside the screen
    When I resize the floating window
    Then the floating window is inside the screen
    When I tap the "#scrFMin" control
    Then "#scrFloat" is minimised
    When I tap the "#scrFMin" control
    Then "#scrFloat" is not minimised
    When I tap the "#scrFDock" control
    Then the "Screen" tab is shown
    And the screen shows "Studio PC" live

  Scenario: The floating window keeps its place, and docks back from the tab
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    When I open the "Screen" tab
    And I tap the "#scrPop" control
    Then "#scrHole" contains "floating window"
    When I tap the "#scrHoleDock" control
    Then the floating window is closed

  Scenario: Closing the floating window stops the picture
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    When I open the "Screen" tab
    Then the screen shows "Studio PC" live
    When I tap the "#scrPop" control
    And I open the "Media" tab
    And I tap the "#scrFClose" control
    Then the floating window is closed
    And "Studio PC" sees the viewer leave

  Scenario: Leaving the tab stops the picture, coming back starts it
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    When I open the "Screen" tab
    Then the screen shows "Studio PC" live
    When I open the "Media" tab
    Then "Studio PC" sees the viewer leave
    When I open the "Screen" tab
    Then the screen shows "Studio PC" live

  Scenario: A computer whose app is not running
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired but not running
    When I open the "Screen" tab
    Then the screen says "Studio PC is not reachable"
    And "#scrList" contains "not running"

  Scenario: Four screens already watching, and joining when one closes
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    And "Studio PC" turns viewers away with "busy"
    When I open the "Screen" tab
    Then the screen says "Studio PC is already on four screens"
    When "Studio PC" has room again
    Then the screen shows "Studio PC" live

  Scenario: A certificate the board did not vouch for is refused
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    And someone in the middle answers for "Studio PC"
    When I open the "Screen" tab
    Then the screen says "Stopped for your safety"

  Scenario: The board unplugged from the computer
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    When I open the "Screen" tab
    Then the screen shows "Studio PC" live
    When the board is unplugged from "Studio PC"
    Then the screen says "HID-Fi is not plugged into Studio PC"

  Scenario: The computer cannot be reached
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    And "Studio PC" turns viewers away with "unreachable"
    When I open the "Screen" tab
    Then the screen says "Cannot reach Studio PC"

  Scenario: Two computers, choosing one
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    And a computer "Den Laptop" is paired and running
    When I open the "Screen" tab
    Then the screen shows "Studio PC" live
    When I tap "Show" for "Den Laptop"
    Then the screen shows "Den Laptop" live

  Scenario: Two computers and no choice made yet
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    And a computer "Den Laptop" is paired and running
    When I reload the dashboard
    And I open the "Screen" tab
    Then the screen says "Choose a computer below"

  Scenario: Removing a computer
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    When I open the "Screen" tab
    And I will answer "yes" to the next question
    And I tap "Remove" for "Studio PC"
    Then a confirmation asking "Its screen can no longer be shown here" was shown
    And the board receives {"cmd": "companion_unpair"}
    And the screen says "See a computer’s screen here"

  Scenario: The computer's app is not listening
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired but its app refuses connections
    When I open the "Screen" tab
    Then the screen says "Cannot reach Studio PC"
