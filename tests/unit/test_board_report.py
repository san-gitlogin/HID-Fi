"""The board-restore report and its failure message never carry a macro's contents.

tests/.reports/board_restore.json listed every macro's name and steps in plain
text until 2026-10-28 - on the owner's board that was an identity number. The
report now holds hashes; this keeps it that way.
"""
import json

import pytest

import conftest

pytestmark = pytest.mark.unit


def test_a_snapshot_is_printed_as_hashes_only():
    secret = "123456789012"
    snap = {"macros": {1: ("Private name", [secret]), 4: ("x", ["text hello"])}, "auth_set": True,
            "power_mode": "balanced"}
    shown = json.dumps(conftest._redacted(snap))
    assert secret not in shown and "Private name" not in shown and "hello" not in shown
    assert set(conftest._redacted(snap)["macros"]) == {1, 4}, "which slots are used is still visible"
    assert conftest._redacted(snap)["power_mode"] == "balanced"


def test_two_different_macros_never_look_the_same():
    a = conftest._redacted({"macros": {1: ("a", ["x"])}})["macros"][1]
    b = conftest._redacted({"macros": {1: ("a", ["y"])}})["macros"][1]
    assert a != b, "a changed macro must still show as changed"
