"""Application web GGR Trafic — catalogue et replay du trafic HF."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app import globe_tiles, metarea, store
from recorder.config import ack_label, display_defaults, fmt_khz, fmt_mhz, load_config, parse_display, parse_qrg_khz, parse_tx_sites, qrg_context, save_runtime_settings, tx_sites_aim, version
from recorder.fleet import buddy_aim, fetch_fleet
from recorder.kiwi_list import assign_buddy_kiwis, bulletin_tx_label, bulletin_tx_qths, fetch_ranked_kiwis, kiwi_directory, map_kiwis, read_directory_cache
from recorder.scheduler import apply_vacation_schedule, build_scheduler
from recorder.session import (
    finalize_pending_sessions,
    next_recording_utc,
    next_vacation_utc,
    recover_orphaned,
    run_buddy_call,
    run_manual_qrg,
    run_vacation,
)

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
CFG = load_config()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("Templates : %s → %s", ROOT / "templates", list((ROOT / "templates").glob("*.html")))
    cfg = load_config()
    recovered = recover_orphaned(cfg)
    if recovered:
        log.warning("Récupération : %s verrou(s) / vacation(s) orphelin(s)", recovered)
    scheduler = None
    if (os.environ.get("GGR_SCHEDULER") or "1").strip() not in {"0", "off", "false", "no"}:
        scheduler = build_scheduler(cfg)
        scheduler.start()
    else:
        log.info("GGR_SCHEDULER=0 : pas d’enregistreur sur cette instance")
    _app.state.scheduler = scheduler

    async def _mux_pending() -> None:
        try:
            n = await asyncio.to_thread(finalize_pending_sessions, load_config())
            if n:
                log.info("Finalisation média : %s session(s)", n)
        except Exception:
            log.exception("Finalisation média")

    mux_task = asyncio.create_task(_mux_pending())

    async def _kiwi_directory_loop() -> None:
        try:
            n = len(await kiwi_directory(load_config(), refresh=True))
            log.info("Annuaire KiwiSDR prêt : %s récepteurs", n)
        except Exception:
            log.exception("Annuaire KiwiSDR initial")
        while True:
            await asyncio.sleep(3600)
            try:
                n = len(await kiwi_directory(load_config(), refresh=True))
                log.info("Annuaire KiwiSDR rafraîchi : %s récepteurs", n)
            except Exception:
                log.exception("Annuaire KiwiSDR horaire")

    kiwi_task = asyncio.create_task(_kiwi_directory_loop())
    try:
        yield
    finally:
        mux_task.cancel()
        kiwi_task.cancel()
        if scheduler is not None:
            scheduler.shutdown(wait=False)


app = FastAPI(title="GGR Trafic", version=version(CFG), lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


@app.middleware("http")
async def observe_visits(request: Request, call_next):
    from app import visitors

    visitors.schedule(request)
    return await call_next(request)
jinja = Environment(
    loader=FileSystemLoader(str(ROOT / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)
jinja.filters["when"] = store.iso_to_label


_UNAVAILABLE_FALLBACK = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GGR Trafic — indisponible</title>
<link rel="stylesheet" href="/static/css/app.css">
</head>
<body class="globe-page kiwi-panel-off is-map-3d is-unavailable" data-ggr-force-wait="1">
<div class="globe-app" data-ggr-force-wait="1">
  <div class="globe-stage"><div id="ggr-globe" class="ggr-globe" role="img" aria-hidden="true"></div></div>
  <div id="ggr-wait" class="ggr-wait" role="status">
    <p class="ggr-wait__title">Site momentanément indisponible</p>
    <p class="ggr-wait__text">Le trafic HF F6KUF / Golden Globe Race reviendra dans un instant.</p>
  </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/globe.gl@2.41.4"></script>
<script src="/static/js/wait.js"></script>
</body>
</html>
"""


def render(request: Request, name: str, **extra) -> HTMLResponse:
    """Rendu Jinja direct — Starlette TemplateResponse passe parfois le context dict comme nom de template."""
    try:
        ctx = _ctx(request, **extra)
        html = jinja.get_template(name).render(**ctx)
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})
    except Exception as exc:
        log.exception("Rendu %s", name)
        if name == "flotte.html":
            return render_unavailable(request, status_code=503)
        return HTMLResponse(
            f"<!doctype html><pre>Erreur interne : {type(exc).__name__}: {exc}</pre>",
            status_code=500,
        )


