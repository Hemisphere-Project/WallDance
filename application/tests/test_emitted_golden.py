"""TEST-2: the emitted (identity-slot) stream on recorded replay timelines (no GPU, no recordings).

Fixtures + goldens live in tests/golden/emitted (built by tests/emitted_golden.py from dev37 TRT
replays).  Two gates:
  * the per-frame emitted stream (slot ids, states, positions) matches the golden for the shipped
    defaults -- an intended change regenerates it: ``python tests/emitted_golden.py --update``;
  * a KPI floor that must hold whatever the change (continuity, on-dancer, coasting, jitter).
"""
import json

import pytest

import emitted_golden as eg

FIXTURES = eg.fixtures()


def _golden(name):
    return json.loads((eg.DIR / f"{name}.golden.json").read_text())


@pytest.mark.parametrize("name", FIXTURES)
def test_emitted_stream_matches_golden(name):
    g = _golden(name)
    if g.get("env") and g["env"] != eg.env_versions():
        pytest.skip(f"exact golden built with {g['env']}, here {eg.env_versions()}: "
                    "KPI floors still apply (test_emitted_kpi_floor)")
    rows, kpi = eg.simulate(eg.load_fixture(name))
    got = eg.stream_of(rows)
    want = _golden(name)["stream"]
    assert len(got) == len(want)
    bad_ids = sum(1 for g, w in zip(got, want) if [(t[0], t[1]) for t in g[1]] != [(t[0], t[1]) for t in w[1]])
    bad_pos = sum(1 for g, w in zip(got, want)
                  for a, b in zip(g[1], w[1]) if abs(a[2] - b[2]) > 1.0 or abs(a[3] - b[3]) > 1.0)
    assert bad_ids == 0 and bad_pos == 0, (
        f"{name}: {bad_ids} frames with other slot ids/states, {bad_pos} points moved > 1 px; "
        "if intended, regenerate with `python tests/emitted_golden.py --update`")


# KPI floors: what the emitted stream must keep on each fixture (PLAN_25M demo KPI, per take).
FLOORS = {
    # name:               (coverage >=, on_dancer >=, coasting <=)
    "white-duo-full":      (0.85, 0.74, 0.20),
    "white-duo-ghost":     (0.97, 0.72, 0.17),   # the injected static ghost must not starve a dancer
    "texture-duo-win":     (0.89, 0.89, 0.32),
    "hangar-aerial-win":   (0.99, 0.92, 0.02),
    "bdx-s5-static-ghost": (0.99, 0.75, 0.20),   # a static figure must yield the only slot to the walker
    "wallhang-still":      (0.83, 0.94, 0.10),   # a still dancer must never be released / yielded
}


@pytest.mark.parametrize("name", FIXTURES)
def test_emitted_kpi_floor(name):
    _rows, kpi = eg.simulate(eg.load_fixture(name))
    cov, ond, coast = FLOORS[name]
    assert kpi["continuity.coverage"] >= cov, kpi
    assert kpi["continuity.on_dancer"] >= ond, kpi
    assert kpi["quality.coasting_share"] <= coast, kpi
    assert kpi["quality.over_n_frames"] == 0, kpi


def test_every_fixture_has_a_floor():
    assert set(FIXTURES) == set(FLOORS)
