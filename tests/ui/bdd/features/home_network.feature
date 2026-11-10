Feature: When the home network has a bad night
  Routers restart, drop the board, and sometimes give it a new address. The board
  rejoins by itself and keeps its own WiFi up meanwhile; the dashboard says what
  is going on, says when the address changed, and - when it can no longer reach
  the board at all - says how to find it. The same cases run on a real board in
  tests/board/test_home_network.py.

  Background:
    Given the board is on the home network at "192.168.1.107"

  Scenario: While the router is away, Settings says it is reconnecting and since when
    Given the dashboard is open
    When the home network drops the board
    And I open the "Settings" tab
    Then "#netInfo" contains "reconnecting to HomeNet"
    And "#netInfo" contains "BEACON_TIMEOUT"
    And "#netInfo" contains "Last address"
    And "#netInfo" contains "192.168.1.107"

  Scenario: Back at the same address: nothing to tell, and the drop is counted
    Given the dashboard is open
    When the home network drops the board
    And the board is back on the home network at "192.168.1.107"
    And I open the "Settings" tab
    Then "#netInfo" contains "Dropped since start"
    And "#netInfo" does not contain "Address before"
    And I never see the message "address changed"

  Scenario: Back at a new address: the dashboard says so, once, and Settings shows both
    Given the dashboard is open
    And this phone has seen the board at "192.168.1.107"
    When the home network drops the board
    And the board is back on the home network at "192.168.1.100"
    Then I see the message "address changed to 192.168.1.100"
    When I open the "Settings" tab
    Then "#netInfo" contains "Address before"
    And "#netInfo" contains "192.168.1.107"
    And "#netInfo" contains "esp-hid-da1a2c.local"

  Scenario: Settings says which address to bookmark
    Given the dashboard is open
    When I open the "Settings" tab
    Then "#netInfo" contains "http://esp-hid-da1a2c.local/"

  Scenario: Opened at an address that stopped answering, the page says how to find the board
    Given the dashboard is open
    When the board becomes unreachable
    And the board has not answered for a minute
    Then the dialog "Cannot reach the board" is open
    And "#lostByName" links to "http://esp-hid-da1a2c.local/"
    And "#sheetin" contains "tools/hidfi.py find"

  Scenario: On the board's own WiFi there is nothing to look for, so no such advice
    Given this phone is on the board's own WiFi
    And the dashboard is open
    When the board becomes unreachable
    And the board has not answered for a minute
    Then no dialog is open

  Scenario: When the board comes back the advice is not shown again for the same outage
    Given the dashboard is open
    When the board becomes unreachable
    And the board has not answered for a minute
    And I close the dialog
    And the board takes connections again
    Then the dashboard reconnects
    And no dialog is open

  # ---- sharing the home internet on the board's own WiFi --------------------------

  Scenario: Sharing the home internet warns first, and a no changes nothing
    Given the dashboard is open
    And I will answer "no" to the next question
    When I open the "Settings" tab
    And I tap the "#tShare" control
    Then a confirmation asking "can then reach your home network" was shown
    And the board does not receive "internet_share"

  Scenario: Sharing switched on, and it says it is working
    Given the dashboard is open
    And I will answer "yes" to the next question
    When I open the "Settings" tab
    And I tap the "#tShare" control
    Then the board receives {"cmd": "internet_share", "on": true}
    And I see the message "Sharing the home internet"
    And "#shareHint" contains "have internet"

  Scenario: While the home network is away, sharing waits for it
    Given the dashboard is open
    And I will answer "yes" to the next question
    When the home network drops the board
    And I open the "Settings" tab
    And I tap the "#tShare" control
    Then "#shareHint" contains "Waiting for the home network"

  Scenario: Without an access PIN, sharing cannot be switched on over WiFi
    Given the board's own WiFi needs no PIN
    And this phone is on the board's own WiFi
    And the dashboard is open
    And I will answer "yes" to the next question
    When I open the "Settings" tab
    And I tap the "#tShare" control
    Then I see the message "Set an access PIN first"

  # ---- saved networks, like a phone (v3.11) ------------------------------------------

  Scenario: Settings lists the saved networks, which one is connected, and which have worked
    Given the board has saved the networks "Bangalore, HomeNet"
    And the dashboard is open
    When I open the "Settings" tab
    Then "#savedNets" contains "HomeNet"
    And "#savedNets" contains "Bangalore"
    And "#savedNets" contains "connected"
    And "#savedNets" contains "not joined yet"

  Scenario: Forgetting a network asks first, and a no keeps it
    Given the board has saved the networks "Bangalore, HomeNet"
    And the dashboard is open
    And I will answer "no" to the next question
    When I open the "Settings" tab
    And I forget the saved network "Bangalore"
    Then a confirmation asking "will not join it again" was shown
    And the board does not receive "wifi_forget"

  # ---- the network your devices use (docs/DECISIONS.md D-02) ------------------------

  Scenario: Signing in over the home network marks it as where the board is used
    Given the board has saved the networks "Bangalore, HomeNet"
    And this phone reaches the board over the home network
    And the dashboard is opened by the board's name
    When I open the "Settings" tab
    Then "#savedNets" contains "you have used the board over this one"
    And "#savedNets" contains "preferred"
    And the board prefers "HomeNet"

  Scenario: The network you use is marked, and another can be preferred
    Given the board has saved the networks "Bangalore, HomeNet"
    And the board is used over "HomeNet"
    And the dashboard is open
    And I will answer "no" to the next question
    When I open the "Settings" tab
    And I prefer the saved network "Bangalore"
    Then a confirmation asking "moves there by itself when nobody is using it" was shown
    And the board receives {"cmd": "wifi_prefer", "ssid": "Bangalore", "now": false}
    And I see the message "the board moves there when nobody is using it here"
    And the board prefers "Bangalore"

  Scenario: Preferring a network and moving now
    Given the board has saved the networks "Bangalore, HomeNet"
    And the board is used over "HomeNet"
    And the dashboard is open
    And I will answer "yes" to the next question
    When I open the "Settings" tab
    And I prefer the saved network "Bangalore"
    Then the board receives {"cmd": "wifi_prefer", "ssid": "Bangalore", "now": true}
    And I see the message "Moving to Bangalore"

  Scenario: The paired computer's network says so
    Given the board has saved the networks "Bangalore, HomeNet"
    And the paired computer uses "Bangalore"
    And the dashboard is open
    When I open the "Settings" tab
    Then "#savedNets" contains "your paired computer uses this one"
    And the board prefers "Bangalore"

  Scenario: A board nobody uses on one network tries the next, and says so
    Given the board has saved the networks "Bangalore, HomeNet"
    And in the air "HomeNet" is strong and "Bangalore" is weaker
    And the dashboard is open
    When nobody uses the board on "HomeNet" for 5 minutes
    Then I see the message "Nobody used the board on HomeNet - trying Bangalore"

  Scenario: Back where it is used, it says so
    Given the board has saved the networks "Bangalore, HomeNet"
    And the board is used over "Bangalore"
    And in the air "HomeNet" is strong and "Bangalore" is weaker
    And the dashboard is open
    When the board looks at whether to move
    Then I see the message "Moving to the network it is used over: Bangalore"

  # ---- devices on another network (docs/DECISIONS.md D-10) ---------------------------

  Scenario: Opened by address from another network, the page says so and the visit does not count
    Given the board has saved the networks "Bangalore, HomeNet"
    And this phone reaches the board over the home network
    And the board's name does not reach this device
    And the dashboard is opened by the board's address
    Then "#netAway" is visible
    And "#netAway" contains "esp-hid-da1a2c.local"
    And the board prefers no network yet

  Scenario: Opened by address right on the board's network, the page checks the name and counts
    Given the board has saved the networks "Bangalore, HomeNet"
    And this phone reaches the board over the home network
    And the dashboard is opened by the board's address
    Then "#netAway" is hidden
    And the board receives {"cmd": "net_here"}
    And the board prefers "HomeNet"

  Scenario: Bring the board to this device's WiFi - pick it, and the board moves
    Given the board has saved the networks "Bangalore, HomeNet"
    And this phone reaches the board over the home network
    And the board's name does not reach this device
    And the dashboard is opened by the board's address
    When I tap the "#netBring" control
    Then the dialog "Which WiFi is this device on?" is open
    When I pick "Bangalore" as this device's WiFi
    Then the board receives {"cmd": "wifi_prefer", "ssid": "Bangalore", "now": true, "force": false}
    And I see the message "Moving the board to Bangalore"
    And "#netAway" is hidden

  Scenario: Someone using the board on its network is asked about before it moves
    Given the board has saved the networks "Bangalore, HomeNet"
    And this phone reaches the board over the home network
    And the board's name does not reach this device
    And someone else is using the board right on its network
    And the dashboard is opened by the board's address
    And I will answer "no" to the next question
    When I tap the "#netBring" control
    And I pick "Bangalore" as this device's WiFi
    Then a confirmation asking "cuts them off" was shown
    And the board does not receive "force"
    And the board prefers no network yet

  Scenario: The board's own WiFi is offered, with the same address
    Given the board has saved the networks "Bangalore, HomeNet"
    And this phone reaches the board over the home network
    And the board's name does not reach this device
    And the dashboard is opened by the board's address
    When I tap the "#netOwn" control
    Then "#sheetin" contains "esp-hid-da1a2c.local"
    And "#sheetin" contains "ESP32-HID"

  Scenario: A paired computer behind another router does not anchor the board
    Given the board has saved the networks "Bangalore, HomeNet"
    And the paired computer reaches the board from another network
    Then the board prefers no network yet
    And the board says its computer is on another network

  Scenario: A saved network whose password changed says so, and the board stays where it works
    Given the board has saved the networks "Bangalore, HomeNet"
    And the board is used over "HomeNet"
    And the router for "HomeNet" no longer takes its saved password
    And the dashboard is open
    When I open the "Settings" tab
    Then "#savedNets" contains "the router no longer takes the saved password"
    And the board stays on "Bangalore" when it looks at whether to move

  Scenario: Forgetting a network removes only that one
    Given the board has saved the networks "Bangalore, HomeNet"
    And the dashboard is open
    And I will answer "yes" to the next question
    When I open the "Settings" tab
    And I forget the saved network "Bangalore"
    Then the board receives {"cmd": "wifi_forget", "ssid": "Bangalore"}
    And I see the message "Forgot Bangalore"
    And "#savedNets" contains "HomeNet"
    And "#savedNets" does not contain "Bangalore"

  Scenario: Forgetting the connected one warns that the board will leave it
    Given the board has saved the networks "Bangalore, HomeNet"
    And the dashboard is open
    And I will answer "no" to the next question
    When I open the "Settings" tab
    And I forget the saved network "HomeNet"
    Then a confirmation asking "looks for another saved network" was shown

  Scenario: Away from every saved network, Settings says so instead of "failed"
    Given the board has saved the networks "Bangalore, HomeNet"
    And the dashboard is open
    When the board is somewhere none of its networks reach
    And I open the "Settings" tab
    Then "#netInfo" contains "none of your 2 saved networks in range"
