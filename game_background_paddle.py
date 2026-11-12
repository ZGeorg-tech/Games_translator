from __future__ import annotations

import time
from typing import Any


PIPELINE_NAME = "sequential_full_paddle"


class GameBackgroundPaddleState:
    """Cadence state for the production game-background Paddle pipeline."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.next_run_monotonic = 0.0
        self.last_normalized_text = ""
        self.last_result_class = ""
        self.last_duration_ms = 0.0
        self.last_start_monotonic = 0.0
        self.last_finish_monotonic = 0.0
        self.pipeline_marker_logged = False

    def ready(self, now: float | None = None) -> tuple[bool, float]:
        current = time.monotonic() if now is None else float(now)
        remaining_ms = max(0.0, self.next_run_monotonic - current) * 1000.0
        return remaining_ms <= 0.0, remaining_ms

    def mark_pipeline_active(self) -> bool:
        if self.pipeline_marker_logged:
            return False
        self.pipeline_marker_logged = True
        return True

    def started(self, now: float | None = None) -> None:
        self.last_start_monotonic = time.monotonic() if now is None else float(now)

    def finished(
        self,
        normalized_text: str,
        duration_ms: float,
        error: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        current = time.monotonic() if now is None else float(now)
        normalized = str(normalized_text or "")
        if error:
            result_class, delay_ms = "error", 500.0
        elif not normalized:
            result_class, delay_ms = "empty", 300.0
        elif normalized == self.last_normalized_text:
            result_class, delay_ms = "same", 250.0
        else:
            result_class, delay_ms = "new", 0.0
        self.last_finish_monotonic = current
        self.last_duration_ms = max(0.0, float(duration_ms))
        self.last_result_class = result_class
        if normalized:
            self.last_normalized_text = normalized
        self.next_run_monotonic = current + delay_ms / 1000.0
        return {"result_class": result_class, "backoff_ms": delay_ms}
