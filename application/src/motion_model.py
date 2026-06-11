"""Import shim (docs/DECOMPOSITION_PLAN.md Phase 1): moved to core/motion_model.py.

Aliases this module to the moved one so bare-name imports, module-attribute
access, and monkeypatching keep working for app.py/gui.py, tests, and
in-flight branches. Follow-up: delete all shims once those import from the
packages directly.
"""
import sys as _sys
from core import motion_model as _mod
_sys.modules[__name__] = _mod
