import threading
import time

class TokenBucket:
    """Thread-safe token bucket: refill `rate` tokens per second, capped at `capacity`."""
    def __init__(self, rate: float, capacity: int):
        self.rate = float(rate)
        self.capacity = float(capacity)
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: int = 1) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                self._last = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                wait = deficit / self.rate
            time.sleep(wait)
