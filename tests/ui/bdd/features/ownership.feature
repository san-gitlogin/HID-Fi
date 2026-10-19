Feature: Who owns the board - the first PIN, recovery, and someone else trying
  The board's own WiFi password is the proof of ownership when the first access
  PIN is set over WiFi. Recovery - a forgotten WiFi password or access PIN -
  happens only over the USB COM cable. Wrong PINs lock out the device that typed
  them, not the owner. Every row of the ownership table in SECURITY.md is here.

  # ---- setting the first PIN --------------------------------------------------------

  Scenario: The first PIN asks for the board's WiFi password, and says why
    Given the board has no access PIN
    And the dashboard is open
    When I open the "Settings" tab
    Then "#pinProof" is visible
    And "#pinProofWhy" contains "proves the board is yours"

  Scenario: The first PIN without the WiFi password is stopped on the page
    Given the board has no access PIN
    And the dashboard is open
    When I open the "Settings" tab
    And I type the new PIN "1357"
    And I tap the "#btnPin" control
    Then I see the message "Enter this board's WiFi password first"
    And the board does not receive "set_auth"

  Scenario: The first PIN with the wrong WiFi password is refused by the board
    Given the board has no access PIN
    And the dashboard is open
    When I open the "Settings" tab
    And I enter the board's WiFi password "not-the-password"
    And I type the new PIN "1357"
    And I tap the "#btnPin" control
    Then I see the message "That is not this board's WiFi password"
    And the board has no access PIN still

  Scenario Outline: The first PIN with the right WiFi password, on either network
    Given the board has no access PIN
    And the board is on the home network
    And this phone reaches the board over <network>
    When I open the dashboard there
    And I open the "Settings" tab
    And I enter the board's WiFi password "k7m2-x9qp-4tre"
    And I type the new PIN "1357"
    And I tap the "#btnPin" control
    Then the board receives {"cmd": "set_auth", "token": "1357", "ap_password": "k7m2-x9qp-4tre"}
    And I see the message "PIN set"
    And "#pinProof" is hidden
    And the WiFi password field is empty

    Examples:
      | network                    |
      | the board's own WiFi       |
      | the home network           |

  Scenario: Someone on the home network cannot claim the board
    Given the board has no access PIN
    And the board is on the home network
    And this phone reaches the board over the home network
    When I open the dashboard there
    And someone on the home network tries to set the PIN "0000" without the WiFi password
    Then the board has no access PIN still

  Scenario: An open board's first PIN can only be set from its own WiFi
    Given the board has no access PIN
    And the board's own WiFi is open
    And the board is on the home network
    And this phone reaches the board over the home network
    When I open the dashboard there
    And someone on the home network tries to set the PIN "0000" without the WiFi password
    Then the board has no access PIN still
    And the board said "This board's WiFi is open. Connect to it to set the first PIN."

  Scenario: On an open board's own WiFi, the first PIN needs no password
    Given the board has no access PIN
    And the board's own WiFi is open
    And the dashboard is open
    When I open the "Settings" tab
    Then "#pinProof" is hidden
    When I type the new PIN "1357"
    And I tap the "#btnPin" control
    Then I see the message "PIN set"

  Scenario: Changing a PIN you know needs the old PIN, not the WiFi password
    Given the dashboard is open and signed in with "4826"
    When I open the "Settings" tab
    Then "#pinProof" is hidden
    When I type the new PIN "1357"
    And I tap the "#btnPin" control
    Then the board receives {"cmd": "set_auth", "token": "1357", "old": "4826"}
    And I see the message "PIN set"

  Scenario: The WiFi password is never sent over WiFi
    Given the dashboard is open and signed in with "4826"
    When I open the "Settings" tab
    Then nothing the page received contains the board's WiFi password

  # ---- explanations ----------------------------------------------------------------

  Scenario: How this works, in short headed lists
    Given the dashboard is open
    When I open the "Settings" tab
    And I tap the "#btnSecInfo" control
    Then the dialog "PIN, passwords and recovery" is open
    And the explanation covers "Three things, three jobs"
    And the explanation covers "Where you are decides what is needed"
    And the explanation covers "If you forget something"
    And the explanation covers "If someone else tries"
    And the explanation shows both recovery commands
    When I tap the "#siDone" control
    Then no dialog is open

  Scenario: The WiFi password can be shown while typing it
    Given the board has no access PIN
    And the dashboard is open
    When I open the "Settings" tab
    And I enter the board's WiFi password "k7m2-x9qp-4tre"
    And I tap the "#pinProofEye" control
    Then the WiFi password field shows its text
    When I tap the "#pinProofEye" control
    Then the WiFi password field hides its text

  Scenario: Forgot the PIN, from Settings: the USB steps and what happens to the vault
    Given the dashboard is open and signed in with "4826"
    When I open the "Settings" tab
    And I tap the "#btnPinForgot" control
    Then the dialog "Forgot the access PIN" is open
    And the explanation shows both recovery commands
    And the explanation covers "What happens to stored passwords"
    When I tap the "#fpDone" control
    Then no dialog is open

  Scenario: Forgot the PIN, from the PIN prompt, and back to the prompt
    Given the board's access PIN is "4826"
    When I open the dashboard without signing in
    And I tap the "#pinAskForgot" control
    Then the dialog "Forgot the access PIN" is open
    When I tap the "#fpDone" control
    Then the dialog "Access PIN" is open

  Scenario Outline: The access point card advises on the password it has
    Given the board's WiFi password is <kind>
    And the dashboard is open
    When I open the "Settings" tab
    Then the password advice <shows>

    Examples:
      | kind                    | shows                                          |
      | the one it made itself  | recommends choosing your own                   |
      | the old shared default  | warns to change it                             |
      | one I chose             | is not shown                                   |

  # ---- someone else trying ---------------------------------------------------------

  Scenario: Wrong PINs lock out the device that typed them, not the owner
    Given the board's access PIN is "4826"
    And the board is on the home network
    When a device on the home network tries 3 wrong PINs
    Then that device is locked out
    And the owner's phone on the home network can still sign in with "4826"

  Scenario: Many wrong PINs from the home network lock it, but not the board's own WiFi
    Given the board's access PIN is "4826"
    And the board is on the home network
    When 20 devices on the home network each try 1 wrong PIN
    Then a new device on the home network is locked out
    And the owner's phone on the board's own WiFi can still sign in with "4826"

  Scenario: A connection that never signs in is closed, and the dashboard recovers
    Given the board's access PIN is "4826"
    And the board closes connections that do not sign in within 1.5 seconds
    When I open the dashboard without signing in
    And 3 seconds pass on the page
    Then the board closed that connection
    When I enter the PIN "4826"
    Then the dashboard is signed in
