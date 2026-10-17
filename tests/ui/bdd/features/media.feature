Feature: The Media tab - knobs, playback and quick actions
  Knobs are turned with a finger: each detent is one step, an endless knob only
  ever sends up and down so it cannot drift from the PC. Quick actions are
  editable tiles. Everything customised is saved on the board.

  Background:
    Given the dashboard is open
    And I am on the "Media" tab

  Scenario: Turning the volume knob clockwise turns the volume up, step by step
    When I turn the knob 0 by 120 degrees
    Then the board receives "Volume up" several times
    And the knob 0 shows a positive count

  Scenario: Turning it the other way turns the volume down
    When I turn the knob 0 by -120 degrees
    Then the board receives "Volume down" several times

  Scenario: Holding a media knob without turning it mutes
    When I hold the knob 0 still
    Then the board receives "Mute" once

  Scenario Outline: Playback tiles send their media key
    When I tap "<tile>"
    Then the board receives {"cmd": "media", "key": "<key>"}

    Examples:
      | tile          | key             |
      | Previous      | prev            |
      | Play / pause  | play_pause      |
      | Next          | next            |
      | Stop          | stop            |
      | Dimmer        | brightness_down |
      | Brighter      | brightness_up   |
      | Search        | search          |

  Scenario: The Mute tile toggles, and shows what it believes it set
    When I tap "Mute"
    Then the board receives "Mute" once
    And the "Mute" tile looks active
    When I tap "Mute"
    Then the "Mute" tile looks inactive

  Scenario: A new knob can drive a keyboard combo, and is saved on the board
    When I tap the "#btnKnobNew" control
    Then the dialog "New knob" is open
    When I enter "Zoom" in "Name"
    And I choose "Keyboard combo" for "Action type"
    And I type "CTRL+PLUS" into "#kn_up_k"
    And I type "CTRL+MINUS" into "#kn_dn_k"
    And I tap the "#kn_ok" control
    Then I see the message "Saved to board"
    And the board saved a layout with a knob named "Zoom"
    When I turn the knob 1 by 90 degrees
    Then the board receives {"cmd": "press", "keys": "CTRL+PLUS"}

  Scenario: A knob can be edited and deleted
    When I tap the edit button of knob 0
    Then the dialog "Edit knob" is open
    When I tap "Delete"
    Then the board saved a layout with 0 knobs
    And there is no knob 0

  Scenario: A ranged knob shows a level and stops at its ends
    When I tap the edit button of knob 0
    And I choose "Ranged 0 to 100 with end stops" for "Behaviour"
    And I tap the "#kn_ok" control
    And I turn the knob 0 by 120 degrees
    Then the knob 0 shows a percentage

  Scenario: A layout saved on the board is what every phone sees
    Given the board has a saved layout with a knob named "Scrub"
    When I reload the dashboard
    And I open the "Media" tab
    Then there is a knob named "Scrub"

  Scenario Outline: The default quick actions match the computer
    Given the board is plugged into a <os> computer
    When I reload the dashboard
    And I open the "Media" tab
    And I tap the quick action "Mic mute"
    Then the board receives {"cmd": "press", "keys": "<mute>"}
    And the quick action "<fourth>" is there

    Examples:
      | os      | mute        | fourth          |
      | Mac     | GUI+SHIFT+M | Mission Control |
      | Windows | CTRL+SHIFT+M| Display         |

  Scenario: A toggle quick action shows the state it believes it set
    When I tap the quick action "Mic mute"
    Then the quick action "Mic mute" looks active
    When I tap the quick action "Mic mute"
    Then the quick action "Mic mute" looks inactive

  Scenario: The Lock PC quick action sends the lock command
    When I tap the quick action "Lock PC"
    Then the board receives {"cmd": "lock"}

  Scenario: A long press edits a quick action, Test sends it without saving, Save keeps it
    When I long-press the quick action "Answer"
    Then the dialog "Edit action" is open
    When I enter "F9" in "Value"
    And I tap "Test"
    Then the board receives {"cmd": "press", "keys": "F9"}
    And the board does not receive "ui_save"
    When I tap the "#q_ok" control
    Then I see the message "Saved to board"

  Scenario: A new quick action can be a board command
    When I tap the quick action "Add"
    Then the dialog "New action" is open
    When I enter "Lock now" in "Label"
    And I choose "Board command" for "Type"
    And I enter "lock" in "Value"
    And I tap the "#q_ok" control
    And I tap the quick action "Lock now"
    Then the board receives {"cmd": "lock"}

  Scenario: Deleting a quick action removes it from the board's layout
    When I long-press the quick action "Hang up"
    And I tap "Delete"
    Then the quick action "Hang up" is gone
    And I see the message "Saved to board"

  Scenario: A media knob from the Add tile: its icon, both directions and its step
    When I tap the "#knobAdd" control
    Then the dialog "New knob" is open
    When I enter "Tracks" in "Name"
    And I choose "media" for "Icon"
    And I choose "Next track" for "Turn right"
    And I choose "Previous track" for "Turn left"
    And I set "#kn_s" to 20
    And I tap the "#kn_ok" control
    Then the board saved a layout whose knob "Tracks" is {"i": "media", "t": "media", "up": 181, "dn": 182, "step": 20}
    When I turn the knob 1 by 60 degrees
    Then the board receives "Next track" several times

  Scenario: The New action button in the header, with its own icon
    When I tap the "#btnQuickNew" control
    Then the dialog "New action" is open
    When I enter "Play it" in "Label"
    And I choose "play" for "Icon"
    And I choose "Media key" for "Type"
    And I enter "play_pause" in "Value"
    And I tap the "#q_ok" control
    And I tap the quick action "Play it"
    Then the board receives {"cmd": "media", "key": "play_pause"}
    And the board saved a layout whose quick action "Play it" is {"i": "play", "t": "media", "v": "play_pause"}

  @known_issue:knob_step_label
  Scenario: The knob editor's step label follows the slider
    When I tap the "#knobAdd" control
    And I set "#kn_s" to 20
    Then the step label reads "Degrees per step (20)"
