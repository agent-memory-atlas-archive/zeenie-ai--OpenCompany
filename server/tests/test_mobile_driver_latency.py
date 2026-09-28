from types import SimpleNamespace
from unittest.mock import Mock

from nodes.mobile.runtime.device_server import AndroidDriver


def test_geometry_uses_one_device_query_and_preserves_rotation():
    driver = AndroidDriver.__new__(AndroidDriver)
    driver.device = SimpleNamespace(info={"displayWidth": 2400, "displayHeight": 1080, "displayRotation": 1},
                                    window_size=Mock(side_effect=AssertionError("redundant query")))
    assert driver.geometry() == {"width": 2400, "height": 1080, "rotation": 1}


def test_key_input_does_not_wait_for_geometry():
    driver = AndroidDriver.__new__(AndroidDriver)
    driver.device = SimpleNamespace(press=Mock())
    driver.geometry = Mock(side_effect=AssertionError("unneeded query"))
    assert driver.call("key", {"key": "home"}) is True
    driver.device.press.assert_called_once_with("home")


def test_pointer_still_rejects_stale_screen():
    import pytest
    driver = AndroidDriver.__new__(AndroidDriver)
    driver.device = SimpleNamespace(info={"displayWidth": 2400, "displayHeight": 1080, "displayRotation": 1})
    with pytest.raises(ValueError, match="Screen changed"):
        driver.call("tap", {"x": 10, "y": 10, "geometry": {"width": 1080, "height": 2400, "rotation": 0}})


def test_android_skill_is_registered_for_auto_attachment():
    from nodes._visuals import get_skill
    assert get_skill("android_tool") == "android-phone-skill"
