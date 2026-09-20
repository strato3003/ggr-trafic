"""Tests géodésie, tracker Yellowbrick et classement KiwiSDR."""

from __future__ import annotations

import json
import struct
from datetime import datetime, timezone

from recorder.fleet import parse_positions3, _heading_deg, _team_colour, _sog_kn, _nm, _gps_at, parse_yb_grib2, _wind_at
from recorder.geo import centroid, fmt_latlon, haversine_km, initial_bearing
from recorder.kiwi_audio import ImaAdpcmDecoder, wav_from_snd_frames, _pcm_from_snd, _ws_uris
from recorder.kiwi_list import parse_kiwi_directory, score_kiwi
from recorder.session import next_recording_utc, next_vacation_utc, vacation_id


def test_haversine_les_sables_to_self():
    assert haversine_km(46.5025, -1.7888, 46.5025, -1.7888) < 0.01


def test_haversine_atlantic_order():
    # Les Sables → cap Finisterre ~ 550–700 km
    d = haversine_km(46.50, -1.79, 42.88, -9.27)
    assert 500 < d < 800


def test_initial_bearing_cardinals():
    assert abs(initial_bearing(0.0, 0.0, 1.0, 0.0) - 0.0) < 0.5
    assert abs(initial_bearing(0.0, 0.0, 0.0, 1.0) - 90.0) < 0.5


def test_centroid_and_fmt():
    c = centroid([(46.5, -1.8), (46.6, -1.7)])
    assert c is not None
    assert 46.5 < c[0] < 46.6
    assert -1.8 < c[1] < -1.7
    label = fmt_latlon(*c)
    assert label == "46.550°N 1.750°W"


def test_fmt_latlon_uses_hemispheres():
    assert fmt_latlon(46.55, -1.75) == "46.550°N 1.750°W"
    assert fmt_latlon(-33.9, 18.4).endswith("E")


def test_vacation_id_utc():
    dt = datetime(2026, 9, 7, 17, 50, tzinfo=timezone.utc)
    assert vacation_id(dt) == "2026-09-07T1750Z"


def test_next_vacation_before_slot():
    cfg = {"schedule": {"time_utc": "18:00", "lead_minutes": 10}}
    now = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
    nxt = next_vacation_utc(cfg, now)
    assert nxt.hour == 17 and nxt.minute == 50
    assert nxt.date() == now.date()


def test_next_recording_picks_buddy_in_the_morning():
    cfg = {
        "schedule": {"time_utc": "18:00", "lead_minutes": 1},
        "buddy": {"time_utc": "12:00", "lead_minutes": 1, "enabled": True},
    }
    morning = datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc)
    nxt = next_recording_utc(cfg, morning)
    assert nxt.hour == 11 and nxt.minute == 59
    afternoon = datetime(2026, 9, 17, 15, 0, tzinfo=timezone.utc)
    nxt = next_recording_utc(cfg, afternoon)
    assert nxt.hour == 17 and nxt.minute == 59
    off = dict(cfg)
    off["buddy"] = {**cfg["buddy"], "enabled": False}
    nxt = next_recording_utc(off, morning)
    assert nxt.hour == 17 and nxt.minute == 59


def test_scheduler_cron_is_1759_with_one_minute_lead():
    from recorder.scheduler import _lead

    assert _lead({"schedule": {"time_utc": "18:00", "lead_minutes": 1}}) == (17, 59)


def test_sog_and_gps_at_from_fixes():
    moments = [
        {"lat": 46.50, "lon": -1.80, "at": 1_789_286_422 - 3600},
        {"lat": 46.56, "lon": -1.80, "at": 1_789_286_422},
    ]
    sog = _sog_kn(moments)
    assert sog is not None
    assert 3.0 < sog < 4.0
    assert _gps_at(1_789_286_422) == "2026-09-13 08:00 TU"
    assert _nm(1852) == 1.0
    assert _nm(None) is None


def test_yb_grib2_nearest_wind():
    buf = bytearray()
    buf.append(1)
    buf += struct.pack(">I", 1)
    buf.append(1)
    buf += struct.pack(">i", 0)
    buf += struct.pack(">i", int(46.5 * 1e5))
    buf += struct.pack(">i", int(-1.8 * 1e5))
    buf.append(100)
    buf += struct.pack(">H", 1)
    buf += struct.pack(">I", 1)
    buf.append(15)
    buf.append(40)
    grids = parse_yb_grib2(bytes(buf), 1_788_697_800)
    assert len(grids) == 1
    wind = _wind_at(grids, 46.5, -1.8, 1_788_697_800)
    assert wind == (12.0, 30)


def test_heading_from_two_fixes():
    moments = [
        {"lat": 46.50, "lon": -1.80, "at": 1},
        {"lat": 46.51, "lon": -1.80, "at": 2},
    ]
    h = _heading_deg(moments)
    assert h is not None
    assert abs(h - 0.0) < 2.0
    assert _team_colour({"colour": "FF3300"}) == "#FF3300"


def test_parse_positions3_single_fix():
    buf = bytearray()
    buf.append(0)
    buf += struct.pack(">I", 1_788_697_800)
    buf += struct.pack(">H", 6)  # Damien Guillou
    buf += struct.pack(">H", 1)
    buf += struct.pack(">I", 0)
    buf += struct.pack(">i", 4_650_250)
    buf += struct.pack(">i", -178_880)
    teams = parse_positions3(bytes(buf))
    assert len(teams) == 1
    assert teams[0]["id"] == 6
    fix = teams[0]["moments"][0]
    assert abs(fix["lat"] - 46.5025) < 1e-6
    assert abs(fix["lon"] + 1.7888) < 1e-6


def test_parse_kiwi_directory_trailing_comma():
    raw = """
var kiwisdr_com =
[
  {
    "id": "abc",
    "name": "test kiwi",
    "url": "http://example.invalid:8073"
  },
];
"""
    rows = parse_kiwi_directory(raw)
    assert len(rows) == 1
    assert rows[0]["id"] == "abc"


def test_score_prefers_closer_higher_snr():
    close = {"distance_km": 400, "snr_hf": 30, "free_slots": 4}
    far = {"distance_km": 8000, "snr_hf": 10, "free_slots": 1}
    assert score_kiwi(close, 0, 0) > score_kiwi(far, 0, 0)


def test_kiwi_snd_uri_uses_ws_kiwi_path():
    uris = _ws_uris({"host": "g3sdr.com", "port": 8074, "https": False})
    assert uris[0].startswith("ws://g3sdr.com:8074/ws/kiwi/")
    assert uris[0].endswith("/SND")
    assert "/ws/kiwi/" not in uris[1]


def test_pcm_from_snd_uncompressed_be():
    # flags, seq(3), smeter(2) puis un échantillon s16be = 0x0100
    body = bytes([0, 0, 0, 0, 0, 0x32, 0x00, 0x01, 0x00])
    pcm = _pcm_from_snd(body, ImaAdpcmDecoder())
    assert pcm == struct.pack("<h", 256)


def test_ima_adpcm_two_samples_per_byte():
    dec = ImaAdpcmDecoder()
    pcm = dec.decode(bytes([0x00]))
    assert len(pcm) == 4


def test_wav_from_snd_frames(tmp_path):
    body = bytes([0, 0, 0, 0, 0, 0x32, 0x00, 0x01, 0x00])
    dest = tmp_path / "t.wav"
    assert wav_from_snd_frames([b"SND" + body], dest)
    assert dest.stat().st_size > 44


def test_amplify_pcm_raises_quiet_peak():
    from recorder.kiwi_audio import amplify_pcm

    quiet = struct.pack("<h", 2000) * 64
    out = amplify_pcm(quiet)
    peak = max(abs(s) for s in struct.unpack("<" + "h" * 64, out))
    assert peak >= 20000


def test_kiwi_agc_uses_web_threshold():
    from recorder.kiwi_audio import _send_rx_setup
    import inspect

    src = inspect.getsource(_send_rx_setup)
    assert "thresh=-20" in src
    assert "thresh=-100" not in src


def test_recover_orphaned_clears_lock_and_running(tmp_path):
    import json

    from recorder.session import recover_orphaned

    cfg = {"storage": {"data_dir": str(tmp_path)}}
    lock = tmp_path / ".recording.lock"
    lock.write_text("2026-09-08T17:50:00+00:00", encoding="utf-8")
    folder = tmp_path / "vacations" / "2026-09-08T1750Z"
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text(
        json.dumps({"id": "2026-09-08T1750Z", "status": "running", "channels": []}),
        encoding="utf-8",
    )
    n = recover_orphaned(cfg)
    assert n >= 2
    assert not lock.exists()
    saved = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert saved["status"] == "error"
    assert "interrompu" in saved["error"]


