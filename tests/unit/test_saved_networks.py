"""Saved networks (v3.11): remember up to five, join the strongest in range.

Against the board model, which mirrors the firmware's net* table and
staSuperviseScan(); tests/board/test_home_network.py holds the real board to
the same rules, and test_sim_contract.py holds the replies' shape.
"""
import pytest

from support.board_sim import BoardSim

pytestmark = pytest.mark.unit


def board(*ssids):
    s = BoardSim()
    for n in ssids:
        s.handle({"cmd": "wifi_configure", "ssid": n, "password": "pw-" + n}, "serial")
    return s


def saved(s):
    return [n["ssid"] for n in s.handle({"cmd": "wifi_networks"}, "serial")[0]["networks"]]


def test_adding_a_network_keeps_the_others_newest_first():
    assert saved(board("Hometown", "Bangalore")) == ["Bangalore", "Hometown"]


def test_the_list_never_carries_a_password():
    r = board("Hometown").handle({"cmd": "wifi_networks"}, "serial")[0]
    assert r["max"] == 5 and "pw-Hometown" not in str(r)
    assert set(r["networks"][0]) == {"ssid", "proven", "static", "current"}


def test_six_networks_the_oldest_is_forgotten():
    s = board("A", "B", "C", "D", "E", "F")
    assert saved(s) == ["F", "E", "D", "C", "B"]


def test_re_entering_a_network_moves_it_to_the_front_and_keeps_one_row():
    s = board("Hometown", "Bangalore", "Hometown")
    assert saved(s) == ["Hometown", "Bangalore"]


def test_a_changed_password_is_unproven_until_it_works():
    s = board("Hometown")
    s._net_worked("Hometown")
    assert s.nets[0]["proven"]
    s.handle({"cmd": "wifi_configure", "ssid": "Hometown", "password": "new-one-123"}, "serial")
    assert not s.nets[0]["proven"]


@pytest.mark.parametrize("air,joins", [
    ({"Hometown": -45}, "Hometown"),                                  # at home
    ({"Bangalore": -60}, "Bangalore"),                                # at the other home
    ({"Hometown": -80, "Bangalore": -50}, "Bangalore"),               # both: the stronger
    ({"Hometown": -60, "Bangalore": -60}, "Bangalore"),               # a tie: the more recent
    ({"Cafe": -30}, None),                                            # neither: away
    ({}, None),                                                       # nothing at all: away
])
def test_it_joins_the_strongest_saved_network_in_range(air, joins):
    s = board("Hometown", "Bangalore")
    s.air = air
    assert s.net_search() == joins
    st = s.status()
    if joins:
        assert st["wifi_connected"] and st["wifi_ssid"] == joins and not st["sta_away"]
        assert saved(s)[0] == joins, "the network that worked goes to the front"
    else:
        assert st["sta_away"] and st["sta_phase"] == "reconnecting" and "in range" in st["sta_error"]


def test_a_refused_password_is_skipped_even_when_in_range():
    s = board("Hometown", "Bangalore")
    s.nets[0]["refused"] = True                    # Bangalore's new password was wrong
    s.air = {"Bangalore": -40, "Hometown": -70}
    assert s.net_search() == "Hometown"
    assert s.handle({"cmd": "wifi_networks"}, "serial")[0]["networks"][1]["refused"] is True


def test_forgetting_one_keeps_the_rest():
    s = board("Hometown", "Bangalore")
    r = s.handle({"cmd": "wifi_forget", "ssid": "Bangalore"}, "serial")[0]
    assert r == {"status": "ok", "reply": "wifi_forgotten", "ssid": "Bangalore", "left": 1}
    assert saved(s) == ["Hometown"]


def test_forgetting_the_connected_one_leaves_it():
    s = board("Hometown", "Bangalore")
    s.air = {"Hometown": -40}
    s.net_search()
    s.handle({"cmd": "wifi_forget", "ssid": "Hometown"}, "serial")
    assert not s.status()["wifi_connected"]


def test_forgetting_an_unknown_network_says_so():
    r = board("Hometown").handle({"cmd": "wifi_forget", "ssid": "Nowhere"}, "serial")[0]
    assert r["status"] == "error" and "saved" in r["message"]


def test_reset_wifi_forgets_them_all():
    s = board("Hometown", "Bangalore")
    s.handle({"cmd": "wifi_disconnect"}, "serial")
    assert saved(s) == [] and s.status()["sta_known"] == 0


def test_status_counts_them():
    st = board("Hometown", "Bangalore").status()
    assert st["sta_known"] == 2 and "sta_scans" in st
