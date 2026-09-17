"""
scripts/tests/test_grid_time_sense.py

Verification suite for the **time sense** wired into the day grid — the P2
deliverable, and the fix for the G7 gap the blueprint recorded:

    "No time sense: no `now` / elapsed / remaining in the grid → past windows are
     still offered."  (docs/Repo-to-Curriculum Blueprint.md)

`get_day_grid` now returns the clock and marks each slot `elapsed` / `current`.
This suite holds the whole seam, because a clock that is wrong is worse than no
clock at all — it makes a confident false claim about his day.

Covers:
 1. `grid.time_sense` — elapsed/remaining/is_today for today, and 0 for any other
    date (asking about tomorrow must not report hours already gone)
 2. `build_day_grid` carries the clock and marks slots relative to an injected
    `now`: the slot the moment falls in is `current`, earlier ones `elapsed`
 3. A future date marks nothing elapsed
 4. The tool contract declares the fields (manifest + the generated TS module)
 5. Over real HTTP: the endpoint returns them
 6. The TypeScript extension renders and cites them
 7. **One implementation** — `read_state` and `build_day_grid` cannot disagree,
    because the reader consumes the grid's clock rather than recomputing it
 8. Backward compatibility — `slots` and `free_windows` keep their shape for the
    callers that only want the grid

Read-only against live memory; the behavioural checks run on fakes.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from api import tools as sidecar_tools  # noqa: E402
from api.main import app  # noqa: E402
from orchestrator.harness import build_day_grid  # noqa: E402
from orchestrator.memory import read_state  # noqa: E402
from orchestrator.memory.grid import grid_date_of, time_sense  # noqa: E402

_passed = 0
_failed = 0
_failed_names: list[str] = []

DAY = "2026-09-18"
MOMENT = datetime(2026, 9, 18, 14, 20)   # 860 minutes in -> slot 28
CURRENT_SLOT = 28


def check(ok: bool, name: str, detail: str = "") -> None:
    """`check(condition, label)` — the shape the other memory suites use."""
    global _passed, _failed
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if ok:
        _passed += 1
    else:
        _failed += 1
        _failed_names.append(name)


def _clock(index: int) -> str:
    return f"{index // 2:02d}:{'30' if index % 2 else '00'}"


def _slots(overrides: dict[int, dict] | None = None) -> list[dict]:
    overrides = overrides or {}
    out: list[dict] = []
    for index in range(48):
        slot = {
            "slot_index": index,
            "state": "free",
            "clock": _clock(index),
            "status": "planned",
            "event_id": None,
            "date": DAY,
        }
        slot.update(overrides.get(index, {}))
        out.append(slot)
    return out


class FakeManager:
    def __init__(self, slots: list[dict] | None = None) -> None:
        self._slots = slots if slots is not None else _slots()

    def get_day_slots(self, _day) -> list[dict]:
        return list(self._slots)

    def get_schedule_events(self, start_date=None, end_date=None) -> list[dict]:
        return []

    def load_profile_facts(self, keys=None) -> dict:
        return {}


class BrokenManager:
    def get_day_slots(self, _day):
        raise RuntimeError("grid store down")

    def get_schedule_events(self, **_kw):
        raise RuntimeError("grid store down")

    def load_profile_facts(self, keys=None):
        return {}


# ---------------------------------------------------------------------------
# 1. The pure primitive
# ---------------------------------------------------------------------------


def test_time_sense_primitive() -> None:
    print("\n=== 1. grid.time_sense — the pure primitive ===")
    check(grid_date_of(DAY) == MOMENT.date(), "grid_date_of parses an ISO date string")

    elapsed, remaining, is_today = time_sense(MOMENT, DAY)
    check(is_today is True, "today is recognised")
    check(elapsed == 860, "elapsed minutes are computed", f"{elapsed}")
    check(remaining == 580, "remaining minutes are computed", f"{remaining}")
    check(elapsed + remaining == 24 * 60, "and together they span the day")

    tomorrow = (MOMENT + timedelta(days=1)).date().isoformat()
    e2, r2, t2 = time_sense(MOMENT, tomorrow)
    check(t2 is False, "a future date is not today")
    check(e2 == 0, "and nothing has elapsed on it", f"{e2}")
    check(r2 == 24 * 60, "so the whole day remains", f"{r2}")

    e3, r3, _ = time_sense(datetime(2026, 9, 18, 0, 0), DAY)
    check((e3, r3) == (0, 1440), "at midnight: nothing elapsed, everything left", f"{e3}/{r3}")

    _e4, r4, _ = time_sense(datetime(2026, 9, 18, 23, 59), DAY)
    check(r4 == 1, "at 23:59 one minute remains", f"{r4}")


# ---------------------------------------------------------------------------
# 2-3. The grid carries and marks the clock
# ---------------------------------------------------------------------------


def test_grid_clock_and_marks() -> None:
    print("\n=== 2. build_day_grid carries the clock ===")
    grid = build_day_grid(FakeManager(), DAY, now=MOMENT)
    check(grid.get("is_today") is True, "is_today is reported")
    check(grid.get("now_clock") == "14:20", "the clock reading is reported", str(grid.get("now_clock")))
    check(grid.get("elapsed_minutes") == 860, "elapsed is reported", str(grid.get("elapsed_minutes")))
    check(grid.get("remaining_minutes") == 580, "remaining is reported", str(grid.get("remaining_minutes")))
    check(
        grid.get("current_slot") == CURRENT_SLOT,
        "the current slot is reported",
        str(grid.get("current_slot")),
    )

    print("\n--- per-slot marks ---")
    slots = grid["slots"]
    check(len(slots) == 48, "all 48 slots are present", str(len(slots)))
    check(
        all(slots[i]["elapsed"] is True for i in range(CURRENT_SLOT)),
        "every slot before the current one is marked elapsed",
    )
    check(slots[CURRENT_SLOT]["elapsed"] is False, "the current slot is not marked elapsed")
    check(slots[CURRENT_SLOT]["current"] is True, "the current slot is marked current")
    check(
        all(slots[i]["current"] is False for i in range(48) if i != CURRENT_SLOT),
        "exactly one slot is marked current",
    )
    check(
        all(slots[i]["elapsed"] is False for i in range(CURRENT_SLOT + 1, 48)),
        "nothing after the current slot is marked elapsed",
    )

    print("\n=== 3. A future date marks nothing elapsed ===")
    future = (MOMENT + timedelta(days=2)).date().isoformat()
    grid_future = build_day_grid(FakeManager(), future, now=MOMENT)
    check(grid_future.get("is_today") is False, "is_today is False")
    check(grid_future.get("current_slot") is None, "there is no current slot")
    check(grid_future.get("elapsed_minutes") == 0, "elapsed is 0", str(grid_future.get("elapsed_minutes")))
    check(
        not any(slot["elapsed"] for slot in grid_future["slots"]),
        "and not one slot is marked elapsed — tomorrow is not half over",
    )


# ---------------------------------------------------------------------------
# 4-6. The contract, on both sides, and over the wire
# ---------------------------------------------------------------------------


def test_contract_and_http() -> None:
    print("\n=== 4. The tool contract declares the clock ===")
    manifest = sidecar_tools.build_manifest()
    descriptor = next(t for t in manifest.tools if t.name == "get_day_grid")
    props = (descriptor.result.get("properties") or {})
    for field in ("is_today", "now_clock", "elapsed_minutes", "remaining_minutes", "errors"):
        check(field in props, f"the response schema declares `{field}`")
    check(
        "left" in (descriptor.description or ""),
        "the description tells the model the day's remainder is available",
    )
    generated = (ROOT / "mentor/src/generated/mentor-tools.ts").read_text(encoding="utf-8")
    check('"remaining_minutes"' in generated, "the generated TS module carries it")

    print("\n=== 5. Over real HTTP ===")
    response = TestClient(app).post("/tools/get_day_grid", json={})
    check(response.status_code == 200, "the endpoint answers 200", str(response.status_code))
    payload = response.json()
    check(payload.get("error") is None, "no error", str(payload.get("error")))
    check(payload.get("is_today") is True, "the default date is today")
    check(
        isinstance(payload.get("remaining_minutes"), int)
        and 0 <= payload["remaining_minutes"] <= 1440,
        "remaining_minutes is a real minute count from the live grid",
        str(payload.get("remaining_minutes")),
    )
    check(
        payload.get("elapsed_minutes") + payload.get("remaining_minutes") == 1440,
        "and elapsed + remaining span the live day",
    )
    check(isinstance(payload.get("errors"), list), "the errors channel is present")

    print("\n=== 6. The TypeScript extension renders it ===")
    ext = (ROOT / "mentor/extensions/read-tools.ts").read_text(encoding="utf-8")
    check("remaining_minutes" in ext and "elapsed_minutes" in ext, "it reads the clock fields")
    check("elapsed?: boolean" in ext, "the slot type carries the elapsed mark")
    check("windowIsPast" in ext, "a past-window test exists")
    check("ALREADY ELAPSED" in ext, "and a past window is flagged to the model")
    check(
        "could not be read" in ext,
        "an unreadable grid is distinguished from an empty one",
    )
    check(
        "do not compute or estimate time left yourself" in ext,
        "the guideline forbids re-estimating the clock",
    )


# ---------------------------------------------------------------------------
# 7-8. One implementation, and backward compatibility
# ---------------------------------------------------------------------------


def test_single_source_and_compat() -> None:
    print("\n=== 7. One implementation of the clock ===")
    grid = build_day_grid(FakeManager(), DAY, now=MOMENT)
    snap = read_state(memory_manager=FakeManager(), day=DAY, now=MOMENT)
    check(
        snap.elapsed_minutes == grid["elapsed_minutes"],
        "the P2 reader and the grid agree on elapsed",
        f"{snap.elapsed_minutes} vs {grid['elapsed_minutes']}",
    )
    check(
        snap.remaining_minutes == grid["remaining_minutes"],
        "and on remaining",
        f"{snap.remaining_minutes} vs {grid['remaining_minutes']}",
    )
    check(snap.now_clock == grid["now_clock"], "and on the clock reading")

    source = (ROOT / "orchestrator/memory/state.py").read_text(encoding="utf-8")
    check("time_sense(" in source, "the reader calls the shared primitive")
    check(
        "hour * 60" not in source,
        "and does not re-derive elapsed minutes itself (no second implementation)",
    )

    print("\n=== 8. Backward compatibility ===")
    check(
        {"slots", "free_windows"} <= set(grid),
        "the original grid keys are still present",
    )
    check(
        {"slot_index", "state", "clock"} <= set(grid["slots"][0]),
        "the slot shape is preserved — the marks are additive",
    )
    window = grid["free_windows"][0]
    check(
        {"start_clock", "end_clock", "duration_min", "start_slot", "slots"} <= set(window),
        "the free-window shape is preserved",
    )

    print("\n--- an unreadable grid is visible, not silent ---")
    broken = build_day_grid(BrokenManager(), DAY, now=MOMENT)
    check(bool(broken.get("errors")), "the failure is collected", str(broken.get("errors")))
    check(broken["slots"] == [], "and the grid is empty (fail-open preserved)")
    check(
        broken.get("remaining_minutes") == 580,
        "while the clock still answers — time sense survives a store outage",
    )


def main() -> int:
    print("=" * 78)
    print("GRID TIME SENSE — where P2 reaches the agent (G7)")
    print("=" * 78)
    test_time_sense_primitive()
    test_grid_clock_and_marks()
    test_contract_and_http()
    test_single_source_and_compat()

    print("\n" + "=" * 78)
    if _failed:
        print(f"RESULT: {_passed} passed, {_failed} failed")
        for name in _failed_names:
            print(f"  ❌ {name}")
        return 1
    print(f"RESULT: {_passed} passed, 0 failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())