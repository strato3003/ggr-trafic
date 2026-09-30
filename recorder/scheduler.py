"""Planification : F6KUF lundi et jeudi 18:00 TU, Michel FO5QB tous les jours."""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from recorder.config import load_config, schedule_days
from recorder.session import purge_old, run_vacation

log = logging.getLogger(__name__)


def _hhmm_lead(time_utc: str, lead_minutes: int) -> tuple[int, int]:
    hh, mm = (time_utc or "00:00").split(":")
    total = int(hh) * 60 + int(mm) - int(lead_minutes)
    if total < 0:
        total += 24 * 60
    return divmod(total, 60)


def _lead(cfg: dict) -> tuple[int, int]:
    sched = cfg.get("schedule") or {}
    return _hhmm_lead(str(sched.get("time_utc") or "18:00"), int(sched.get("lead_minutes") or 1))


def _vacation_dow(cfg: dict) -> str | None:
    """Jours UTC du cron bulletin. None = tous les jours (Michel FO5QB)."""
    from recorder.config import tahiti_daily

    if tahiti_daily(cfg):
        return None
    return ",".join(schedule_days(cfg)) or "mon,thu"


def _vacation_trigger(cfg: dict) -> CronTrigger:
    hour, minute = _lead(cfg)
    dow = _vacation_dow(cfg)
    kwargs: dict = {"hour": hour, "minute": minute, "timezone": "UTC"}
    if dow:
        kwargs["day_of_week"] = dow
    return CronTrigger(**kwargs)


def build_scheduler(cfg: dict | None = None) -> AsyncIOScheduler:
    cfg = cfg or load_config()
    hour, minute = _lead(cfg)
    dow = _vacation_dow(cfg)
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        run_vacation,
        _vacation_trigger(cfg),
        kwargs={"reason": "schedule"},
        id="ggr-vacation",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        purge_old,
        CronTrigger(hour=4, minute=10, timezone="UTC"),
        id="ggr-purge",
        replace_existing=True,
    )
    log.info("Planification : bulletin %s %02d:%02d TU", dow or "tous les jours", hour, minute)
    # Retirer un éventuel job buddy hérité d’une version précédente.
    try:
        if scheduler.get_job("ggr-buddy"):
            scheduler.remove_job("ggr-buddy")
            log.info("Job buddy call retiré")
    except Exception:
        pass
    return scheduler


def apply_vacation_schedule(scheduler: AsyncIOScheduler, cfg: dict) -> None:
    """Recale le cron après un changement d’heure / d’avance dans Réglages."""
    hour, minute = _lead(cfg)
    dow = _vacation_dow(cfg)
    scheduler.reschedule_job("ggr-vacation", trigger=_vacation_trigger(cfg))
    log.info("Planification bulletin mise à jour : %s %02d:%02d TU", dow or "tous les jours", hour, minute)
    try:
        if scheduler.get_job("ggr-buddy"):
            scheduler.remove_job("ggr-buddy")
            log.info("Job buddy call retiré")
    except Exception:
        pass
