Feature: Factory reset - the board as new, without a pin in a pinhole
  Settings ends with Factory reset. A plain warning lists what goes, a checkbox
  says "I understand", and a 10-second countdown with one large Cancel comes
  before anything is erased - no word to type. A computer that is not paired can
  ask; the owner approves or declines here, behind the PIN (docs/FACTORY_RESET.md).

  Background:
    Given the page's clock can be fast-forwarded

  Scenario: The warning says what goes and what stays, and nothing starts until I tick the box
    Given the dashboard is open and signed in with "4826"
    When I open the "Settings" tab
    And I tap the "#btnFactory" control
    Then the dialog "Factory reset" is open
    And the factory-reset warning lists "The access PIN and every setting"
    And the factory-reset warning lists "Stored passwords (the vault)"
    And the factory-reset warning lists "Paired computers (screen sharing)"
    And the factory-reset sheet says it keeps "its own WiFi name"
    And the factory-reset button is disabled
    When I tick "I understand everything on this board will be erased"
    Then the factory-reset button is enabled

  Scenario: Cancel before starting closes it, and nothing is sent
    Given the dashboard is open and signed in with "4826"
    When I open the "Settings" tab
    And I tap the "#btnFactory" control
    And I tap the "#frCancel" control
    Then no dialog is open
    And the board does not receive "factory_reset"

  Scenario: The countdown can be cancelled, and nothing is sent
    Given the dashboard is open and signed in with "4826"
    When I open the "Settings" tab
    And I tap the "#btnFactory" control
    And I tick "I understand everything on this board will be erased"
    And I tap the "#frGo" control
    Then "#frStop" reads "Cancel (10)"
    When 4 seconds pass on the page
    Then "#frStop" reads "Cancel (6)"
    When I tap the "#frStop" control
    Then I see the message "Cancelled - nothing was changed"
    And no dialog is open
    When 10 seconds pass on the page
    Then the board does not receive "factory_reset"

  Scenario: Closing the dialog during the countdown cancels it too
    Given the dashboard is open and signed in with "4826"
    When I open the "Settings" tab
    And I tap the "#btnFactory" control
    And I tick "I understand everything on this board will be erased"
    And I tap the "#frGo" control
    And I close the dialog
    Then I see the message "Cancelled - nothing was changed"
    When 10 seconds pass on the page
    Then the board does not receive "factory_reset"

  Scenario: After the countdown the board is erased and the page says what to do next
    Given the dashboard is open and signed in with "4826"
    When I open the "Settings" tab
    And I tap the "#btnFactory" control
    And I tick "I understand everything on this board will be erased"
    And I tap the "#frGo" control
    And 10 seconds pass on the page
    Then the board receives {"cmd":"factory_reset","confirm":true}
    And "#frMsg" contains "Erased."
    And "#frMsg" contains "Join its WiFi"

  Scenario: When the board refuses, the page says nothing was changed
    Given the board will refuse the next "factory_reset" with "Not while an update is being installed"
    And the dashboard is open and signed in with "4826"
    When I open the "Settings" tab
    And I tap the "#btnFactory" control
    And I tick "I understand everything on this board will be erased"
    And I tap the "#frGo" control
    And 10 seconds pass on the page
    Then "#frMsg" contains "Nothing was changed: Not while an update is being installed"

  Scenario: A computer that is not paired asks, and the owner approves
    Given the dashboard is open and signed in with "4826"
    When the computer "LAPTOP-X" asks to factory reset the board
    Then the dialog "Factory reset requested" is open
    And "#sheetin" contains "LAPTOP-X asks to factory reset this board"
    When I tick "I understand everything on this board will be erased"
    And I tap the "#frGo" control
    And 10 seconds pass on the page
    Then the board receives the approval of that request
    And the request from "LAPTOP-X" was approved

  Scenario: The owner declines, and nothing is erased
    Given the dashboard is open and signed in with "4826"
    When the computer "LAPTOP-X" asks to factory reset the board
    And I tap the "#frNo" control
    Then I see the message "Declined - nothing was changed"
    And the request from "LAPTOP-X" was declined

  Scenario: A dashboard opened after the request shows it - after the PIN, never over it
    Given the board's access PIN is "4826"
    And the computer "LAPTOP-X" has asked to factory reset the board
    When I open the dashboard and sign in with "4826"
    Then the dialog "Factory reset requested" is open
    And "#sheetin" contains "LAPTOP-X"

  Scenario: A paired computer's reset says so, not "BOOT was held"
    Given the dashboard is open and signed in with "4826"
    When a paired computer opens the board's USB link for a factory reset
    And I wait for the dashboard to reconnect
    Then I see the message "A paired computer is factory resetting this board over its USB cable"
