"""SetRoiRect / ExcludeAt: remote ROI and ghost-spot fixes (normalized frame coords)."""
import pytest

from runtime import api


def test_roi_rect_and_exclude_validate_normalized_coords():
    assert api.SetRoiRect(0.1, 0.2, 0.5, 0.6).w == 0.5
    with pytest.raises(ValueError):
        api.SetRoiRect(0.1, 0.2, 1.5, 0.6)
    with pytest.raises(ValueError):
        api.SetRoiRect(0.1, 0.2, 0.0, 0.6)
    assert api.ExcludeAt(0.043, 0.547, radius=1).include is False
    with pytest.raises(ValueError):
        api.ExcludeAt(-0.1, 0.5)
    with pytest.raises(ValueError):
        api.ExcludeAt(0.5, 0.5, radius=9)


def test_remote_policy_classes_them_as_control():
    from services.remote_api import POLICY
    assert POLICY["SetRoiRect"] == POLICY["ExcludeAt"] == POLICY["ClearMask"]
