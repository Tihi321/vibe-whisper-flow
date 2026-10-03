import pytest

from vibeflow.hotkeys import ChordTracker, key_id_to_vk, parse_chord


def ids(chord):
    return [set(g) for g in chord.groups]


def test_parse_ctrl_win():
    c = parse_chord("Ctrl + Win")
    assert ids(c) == [{"ctrl_l", "ctrl_r"}, {"cmd_l", "cmd_r"}]
    assert c.has_win
    assert c.describe() == "Ctrl + Win"


@pytest.mark.parametrize("spec,expected", [
    ("control", {"ctrl_l", "ctrl_r"}), ("super", {"cmd_l", "cmd_r"}),
    ("meta", {"cmd_l", "cmd_r"}), ("cmd", {"cmd_l", "cmd_r"}),
    ("capslock", {"caps_lock"}), ("caps", {"caps_lock"}), ("escape", {"esc"}),
    ("rctrl", {"ctrl_r"}), ("right_ctrl", {"ctrl_r"}), ("ctrl_l", {"ctrl_l"}),
    ("lwin", {"cmd_l"}), ("right_win", {"cmd_r"}), ("ralt", {"alt_r"}),
    ("shift", {"shift_l", "shift_r"}), ("F12", {"f12"}), ("A", {"a"}), ("5", {"5"}),
    ("space", {"space"}),
])
def test_aliases(spec, expected):
    assert ids(parse_chord(spec)) == [expected]


def test_three_key_chord_and_describe():
    c = parse_chord("ctrl+shift+space")
    assert len(c.groups) == 3 and not c.has_win
    assert c.describe() == "Ctrl + Shift + Space"
    assert parse_chord("ctrl_r").describe() == "Right Ctrl"


@pytest.mark.parametrize("bad", ["", "  ", "ctrl+", "+a", "ctrl+bogus", "foo"])
def test_parse_errors(bad):
    with pytest.raises(ValueError):
        parse_chord(bad)


def test_error_message_helpful():
    with pytest.raises(ValueError, match="bogus"):
        parse_chord("ctrl+bogus")


def test_tracker_ctrl_win():
    t = ChordTracker(parse_chord("ctrl+win"))
    assert t.press("ctrl_l") is False
    assert t.press("cmd_l") is True
    assert t.is_down
    assert t.press("cmd_l") is False  # key repeat
    assert t.press("ctrl_l") is False
    assert t.release("cmd_l") is True
    assert not t.is_down
    assert t.release("ctrl_l") is False
    # re-press
    assert t.press("cmd_r") is False
    assert t.press("ctrl_r") is True


def test_tracker_order_independent_and_other_keys():
    t = ChordTracker(parse_chord("ctrl+win"))
    assert t.press("cmd_l") is False
    assert t.press("a") is False
    assert t.press("ctrl_r") is True
    assert t.release("a") is False
    assert t.release("ctrl_r") is True


def test_tracker_single_key_repeat():
    t = ChordTracker(parse_chord("ctrl_r"))
    assert t.press("ctrl_l") is False
    assert t.press("ctrl_r") is True
    assert t.press("ctrl_r") is False
    assert t.press("ctrl_r") is False
    assert t.release("ctrl_l") is False
    assert t.release("ctrl_r") is True
    assert t.release("ctrl_r") is False


def test_tracker_set_chord_resets():
    t = ChordTracker(parse_chord("ctrl_r"))
    t.press("ctrl_r")
    t.set_chord(parse_chord("f9"))
    assert not t.is_down
    assert t.press("f9") is True


def test_key_id_to_vk():
    assert key_id_to_vk("ctrl_l") == 0xA2
    assert key_id_to_vk("a") == 0x41
    assert key_id_to_vk("f1") == 0x70
    assert key_id_to_vk("vk:200") == 200
    assert key_id_to_vk("nonsense") is None