def test_delete_vacation_removes_folder(tmp_path):
    from app.store import delete_vacation

    cfg = {"storage": {"data_dir": str(tmp_path)}}
    folder = tmp_path / "vacations" / "2026-09-11T0900Z-qrg"
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text('{"id": "2026-09-11T0900Z-qrg", "status": "complete"}', encoding="utf-8")
    (folder / "audio-tx.wav").write_bytes(b"RIFF")
    assert delete_vacation("2026-09-11T0900Z-qrg", cfg) is None
    assert not folder.exists()
    assert delete_vacation("2026-09-11T0900Z-qrg", cfg) == "introuvable"
    assert delete_vacation("../etc", cfg) == "identifiant invalide"


def test_delete_vacation_refuses_running(tmp_path):
    from app.store import delete_vacation

    cfg = {"storage": {"data_dir": str(tmp_path)}}
    (tmp_path / ".recording.lock").write_text("now", encoding="utf-8")
    folder = tmp_path / "vacations" / "2026-09-11T0910Z"
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text(
        '{"id": "2026-09-11T0910Z", "status": "running"}', encoding="utf-8"
    )
    assert delete_vacation("2026-09-11T0910Z", cfg) == "enregistrement en cours"
    assert folder.exists()


def test_recording_state_idle_without_lock(tmp_path):
    from app.store import recording_state

    cfg = {"storage": {"data_dir": str(tmp_path)}}
    st = recording_state(cfg)
    assert st["active"] is False
    assert st["ends_at"] is None


def test_recording_state_buddy_countdown(tmp_path):
    import json
    from datetime import datetime, timedelta, timezone

    from app.store import recording_state

    started = datetime(2026, 9, 17, 11, 59, tzinfo=timezone.utc)
    cfg = {
        "storage": {"data_dir": str(tmp_path)},
        "buddy": {"duration_minutes": 15},
        "schedule": {"duration_minutes": 10},
    }
    (tmp_path / ".recording.lock").write_text(started.isoformat(), encoding="utf-8")
    folder = tmp_path / "vacations" / "2026-09-17T1159Z-buddy"
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text(
        json.dumps(
            {
                "id": "2026-09-17T1159Z-buddy",
                "status": "running",
                "reason": "buddy",
                "started_at": started.isoformat(),
                "duration_minutes": 15,
            }
        ),
        encoding="utf-8",
    )
    st = recording_state(cfg)
    assert st["active"] is True
    assert st["label"] == "Buddy call"
    assert st["id"] == "2026-09-17T1159Z-buddy"
    ends = datetime.fromisoformat(st["ends_at"])
    assert ends == started + timedelta(minutes=15)
    assert st["remaining_hms"].count(":") == 2


def test_recording_state_falls_back_to_mtime_if_lock_unreadable(tmp_path):
    from app.store import recording_state

    cfg = {"storage": {"data_dir": str(tmp_path)}, "schedule": {"duration_minutes": 10}}
    (tmp_path / ".recording.lock").write_text("pas-une-date", encoding="utf-8")
    st = recording_state(cfg)
    assert st["active"] is True
    assert st["label"] == "Enregistrement"
    assert st["ends_at"]


def test_channel_place_and_globe_keeps_mute_channels():
    from app.store import channel_place, globe_vacation

    assert channel_place({"kiwi": {"loc": "Amarante, Portugal"}}) == "Amarante, Portugal"
    assert (
        channel_place({"kiwi": {"name": "0-30 MHz SDR, CT2HMR, Amarante, Portugal"}})
        == "Amarante, Portugal"
    )
    assert (
        channel_place({"kiwi": {"name": "SAL 30 | Montmorillon 86500 FRANCE"}})
        == "Montmorillon 86500 FRANCE"
    )
    card = globe_vacation(
        {
            "id": "2026-09-13T1159Z-buddy",
            "reason": "buddy",
            "started_at": "2026-09-13T11:59:00+00:00",
            "fleet": {"lat": 40.0, "lon": -10.0, "fmt": "40.000°N 10.000°W"},
            "channels": [
                {
                    "id": "nvis-alt",
                    "freq_khz": 6516.0,
                    "audio": "audio-nvis-alt.wav",
                    "kiwi": {"name": "0-30 MHz SDR, CT2HMR, Amarante, Portugal"},
                    "site_label": "proche / NVIS",
                },
                {
                    "id": "hop-alt",
                    "freq_khz": 6516.0,
                    "kiwi": {"name": "SAL 30 | Montmorillon 86500 FRANCE"},
                    "site_label": "saut 1 hop",
                },
            ],
        }
    )
    assert card["is_buddy"] is True
    assert card["sdrs"] == 1
    assert [c["has_audio"] for c in card["channels"]] == [True, False]
    assert card["channels"][0]["place"] == "Amarante, Portugal"
    assert card["channels"][1]["place"] == "Montmorillon 86500 FRANCE"
    assert card["channels"][0]["waterfall"] == ""
    assert card["channels"][1]["waterfall"] == ""


def test_mixer_tracks_include_silent_channels():
    from app.store import _decorate

    meta = _decorate(
        {
            "channels": [
                {
                    "id": "nvis-main",
                    "audio": "audio-nvis-main.wav",
                    "freq_khz": 4483.0,
                    "kiwi": {"loc": "Amarante, Portugal"},
                    "waterfall": "waterfall-nvis-main.png",
                },
                {"id": "far-alt", "freq_khz": 6516.0, "kiwi": {"loc": "Borås"}},
            ]
        }
    )
    assert [t["id"] for t in meta["mixer_tracks"]] == ["nvis-main", "far-alt"]
    assert meta["mixer_tracks"][0]["place"] == "Amarante, Portugal"
    assert meta["mixer_tracks"][0]["src"] == "audio-nvis-main.wav"
    assert meta["mixer_tracks"][0]["wav"] == "audio-nvis-main.wav"
    assert meta["mixer_tracks"][0]["has_audio"] is True
    assert meta["mixer_tracks"][0]["waterfall"] == "waterfall-nvis-main.png"
    assert meta["mixer_tracks"][1]["has_audio"] is False
    assert not meta["mixer_tracks"][1]["src"]
    assert not meta["mixer_tracks"][1]["waterfall"]
    assert meta["sdrs"] == 1


def test_mixer_tracks_prefers_mp3(tmp_path):
    from app.store import _decorate

    (tmp_path / "audio-nvis-main.mp3").write_bytes(b"ID3" + b"\x00" * 80)
    meta = _decorate(
        {
            "channels": [
                {
                    "id": "nvis-main",
                    "audio": "audio-nvis-main.wav",
                    "freq_khz": 4483.0,
                    "kiwi": {"loc": "Amarante, Portugal"},
                }
            ]
        },
        tmp_path,
    )
    assert meta["mixer_tracks"][0]["src"] == "audio-nvis-main.mp3"
    assert meta["mixer_tracks"][0]["wav"] == "audio-nvis-main.wav"
    assert meta["channels"][0]["play"] == "audio-nvis-main.mp3"


def test_encode_mixer_play_keeps_wav(tmp_path):
    import shutil

    import pytest

    from recorder import postprocess

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg absent")
    postprocess._encoder = None
    wav = tmp_path / "audio-tx.wav"
    _write_tone_wav(wav, seconds=0.4)
    out = postprocess.encode_mixer_play(wav)
    if out is None:
        pytest.skip("pas d'encodeur mp3/aac dans ffmpeg")
    assert out.suffix.lower() in {".mp3", ".m4a"}
    assert out.stat().st_size > 64
    assert wav.is_file()
    again = postprocess.encode_mixer_play(wav)
    assert again == out


def test_finalize_pending_promotes_orphan_with_audio(tmp_path):
    import json

    from recorder.session import finalize_pending_sessions

    cfg = {"storage": {"data_dir": str(tmp_path)}}
    folder = tmp_path / "vacations" / "2026-09-09T1750Z"
    folder.mkdir(parents=True)
    wav = folder / "audio-tx.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 80)
    (folder / "metadata.json").write_text(
        json.dumps(
            {
                "id": "2026-09-09T1750Z",
                "status": "error",
                "error": "Enregistrement interrompu (processus arrêté avant la fin)",
                "channels": [{"id": "tx", "audio_file": "audio-tx.wav"}],
            }
        ),
        encoding="utf-8",
    )
    assert finalize_pending_sessions(cfg) >= 1
    saved = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert saved["status"] == "complete"
    assert saved["channels"][0]["audio"] == "audio-tx.wav"
    assert "waterfall" not in saved["channels"][0]
    assert "error" not in saved


