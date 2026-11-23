"""Text sounds: one soft blip per burst of text. On Windows it plays a real sound; anywhere else (for example
over SSH) it can only ring the terminal bell, which is whatever your terminal makes of it."""
from __future__ import annotations

import io
import struct
import time
import wave


def _blip(freq: float, ms: int = 28, rate: int = 22050) -> bytes:
    n = int(rate * ms / 1000)
    frames = bytearray()
    for i in range(n):
        env = (1 - i / n) ** 1.6 * min(1.0, i / 90)
        tri = 2 * abs(2 * ((i * freq / rate) % 1) - 1) - 1
        frames += struct.pack("<h", int(tri * env * 5200))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


class Sound:
    def __init__(self) -> None:
        self.on = False
        self._last = 0.0
        self._n = 0
        self._ws = None
        try:
            import winsound
            self._ws = winsound
            self._waves = [_blip(f) for f in (170, 190, 210)]
        except ImportError:
            pass

    def blip(self, app) -> None:
        if not self.on:
            return
        now = time.monotonic()
        if now - self._last < (0.05 if self._ws else 0.12):
            return
        self._last = now
        self._n += 1
        if self._ws:
            try:
                self._ws.PlaySound(self._waves[self._n % 3], self._ws.SND_MEMORY | self._ws.SND_ASYNC)
            except Exception:
                pass
        else:
            app.bell()
