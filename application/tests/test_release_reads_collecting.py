"""P6 (Plan Cordee): the release reads the collector policy's public ``collecting`` (agreed with the owner of
``core.gc_policy``), not its private ``_ours``.

When the release's 0.2 s for a frame's end run out while the policy is collecting, it waits once more for a frame's
end (commit 0310ad8).  A policy that says so through ``collecting`` alone is heard.
"""
import time
from types import SimpleNamespace

from core import gc_policy
from test_release_after_collection_bound import _frame_end, _proc, _release_in_a_thread


def test_the_bound_running_out_while_the_policy_says_collecting_waits_for_the_next_frames_end(monkeypatch):
    policy = SimpleNamespace(installed=True, collected_n=7, collecting=False)
    monkeypatch.setattr(gc_policy, "POLICY", policy)
    proc = _proc()
    t0 = time.perf_counter()
    done, out = _release_in_a_thread(proc)
    time.sleep(0.05)
    policy.collecting = True                      # a full collection under way when the bound runs out
    time.sleep(0.27 - (time.perf_counter() - t0))
    assert not done.is_set(), "released when the bound ran out inside the policy's collection"
    policy.collecting = False
    _frame_end(proc)
    t_end = proc.frame_end_t
    assert done.wait(1.0)
    assert t_end <= out["t"] < t_end + 0.1