def _write_tone_wav(path, sr=12000, seconds=0.6, freq=800.0):
    import math
    import wave

    n = int(sr * seconds)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        frames = b"".join(
            struct.pack("<h", int(16000 * math.sin(2 * math.pi * freq * i / sr))) for i in range(n)
        )
        wf.writeframes(frames)


def test_waterfall_png_from_usb_tone(tmp_path):
    from PIL import Image

    from recorder.spectrogram import FREQ, TIME, write_channel_waterfall, write_waterfall

    wav = tmp_path / "audio-tx.wav"
    _write_tone_wav(wav)
    name = write_channel_waterfall(tmp_path, "tx", wav)
    assert name == "waterfall-tx.png"
    png = tmp_path / name
    img = Image.open(png)
    assert img.size == (TIME, FREQ)
    extrema = img.convert("RGB").getextrema()
    assert max(hi for _lo, hi in extrema) > 80
    mtime = png.stat().st_mtime
    assert write_waterfall(wav, png) is True
    assert png.stat().st_mtime == mtime


def test_waterfall_rejects_stub_wav(tmp_path):
    from recorder.spectrogram import write_channel_waterfall

    wav = tmp_path / "audio-tx.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 80)
    assert write_channel_waterfall(tmp_path, "tx", wav) is None
    assert not (tmp_path / "waterfall-tx.png").exists()


def test_finalize_pending_writes_waterfall(tmp_path):
    import json

    from recorder.session import finalize_pending_sessions

    cfg = {"storage": {"data_dir": str(tmp_path)}}
    folder = tmp_path / "vacations" / "2026-09-17T1759Z"
    folder.mkdir(parents=True)
    _write_tone_wav(folder / "audio-tx.wav")
    (folder / "metadata.json").write_text(
        json.dumps(
            {
                "id": "2026-09-17T1759Z",
                "status": "complete",
                "channels": [{"id": "tx", "audio": "audio-tx.wav"}],
            }
        ),
        encoding="utf-8",
    )
    assert finalize_pending_sessions(cfg) >= 1
    saved = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert saved["channels"][0]["waterfall"] == "waterfall-tx.png"
    assert (folder / "waterfall-tx.png").is_file()


def test_hold_page_stops_if_chromium_frozen(monkeypatch):
    import asyncio
    import time

    from recorder.screencast import _hold_page

    monkeypatch.setattr("recorder.screencast._PAGE_PING_S", 0.05)
    monkeypatch.setattr("recorder.screencast._PAGE_PING_TIMEOUT_S", 0.05)

    class Frozen:
        async def evaluate(self, _expr):
            raise RuntimeError("Target closed")

    async def go():
        t0 = time.monotonic()
        await _hold_page(Frozen(), 30)
        return time.monotonic() - t0

    elapsed = asyncio.run(go())
    assert elapsed < 2.0


def test_snd_hook_batches_instead_of_per_packet():
    from recorder.screencast import SND_HOOK_JS

    assert "ggrSndBatch" in SND_HOOK_JS
    assert "ggrSndFlush" in SND_HOOK_JS
    assert "ggrSndFrame" not in SND_HOOK_JS
    assert "fromCharCode.apply" in SND_HOOK_JS


def test_mux_cmd_itsoffset_delays_audio():
    from pathlib import Path

    from recorder.postprocess import mux_cmd

    cmd = mux_cmd(
        "ffmpeg",
        Path("v.webm"),
        Path("o.mp4"),
        audio_wav=Path("a.wav"),
        audio_delay_s=6.25,
    )
    assert cmd[cmd.index("-itsoffset") + 1] == "6.250"
    assert cmd.index("-i") < cmd.index("-itsoffset") < cmd.index("a.wav")


def test_channel_audio_delay_sidecar(tmp_path):
    from recorder.session import _channel_audio_delay

    (tmp_path / "audio-tx.delay").write_text("5.5\n", encoding="utf-8")
    assert _channel_audio_delay(tmp_path, {"id": "tx"}) == 5.5
    assert _channel_audio_delay(tmp_path, {"id": "tx", "audio_delay_s": 2}) == 2.0


def test_usb_dial_from_left_edge_of_ssb_blob():
    from recorder.kiwi_wf import (
        HUNT_CF_KHZ,
        HUNT_ZOOM,
        WF_BINS,
        bin_freq_khz,
        usb_dial_from_spectrum,
    )

    bins = [40.0] * WF_BINS
    # Voix USB 300–2700 Hz au-dessus de 14 200,0 kHz.
    i0 = min(
        range(WF_BINS),
        key=lambda i: abs(bin_freq_khz(i, HUNT_ZOOM, HUNT_CF_KHZ) - 14200.3),
    )
    i1 = min(
        range(WF_BINS),
        key=lambda i: abs(bin_freq_khz(i, HUNT_ZOOM, HUNT_CF_KHZ) - 14202.7),
    )
    for i in range(min(i0, i1), max(i0, i1) + 1):
        bins[i] = 180.0
    hit = usb_dial_from_spectrum(
        bins, zoom=HUNT_ZOOM, cf_khz=HUNT_CF_KHZ, low_hz=300, high_hz=2700
    )
    assert hit is not None
    assert abs(hit["freq_khz"] - 14200.0) < 0.2
    assert 1800 <= hit["width_hz"] <= 3000
    assert hit["usb_low_hz"] == 300 and hit["usb_high_hz"] == 2700


def test_usb_dial_rejects_cw_and_wide_am():
    from recorder.kiwi_wf import (
        HUNT_CF_KHZ,
        HUNT_ZOOM,
        WF_BINS,
        bin_freq_khz,
        usb_dial_from_spectrum,
    )

    def fill(f0: float, f1: float) -> list[float]:
        bins = [40.0] * WF_BINS
        i0 = min(range(WF_BINS), key=lambda i: abs(bin_freq_khz(i, HUNT_ZOOM, HUNT_CF_KHZ) - f0))
        i1 = min(range(WF_BINS), key=lambda i: abs(bin_freq_khz(i, HUNT_ZOOM, HUNT_CF_KHZ) - f1))
        for i in range(min(i0, i1), max(i0, i1) + 1):
            bins[i] = 180.0
        return bins

    assert usb_dial_from_spectrum(fill(14220.0, 14220.4), zoom=HUNT_ZOOM, cf_khz=HUNT_CF_KHZ) is None
    assert usb_dial_from_spectrum(fill(14200.0, 14208.0), zoom=HUNT_ZOOM, cf_khz=HUNT_CF_KHZ) is None


def test_usb_dial_none_on_flat_noise():
    from recorder.kiwi_wf import HUNT_CF_KHZ, HUNT_ZOOM, WF_BINS, usb_dial_from_spectrum

    assert usb_dial_from_spectrum([50.0] * WF_BINS, zoom=HUNT_ZOOM, cf_khz=HUNT_CF_KHZ) is None


def test_parse_wf_line_uncompressed():
    from recorder.kiwi_wf import WF_BINS, _parse_wf_line

    payload = bytes((i * 3) % 256 for i in range(WF_BINS))
    msg = b"W/F" + b"\x00" + struct.pack("<III", 0, 0, 7) + payload
    assert _parse_wf_line(msg) == list(payload)


def test_wf_uri_uses_ws_kiwi_path():
    from recorder.kiwi_audio import _ws_uris

    uris = _ws_uris({"host": "g3sdr.com", "port": 8074, "https": False}, "WF")
    assert uris[0].startswith("ws://g3sdr.com:8074/ws/kiwi/")
    assert uris[0].endswith("/WF")


def test_fmt_mhz_keeps_hertz():
    from recorder.config import fmt_mhz

    assert fmt_mhz(14135.0) == "14.135"
    assert fmt_mhz(16551.0) == "16.551"
    assert fmt_mhz(12418.0) == "12.418"
    assert fmt_mhz(16551.5) == "16.5515"


def test_parse_qrg_khz_accepts_mhz_or_khz():
    from recorder.config import parse_qrg_khz

    assert parse_qrg_khz(14.135) == 14135.0
    assert parse_qrg_khz(14135) == 14135.0
    assert parse_qrg_khz("16.551") == 16551.0
    assert parse_qrg_khz(16.551) == 16551.0
    assert parse_qrg_khz(12.418) == 12418.0


def test_zoom_for_span_covers_five_khz_window():
    from recorder.kiwi_wf import span_khz, zoom_for_span

    z = zoom_for_span(12.5)
    assert z == 11
    assert span_khz(z) >= 12.5
    assert span_khz(z + 1) < 12.5


