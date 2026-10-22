Feature: Screen - seeing the computer's own picture
  The HID-Fi Companion app on the computer streams its screen straight to the
  dashboard; the board only pairs it and hands out a one-use ticket. These run
  against the board model and a stand-in computer (tests/support/fake_pc.py)
  that streams a real WebRTC picture.

  Scenario: Nothing to show until a computer is paired
    Given the dashboard is open and signed in with "4826"
    When I open the "Screen" tab
    Then the screen says "See a computer’s screen here"
    And "#scrList" contains "Install HID-Fi Companion"
    And "#scrList" contains "Tap Pair here"
    And the trackpad is in the Screen tab

  # Found by a person: the tab showed a link to GitHub and nothing else, so a
  # first-time user had no idea what to do - and a phone on the board's own
  # WiFi cannot open GitHub. The steps are on the board, for this computer's OS.
  Scenario: With no computer yet, the steps are here, for this computer's OS
    Given the board is plugged into a Mac computer
    And the board is on the home WiFi
    And the dashboard is open and signed in with "4826"
    When I open the "Screen" tab
    Then "#scrList" contains "Download for Mac"
    And "#scrList" does not contain "join the board to your home WiFi"
    When I choose "Windows" in the install steps
    Then "#scrList" contains "install-windows.ps1"
    When I tap the copy button in the install steps
    Then I see the message "Copied"
    When I choose "Linux" in the install steps
    Then "#scrList" contains "install-linux.sh"

  Scenario: A board not on the home WiFi says to join it first
    Given the dashboard is open and signed in with "4826"
    When I open the "Screen" tab
    Then "#scrList" contains "First, join the board to your home WiFi"
    When I tap the "#scrToNet" control
    Then the "Settings" tab is shown

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

  # Seen live: in the tab, the name and fps sat over the top left of the
  # picture and the buttons over the bottom right, hiding what was under them.
  # Like a window: a slim title bar above, a slim toolbar below, nothing over it.
  Scenario Outline: Nothing covers the picture, on any screen, either way up
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    When the window is <width> by <height>
    And I open the "Screen" tab
    Then the screen shows "Studio PC" live
    And the name and frame rate are in the title bar above the picture
    And nothing covers the picture

    Examples:
      | width | height |
      | 390   | 844    |
      | 844   | 390    |
      | 320   | 568    |
      | 568   | 320    |
      | 820   | 1180   |
      | 1180  | 820    |
      | 1400  | 900    |
      | 1920  | 1080   |
      | 700   | 1000   |

  Scenario: A portrait monitor gets a tall picture, not a strip in a wide box
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running, its screen 360 by 640
    When I open the "Screen" tab
    Then the screen shows "Studio PC" live
    And the picture is far taller than a 16:10 box would let it be
    And nothing covers the picture

  Scenario: Floating the picture over other tabs
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    When I open the "Screen" tab
    Then the screen shows "Studio PC" live
    When I tap the "#scrPop" control
    And I open the "Media" tab
    Then the floating window is shown, still live
    And the floating window is inside the screen
    And the frame rate is in the floating window's title bar, not over the picture
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

  Scenario: Six screens already watching, and joining when one closes
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    And "Studio PC" turns viewers away with "busy"
    When I open the "Screen" tab
    Then the screen says "Studio PC is already on six screens"
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

  # ---- Watch only: the picture alone on a screen that just watches ------------------------

  Scenario: A screen that only watches
    Given "Studio PC" is paired and running and this screen opens "#watch"
    Then the screen shows "Studio PC" live
    And the dashboard is hidden behind the picture
    And "#watchName" reads "Studio PC"
    And the trackpad is back in the Trackpad tab
    When I tap the "#watchExit" control
    Then the "Screen" tab is shown
    And the screen shows "Studio PC" live

  Scenario: Opening the screen on its own from the Screen tab
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    When I open the "Screen" tab
    And I open the screen on its own
    Then a new window opens at "#watch"

  Scenario: The bar fades from a live picture and comes back on a touch
    Given "Studio PC" is paired and running and this screen opens "#watch"
    Then the screen shows "Studio PC" live
    When 4 seconds pass with nothing touched
    Then the watch bar is hidden
    When I touch the picture
    Then the watch bar is shown

  Scenario: Full screen, and zooming from a keyboard
    Given "Studio PC" is paired and running and this screen opens "#watch"
    Then the screen shows "Studio PC" live
    When I tap the "#watchFs" control
    Then the page is full screen
    When I tap the "#watchFs" control
    Then the page is not full screen
    When I press "+" on the keyboard
    Then the picture is zoomed in
    When I press "0" on the keyboard
    Then the picture fits the screen
    When I press "F" on the keyboard
    Then the page is full screen

  Scenario: Choosing between two computers while only watching
    Given "Studio PC" and "Den Laptop" are paired and running and this screen opens "#watch"
    Then the screen says "Choose a computer"
    When I tap the "#watchPick2" control
    And I choose "Den Laptop" in the list
    Then the screen shows "Den Laptop" live
    When I tap the "#watchWho" control
    Then the dialog "Choose a computer" is open

  Scenario: Only watching with nothing paired yet
    Given nothing is paired and this screen opens "#watch"
    Then the screen says "Set it up first on the dashboard’s Screen tab"

  Scenario: The install guide is one tap away
    Given the dashboard is open and signed in with "4826"
    When I open the "Screen" tab
    And I open the install guide from "#scrGuide"
    Then the install guide opens on GitHub

  # The owner asked: what about 3-4 monitors? Each viewer picks any of them.
  Scenario: A computer with several displays shows any of them
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" with 3 displays is paired and running
    When I open the "Screen" tab
    Then the screen shows "Studio PC" live
    And there is a display button
    And "#scrBadge" contains "Built-in display"
    When I tap the "#scrDisp" control
    Then the dialog "Choose the display" is open
    And "#sheet" contains "main display"
    And "#sheet" contains "2560 × 1440"
    When I choose "Display 2" in the display list
    Then "Studio PC" was asked to show display 2
    And "#scrBadge" contains "Display 2"

  Scenario: One display, no display button
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    When I open the "Screen" tab
    Then the screen shows "Studio PC" live
    And there is no display button

  Scenario: The display being watched is unplugged: the main one, and a message
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" with 3 displays is paired and running
    When I open the "Screen" tab
    And I tap the "#scrDisp" control
    And I choose "Display 3" in the display list
    Then "#scrBadge" contains "Display 3"
    When display 3 is unplugged from "Studio PC"
    Then I see the message "Display 3 is not connected"
    And "#scrBadge" contains "Built-in display"
    And the screen shows "Studio PC" live

  Scenario: The display chosen is chosen again next time
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" with 3 displays is paired and running
    When I open the "Screen" tab
    And I tap the "#scrDisp" control
    And I choose "Display 2" in the display list
    Then "Studio PC" was asked to show display 2
    When I reload the dashboard
    And I open the "Screen" tab
    Then the screen shows "Studio PC" live
    And "#scrBadge" contains "Display 2"

  # Found by a person: a phone left idle, then woken, asked for the PIN again,
  # signed in - and never showed the paired MacBook again until a reload. The
  # ticket request made while the phone was away never got an answer, and the
  # Screen tab waited for it forever.
  Scenario: After the phone sleeps and signs in again, the paired computer shows by itself
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    When I open the "Screen" tab
    Then the screen shows "Studio PC" live
    When the phone sleeps, and the board forgets its sign-in
    And the phone wakes up
    And I enter the PIN "4826"
    Then the screen shows "Studio PC" live

  Scenario: A ticket request lost with the connection is asked again
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    And the board will not answer the next screen ticket
    When I open the "Screen" tab
    And the board drops the connection
    Then the screen shows "Studio PC" live

  # Found on the real board: with one computer already paired, pairing a second
  # left the Screen tab on neither - the one just paired is the one wanted.
  Scenario: The computer just paired is the one shown, even beside another
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired and running
    And a computer "Den Laptop" is asking to pair
    When I open the "Screen" tab
    And I tap "Pair" for "Den Laptop"
    And "Den Laptop" shows its pairing code
    And I type the code "Den Laptop" shows
    And I confirm the code for "Den Laptop"
    Then I see the message "Paired with Den Laptop"
    And the Screen tab is on "Den Laptop"

  Scenario: The setup steps say what each OS will ask, and what to choose
    Given the board is plugged into a Mac computer
    And the dashboard is open and signed in with "4826"
    When I open the "Screen" tab
    Then "#scrList" contains "Screen Recording"
    And "#scrList" contains "Quit & Reopen"
    When I choose "Windows" in the install steps
    Then "#scrList" contains "administrator approval"
    And "#scrList" contains "Private"
    When I choose "Linux" in the install steps
    Then "#scrList" contains "X11"

  Scenario: A paired computer that is not running says where its app is, on its OS
    Given the board is plugged into a Windows computer
    And the dashboard is open and signed in with "4826"
    And a computer "Den Laptop" is paired but not running
    When I open the "Screen" tab
    Then the screen says "Den Laptop is not reachable"
    And the screen says "Start menu"

  # Seen live: the Mac's Screen Recording switch looked on (left from an older
  # build) while macOS refused this one; viewers sat at 0 fps with no word why.
  Scenario: A computer not allowed to record its screen says what to switch on
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" is paired, running, and not allowed to record its screen
    When I open the "Screen" tab
    Then the screen says "Studio PC is not allowed to record its screen yet"
    And the screen says "switch it off and on again"

  # The owner asked: see every monitor at once, choose how many, pop each out
  # to drag it anywhere, full screen one or all - in one window or several.
  Scenario: All monitors at once, how many, and one popped out
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" with 3 displays is paired and running
    When I open the "Screen" tab
    Then the screen shows "Studio PC" live
    When I tap the "#scrWall" control
    Then the wall shows 3 monitors, all live
    And "Studio PC" was asked to show display 2
    And "Studio PC" was asked to show display 3
    When I choose 2 monitors on the wall
    Then the wall shows 2 monitors, all live
    When I show the next monitor on the first tile
    Then "#wallGrid" contains "Display 2"
    When I pop out the first monitor
    Then a window opens showing only that monitor
    And the wall shows 1 monitors, all live
    When I tap the "#wallExit" control
    Then the "Screen" tab is shown
    And the screen shows "Studio PC" live

  Scenario: Every monitor in its own window at once
    Given the dashboard is open and signed in with "4826"
    And a computer "Studio PC" with 3 displays is paired and running
    When I open the "Screen" tab
    And I tap the "#scrWall" control
    Then the wall shows 3 monitors, all live
    When I tap the "#wallFs" control
    Then the wall fills the screen
    When I tap the "#wallFs" control
    And I make the first monitor fill the screen
    Then only that monitor fills the screen
    When I leave full screen
    And I am watching what the wall opens
    And I tap the "#wallPopAll" control
    Then 3 windows open, one per monitor

  # Found by the live test: a page opened straight at #wall left it at once
  # (start-up wrote the default tab into the address).
  Scenario: The wall opens straight from its address
    Given a computer "Studio PC" with 3 displays is paired and running, and this screen opens "#wall"
    Then the wall shows 3 monitors, all live

