"""A seat may ask to be woken by a birth (`child_born`): a son's birth can turn a succession into a split, and a long
step can run on from it to the ruler's death with no read between."""
from ck3bench.env import wake
from ck3bench.env.ck3 import WAKE_EVENTS


def test_child_born_fires_on_a_new_child_only():
    start = {"character_id": 31762, "events": {"child_born": ["36303", "66504"]}}
    now = {"character_id": 31762, "events": {"child_born": ["36303", "66504", "66896"]}}
    assert wake.holds({"event": "child_born"}, start, now) == (True, ["66896"])


def test_child_born_CONTROL_no_birth_no_wake_and_an_unread_child_list_does_not_fire():
    start = {"character_id": 31762, "events": {"child_born": ["36303"]}}
    assert wake.holds({"event": "child_born"}, start, start)[0] is False
    assert wake.holds({"event": "child_born"}, start, {"character_id": 31762, "events": {"child_born": None}})[0] is False


def test_the_game_backend_witnesses_it():
    assert "child_born" in WAKE_EVENTS and "child_born" in wake.EVENTS and "child_born" in wake.GAINED