def render_unavailable(request: Request, status_code: int = 503) -> HTMLResponse:
    """Page d’attente (globe 3D flouté) — incident manifeste ou maintenance."""
    try:
        ctx = _ctx(request, unavailable=True)
        html = jinja.get_template("indisponible.html").render(**ctx)
        return HTMLResponse(html, status_code=status_code, headers={"Cache-Control": "no-store"})
    except Exception:
        log.exception("Page d'attente")
        return HTMLResponse(
            _UNAVAILABLE_FALLBACK,
            status_code=status_code,
            headers={"Cache-Control": "no-store"},
        )


def _ctx(request: Request, **extra):
    cfg = load_config()
    nxt = next_vacation_utc(cfg)
    bulletin = _bulletin_utc(cfg)
    buddy_at = _buddy_clock_utc(cfg)
    club = cfg.get("club") or {}
    schedule = cfg.get("schedule") or {}
    qrg = qrg_context(cfg)
    return {
        "app_name": (cfg.get("web") or {}).get("title") or "GGR Trafic",
        "version": version(cfg),
        "club": club,
        "club_callsign": club.get("callsign") or "F6KUF",
        "radio": cfg.get("radio") or {},
        "schedule": schedule,
        "schedule_lead": qrg["schedule_lead"],
        "next_start": nxt,
        "next_start_iso": nxt.isoformat(),
        "bulletin_iso": bulletin.isoformat(),
        "bulletin_label": bulletin.strftime("%d/%m %H:%M"),
        "buddy_iso": buddy_at.isoformat(),
        "buddy_label": buddy_at.strftime("%d/%m %H:%M"),
        "recording": store.recording_in_progress(cfg),
        "recording_state": store.recording_state(cfg),
        "next_recording_iso": next_recording_utc(cfg).isoformat(),
        "admin_configured": _admin_configured(cfg),
        **qrg,
        **extra,
    }


