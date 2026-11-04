"""Playback-window sampling policy.

How densely Orbix samples a simulation window, and how accurate that sampling is.
This is *policy*, not serialisation, so it lives in the orbital layer rather than in
the globe payload module (which only encodes whatever window it is handed).

The decision is made in two steps:

1. **Accuracy** - sample finely enough that linear interpolation between keyframes
   stays within ``TARGET_INTERPOLATION_KM`` of the true arc.
2. **Budget** - coarsen only if ``satellites x keyframes`` would push the browser
   payload past ``KEYFRAME_BUDGET_CHARS``.

The resulting error estimate is a genuine computation (the chord/sagitta of a
circular arc), not a guess: the UI quotes this number to the user.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

__all__ = [
    "KEYFRAME_BUDGET_CHARS",
    "MAX_KEYFRAMES",
    "TARGET_INTERPOLATION_KM",
    "WindowPlan",
    "chord_error_km",
    "interval_for_error",
    "plan_window",
]

#: Upper bound on base64 characters spent on keyframes per payload (~1.5 MB binary).
KEYFRAME_BUDGET_CHARS = 2_000_000

#: Never sample more finely than this, even for tiny constellations.
MAX_KEYFRAMES = 91

#: Sampling target: aim for at most this chord error, then coarsen only if the payload
#: budget demands it. 1 km is far below the element-set accuracy, so the animation is
#: essentially exact while the payload stays reasonable.
TARGET_INTERPOLATION_KM = 1.0

#: Reference LEO period/radius for the error estimate: the fastest objects Orbix
#: displays, which keeps the quoted error conservative.
_REFERENCE_PERIOD_S = 92.95 * 60.0
_REFERENCE_RADIUS_KM = 6793.0


@dataclass(frozen=True)
class WindowPlan:
    """How a playback window is sampled, and how accurate that sampling is."""

    start: datetime
    end: datetime
    keyframes: int
    interval_seconds: float
    max_interpolation_km: float

    @property
    def duration_seconds(self) -> float:
        """Window length in seconds."""
        return (self.end - self.start).total_seconds()

    def describe(self) -> str:
        """One-line accuracy note for the UI."""
        return (
            f"{self.keyframes} keyframes over "
            f"{self.duration_seconds / 60.0:.0f} min "
            f"(every {self.interval_seconds / 60.0:.1f} min); worst-case "
            f"interpolation error \u2248 {self.max_interpolation_km:.1f} km"
        )


def chord_error_km(interval_seconds: float) -> float:
    """Worst-case error from linearly interpolating a circular orbit.

    A satellite moving at angular rate ``w`` along a circle of radius ``r`` deviates
    from the straight line between two samples by ``r * (1 - cos(w*dt/2))`` at the
    midpoint. The UI quotes this number, so it is computed rather than guessed.
    """
    omega = 2.0 * math.pi / _REFERENCE_PERIOD_S
    half = omega * interval_seconds / 2.0
    return _REFERENCE_RADIUS_KM * (1.0 - math.cos(half))


def interval_for_error(target_km: float) -> float:
    """Largest sampling interval whose chord error stays below ``target_km``."""
    if target_km <= 0:
        return 0.0
    omega = 2.0 * math.pi / _REFERENCE_PERIOD_S
    ratio = min(1.0, target_km / _REFERENCE_RADIUS_KM)
    return 2.0 * math.acos(1.0 - ratio) / omega


def plan_window(
    nominal_start: datetime,
    nominal_end: datetime,
    n_satellites: int,
    *,
    budget_chars: int = KEYFRAME_BUDGET_CHARS,
    max_keyframes: int = MAX_KEYFRAMES,
    target_error_km: float = TARGET_INTERPOLATION_KM,
) -> WindowPlan:
    """Pick a keyframe count from the accuracy target and the payload budget.

    Sampling is as fine as needed to stay under ``target_error_km``, then coarsened if
    ``n_satellites`` would push the payload past ``budget_chars``. Bigger
    constellations therefore get a larger - but always displayed - interpolation error.
    """
    if nominal_end <= nominal_start:
        raise ValueError("window end must be after start")
    if n_satellites <= 0:
        raise ValueError("need at least one satellite")

    duration = (nominal_end - nominal_start).total_seconds()

    # 1. How fine must we sample to hit the accuracy target?
    wanted_interval = max(1.0, interval_for_error(target_error_km))
    needed = int(math.ceil(duration / wanted_interval)) + 1

    # 2. How fine may we sample before the payload budget bites?
    #    3 coordinates per satellite per keyframe, 4 bytes each, 4/3 base64 inflation.
    chars_per_keyframe = n_satellites * 3 * 4 * 4 / 3
    affordable = max(2, int(budget_chars / chars_per_keyframe))

    keyframes = int(max(2, min(max_keyframes, needed, affordable)))
    interval = duration / (keyframes - 1)
    return WindowPlan(
        start=nominal_start,
        end=nominal_end,
        keyframes=keyframes,
        interval_seconds=interval,
        max_interpolation_km=chord_error_km(interval),
    )
