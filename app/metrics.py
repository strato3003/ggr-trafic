"""Métriques Prometheus pour Grafana (scrape interne /metrics)."""

from __future__ import annotations

import shutil

from prometheus_client import Gauge, Info, generate_latest, CONTENT_TYPE_LATEST

from app import store
from recorder.config import data_dir, load_config, version

GGR_RECORDING = Gauge("ggr_recording", "1 si un enregistrement bulletin/buddy/test est en cours")
GGR_DATA_USED_BYTES = Gauge("ggr_data_used_bytes", "Octets utilisés sur le volume de données")
GGR_DATA_TOTAL_BYTES = Gauge("ggr_data_total_bytes", "Taille du volume de données")
GGR_INFO = Info("ggr_trafic", "Version GGR Trafic")


def refresh() -> None:
    cfg = load_config()
    GGR_RECORDING.set(1 if store.recording_in_progress(cfg) else 0)
    GGR_INFO.info({"version": version(cfg)})
    try:
        usage = shutil.disk_usage(str(data_dir(cfg)))
        GGR_DATA_USED_BYTES.set(usage.used)
        GGR_DATA_TOTAL_BYTES.set(usage.total)
    except OSError:
        pass


def payload() -> tuple[bytes, str]:
    refresh()
    return generate_latest(), CONTENT_TYPE_LATEST
