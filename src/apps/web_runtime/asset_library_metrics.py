"""Small bounded latency window for Asset Library structured logs."""

from __future__ import annotations

import math
import threading
from collections import deque
from typing import Final


class RollingLatencyMetrics:
    """Record endpoint latency without labels or workspace-sensitive content."""

    def __init__(self, *, capacity: int = 512):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._samples: deque[float] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    @staticmethod
    def _percentile(values: list[float], percentile: int) -> float:
        index = max(0, math.ceil((percentile / 100) * len(values)) - 1)
        return round(values[index], 3)

    def observe(self, elapsed_ms: float) -> dict[str, float | int]:
        value = max(0.0, float(elapsed_ms))
        with self._lock:
            self._samples.append(value)
            values = sorted(self._samples)
        return {
            "samples": len(values),
            "p50_ms": self._percentile(values, 50),
            "p95_ms": self._percentile(values, 95),
            "p99_ms": self._percentile(values, 99),
        }


ASSET_LIBRARY_FEED_LATENCY: Final = RollingLatencyMetrics()
