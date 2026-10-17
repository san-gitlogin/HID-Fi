Feature: The Session tab - unlock, lock, saved PCs, sleep and wake, presenter, stay awake
  The board types a password into the PC's login screen, locks it, remembers PCs
  by name (the password never comes back to the page), sleeps and wakes it,
  drives a presentation, and keeps it awake.

  Scenario: Unlock types the password, and asks before typing it again
    Given the dashboard is open
    When I open the "Session" tab
    And I type "hunter2" into "#unlockPw"
    And I tap "Unlock PC"
    Then the board receives {"cmd": "unlock", "password": "hunter2", "ctrl_alt_del": false, "force": false}
    And I see the message "Unlock sent"
    When I will answer "no" to the next question
    And I tap "Unlock PC"
    Then a confirmation asking "already unlocked" was shown
    And the board received 2 unlock requests

  Scenario: Unlock without a password is refused on the page
    Given the dashboard is open
    When I open the "Session" tab
    And I tap "Unlock PC"
    Then I see the message "Enter the password"
    And the board does not receive "unlock"

  Scenario: On a PC, Ctrl+Alt+Del can be sent first, for login screens that need it
    Given the board is plugged into a Windows computer
    And the dashboard is open
    When I open the "Session" tab
    And I tap "Use Ctrl+Alt+Del"
    And I type "hunter2" into "#unlockPw"
    And I tap "Unlock PC"
    Then the board receives {"cmd": "unlock", "ctrl_alt_del": true}

  Scenario: On a Mac the Ctrl+Alt+Del option is hidden, because it would break the unlock
    Given the board is plugged into a Mac computer
    And the dashboard is open
    When I open the "Session" tab
    Then the "#tCad" control is hidden

  Scenario: Lock locks
    Given the dashboard is open
    When I open the "Session" tab
    And I tap the "#btnLock" control
    Then the board receives {"cmd": "lock"}
    And I see the message "Locked"

  Scenario: Lock then unlock does both, three seconds apart
    Given the dashboard is open
    When I open the "Session" tab
    And I type "hunter2" into "#unlockPw"
    And I tap "Lock then unlock"
    Then the board receives {"cmd": "lock", "force": true}
    And I see the message "Locking, unlocking in 3s"
    And within 5 seconds the board receives {"cmd": "unlock", "password": "hunter2", "force": true}

  Scenario: Saving a PC needs an access PIN
    Given the board has no access PIN
    And the dashboard is open
    When I open the "Session" tab
    Then "#pcHint" contains "Set an access PIN under Settings"
    When I tap the "#btnPcNew" control
    Then I see the message "Set an access PIN first"

  Scenario: Saving a new PC stores its name, OS and password on the board
    Given the dashboard is open and signed in with "4826"
    When I open the "Session" tab
    And I tap the "#btnPcNew" control
    And I enter "Work laptop" in "Name"
    And I tap "Windows"
    And I type "hunter2" into "#pc_p"
    And I tap the "#pc_ok" control
    Then the board receives {"cmd": "pc_save", "name": "Work laptop", "os": "windows", "password": "hunter2"}
    And the saved PC "Work laptop" shows "Windows"

  @known_issue:pc_new_event
  Scenario: The + button opens Save this PC, with the unlock password already filled in
    Given the dashboard is open and signed in with "4826"
    When I open the "Session" tab
    And I type "hunter2" into "#unlockPw"
    And I tap the "#btnPcNew" control
    Then the dialog "Save this PC" is open
    And the field "#pc_p" already holds "hunter2"

  Scenario: A saved PC is unlocked by name, and its password never comes back to the page
    Given the board remembers a PC "Desk" running macOS
    And the dashboard is open and signed in with "4826"
    When I open the "Session" tab
    Then the saved PC "Desk" shows "macOS"
    And no reply from the board contained "pw-Desk"
    When I tap "Unlock" on the saved PC "Desk"
    Then the board receives {"cmd": "unlock", "profile": 0}
    And I see the message "Unlock sent"

  Scenario: Editing a saved PC can keep its password, and forgetting one asks first
    Given the board remembers a PC "Desk" running macOS
    And the dashboard is open and signed in with "4826"
    When I open the "Session" tab
    And I tap the "Edit" button on the saved PC "Desk"
    Then the dialog "Edit saved PC" is open
    When I enter "Desk (home)" in "Name"
    And I tap the "#pc_ok" control
    Then the board receives a PC save for "Desk (home)" without a password
    When I will answer "yes" to the next question
    And I tap the "Forget" button on the saved PC "Desk (home)"
    Then the board receives {"cmd": "pc_delete", "slot": 0}
    And I see the message "Forgotten"

  Scenario Outline: Sleep and wake ask first
    Given the dashboard is open
    When I open the "Session" tab
    And I will answer "yes" to the next question
    And I tap "<button>"
    Then a confirmation asking "Send <action> to the PC?" was shown
    And the board receives {"cmd": "system", "action": "<action>"}

    Examples:
      | button   | action |
      | Sleep PC | sleep  |
      | Wake PC  | wake   |

  Scenario: Wake explains what to change when the PC refuses USB wake
    Given the PC is asleep and has not allowed USB wake
    And the dashboard is open
    When I open the "Session" tab
    And I will answer "yes" to the next question
    And I tap "Wake PC"
    Then I see the message "The PC is not set to allow USB wake"
    And the dialog "Wake did not work" is open

  Scenario Outline: The presenter buttons
    Given the dashboard is open
    When I open the "Session" tab
    And I tap the presenter button "<button>"
    Then the board receives {"cmd": "presenter", "action": "<action>"}

    Examples:
      | button | action |
      | Prev   | prev   |
      | Next   | next   |
      | Start  | start  |
      | Black  | black  |
      | White  | white  |
      | End    | end    |

  Scenario: The presentation timer runs, stops and resets
    Given the dashboard is open
    When I open the "Session" tab
    And I tap the "#btnPresPlay" control
    And 2 seconds pass on the page
    Then "#presTime" is no longer "00:00"
    When I tap the "#btnPresPlay" control
    And I tap the "#btnPresReset" control
    Then "#presTime" reads "00:00"

  Scenario: Stay awake starts at the chosen interval and stops
    Given the dashboard is open
    When I open the "Session" tab
    And I set the stay-awake interval to 25 seconds
    Then "#wigIntV" reads "25s"
    When I tap the "#btnWigStart" control
    Then the board receives {"cmd": "wiggle", "mode": "interval", "interval_ms": 25000, "force": true}
    And I see the message "Wiggle on"
    And "#wigState" contains "Active"
    When I tap the "#btnWigStop" control
    Then the board receives {"cmd": "wiggle_stop"}
    And "#wigState" reads "Idle"

  Scenario: A PC save the board refuses says why, and the sheet stays open
    Given the board will refuse the next "pc_save" with "All 8 slots are used"
    And the dashboard is open and signed in with "4826"
    When I open the "Session" tab
    And I tap the "#btnPcNew" control
    And I enter "Spare" in "Name"
    And I type "pw" into "#pc_p"
    And I tap the "#pc_ok" control
    Then I see the message "All 8 slots are used"
    And the dialog is still open

  Scenario: While the board's socket is out of reach, a click still goes through over HTTP
    Given the dashboard is open
    When the board becomes unreachable
    And I open the "Trackpad" tab
    And I tap "Left" in the Buttons card
    Then the board receives a mouse click over HTTP
