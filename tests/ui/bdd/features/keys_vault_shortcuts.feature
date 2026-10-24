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

  Scenario Outline: Four digits typed the instant a PIN box is tapped each land in their own box
    Given the dashboard is open and signed in with "4826"
    And I am on the "Keys" tab
    When I tap "Add a vault PIN"
    And I tap the first box of "#<row>" and type "<digits>" at once
    Then the boxes of "#<row>" read "<digits>"
    And the focus stays put

    Examples:
      | row   | digits |
      | vpCur | 4826   |
      | vpNew | 7391   |
      | vpCur | 1111   |

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


  # ---- text shortcuts --------------------------------------------------------------
  # Seen live, before a booking site that refuses paste and Tab: one tile per
  # value - click the field on the computer, tap the tile, the board types it.
  # They live on the Keys tab, where a person looks to type faster.
  Scenario: A new text shortcut types its text, from the Keys tab
    Given the dashboard is open and signed in with "4826"
    And I am on the "Keys" tab
    When I tap the "#btnSnipNew" control
    Then the dialog "New text shortcut" is open
    When I enter "Person 1 ID" in "Label"
    And I enter "123412341234" in "Text it types"
    And I tap the "#s_ok" control
    Then I see the message "Saved to board"
    When I tap the text shortcut "Person 1 ID"
    Then the board receives {"cmd": "type", "text": "123412341234"}

  Scenario: Text shortcuts are on the Keys tab, not among the media quick actions
    Given the board has 3 text shortcuts
    And the dashboard is open and signed in with "4826"
    When I open the "Keys" tab
    Then there are 3 text shortcuts
    When I open the "Media" tab
    Then no quick action types text

  # The live case: 24 shortcuts (name, age, ID for eight people) make a layout of
  # about 1.9 KB - over the old 2 KB message limit once escaped, so every delete
  # from the phone was refused and the tile came back.
  Scenario: With 24 shortcuts, Edit then Delete removes one, and it stays gone
    Given the board has 24 text shortcuts
    And the dashboard is open and signed in with "4826"
    And I am on the "Keys" tab
    When I tap the "#btnSnipEdit" control
    And I tap the text shortcut "8 ID"
    Then the dialog "Edit text shortcut" is open
    And the board does not receive "type"
    When I tap "Delete"
    Then I see the message "Saved to board"
    When I reload the dashboard
    And I open the "Keys" tab
    Then there are 23 text shortcuts
    And the text shortcut "8 ID" is gone

  # A finger resting on a tile to scroll must not open anything: editing is the
  # Edit button only (the hold-to-edit that was here got in people's way).
  Scenario Outline: Holding a finger on a text shortcut never opens an editor
    Given the board has 24 text shortcuts
    And the dashboard is open and signed in with "4826"
    And I am on the "Keys" tab
    When I hold the text shortcut "<tile>" for <ms> ms
    Then no dialog is open

    Examples:
      | tile   | ms   |
      | 1 Name | 700  |
      | 8 ID   | 2500 |

  Scenario: With the board's storage nearly full, a new text shortcut is refused and the tiles stay as they were
    Given the board has 3 text shortcuts
    And the dashboard is open and signed in with "4826"
    And I am on the "Keys" tab
    And the board's storage is nearly full
    When I tap the "#btnSnipNew" control
    And I enter "Person 9 ID" in "Label"
    And I enter "999999999999" in "Text it types"
    And I tap the "#s_ok" control
    Then I see the message "Not saved: The board's storage is nearly full - delete a macro or a text shortcut first"
    And there are 3 text shortcuts

  Scenario: A save the board refuses says why and shows what the board has
    Given the board has 3 text shortcuts
    And the dashboard is open and signed in with "4826"
    And I am on the "Keys" tab
    And the board will refuse the next "ui_save" with "layout too large"
    When I tap the "#btnSnipEdit" control
    And I tap the text shortcut "1 Name"
    And I tap "Delete"
    Then I see the message "Not saved: layout too large"
    And there are 3 text shortcuts

  Scenario: A shortcut with nothing to type is refused before it reaches the board
    Given the dashboard is open and signed in with "4826"
    And I am on the "Keys" tab
    When I tap the "#btnSnipNew" control
    And I tap the "#s_ok" control
    Then I see the message "Nothing to type"
    And the board does not receive "ui_save"

  # Seen while fixing deletes: on a PIN-locked board the layout asked for at
  # connect was refused, and signing in did not ask again - the dashboard showed
  # the default tiles, and an edit saved them over the board's real layout.
  Scenario: Signing in shows the board's own layout, and an edit keeps the rest of it
    Given the board has 24 text shortcuts
    And the dashboard is open and signed in with "4826"
    When I open the "Keys" tab
    Then there are 24 text shortcuts
    When I tap the "#btnSnipEdit" control
    And I tap the text shortcut "1 Name"
    And I tap "Delete"
    Then I see the message "Saved to board"
    And the board's layout still has 23 text shortcuts
