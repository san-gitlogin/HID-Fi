Feature: The Keyboard tab
  Typing text, the on-screen keyboard with real press and release, modifiers held
  with one finger or latched with a tap, the symbol layer, custom combos and the
  eight macro slots stored on the board.

  Background:
    Given the dashboard is open
    And I am on the "Keyboard" tab

  Scenario: Send types the text without pressing Enter, and clears the box
    When I type "hello world" into "#typeText"
    And I tap the "#btnTypeEnter" control
    Then the board receives {"cmd": "type", "text": "hello world", "enter": true}
    And the "#typeText" box is empty
    When I type "no enter" into "#typeText"
    And I tap the "#btnType" control
    Then the board receives {"cmd": "type", "text": "no enter", "enter": false}

  Scenario: The phone's Go key types with Enter
    When I type "from the go key" into "#typeText"
    And I press Enter in "#typeText"
    Then the board receives {"cmd": "type", "text": "from the go key", "enter": true}

  Scenario: Enter with an empty box sends a bare Return, Send with an empty box asks for text
    When I tap the "#btnTypeEnter" control
    Then the board receives {"cmd": "press", "keys": "ENTER"}
    When I tap the "#btnType" control
    Then I see the message "Type something first"

  Scenario: A character the PC cannot type is reported
    When I type "smile 😀" into "#typeText"
    And I tap the "#btnType" control
    Then I see the message "1 character was skipped"

  Scenario Outline: Each key on the keyboard presses and releases on the PC
    When I press the key "<key>"
    Then the board receives the key "<key>" down and up

    Examples:
      | key       |
      | a         |
      | z         |
      | 5         |
      | ENTER     |
      | BACKSPACE |
      | TAB       |
      | UP        |
      | ESC       |
      | F5        |

  Scenario: Holding Shift with one finger while striking a key with another
    When I hold the key "#SHIFT" and press "a"
    Then the board pressed "SHIFT" before "a" and released it after

  Scenario: A quick tap latches a modifier for the next key only
    When I tap the key "#CTRL"
    Then the key "#CTRL" shows latched
    When I press the key "c"
    Then the board receives the key "c" down and up
    And the board releases "CTRL"
    And the key "#CTRL" shows released

  Scenario: Tapping a latched modifier again releases it on the PC
    When I tap the key "#ALT"
    And I tap the key "#ALT"
    Then the board releases "ALT"

  Scenario: Tapping a latched modifier again clears its latched look
    When I tap the key "#ALT"
    And I tap the key "#ALT"
    Then the key "#ALT" shows released

  Scenario: The 123 key swaps letters for symbols and back
    When I tap the key "@sym"
    Then the keyboard shows the key "!"
    When I press the key "!"
    Then the board receives the key "!" down and up
    When I tap the key "@abc"
    Then the keyboard shows the key "q"

  Scenario Outline: Modifier caps are labelled for the computer
    Given the board is plugged into a <os> computer
    When I reload the dashboard
    And I open the "Keyboard" tab
    Then the key "#GUI" is labelled "<gui>"
    And the key "#CTRL" is labelled "<ctrl>"

    Examples:
      | os      | gui | ctrl |
      | Mac     | ⌘   | ⌃    |
      | Windows | ⊞   | Ctrl |
      | Linux   | ◆   | Ctrl |

  Scenario: A custom combo is pressed as written, also from the Go key
    When I type "CTRL+SHIFT+ESC" into "#customKeys"
    And I tap the "#btnCombo" control
    Then the board receives {"cmd": "press", "keys": "CTRL+SHIFT+ESC"}
    When I type "ALT+F4" into "#customKeys"
    And I press Enter in "#customKeys"
    Then the board receives {"cmd": "press", "keys": "ALT+F4"}

  Scenario: The + adds a macro, and it appears as a tile
    When I tap the "#btnMacroNew" control
    Then the dialog "New macro" is open
    When I enter "Greeting" in "Name"
    And I enter the macro steps "text Hello" and "key ENTER"
    And I tap "Save"
    Then the board receives {"cmd": "macro_save", "slot": 0, "name": "Greeting", "steps": ["text Hello", "key ENTER"]}
    And I see the message "Macro saved"
    And the macro slot 0 is named "Greeting"

  Scenario: With no macros yet, the Add tile opens a new one
    When I tap the "#macroAdd" control
    Then the dialog "New macro" is open

  Scenario: Tapping a saved macro runs it
    Given the board has a macro "Sign off" in slot 2
    When I reload the dashboard
    And I open the "Keyboard" tab
    And I tap the macro slot 2
    Then the board receives {"cmd": "macro_run", "slot": 2}
    And I see the message "Running Sign off"

  # Seen live: nobody found the long press, so nobody could delete a macro; and
  # people rest a finger on a tile to scroll. Editing is the Edit button only.
  Scenario: The Edit button turns macros into editors, and Delete removes one
    Given the board has a macro "Sign off" in slot 2
    When I reload the dashboard
    And I open the "Keyboard" tab
    And I tap the "#btnMacroEdit" control
    And I tap the macro slot 2
    Then the dialog "Edit macro" is open
    And the macro editor shows the steps of "Sign off"
    And the board does not receive "macro_run"
    When I tap "Delete"
    Then the board receives {"cmd": "macro_delete", "slot": 2}
    And I see the message "Macro deleted"
    And the macro slot 2 is empty

  Scenario: Holding a finger on a macro never opens an editor
    Given the board has a macro "Sign off" in slot 2
    When I reload the dashboard
    And I open the "Keyboard" tab
    And I long-press the macro slot 2
    Then no dialog is open

  Scenario: A macro with no steps is refused before it reaches the board
    When I tap the "#btnMacroNew" control
    And I tap "Save"
    Then I see the message "No steps"
    And the board does not receive "macro_save"

  Scenario: Thirty-two macros, and the + says so when all are used
    Given the board has macros in every slot
    When I reload the dashboard
    And I open the "Keyboard" tab
    Then there are 32 macros
    When I tap the "#btnMacroNew" control
    Then I see the message "All 32 macros used - delete one first"

  # The macros share the board's storage with the WiFi settings, the PIN and
  # the vault, which must always be able to save.
  Scenario: Macros past 4000 characters in all are refused, and the editor says why
    Given the board's macros already hold 3990 characters
    When I reload the dashboard
    And I open the "Keyboard" tab
    And I tap the "#btnMacroNew" control
    And I enter "Long one" in "Name"
    And I enter the macro steps "text abcdefghijklmnop" and "key ENTER"
    And I tap "Save"
    Then I see the message "Macros are full (4000 characters in all) - delete one first"
    And the dialog "New macro" is open

  Scenario: With the board's storage nearly full, a macro is refused and nothing is lost
    Given the board's storage is nearly full
    When I tap the "#btnMacroNew" control
    And I enter "Greeting" in "Name"
    And I enter the macro steps "text Hello" and "key ENTER"
    And I tap "Save"
    Then I see the message "The board's storage is nearly full - delete a macro or a text shortcut first"
    And the dialog "New macro" is open

  # Seen live: a phone capitalises each line ("Text ..."), and those lines were
  # skipped without a word. Any case works, and a line with no verb is typed.
  Scenario: A macro written on a phone types every line
    Given the board has a macro "Booking" in slot 0 with the lines "Text Thirumalai T", "KEY tab" and "60"
    When I reload the dashboard
    And I open the "Keyboard" tab
    And I tap the macro slot 0
    Then the macro ran: type "Thirumalai T", press "tab", type "60"

  # ---- one keyboard per size of screen ------------------------------------------------

  Scenario Outline: The keyboard picks its layout from the room it has
    Given the dashboard is open on a <device>
    When I open the "Keyboard" tab
    Then the keyboard is the "<tier>" layout

    Examples:
      | device       | tier    |
      | small phone  | compact |
      | phone        | compact |
      | tablet       | ansi    |
      | desktop      | tkl     |
      | wide desktop | full    |

  Scenario: On a wide screen the numpad types the keypad keys
    Given the dashboard is open on a wide desktop
    When I open the "Keyboard" tab
    And I tap the key "KP7"
    Then the board receives the key "KP7" down and up
    When I tap the key "NUMLOCK"
    Then the board receives the key "NUMLOCK" down and up

  Scenario: The deck under the trackpad stays ANSI in its card, however wide the screen
    Given the dashboard is open on a wide desktop
    When I tap the "#btnDeck" control
    Then the deck keyboard is the "ansi" layout