def test_assign_vacation_kiwis_geo_sites():
    from recorder.kiwi_list import assign_vacation_kiwis

    def kiwi(kid, name, lat, lon, snr=20.0, free=3):
        return {
            "id": kid,
            "name": name,
            "lat": lat,
            "lon": lon,
            "snr_hf": snr,
            "free_slots": free,
            "url": f"http://{kid}.invalid",
        }

    fleet_lat, fleet_lon = -30.0, 10.0
    pool = [
        kiwi("loud-far", "singapore", 1.35, 103.82, snr=40, free=8),
        kiwi("near-a", "walvis", -29.5, 14.5, snr=12, free=3),
        kiwi("near-b", "cape", -33.92, 18.42, snr=18, free=2),
        kiwi("fr", "les-sables", 46.5025, -1.7888, snr=22, free=4),
        kiwi("th", "papeete", -17.5350, -149.5697, snr=16, free=2),
        kiwi("nz", "auckland", -36.85, 174.76, snr=30, free=4),
    ]
    cfg = {
        "sdr": {
            "min_free_slots": 2,
            "sites": {
                "france": {"lat": 46.5025, "lon": -1.7888, "label": "France", "radius_km": 1500},
                "tahiti": {"lat": -17.5350, "lon": -149.5697, "label": "Tahiti", "radius_km": 2500},
            },
        }
    }
    roles = assign_vacation_kiwis(pool, fleet_lat=fleet_lat, fleet_lon=fleet_lon, cfg=cfg)
    # Atlantique (ouest du cap) : émetteur F6KUF + flotte, pas Tahiti.
    assert roles["tx"]["id"] == "fr"
    assert roles["tx_fleet"]["id"] == "near-a"
    assert roles["fleet"]["id"] == "near-b"
    assert roles["tahiti"]["id"] == "th"
    assert "loud-far" not in {r["id"] for r in roles.values()}
    assert "nz" not in {r["id"] for r in roles.values()}

    thin = [k for k in pool if k["id"] != "th"]
    padded = assign_vacation_kiwis(thin, fleet_lat=fleet_lat, fleet_lon=fleet_lon, cfg=cfg)
    assert "tahiti" not in padded
    assert padded["tx"]["id"] == "fr"

    indian = assign_vacation_kiwis(pool, fleet_lat=-35.0, fleet_lon=25.0, cfg=cfg)
    assert indian["tx"]["id"] == "th"
    assert indian["tx"].get("club_id") == "tahiti"


def test_ack_channels_per_site():
    from recorder.session import _channels, _pick_kiwis

    cfg = {
        "radio": {
            "tx": {"freq_khz": 14135.0, "label": "Bulletin météo F6KUF"},
            "ack": [
                {"freq_khz": 16551.0, "label": "Accusé 16,551 MHz"},
                {"freq_khz": 12418.0, "label": "Accusé 12,418 MHz"},
            ],
        },
        "sdr": {"screencast_tx": True, "screencast_ack": False},
    }
    sites = [
        {"id": "fleet", "label": "flotte"},
        {"id": "france", "label": "France"},
        {"id": "tahiti", "label": "Tahiti"},
    ]
    channels = _channels(cfg, sites)
    assert [c["id"] for c in channels] == [
        "tx",
        "tx-fleet",
        "ack1-fleet",
        "ack1-france",
        "ack1-tahiti",
        "ack2-fleet",
        "ack2-france",
        "ack2-tahiti",
    ]
    roles = {
        "tx": {"name": "k-tx"},
        "tx_fleet": {"name": "k-fleet-tx"},
        "fleet": {"name": "k-fleet"},
        "france": {"name": "k-fr"},
        "tahiti": {"name": "k-th"},
    }
    got = _pick_kiwis(roles, channels)
    assert got["tx"]["name"] == "k-tx"
    assert got["tx-fleet"]["name"] == "k-fleet-tx"
    assert got["ack1-france"]["name"] == "k-fr"
    assert got["ack2-tahiti"]["name"] == "k-th"


def test_runtime_settings_override_qrg(tmp_path, monkeypatch):
    monkeypatch.setenv("GGR_DATA_DIR", str(tmp_path))
    from recorder.config import load_config, qrg_context, save_runtime_settings

    save_runtime_settings(
        {
            "radio": {"tx": {"freq_khz": 14137.25, "qrg_tolerance_khz": 5.0}},
            "schedule": {"lead_minutes": 1, "duration_minutes": 12},
        }
    )
    qrg = qrg_context(load_config())
    assert qrg["tx_khz"] == 14137.25
    assert qrg["qrg_tolerance_khz"] == 5.0
    assert qrg["schedule_lead"] == 1
    assert qrg["duration_minutes"] == 12
    assert qrg["ack1_khz"] == 16551.0
    assert qrg["ack2_khz"] == 12418.0
    assert (tmp_path / "settings.json").is_file()


def test_parse_tx_sites_max_five_and_empty_rows():
    from recorder.config import parse_tx_sites, tx_sites_aim, tx_sites_from_cfg

    assert parse_tx_sites([]) == []
    assert parse_tx_sites([{}, {"label": "", "lat": "", "lon": ""}]) == []
    rows = parse_tx_sites(
        [
            {"label": "F6KUF", "lat": 46.5025, "lon": -1.7888},
            {"label": "  ", "lat": -33.9, "lon": 18.4},
            {"label": "A", "lat": 1, "lon": 1},
            {"label": "B", "lat": 2, "lon": 2},
            {"label": "C", "lat": 3, "lon": 3},
        ]
    )
    assert len(rows) == 5
    assert rows[0]["label"] == "F6KUF"
    assert rows[1]["label"] == "Émission 2"
    try:
        parse_tx_sites(rows + [{"label": "D", "lat": 4, "lon": 4}])
        raise AssertionError("expected max 5")
    except ValueError as exc:
        assert "maximum" in str(exc)
    aimed = tx_sites_aim({"tx_sites": rows[:1]}, 0.0, 0.0)
    assert aimed[0]["distance_km"] > 0
    assert 0 <= aimed[0]["azimuth_deg"] <= 359
    from recorder.geo import initial_bearing

    assert int(round(initial_bearing(46.5025, -1.7888, 47.5025, -1.7888))) % 360 == 0
    assert tx_sites_from_cfg({"tx_sites": "nope"}) == []


def test_qrg_context_includes_tx_sites():
    from recorder.config import qrg_context

    qrg = qrg_context(
        {
            "radio": {
                "tx": {"freq_khz": 14135.0},
                "ack": [{"freq_khz": 16551.0}, {"freq_khz": 12418.0}],
            },
            "schedule": {},
            "buddy": {},
            "tx_sites": [{"label": "F6KUF", "lat": 46.5025, "lon": -1.7888}],
        }
    )
    assert qrg["tx_sites"][0]["label"] == "F6KUF"
    assert qrg["tx_sites"][0]["lat"] == 46.5025
    assert qrg["tx_sites"][0]["lon"] == -1.7888


