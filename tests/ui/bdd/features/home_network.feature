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
