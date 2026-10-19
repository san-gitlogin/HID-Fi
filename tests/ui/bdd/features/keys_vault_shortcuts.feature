Feature: The Keys tab - the password vault and the shortcuts
  The vault stores passwords and PINs on the board and types them on the PC.
  Reading or typing one asks for a PIN every time; it never needs one to save,
  rename or delete. The shortcuts are hundreds of key combos, per operating system.

  Scenario: Without an access PIN the vault explains why it will not store anything
    Given the board has no access PIN
    And the dashboard is open
    When I open the "Keys" tab
    Then "#vaultHint" contains "Set an access PIN under Settings"
    When I tap the "#btnVaultNew" control
    Then I see the message "Set an access PIN first"
    And no dialog is open

  Scenario: Storing a password
    Given the dashboard is open and signed in with "4826"
    And I am on the "Keys" tab
    When I tap the "#btnVaultNew" control
    Then the dialog "Store a password" is open
    When I enter "Work email" in "What it is for"
    And I type "s3cret!" into "#v_s"
    And I tap the "#v_ok" control
    Then the board receives {"cmd": "vault_save", "label": "Work email", "type": "password", "secret": "s3cret!"}
    And I see the message "Stored on the board"
    And the vault lists "Work email" as a "Password"

  Scenario: The store form refuses what the board would refuse
    Given the dashboard is open and signed in with "4826"
    And I am on the "Keys" tab
    When I tap the "#btnVaultNew" control
    And I tap the "#v_ok" control
    Then I see the message "Give it a name"
    When I enter "Bank" in "What it is for"
    And I tap the "#v_ok" control
    Then I see the message "Enter the password"
    And the board does not receive "vault_save"

  Scenario: A PIN kind only accepts digits
    Given the dashboard is open and signed in with "4826"
    And I am on the "Keys" tab
    When I tap the "#btnVaultNew" control
    And I tap "PIN"
    And I type "12ab34" into "#v_s"
    Then the field "#v_s" holds "1234"

  Scenario: Typing a stored password asks for the PIN, every time
    Given the vault holds a password "Netflix"
    And the dashboard is open and signed in with "4826"
    When I open the "Keys" tab
    And I tap "Type" on the vault entry "Netflix"
    Then the dialog "Type on the computer" is open
    When I enter "4826" in the PIN row "#vpinRow"
    Then the board receives {"cmd": "vault_type", "pin": "4826"}
    And I see the message "Typed on the computer"
    When I tap "Type" on the vault entry "Netflix"
    Then the dialog "Type on the computer" is open

  Scenario: A wrong PIN does not type, and says why
    Given the vault holds a password "Netflix"
    And the dashboard is open and signed in with "4826"
    When I open the "Keys" tab
    And I tap "Type" on the vault entry "Netflix"
    And I enter "0000" in the PIN row "#vpinRow"
    Then "#vpinMsg" contains "PIN required"
    And the dialog "Type on the computer" is open

  Scenario: Showing a stored password reveals it only after the PIN
    Given the vault holds a password "Netflix"
    And the dashboard is open and signed in with "4826"
    When I open the "Keys" tab
    And I tap the "Show" button on the vault entry "Netflix"
    Then the dialog "Show this password" is open
    When I enter "4826" in the PIN row "#vpinRow"
    Then the dialog "Netflix" is open
    And the field "#vshow" holds the stored secret of "Netflix"
    When I tap the "#vshow" control
    And I press the key "x" on this device
    Then the field "#vshow" holds the stored secret of "Netflix"
    When I tap the "#vshowClose" control
    Then no dialog is open

  Scenario: Renaming keeps the secret without asking for it
    Given the vault holds a password "Netflix"
    And the dashboard is open and signed in with "4826"
    When I open the "Keys" tab
    And I tap the "Edit" button on the vault entry "Netflix"
    Then the dialog "Edit stored secret" is open
    When I enter "Netflix family" in "What it is for"
    And I tap the "#v_ok" control
    Then the board receives a vault save for "Netflix family" without a secret
    And I see the message "Updated"

  Scenario: Deleting asks first, and the answer matters
    Given the vault holds a password "Netflix"
    And the dashboard is open and signed in with "4826"
    When I open the "Keys" tab
    And I tap the "Edit" button on the vault entry "Netflix"
    And I will answer "no" to the next question
    And I tap the "#v_del" control
    Then the board does not receive "vault_delete"
    When I will answer "yes" to the next question
    And I tap the "#v_del" control
    Then the board receives {"cmd": "vault_delete"}
    And I see the message "Deleted"

  Scenario: Giving the vault a PIN of its own
    Given the dashboard is open and signed in with "4826"
    And I am on the "Keys" tab
    When I tap "Add a vault PIN"
    Then the dialog "Add a vault PIN" is open
    When I enter "4826" in the PIN row "#vpCur"
    And I enter "7391" in the PIN row "#vpNew"
    And I tap the "#vp_ok" control
    Then the board receives {"cmd": "vault_pin", "access": "4826", "new_pin": "7391"}
    And I see the message "Vault PIN saved"
    And "#btnVaultPin" reads "Change vault PIN"
    And "#vaultHint" contains "vault PIN"

  Scenario: Changing the vault PIN with the wrong current one says so, and changes nothing
    Given the vault holds a password "Netflix"
    And the vault has its own PIN "7391"
    And the dashboard is open and signed in with "4826"
    When I open the "Keys" tab
    And I tap "Change vault PIN"
    Then the dialog "Change vault PIN" is open
    When I enter "1111" in the PIN row "#vpCur"
    And I enter "2468" in the PIN row "#vpNew"
    And I tap the "#vp_ok" control
    Then "#vp_msg" contains "PIN"
    And the dialog "Change vault PIN" is open
    And the board's vault PIN is still "7391"

  Scenario: A forgotten vault PIN can be reset with the access PIN, erasing the vault
    Given the vault holds a password "Netflix"
    And the vault has its own PIN "7391"
    And the dashboard is open and signed in with "4826"
    When I open the "Keys" tab
    And I tap "Change vault PIN"
    And I will answer "yes" to the next question
    And I tap the "#vp_forgot" control
    Then the dialog "Reset vault PIN" is open
    When I enter "4826" in the PIN row "#vrAcc"
    And I tap the "#vr_ok" control
    Then the board receives {"cmd": "vault_pin", "access": "4826", "new_pin": ""}
    And I see the message "Vault reset"
    And the vault is empty

  Scenario: Erasing the whole vault needs two yeses
    Given the vault holds a password "Netflix"
    And the dashboard is open and signed in with "4826"
    When I open the "Keys" tab
    And I will answer "yes" to the next question
    And I will answer "no" to the next question
    And I tap the "#btnVaultWipe" control
    Then the board does not receive "vault_wipe"
    When I will answer "yes" to the next question
    And I will answer "yes" to the next question
    And I tap the "#btnVaultWipe" control
    Then the board receives {"cmd": "vault_wipe"}
    And I see the message "Vault erased"

  Scenario: The erase button is hidden while the vault is empty
    Given the dashboard is open and signed in with "4826"
    When I open the "Keys" tab
    Then the "#btnVaultWipe" control is hidden

  Scenario Outline: The shortcuts follow the computer, and a tap presses that computer's combo
    Given the board is plugged into a <os> computer
    And the dashboard is open on a <phone>
    When I open the "Keys" tab
    Then "<tab>" is switched on
    When I open the shortcut group "Essentials"
    And I tap the shortcut "Copy"
    Then the board receives {"cmd": "press", "keys": "<copy>"}

    Examples:
      | phone         | os      | tab     | copy   |
      | Android phone | Mac     | macOS   | GUI+C  |
      | Android phone | Windows | Windows | CTRL+C |
      | Android phone | Linux   | Linux   | CTRL+C |
      | iPhone        | Mac     | macOS   | GUI+C  |
      | iPhone        | Linux   | Linux   | CTRL+C |

  Scenario: An iPhone driving a Windows PC shows the Windows shortcuts
    Given the board is plugged into a Windows computer
    And the dashboard is open on a iPhone
    When I open the "Keys" tab
    And I open the shortcut group "Essentials"
    And I tap the shortcut "Copy"
    Then the board receives {"cmd": "press", "keys": "CTRL+C"}

  Scenario: Browsing another OS's shortcuts is remembered on this phone
    Given the board is plugged into a Mac computer
    And the dashboard is open
    When I open the "Keys" tab
    And I tap "Windows"
    Then I see the message "Windows shortcuts"
    When I reload the dashboard
    And I open the "Keys" tab
    Then "Windows" is switched on

  Scenario: Essentials starts open, groups toggle, and stay as they were left
    Given the dashboard is open
    When I open the "Keys" tab
    Then the shortcut group "Essentials" is open
    When I tap the heading of the shortcut group "Essentials"
    Then the shortcut group "Essentials" is closed
    When I reload the dashboard
    And I open the "Keys" tab
    Then the shortcut group "Essentials" is closed
    When I tap the heading of the shortcut group "Essentials"
    Then the shortcut group "Essentials" is open

  Scenario: Search filters on names and on keys, and opens the matching groups
    Given the dashboard is open
    When I open the "Keys" tab
    And I type "paste" into "#scq"
    Then the shortcut "Paste" is visible
    And every visible shortcut matches "paste"
    When I type "zzzz-nothing" into "#scq"
    Then "#scList" contains "Nothing matches that search."

  Scenario: The count says how many shortcuts the chosen OS has
    Given the dashboard is open
    When I open the "Keys" tab
    Then "#scNote" contains "macOS shortcut"

  Scenario: After a reconnect, a PIN-protected board's vault is still listed
    Given the vault holds a password "Netflix"
    And the dashboard is open and signed in with "4826"
    When I reload the dashboard
    And I open the "Keys" tab
    Then the vault lists "Netflix" as a "Password"

  # ---- when the board says no ------------------------------------------------------

  Scenario: A store the board refuses keeps the form open and shows why
    Given the board will refuse the next "vault_save" with "The vault is full"
    And the dashboard is open and signed in with "4826"
    And I am on the "Keys" tab
    When I tap the "#btnVaultNew" control
    And I enter "Bank" in "What it is for"
    And I type "s3cret!" into "#v_s"
    And I tap the "#v_ok" control
    Then "#v_msg" contains "The vault is full"
    And the dialog "Store a password" is open

  Scenario: A delete the board refuses says so
    Given the vault holds a password "Netflix"
    And the board will refuse the next "vault_delete" with "Slot is busy"
    And the dashboard is open and signed in with "4826"
    When I open the "Keys" tab
    And I tap the "Edit" button on the vault entry "Netflix"
    And I will answer "yes" to the next question
    And I tap the "#v_del" control
    Then "#v_msg" contains "Slot is busy"
    And the vault lists "Netflix" as a "Password"

  Scenario: Resetting the vault PIN with the wrong access PIN erases nothing
    Given the vault holds a password "Netflix"
    And the vault has its own PIN "7391"
    And the dashboard is open and signed in with "4826"
    When I open the "Keys" tab
    And I tap "Change vault PIN"
    And I will answer "yes" to the next question
    And I tap the "#vp_forgot" control
    And I enter "1111" in the PIN row "#vrAcc"
    And I tap the "#vr_ok" control
    Then "#vr_msg" contains "PIN"
    And the board still holds 1 vault entry

  # ---- open an app ----------------------------------------------------------------------

  Scenario Outline: Open an app follows the computer and goes through its own search
    Given the board is plugged into a <os> computer
    And the dashboard is open
    When I open the "Keys" tab
    Then "#appHint" contains "<via>"
    When I tap the app "<app>"
    Then the board receives {"cmd": "open", "app": "<app>"}
    And I see the message "Opening <app>"

    Examples:
      | os      | via                     | app         |
      | Mac     | Spotlight               | TextEdit    |
      | Windows | Start                   | Notepad     |
      | Linux   | the Activities overview | Text Editor |

  Scenario: Any app, by typing its name and pressing Enter
    Given the dashboard is open
    When I open the "Keys" tab
    And I type "Visual Studio Code" into the app name and press Enter
    Then the board receives {"cmd": "open", "app": "Visual Studio Code"}

  Scenario: Open with no name sends nothing, and says what to do
    Given the dashboard is open
    When I open the "Keys" tab
    And I tap the "#btnOpenApp" control
    Then I see the message "Type the app's name"
    And the board does not receive "open"

  Scenario: A board that cannot open it says why
    Given the board is plugged into a Windows computer
    And the board will refuse the next "open" with "USB HID not initialized"
    And the dashboard is open
    When I open the "Keys" tab
    And I tap the app "Notepad"
    Then I see the message "USB HID not initialized"
