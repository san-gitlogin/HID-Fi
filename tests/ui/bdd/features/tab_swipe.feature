Feature: A sideways swipe changes tab only on empty space
  A quick flick across empty space moves to the next or previous tab. Anything
  that handles its own touches - the trackpad, the keyboard in its place, the
  keyboard tab, a knob, a stick, the D-pad, the slide tile - keeps every
  gesture it is given, in every direction, on every screen size. A tab that
  changes under a working finger takes away what the person was using.
  (The Screen tab, in every way of watching, is in screen.feature.)

  Scenario Outline: Every surface that handles its own touches keeps its gestures, on every tab
    Given the dashboard is open
    And I am on the "<tab>" tab
    When I try every tab-changing gesture on every surface that handles its own touches
    Then the "<tab>" tab was never left

    Examples:
      | tab      |
      | Trackpad |
      | Keyboard |
      | Media    |
      | Gamepad  |

  Scenario Outline: The same sweep on a tablet and on a phone on its side
    Given the dashboard is open on a <device>
    And I am on the "<tab>" tab
    When I try every tab-changing gesture on every surface that handles its own touches
    Then the "<tab>" tab was never left

    Examples:
      | device | tab      |
      | tablet | Trackpad |
      | tablet | Keyboard |
      | tablet | Media    |
      | tablet | Gamepad  |

  Scenario Outline: A phone on its side keeps every gesture on its surfaces
    Given the dashboard is open
    And I am on the "<tab>" tab
    When the window is 844 by 390
    And I try every tab-changing gesture on every surface that handles its own touches
    Then the "<tab>" tab was never left

    Examples:
      | tab      |
      | Trackpad |
      | Keyboard |
      | Gamepad  |

  Scenario Outline: On the trackpad, no gesture changes tab
    Given the dashboard is open
    And I am on the "Trackpad" tab
    When I <gesture> <direction> on "#pad"
    Then the "Trackpad" tab is still shown

    Examples:
      | gesture               | direction |
      | flick                 | left      |
      | flick                 | right     |
      | pan two fingers       | left      |
      | pan two fingers       | right     |
      | swipe three fingers   | left      |
      | swipe three fingers   | right     |
      | pinch out             | left      |
      | pinch in              | right     |
      | double tap then flick | left      |
      | double tap then flick | right     |
      | hold then drag        | left      |
      | hold then drag        | right     |
      | drag slowly           | left      |
      | drag slowly           | right     |

  Scenario Outline: With the keyboard shown in the trackpad's place, no gesture changes tab
    Given the dashboard is open
    And I am on the "Trackpad" tab
    When I tap the "#btnDeck" control
    And I <gesture> <direction> on "<surface>"
    Then the "Trackpad" tab is still shown

    Examples:
      | gesture             | direction | surface |
      | flick               | left      | #kbd2   |
      | flick               | right     | #kbd2   |
      | pan two fingers     | left      | #kbd2   |
      | hold then drag      | left      | #kbd2   |
      | flick               | left      | #pkText |

  Scenario: The trackpad in full screen keeps every gesture
    Given the dashboard is open
    And I am on the "Trackpad" tab
    When I tap the "#btnFs" control
    And I flick left on "#pad"
    And I pan two fingers left on "#pad"
    And I swipe three fingers right on "#pad"
    Then the "Trackpad" tab is still shown

  Scenario Outline: The same flick on empty space still changes tab
    Given the dashboard is open
    And I am on the "<from>" tab
    When I flick <direction> across empty space
    Then the "<to>" tab is shown

    Examples:
      | from     | direction | to       |
      | Session  | left      | Settings |
      | Settings | right     | Session  |
      | Media    | left      | Gamepad  |
      | Keys     | right     | Keyboard |
