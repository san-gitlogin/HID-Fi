Feature: The Trackpad tab
  The pad itself (touch gestures are covered in depth by tests/test_gestures.py),
  the mouse buttons and wheel, grab, the gesture tiles, the app switcher, the feel
  settings, the keyboard deck and fullscreen.

  Background:
    Given the dashboard is open

  Scenario: Moving a finger on the pad moves the pointer
    When I drag one finger across the trackpad
    Then the board receives a "move" frame

  Scenario: Tapping the pad clicks
    When I tap the trackpad
    Then the board receives a click of the "left" button

  Scenario: With tap-to-click off, a tap does not click
    When I tap "Tap to click"
    And I tap the trackpad
    Then the board receives no click

  Scenario Outline: The mouse buttons click
    When I tap "<label>" in the Buttons card
    Then the board receives a click of the "<button>" button

    Examples:
      | label  | button |
      | Left   | left   |
      | Right  | right  |
      | Middle | middle |

  Scenario: Double click sends two clicks in one report
    When I tap "Double click"
    Then the board receives a double click

  Scenario: The buttons can be shown as a mouse, and the choice is remembered
    When I tap the "#btnBtnView" control
    Then the mouse-shaped buttons are shown
    When I reload the dashboard
    Then the mouse-shaped buttons are shown

  Scenario: Dragging the wheel scrolls, and a quick tap on it is a middle click
    When I tap the "#btnBtnView" control
    And I drag the mouse wheel down
    Then the board receives a scroll
    When I tap the mouse wheel
    Then the board receives a click of the "middle" button

  Scenario: Grab holds the button down until it is tapped again
    When I tap the "#btnGrab" control
    Then the board receives a press of the "left" button
    And every grab button shows it is on
    When I tap the "#btnGrab" control
    Then the board receives a release of the "left" button

  Scenario Outline: Each gesture tile sends its gesture, named for the computer it drives
    Given the board is plugged into a <os> computer
    When I reload the dashboard
    And I tap "<tile>"
    Then the board receives {"cmd": "gesture", "name": "<gesture>"}

    Examples:
      | os      | tile            | gesture       |
      | Windows | Task view       | task_view     |
      | Windows | Desktop left    | desktop_left  |
      | Windows | Desktop right   | desktop_right |
      | Mac     | Mission Control | task_view     |
      | Mac     | Space left      | desktop_left  |
      | Mac     | Space right     | desktop_right |
      | Linux   | Activities      | task_view     |
      | Linux   | Workspace left  | desktop_left  |
      | Windows | Back            | back          |
      | Windows | Forward         | forward       |

  Scenario: A quick tap on Switch app flips to the last window
    When I tap "Switch app"
    Then the board receives {"cmd": "gesture", "name": "switch_app"}

  Scenario: Holding Switch app keeps the switcher open on a PC, and Cancel backs out
    Given the board is plugged into a Windows computer
    When I reload the dashboard
    And I hold "Switch app"
    Then the app switcher is open
    And the board holds "ALT" and taps "TAB"
    When I tap the "#aswNext" control
    Then "#aswN" reads "2"
    When I tap the "#aswCancel" control
    Then the board taps "ESC"
    And the board releases "ALT"
    And the app switcher is closed

  Scenario: On a Mac the switcher holds Command, and Switch to it commits without Escape
    Given the board is plugged into a Mac computer
    When I reload the dashboard
    And I hold "Switch app"
    Then the board holds "GUI" and taps "TAB"
    When I tap the "#aswGo" control
    Then the board releases "GUI"
    And the board never tapped "ESC"

  Scenario: The feel settings are remembered on this phone
    When I set the "Speed" slider to 4.0
    Then "#vSens" reads "4.0"
    When I reload the dashboard
    Then "#vSens" reads "4.0"

  Scenario: Glide at zero reads off
    When I set the "Glide" slider to 0
    Then "#vMom" reads "off"

  Scenario: Scroll direction can be natural or classic
    When I tap "Natural"
    Then I see the message "macOS scrolling"
    And "Natural" is switched on
    When I tap "Classic"
    Then I see the message "Windows scrolling"
    And "Classic" is switched on

  Scenario Outline: Each feel switch toggles and is remembered
    When I tap "<switch>"
    And I reload the dashboard
    Then "<switch>" is switched <state>

    Examples:
      | switch                  | state |
      | Three finger drag       | on    |
      | Three finger app switch | off   |
      | Tap to click            | off   |

  Scenario: The keyboard deck flips the pad to a keyboard, and is remembered
    When I tap the "#btnDeck" control
    Then the pad shows its keyboard
    When I reload the dashboard
    Then the pad shows its keyboard
    When I tap the "#btnDeck" control
    Then the pad shows the trackpad

  Scenario: Text typed on the deck is sent, with or without Enter
    When I tap the "#btnDeck" control
    And I type "hello there" into "#pkText"
    And I tap the "#pkSend" control
    Then the board receives {"cmd": "type", "text": "hello there", "enter": false}
    When I type "and go" into "#pkText"
    And I tap the "#pkEnter" control
    Then the board receives {"cmd": "type", "text": "and go", "enter": true}

  Scenario: The phone's own Go key on the deck sends with Enter
    When I tap the "#btnDeck" control
    And I type "quick" into "#pkText"
    And I press Enter in "#pkText"
    Then the board receives {"cmd": "type", "text": "quick", "enter": true}

  Scenario: The keys on the deck press keys on the PC
    When I tap the "#btnDeck" control
    And I press the deck key "q"
    Then the board receives the key "q" down and up

  Scenario: Fullscreen takes over the screen and can be left three ways
    When I tap the "#btnFs" control
    Then the pad is fullscreen
    When I tap the "#btnFsExitTop" control
    Then the pad is not fullscreen
    When I tap the "#btnFs" control
    And I press Escape
    Then the pad is not fullscreen
    When I tap the "#btnFs" control
    And I go back in the browser
    Then the pad is not fullscreen
    And the "Trackpad" tab is shown

  Scenario: The keyboard is available in fullscreen too
    When I tap the "#btnFs" control
    And I tap the "#btnDeck" control
    Then the pad shows its keyboard
    And the fullscreen keyboard has keys

  # ---- drags, pinches and glide ----------------------------------------------------

  Scenario: Holding one finger still, then moving, drags with the left button down
    When I hold one finger on the trackpad and then move it
    Then the board receives a press of the "left" button
    And the pointer moved while the button was down
    And the board receives a release of the "left" button

  Scenario: Tap, then touch again and move, is a drag - the pointer is nudged first
    When I tap the trackpad and at once touch again and move
    Then the board receives a press of the "left" button
    And the board received a 6 px nudge before the press
    And the board receives a release of the "left" button

  Scenario: With three finger drag on, three fingers drag
    When I tap "Three finger drag"
    And I move three fingers across the trackpad
    Then the board receives a press of the "left" button
    And the board receives a release of the "left" button

  Scenario Outline: Pinching zooms
    When I pinch <direction> on the trackpad
    Then the board receives {"cmd": "gesture", "name": "<gesture>"}

    Examples:
      | direction | gesture  |
      | out       | zoom_in  |
      | in        | zoom_out |

  Scenario: A two finger flick keeps scrolling after the fingers lift
    When I flick two fingers up the trackpad
    Then the scroll carries on after the fingers lift

  Scenario: With glide off, scrolling stops when the fingers lift
    When I set the "Glide" slider to 0
    And I flick two fingers up the trackpad
    Then the scroll stops when the fingers lift

  Scenario Outline: Speed, acceleration and scroll speed are shown and remembered
    When I set the "<slider>" slider to <value>
    Then "#<shown>" reads "<value>"
    When I reload the dashboard
    Then "#<shown>" reads "<value>"

    Examples:
      | slider | value | shown |
      | Accel  | 2.5   | vAcc  |
      | Scroll | 3.0   | vScr  |

  Scenario: Haptics can be switched off, and stay off
    When I tap "Haptics"
    And I reload the dashboard
    Then "Haptics" is switched off

  # ---- grab, everywhere it is offered ----------------------------------------------

  Scenario: Grab in the Buttons card
    When I tap the "#btnGrab3" control
    Then the board receives a press of the "left" button
    And every grab button shows it is on
    When I tap the "#btnGrab3" control
    Then the board receives a release of the "left" button

  Scenario: Grab under the mouse-shaped buttons
    When I tap the "#btnBtnView" control
    And I tap the "#btnGrab4" control
    Then the board receives a press of the "left" button
    When I tap the "#btnGrab4" control
    Then the board receives a release of the "left" button

  Scenario: Grab in the fullscreen pad's bar
    When I tap the "#btnFs" control
    And I tap the "#btnGrab2" control
    Then the board receives a press of the "left" button
    When I tap the "#btnGrab2" control
    Then the board receives a release of the "left" button

  # ---- the app switcher's other controls --------------------------------------------

  Scenario: In the switcher, rows move with the arrows and Back steps back
    Given the board is plugged into a Windows computer
    When I reload the dashboard
    And I hold "Switch app"
    And I tap the "#aswNext" control
    And I tap the "#aswNext" control
    Then "#aswN" reads "3"
    When I tap the "#aswPrev" control
    Then "#aswN" reads "2"
    And the board taps "TAB" with "SHIFT" held
    When I tap the "#aswDown" control
    Then the board taps "DOWN"
    When I tap the "#aswUp" control
    Then the board taps "UP"
    When I tap the "#aswGo" control
    Then the board releases "ALT"

  Scenario: Leaving the page with the switcher open lets go of Alt
    Given the board is plugged into a Windows computer
    When I reload the dashboard
    And I hold "Switch app"
    Then the board holds "ALT" and taps "TAB"
    When the page is hidden
    Then the board releases "ALT"
    And the app switcher is closed

  # ---- three fingers ------------------------------------------------------------------

  Scenario Outline: Three fingers swiped up or down
    When I swipe three fingers <direction> the trackpad
    Then the board receives {"cmd": "gesture", "name": "<gesture>"}

    Examples:
      | direction | gesture      |
      | up        | task_view    |
      | down      | show_desktop |

  Scenario Outline: With three finger app switch off, a sideways swipe changes desktop
    When I tap "Three finger app switch"
    And I swipe three fingers <direction> the trackpad
    Then the board receives {"cmd": "gesture", "name": "<gesture>"}

    Examples:
      | direction | gesture       |
      | left      | desktop_left  |
      | right     | desktop_right |

  Scenario: With three finger app switch on, a sideways swipe walks the switcher and lifting picks
    Given the board is plugged into a Windows computer
    When I reload the dashboard
    And I swipe three fingers right the trackpad
    Then the board holds "ALT" and taps "TAB"
    And the board tapped "TAB" at least 2 times
    And the board releases "ALT"
    And the board never tapped "ESC"
    And the app switcher is closed

  Scenario: A three finger tap is a middle click
    When I tap three fingers on the trackpad
    Then the board receives a click of the "middle" button

  Scenario: Holding Switch app and sliding picks a window as the thumb lifts
    Given the board is plugged into a Windows computer
    When I reload the dashboard
    And I hold "Switch app" and slide 130 px right and 60 px down
    Then the board tapped "TAB" at least 3 times
    And the board taps "DOWN"
    And the board releases "ALT"
    And the app switcher is closed

  Scenario: If the board drops the link while the switcher is up, the switcher closes
    Given the board is plugged into a Windows computer
    When I reload the dashboard
    And I hold "Switch app"
    Then the app switcher is open
    When the board drops the connection
    Then the app switcher is closed

  Scenario: Coming back to the page asks the board for its state at once
    When the page is hidden
    And the page is shown again
    Then the board receives {"cmd": "status"}

  # ---- haptics, where the browser allows them -----------------------------------------

  Scenario: Where the browser cannot vibrate, Haptics is shown off and the reason given
    Given this phone's browser cannot vibrate
    When I reload the dashboard
    Then "#hapNote" contains "Haptics are not available in this browser"

  Scenario: On iOS the haptic is the system tick of a switch control, and a click plays it
    Given this phone's browser is iOS Safari with switch controls
    When I reload the dashboard
    Then "#hapNote" contains "the system tick iOS plays"
    When I tap the trackpad
    Then the hidden switch control was flipped

  Scenario: Tap, then touch again and hold still, also becomes a drag
    When I tap the trackpad and at once touch again and hold still
    Then the board receives a press of the "left" button
    And the board receives a release of the "left" button

  Scenario: If the browser cancels a held Switch app, the switcher stays up to be tapped
    Given the board is plugged into a Windows computer
    When I reload the dashboard
    And I hold "Switch app" until the browser cancels the touch
    Then the app switcher is open
    And the board has not released "ALT"
