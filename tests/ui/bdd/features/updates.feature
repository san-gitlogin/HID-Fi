Feature: Updates - the board tells you about a new version and installs it only when you say so
  The Updates card in Settings (docs/OTA.md). The board does the work; the
  dashboard shows where it is, asks before anything is downloaded, and says
  plainly what happened after a restart - including when the board went back to
  the version before by itself.

  Background:
    Given the board runs "hid_fi_v4.0.4"

  Scenario: Nothing newer: the card says the version and that it looks by itself
    Given the dashboard is open
    When I open the "Settings" tab
    Then "#otaState" reads "HID-Fi v4.0.4"
    And "#otaSub" contains "Up to date"

  Scenario: Check now finds a release and nothing is downloaded until I say so
    Given a release "4.1.0" is published with sections "4.1.0, 4.0.5"
    And the dashboard is open
    When I open the "Settings" tab
    And I tap the "#btnOtaCheck" control
    Then the board receives {"cmd": "ota_check"}
    And "#otaState" eventually reads "Update available: 4.1.0"
    And "#otaSub" contains "Nothing is downloaded until you say so"
    And the board does not receive "ota_install"

  Scenario: The update sheet shows every change since my version and the download size, then installs on consent
    Given a release "4.1.0" is published with sections "4.1.0, 4.0.5"
    And the board has found that release
    And the dashboard is open
    When I open the "Settings" tab
    And I tap the "#btnOtaShow" control
    Then the dialog "Update available" is open
    And I see "4.0.5"
    And I see "1.5 MB to download"
    And I see "restarts once"
    And I see "pairings stay"
    When I tap "Update now"
    Then the board receives {"cmd": "ota_install"}
    And no dialog is open
    And "#otaState" eventually reads "Ready: restart into 4.1.0"

  Scenario: Later closes the sheet and installs nothing
    Given a release "4.1.0" is published with sections "4.1.0"
    And the board has found that release
    And the dashboard is open
    When I open the "Settings" tab
    And I tap the "#btnOtaShow" control
    And I tap "Later"
    Then no dialog is open
    And the board does not receive "ota_install"
    And "#otaState" reads "Update available: 4.1.0"

  Scenario: A security update says so first, with the risk it removes
    Given a security release "4.0.6" is published fixing "Anyone on the network could crash the board"
    And the board has found that release
    And the dashboard is open
    When I open the "Settings" tab
    And I tap the "#btnOtaShow" control
    Then I see "Security update."
    And I see "Anyone on the network could crash the board"

  Scenario: While it downloads the card shows progress and keyboard and mouse keep working
    Given the board is downloading "4.1.0" and has 40 percent
    And the dashboard is open
    When I open the "Settings" tab
    Then "#otaState" reads "Updating: 40%"
    And "#otaSub" contains "Keyboard and mouse keep working"
    And "#otaBar" is shown

  Scenario: While I use the keyboard or mouse the download waits, and says so
    Given the board is downloading "4.1.0" and has 40 percent
    And the download is paused because I am using the board
    And the dashboard is open
    When I open the "Settings" tab
    Then "#otaSub" contains "Paused while you use the keyboard or mouse"

  Scenario: Cancel while downloading changes nothing
    Given the board is downloading "4.1.0" and has 40 percent
    And the dashboard is open
    When I open the "Settings" tab
    And I tap the "#btnOtaCancel" control
    Then the board receives {"cmd": "ota_cancel"}
    And "#otaSub" eventually contains "Cancelled. Nothing was changed."

  Scenario: Cancel cannot interrupt the moment of switching
    Given the board is switching to "4.1.0"
    And the dashboard is open
    When I open the "Settings" tab
    Then "#btnOtaCancel" is disabled
    And "#otaState" reads "Switching…"

  Scenario: Ready - restart now, and the board waits until nobody is typing
    Given the board is ready to restart into "4.1.0"
    And the dashboard is open
    When I open the "Settings" tab
    Then "#otaSub" contains "It waits until nobody is typing"
    When I tap the "#btnOtaApply" control
    Then the board receives {"cmd": "ota_apply"}

  Scenario: Ready - cancel keeps this version
    Given the board is ready to restart into "4.1.0"
    And the dashboard is open
    When I open the "Settings" tab
    And I tap the "#btnOtaCancel" control
    Then the board receives {"cmd": "ota_cancel"}
    And I see the message "Cancelled - the board stays on this version"

  Scenario: An update HID-Fi did not sign is ignored, and said so
    Given a release "4.1.0" is published but signed by someone else
    And the dashboard is open
    When I open the "Settings" tab
    And I tap the "#btnOtaCheck" control
    Then "#otaSub" eventually contains "HID-Fi did not sign"
    And "#btnOtaShow" is hidden

  Scenario: No connection to the update server
    Given the update server cannot be reached
    And the dashboard is open
    When I open the "Settings" tab
    And I tap the "#btnOtaCheck" control
    Then "#otaSub" eventually contains "Could not reach the update server"

  Scenario: A board too old for the release is told to use the cable once
    Given a release "4.1.0" is published that updates only from "4.0.5"
    And the dashboard is open
    When I open the "Settings" tab
    And I tap the "#btnOtaCheck" control
    Then "#otaSub" eventually contains "update it once with the cable"

  Scenario: Right after an update the new version is proving itself
    Given the board is proving the new version "hid_fi_v4.1.0"
    And the dashboard is open
    When I open the "Settings" tab
    Then "#otaState" reads "Checking the new version v4.1.0"
    And "#btnOtaCheck" is disabled

  Scenario: After the restart the dashboard says the update was kept - once
    Given the board was updated from "hid_fi_v4.0.4" to "hid_fi_v4.1.0"
    And the dashboard is open
    When I open the "Settings" tab
    Then I see the message "Updated to v4.1.0"
    When I reload the dashboard
    And I open the "Settings" tab
    Then the message "Updated to v4.1.0" was shown once

  Scenario: An update that did not start properly - the board went back and says why
    Given the update to "hid_fi_v4.1.0" was not kept because "did_not_start"
    And the dashboard is open
    When I open the "Settings" tab
    Then "#otaSub" eventually contains "it did not start properly"
    And "#otaSub" contains "went back to v4.0.4 by itself"

  Scenario: A board still on the single-slot layout needs the cable once
    Given the board has the single-slot layout
    And the dashboard is open
    When I open the "Settings" tab
    Then "#otaState" reads "One cable update needed"
    And "#btnOtaCheck" is hidden

  Scenario: Looking for updates by itself can be switched off
    Given the dashboard is open
    When I open the "Settings" tab
    Then "#tOtaAuto" is on
    When I tap the "#tOtaAuto" control
    Then the board receives {"cmd": "ota_settings", "auto": false}
    And "#tOtaAuto" is off

  Scenario: Installing security updates by itself asks first, and only when switching it on
    Given the dashboard is open
    When I open the "Settings" tab
    And I will answer "no" to the next question
    And I tap the "#tOtaAutoSec" control
    Then a confirmation asking "fix security problems by themselves" was shown
    And the board does not receive "ota_settings"
    When I will answer "yes" to the next question
    And I tap the "#tOtaAutoSec" control
    Then the board receives {"cmd": "ota_settings", "auto_security": true}
    And "#tOtaAutoSec" is on
