Feature: The Settings tab
  Everything about the board itself: which computer it is plugged into, the
  one-off Mac and emoji setup, the home network and its own access point, the
  access PIN, pointer and power modes, and what the board can report about itself.

  # ---- Computer ------------------------------------------------------------------

  Scenario: The detected computer is named, with the evidence behind it
    Given the board is plugged into a Windows computer
    And the dashboard is open
    When I open the "Settings" tab
    Then "#osName" reads "Windows"
    And "Auto" is switched on
    And "#osDiag" contains "Signals seen"
    And "#osDiag" contains "windows"

  Scenario: Pinning the computer by hand, and handing back to detection
    Given the board is plugged into a Windows computer
    And the dashboard is open
    When I open the "Settings" tab
    And I tap "macOS"
    Then the board receives {"cmd": "set_host_os", "os": "mac"}
    And I see the message "macOS"
    And "#osName" reads "macOS"
    And "macOS" is switched on
    When I open the "Keys" tab
    Then "macOS" is switched on
    When I open the "Settings" tab
    And I tap "Auto"
    Then the board receives {"cmd": "set_host_os", "os": "auto"}
    And I see the message "Detecting the computer"
    And "#osName" reads "Windows"

  # ---- Mac setup -----------------------------------------------------------------

  Scenario Outline: The Keyboard Setup Assistant keys
    Given the dashboard is open
    When I open the "Settings" tab
    And I tap the "#<button>" control
    Then the board receives {"cmd": "press", "keys": "<key>"}
    And I see the message "<toast>"

    Examples:
      | button     | key | toast  |
      | btnIdZ     | z   | Sent Z |
      | btnIdSlash | /   | Sent / |

  # ---- Emoji and symbols ---------------------------------------------------------

  Scenario: Turning Unicode entry on and off by hand
    Given the board is plugged into a Mac computer
    And the dashboard is open
    When I open the "Settings" tab
    And I tap "macOS is set up"
    Then the board receives {"cmd": "set_unicode", "os": "mac", "on": true}
    And I see the message "Emoji and symbols on for macOS"
    And "macOS is set up" is switched on
    When I tap "macOS is set up"
    Then the board receives {"cmd": "set_unicode", "os": "mac", "on": false}
    And "macOS is set up" is switched off

  Scenario: The test types a sample, and only a yes turns Unicode on
    Given the board is plugged into a Mac computer
    And the dashboard is open
    When I open the "Settings" tab
    And I will answer "yes" to the next question
    And I will answer "yes" to the next question
    And I tap "Test on macOS"
    Then the board receives {"cmd": "unicode_test", "os": "mac"}
    And the board receives {"cmd": "set_unicode", "os": "mac", "on": true}
    And a confirmation asking "Click into a text box" was shown
    And a confirmation asking "Did exactly" was shown
    And "macOS is set up" is switched on

  Scenario: Saying no after the test leaves Unicode off and says what to do
    Given the board is plugged into a Linux computer
    And the dashboard is open
    When I open the "Settings" tab
    And I will answer "yes" to the next question
    And I will answer "no" to the next question
    And I tap "Test on Linux"
    Then the board receives {"cmd": "unicode_test", "os": "linux"}
    And I see the message "Linux is not set up yet"
    And the board does not receive "set_unicode"

  Scenario: Cancelling before the test types nothing
    Given the dashboard is open
    When I open the "Settings" tab
    And I will answer "no" to the next question
    And I tap "Test on macOS"
    Then the board does not receive "unicode_test"

  Scenario: The macOS test is refused on a PC, so it never presses Alt shortcuts there
    Given the board is plugged into a Windows computer
    And the dashboard is open
    When I open the "Settings" tab
    And I will answer "yes" to the next question
    And I tap "Test on macOS"
    Then I see the message "This computer looks like a PC"
    And the board does not receive "set_unicode"

  # ---- Home network -------------------------------------------------------------

  Scenario: Scanning lists the networks the board can see
    Given the dashboard is open
    When I open the "Settings" tab
    And I tap "Scan networks"
    Then "#wifiList" contains "HomeNet"
    And "#wifiList" contains "Cafe Guest"
    And "#wifiList" contains "2 networks"
    When I tap "Scan again"
    Then "#wifiList" contains "HomeNet"

  Scenario: Joining a network shows the join as it happens
    Given the dashboard is open
    When I open the "Settings" tab
    And I tap "Scan networks"
    And I pick the network "HomeNet"
    Then the dialog "Join HomeNet" is open
    And "#sheetin" contains "You are not on the board's access point"
    When I type "correct horse" into "#w_p"
    And I tap the "#w_eye" control
    Then the field "#w_p" shows its text
    When I tap "Connect"
    Then the board receives {"cmd": "wifi_configure", "ssid": "HomeNet", "password": "correct horse"}
    And "#joinTitle" contains "Joining HomeNet"
    And I see the message "Connected as 192.168.1.77"
    And "#joinMsg" contains "Address assigned by the router"

  Scenario: A join that fails says so, and the access point stays up
    Given the dashboard is open
    When I open the "Settings" tab
    And I tap "Scan networks"
    And I pick the network "Cafe Guest"
    And I tap "Connect"
    Then I see the message "Join failed"
    And "#joinTitle" reads "Could not join"
    And "#joinMsg" contains "The access point is unaffected"

  Scenario: A fixed IP needs an address and a gateway
    Given the dashboard is open
    When I open the "Settings" tab
    And I tap the "#tStatic" control
    Then "#staticFields" is visible
    When I tap "Scan networks"
    And I pick the network "HomeNet"
    Then "#sheetin" contains "Requesting the fixed address"
    When I tap "Connect"
    Then I see the message "A fixed IP needs an address and a gateway"
    And the board does not receive "wifi_configure"

  Scenario: Joining with a fixed IP asks the router for exactly that address
    Given the dashboard is open
    When I open the "Settings" tab
    And I tap the "#tStatic" control
    And I type "192.168.1.50" into "#stIp"
    And I type "192.168.1.1" into "#stGw"
    And I type "255.255.255.0" into "#stMask"
    And I tap "Scan networks"
    And I pick the network "HomeNet"
    And I tap "Connect"
    Then the board receives {"cmd": "wifi_configure", "ssid": "HomeNet", "ip": "192.168.1.50", "gateway": "192.168.1.1", "subnet": "255.255.255.0"}
    And I see the message "Connected as 192.168.1.50"

  Scenario: Scanning while a join is under way explains the wait
    Given the board is joining "HomeNet"
    And the dashboard is open
    When I open the "Settings" tab
    And I tap "Scan networks"
    Then "#wifiList" contains "Still joining a network"

  Scenario: Forgetting the home network asks first
    Given the dashboard is open
    When I open the "Settings" tab
    And I will answer "no" to the next question
    And I tap the "#btnWifiReset" control
    Then the board does not receive "wifi_disconnect"
    When I will answer "yes" to the next question
    And I tap the "#btnWifiReset" control
    Then the board receives {"cmd": "wifi_disconnect"}

  Scenario: A board on the home network without a PIN warns about it
    Given the board is on the home network
    And the board has no access PIN
    And the dashboard is open
    When I open the "Settings" tab
    Then "#lanWarn" is visible

  Scenario: With a PIN, the home-network warning is gone
    Given the board is on the home network
    And the dashboard is open and signed in with "4826"
    When I open the "Settings" tab
    Then "#lanWarn" is hidden

  # ---- Access point -------------------------------------------------------------

  Scenario: The network name counts bytes, not letters
    Given the dashboard is open
    When I open the "Settings" tab
    And I replace "#apSsid" with "Café 😀"
    Then "#apBytes" reads "10 / 32 bytes"

  Scenario Outline: The access point form refuses what a network cannot carry
    Given the dashboard is open
    When I open the "Settings" tab
    And I replace "#apSsid" with "<name>"
    And I type "<password>" into "#apPass"
    And I tap the "#btnAp" control
    Then I see the message "<message>"
    And the board does not receive "ap_configure"

    Examples:
      | name                                  | password | message                                    |
      |                                       |          | A network name is required                 |
      | ééééééééééééééééé                     |          | Name is 34 bytes, the limit is 32          |
      | Desk                                  | short    | Password needs 8 characters or more        |

  Scenario: Changing the access point warns, then guides the phone back on
    Given the dashboard is open
    When I open the "Settings" tab
    And I replace "#apSsid" with "Desk-HIDFi"
    And I type "longpassword" into "#apPass"
    And I will answer "yes" to the next question
    And I tap the "#btnAp" control
    Then a confirmation asking "This phone will drop off the network" was shown
    And the board receives {"cmd": "ap_configure", "ssid": "Desk-HIDFi", "password": "longpassword", "open": false}
    And "#apnewInfo" contains "Desk-HIDFi"
    And "#apnewInfo" contains "longpassword"
    And the dashboard reconnects
    And I see the message "Reconnected"
    And "#apnew" is hidden
    And the field "#apPass" is empty

  Scenario: An open access point asks twice, and a no at either step changes nothing
    Given the dashboard is open
    When I open the "Settings" tab
    And I tap the "#tApOpen" control
    Then the field "#apPass" is disabled
    When I will answer "yes" to the next question
    And I will answer "no" to the next question
    And I tap the "#btnAp" control
    Then a confirmation asking "Remove the password" was shown
    And the board does not receive "ap_configure"
    And "#apnew" is hidden

  # ---- Access PIN ---------------------------------------------------------------

  Scenario: Setting a PIN
    Given the board has no access PIN
    And the dashboard is open
    When I open the "Settings" tab
    And I enter the board's WiFi password "k7m2-x9qp-4tre"
    Then "#authState" reads "Off"
    When I type the new PIN "1357"
    And I tap the "#btnPin" control
    Then the board receives {"cmd": "set_auth", "token": "1357"}
    And I see the message "PIN set"
    And "#authState" reads "On"
    And the new PIN boxes are empty

  Scenario: A PIN needs all four digits
    Given the dashboard is open
    When I open the "Settings" tab
    And I type the new PIN "13"
    And I tap the "#btnPin" control
    Then I see the message "Enter all four digits"
    And the board does not receive "set_auth"

  Scenario: Changing the PIN proves the old one, and keeps the vault
    Given the vault holds a password "Netflix"
    And the dashboard is open and signed in with "4826"
    When I open the "Settings" tab
    And I type the new PIN "1357"
    And I tap the "#btnPin" control
    Then the board receives {"cmd": "set_auth", "token": "1357", "old": "4826"}
    And I see the message "PIN set"
    And the board still holds 1 vault entry

  Scenario: Turning the PIN off warns about the vault, and a no keeps it
    Given the vault holds a password "Netflix"
    And the dashboard is open and signed in with "4826"
    When I open the "Settings" tab
    And I will answer "yes" to the next question
    And I will answer "no" to the next question
    And I tap the "#btnPinOff" control
    Then a confirmation asking "1 stored password will also be erased" was shown
    And the board does not receive "set_auth"

  Scenario: Turning the PIN off
    Given the dashboard is open and signed in with "4826"
    When I open the "Settings" tab
    And I will answer "yes" to the next question
    And I tap the "#btnPinOff" control
    Then the board receives {"cmd": "set_auth", "token": ""}
    And I see the message "PIN turned off"
    And "#authState" reads "Off"

  # ---- Pointer mode -------------------------------------------------------------

  Scenario: Switching the pointer mode reboots the board, and asks first
    Given the dashboard is open
    When I open the "Settings" tab
    Then "#ptrMode" reads "relative"
    When I will answer "no" to the next question
    And I tap "Absolute"
    Then the board does not receive "pointer_mode"
    When I will answer "yes" to the next question
    And I tap "Absolute"
    Then the board receives {"cmd": "pointer_mode", "mode": "absolute", "reboot": true}
    And the reboot overlay says "Switching the pointer to absolute. Windows will install the new device."
    And the dashboard reconnects
    And I see the message "Board is back"
    And "#ptrMode" reads "absolute"

  # ---- Power --------------------------------------------------------------------

  Scenario Outline: Power modes
    Given the dashboard is open
    When I open the "Settings" tab
    And I tap the power mode "<mode>"
    Then the board receives {"cmd": "power_mode", "mode": "<mode>"}
    And I see the message "Power: <mode>"
    And "#pwrState" reads "<name>"

    Examples:
      | mode        | name        |
      | performance | Performance |
      | balanced    | Balanced    |

  Scenario: Saver warns that the pointer will stutter
    Given the dashboard is open
    When I open the "Settings" tab
    And I will answer "no" to the next question
    And I tap the power mode "saver"
    Then the board does not receive "power_mode"
    When I will answer "yes" to the next question
    And I tap the power mode "saver"
    Then the board receives {"cmd": "power_mode", "mode": "saver"}
    And "#pwrState" reads "Saver"
    And "#pwrSub" contains "Radio sleeping"

  Scenario: Switching the access point off automatically asks first, switching it back does not
    Given the dashboard is open
    When I open the "Settings" tab
    And I will answer "yes" to the next question
    And I tap the "#tApOff" control
    Then a confirmation asking "Hold BOOT for two seconds" was shown
    And the board receives {"cmd": "power_mode", "ap_auto_off": true}
    And "#tApOff" is on
    When I tap the "#tApOff" control
    Then the board receives {"cmd": "power_mode", "ap_auto_off": false}
    And "#tApOff" is off
    And 1 confirmation was shown

  # ---- Device, clients, storage, tools ---------------------------------------------

  Scenario: The device card and the footer report the board
    Given the dashboard is open
    When I open the "Settings" tab
    Then "#devInfo" contains "hid_fi_v3.10"
    And "#devInfo" contains "Free heap"
    And "#devInfo" contains "PSRAM"
    And "#colVer" contains "v3.10"
    And "#netInfo" contains "192.168.4.1"

  Scenario: Connected clients
    Given the dashboard is open
    When I open the "Settings" tab
    And I tap the "#btnClients" control
    Then "#clientList" contains "192.168.4.2"
    And "#clientList" contains "dashboard open"
    And "#clientList" contains "Device names are not shown"

  Scenario: No clients on the access point is explained
    Given nobody is on the board's access point
    And the dashboard is open
    When I open the "Settings" tab
    And I tap the "#btnClients" control
    Then "#clientList" contains "Nothing is connected to the board"

  Scenario: What is stored on the board, without a single secret
    Given the dashboard is open and signed in with "4826"
    When I open the "Settings" tab
    And I tap the "#btnStorage" control
    Then "#storeInfo" contains "172 of 630"
    And "#storeInfo" contains "Access PIN"
    And "#storeInfo" contains "Pointer mode"
    And "#storeInfo" does not contain "4826"

  Scenario: Test HID and the activity log
    Given the dashboard is open
    When I open the "Settings" tab
    And I tap the "#btnTestHid" control
    Then the board receives {"cmd": "test_hid"}
    And "#log" contains "ok test_hid"

  # ---- reached over the home network ---------------------------------------------

  Scenario: Over the home network without a PIN, commands are refused, explained once, then briefly
    Given the board is on the home network
    And the board has no access PIN
    And this phone reaches the board over the home network
    When I open the dashboard on the home network
    Then the dialog "A PIN is needed on this network" is open
    When I tap "Take me to Settings"
    Then the "Settings" tab is shown
    When I open the "Media" tab
    And I tap "Next"
    Then I see the message "Set a PIN to use this over your network"

  Scenario: Over the home network without a PIN, the trackpad cannot move the pointer either
    Given the board is on the home network
    And the board has no access PIN
    And this phone reaches the board over the home network
    When I open the dashboard on the home network
    And I close the dialog
    And I drag one finger across the trackpad
    Then the board refused the pointer

  Scenario: Scanning over the home network without a PIN says a PIN is needed
    Given the board is on the home network
    And the board has no access PIN
    And this phone reaches the board over the home network
    When I open the dashboard on the home network
    And I close the dialog
    And I open the "Settings" tab
    And I tap "Scan networks"
    Then "#wifiList" contains "PIN"

  Scenario: Over the home network with a PIN, scanning works
    Given the board is on the home network
    And this phone reaches the board over the home network
    And the dashboard is open and signed in with "4826"
    When I open the "Settings" tab
    And I tap "Scan networks"
    Then "#wifiList" contains "HomeNet"

  Scenario: The fixed IP form carries an optional DNS server
    Given the dashboard is open
    When I open the "Settings" tab
    And I tap the "#tStatic" control
    And I type "192.168.1.50" into "#stIp"
    And I type "192.168.1.1" into "#stGw"
    And I type "1.1.1.1" into "#stDns"
    And I tap "Scan networks"
    And I pick the network "HomeNet"
    And I tap "Connect"
    Then the board receives {"cmd": "wifi_configure", "ssid": "HomeNet", "dns": "1.1.1.1"}

  Scenario: If the phone does not find its way back after an AP change, Reload is offered
    Given the board will not take the phone back after an AP change
    And the dashboard is open
    When I open the "Settings" tab
    And I replace "#apSsid" with "Desk-HIDFi"
    And I will answer "yes" to the next question
    And I tap the "#btnAp" control
    Then "#apnew" is visible
    And "#apnewTry" contains "attempt"
    When the board takes connections again
    And I tap the "#apnewReload" control
    Then the dashboard reconnects
    And "#apnew" is hidden

  Scenario: A join the board rejects outright says why
    Given the board will refuse the next "wifi_configure" with "That network name is not valid"
    And the dashboard is open
    When I open the "Settings" tab
    And I tap "Scan networks"
    And I pick the network "HomeNet"
    And I tap "Connect"
    Then I see the message "That network name is not valid"

  Scenario: On the board's own access point, the join sheet says the password stays private
    Given this phone is on the board's own access point
    And the dashboard is open
    When I open the "Settings" tab
    And I tap "Scan networks"
    And I pick the network "HomeNet"
    Then "#sheetin" contains "WPA2 covers this password the whole way"

  Scenario: Forgetting the network in the middle of a join takes the join card away
    Given the dashboard is open
    When I open the "Settings" tab
    And I tap "Scan networks"
    And I pick the network "HomeNet"
    And I tap "Connect"
    Then "#joinBox" is visible
    When the board forgets the network before it joins
    Then "#joinBox" is hidden

  # ---- the four PIN boxes -----------------------------------------------------------

  Scenario: Backspace in an empty box goes back and clears the one before
    Given the board has no access PIN
    And the dashboard is open
    When I open the "Settings" tab
    And I enter the board's WiFi password "k7m2-x9qp-4tre"
    And I type the new PIN "13"
    And I press "Backspace" in the PIN boxes
    And I type the rest of the PIN "579"
    And I tap the "#btnPin" control
    Then the board receives {"cmd": "set_auth", "token": "1579"}

  Scenario: Letters never reach a PIN box
    Given the board has no access PIN
    And the dashboard is open
    When I open the "Settings" tab
    And I enter the board's WiFi password "k7m2-x9qp-4tre"
    And I type the new PIN "1a2b3c4"
    And I tap the "#btnPin" control
    Then the board receives {"cmd": "set_auth", "token": "1234"}

  Scenario: Pasting a PIN fills all four boxes, ignoring anything that is not a digit
    Given the board has no access PIN
    And the dashboard is open
    When I open the "Settings" tab
    And I enter the board's WiFi password "k7m2-x9qp-4tre"
    And I paste "PIN: 24-68" into the PIN boxes
    Then the PIN boxes hold "2468"
    When I tap the "#btnPin" control
    Then the board receives {"cmd": "set_auth", "token": "2468"}

  Scenario: The arrow keys move between PIN boxes, and a digit typed over a full box replaces it
    Given the board has no access PIN
    And the dashboard is open
    When I open the "Settings" tab
    And I type the new PIN "1234"
    And I press "ArrowLeft" in the PIN boxes
    And I press "ArrowLeft" in the PIN boxes
    And I type the rest of the PIN "9"
    Then the PIN boxes hold "1934"

  Scenario: An access point change the board refuses says why
    Given the board will refuse the next "ap_configure" with "AP password must be at least 8 characters"
    And the dashboard is open
    When I open the "Settings" tab
    And I replace "#apSsid" with "Desk-HIDFi"
    And I will answer "yes" to the next question
    And I tap the "#btnAp" control
    Then I see the message "AP password must be at least 8 characters"