def test_legacy_ack_qrg_migrated_from_settings(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("GGR_DATA_DIR", str(tmp_path))
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "radio": {
                    "ack": [
                        {"freq_khz": 16551.5, "label": "Accusé 16,5515 MHz"},
                        {"freq_khz": 12418.5, "label": "Accusé 12,4185 MHz"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    from recorder.config import load_config, qrg_context

    qrg = qrg_context(load_config())
    assert qrg["ack1_khz"] == 16551.0
    assert qrg["ack2_khz"] == 12418.0
    assert qrg["ack1_mhz"] == "16.551"
    assert qrg["ack2_mhz"] == "12.418"
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["radio"]["ack"][0]["freq_khz"] == 16551.0
    assert saved["radio"]["ack"][1]["freq_khz"] == 12418.0
    assert saved["radio"]["ack"][0]["label"] == "Accusé 16,551 MHz"


def test_usb_dial_in_plus_minus_five_khz_window():
    from recorder.kiwi_wf import WF_BINS, bin_freq_khz, usb_dial_from_spectrum, zoom_for_span

    cf = 14135.0
    tol = 5.0
    high_hz = 2700
    zoom = zoom_for_span((2 * tol + high_hz / 1000.0) * 1.25)
    bins = [40.0] * WF_BINS
    # Voix USB 300–2700 Hz au-dessus de 14 138,0 kHz (QRM +3 kHz).
    i0 = min(range(WF_BINS), key=lambda i: abs(bin_freq_khz(i, zoom, cf) - 14138.3))
    i1 = min(range(WF_BINS), key=lambda i: abs(bin_freq_khz(i, zoom, cf) - 14140.7))
    for i in range(min(i0, i1), max(i0, i1) + 1):
        bins[i] = 180.0
    hit = usb_dial_from_spectrum(
        bins,
        zoom=zoom,
        cf_khz=cf,
        lo_khz=cf - tol,
        hi_khz=cf + tol + high_hz / 1000.0,
        low_hz=300,
        high_hz=high_hz,
    )
    assert hit is not None
    assert abs(hit["freq_khz"] - 14138.0) < 0.25


def test_scheduler_buddy_cron_is_1159():
    from recorder.scheduler import _buddy_lead

    assert _buddy_lead({"buddy": {"time_utc": "12:00", "lead_minutes": 1}}) == (11, 59)


def test_fmt_khz_keeps_integer_channel():
    from recorder.config import fmt_khz

    assert fmt_khz(4483.0) == "4483"
    assert fmt_khz(6516.0) == "6516"


def test_covers_freqs_not_full_span():
    from recorder.kiwi_list import _covers_freqs, _covers_hf

    bands = (3_000_000, 8_000_000)
    assert _covers_freqs(bands, [4_483_000, 6_516_000])
    assert not _covers_hf(bands, 12_000_000, 17_000_000)
    assert not _covers_freqs(bands, [14_135_000])


def test_midday_prop_prefers_nvis_on_4mhz_and_hop_on_6mhz():
    from recorder.kiwi_list import hf_midday_prop_score, hf_midday_zone

    assert hf_midday_zone(400) == "nvis"
    assert hf_midday_zone(1100) == "skip"
    assert hf_midday_zone(2000) == "hop"
    assert hf_midday_prop_score(400, 4483) > hf_midday_prop_score(1100, 4483)
    assert hf_midday_prop_score(2000, 6516) > hf_midday_prop_score(1100, 6516)


def test_buddy_aim_listed_skippers_only():
    from recorder.fleet import buddy_aim

    fleet = {
        "lat": 0.0,
        "lon": 0.0,
        "fmt": "0.000°N 0.000°E",
        "label": "flotte",
        "n_boats": 4,
        "boats": [
            {"id": 6, "name": "Damien Guillou", "lat": 40.0, "lon": -20.0},
            {"id": 13, "name": "Etienne Messikommer", "lat": 41.0, "lon": -21.0},
            {"id": 2, "name": "Louis Kerdelhue", "lat": 42.0, "lon": -22.0},
            {"id": 1, "name": "Gunnar Christensen", "lat": 10.0, "lon": 10.0},
        ],
        "source": "yellowbrick",
    }
    trio_cfg = {
        "buddy": {
            "centroid": {
                "skippers": ["Damien Guillou", "Etienne Messikommer", "Louis Kerdelhue"],
            }
        }
    }
    trio = buddy_aim(fleet, trio_cfg)
    assert trio["n_boats"] == 3
    assert 40.0 < trio["lat"] < 42.0
    # include_fleet historique : ignoré, le centroïde reste le skipper listé.
    one_cfg = {
        "buddy": {
            "centroid": {
                "skippers": ["Damien Guillou"],
                "include_fleet": True,
            }
        }
    }
    one = buddy_aim(fleet, one_cfg)
    assert one["n_boats"] == 1
    assert abs(one["lat"] - 40.0) < 0.01

    empty = buddy_aim({"lat": 46.5, "lon": -1.8, "fmt": "x", "label": "repli", "boats": []}, trio_cfg)
    assert "warning" not in empty
    mismatch = buddy_aim(
        {
            "lat": 1.0,
            "lon": 2.0,
            "fmt": "x",
            "label": "flotte",
            "boats": [{"id": 1, "name": "Autre Skipper", "lat": 10.0, "lon": 10.0}],
        },
        trio_cfg,
    )
    assert mismatch["warning"] == "Skippers du centroïde introuvables — repli flotte"


def test_assign_buddy_kiwis_nvis_and_hop_not_just_nearest():
    from recorder.kiwi_list import assign_buddy_kiwis, score_buddy_kiwi

    def kiwi(kid, lat, lon, dist, snr=20, free=3):
        row = {
            "id": kid,
            "name": kid,
            "lat": lat,
            "lon": lon,
            "snr_hf": snr,
            "free_slots": free,
            "distance_km": dist,
            "bands_hz": [0, 30_000_000],
        }
        row["score"] = score_buddy_kiwi(row, 40.0, -20.0, [4483.0, 6516.0])
        return row

    # Centroïde ~ 40N 20W. Un Kiwi très proche mais en zone morte ~1100 km
    # ne doit pas gagner contre un NVIS et un 1 hop.
    pool = [
        kiwi("dead-zone", 50.0, -20.0, 1112, snr=35, free=8),
        kiwi("nvis", 41.0, -21.0, 140, snr=12, free=3),
        kiwi("hop-east", 40.0, 0.0, 1700, snr=18, free=4),
        kiwi("hop-west", 40.0, -40.0, 1700, snr=16, free=3),
    ]
    cfg = {"buddy": {"kiwi": {"count": 3, "min_separation_km": 300}}}
    roles = assign_buddy_kiwis(pool, lat=40.0, lon=-20.0, cfg=cfg)
    names = {k.get("name") for k in roles.values()}
    assert "nvis" in names
    assert "hop-east" in names or "hop-west" in names
    assert "dead-zone" not in names or len(names) >= 3
    assert roles["nvis"]["name"] == "nvis"
    assert len(roles) == 4


def test_buddy_channels_record_both_qrgs():
    from recorder.session import _buddy_channels

    cfg = {
        "buddy": {
            "main": {"freq_khz": 4483.0, "label": "Buddy call 4483 kHz", "zoom": 12},
            "alternate": {"freq_khz": 6516.0, "label": "Buddy call 6516 kHz (secours)", "zoom": 12},
        }
    }
    roles = {
        "nvis": {"name": "kiwi-nvis", "site_label": "proche / NVIS", "free_slots": 3},
        "hop": {"name": "kiwi-hop", "site_label": "saut 1 hop", "free_slots": 1},
    }
    rows = _buddy_channels(cfg, roles)
    assert [c["id"] for c in rows] == ["nvis-main", "nvis-alt", "hop-main"]
    assert [c["freq_khz"] for c in rows] == [4483.0, 6516.0, 4483.0]
    assert rows[0]["screencast"] is True
    assert rows[1]["screencast"] is False
    assert rows[2]["screencast"] is False


def test_kiwi_tune_url_sets_wf_colormap():
    from recorder.kiwi_list import kiwi_tune_url

    url = kiwi_tune_url(
        {"url": "http://kiwi.example:8073/"},
        4483.0,
        mode="usb",
        zoom=12,
    )
    assert url.startswith("http://kiwi.example:8073/?f=4483.00usbz12")
    assert "wfm=-110,-40" in url


def test_osm_water_is_carto_cyan_not_ice_or_forest():
    from app.globe_tiles import is_osm_water, punch_water, valid_tile
    from PIL import Image
    import io

    assert is_osm_water(170, 211, 223)  # #aad3df Carto
    assert not is_osm_water(221, 236, 236)  # glacier #ddecec
    assert not is_osm_water(120, 170, 90)  # forêt
    assert not is_osm_water(210, 180, 120)  # désert
    assert valid_tile(6, 31, 22)
    assert not valid_tile(9, 0, 0)
    assert not valid_tile(2, 8, 0)

    im = Image.new("RGB", (2, 1), (170, 211, 223))
    im.putpixel((1, 0), (34, 139, 34))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    out = Image.open(io.BytesIO(punch_water(buf.getvalue()))).convert("RGBA")
    assert out.getpixel((0, 0))[:3] == (10, 53, 88)
    assert out.getpixel((0, 0))[3] == 255
    assert out.getpixel((1, 0))[3] == 255
    assert out.getpixel((1, 0))[1] > 100


def test_fleet_uses_tahiti_tx_after_cape_of_good_hope():
    from recorder.kiwi_list import bulletin_tx_qth, fleet_uses_tahiti_tx

    assert not fleet_uses_tahiti_tx(28.0, -15.0)  # Canaries
    assert not fleet_uses_tahiti_tx(-35.0, 10.0)  # Atlantique sud, ouest du cap
    assert fleet_uses_tahiti_tx(-35.0, 25.0)  # Indien, est de 18°28′E
    assert fleet_uses_tahiti_tx(-45.0, -120.0)  # Pacifique, ouest du Horn
    assert not fleet_uses_tahiti_tx(-50.0, -40.0)  # Atlantique après Horn
    cfg = {"sdr": {"sites": {}}}
    assert bulletin_tx_qth(cfg, 28.0, -15.0)["id"] == "france"
    assert bulletin_tx_qth(cfg, -35.0, 25.0)["id"] == "tahiti"
    assert bulletin_tx_qth(cfg, -35.0, 25.0)["label"] == "Tahiti"


def test_metrics_payload_exposes_ggr_gauges(tmp_path, monkeypatch):
    monkeypatch.setenv("GGR_DATA_DIR", str(tmp_path))
    from app.metrics import payload

    body, media = payload()
    text = body.decode()
    assert "ggr_recording" in text
    assert "ggr_data_used_bytes" in text
    assert "ggr_data_total_bytes" in text
    assert "ggr_http_visitors" in text
    assert "ggr_http_replays_total" in text
    assert "text/plain" in media


def _starlette_request(
    path: str,
    *,
    method: str = "GET",
    forwarded: str | None = None,
    extra_headers: list[tuple[bytes, bytes]] | None = None,
    client: str = "10.42.0.1",
):
    from starlette.requests import Request

    headers = []
    if forwarded:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    if extra_headers:
        headers.extend(extra_headers)
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "client": (client, 12345),
            "server": ("127.0.0.1", 443),
        }
    )


def test_visitors_count_public_page_not_probes(tmp_path, monkeypatch):
    monkeypatch.setenv("GGR_DATA_DIR", str(tmp_path))
    from app import visitlog, visitors

    visitlog.reset_for_tests()

    visitors.reset_for_tests()
    visitors._geo_cache["8.8.8.8"] = {
        "country": "France",
        "city": "Paris",
        "latitude": "48.86",
        "longitude": "2.35",
    }
    before = sum(s.value for s in visitors.VISITS.collect()[0].samples if s.name == "ggr_http_visits_total")
    visitors.schedule(_starlette_request("/", forwarded="8.8.8.8"))
    visitors.schedule(_starlette_request("/health", forwarded="8.8.8.8"))
    visitors.schedule(_starlette_request("/api/trafic", forwarded="8.8.8.8"))
    visitors.schedule(_starlette_request("/api/globe/osm/3/2/1.png", forwarded="8.8.8.8"))
    visitors.schedule(_starlette_request("/", forwarded="10.42.0.9"))
    labelled = [s for s in visitors.VISITS.collect()[0].samples if s.name == "ggr_http_visits_total"]
    assert labelled
    assert sum(s.value for s in labelled) == before + 1
    assert visitors.UNIQUE._value.get() == 1


def test_visitors_client_ip_leftmost_public():
    from app import visitors

    req = _starlette_request("/", forwarded="8.8.8.8, 10.42.0.1")
    assert visitors.client_ip(req) == "8.8.8.8"
    assert visitors.client_ip(_starlette_request("/", forwarded="8.8.8.8:54321")) == "8.8.8.8"
    assert visitors.client_ip(
        _starlette_request("/", extra_headers=[(b"forwarded", b"for=1.1.1.1;proto=https")])
    ) == "1.1.1.1"
    assert visitors.is_page_visit("GET", "/")
    assert visitors.is_page_visit("GET", "/trafic/2026-09-16T1759Z")
    assert not visitors.is_page_visit("GET", "/metrics")
    assert not visitors.is_page_visit("POST", "/")


def test_visitors_records_trafic_path(tmp_path, monkeypatch):
    monkeypatch.setenv("GGR_DATA_DIR", str(tmp_path))
    from app import visitlog, visitors

    visitlog.reset_for_tests()

    visitors.reset_for_tests()
    visitors._geo_cache["8.8.8.8"] = {
        "country": "United States",
        "city": "Ashburn",
        "latitude": "39.04",
        "longitude": "-77.49",
    }
    visitors.schedule(_starlette_request("/trafic/2026-09-19T1159Z-buddy", forwarded="8.8.8.8"))
    hits = [
        s
        for s in visitors.VISITS.collect()[0].samples
        if s.name == "ggr_http_visits_total"
        and s.labels.get("path") == "/trafic/2026-09-19T1159Z-buddy"
        and s.labels.get("city") == "Ashburn"
    ]
    assert hits
    assert visitors.page_label("/trafic/2026-09-19T1159Z-buddy/") == "/trafic/2026-09-19T1159Z-buddy"
    assert visitors.page_label("/") == "/"
    assert visitors.page_label("/#metarea") == "/#metarea"
    assert visitors.page_label("/#setup") == "/#setup"
    assert visitors.page_label("/#apropos") == "/#apropos"
    assert visitors.page_label("/api/trafic/2026-09-20T1159Z-buddy/play") == (
        "/api/trafic/2026-09-20T1159Z-buddy/play"
    )


def test_visitlog_ip_timeline(tmp_path, monkeypatch):
    monkeypatch.setenv("GGR_DATA_DIR", str(tmp_path))
    from app import visitlog

    visitlog.reset_for_tests()
    visitlog.append("8.8.8.8", "page", "/", "United States", "Ashburn")
    visitlog.append(
        "8.8.8.8",
        "replay",
        "2026-09-19T1159Z-buddy",
        "France",
        "Vigneux-de-Bretagne",
        region="Pays de la Loire",
        postal="44360",
        isp="Orange",
        latitude="47.3250",
        longitude="-1.7380",
        ptr="anantes-651-1-2-3.w90-37.abo.wanadoo.fr",
    )
    visitlog.append("1.1.1.1", "page", "/", "Australia", "Sydney")
    mine = visitlog.list_events(ip="8.8.8.8")
    assert [e["kind"] for e in mine] == ["replay", "page"]
    assert mine[0]["target"] == "2026-09-19T1159Z-buddy"
    assert mine[0]["ip"] == "8.8.8.8"
    assert mine[0]["postal"] == "44360"
    assert mine[0]["isp"] == "Orange"
    assert mine[0]["ptr"].startswith("anantes-")
    assert "T" in mine[0]["ts"]
    all_rows = visitlog.list_events()
    assert len(all_rows) == 3
    buddy = visitlog.list_events(target="buddy")
    assert len(buddy) == 1
    assert buddy[0]["target"] == "2026-09-19T1159Z-buddy"
    both = visitlog.list_events(ip="8.8.8.8", target="/")
    assert [e["target"] for e in both] == ["/"]


def test_visitlog_stdout_json_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GGR_DATA_DIR", str(tmp_path))
    from app import visitlog

    visitlog.reset_for_tests()
    visitlog.append("9.9.9.9", "page", "/#metarea", "France", "Paris")
    lines = [ln for ln in capsys.readouterr().out.splitlines() if '"ggr_visit"' in ln]
    assert lines
    payload = json.loads(lines[-1])
    assert payload["ggr_visit"] is True
    assert payload["ip"] == "9.9.9.9"
    assert payload["target"] == "/#metarea"
    assert payload["kind"] == "page"
    assert "T" in payload["ts"]


def test_geo_labels_keeps_isp_out_of_prometheus():
    from app import visitors

    labels = visitors._geo_labels(
        {
            "country": "France",
            "city": "Vigneux-de-Bretagne",
            "region": "Pays de la Loire",
            "postal": "44360",
            "isp": "Orange",
            "latitude": 47.325,
            "longitude": -1.738,
            "ptr": "anantes-651-1-2-3.w90-37.abo.wanadoo.fr",
        }
    )
    assert labels["postal"] == "44360"
    assert labels["isp"] == "Orange"
    assert labels["region"] == "Pays de la Loire"
    prom = visitors._prom_labels(labels)
    assert set(prom) == {"country", "city", "latitude", "longitude"}
    assert "isp" not in prom
    assert prom["latitude"] == "47.3250"


def test_visitors_records_replay_play(tmp_path, monkeypatch):
    monkeypatch.setenv("GGR_DATA_DIR", str(tmp_path))
    from app import visitlog, visitors

    visitlog.reset_for_tests()

    visitors.reset_for_tests()
    visitors._geo_cache["8.8.8.8"] = {
        "country": "United States",
        "city": "Ashburn",
        "latitude": "39.04",
        "longitude": "-77.49",
    }
    visitors.schedule_replay(
        _starlette_request("/api/trafic/2026-09-19T1159Z-buddy/play", method="POST", forwarded="8.8.8.8"),
        "2026-09-19T1159Z-buddy",
    )
    hits = [
        s
        for s in visitors.REPLAYS.collect()[0].samples
        if s.name == "ggr_http_replays_total"
        and s.labels.get("replay") == "2026-09-19T1159Z-buddy"
        and s.labels.get("city") == "Ashburn"
    ]
    assert hits
    rows = visitlog.list_events(ip="8.8.8.8")
    assert rows[0]["kind"] == "replay"
    assert rows[0]["target"] == "/api/trafic/2026-09-19T1159Z-buddy/play"


def test_visitors_records_nav_urls(tmp_path, monkeypatch):
    monkeypatch.setenv("GGR_DATA_DIR", str(tmp_path))
    from app import visitlog, visitors

    visitlog.reset_for_tests()
    visitors.reset_for_tests()
    visitors._geo_cache["8.8.8.8"] = {
        "country": "France",
        "city": "Paris",
        "latitude": "48.86",
        "longitude": "2.35",
    }
    req = _starlette_request("/api/nav", method="POST", forwarded="8.8.8.8")
    visitors.schedule_nav(req, "/#metarea")
    visitors.schedule_nav(req, "/trafic/2026-09-20T1159Z-buddy")
    visitors.schedule_nav(req, "/#trafic")
    visitors.schedule_nav(req, "/admin")
    targets = [e["target"] for e in visitlog.list_events(ip="8.8.8.8")]
    assert "/#metarea" in targets
    assert "/trafic/2026-09-20T1159Z-buddy" in targets
    assert "/" in targets
    assert "/admin" not in targets


def _pip(lon: float, lat: float, ring: list) -> bool:
    inside = False
    n = len(ring)
    if n < 4:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-18) + xi:
            inside = not inside
        j = i
    return inside


def _metarea_has(feat: dict, lat: float, lon: float) -> bool:
    geom = feat["geometry"]
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    for poly in polys:
        if not _pip(lon, lat, poly[0]):
            continue
        if any(_pip(lon, lat, hole) for hole in poly[1:]):
            continue
        return True
    return False


def test_metareas_geojson_iho_omm():
    import json
    from pathlib import Path

    raw = json.loads((Path("app/static/geo/metareas.json")).read_text(encoding="utf-8"))
    assert raw["type"] == "FeatureCollection"
    feats = raw["features"]
    names = [f["properties"]["name"] for f in feats]
    assert names == [
        "I", "II", "III", "IV", "V", "VI", "VII", "VIII-N", "VIII-S", "IX", "X", "XI",
        "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX", "XXI",
    ]
    by = {f["properties"]["name"]: f for f in feats}
    assert by["II"]["properties"]["coordinator"] == "France"
    assert by["XVI"]["properties"]["coordinator"] == "Pérou"
    assert by["VIII-S"]["properties"]["roman"] == "VIII(S)"
    # Géométries OHI découpées sur l’océan : points en mer, pas sur les continents.
    assert _metarea_has(by["I"], 50.5, -8.0)
    assert _metarea_has(by["II"], 45.0, -10.0)
    assert _metarea_has(by["III"], 37.5, 16.3)
    assert _metarea_has(by["X"], -32.0, 114.5)
    assert _metarea_has(by["XV"], -33.4, -75.0)
    assert not _metarea_has(by["I"], 45.0, -10.0)
    assert not _metarea_has(by["II"], 46.50, -1.79)
    assert not _metarea_has(by["II"], 12.0, 8.0)


def test_metarea2_subzones_ocean_grid():
    from app.metarea import zone_at

    assert zone_at(28.0, -16.0) == "CANARIAS"
    assert zone_at(33.2, -18.0) == "MADEIRA"
    assert zone_at(37.7, -25.7) == "ACORES"
    assert zone_at(46.5, -25.0) == "FARADAY"
    assert zone_at(43.0, -34.0) == "ALTAIR"
    assert zone_at(16.5, -24.0) == "CAPE VERDE"
    assert zone_at(31.0, -28.0) == "METEOR"
    assert zone_at(29.0, -12.0) == "AGADIR"
    assert zone_at(28.86, -13.82) == "CANARIAS"


def test_metarea_at_follows_iho_for_ggr_route():
    from app.metarea import metarea_at

    assert metarea_at(45.0, -10.0)["name"] == "II"
    assert metarea_at(28.0, -16.0)["n"] == 2
    assert metarea_at(-32.0, 114.5)["name"] == "X"
    assert metarea_at(-33.4, -75.0)["name"] == "XV"
    assert metarea_at(46.50, -1.79) is None


def test_metarea2_fqnt52_digest_canarias():
    from app.metarea import assemble, fr_marine

    assert "mer agitée" in fr_marine("North or Northeast 3 or 4, at times 5 near islands. Moderate.").lower()
    raw = {
        "title": "Bulletinset for METAREA 2",
        "date": "2026-09-19 05:16:43",
        "bulletin": [
            {
                "label": "HIGH SEAS WARNING",
                "content": {
                    "1": "WONT50 LFPW 181823",
                    "2": "SECURITE ON METAREA 2, METEO-FRANCE,",
                    "3": "WARNING NR 353, FRIDAY 18 SEPTEMBER 2026 AT 1820 UTC",
                    "4": "EAST OF CADIZ, GIBRALTAR STRAIT.",
                    "5": "EAST 8 IN AND LEEWARD STRAIT. GUSTS.",
                    "6": "BT",
                },
            },
            {
                "label": "HIGH SEAS FORECAST",
                "content": {
                    "1": "FQNT52 LFPW 181834",
                    "2": "Weather bulletin on METAREA 2,",
                    "3": "METEO-FRANCE Toulouse, Friday 18 September 2026 at 2215 UTC.",
                    "4": "Part 1 : WARNING NR 353.",
                    "5": "Part 2 : General synopsis, Friday 18 at 12 UTC",
                    "6": "Low 1011 over Morocco, with little change.",
                    "7": "Part 3 : Area forecasts to Sunday 20 at 00 UTC",
                    "8": "CANARIAS.",
                    "9": "North or Northeast 3 or 4, at times 5 near islands.",
                    "10": "Moderate.",
                    "11": "FARADAY.",
                    "12": "Southwest 5 or 6.",
                    "13": "Part 4 : outlook for next 24 hours",
                    "14": "Threat of Southwesterly near gale over FARADAY.",
                },
            },
        ],
    }
    body = assemble(raw, [{"name": "Skipper test", "sail": "FRA 1", "lat": 28.0, "lon": -16.0}])
    assert body["ok"] is True
    assert body["fleet_zones"] == ["CANARIAS"]
    assert body["official_label"].startswith("vendredi 18 septembre 2026 à 22:15")
    assert body["geojson"]["features"]
    assert body["geojson"]["features"][0]["properties"]["name"] == "CANARIAS"
    lecture = body["lecture"]
    assert "CANARIAS" in lecture
    assert "Skipper test" not in lecture
    assert "FARADAY" not in lecture
    assert "Gibraltar" not in lecture
    assert "hors zones" not in lecture.lower()
    assert "nord ou nord-est 3 ou 4" in lecture.lower()
    assert "22:15" in body["official_label"]
    assert body["has_subzones"] is True
    assert body["metarea"] == "II"
    assert body["disclaimer"].startswith("Ce texte est un condensé automatique")
    assert body["warning"]["for_fleet"] is False
    assert body["warning"]["outside"] is True
    assert "hors des sous-zones" in lecture


def test_metarea_warning_kept_for_casablanca_neighbor():
    from app.metarea import assemble

    raw = {
        "title": "Bulletinset for METAREA 2",
        "date": "2026-09-19 05:16:43",
        "bulletin": [
            {
                "label": "HIGH SEAS WARNING",
                "content": {
                    "1": "WONT50 LFPW 181823",
                    "2": "SECURITE ON METAREA 2, METEO-FRANCE,",
                    "3": "WARNING NR 353, FRIDAY 18 SEPTEMBER 2026 AT 1820 UTC",
                    "4": "EAST OF CADIZ, GIBRALTAR STRAIT.",
                    "5": "EAST 8 IN AND LEEWARD STRAIT. GUSTS.",
                    "6": "BT",
                },
            },
            {
                "label": "HIGH SEAS FORECAST",
                "content": {
                    "1": "FQNT52 LFPW 181834",
                    "2": "Weather bulletin on METAREA 2,",
                    "3": "METEO-FRANCE Toulouse, Friday 18 September 2026 at 2215 UTC.",
                    "4": "Part 3 : Area forecasts to Sunday 20 at 00 UTC",
                    "5": "CASABLANCA.",
                    "6": "North or Northeast 4 or 5.",
                    "7": "CANARIAS.",
                    "8": "North 3.",
                },
            },
        ],
    }
    body = assemble(raw, [{"name": "Selim", "lat": 34.67, "lon": -9.94}])
    assert body["fleet_zones"] == ["CASABLANCA"]
    assert body["warning"]["for_fleet"] is True
    assert "Gibraltar" in body["lecture"]
    assert "CANARIAS" not in body["lecture"]
    assert "Selim" not in body["lecture"]


def test_metarea_near_coastal_lanzarote_pulls_agadir():
    from app.metarea import near_coastal_names, zone_at

    assert zone_at(28.86, -13.82) == "CANARIAS"
    assert "AGADIR" in near_coastal_names(28.86, -13.82, "CANARIAS")
    assert near_coastal_names(28.0, -16.0, "CANARIAS") == []


def test_metarea_coastal_las_palmas_only_occupied_zones():
    from app.metarea import assemble, parse_coastal

    raw = {
        "title": "Bulletinset for METAREA 2",
        "date": "2026-09-19 05:16:43",
        "bulletin": [
            {
                "label": "HIGH SEAS FORECAST",
                "content": {
                    "1": "FQNT52 LFPW 181834",
                    "2": "Weather bulletin on METAREA 2, Friday 18 September 2026 at 2215 UTC.",
                    "3": "Part 3 : Area forecasts to Sunday 20 at 00 UTC",
                    "4": "CANARIAS.",
                    "5": "North or Northeast 3 or 4.",
                },
            }
        ],
    }
    parsed = parse_coastal(
        [
            "FQNT72 LEMM 181600",
            "STATE MET AGENCY OF SPAIN",
            "CANARIAS NAVTEX SERVICE AREA",
            "GALE WARNINGS: NONE.",
            "24 HOURS FCST:",
            "MADEIRA: N OR NE 4 OR 5, LOC 6 NEARS ISLANDS.",
            "CANARIAS: N OR NE 4 OR 5, LOC 6 BETWEEN ISLANDS.",
            "TARFAYA: N OR NE 4 OR 5.",
        ]
    )
    coastal = [{**parsed, "title": "LAS PALMAS FORECAST", "gts": "FQNT72", "url": "https://wwmiws.wmo.int/x"}]
    body = assemble(raw, [{"lat": 28.0, "lon": -16.0}], coastal=coastal)
    lecture = body["lecture"]
    assert "Bulletin côtier LAS PALMAS FORECAST" in lecture
    assert "entre les îles" in lecture.lower()
    assert "TARFAYA" not in lecture
    assert "MADEIRA" not in lecture
    assert body["coastal"][0]["gts"] == "FQNT72"
    assert extra_catalog_has_las_palmas()


def test_metarea_coastal_dedupes_casablanca_between_tarifa_and_las_palmas():
    from app.metarea import assemble, parse_coastal

    raw = {
        "title": "Bulletinset for METAREA 2",
        "date": "2026-09-19 05:16:43",
        "bulletin": [
            {
                "label": "HIGH SEAS FORECAST",
                "content": {
                    "1": "FQNT52 LFPW 181834",
                    "2": "Weather bulletin on METAREA 2, Friday 18 September 2026 at 2215 UTC.",
                    "3": "Part 3 : Area forecasts to Sunday 20 at 00 UTC",
                    "4": "CASABLANCA.",
                    "5": "North or Northeast 4 or 5.",
                    "6": "CANARIAS.",
                    "7": "North 3.",
                },
            }
        ],
    }
    tarifa = {
        **parse_coastal(
            [
                "FQMQ72 LEMM 181600",
                "GALE OR NEAR GALE WARNINGS: NONE.",
                "24 HOURS FCST:",
                "CASABLANCA: N OR NE 4 OR 5, EXCEPT VRB 1 TO 3 IN NE.=",
                "CADIZ: E OR NE 5 TO 7.",
            ]
        ),
        "title": "TARIFA FORECAST",
        "gts": "FQMQ72",
        "url": "https://wwmiws.wmo.int/tarifa",
    }
    las = {
        **parse_coastal(
            [
                "FQNT72 LEMM 181600",
                "GALE WARNINGS: NONE.",
                "24 HOURS FCST:",
                "CASABLANCA: N OR NE 4 OR 5, EXCEPT VRB 1 TO 3 IN NE.",
                "CANARIAS: N OR NE 4 OR 5, LOC 6 BETWEEN ISLANDS.",
            ]
        ),
        "title": "LAS PALMAS FORECAST",
        "gts": "FQNT72",
        "url": "https://wwmiws.wmo.int/laspalmas",
    }
    body = assemble(
        raw,
        [{"lat": 28.0, "lon": -16.0}, {"lat": 34.67, "lon": -9.94}],
        coastal=[tarifa, las],
    )
    lecture = body["lecture"]
    assert lecture.count("Bulletin côtier") == 1
    assert "LAS PALMAS FORECAST" in lecture
    assert "TARIFA FORECAST" not in lecture
    assert [c["gts"] for c in body["coastal"]] == ["FQNT72"]


def test_needed_coastal_includes_unknown_metarea_products():
    from app.metarea import needed_coastal

    cat = [
        {"gts": "FQNT53", "title": "CROSS CORSEN FORECAST", "url": "a"},
        {"gts": "FQNT72", "title": "LAS PALMAS FORECAST", "url": "b"},
        {"gts": "FQZA30", "title": "COASTAL FORECAST", "url": "c"},
    ]
    assert [c["gts"] for c in needed_coastal(cat, ["CANARIAS"])] == ["FQNT72", "FQZA30"]
    assert [c["gts"] for c in needed_coastal(cat, ["IROISE"])] == ["FQNT53", "FQZA30"]


def test_metarea_coastal_dedup_without_metarea2_grid():
    from app.metarea import assemble, parse_coastal

    raw = {
        "title": "Bulletinset for METAREA 7",
        "date": "2026-09-19 05:16:43",
        "bulletin": [
            {
                "label": "HIGH SEAS FORECAST",
                "content": {
                    "1": "FQZA31 FAPR 181200",
                    "2": "High seas forecast for METAREA 7, Tuesday 15 September 2026 at 1200 UTC.",
                    "3": "Southwest 5 or 6. Moderate.",
                },
            }
        ],
    }
    first = {
        **parse_coastal(
            [
                "FQZA30 FAPR 181200",
                "CAPE COLUMBINE TO CAPE AGULHAS.",
                "SW 5 TO 6. MODERATE.",
                "WALVIS BAY TO LUDERITZ.",
                "S 4.",
            ],
            strict=False,
        ),
        "title": "COASTAL FORECAST",
        "gts": "FQZA30",
    }
    dup = {
        **parse_coastal(
            [
                "FQXX99 XXXX 181200",
                "CAPE COLUMBINE TO CAPE AGULHAS.",
                "SW 5 TO 6. MODERATE.",
            ],
            strict=False,
        ),
        "title": "OTHER COASTAL",
        "gts": "FQXX99",
    }
    body = assemble(
        raw,
        [{"name": "Skipper", "lat": -34.5, "lon": 18.2}],
        area={"n": 7, "name": "VII", "roman": "VII", "coordinator": "South Africa"},
        coastal=[dup, first],
    )
    lecture = body["lecture"]
    assert lecture.count("CAPE COLUMBINE TO CAPE AGULHAS") == 1
    assert "WALVIS BAY TO LUDERITZ" in lecture
    assert "Skipper" not in lecture
    assert "OTHER COASTAL" not in lecture
    assert [c["gts"] for c in body["coastal"]] == ["FQZA30"]


def extra_catalog_has_las_palmas():
    from app.metarea import extra_catalog

    html = (
        """<a class="bull bullnormal bullnavtex" """
        """href="javascript:ouvre_popup('https://wwmiws.wmo.int/index.php/metareas/display/bulletin/FQNT72_LEMM/20260918190540865453')" """
        """data-tip="Bulletin display in popup" > LAS PALMAS FORECAST&nbsp; </a>"""
        """<a href="javascript:ouvre_popup('https://wwmiws.wmo.int/index.php/metareas/display/bulletin/FQNT52_LFPW/1')" > HIGH SEAS FORECAST&nbsp; </a>"""
    )
    cat = extra_catalog(html)
    assert [c["gts"] for c in cat] == ["FQNT72"]
    return True


def test_metarea_without_grid_reads_full_official_bulletin():
    from app.metarea import assemble

    raw = {
        "title": "Bulletinset for METAREA 10",
        "date": "2026-09-19 05:16:43",
        "bulletin": [
            {
                "label": "HIGH SEAS FORECAST",
                "content": {
                    "1": "FQAU01 AMMC 181200",
                    "2": "High seas forecast for METAREA 10, Tuesday 15 September 2026 at 1200 UTC.",
                    "3": "Northeast 4 or 5. Moderate.",
                },
            }
        ],
    }
    body = assemble(
        raw,
        [{"name": "Skipper test", "lat": -32.0, "lon": 114.5}],
        area={"n": 10, "name": "X", "roman": "X", "coordinator": "Australia"},
    )
    assert body["ok"] is True
    assert body["metarea"] == "X"
    assert body["has_subzones"] is False
    assert body["fleet_zones"] == []
    assert "Skipper test" not in body["lecture"]
    assert "METAREA X" in body["lecture"]
    assert "nord-est 4 ou 5" in body["lecture"].lower()
