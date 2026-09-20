"""Muxage ffmpeg : waterfall WebM + WAV Kiwi → MP4 H.264 / AAC + vignette.

Pistes mixer : MP3 48 kbit/s (sinon M4A/AAC) après le WAV, pour la Lecture HTML5
sur téléphone / Windows / macOS / Linux. Le WAV 12 kHz reste l’archive.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# USB voix ~0–2,7 kHz : 24 kHz MPEG-2, plus compatible que du MP3 12 kHz (MPEG-2.5).
_MIX_RATE = 24000
_MIX_BITRATE = "48k"
_encoder: tuple[str, str] | None = None  # codec ffmpeg, suffixe .mp3|.m4a


def _ffmpeg() -> str:
    bin_path = shutil.which("ffmpeg")
    if not bin_path:
        raise RuntimeError("ffmpeg introuvable dans le PATH")
    return bin_path


def mux_cmd(
    ffmpeg: str,
    video_webm: Path,
    dest_mp4: Path,
    *,
    audio_wav: Path | None = None,
    audio_delay_s: float = 0.0,
) -> list[str]:
    """Construit la ligne ffmpeg. -itsoffset retarde l’audio (intro WebM blanche)."""
    cmd = [ffmpeg, "-y", "-i", str(video_webm)]
    if audio_wav is not None:
        if audio_delay_s > 0.001:
            cmd += ["-itsoffset", f"{audio_delay_s:.3f}"]
        cmd += ["-i", str(audio_wav), "-shortest", "-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += ["-an"]
    cmd += [
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-movflags",
        "+faststart",
        str(dest_mp4),
    ]
    return cmd


def mux_screencast(
    video_webm: Path,
    audio_wav: Path,
    dest_mp4: Path,
    *,
    audio_delay_s: float = 0.0,
) -> Path | None:
    if not video_webm.exists():
        return None
    dest_mp4.parent.mkdir(parents=True, exist_ok=True)
    has_wav = audio_wav.is_file() and audio_wav.stat().st_size > 64
    cmd = mux_cmd(
        _ffmpeg(),
        video_webm,
        dest_mp4,
        audio_wav=audio_wav if has_wav else None,
        audio_delay_s=audio_delay_s,
    )
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=1200)
        return dest_mp4
    except subprocess.TimeoutExpired:
        log.warning("ffmpeg mux : timeout 20 min")
        return None
    except subprocess.CalledProcessError as exc:
        log.warning("ffmpeg mux : %s", exc.stderr[-400:] if exc.stderr else exc)
        return None


def thumbnail(video: Path, dest_jpg: Path, at_s: int = 45) -> Path | None:
    if not video.exists():
        return None
    cmd = [
        _ffmpeg(),
        "-y",
        "-ss",
        str(at_s),
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-q:v",
        "4",
        str(dest_jpg),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return dest_jpg
    except subprocess.CalledProcessError:
        return None


def _audio_encoder() -> tuple[str, str] | None:
    """libmp3lame → .mp3 (le plus universel en <audio>) ; sinon AAC → .m4a."""
    global _encoder
    if _encoder is not None:
        return _encoder
    try:
        ffmpeg = _ffmpeg()
        out = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return None
    text = out.stdout or ""
    if "libmp3lame" in text:
        _encoder = ("libmp3lame", ".mp3")
    elif " aac " in f" {text} ":
        _encoder = ("aac", ".m4a")
    else:
        return None
    return _encoder


def mixer_play_path(wav: Path) -> Path | None:
    """Fichier compressé déjà présent à côté du WAV."""
    stem = wav.with_suffix("")
    for ext in (".mp3", ".m4a"):
        path = Path(str(stem) + ext)
        if path.is_file() and path.stat().st_size > 64:
            return path
    return None


def encode_mixer_play(wav: Path) -> Path | None:
    """Encode le WAV mixer en MP3 (ou M4A). Idempotent, hors record live."""
    if not wav.is_file() or wav.stat().st_size <= 64:
        return None
    existing = mixer_play_path(wav)
    if existing is not None and existing.stat().st_mtime >= wav.stat().st_mtime - 0.05:
        return existing
    enc = _audio_encoder()
    if enc is None:
        return None
    codec, ext = enc
    dest = wav.with_suffix(ext)
    cmd = [
        _ffmpeg(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(wav),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(_MIX_RATE),
        "-c:a",
        codec,
        "-b:a",
        _MIX_BITRATE,
    ]
    if codec == "aac":
        cmd += ["-movflags", "+faststart"]
    cmd.append(str(dest))
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        log.warning("ffmpeg mixer %s : timeout", wav.name)
        dest.unlink(missing_ok=True)
        return None
    except subprocess.CalledProcessError as exc:
        log.warning("ffmpeg mixer %s : %s", wav.name, (exc.stderr or str(exc))[-300:])
        dest.unlink(missing_ok=True)
        return None
    if dest.is_file() and dest.stat().st_size > 64:
        log.info("Mixer %s → %s (%s)", wav.name, dest.name, codec)
        return dest
    dest.unlink(missing_ok=True)
    return None
