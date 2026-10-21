Feature: The dashboard shell - navigation, connection, the access PIN
  The frame every screen lives in: seven tabs, the live connection to the board,
  the latency and connection-count pills, help for every tab, and the access PIN
  that gates everything when one is set.

  Scenario Outline: Every tab can be opened from the tab bar on a phone
    Given the dashboard is open
    When I open the "<tab>" tab
    Then the "<tab>" tab is shown
    And "#vtitle" reads "<title>"

    Examples:
      | tab      | title    |
      | Trackpad | Trackpad |
      | Keyboard | Keyboard |
      | Keys     | Keys     |
      | Media    | Media    |
      | Gamepad  | Gamepad  |
      | Session  | Session  |
      | Settings | Settings |

  Scenario Outline: Every tab can be opened from the sidebar on a desktop
    Given the dashboard is open on a desktop
    When I open the "<tab>" tab
    Then the "<tab>" tab is shown

    Examples:
      | tab      |
      | Keyboard |
      | Settings |
      | Trackpad |

  Scenario: A link to a tab opens that tab
    Given the dashboard is opened at "#setup"
    Then the "Settings" tab is shown

  Scenario: The browser's back button returns to the previous tab
    Given the dashboard is open
    When I open the "Media" tab
    And I open the "Session" tab
    And I go back in the browser
    Then the "Media" tab is shown

  Scenario: The dashboard connects and shows what it is talking to
    Given the dashboard is open
    Then "#ltext" reads "live"
    And "#brandsub" reads "v3.10"
    And "#connN" reads "1"

  Scenario: The latency pill shows the round trip to the board
    Given the dashboard is open
    When the latency ping has had time to run
    Then "#trtt" contains "ms"

  Scenario: Help is available on every tab
    Given the dashboard is open
    When I tap the "#btnInfo" control
    Then the dialog "Trackpad gestures" is open
    When I close the dialog
    And I open the "Settings" tab
    And I tap the "#btnInfo" control
    Then the dialog "Settings" is open
    When I tap outside the dialog
    Then no dialog is open

  Scenario: Refresh asks the board for everything again
    Given the dashboard is open
    When I tap the "#btnRefresh" control
    Then I see the message "Refreshed"
    And the board receives {"cmd": "status"}
    And the board receives {"cmd": "vault_list"}
    And the board receives {"cmd": "pc_list"}
    And the board receives {"cmd": "macro_list"}

  Scenario: A new connection releases anything a previous session left held
    Given the dashboard is open
    Then the board receives {"cmd": "key_release_all"}

  Scenario: Losing the board shows offline, and the dashboard reconnects by itself
    Given the dashboard is open
    When the board drops the connection
    Then "#ltext" reads "offline"
    And the dashboard reconnects
    And "#ltext" reads "live"

  Scenario: A board with a PIN asks for it before anything works
    Given the board's access PIN is "4826"
    When I open the dashboard without signing in
    Then the dialog "Access PIN" is open
    When I enter the PIN "4826"
    Then I see the message "Unlocked"
    And no dialog is open
    And the board receives {"cmd": "auth", "token": "4826"}

  Scenario: A wrong PIN is refused, and the next wrong one locks for 5 seconds
    Given the board's access PIN is "4826"
    When I open the dashboard without signing in
    And I enter the PIN "1111"
    Then "#pinAskMsg" reads "That PIN was not right."
    When I enter the PIN "2222"
    Then "#pinAskMsg" contains "Locked for 5s"

  Scenario: While locked out even the right PIN is refused until the wait is over
    Given the board's access PIN is "4826"
    When I open the dashboard without signing in
    And I enter the PIN "1111"
    And I enter the PIN "2222"
    And I enter the PIN "4826"
    Then "#pinAskMsg" contains "Too many wrong PINs"
    When 6 seconds pass on the board
    And I enter the PIN "4826"
    Then I see the message "Unlocked"

  Scenario: Signing in survives a reconnect without asking again
    Given the dashboard is open and signed in with "4826"
    When the board drops the connection
    Then the dashboard reconnects
    And no dialog is open
    And the board receives {"cmd": "auth", "token": "4826"}

  Scenario: The sidebar collapses on a desktop and stays collapsed
    Given the dashboard is open on a desktop
    When I tap the "#btnRail" control
    Then the sidebar is collapsed
    When I reload the dashboard
    Then the sidebar is collapsed

  Scenario: A quick sideways flick moves between tabs on a phone
    Given the dashboard is open
    And I am on the "Session" tab
    When I flick left across empty space
    Then the "Settings" tab is shown
    When I flick right across empty space
    Then the "Session" tab is shown

  Scenario: When the board cannot be reached at all, the link says so and the dashboard keeps trying
    Given the dashboard is open
    When the board becomes unreachable
    Then "#ltext" reads "offline"
    When the board takes connections again
    Then the dashboard reconnects

  Scenario: With the socket out of reach, coming back to the page asks for status over HTTP
    Given the dashboard is open
    When the board becomes unreachable
    And the page is hidden
    And the page is shown again
    Then the board was asked for its status over HTTP

  Scenario Outline: Only a quick sideways flick on empty space changes tab
    Given the dashboard is open
    And I am on the "Session" tab
    When I <gesture>
    Then the "Session" tab is shown

    Examples:
      | gesture                                    |
      | flick up across empty space                |
      | flick left starting on a button            |
      | drag slowly left across empty space        |
      | flick left with two fingers                |

  Scenario: A mouse drag never changes tab
    Given the dashboard is open on a desktop
    And I am on the "Session" tab
    When I drag the mouse left across empty space
    Then the "Session" tab is shown

  # Found by a person: the header said 3 and nothing on the page said who. A tab
  # left open on a laptop over the home network was never in any list.
  Scenario: Tapping the count shows every dashboard, and which one is this phone
    Given the dashboard is open and signed in with "4826"
    When a laptop on the home network opens the dashboard
    Then the count of dashboards becomes 2
    When I tap the "#connPill" control
    Then the dialog "Connected dashboards" is open
    And "#dashList" contains "this device"
    And "#dashList" contains "over your home network"
    And "#dashList" contains "not signed in"
    And "#dashList" contains "A browser tab left open anywhere counts"

  # Found by a person: three phones read as 5 - a phone with the dashboard and
  # its watch-only window open is one device with two pages, not two devices.
  Scenario: A device with two pages open counts once, and the list says so
    Given the dashboard is open and signed in with "4826"
    When a laptop on the home network opens the dashboard
    And the laptop opens a second page
    Then the count of dashboards becomes 2
    When I tap the "#connPill" control
    Then the dialog "Connected dashboards" is open
    And "#dashList" contains "2 pages open"
    And "#dashList" contains "this device"

  Scenario: Settings lists dashboards on the home network too, not only the board's WiFi
    Given the dashboard is open and signed in with "4826"
    When a laptop on the home network opens the dashboard
    And I open the "Settings" tab
    And I tap the "#btnClients" control
    Then "#clientList" contains "Dashboards open"
    And "#clientList" contains "over your home network"
    And "#clientList" contains "this device"

  Scenario: A closed tab stops counting
    Given the dashboard is open and signed in with "4826"
    When a laptop on the home network opens the dashboard
    Then the count of dashboards becomes 2
    When the laptop closes its tab
    Then the count of dashboards becomes 1

  Scenario: The count opens from the keyboard too
    Given the dashboard is open on a desktop
    When I focus the dashboard count and press Enter
    Then the dialog "Connected dashboards" is open

  # Found on the real board: the 6th page (three phones, a watch window each)
  # showed "offline" and a PIN box that could never work - the board was full.
  Scenario: A page the board has no room for says so, and connects when one closes
    Given the board can serve 2 pages at once
    And a laptop on the home network has the dashboard open twice
    When the dashboard is opened on this phone
    Then the dialog "This board is full" is open
    When the laptop closes its tab
    Then the dashboard reconnects
    And no dialog is open

