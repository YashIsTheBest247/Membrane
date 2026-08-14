"""Circuit breaker.

Bounds the blast radius of a novel attack class that slips past all four
layers. An anomalous rate of held actions auto-quarantines the agent's
privileged capabilities and alerts the operator: the system assumes failure
rather than assuming success.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from .config import get_settings


@dataclass
class BreakerState:
    session_id: str
    holds: deque[float] = field(default_factory=deque)
    tripped_at: float | None = None
    trips: int = 0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "recent_holds": len(self.holds),
            "open": self.tripped_at is not None,
            "tripped_at": self.tripped_at,
            "trips": self.trips,
        }


class CircuitBreaker:
    """Per-session sliding window over held and blocked actions."""

    def __init__(self) -> None:
        self._states: dict[str, BreakerState] = {}

    def _state(self, session_id: str) -> BreakerState:
        state = self._states.get(session_id)
        if state is None:
            state = BreakerState(session_id=session_id)
            self._states[session_id] = state
        return state

    def _prune(self, state: BreakerState, now: float) -> None:
        window = get_settings().breaker_window_seconds
        while state.holds and now - state.holds[0] > window:
            state.holds.popleft()

    def record_hold(self, session_id: str) -> bool:
        """Record a hold or block. Returns True if this tripped the breaker."""
        settings = get_settings()
        now = time.time()
        state = self._state(session_id)
        self._prune(state, now)
        state.holds.append(now)

        if state.tripped_at is not None:
            return False
        if len(state.holds) >= settings.breaker_hold_threshold:
            state.tripped_at = now
            state.trips += 1
            return True
        return False

    def is_open(self, session_id: str) -> bool:
        """Is the breaker currently quarantining privileged capabilities?"""
        settings = get_settings()
        state = self._states.get(session_id)
        if state is None or state.tripped_at is None:
            return False
        if time.time() - state.tripped_at > settings.breaker_cooldown_seconds:
            # Cooldown elapsed: close the breaker but keep the trip count, so
            # the incident stays visible in the session's history.
            state.tripped_at = None
            state.holds.clear()
            return False
        return True

    def reset(self, session_id: str) -> None:
        """Operator override after investigating."""
        state = self._states.get(session_id)
        if state is not None:
            state.tripped_at = None
            state.holds.clear()

    def state(self, session_id: str) -> BreakerState:
        return self._state(session_id)

    def open_sessions(self) -> list[str]:
        return [sid for sid in self._states if self.is_open(sid)]

    def seconds_until_close(self, session_id: str) -> float:
        settings = get_settings()
        state = self._states.get(session_id)
        if state is None or state.tripped_at is None:
            return 0.0
        elapsed = time.time() - state.tripped_at
        return max(0.0, settings.breaker_cooldown_seconds - elapsed)


breaker = CircuitBreaker()
