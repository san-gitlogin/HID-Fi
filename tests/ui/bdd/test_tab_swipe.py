import pytest
from pytest_bdd import parsers, scenarios, when

pytestmark = pytest.mark.ui
scenarios("features/tab_swipe.feature")


@when(parsers.parse("the window is {w:d} by {h:d}"))
def window_size(ctx, w, h):
    ctx["ui"].page.set_viewport_size({"width": w, "height": h})
    ctx["ui"].idle(400)
