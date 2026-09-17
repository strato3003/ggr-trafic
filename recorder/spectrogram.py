"""Waterfall USB (0–2,7 kHz) figé à la fin du WAV — même grille que mixer.js."""

from __future__ import annotations

import array
import logging
import math
import wave
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)

TIME = 256
FREQ = 128
FFT = 512
FMAX_HZ = 2700.0
SCALE = 1.0 / 32768.0


def png_name(channel_id: str) -> str:
    return f"waterfall-{channel_id}.png"


def _kiwi_color(t: float) -> tuple[int, int, int]:
    u = max(0.0, min(1.0, t))
    if u < 0.13:
        k = u / 0.13
        return (0, 0, int(8 + 72 * k))
    if u < 0.32:
        k = (u - 0.13) / 0.19
        return (0, int(8 + 28 * k), int(80 + 145 * k))
    if u < 0.5:
        k = (u - 0.32) / 0.18
        return (0, int(36 + 184 * k), int(225 - 55 * k))
    if u < 0.68:
        k = (u - 0.5) / 0.18
        return (int(16 + 48 * k), int(220 - 10 * k), int(170 - 152 * k))
    if u < 0.84:
        k = (u - 0.68) / 0.16
        return (int(64 + 176 * k), int(210 + 30 * k), 18)
    k = (u - 0.84) / 0.16
    return (int(240 + 15 * k), int(240 + 15 * k), int(18 + 237 * k))


def _fft_mag(src: array.array, offset: int, n: int, window: list[float], scale: float) -> list[float]:
    re = [0.0] * n
    im = [0.0] * n
    nsrc = len(src)
    for i in range(n):
        j = offset + i
        s = src[j] if 0 <= j < nsrc else 0
        re[i] = (s * scale) * window[i]
    j = 0
    for i in range(n):
        if i < j:
            re[i], re[j] = re[j], re[i]
        m = n >> 1
        while m >= 1 and j >= m:
            j -= m
            m >>= 1
        j += m
    size = 2
    while size <= n:
        half = size >> 1
        step = (-2.0 * math.pi) / size
        for i in range(0, n, size):
            for k in range(half):
                ang = step * k
                cr = math.cos(ang)
                ci = math.sin(ang)
                ur = re[i + k]
                ui = im[i + k]
                vr = re[i + k + half]
                vi = im[i + k + half]
                tr = cr * vr - ci * vi
                ti = cr * vi + ci * vr
                re[i + k] = ur + tr
                im[i + k] = ui + ti
                re[i + k + half] = ur - tr
                im[i + k + half] = ui - ti
        size <<= 1
    return [math.hypot(re[k], im[k]) for k in range(n // 2)]


def _read_mono16(path: Path) -> tuple[array.array, int] | None:
    try:
        with wave.open(str(path), "rb") as wf:
            nch = wf.getnchannels()
            width = wf.getsampwidth()
            sr = wf.getframerate() or 12000
            n = wf.getnframes()
            raw = wf.readframes(n)
    except (OSError, wave.Error):
        return None
    if width != 2 or nch < 1 or not raw:
        return None
    samples = array.array("h")
    try:
        samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
    except (ValueError, OverflowError):
        return None
    if nch > 1:
        samples = array.array("h", (samples[i] for i in range(0, len(samples), nch)))
    if len(samples) < FFT:
        return None
    return samples, int(sr)


def write_waterfall(wav: Path, dest: Path) -> bool:
    """Calcule le spectrogramme USB 256×128 et écrit un PNG Kiwi. Idempotent si déjà à jour."""
    if dest.is_file() and dest.stat().st_size > 128:
        if (not wav.is_file()) or dest.stat().st_mtime >= wav.stat().st_mtime - 0.05:
            return True
    if not wav.is_file() or wav.stat().st_size <= 64:
        return False
    parsed = _read_mono16(wav)
    if parsed is None:
        return False
    samples, sr = parsed
    n = len(samples)
    nyquist = sr / 2.0
    f_max = min(FMAX_HZ, nyquist)
    bin_max = max(2, int((f_max / nyquist) * (FFT / 2)))
    last = max(FFT - 1, 1)
    window = [0.5 * (1.0 - math.cos((2.0 * math.pi * i) / last)) for i in range(FFT)]
    img = [-120.0] * (TIME * FREQ)
    for t in range(TIME):
        start = min(n - FFT, int((t / TIME) * (n - FFT)))
        mag = _fft_mag(samples, start, FFT, window, SCALE)
        row = t * FREQ
        for f in range(FREQ):
            b = 1 + int((f / FREQ) * (bin_max - 1))
            img[row + f] = 20.0 * math.log10(mag[b] + 1e-9)
    step = max(1, len(img) // 4096)
    sample = sorted(img[i] for i in range(0, len(img), step))
    lo = sample[int(len(sample) * 0.25)] if sample else -80.0
    hi = sample[int(len(sample) * 0.99)] if sample else -20.0
    hi = max(hi if math.isfinite(hi) else -20.0, lo + 12.0)
    span = max(8.0, hi - lo)
    for i, v in enumerate(img):
        u = max(0.0, min(1.0, (v - lo) / span))
        img[i] = u**0.72
    buf = bytearray(TIME * FREQ * 3)
    off = 0
    for y in range(FREQ):
        f = FREQ - 1 - y
        for x in range(TIME):
            r, g, b = _kiwi_color(img[x * FREQ + f])
            buf[off] = r
            buf[off + 1] = g
            buf[off + 2] = b
            off += 3
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".png.tmp")
    Image.frombytes("RGB", (TIME, FREQ), bytes(buf)).save(tmp, "PNG", optimize=True)
    tmp.replace(dest)
    return dest.is_file() and dest.stat().st_size > 128


def write_channel_waterfall(session_dir: Path, channel_id: str, wav: Path) -> str | None:
    dest = session_dir / png_name(str(channel_id or "tx"))
    try:
        if write_waterfall(wav, dest):
            return dest.name
    except Exception:
        log.exception("Waterfall USB %s", channel_id)
    return None