def _admin_configured(cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    token = (cfg.get("web") or {}).get("admin_token") or os.environ.get("GGR_ADMIN_TOKEN") or ""
    return bool(token)


def _require_admin(x_admin_token: str | None, cfg: dict | None = None) -> None:
    cfg = cfg or load_config()
    expected = (cfg.get("web") or {}).get("admin_token") or os.environ.get("GGR_ADMIN_TOKEN") or ""
    if not expected or x_admin_token != expected:
        raise HTTPException(403, "Jeton administrateur invalide")


def _clock_utc(time_utc: str) -> datetime:
    hh, mm = (time_utc or "00:00").split(":")
    now = datetime.now(timezone.utc)
    t = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    if t <= now:
        from datetime import timedelta

        t = t + timedelta(days=1)
    return t


def _bulletin_utc(cfg) -> datetime:
    """Prochaine heure d’antenne 18:00 TU (lundi / jeudi), pas le début d’enregistrement."""
    sched = cfg.get("schedule") or {}
    lead = int(sched.get("lead_minutes") or 1)
    return next_vacation_utc(cfg) + timedelta(minutes=lead)


def _buddy_clock_utc(cfg) -> datetime:
    buddy = cfg.get("buddy") or {}
    return _clock_utc(str(buddy.get("time_utc") or "12:00"))


@app.get("/health")
async def health():
    cfg = load_config()
    rec = store.recording_state(cfg)
    return {
        "ok": True,
        "version": version(cfg),
        "recording": rec["active"],
        "recording_label": rec["label"],
        "recording_started_at": rec["started_at"],
        "recording_ends_at": rec["ends_at"],
        "next_recording_at": next_recording_utc(cfg).isoformat(),
    }


@app.get("/metrics")
async def metrics():
    from app.metrics import payload

    body, media = payload()
    return Response(body, media_type=media)


@app.get("/ping", response_class=HTMLResponse)
async def ping():
    return HTMLResponse("<!doctype html><p>ggr-trafic ping</p>")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return await _globe_page(request)


@app.get("/trafic/{trafic_id}", response_class=HTMLResponse)
async def trafic_page(request: Request, trafic_id: str):
    meta = store.get_vacation(trafic_id, load_config())
    if not meta:
        raise HTTPException(404, "Trafic introuvable")
    return render(request, "trafic.html", trafic=meta)


@app.post("/api/trafic/{trafic_id}/play")
@app.post("/api/vacations/{trafic_id}/play")
async def api_replay_play(request: Request, trafic_id: str):
    """Ping mixer : une lecture a démarré (pas d’admin, pas d’IP dans Prometheus)."""
    if not store.get_vacation(trafic_id, load_config()):
        raise HTTPException(404, "Trafic introuvable")
    from app import visitors

    visitors.schedule_replay(request, trafic_id)
    return {"ok": True}


@app.post("/api/nav")
async def api_nav(request: Request):
    """Ping UI : onglet hash ou ouverture mixer (pas d’admin)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    path = str((body or {}).get("path") or "")
    from app import visitors

    visitors.schedule_nav(request, path)
    return {"ok": True}


@app.get("/api/visits")
async def api_visits(
    ip: str | None = None,
    target: str | None = None,
    limit: int = 200,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    _require_admin(x_admin_token)
    from app import visitlog

    rows = visitlog.list_events(ip=ip, target=target, limit=limit)
    return {"ok": True, "events": rows, "count": len(rows)}


@app.get("/vacations/{vacation_id}")
async def vacation_redirect(vacation_id: str):
    return RedirectResponse("/trafic/" + vacation_id, status_code=302)


@app.get("/flotte")
async def flotte_redirect():
    return RedirectResponse("/", status_code=302)


async def _globe_page(request: Request):
    cfg = load_config()
    try:
        fleet = await fetch_fleet(cfg, with_wx=True)
        aim = buddy_aim(fleet, cfg)
        try:
            kiwis = await fetch_ranked_kiwis(cfg, fleet["lat"], fleet["lon"], limit=8)
        except Exception:
            log.exception("Liste KiwiSDR indisponible")
            kiwis = []
        buddy_kiwis = []
        try:
            qrg = qrg_context(cfg)
            cover = [int(round(qrg["buddy_main_khz"] * 1000)), int(round(qrg["buddy_alt_khz"] * 1000))]
            pool = await fetch_ranked_kiwis(
                cfg,
                float(aim["lat"]),
                float(aim["lon"]),
                limit=0,
                min_free=1,
                cover_hz=cover,
                score_mode="buddy",
            )
            roles = assign_buddy_kiwis(pool, lat=float(aim["lat"]), lon=float(aim["lon"]), cfg=cfg)
            buddy_kiwis = list(roles.values())
        except Exception:
            log.exception("KiwiSDR buddy indisponibles")
        now = datetime.now(timezone.utc)
        qths = bulletin_tx_qths(
            cfg,
            float(fleet.get("lat") or 0),
            float(fleet.get("lon") or 0),
            boats=aim.get("skippers") or [],
            when=now,
        )
        tahiti_tx = any(qth.get("id") == "tahiti" for qth in qths)
    except Exception:
        log.exception("Page flotte")
        return render_unavailable(request, status_code=503)
    wait_q = str(request.query_params.get("wait") or "").lower()
    return render(
        request,
        "flotte.html",
        fleet=fleet,
        kiwis=kiwis,
        buddy_aim=aim,
        buddy_kiwis=buddy_kiwis,
        globe_trafics=[store.globe_vacation(v) for v in store.list_vacations(cfg)],
        tx_sites=tx_sites_aim(cfg, fleet.get("lat"), fleet.get("lon")),
        boats=fleet.get("boats") or [],
        bulletin_tx_label=bulletin_tx_label(qths),
        bulletin_tx_from_tahiti=tahiti_tx,
        bulletin_tx_overlap=len(qths) > 1,
        unavailable=bool((cfg.get("web") or {}).get("unavailable")) or wait_q in ("1", "true", "oui"),
    )


@app.get("/a-propos")
async def about_redirect():
    return RedirectResponse("/#apropos", status_code=302)


@app.get("/reglages")
async def reglages_redirect():
    return RedirectResponse("/#setup", status_code=302)


@app.get("/api/globe/osm/{z}/{x}/{y}.png")
async def osm_land_tile(z: int, x: int, y: int):
    """Tuile OSM : l’eau Carto est remplacée par un bleu uniforme, sans mosaïque de LOD."""
    if not globe_tiles.valid_tile(z, x, y):
        raise HTTPException(404, "Tuile OSM hors limites")
    try:
        png = await globe_tiles.land_tile_png(z, x, y, load_config())
    except httpx.HTTPError as exc:
        log.warning("Tuile OSM %s/%s/%s : %s", z, x, y, exc)
        raise HTTPException(502, "Tuile OSM indisponible") from exc
    except Exception:
        log.exception("Tuile OSM %s/%s/%s", z, x, y)
        raise HTTPException(502, "Tuile OSM illisible")
    return Response(
        png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/metarea")
async def api_metarea():
    """Bulletin haute mer WWMIWS filtré sur les METAREA occupées par la flotte GGR."""
    cfg = load_config()
    try:
        fleet = await fetch_fleet(cfg, with_wx=False)
    except Exception:
        log.exception("Flotte pour METAREA")
        fleet = {"boats": []}
    body = await metarea.snapshot(fleet.get("boats") or [])
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


@app.get("/media/{trafic_id}/{filename}")
async def media(trafic_id: str, filename: str):
    path = store.media_path(trafic_id, filename, load_config())
    if not path:
        raise HTTPException(404)
    headers = {"Cache-Control": "public, max-age=86400"}
    if path.suffix.lower() == ".wav":
        return FileResponse(path, media_type="audio/wav", headers=headers)
    if path.suffix.lower() == ".mp3":
        return FileResponse(path, media_type="audio/mpeg", headers=headers)
    if path.suffix.lower() in {".m4a", ".aac"}:
        return FileResponse(path, media_type="audio/mp4", headers=headers)
    if path.suffix.lower() == ".png":
        return FileResponse(path, media_type="image/png", headers=headers)
    return FileResponse(path, headers=headers)


def _trafic_meta(vid: str):
    meta = store.get_vacation(vid, load_config())
    if not meta:
        raise HTTPException(404)
    meta.pop("_dir", None)
    return meta


@app.get("/api/kiwis")
async def api_kiwis():
    """Annuaire KiwiSDR local (calque SDR potentiels). Rafraîchi toutes les heures."""
    cfg = load_config()
    rows, at = read_directory_cache(cfg)
    if not rows:
        rows = await kiwi_directory(cfg)
        _, at = read_directory_cache(cfg)
    kiwis = map_kiwis(rows)
    return {
        "ok": True,
        "kiwis": kiwis,
        "count": len(kiwis),
        "fetched_at": at.isoformat() if at else None,
    }


@app.get("/api/trafic")
@app.get("/api/vacations")
async def api_trafic():
    return store.list_vacations(load_config())


@app.get("/api/trafic/{trafic_id}")
async def api_trafic_one(trafic_id: str):
    return _trafic_meta(trafic_id)


@app.get("/api/vacations/{vacation_id}")
async def api_vacation_one(vacation_id: str):
    return _trafic_meta(vacation_id)


async def _trafic_delete(vid: str, x_admin_token: str | None):
    cfg = load_config()
    _require_admin(x_admin_token, cfg)
    err = store.delete_vacation(vid, cfg)
    if err == "introuvable":
        raise HTTPException(404, "Trafic introuvable")
    if err == "enregistrement en cours":
        raise HTTPException(409, "Trafic en cours d’enregistrement")
    if err:
        raise HTTPException(400, err)
    return {"ok": True, "deleted": vid}


@app.delete("/api/trafic/{trafic_id}")
async def api_trafic_delete(
    trafic_id: str,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    return await _trafic_delete(trafic_id, x_admin_token)


@app.delete("/api/vacations/{vacation_id}")
async def api_vacation_delete(
    vacation_id: str,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    return await _trafic_delete(vacation_id, x_admin_token)


@app.post("/api/trafic/{trafic_id}/delete")
async def api_trafic_delete_post(
    trafic_id: str,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Alias POST : certains proxys bloquent DELETE."""
    return await _trafic_delete(trafic_id, x_admin_token)


@app.post("/api/vacations/{vacation_id}/delete")
async def api_vacation_delete_post(
    vacation_id: str,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    return await _trafic_delete(vacation_id, x_admin_token)


@app.get("/api/settings")
async def api_settings_get():
    cfg = load_config()
    qrg = qrg_context(cfg)
    qrg["admin_configured"] = _admin_configured(cfg)
    qrg["recording"] = store.recording_in_progress(cfg)
    return qrg


def _khz_field(body: dict, key: str, label: str) -> float:
    try:
        khz = round(float(body[key]), 4)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"{label} invalide") from exc
    if not (1000.0 <= khz <= 30000.0):
        raise HTTPException(400, f"{label} hors bande HF (1000–30000 kHz)")
    return khz


@app.put("/api/settings")
async def api_settings_put(
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    cfg = load_config()
    _require_admin(x_admin_token, cfg)
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(400, "JSON invalide") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON objet attendu")
    tx_khz = _khz_field(body, "tx_khz", "QRG TX")
    ack1_khz = _khz_field(body, "ack1_khz", "QRG ACK 16 m")
    ack2_khz = _khz_field(body, "ack2_khz", "QRG ACK 12 m")
    try:
        tol = round(float(body.get("qrg_tolerance_khz", 5.0)), 3)
        lead = int(body.get("lead_minutes", 1))
        duration = int(body.get("duration_minutes", 10))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "Tolérance, avance ou durée invalide") from exc
    if not (0.1 <= tol <= 15.0):
        raise HTTPException(400, "Tolérance QRG hors plage (0,1–15 kHz)")
    if not (0 <= lead <= 15):
        raise HTTPException(400, "Avance hors plage (0–15 min)")
    if not (1 <= duration <= 45):
        raise HTTPException(400, "Durée hors plage (1–45 min)")
    radio = cfg.get("radio") or {}
    acks = [dict(row) for row in (radio.get("ack") or [])]
    while len(acks) < 2:
        acks.append({})
    acks[0]["freq_khz"] = ack1_khz
    acks[0]["label"] = ack_label(ack1_khz)
    acks[1]["freq_khz"] = ack2_khz
    acks[1]["label"] = ack_label(ack2_khz)
    patch = {
        "radio": {
            "tx": {"freq_khz": tx_khz, "qrg_tolerance_khz": tol},
            "ack": acks[:2],
        },
        "schedule": {"lead_minutes": lead, "duration_minutes": duration},
    }
    if "buddy_main_khz" in body or "buddy_skippers" in body:
        buddy_cfg = dict(cfg.get("buddy") or {})
        main = dict(buddy_cfg.get("main") or {})
        alt = dict(buddy_cfg.get("alternate") or {})
        cent = dict(buddy_cfg.get("centroid") or {})
        kiwi = dict(buddy_cfg.get("kiwi") or {})
        if "buddy_main_khz" in body:
            main["freq_khz"] = _khz_field(body, "buddy_main_khz", "QRG buddy 4483")
            main["label"] = f"Buddy call {fmt_khz(main['freq_khz'])} kHz"
        if "buddy_alt_khz" in body:
            alt["freq_khz"] = _khz_field(body, "buddy_alt_khz", "QRG buddy 6516")
            alt["label"] = f"Buddy call {fmt_khz(alt['freq_khz'])} kHz (secours)"
        if "buddy_time_utc" in body:
            from recorder.config import _hhmm

            buddy_cfg["time_utc"] = _hhmm(str(body.get("buddy_time_utc") or ""), "12:00")
        if "buddy_lead" in body:
            try:
                b_lead = int(body.get("buddy_lead"))
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, "Avance buddy invalide") from exc
            if not (0 <= b_lead <= 15):
                raise HTTPException(400, "Avance buddy hors plage (0–15 min)")
            buddy_cfg["lead_minutes"] = b_lead
        if "buddy_duration_minutes" in body:
            try:
                b_dur = int(body.get("buddy_duration_minutes"))
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, "Durée buddy invalide") from exc
            if not (1 <= b_dur <= 45):
                raise HTTPException(400, "Durée buddy hors plage (1–45 min)")
            buddy_cfg["duration_minutes"] = b_dur
        if "buddy_enabled" in body:
            buddy_cfg["enabled"] = bool(body.get("buddy_enabled"))
        if "buddy_skippers" in body or "buddy_include_fleet" in body:
            cent["include_fleet"] = False
        if "buddy_skippers" in body:
            raw_skip = body.get("buddy_skippers")
            if isinstance(raw_skip, str):
                names = [ln.strip() for ln in raw_skip.splitlines() if ln.strip()]
            elif isinstance(raw_skip, list):
                names = [str(x).strip() for x in raw_skip if str(x).strip()]
            else:
                raise HTTPException(400, "Liste de skippers buddy invalide")
            cent["skippers"] = names
        if "buddy_kiwi_count" in body:
            try:
                n_kiwi = int(body.get("buddy_kiwi_count"))
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, "Nombre de Kiwi buddy invalide") from exc
            if not (1 <= n_kiwi <= 8):
                raise HTTPException(400, "Nombre de Kiwi buddy hors plage (1–8)")
            kiwi["count"] = n_kiwi
        buddy_cfg["main"] = main
        buddy_cfg["alternate"] = alt
        buddy_cfg["centroid"] = cent
        buddy_cfg["kiwi"] = kiwi
        patch["buddy"] = buddy_cfg
    if "tx_sites" in body:
        try:
            patch["tx_sites"] = parse_tx_sites(body.get("tx_sites"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    web_patch: dict = {}
    if "display" in body:
        try:
            web_patch["display"] = parse_display(body.get("display"), display_defaults(cfg))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if "unavailable" in body:
        web_patch["unavailable"] = bool(body.get("unavailable"))
    if web_patch:
        patch["web"] = web_patch
    new_cfg = save_runtime_settings(patch, cfg)
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        apply_vacation_schedule(scheduler, new_cfg)
    qrg = qrg_context(new_cfg)
    qrg["ok"] = True
    qrg["admin_configured"] = _admin_configured(new_cfg)
    return qrg


@app.post("/api/trafic/record")
@app.post("/api/vacations/record")
async def api_record(
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    cfg = load_config()
    _require_admin(x_admin_token, cfg)
    if store.recording_in_progress(cfg):
        raise HTTPException(409, "Enregistrement déjà en cours")
    duration = None
    freq_khz = None
    hunt = True
    tol = None
    kind = "trafic"
    raw = await request.body()
    if raw:
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "JSON invalide") from exc
        if isinstance(body, dict):
            kind = str(body.get("kind") or "trafic")
            if body.get("duration_minutes") is not None:
                try:
                    duration = int(body["duration_minutes"])
                except (TypeError, ValueError) as exc:
                    raise HTTPException(400, "duration_minutes invalide") from exc
                if duration < 1 or duration > 45:
                    raise HTTPException(400, "Durée hors plage (1–45 min)")
            if body.get("freq_khz") is not None or body.get("freq") is not None:
                try:
                    freq_khz = parse_qrg_khz(body.get("freq_khz", body.get("freq")))
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from exc
            if "hunt" in body:
                hunt = bool(body.get("hunt"))
            if body.get("qrg_tolerance_khz") is not None:
                try:
                    tol = float(body["qrg_tolerance_khz"])
                except (TypeError, ValueError) as exc:
                    raise HTTPException(400, "Tolérance QRG invalide") from exc
                if not (0.1 <= tol <= 15.0):
                    raise HTTPException(400, "Tolérance QRG hors plage (0,1–15 kHz)")
    if freq_khz is not None:
        minutes = duration if duration is not None else 2
        asyncio.create_task(
            run_manual_qrg(
                freq_khz=freq_khz,
                duration_minutes=minutes,
                hunt=hunt,
                qrg_tolerance_khz=tol,
            )
        )
        return JSONResponse(
            {
                "ok": True,
                "status": "started",
                "mode": "qrg",
                "freq_khz": freq_khz,
                "freq_mhz": fmt_mhz(freq_khz),
                "duration_minutes": minutes,
                "hunt": hunt,
            },
            status_code=202,
        )
    if kind == "buddy":
        minutes = duration if duration is not None else int((cfg.get("buddy") or {}).get("duration_minutes") or 15)
        asyncio.create_task(run_buddy_call(reason="buddy-api", duration_minutes=minutes))
        return JSONResponse(
            {"ok": True, "status": "started", "mode": "buddy", "duration_minutes": minutes},
            status_code=202,
        )
    asyncio.create_task(run_vacation(reason="api", duration_minutes=duration))
    minutes = duration if duration is not None else int((cfg.get("schedule") or {}).get("duration_minutes") or 10)
    return JSONResponse(
        {"ok": True, "status": "started", "mode": "trafic", "duration_minutes": minutes},
        status_code=202,
    )


def run() -> None:
    import uvicorn

    web = CFG.get("web") or {}
    uvicorn.run(
        "app.main:app",
        host=web.get("host") or "0.0.0.0",
        port=int(web.get("port") or 8080),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    run()
