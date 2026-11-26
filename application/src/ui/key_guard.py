"""A4 ① (Plan Cordée, gui-operator#1 / ARCH-17): a key typed into a text field is text, not a shortcut.

DearPyGui delivers every key press to the global key handler, also while the operator types into an
input: « PIR130 » in the rig sheet's filter field toggled the preview (P), the ids (I) and the
skeleton overlays; the Save name, the OSC address and the notes did the same.  While any text or
number field is being edited (DPG: the item is active), the single-key shortcuts (letters, digits)
are ignored; Ctrl combinations (Ctrl+S, Ctrl+Shift+E) and the function keys (F8) still work.

The check walks the live item tree, so a field added later (a dialog, a new rig field) is covered
without a list to keep up to date: about 3 ms on the whole window, only for a single-key press.
"""
from __future__ import annotations

import dearpygui.dearpygui as dpg

# The DPG item types an operator types text or numbers into.
TEXT_FIELD_TYPES = frozenset(f"mvAppItemType::{t}" for t in (
    "mvInputText", "mvInputInt", "mvInputFloat", "mvInputDouble",
    "mvInputIntMulti", "mvInputFloatMulti", "mvInputDoubleMulti",
))

# The keys that type a character into a field: the shortcuts made of one of them are ignored while
# a field is being edited.
SINGLE_KEYS = frozenset(
    k for k in (getattr(dpg, f"mvKey_{c}", None) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    if k is not None)


def text_field_active() -> bool:
    """True while a text or number field has the keyboard (is being edited)."""
    for item in dpg.get_all_items():
        try:
            if dpg.get_item_type(item) in TEXT_FIELD_TYPES and dpg.is_item_active(item):
                return True
        except Exception:      # an item deleted mid-walk (a dialog closing): not a field being edited
            continue
    return False


def typed_into_a_field(key, ctrl_down: bool) -> bool:
    """A single-key press (no Ctrl) while a field is being edited: text, so no shortcut."""
    return key in SINGLE_KEYS and not ctrl_down and text_field_active()
