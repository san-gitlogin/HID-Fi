Feature: The Gamepad tab (beta)
  The controller is a second USB device, off by default; turning it on or off
  reboots the board. Two sticks, a D-pad and twelve buttons, streamed as binary
  frames only while the controller is enabled.

  Scenario: Disabled by default, the tab explains itself and sends nothing
    Given the dashboard is open
    When I open the "Gamepad" tab
    Then the controller overlay offers "Enable controller"
    When I press the D-pad "up"
    Then the board receives no gamepad frame

  Scenario: Enabling the controller reboots the board, and the tab comes alive
    Given the dashboard is open
    When I open the "Gamepad" tab
    And I tap the "#gpEnable" control
    Then the board receives {"cmd": "gamepad_enable", "on": true, "reboot": true}
    And the reboot overlay says "Adding the controller device."
    And the dashboard reconnects
    And I see the message "Board is back"
    And the controller is shown as enabled

  Scenario: The power button asks first, and a no changes nothing
    Given the controller is enabled on the board
    And the dashboard is open
    When I open the "Gamepad" tab
    And I will answer "no" to the next question
    And I tap the "#btnGpToggle" control
    Then the board does not receive "gamepad_enable"
    When I will answer "yes" to the next question
    And I tap the "#btnGpToggle" control
    Then the board receives {"cmd": "gamepad_enable", "on": false, "reboot": true}

  Scenario Outline: The left and right sticks stream their position and spring back
    Given the controller is enabled on the board
    And the dashboard is open
    When I open the "Gamepad" tab
    And I push the <side> stick to the <direction>
    Then the board receives the <side> stick at <axis> <sign>
    And the last gamepad frame has both sticks centred

    Examples:
      | side  | direction | axis | sign     |
      | left  | right     | x    | positive |
      | left  | top       | y    | negative |
      | right | left      | x    | negative |
      | right | bottom    | y    | positive |

  Scenario Outline: The D-pad sends its direction while held
    Given the controller is enabled on the board
    And the dashboard is open
    When I open the "Gamepad" tab
    And I press the D-pad "<dir>"
    Then a gamepad frame carried the hat value <hat>
    And the last gamepad frame has the hat released

    Examples:
      | dir   | hat |
      | up    | 1   |
      | right | 3   |
      | down  | 5   |
      | left  | 7   |

  Scenario Outline: Each button sets its own bit while held
    Given the controller is enabled on the board
    And the dashboard is open
    When I open the "Gamepad" tab
    And I press the gamepad button "<button>"
    Then a gamepad frame carried the button bit <bit>
    And the last gamepad frame has no buttons held

    Examples:
      | button | bit |
      | A      | 0   |
      | B      | 1   |
      | X      | 3   |
      | Y      | 4   |
      | LB     | 6   |
      | RB     | 7   |
      | LT     | 8   |
      | RT     | 9   |
      | Back   | 10  |
      | Start  | 11  |

  Scenario: A board that does not come back within 25 seconds gets a plain explanation
    Given the board will not come back after a reboot
    And the dashboard is open
    When I open the "Gamepad" tab
    And I tap the "#gpEnable" control
    And 26 seconds pass on the page
    Then "#bootTitle" reads "Still waiting"
    And "#bootMsg" contains "check the USB cable and reload the page"
    When the board takes connections again
    Then the dashboard reconnects
    And I see the message "Board is back"
