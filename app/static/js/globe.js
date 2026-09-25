(() => {
  const el = document.getElementById("ggr-globe");
  const mapEl = document.getElementById("ggr-map");
  const blob = document.getElementById("ggr-globe-data");
  if (!el || !blob) return;

  let data;
  try {
    data = JSON.parse(blob.textContent || "{}");
  } catch {
    return;
  }

  const I18N = (() => {
    const node = document.getElementById("ggr-i18n");
    try {
      return JSON.parse((node && node.textContent) || "{}") || {};
    } catch {
      return {};
    }
  })();
  const t = (key, vars) => {
    let s = I18N[key] || key;
    if (vars) {
      Object.keys(vars).forEach((k) => {
        s = s.split("{" + k + "}").join(String(vars[k]));
      });
    }
    return s;
  };
  function siteLabel(s) {
    const raw = String(s || "");
    const map = {
      "écoute flotte": "site_fleet",
      flotte: "site_fleet",
      "flotte (bulletin)": "site_fleet_bull",
      "proche / NVIS": "prop_nvis",
      "saut 1 hop": "prop_hop",
      "zone morte": "prop_skip",
      "saut long": "prop_far",
      diversité: "site_div",
    };
    if (map[raw]) return t(map[raw]);
    const beam = raw.match(/^(?:portée TX bulletin|faisceau)\s*(.*)$/i);
    if (beam) return (t("site_beam") + " " + (beam[1] || "")).trim();
    return raw;
  }
  function zoneLabel(z) {
    if (!z) return "";
    const k = "prop_" + z;
    const s = t(k);
    return s === k ? z : s;
  }

  window.GgrWfCache = window.GgrWfCache || {};
  (function prefetchWaterfalls() {
    const cache = window.GgrWfCache;
    (data.trafics || data.vacations || []).forEach((v, i) => {
      (v.channels || []).forEach((c) => {
        const name = c.waterfall || "";
        if (!name || !(c.has_audio || c.audio)) return;
        const url = "/media/" + encodeURIComponent(v.id) + "/" + encodeURIComponent(name);
        if (cache[url]) return;
        fetch(url, { cache: "force-cache", priority: i < 2 ? "high" : "low" })
          .then((r) => (r.ok ? r.blob() : null))
          .then((b) => {
            if (b && b.size > 128) cache[url] = b;
          })
          .catch(() => {});
      });
    });
  })();

  const TOKEN_KEY = "ggr-admin-token";
  const token = () => localStorage.getItem(TOKEN_KEY) || "";
  const boats = (data.boats || []).filter((b) => Number.isFinite(b.lat) && Number.isFinite(b.lon));
  const skippers = new Set(data.skippers || []);
  const trafics = (data.trafics || data.vacations || []).filter((v) => Number.isFinite(v.lat) && Number.isFinite(v.lon));
  const TX_MAX = 5;
  let txSites = (data.tx_sites || []).filter((s) => Number.isFinite(s.lat) && Number.isFinite(s.lon)).slice(0, TX_MAX);
  let placingTx = false;
  let mapMode = "3d";
  let metareaFc = null;
  const display = data.display || {};
  function prefOn(lsKey, cfgKey, fallback) {
    try {
      const v = localStorage.getItem(lsKey);
      if (v === "on") return true;
      if (v === "off") return false;
    } catch {
      /* ignore */
    }
    if (typeof display[cfgKey] === "boolean") return display[cfgKey];
    return fallback;
  }
  let metareaOn = prefOn("ggr-metarea", "metarea", true);
  const metareaBtn = document.getElementById("ggr-metarea");
  let subzoneFc = null;
  let subzonesOn = prefOn("ggr-subzones", "subzones", true);
  const subzoneBtn = document.getElementById("ggr-subzones");
  let metareaSnap = null;

  const selEl = document.getElementById("globe-sel");
  const vacsEl = document.getElementById("globe-vacs");
  const skipEl = document.getElementById("globe-skippers");
  const msgEl = document.getElementById("globe-buddy-msg");
  const txListEl = document.getElementById("globe-tx-list");
  const txMsgEl = document.getElementById("globe-tx-msg");
  const txPlaceBtn = document.getElementById("globe-tx-place");
  const txSaveBtn = document.getElementById("globe-save-tx");
  const mixRoot = document.getElementById("mix");
  const mixPark = document.getElementById("mix-park");
  const kiwiToggle = document.getElementById("kiwi-toggle");
  let openVid = null;
  let muxOpen = null;
  let muxFocusId = null;
  let kiwisAll = [];
  let kmLabelNodes = [];
  let kmAlignRaf = 0;
  let muxPulseFat = false;
  let muxPulseTimer = 0;
  let globe = null;
  let map = null;
  let mapLayers = null;
  const layers = {
    banner: prefOn("ggr-layer-banner", "banner", true),
    sdrActive: prefOn("ggr-layer-sdrActive", "sdr_fleet", true),
    sdrPotential: prefOn("ggr-layer-sdrPotential", "sdr_potential", false),
    boats: prefOn("ggr-layer-boats", "boats", true),
    labelsSdr: prefOn("ggr-layer-labels-sdr", "labels_sdr", true),
    labelsBoats: prefOn("ggr-layer-labels-boats", "labels_boats", true),
  };
  let panelOpenedOnce = false;

  function setPanelOpen(on) {
    if (on) panelOpenedOnce = true;
    document.body.classList.toggle("kiwi-panel-off", !on);
    if (kiwiToggle) {
      kiwiToggle.setAttribute("aria-expanded", on ? "true" : "false");
      kiwiToggle.setAttribute("aria-label", on ? t("menu_collapse") : t("menu_open"));
      kiwiToggle.setAttribute("title", on ? t("menu_collapse_short") : t("menu"));
    }
    try {
      localStorage.setItem("ggr-kiwi-panel", on ? "on" : "off");
    } catch {
      /* ignore */
    }
    window.dispatchEvent(new Event("resize"));
    window.setTimeout(() => {
      window.dispatchEvent(new Event("resize"));
      if (map) map.invalidateSize();
    }, 200);
  }

  document.body.classList.add("is-map-3d");
  document.body.classList.remove("is-map-2d");

  function parseHash() {
    const h = (location.hash || "").replace(/^#/, "");
    if (h === "setup") return "setup";
    if (h === "metarea") return "metarea";
    if (h === "trafic") return "trafic";
    if (h === "apropos" || h === "a-propos") return "apropos";
    return "apropos";
  }

  function parkMix() {
    if (window.GgrMixer) window.GgrMixer.unmount();
    if (mixRoot) {
      mixRoot.hidden = true;
      if (mixPark) mixPark.appendChild(mixRoot);
    }
    document.querySelectorAll(".globe-vac-row.is-open").forEach((li) => {
      li.classList.remove("is-open");
      const btn = li.querySelector(".globe-vac");
      if (btn) btn.setAttribute("aria-expanded", "false");
    });
    openVid = null;
    muxOpen = null;
    muxFocusId = null;
    stopMuxPulse();
    document.body.classList.remove("kiwi-mix-on", "kiwi-mix-focus");
    refreshGlobe();
  }

  function showTab(name) {
    const tab = name || "apropos";
    document.querySelectorAll(".kiwi-tabs [data-tab]").forEach((b) => {
      const on = b.getAttribute("data-tab") === tab;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    document.querySelectorAll(".kiwi-page").forEach((p) => {
      p.hidden = p.getAttribute("data-page") !== tab;
    });
    const hash = "#" + tab;
    if (location.hash !== hash) history.replaceState(null, "", hash);
    if (tab !== "trafic") parkMix();
    if (!navReady) {
      navReady = true;
      if (tab !== "trafic") noteNav("/#" + tab);
      return;
    }
    if (tab === "trafic") noteNav("/");
    else noteNav("/#" + tab);
  }

  let navReady = false;
  let lastNav = "";
  function noteNav(path) {
    if (!path || path === lastNav) return;
    lastNav = path;
    try {
      fetch("/api/nav", {
        method: "POST",
        keepalive: true,
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ path: path }),
      }).catch(function () {});
    } catch {
      /* ignore */
    }
  }

  const traficQBoot = new URLSearchParams(location.search).get("trafic") || new URLSearchParams(location.search).get("vac");
  const deepQ = new URLSearchParams(location.search);
  const deepZoom = deepQ.get("zoom") === "1" || deepQ.get("zoom") === "true" || deepQ.has("ch");
  const deepFocus = deepQ.get("ch") || deepQ.get("focus") || (deepZoom ? "0" : "");
  const deepT = deepQ.get("t") || deepQ.get("at") || "";
  setPanelOpen(!!traficQBoot);

  if (kiwiToggle) {
    kiwiToggle.addEventListener("click", () => {
      const opening = document.body.classList.contains("kiwi-panel-off");
      if (opening && !panelOpenedOnce) showTab("apropos");
      setPanelOpen(opening);
    });
  }

  document.querySelectorAll(".kiwi-tabs [data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      panelOpenedOnce = true;
      showTab(btn.getAttribute("data-tab"));
      setPanelOpen(true);
    });
  });
  window.addEventListener("hashchange", () => {
    showTab(parseHash());
  });
  showTab(traficQBoot ? "trafic" : "apropos");

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");
  }

  function showSel(html) {
    if (!selEl) return;
    selEl.hidden = false;
    selEl.innerHTML = html;
    setPanelOpen(true);
  }

  function sayBuddy(text, ok) {
    if (!msgEl) return;
    msgEl.hidden = false;
    msgEl.textContent = text;
    msgEl.classList.toggle("err", !ok);
    msgEl.classList.toggle("ok", !!ok);
  }

  function sayTx(text, ok) {
    if (!txMsgEl) return;
    txMsgEl.hidden = false;
    txMsgEl.textContent = text;
    txMsgEl.classList.toggle("err", !ok);
    txMsgEl.classList.toggle("ok", !!ok);
  }

  function boatInBuddy(name) {
    return skippers.has(name);
  }

  const EARTH_KM = 6371;

  function toRad(d) {
    return (d * Math.PI) / 180;
  }

  function toDeg(r) {
    return (r * 180) / Math.PI;
  }

  function haversineKm(lat1, lon1, lat2, lon2) {
    const p1 = toRad(lat1);
    const p2 = toRad(lat2);
    const dp = toRad(lat2 - lat1);
    const dl = toRad(lon2 - lon1);
    const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
    return 2 * EARTH_KM * Math.asin(Math.min(1, Math.sqrt(a)));
  }

  function bearingDeg(lat1, lon1, lat2, lon2) {
    const p1 = toRad(lat1);
    const p2 = toRad(lat2);
    const dl = toRad(lon2 - lon1);
    const y = Math.sin(dl) * Math.cos(p2);
    const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
    return (toDeg(Math.atan2(y, x)) + 360) % 360;
  }

  function destPoint(lat, lon, brngDeg, distKm) {
    const ang = distKm / EARTH_KM;
    const br = toRad(brngDeg);
    const p1 = toRad(lat);
    const l1 = toRad(lon);
    const p2 = Math.asin(Math.sin(p1) * Math.cos(ang) + Math.cos(p1) * Math.sin(ang) * Math.cos(br));
    const l2 =
      l1 + Math.atan2(Math.sin(br) * Math.sin(ang) * Math.cos(p1), Math.cos(ang) - Math.sin(p1) * Math.sin(p2));
    let lon2 = toDeg(l2);
    lon2 = ((lon2 + 540) % 360) - 180;
    return [toDeg(p2), lon2];
  }

  function selectedBoats() {
    return boats.filter((b) => skippers.has(b.name));
  }

  function sphericalCentroid(pts) {
    if (!pts.length) return null;
    let x = 0;
    let y = 0;
    let z = 0;
    pts.forEach(([lat, lon]) => {
      const p = toRad(lat);
      const l = toRad(lon);
      x += Math.cos(p) * Math.cos(l);
      y += Math.cos(p) * Math.sin(l);
      z += Math.sin(p);
    });
    const n = pts.length;
    x /= n;
    y /= n;
    z /= n;
    const hyp = Math.sqrt(x * x + y * y);
    return { lat: toDeg(Math.atan2(z, hyp)), lon: toDeg(Math.atan2(y, x)) };
  }

  function fleetCenter() {
    return sphericalCentroid(selectedBoats().map((b) => [b.lat, b.lon]));
  }

  function fleetRingKm(center) {
    const sel = selectedBoats();
    if (sel.length) {
      const maxD = Math.max(...sel.map((b) => haversineKm(center.lat, center.lon, b.lat, b.lon)));
      // Entoure les skippers cochés : écart max au centroïde + 15 %, plancher 40 km.
      return Math.max(40, maxD * 1.15);
    }
    return 300;
  }

  function circleCoords(lat, lon, rKm, steps) {
    const pts = [];
    for (let i = 0; i <= steps; i++) {
      const p = destPoint(lat, lon, 360 - (360 * i) / steps, rKm);
      pts.push([p[0], p[1]]);
    }
    return pts;
  }

  function geoAlongDeg(brg) {
    let a = brg - 90;
    const n = ((a % 360) + 360) % 360;
    if (n > 90 && n < 270) a += 180;
    return a;
  }

  function pathMidSamples(lat1, lon1, lat2, lon2) {
    const d = haversineKm(lat1, lon1, lat2, lon2);
    const brg = bearingDeg(lat1, lon1, lat2, lon2);
    const midKm = d / 2;
    const sample = Math.max(18, Math.min(140, d * 0.04));
    return {
      mid: destPoint(lat1, lon1, brg, midKm),
      brg: brg,
      d: d,
      tanA: destPoint(lat1, lon1, brg, Math.max(0, midKm - sample)),
      tanB: destPoint(lat1, lon1, brg, Math.min(d, midKm + sample)),
      fallback: geoAlongDeg(brg),
    };
  }

  function projectLatLon(lat, lon) {
    if (mapMode === "2d" && map && typeof map.latLngToContainerPoint === "function") {
      const p = map.latLngToContainerPoint([lat, lon]);
      return p && Number.isFinite(p.x) ? { x: p.x, y: p.y } : null;
    }
    if (!globe || typeof globe.getScreenCoords !== "function") return null;
    const p = globe.getScreenCoords(lat, lon);
    return p && Number.isFinite(p.x) && Number.isFinite(p.y) ? { x: p.x, y: p.y } : null;
  }

  // Angle écran du trait, pas le bearing géographique : zoom / POV du globe
  // changent la tangente projetée. CSS rotate() est en pixels, pas en cap.
  function screenAlongDeg(tanA, tanB, fallback) {
    if (!tanA || !tanB) return fallback;
    const pa = projectLatLon(tanA[0], tanA[1]);
    const pb = projectLatLon(tanB[0], tanB[1]);
    if (!pa || !pb) return fallback;
    const dx = pb.x - pa.x;
    const dy = pb.y - pa.y;
    if (dx * dx + dy * dy < 9 || Math.abs(dx) > 2400 || Math.abs(dy) > 2400) return fallback;
    let ang = (Math.atan2(dy, dx) * 180) / Math.PI;
    const n = ((ang % 360) + 360) % 360;
    if (n > 90 && n < 270) ang += 180;
    return ang;
  }

  function applyKmLabelAngle(row) {
    if (!row || !row.el) return;
    const ang = screenAlongDeg(row.tanA, row.tanB, row.fallback);
    row.el.style.transform = "translate(-50%,-50%) rotate(" + ang + "deg)";
  }

  function alignKmLabels() {
    kmLabelNodes.forEach(applyKmLabelAngle);
  }

  function scheduleKmAlign() {
    if (kmAlignRaf) return;
    kmAlignRaf = window.requestAnimationFrame(() => {
      kmAlignRaf = 0;
      alignKmLabels();
    });
  }

  function bindKmAlign() {
    try {
      if (globe && typeof globe.controls === "function") {
        const c = globe.controls();
        if (c && !c.__ggrKmAlign && typeof c.addEventListener === "function") {
          c.__ggrKmAlign = true;
          c.addEventListener("change", scheduleKmAlign);
        }
      }
    } catch {
      /* globe.gl */
    }
    if (map && !map.__ggrKmAlign) {
      map.__ggrKmAlign = true;
      map.on("zoom move viewreset zoomanim", scheduleKmAlign);
    }
  }

  function geodesicCoords(lat1, lon1, lat2, lon2) {
    const d = haversineKm(lat1, lon1, lat2, lon2);
    const brg = bearingDeg(lat1, lon1, lat2, lon2);
    const steps = Math.max(16, Math.min(64, Math.round(d / 80) || 16));
    const pts = [[lat1, lon1]];
    for (let i = 1; i < steps; i++) {
      const p = destPoint(lat1, lon1, brg, (d * i) / steps);
      pts.push([p[0], p[1]]);
    }
    pts.push([lat2, lon2]);
    return pts;
  }

  function points() {
    const rows = [];
    boats.forEach((b) => {
      if (!layers.boats) return;
      rows.push({
        ...b,
        lng: b.lon,
        kind: "boat",
        inBuddy: boatInBuddy(b.name),
      });
    });
    const seenSdr = new Set();
    const sdrKey = (k) => String(k.id || k.host || k.name || (k.lat.toFixed(3) + "," + k.lon.toFixed(3)));
    if (muxOpen || layers.sdrActive) {
      activeSdrs().forEach((k) => {
        if (!Number.isFinite(k.lat) || !Number.isFinite(k.lon)) return;
        seenSdr.add(sdrKey(k));
        rows.push({
          ...k,
          lng: k.lon,
          kind: k._mux
            ? "mux_kiwi"
            : k._kind === "buddy_kiwi"
              ? "buddy_kiwi"
              : k._kind === "beam_kiwi"
                ? "beam_kiwi"
                : "kiwi",
        });
      });
    }
    if (layers.sdrPotential) {
      potentialSdrs().forEach((k) => {
        if (!Number.isFinite(k.lat) || !Number.isFinite(k.lon)) return;
        if (seenSdr.has(sdrKey(k))) return;
        seenSdr.add(sdrKey(k));
        rows.push({ ...k, lng: k.lon, kind: "kiwi_all" });
      });
    }
    const c = fleetCenter();
    if (c) {
      rows.push({ lat: c.lat, lon: c.lon, lng: c.lon, kind: "centroid", name: t("centroid_name") });
    }
    trafics.forEach((v) => {
      rows.push({
        ...v,
        lng: v.lon,
        kind: "trafic",
        name: v.title || v.id,
      });
    });
    listedTxSites().forEach((s, i) => {
      rows.push({ ...s, lng: s.lon, kind: "tx", _idx: i, name: s.label || t("tx_name") });
    });
    kiwiKmPoints().forEach((p) => rows.push(p));
    txKmPoints().forEach((p) => rows.push(p));
    htmlBannerPoints().forEach((p) => rows.push(p));
    metareaLabelPoints().forEach((p) => rows.push(p));
    subzoneLabelPoints().forEach((p) => rows.push(p));
    return rows;
  }

  function fmtLatLon(lat, lon) {
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return "";
    const ns = lat >= 0 ? "N" : "S";
    const ew = lon >= 0 ? "E" : "W";
    return Math.abs(lat).toFixed(3) + "°" + ns + " " + Math.abs(lon).toFixed(3) + "°" + ew;
  }

  function skipperTipHtml(d) {
    const row = (label, val) =>
      val == null || val === ""
        ? ""
        : `<div class="globe-skipper-tip__row"><span>${esc(label)}</span><strong>${esc(val)}</strong></div>`;
    const kn = (x) => (Number.isFinite(x) ? String(x).replace(".", ",") + " kn" : "");
    const nm = (x) => (Number.isFinite(x) ? String(x).replace(".", ",") + " NM" : "");
    const deg = (x) => (Number.isFinite(x) ? String(Math.round(x)).padStart(3, "0") + "°" : "");
    const vent = [kn(d.wind_kn), deg(d.wind_deg)].filter(Boolean).join(" @ ");
    const col = d.colour
      ? `<span class="globe-skipper-tip__swatch" style="background:${esc(d.colour)}"></span>`
      : "";
    return (
      `<header>${col}<div><p class="globe-skipper-tip__kicker">${esc(t("skipper"))}</p><h3>${esc(d.name || "—")}</h3></div></header>` +
      `<div class="globe-skipper-tip__grid">` +
      row(t("fld_sail"), d.sail) +
      row(t("fld_rank"), d.rank != null ? String(d.rank) : "") +
      row(t("fld_status"), d.status) +
      row(t("fld_flag"), [d.flag, d.country].filter(Boolean).join(" · ")) +
      row(t("fld_boat"), d.model) +
      row(t("fld_owner"), d.owner) +
      row(t("fld_pos"), fmtLatLon(d.lat, d.lon)) +
      row(t("fld_hdg"), deg(d.heading)) +
      row(t("fld_sog"), kn(d.sog_kn)) +
      row(t("fld_vmg"), kn(d.vmg_kn)) +
      row(t("fld_dtf"), nm(d.dtf_nm)) +
      row(t("fld_24h"), nm(d.d24_nm)) +
      row(t("fld_dmg"), nm(d.dmg_nm)) +
      row(t("fld_gps"), d.gps_at) +
      row(t("fld_eta"), d.finish_at) +
      row(t("fld_wind"), vent) +
      `</div>`
    );
  }

  let skipperTipHide = 0;

  function skipperTipEl() {
    let tip = document.getElementById("globe-skipper-tip");
    if (tip) return tip;
    tip = document.createElement("aside");
    tip.id = "globe-skipper-tip";
    tip.className = "globe-skipper-tip";
    tip.hidden = true;
    const host = document.querySelector(".globe-app") || document.body;
    host.appendChild(tip);
    return tip;
  }

  function placeSkipperTip(ev) {
    const tip = skipperTipEl();
    const host = document.querySelector(".globe-app") || document.body;
    const r = host.getBoundingClientRect();
    tip.hidden = false;
    const tw = tip.offsetWidth || 264;
    const th = tip.offsetHeight || 280;
    let x = ev.clientX - r.left + 16;
    let y = ev.clientY - r.top + 16;
    if (x + tw > r.width - 10) x = ev.clientX - r.left - tw - 14;
    if (x < 8) x = 8;
    if (y + th > r.height - 10) y = ev.clientY - r.top - th - 14;
    if (y < 8) y = 8;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }

  function showSkipperTip(d, ev) {
    window.clearTimeout(skipperTipHide);
    const tip = skipperTipEl();
    tip.innerHTML = skipperTipHtml(d);
    placeSkipperTip(ev);
  }

  function hideSkipperTip() {
    window.clearTimeout(skipperTipHide);
    skipperTipHide = window.setTimeout(() => {
      const tip = document.getElementById("globe-skipper-tip");
      if (tip) tip.hidden = true;
    }, 80);
  }

  function markerEl(d) {
    const wrap = document.createElement("div");
    wrap.className = "globe-mark globe-mark--" + d.kind + (d.kind === "boat" && d.inBuddy ? " is-buddy" : "") + (d.kind === "mux_kiwi" || d.pulse ? " globe-mark--pulse" : "");
    wrap.style.cssText =
      d.kind === "boat"
        ? "width:22px;height:22px;margin:0;padding:0;overflow:visible;pointer-events:auto;"
        : d.kind === "metarea" || d.kind === "subzone"
          ? "width:auto;height:auto;margin:0;padding:0;overflow:visible;pointer-events:none;"
          : "width:12px;height:12px;margin:0;padding:0;overflow:visible;";
    if (d.kind === "metarea" || d.kind === "subzone") {
      const lab = document.createElement("span");
      lab.className = "globe-mark__metarea";
      lab.textContent = d.roman || d.name || "";
      wrap.appendChild(lab);
      wrap.title = d.name || "";
      return wrap;
    }
    if (d.kind === "link_km" || d.kind === "tx_km") {
      const km = document.createElement("span");
      km.className = "globe-mark__km";
      km.textContent = d.name || "";
      wrap.appendChild(km);
      wrap.title = d.name || "";
      if (d.pulse) wrap.classList.add("globe-mark--pulse");
      const row = {
        el: km,
        tanA: d.tanA,
        tanB: d.tanB,
        fallback: Number.isFinite(d.alongDeg) ? d.alongDeg : 0,
      };
      kmLabelNodes.push(row);
      applyKmLabelAngle(row);
      return wrap;
    }
    if (d.kind === "ggr_banner") {
      const lab = document.createElement("span");
      lab.className = "ggr-banner";
      lab.textContent = d.name || "GGR 2026 — trafic HF";
      wrap.style.opacity = String(d.opacity != null ? d.opacity : 1);
      wrap.appendChild(lab);
      return wrap;
    }
    const icon = document.createElement("span");
    icon.className = "globe-mark__icon";
    if (d.kind === "boat") {
      icon.style.borderBottomColor = d.inBuddy ? "#e8c547" : d.colour || "#8aa4a8";
      const rot = Number.isFinite(d.heading) ? " rotate(" + d.heading + "deg)" : "";
      icon.style.transform = "translate(-50%,-50%)" + rot;
    } else if (d.kind === "trafic" || d.kind === "vacation") {
      icon.style.background = d.is_buddy ? "#d4a84b" : "#d45c3a";
    } else if (d.kind === "kiwi_all") {
      wrap.classList.add("globe-mark--pulse");
      wrap.style.width = "4px";
      wrap.style.height = "4px";
    } else if (d.kind === "mux_kiwi") {
      icon.style.background = "#7dffb0";
      icon.style.boxShadow = "0 0 8px #3dba7a";
    }
    wrap.appendChild(icon);
    const sdrKinds = d.kind === "kiwi" || d.kind === "buddy_kiwi" || d.kind === "beam_kiwi" || d.kind === "mux_kiwi" || d.kind === "tx";
    const showLab =
      (d.kind === "boat" && layers.labelsBoats) || (sdrKinds && layers.labelsSdr);
    if (showLab) {
      const name = document.createElement("span");
      name.className = "globe-mark__name";
      name.style.cssText = "position:absolute;left:14px;top:50%;transform:translateY(-50%);white-space:nowrap;";
      name.textContent =
        d.kind === "kiwi" || d.kind === "buddy_kiwi" || d.kind === "beam_kiwi" || d.kind === "mux_kiwi"
          ? sdrLabel(d)
          : d.name || "";
      wrap.appendChild(name);
    }
    if (d.kind !== "boat") {
      wrap.title = d.kind === "kiwi" || d.kind === "buddy_kiwi" || d.kind === "beam_kiwi" ? sdrLabel(d) : d.name || d.label || d.kind;
    }
    const pick = (ev) => {
      ev.stopPropagation();
      onPointClick(d);
    };
    icon.addEventListener("click", pick);
    if (d.kind === "boat") {
      wrap.addEventListener("click", pick);
      wrap.addEventListener("mouseenter", (ev) => showSkipperTip(d, ev));
      wrap.addEventListener("mousemove", (ev) => placeSkipperTip(ev));
      wrap.addEventListener("mouseleave", hideSkipperTip);
    }
    return wrap;
  }

  function paths() {
    return boats
      .map((b) => {
        if (!layers.boats) return null;
        const pts = (b.track || [])
          .filter((p) => Array.isArray(p) && p.length >= 2 && Number.isFinite(p[0]) && Number.isFinite(p[1]))
          .map((p) => [p[0], p[1]]);
        if (Number.isFinite(b.lat) && Number.isFinite(b.lon)) {
          const last = pts[pts.length - 1];
          if (!last || last[0] !== b.lat || last[1] !== b.lon) pts.push([b.lat, b.lon]);
        }
        if (pts.length < 2) return null;
        return {
          coords: pts,
          color: b.colour || "#c9a227",
          stroke: 1,
        };
      })
      .filter(Boolean)
      .concat(fleetRingPaths())
      .concat(kiwiLinkPaths())
      .concat(txLinkPaths());
  }

  function sdrCity(d) {
    const loc = String(d && d.loc ? d.loc : "").trim();
    if (loc) {
      const city = loc.split(",")[0].trim();
      if (city) return city;
    }
    const raw = String(d && d.name ? d.name : "").trim();
    if (!raw) return "";
    if (raw.includes(" | ")) {
      const tail = raw.split(" | ").pop().trim().split(",")[0].trim();
      if (tail) return tail.replace(/\s+\d{4,5}\b.*$/, "").trim() || tail;
    }
    const parts = raw.split(",").map((p) => p.trim()).filter(Boolean);
    if (parts.length >= 2) return parts[parts.length - 2];
    return "";
  }

  function sdrLabel(d) {
    const city = sdrCity(d);
    return city ? "sdr, " + city : "sdr";
  }

  function sdrSites() {
    const seen = new Set();
    const out = [];
    const add = (k, kind) => {
      if (!Number.isFinite(k.lat) || !Number.isFinite(k.lon)) return;
      const key = String(k.id || k.host || k.lat.toFixed(3) + "," + k.lon.toFixed(3));
      if (seen.has(key)) return;
      seen.add(key);
      out.push({ ...k, _kind: kind });
    };
    (data.beam_kiwis || []).forEach((k) => add(k, "beam_kiwi"));
    (data.buddy_kiwis || []).forEach((k) => add(k, "buddy_kiwi"));
    (data.kiwis || []).forEach((k) => add(k, "fleet_kiwi"));
    return out;
  }

  function fleetRingPaths() {
    const center = fleetCenter();
    if (!center) return [];
    return [
      {
        coords: circleCoords(center.lat, center.lon, fleetRingKm(center), 72),
        color: "rgba(244,230,195,0.55)",
        stroke: 1,
      },
    ];
  }

  // Tirets SDR : ~20 km / trou 16 km. pathStroke 2 = 2 px écran (Line2), pas arcStroke.
  const SDR_DASH_KM = 20;
  const SDR_GAP_KM = 16;
  const SDR_PATH_ALT = 0.0018;

  function geodesicDashed(lat1, lon1, lat2, lon2, color, extra) {
    const d = haversineKm(lat1, lon1, lat2, lon2);
    const brg = bearingDeg(lat1, lon1, lat2, lon2);
    if (!Number.isFinite(d) || d < 1) return [];
    const out = [];
    let along = 0;
    while (along < d - 0.2) {
      const dashEnd = Math.min(along + SDR_DASH_KM, d);
      const a = destPoint(lat1, lon1, brg, along);
      const b = destPoint(lat1, lon1, brg, dashEnd);
      out.push({
        coords: [
          [a[0], a[1], SDR_PATH_ALT],
          [b[0], b[1], SDR_PATH_ALT],
        ],
        color: extra && extra.pulse && muxPulseFat ? "#c8ffdc" : color,
        stroke: extra && extra.pulse ? (muxPulseFat ? 3.8 : 2.15) : 2,
        pulse: !!(extra && extra.pulse),
        dash: false,
      });
      along = dashEnd + SDR_GAP_KM;
    }
    return out;
  }

  function parseFmt(fmt) {
    const m = String(fmt || "").match(
      /([0-9]+(?:\.[0-9]+)?)\s*°\s*([NSns])\s+([0-9]+(?:\.[0-9]+)?)\s*°\s*([EWew])/
    );
    if (!m) return null;
    const lat = Number(m[1]) * (m[2].toUpperCase() === "S" ? -1 : 1);
    const lon = Number(m[3]) * (m[4].toUpperCase() === "W" ? -1 : 1);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    return { lat: lat, lon: lon };
  }

  function liveKiwiByName(name) {
    const want = String(name || "").trim().toLowerCase();
    if (!want) return null;
    const pool = []
      .concat(data.beam_kiwis || [])
      .concat(data.buddy_kiwis || [])
      .concat(data.kiwis || [])
      .concat(kiwisAll);
    return pool.find((k) => String(k.name || "").trim().toLowerCase() === want) || null;
  }

  function muxSdrs() {
    if (!muxOpen) return [];
    const center = muxCenter();
    const focus = muxFocusId;
    const rows = [];
    const seen = new Set();
    (muxOpen.channels || []).forEach((ch) => {
      const kiwi = ch.kiwi && typeof ch.kiwi === "object" ? ch.kiwi : {};
      const name = kiwi.name || (typeof ch.kiwi === "string" ? ch.kiwi : "") || ch.place || ch.id;
      let lat = Number(kiwi.lat != null ? kiwi.lat : ch.lat);
      let lon = Number(kiwi.lon != null ? kiwi.lon : ch.lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
        const parsed = parseFmt(kiwi.fmt || ch.fmt);
        if (parsed) {
          lat = parsed.lat;
          lon = parsed.lon;
        }
      }
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
        const hit = liveKiwiByName(name);
        if (hit) {
          lat = hit.lat;
          lon = hit.lon;
        }
      }
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
      const key = lat.toFixed(3) + "," + lon.toFixed(3);
      if (seen.has(key)) return;
      seen.add(key);
      let km = Number(kiwi.site_km != null ? kiwi.site_km : ch.site_km);
      if (!Number.isFinite(km) && center) km = haversineKm(center.lat, center.lon, lat, lon);
      const id = ch.id || name;
      if (focus && id !== focus && name !== focus) return;
      rows.push({
        ...kiwi,
        ...ch,
        name: name,
        lat: lat,
        lon: lon,
        loc: kiwi.loc || ch.loc || ch.place,
        site_km: km,
        _mux: true,
        _kind: "mux_kiwi",
      });
    });
    return rows;
  }

  function muxCenter() {
    if (!muxOpen) return null;
    const lat = Number(muxOpen.lat);
    const lon = Number(muxOpen.lon);
    if (Number.isFinite(lat) && Number.isFinite(lon)) return { lat: lat, lon: lon };
    return fleetCenter();
  }

  function muxView() {
    const center = muxCenter();
    const pts = [];
    if (center) pts.push([center.lat, center.lon]);
    muxSdrs().forEach((k) => pts.push([k.lat, k.lon]));
    if (!pts.length) return center ? { lat: center.lat, lng: center.lon, altitude: 1.8 } : null;
    let latMin = Infinity;
    let latMax = -Infinity;
    let lonMin = Infinity;
    let lonMax = -Infinity;
    pts.forEach((p) => {
      latMin = Math.min(latMin, p[0]);
      latMax = Math.max(latMax, p[0]);
      lonMin = Math.min(lonMin, p[1]);
      lonMax = Math.max(lonMax, p[1]);
    });
    const lat = (latMin + latMax) / 2;
    const lng = (lonMin + lonMax) / 2;
    const latSpan = Math.max(latMax - latMin, 0.8);
    const lonSpan = Math.max((lonMax - lonMin) * Math.cos((lat * Math.PI) / 180), 0.8);
    const spanDeg = Math.max(latSpan, lonSpan);
    const altitude = Math.min(2.85, Math.max(1.45, (spanDeg / 180) * 8));
    return { lat: lat, lng: lng, altitude: altitude };
  }

  function activeSdrs() {
    if (muxOpen) return muxSdrs();
    return sdrSites();
  }

  function potentialSdrs() {
    return kiwisAll;
  }

  function kiwiLinkPaths() {
    if (!muxOpen && !layers.sdrActive) return [];
    const center = muxOpen ? muxCenter() : fleetCenter();
    if (!center) return [];
    const rows = [];
    const pulse = !!muxOpen;
    activeSdrs().forEach((k) => {
      const nvis = k.prop_zone === "nvis" || k._kind === "fleet_kiwi" || k._mux;
      const color = pulse
        ? "#7dffb0"
        : k._kind === "beam_kiwi"
          ? "#d4a15c"
          : nvis
            ? "#3dba7a"
            : "#6ec9e0";
      geodesicDashed(center.lat, center.lon, k.lat, k.lon, color, { pulse: pulse }).forEach((p) => rows.push(p));
    });
    return rows;
  }

  function kiwiKmPoints() {
    if (!muxOpen && !layers.sdrActive) return [];
    const center = muxOpen ? muxCenter() : fleetCenter();
    if (!center) return [];
    return activeSdrs().map((k) => {
      const km = Number.isFinite(k.site_km) ? k.site_km : haversineKm(center.lat, center.lon, k.lat, k.lon);
      const samp = pathMidSamples(center.lat, center.lon, k.lat, k.lon);
      const city = sdrCity(k) || k.name || "sdr";
      return {
        lat: samp.mid[0],
        lon: samp.mid[1],
        lng: samp.mid[1],
        kind: "link_km",
        name: city + " · " + Math.round(km) + " km",
        alongDeg: samp.fallback,
        tanA: samp.tanA,
        tanB: samp.tanB,
        pulse: !!muxOpen,
      };
    });
  }

  function listedTxSites() {
    return txSites.filter((s) => Number.isFinite(s.lat) && Number.isFinite(s.lon)).slice(0, TX_MAX);
  }

  function fmtAz(deg) {
    const n = ((Math.round(deg) % 360) + 360) % 360;
    return String(n).padStart(3, "0") + "°";
  }

  function txAim(s) {
    const center = fleetCenter();
    if (!center || !Number.isFinite(s.lat) || !Number.isFinite(s.lon)) return null;
    const km = haversineKm(s.lat, s.lon, center.lat, center.lon);
    const az = bearingDeg(s.lat, s.lon, center.lat, center.lon);
    return { km, az, center };
  }

  function txLinkPaths() {
    const center = fleetCenter();
    if (!center) return [];
    const rows = [];
    listedTxSites().forEach((s) => {
      geodesicDashed(s.lat, s.lon, center.lat, center.lon, "#e8c547").forEach((p) => rows.push(p));
    });
    return rows;
  }

  function txKmPoints() {
    return listedTxSites()
      .map((s) => {
        const aim = txAim(s);
        if (!aim || !Number.isFinite(aim.km) || aim.km < 1) return null;
        const samp = pathMidSamples(s.lat, s.lon, aim.center.lat, aim.center.lon);
        return {
          lat: samp.mid[0],
          lon: samp.mid[1],
          lng: samp.mid[1],
          kind: "tx_km",
          name: Math.round(aim.km) + " km · " + fmtAz(aim.az),
          alongDeg: samp.fallback,
          tanA: samp.tanA,
          tanB: samp.tanB,
        };
      })
      .filter(Boolean);
  }

  function setPlacingTx(on) {
    placingTx = !!on;
    el.classList.toggle("is-placing-tx", placingTx);
    if (mapEl) mapEl.classList.toggle("is-placing-tx", placingTx);
    if (txPlaceBtn) {
      txPlaceBtn.classList.toggle("is-placing", placingTx);
      txPlaceBtn.textContent = placingTx
        ? mapMode === "2d"
          ? t("tx_click_map")
          : t("tx_click_globe")
        : t("place_qth");
    }
  }

  function renderTxList() {
    if (!txListEl) return;
    const rows = listedTxSites();
    if (!rows.length) {
      txListEl.innerHTML = "<li class=\"meta\">" + esc(t("tx_none", { n: TX_MAX })) + "</li>";
      return;
    }
    txListEl.innerHTML = rows
      .map((s, i) => {
        const aim = txAim(s);
        const stats = aim
          ? Math.round(aim.km) + " km · az. " + fmtAz(aim.az)
          : t("centroid_unavail");
        return (
          "<li>" +
          "<input type=\"text\" maxlength=\"64\" data-tx-label=\"" +
          i +
          "\" value=\"" +
          esc(s.label || "") +
          "\" aria-label=\"" +
          esc(t("tx_qth_name")) +
          "\">" +
          "<span class=\"tx-list__aim\">" +
          esc(stats) +
          "</span>" +
          "<button type=\"button\" class=\"btn\" data-tx-del=\"" +
          i +
          "\">" +
          esc(t("tx_remove")) +
          "</button>" +
          "</li>"
        );
      })
      .join("");
  }

  function fleetPolygons() {
    const center = fleetCenter();
    if (!center) return [];
    const ring = circleCoords(center.lat, center.lon, fleetRingKm(center), 72).map(([lat, lon]) => [lon, lat]);
    return [{ geometry: { type: "Polygon", coordinates: [ring] } }];
  }

  function fleetView() {
    const sel = selectedBoats();
    const pts = (sel.length ? sel : boats).map((b) => [b.lat, b.lon]);
    const c = fleetCenter() || data.centroid || {};
    if (!pts.length) {
      return {
        lat: Number.isFinite(c.lat) ? c.lat : 25,
        lng: Number.isFinite(c.lon) ? c.lon : -15,
        altitude: 2.2,
      };
    }
    let latMin = Infinity;
    let latMax = -Infinity;
    let lonMin = Infinity;
    let lonMax = -Infinity;
    pts.forEach((p) => {
      latMin = Math.min(latMin, p[0]);
      latMax = Math.max(latMax, p[0]);
      lonMin = Math.min(lonMin, p[1]);
      lonMax = Math.max(lonMax, p[1]);
    });
    const lat = (latMin + latMax) / 2;
    const lng = (lonMin + lonMax) / 2;
    const latSpan = Math.max(latMax - latMin, 0.4);
    const lonSpan = Math.max((lonMax - lonMin) * Math.cos((lat * Math.PI) / 180), 0.4);
    const spanDeg = Math.max(latSpan, lonSpan);
    const altitude = Math.min(0.55, Math.max(0.18, (spanDeg / 180) * 2.5 * 2.4));
    return { lat, lng, altitude };
  }

  function renderSkippers() {
    if (!skipEl) return;
    skipEl.querySelectorAll('input[name="buddy_skipper"], input[data-skipper]').forEach((inp) => {
      const name = inp.value || inp.getAttribute("data-skipper");
      if (name) inp.checked = boatInBuddy(name);
    });
  }

  if (skipEl) {
    skipEl.addEventListener("change", (ev) => {
      const inp = ev.target;
      if (!inp || inp.type !== "checkbox") return;
      const name = inp.value || inp.getAttribute("data-skipper");
      if (!name) return;
      if (inp.checked) skippers.add(name);
      else skippers.delete(name);
      refreshGlobe();
    });
  }

  function audioSdrs(v) {
    if (Number.isFinite(v.sdrs)) return Math.max(0, v.sdrs);
    const keys = new Set();
    (v.channels || []).forEach((c, i) => {
      if (!(c.has_audio || c.audio)) return;
      keys.add(c.kiwi || c.id || String(i));
    });
    return keys.size;
  }

  function fillVacList(root, rows, emptyText) {
    if (!root) return;
    if (!rows.length) {
      root.innerHTML = `<li class="hint">${esc(emptyText)}</li>`;
      return;
    }
    root.innerHTML = rows
      .map((v) => {
        let when = (v.air_at || v.started_at || "").replace("T", " ").slice(0, 16);
        if (when) when += " TU";
        const tag = v.is_buddy ? "Buddy call" : v.is_test ? t("vac_test") : t("vac_bulletin");
        const n = audioSdrs(v);
        const title = v.title && v.title !== v.id ? " · " + esc(v.title) : "";
        return (
          `<li class="globe-vac-row" data-vid="${esc(v.id)}">` +
          `<button type="button" class="globe-vac" data-vid="${esc(v.id)}" aria-expanded="false">` +
          `<span class="globe-vac__arr" aria-hidden="true"></span>` +
          `<span class="globe-vac__body">` +
          `<span class="badge">${esc(tag)}</span>` +
          `<span class="globe-vac__when">${esc(when)}${title}</span>` +
          `<span class="globe-vac__sdr">${n} SDR</span>` +
          `</span></button></li>`
        );
      })
      .join("");
    root.querySelectorAll(".globe-vac").forEach((btn) => {
      btn.addEventListener("click", () => {
        const v = (data.trafics || data.vacations || []).find((x) => x.id === btn.getAttribute("data-vid"));
        if (v) playTrafic(v);
      });
    });
  }

  function renderVacList() {
    const all = data.trafics || data.vacations || [];
    fillVacList(vacsEl, all, t("vac_empty"));
  }

  function mixerTracks(v) {
    return (v.channels || []).map((c, i) => ({
      id: c.id || String(i),
      src: c.play || c.audio || "",
      wav: c.wav || "",
      freq_khz: c.freq_khz,
      place: c.place || c.loc,
      site_label: c.site_label,
      label: c.label,
      has_audio: !!(c.has_audio || c.audio),
      waterfall: c.waterfall || "",
    }));
  }

  function mountMuxMixer(v, tracks) {
    if (openVid !== v.id || !window.GgrMixer) return;
    const q = new URLSearchParams(location.search);
    const zoom = deepZoom || q.get("zoom") === "1" || q.get("zoom") === "true" || q.has("ch");
    const focus = deepFocus || q.get("ch") || q.get("focus") || (zoom ? "0" : "");
    const deepTime = deepT || q.get("t") || q.get("at") || "";
    window.GgrMixer.mount({
      root: mixRoot,
      tracks: tracks,
      vacId: v.id,
      started: v.started_at,
      zoom: zoom,
      focus: focus,
      t: deepTime,
      onFocus: function (trackId) {
        muxFocusId = trackId || null;
        document.body.classList.toggle("kiwi-mix-focus", !!trackId);
        refreshGlobe();
        window.dispatchEvent(new Event("resize"));
      },
    });
  }

  async function playTrafic(v) {
    setPanelOpen(true);
    showTab("trafic");
    if (openVid === v.id) {
      parkMix();
      return;
    }
    parkMix();
    const li = vacsEl && vacsEl.querySelector('.globe-vac-row[data-vid="' + CSS.escape(v.id) + '"]');
    if (!li || !mixRoot) return;
    li.classList.add("is-open");
    const hdr = li.querySelector(".globe-vac");
    if (hdr) hdr.setAttribute("aria-expanded", "true");
    li.appendChild(mixRoot);
    mixRoot.setAttribute("data-vid", v.id || "");
    mixRoot.setAttribute("data-started", v.started_at || "");
    openVid = v.id;
    noteNav("/trafic/" + v.id);
    muxOpen = Object.assign({}, v);
    mixRoot.hidden = false;
    document.body.classList.add("kiwi-mix-on");
    startMuxPulse();
    refreshGlobe();
    const listed = mixerTracks(v);
    mountMuxMixer(v, listed);
    li.scrollIntoView({ block: "nearest" });
    const view = muxView();
    if (view) lookAt(view.lat, view.lng, view.altitude, 900);
    try {
      const full = await fetch("/api/trafic/" + encodeURIComponent(v.id)).then((r) => r.json());
      if (openVid !== v.id) return;
      muxOpen = Object.assign({}, v, full || {});
      refreshGlobe();
      let next = listed;
      if (Array.isArray(full.mixer_tracks) && full.mixer_tracks.length) next = full.mixer_tracks;
      else if ((full.channels || []).length) next = mixerTracks(full);
      const richer = next.some((t, i) => (t.src || "") && (t.src || "") !== ((listed[i] && listed[i].src) || ""));
      if (richer) mountMuxMixer(v, next);
    } catch {
      /* carte globe : lat/lon déjà dans la liste */
    }
  }

  function onPointClick(d) {
    if (!d) return;
    if (d.kind === "boat") {
      const on = boatInBuddy(d.name);
      const yb = (label, val) =>
        val == null || val === "" ? "" : `<div class="globe-yb"><span>${esc(label)}</span><strong>${esc(val)}</strong></div>`;
      const kn = (x) => (Number.isFinite(x) ? String(x).replace(".", ",") + " kn" : null);
      const nm = (x) => (Number.isFinite(x) ? String(x).replace(".", ",") + " NM" : null);
      const deg = (x) => (Number.isFinite(x) ? String(Math.round(x)).padStart(3, "0") + "°" : null);
      const sog = kn(d.sog_kn);
      const spd = sog && deg(d.heading) ? sog + " @ " + deg(d.heading) : sog;
      const wind = kn(d.wind_kn);
      const vent = wind && deg(d.wind_deg) ? wind + " @ " + deg(d.wind_deg) : wind;
      showSel(
        `<p class="badge">${esc(t("skipper"))}</p><h3>${esc(d.name)}</h3>` +
          (d.owner || d.model || d.sail
            ? `<p class="meta">${esc([d.owner, d.model, d.sail ? t("sail_word", { sail: d.sail }) : ""].filter(Boolean).join(" · "))}</p>`
            : "") +
          (d.country ? `<p class="meta">${esc(d.country)}</p>` : "") +
          yb(t("fld_gps"), d.gps_at) +
          yb(t("speed"), spd) +
          yb(t("wind"), vent) +
          yb(t("fld_dtf"), nm(d.dtf_nm)) +
          yb(t("fld_24h"), nm(d.d24_nm)) +
          yb(t("fld_dmg"), nm(d.dmg_nm)) +
          yb(t("fld_vmg"), kn(d.vmg_kn)) +
          yb(t("fld_rank"), d.rank != null ? String(d.rank) : null) +
          yb(t("fld_eta"), d.finish_at) +
          (d.status && d.status !== "RACING" ? yb(t("fld_status"), d.status) : "") +
          `<p class="settings__actions"><button type="button" class="btn" id="globe-toggle-skip">` +
          `${on ? t("skip_remove") : t("skip_add")}</button></p>`
      );
      const btn = document.getElementById("globe-toggle-skip");
      if (btn) {
        btn.addEventListener("click", () => {
          if (skippers.has(d.name)) skippers.delete(d.name);
          else skippers.add(d.name);
          renderSkippers();
          refreshGlobe();
          onPointClick({ ...d, inBuddy: skippers.has(d.name) });
        });
      }
      lookAt(d.lat, d.lon, 1.4, 800);
      return;
    }
    if (d.kind === "kiwi" || d.kind === "buddy_kiwi" || d.kind === "beam_kiwi" || d.kind === "mux_kiwi" || d.kind === "kiwi_all") {
      const zone = d.prop_zone ? ` · ${esc(t("zone"))} ${esc(zoneLabel(d.prop_zone))}` : "";
      const km = d.site_km != null ? d.site_km : d.distance_km;
      const badge =
        d.kind === "buddy_kiwi"
          ? t("badge_buddy")
          : d.kind === "beam_kiwi"
            ? t("badge_beam")
            : d._mux
              ? t("mux_kiwi")
              : t("badge_kiwi");
      showSel(
        `<p class="badge">${badge}</p>` +
          `<h3>${esc(d.name)}</h3>` +
          `<p class="meta">${esc(d.loc || "")}${d.site_label ? " · " + esc(siteLabel(d.site_label)) : ""}</p>` +
          `<p>${km != null ? km + " km" : ""} · SNR HF ${esc(d.snr_hf)} · ${esc(d.free_slots)}/${esc(d.users_max)} ${esc(t("kiwi_slots"))}${zone}</p>` +
          (d.url ? `<p><a href="${esc(d.url)}" rel="noreferrer">${esc(t("open_kiwi"))}</a></p>` : "")
      );
      lookAt(d.lat, d.lng || d.lon, 1.5, 800);
      return;
    }
    if (d.kind === "trafic" || d.kind === "vacation") {
      playTrafic(d);
      return;
    }
    if (d.kind === "tx") {
      const aim = txAim(d);
      const pos = Number.isFinite(d.lat)
        ? d.lat.toFixed(4) + ", " + d.lon.toFixed(4)
        : "";
      showSel(
        `<p class="badge">${esc(t("tx_on_air"))}</p><h3>${esc(d.name || d.label || "QTH")}</h3>` +
          `<p class="meta">${esc(d.loc || pos)}</p>` +
          (aim
            ? `<p>${esc(t("dist_centroid"))} : <strong>${Math.round(aim.km)} km</strong></p>` +
              `<p>${esc(t("az_antenna"))} : <strong>${esc(fmtAz(aim.az))}</strong></p>`
            : "<p class=\"hint\">" + esc(t("centroid_unavail_p")) + "</p>")
      );
      lookAt(d.lat, d.lon, 1.6, 800);
      return;
    }
    showSel(`<h3>${esc(d.name || d.label || d.kind)}</h3><p class="meta">${esc(d.fmt || "")}</p>`);
    lookAt(d.lat, d.lng || d.lon, 1.8, 800);
  }

  function altitudeToZoom(alt) {
    const a = Number(alt);
    if (!Number.isFinite(a)) return 5;
    // globe.gl altitude (rayons) → Leaflet zoom (approx. visuelle).
    return Math.max(2, Math.min(15, Math.round(9.2 - a * 2.4)));
  }

  function zoomToAltitude(zoom) {
    const z = Number(zoom);
    if (!Number.isFinite(z)) return 1.5;
    return Math.max(0.12, Math.min(3.8, (9.2 - z) / 2.4));
  }

  /** Vue courante (avant bascule 2D/3D). */
  function readView(fromMode) {
    if (fromMode === "2d" && map && typeof map.getCenter === "function") {
      const c = map.getCenter();
      const z = map.getZoom();
      if (!c || !Number.isFinite(c.lat) || !Number.isFinite(c.lng)) return null;
      return { lat: c.lat, lng: c.lng, zoom: z, altitude: zoomToAltitude(z) };
    }
    if (fromMode !== "2d" && globe && typeof globe.pointOfView === "function") {
      try {
        const pov = globe.pointOfView();
        if (pov && Number.isFinite(pov.lat) && Number.isFinite(pov.lng)) {
          const alt = Number.isFinite(pov.altitude) ? pov.altitude : 1.5;
          return { lat: pov.lat, lng: pov.lng, altitude: alt, zoom: altitudeToZoom(alt) };
        }
      } catch {
        /* globe.gl */
      }
    }
    return null;
  }

  function applyKeptView(view, mode) {
    if (!view || !Number.isFinite(view.lat) || !Number.isFinite(view.lng)) return;
    if (mode === "2d" && map) {
      const z = Number.isFinite(view.zoom) ? view.zoom : altitudeToZoom(view.altitude);
      map.setView([view.lat, view.lng], z, { animate: false });
      return;
    }
    if (mode !== "2d" && globe && typeof globe.pointOfView === "function") {
      const alt = Number.isFinite(view.altitude) ? view.altitude : zoomToAltitude(view.zoom);
      globe.pointOfView({ lat: view.lat, lng: view.lng, altitude: alt }, 0);
    }
  }

  function lookAt(lat, lng, altitude, ms) {
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
    const dur = ms || 800;
    if (mapMode === "2d" && map) {
      map.flyTo([lat, lng], altitudeToZoom(altitude), { duration: Math.max(0.2, dur / 1000) });
      return;
    }
    if (globe) globe.pointOfView({ lat, lng, altitude: altitude || 1.5 }, dur);
    const t0 = performance.now();
    const tick = () => {
      alignKmLabels();
      if (performance.now() - t0 < dur + 60) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  function placeTxAt(lat, lng) {
    if (listedTxSites().length >= TX_MAX) {
      sayTx(t("tx_max"), false);
      setPlacingTx(false);
      return;
    }
    txSites = listedTxSites().concat([
      {
        label: t("tx_label_n", { n: listedTxSites().length + 1 }),
        lat: Math.round(lat * 1e5) / 1e5,
        lon: Math.round(lng * 1e5) / 1e5,
      },
    ]);
    setPlacingTx(false);
    renderTxList();
    refreshGlobe();
    sayTx(t("tx_placed"), true);
    showTab("setup");
  }

  // Bandeau GGR : or #DEB200 + Montserrat 800 (h1 goldengloberace.com).
  const GGR_BANNER = "GGR 2026 - trafic HF";
  const BANNER_INTRO_MS = 2500;
  const BANNER_ZOOM_MS = 2000;
  const BANNER_COAST_MS = 2000;
  const BANNER_PAN_ALT = 2.7;
  const BANNER_PAN_SPAN = 90;
  const BANNER_SPIN_RAD_MS = 0.00055;
  const BANNER_SPIN_DEG_MS = 0.032;
  const BANNER_OPACITY = 0.55;
  const BANNER_OPACITY_UNDER = 0.08;
  const BANNER_FLEET_LAT = 18;
  const bannerHtml = { on: false, lng0: 0, opacity: BANNER_OPACITY };
  let bannerStarting = false;
  let bannerBelt = null;
  let bannerIntroDone = false;
  let bannerWatchOn = false;
  let bannerReadable = false;
  let introPlaying = false;
  let introCamId = 0;
  let introOnBannerReady = null;
  let introBannerWait = 0;
  let introT0 = 0;

  function bannerSpinMs() {
    return BANNER_INTRO_MS + BANNER_ZOOM_MS + BANNER_COAST_MS;
  }

  function introShowsBanner() {
    return !!layers.banner && (introPlaying || bannerReadable);
  }

  function applyBannerVisibility() {
    const show = !!layers.banner && (!metareaOn || introShowsBanner());
    if (bannerBelt && bannerBelt.mesh) bannerBelt.mesh.visible = show;
  }

  function htmlBannerPoints() {
    if (!layers.banner || !bannerHtml.on || (metareaOn && !introShowsBanner())) return [];
    const n = 8;
    const rows = [];
    for (let i = 0; i < n; i++) {
      let lng = bannerHtml.lng0 + (360 * i) / n;
      lng = ((lng + 540) % 360) - 180;
      rows.push({
        lat: 0,
        lon: lng,
        lng,
        kind: "ggr_banner",
        name: GGR_BANNER,
        opacity: bannerHtml.opacity,
      });
    }
    return rows;
  }

  function boatsUnderBanner() {
    const near = (lat) => Number.isFinite(lat) && Math.abs(lat) < BANNER_FLEET_LAT;
    if (boats.some((b) => near(b.lat))) return true;
    const c = data.centroid || {};
    return near(c.lat);
  }

  function bannerTargetOpacity() {
    if (!bannerIntroDone) return BANNER_OPACITY;
    return boatsUnderBanner() ? BANNER_OPACITY_UNDER : BANNER_OPACITY;
  }

  function applyBannerOpacity(g, belt, snap) {
    const want = bannerTargetOpacity();
    const b = belt || bannerBelt;
    if (b && b.mat) {
      b.mat.opacity = snap ? want : b.mat.opacity + (want - b.mat.opacity) * 0.16;
    }
    const prev = bannerHtml.opacity;
    bannerHtml.opacity = want;
    if (bannerHtml.on && g && !b && prev !== want) g.htmlElementsData(points());
  }

  function watchBannerOpacity(g, belt) {
    if (bannerWatchOn) return;
    bannerWatchOn = true;
    bannerBelt = belt;
    const tick = () => {
      applyBannerOpacity(g, belt, false);
      window.requestAnimationFrame(tick);
    };
    window.requestAnimationFrame(tick);
  }

  function stealGlobeGfx(g) {
    const scene = typeof g.scene === "function" ? g.scene() : null;
    if (!scene || typeof scene.traverse !== "function") return null;
    const gfx = {};
    scene.traverse((o) => {
      if (!o || !o.isMesh || !o.geometry) return;
      if (!gfx.Mesh) gfx.Mesh = o.constructor;
      if (!gfx.Geo) {
        let p = o.geometry;
        for (let i = 0; i < 8 && p; i++) {
          const n = p.constructor && p.constructor.name;
          if (n === "BufferGeometry") {
            gfx.Geo = p.constructor;
            break;
          }
          p = Object.getPrototypeOf(p);
        }
        if (!gfx.Geo) gfx.Geo = o.geometry.constructor;
      }
      const pos = o.geometry.attributes && o.geometry.attributes.position;
      if (!gfx.Attr && pos) gfx.Attr = pos.constructor;
      if (!gfx.Texture && o.material && o.material.map) gfx.Texture = o.material.map.constructor;
      if (!gfx.sampleMat && o.material && o.material.clone) gfx.sampleMat = o.material;
    });
    if (!gfx.Texture && typeof g.globeMaterial === "function") {
      const gm = g.globeMaterial();
      if (gm && gm.map) gfx.Texture = gm.map.constructor;
    }
    return gfx.Mesh && gfx.Geo && gfx.Attr && gfx.Texture ? gfx : null;
  }

  function makeBeltGeometry(gfx, radius, height, segs) {
    const geo = new gfx.Geo();
    const n = segs + 1;
    const pos = new Float32Array(n * 2 * 3);
    const uv = new Float32Array(n * 2 * 2);
    const h = height / 2;
    for (let i = 0; i < n; i++) {
      const t = i / segs;
      const a = t * Math.PI * 2;
      const x = Math.cos(a) * radius;
      const z = Math.sin(a) * radius;
      const i0 = i * 2;
      pos[i0 * 3] = x;
      pos[i0 * 3 + 1] = -h;
      pos[i0 * 3 + 2] = z;
      pos[(i0 + 1) * 3] = x;
      pos[(i0 + 1) * 3 + 1] = h;
      pos[(i0 + 1) * 3 + 2] = z;
      uv[i0 * 2] = 1 - t;
      uv[i0 * 2 + 1] = 0;
      uv[(i0 + 1) * 2] = 1 - t;
      uv[(i0 + 1) * 2 + 1] = 1;
    }
    const idx = [];
    for (let i = 0; i < segs; i++) {
      const a = i * 2;
      idx.push(a, a + 2, a + 1, a + 1, a + 2, a + 3);
    }
    geo.setAttribute("position", new gfx.Attr(pos, 3));
    geo.setAttribute("uv", new gfx.Attr(uv, 2));
    if (typeof geo.setIndex === "function") geo.setIndex(idx);
    if (typeof geo.computeVertexNormals === "function") geo.computeVertexNormals();
    if (typeof geo.computeBoundingSphere === "function") geo.computeBoundingSphere();
    return geo;
  }

  function drawGgrBannerCanvas() {
    const c = document.createElement("canvas");
    c.width = 4096;
    c.height = 768;
    const ctx = c.getContext("2d");
    ctx.fillStyle = "rgba(8, 10, 12, 0.92)";
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.strokeStyle = "#DEB200";
    ctx.lineWidth = 36;
    ctx.beginPath();
    ctx.moveTo(0, 36);
    ctx.lineTo(c.width, 36);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(0, c.height - 36);
    ctx.lineTo(c.width, c.height - 36);
    ctx.stroke();
    ctx.fillStyle = "#DEB200";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    if (ctx.letterSpacing !== undefined) ctx.letterSpacing = "8px";
    const copies = 2;
    const text = GGR_BANNER.toUpperCase();
    let fontSize = 200;
    ctx.font = "800 " + fontSize + "px Montserrat, sans-serif";
    const slot = c.width / copies;
    const maxW = slot * 0.78;
    while (fontSize > 90 && ctx.measureText(text).width > maxW) {
      fontSize -= 6;
      ctx.font = "800 " + fontSize + "px Montserrat, sans-serif";
    }
    for (let i = 0; i < copies; i++) {
      ctx.fillText(text, (i + 0.5) * slot, c.height / 2 + 4);
    }
    return c;
  }

  function makeBannerMesh(g) {
    const gfx = stealGlobeGfx(g);
    const scene = typeof g.scene === "function" ? g.scene() : null;
    if (!gfx || !scene) return null;
    const radius = typeof g.getGlobeRadius === "function" ? g.getGlobeRadius() : 100;
    const canvas = drawGgrBannerCanvas();
    const tex = new gfx.Texture(canvas);
    tex.needsUpdate = true;
    tex.generateMipmaps = false;
    if ("minFilter" in tex && "magFilter" in tex) tex.minFilter = tex.magFilter;
    if ("colorSpace" in tex) tex.colorSpace = "srgb";
    let mat = null;
    if (gfx.sampleMat && typeof gfx.sampleMat.clone === "function") {
      try {
        mat = gfx.sampleMat.clone();
      } catch {
        mat = null;
      }
    }
    if (!mat && typeof g.globeMaterial === "function") {
      try {
        mat = g.globeMaterial().clone();
      } catch {
        mat = null;
      }
    }
    if (!mat) return null;
    mat.map = tex;
    if ("emissiveMap" in mat) mat.emissiveMap = tex;
    if (mat.emissive && typeof mat.emissive.setHex === "function") mat.emissive.setHex(0xffffff);
    if ("emissiveIntensity" in mat) mat.emissiveIntensity = 0.85;
    if (mat.color && typeof mat.color.setHex === "function") mat.color.setHex(0xffffff);
    mat.transparent = true;
    mat.opacity = BANNER_OPACITY;
    mat.depthWrite = false;
    mat.side = 1;
    if ("needsUpdate" in mat) mat.needsUpdate = true;
    const geo = makeBeltGeometry(gfx, radius * 1.08, radius * 0.39, 96);
    const mesh = new gfx.Mesh(geo, mat);
    mesh.name = "ggr-eq-banner";
    mesh.renderOrder = 4;
    mesh.visible = !!layers.banner && (!metareaOn || introShowsBanner());
    scene.add(mesh);
    return { mesh, mat };
  }

  function runBannerAnim(g, belt) {
    bannerBelt = belt;
    applyBannerVisibility();
    if (introOnBannerReady) {
      const fn = introOnBannerReady;
      introOnBannerReady = null;
      fn();
    }
    const start = introT0 || performance.now();
    if (!introT0) introT0 = start;
    const frame = (now) => {
      const elapsed = now - start;
      const limit = bannerSpinMs();
      if (elapsed < limit) {
        if (belt) belt.mesh.rotation.y = elapsed * BANNER_SPIN_RAD_MS;
        else {
          bannerHtml.lng0 = (elapsed * BANNER_SPIN_DEG_MS) % 360;
          if (g) g.htmlElementsData(points());
        }
        applyBannerOpacity(g, belt, true);
        window.requestAnimationFrame(frame);
      } else {
        freezeIntroBanner(g);
      }
    };
    window.requestAnimationFrame(frame);
  }

  function startHtmlBanner(g) {
    bannerHtml.on = true;
    bannerHtml.opacity = BANNER_OPACITY;
    if (g) g.htmlElementsData(points());
    runBannerAnim(g, null);
  }

  function startGgrEquatorBanner(g) {
    if (bannerStarting) return;
    bannerStarting = true;
    let tries = 0;
    const attempt = () => {
      try {
        const belt = makeBannerMesh(g);
        if (belt) {
          runBannerAnim(g, belt);
          return;
        }
      } catch {
        /* tuiles / THREE pas encore prêts */
      }
      tries += 1;
      if (tries < 25) {
        window.setTimeout(attempt, 100);
        return;
      }
      startHtmlBanner(g);
    };
    attempt();
  }

  function oceanImageUrl() {
    const c = document.createElement("canvas");
    c.width = 16;
    c.height = 8;
    const ctx = c.getContext("2d");
    ctx.fillStyle = "#0a3558";
    ctx.fillRect(0, 0, 16, 8);
    return c.toDataURL("image/png");
  }

  function findSlippyEngine(g) {
    const root = typeof g.scene === "function" ? g.scene() : null;
    if (!root) return null;
    let engine = null;
    root.traverse((n) => {
      if (engine) return;
      if (Array.isArray(n.thresholds) && "maxLevel" in n) engine = n;
    });
    return engine;
  }

  function tuneOsmTiles(g) {
    const OCEAN = 0x0a3558;
    const paint = (engine) => {
      if (!engine) return;
      engine.maxLevel = 8;
      engine.thresholds = [10, 8, 6, 4, 2, 1, 0.5, 0.25, 0.15, 0.08, 0.04, 0.02];
      engine.traverse((n) => {
        const mat = n.material;
        if (!mat) return;
        if (mat.isMeshBasicMaterial && !mat.map) {
          mat.color.setHex(OCEAN);
        }
        if (mat.isMeshLambertMaterial) {
          mat.transparent = true;
          if ("alphaTest" in mat) mat.alphaTest = 0.12;
        }
      });
      if (typeof g.controls === "function" && typeof engine.updatePov === "function") {
        try {
          engine.updatePov(g.controls().object);
        } catch {
          /* camera pas encore liée */
        }
      }
    };
    let tries = 0;
    const tick = () => {
      tries += 1;
      paint(findSlippyEngine(g));
      if (tries < 80) window.setTimeout(tick, 250);
    };
    tick();
    if (typeof g.controls === "function") {
      try {
        g.controls().addEventListener("change", () => paint(findSlippyEngine(g)));
      } catch {
        /* OrbitControls */
      }
    }
  }

  function freezeIntroBanner(g) {
    if (bannerReadable || bannerIntroDone) return;
    bannerReadable = true;
    introPlaying = false;
    applyBannerVisibility();
    applyBannerOpacity(g, bannerBelt, true);
  }

  function cancelIntro(g) {
    introPlaying = false;
    bannerReadable = false;
    bannerIntroDone = true;
    introCamId += 1;
    applyBannerVisibility();
    if (g && typeof g.polygonsData === "function") g.polygonsData(metareaPolygons());
    if (g) g.htmlElementsData(points());
    watchBannerOpacity(g, bannerBelt);
  }

  function scheduleIntroCamera(g, dest) {
    const id = ++introCamId;
    let launched = false;
    const start = () => {
      if (launched || id !== introCamId || muxOpen) return;
      launched = true;
      introOnBannerReady = null;
      if (introBannerWait) {
        window.clearTimeout(introBannerWait);
        introBannerWait = 0;
      }
      introT0 = performance.now();
      const pan = { lat: 8, lng: dest.lng, altitude: BANNER_PAN_ALT };
      if (g) g.pointOfView(pan, BANNER_INTRO_MS);
      window.setTimeout(() => {
        if (id !== introCamId || muxOpen) return;
        if (g && dest) g.pointOfView(dest, BANNER_ZOOM_MS);
      }, BANNER_INTRO_MS);
      window.setTimeout(() => {
        if (id !== introCamId) return;
        freezeIntroBanner(g);
      }, bannerSpinMs());
    };
    introOnBannerReady = start;
    introBannerWait = window.setTimeout(start, 1200);
  }

  function hexToRgba(hex, a) {
    const h = String(hex || "").replace("#", "");
    if (h.length !== 6) return "rgba(201,162,39," + a + ")";
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return "rgba(" + r + "," + g + "," + b + "," + a + ")";
  }

  function metareaPolygons() {
    const rows = [];
    if (introShowsBanner()) return rows;
    if (metareaOn && metareaFc) rows.push(...(metareaFc.features || []));
    if (subzonesOn && subzoneFc) rows.push(...(subzoneFc.features || []));
    return rows;
  }

  function metareaLabelPoints() {
    if (introShowsBanner()) return [];
    if (!metareaOn || !metareaFc) return [];
    return (metareaFc.features || [])
      .map((f) => {
        const p = f.properties || {};
        if (!Number.isFinite(p.label_lat) || !Number.isFinite(p.label_lon)) return null;
        return {
          lat: p.label_lat,
          lon: p.label_lon,
          lng: p.label_lon,
          kind: "metarea",
          roman: p.roman || p.name,
          name: "METAREA " + (p.roman || p.name) + " — " + (p.coordinator || ""),
          coordinator: p.coordinator,
        };
      })
      .filter(Boolean);
  }

  function subzoneLabelPoints() {
    if (introShowsBanner()) return [];
    if (!subzonesOn || !subzoneFc) return [];
    return (subzoneFc.features || [])
      .map((f) => {
        const p = f.properties || {};
        if (!Number.isFinite(p.label_lat) || !Number.isFinite(p.label_lon)) return null;
        return {
          lat: p.label_lat,
          lon: p.label_lon,
          lng: p.label_lon,
          kind: "subzone",
          roman: p.name,
          name: p.name,
        };
      })
      .filter(Boolean);
  }

  function paintMetareaMap() {
    if (!map || typeof L === "undefined") return;
    if (map.metareaLayer) {
      map.removeLayer(map.metareaLayer);
      map.metareaLayer = null;
    }
    if (typeof map.getPane === "function" && !map.getPane("metarea")) {
      map.createPane("metarea");
      map.getPane("metarea").style.zIndex = 350;
    }
    const grp = L.layerGroup();
    if (metareaOn && metareaFc) {
      L.geoJSON(metareaFc, {
        pane: "metarea",
        style: (feat) => {
          const p = (feat && feat.properties) || {};
          return {
            color: p.stroke || "#1a1a1a",
            weight: 1.15,
            fillColor: p.fill || "#c9a227",
            fillOpacity: 0.38,
            opacity: 0.92,
          };
        },
        onEachFeature: (feat, layer) => {
          const p = (feat && feat.properties) || {};
          const t = "METAREA " + (p.roman || p.name || "") + " — " + (p.coordinator || "");
          layer.bindTooltip(t, { sticky: true, opacity: 0.92 });
          layer.on("click", () => {
            if (placingTx) return;
            showSel(
              '<p class="badge">METAREA</p><h3>' +
                esc(p.roman || p.name || "") +
                "</h3><p class=\"meta\">" +
                esc(p.coordinator || "") +
                "</p>"
            );
          });
        },
      }).addTo(grp);
      (metareaFc.features || []).forEach((f) => {
        const p = f.properties || {};
        if (!Number.isFinite(p.label_lat) || !Number.isFinite(p.label_lon)) return;
        L.marker([p.label_lat, p.label_lon], {
          pane: "metarea",
          interactive: false,
          keyboard: false,
          icon: L.divIcon({
            className: "ggr-leaflet-icon ggr-metarea-lab",
            html: "<span>" + esc(p.roman || p.name || "") + "</span>",
            iconSize: [52, 18],
            iconAnchor: [26, 9],
          }),
        }).addTo(grp);
      });
    }
    if (subzonesOn && subzoneFc) {
      L.geoJSON(subzoneFc, {
        pane: "metarea",
        style: (feat) => {
          const p = (feat && feat.properties) || {};
          return {
            color: "#f4efe4",
            weight: 1.6,
            fillColor: p.fill || "#c45c26",
            fillOpacity: 0.42,
            opacity: 0.95,
          };
        },
        onEachFeature: (feat, layer) => {
          const p = (feat && feat.properties) || {};
          layer.bindTooltip(p.name || "sous-zone", { sticky: true, opacity: 0.92 });
          layer.on("click", () => {
            if (placingTx) return;
            showSel('<p class="badge">sous-zone</p><h3>' + esc(p.name || "") + "</h3>");
          });
        },
      }).addTo(grp);
      (subzoneFc.features || []).forEach((f) => {
        const p = f.properties || {};
        if (!Number.isFinite(p.label_lat) || !Number.isFinite(p.label_lon)) return;
        L.marker([p.label_lat, p.label_lon], {
          pane: "metarea",
          interactive: false,
          keyboard: false,
          icon: L.divIcon({
            className: "ggr-leaflet-icon ggr-metarea-lab",
            html: "<span>" + esc(p.name || "") + "</span>",
            iconSize: [88, 18],
            iconAnchor: [44, 9],
          }),
        }).addTo(grp);
      });
    }
    if (grp.getLayers().length) {
      grp.addTo(map);
      map.metareaLayer = grp;
    }
  }

  function paintPolygons() {
    applyBannerVisibility();
    if (globe && typeof globe.polygonsData === "function") globe.polygonsData(metareaPolygons());
    if (globe) globe.htmlElementsData(points());
    paintMetareaMap();
  }

  function syncLayerBox(el, on) {
    if (!el) return;
    if (el.type === "checkbox") el.checked = !!on;
    else {
      el.classList.toggle("is-on", !!on);
      el.setAttribute("aria-pressed", on ? "true" : "false");
    }
  }

  function setMetarea(on) {
    metareaOn = !!on;
    try {
      localStorage.setItem("ggr-metarea", metareaOn ? "on" : "off");
    } catch {
      /* ignore */
    }
    syncLayerBox(metareaBtn, metareaOn);
    paintPolygons();
  }

  function setSubzones(on) {
    subzonesOn = !!on;
    try {
      localStorage.setItem("ggr-subzones", subzonesOn ? "on" : "off");
    } catch {
      /* ignore */
    }
    syncLayerBox(subzoneBtn, subzonesOn);
    paintPolygons();
  }

  function refreshMap() {
    if (mapMode === "2d") kmLabelNodes = [];
    if (!map || !mapLayers || typeof L === "undefined" || typeof L.marker !== "function") return;
    mapLayers.clearLayers();
    paths().forEach((p) => {
      const latlngs = (p.coords || [])
        .filter((c) => Array.isArray(c) && Number.isFinite(c[0]) && Number.isFinite(c[1]))
        .map((c) => [c[0], c[1]]);
      if (latlngs.length < 2) return;
      L.polyline(latlngs, {
        color: p.color || "#c9a227",
        weight: p.stroke != null ? p.stroke : 1,
        opacity: 0.9,
        dashArray: p.dash ? "11 9" : null,
        className: p.pulse ? "ggr-sdr-pulse" : "",
        interactive: false,
      }).addTo(mapLayers);
    });
    points()
      .filter((d) => d.kind !== "ggr_banner" && d.kind !== "metarea" && d.kind !== "subzone")
      .forEach((d) => {
        if (!Number.isFinite(d.lat)) return;
        const lon = Number.isFinite(d.lon) ? d.lon : d.lng;
        if (!Number.isFinite(lon)) return;
        const boat = d.kind === "boat";
        const node = markerEl(d);
        const m = L.marker([d.lat, lon], {
          icon: L.divIcon({
            className: "ggr-leaflet-icon",
            html: "",
            iconSize: boat ? [22, 22] : d.kind === "kiwi_all" ? [4, 4] : [12, 12],
            iconAnchor: boat ? [11, 11] : d.kind === "kiwi_all" ? [2, 2] : [6, 6],
          }),
          interactive: d.kind !== "link_km" && d.kind !== "tx_km",
          keyboard: false,
        }).addTo(mapLayers);
        const mount = () => {
          const host = m.getElement();
          if (!host) return false;
          host.innerHTML = "";
          host.appendChild(node);
          return true;
        };
        if (!mount()) {
          m.once("add", mount);
        }
      });
    scheduleKmAlign();
  }

  function refreshGlobe() {
    if (mapMode !== "2d") kmLabelNodes = [];
    if (globe) {
      globe.htmlElementsData(points());
      if (typeof globe.pathsData === "function") globe.pathsData(paths());
      if (typeof globe.polygonsData === "function") globe.polygonsData(metareaPolygons());
      if (typeof globe.arcsData === "function") globe.arcsData([]);
    }
    refreshMap();
    scheduleKmAlign();
    window.setTimeout(alignKmLabels, 40);
  }

  function startMuxPulse() {
    if (muxPulseTimer) return;
    muxPulseFat = false;
    muxPulseTimer = window.setInterval(() => {
      muxPulseFat = !muxPulseFat;
      if (globe && typeof globe.pathsData === "function") globe.pathsData(paths());
    }, 500);
  }

  function stopMuxPulse() {
    if (muxPulseTimer) {
      window.clearInterval(muxPulseTimer);
      muxPulseTimer = 0;
    }
    muxPulseFat = false;
  }

  function sizeGlobe() {
    if (!globe || !el) return;
    globe.width(el.clientWidth);
    globe.height(el.clientHeight);
    scheduleKmAlign();
  }

  function pauseGlobe() {
    if (!globe) return;
    try {
      globe.controls().enabled = false;
    } catch {
      /* globe.gl */
    }
  }

  function resumeGlobe() {
    if (!globe) return;
    try {
      if (typeof globe.resumeAnimation === "function") globe.resumeAnimation();
      globe.controls().enabled = true;
    } catch {
      /* globe.gl */
    }
    sizeGlobe();
    window.dispatchEvent(new Event("resize"));
    window.setTimeout(sizeGlobe, 50);
  }

  function initMap(kept) {
    if (!mapEl || map || typeof L === "undefined" || typeof L.map !== "function") return;
    const dest =
      kept && Number.isFinite(kept.lat) && Number.isFinite(kept.lng)
        ? kept
        : fleetView();
    map = L.map(mapEl, {
      zoomControl: true,
      attributionControl: false,
      worldCopyJump: true,
    });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
    }).addTo(map);
    const z = Number.isFinite(dest.zoom) ? dest.zoom : altitudeToZoom(dest.altitude);
    map.setView([dest.lat, dest.lng], z);
    if (typeof map.getPane === "function" && !map.getPane("metarea")) {
      map.createPane("metarea");
      map.getPane("metarea").style.zIndex = 350;
    }
    mapLayers = L.layerGroup().addTo(map);
    bindKmAlign();
    map.on("click", (ev) => {
      if (!placingTx || !ev || !ev.latlng) return;
      placeTxAt(ev.latlng.lat, ev.latlng.lng);
    });
    refreshMap();
    paintMetareaMap();
    window.setTimeout(() => {
      if (!map) return;
      map.invalidateSize();
      refreshMap();
      if (kept) applyKeptView(kept, "2d");
    }, 80);
  }

  function syncModeButtons() {
    document.querySelectorAll(".map-mode [data-map-mode]").forEach((btn) => {
      const on = btn.getAttribute("data-map-mode") === mapMode;
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function applyMapMode(mode, opts) {
    const boot = !!(opts && opts.boot);
    const next = mode === "2d" ? "2d" : "3d";
    if (!boot && next === mapMode) return;
    // Capturer avant de changer mapMode.
    const keep = !boot ? readView(mapMode) : null;
    const first3d = next === "3d" && !globe;
    mapMode = next;
    document.body.classList.toggle("is-map-2d", mapMode === "2d");
    document.body.classList.toggle("is-map-3d", mapMode !== "2d");
    try {
      localStorage.setItem("ggr-view", mapMode);
    } catch {
      /* ignore */
    }
    syncModeButtons();
    if (mapMode === "2d") {
      if (!map) initMap(keep);
      else {
        map.invalidateSize();
        if (keep) applyKeptView(keep, "2d");
        refreshMap();
        window.setTimeout(() => {
          if (!map) return;
          map.invalidateSize();
          if (keep) applyKeptView(keep, "2d");
        }, 60);
      }
      pauseGlobe();
      return;
    }
    if (first3d) {
      initGlobe({ intro: boot && !keep, view: keep });
      requestAnimationFrame(sizeGlobe);
    } else {
      resumeGlobe();
      if (keep) {
        applyKeptView(keep, "3d");
        window.setTimeout(() => applyKeptView(keep, "3d"), 50);
      }
    }
  }

  function initGlobe(opts) {
    const intro = !opts || opts.intro !== false;
    const kept = opts && opts.view;
    const waiting = document.body.classList.contains("is-unavailable");
    if (intro && !waiting && layers.banner) introPlaying = true;
    if (typeof Globe !== "function") {
      if (window.GgrWait) window.GgrWait.show();
      showSel("<p class=\"err\">Globe 3D indisponible (WebGL / script). Les tableaux restent utilisables plus bas.</p>");
      return;
    }
    try {
    globe = Globe()(el)
      .backgroundColor("#02050a")
      .backgroundImageUrl("https://unpkg.com/three-globe/example/img/night-sky.png")
      // Océan uni (#0a3558) dans les tuiles OSM : plus de mosaïque de LOD sur l’eau.
      .globeImageUrl(oceanImageUrl())
      .showAtmosphere(true)
      .atmosphereColor("#5a7a98")
      .atmosphereAltitude(0.08)
      .htmlElementsData(points())
      .htmlLat("lat")
      .htmlLng("lng")
      .htmlAltitude((d) => (d && d.kind === "ggr_banner" ? 0.08 : 0))
      .htmlElement(markerEl)
      .htmlTransitionDuration(0)
      .arcsData([])
      .pathsData(paths())
      .pathPoints("coords")
      .pathPointLat((p) => p[0])
      .pathPointLng((p) => p[1])
      .pathPointAlt((p) => (Array.isArray(p) && p.length > 2 ? p[2] : 0))
      .pathColor((d) => d.color)
      .pathStroke((d) => (d && d.stroke != null ? d.stroke : 1))
      .pathTransitionDuration(0);

    if (typeof globe.globeTileEngineUrl === "function") {
      globe.globeTileEngineUrl((x, y, l) => "/api/globe/osm/" + l + "/" + x + "/" + y + ".png");
    }
    if (typeof globe.globeTileEngineMaxLevel === "function") {
      globe.globeTileEngineMaxLevel(8);
    }
    if (typeof globe.bumpImageUrl === "function") {
      globe.bumpImageUrl(null);
    }
    if (typeof globe.globeMaterial === "function") {
      try {
        const mat = globe.globeMaterial();
        if (mat) {
          mat.bumpMap = null;
          mat.normalMap = null;
          mat.displacementMap = null;
          if ("bumpScale" in mat) mat.bumpScale = 0;
          if (mat.specular && typeof mat.specular.setHex === "function") mat.specular.setHex(0x000000);
          if ("shininess" in mat) mat.shininess = 0;
          if ("needsUpdate" in mat) mat.needsUpdate = true;
        }
      } catch {
        /* matériau globe.gl */
      }
    }

    if (typeof globe.pathResolution === "function") {
      globe.pathResolution(0.35);
    }

    if (typeof globe.polygonsData === "function") {
      globe.polygonsData(metareaPolygons());
      if (typeof globe.polygonGeoJsonGeometry === "function") globe.polygonGeoJsonGeometry((d) => d.geometry);
      if (typeof globe.polygonCapColor === "function")
        globe.polygonCapColor((d) => {
          const p = (d && d.properties) || {};
          const a = p.layer === "subzone" ? 0.48 : 0.4;
          return hexToRgba(p.fill || (p.layer === "subzone" ? "#c45c26" : "#c9a227"), a);
        });
      if (typeof globe.polygonSideColor === "function") globe.polygonSideColor(() => "rgba(8,16,24,0.18)");
      if (typeof globe.polygonStrokeColor === "function")
        globe.polygonStrokeColor((d) => {
          const p = (d && d.properties) || {};
          return p.layer === "subzone" ? "#f4efe4" : p.stroke || "#1a1a1a";
        });
      if (typeof globe.polygonAltitude === "function")
        globe.polygonAltitude((d) => ((d && d.properties && d.properties.layer) === "subzone" ? 0.006 : 0.003));
      if (typeof globe.polygonCapCurvatureResolution === "function") globe.polygonCapCurvatureResolution(4);
      if (typeof globe.onPolygonClick === "function") {
        globe.onPolygonClick((poly) => {
          if (placingTx) return;
          const p = poly && poly.properties;
          if (!p) return;
          if (p.layer === "subzone") {
            showSel('<p class="badge">sous-zone</p><h3>' + esc(p.name || "") + "</h3>");
            return;
          }
          showSel(
            '<p class="badge">METAREA</p><h3>' +
              esc(p.roman || p.name || "") +
              "</h3><p class=\"meta\">" +
              esc(p.coordinator || "") +
              "</p>"
          );
        });
      }
    }

    if (typeof globe.htmlOcclude === "function") {
      try {
        globe.htmlOcclude(true);
      } catch {
        /* globe.gl < occlude API */
      }
    }

    const dest = fleetView();
    if (waiting) {
      globe.pointOfView({ lat: 8, lng: -20, altitude: 2.35 }, 0);
    } else if (kept && Number.isFinite(kept.lat) && Number.isFinite(kept.lng)) {
      introPlaying = false;
      const alt = Number.isFinite(kept.altitude) ? kept.altitude : zoomToAltitude(kept.zoom);
      globe.pointOfView({ lat: kept.lat, lng: kept.lng, altitude: alt }, 0);
    } else if (intro) {
      introPlaying = true;
      globe.pointOfView({ lat: 8, lng: dest.lng + BANNER_PAN_SPAN, altitude: BANNER_PAN_ALT }, 0);
      scheduleIntroCamera(globe, dest);
      if (typeof globe.polygonsData === "function") globe.polygonsData(metareaPolygons());
      applyBannerVisibility();
    } else {
      globe.pointOfView(dest, 0);
    }
    globe.controls().autoRotate = waiting;
    if (waiting) {
      globe.controls().autoRotateSpeed = 0.35;
      globe.controls().enableZoom = false;
      globe.controls().enabled = false;
    }
    globe.controls().enableDamping = true;
    bindKmAlign();
    el.addEventListener("wheel", scheduleKmAlign, { passive: true });
    tuneOsmTiles(globe);
    if (intro && !kept && layers.banner && !waiting) {
      const bootBanner = () => startGgrEquatorBanner(globe);
      if (typeof globe.onGlobeReady === "function") globe.onGlobeReady(bootBanner);
      const afterFont = () => bootBanner();
      if (document.fonts && document.fonts.load) {
        document.fonts.load("800 200px Montserrat").then(afterFont, afterFont);
      } else {
        afterFont();
      }
    }

    sizeGlobe();
    window.addEventListener("resize", sizeGlobe);
    el.addEventListener("pointerdown", () => {
      globe.controls().autoRotate = false;
      if (introPlaying || bannerReadable) cancelIntro(globe);
      else introCamId += 1;
    });

    const clickLatLng = (a, b) => {
      if (a && Number.isFinite(a.lat) && Number.isFinite(a.lng)) return { lat: a.lat, lng: a.lng };
      if (Number.isFinite(a) && Number.isFinite(b)) return { lat: a, lng: b };
      return null;
    };
    if (typeof globe.onGlobeClick === "function") {
      globe.onGlobeClick((a, b) => {
        if (!placingTx) return;
        const pos = clickLatLng(a, b);
        if (!pos) return;
        placeTxAt(pos.lat, pos.lng);
      });
    }
    } catch (err) {
      if (window.GgrWait) window.GgrWait.show();
      showSel("<p class=\"err\">Globe 3D : " + esc(err && err.message ? err.message : err) + "</p>");
      return;
    }
    if (document.body.classList.contains("is-unavailable")) {
      window.dispatchEvent(new CustomEvent("ggr-wait", { detail: { on: true } }));
    } else if (window.GgrWait) {
      window.GgrWait.ok();
    }
  }

  window.addEventListener("ggr-wait", (ev) => {
    if (!globe) return;
    const on = !!(ev.detail && ev.detail.on);
    try {
      const ctrl = globe.controls();
      ctrl.autoRotate = on;
      ctrl.autoRotateSpeed = 0.35;
      ctrl.enableZoom = !on;
      ctrl.enabled = !on;
      if (typeof globe.pointOfView === "function") {
        if (on) globe.pointOfView({ lat: 8, lng: -20, altitude: 2.35 }, 700);
        else {
          const dest = fleetView();
          if (dest) globe.pointOfView(dest, 700);
        }
      }
    } catch {
      /* globe.gl */
    }
  });

  const saveBtn = document.getElementById("globe-save-buddy");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const tok = token();
      if (!tok) {
        sayBuddy(t("need_token"), false);
        return;
      }
      saveBtn.disabled = true;
      sayBuddy(t("saving"), true);
      try {
        const cur = await fetch("/api/settings").then((r) => r.json());
        const res = await fetch("/api/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json", "X-Admin-Token": tok },
          body: JSON.stringify({
            tx_khz: cur.tx_khz,
            ack1_khz: cur.ack1_khz,
            ack2_khz: cur.ack2_khz,
            qrg_tolerance_khz: cur.qrg_tolerance_khz,
            lead_minutes: cur.schedule_lead,
            duration_minutes: cur.duration_minutes,
            buddy_enabled: cur.buddy_enabled,
            buddy_main_khz: cur.buddy_main_khz,
            buddy_alt_khz: cur.buddy_alt_khz,
            buddy_extra1_khz: cur.buddy_extra1_khz,
            buddy_extra2_khz: cur.buddy_extra2_khz,
            buddy_time_utc: cur.buddy_time_utc,
            buddy_lead: cur.buddy_lead,
            buddy_duration_minutes: cur.buddy_duration_minutes,
            buddy_kiwi_count: 4,
            buddy_skippers: [...skippers],
          }),
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          sayBuddy(body.detail || t("err_http", { status: res.status }), false);
          return;
        }
        sayBuddy(t("centroid_saved"), true);
        window.location.reload();
      } catch (err) {
        sayBuddy(String(err), false);
      } finally {
        saveBtn.disabled = false;
      }
    });
  }

  if (txListEl) {
    txListEl.addEventListener("change", (ev) => {
      const inp = ev.target.closest("input[data-tx-label]");
      if (!inp) return;
      const i = Number(inp.getAttribute("data-tx-label"));
      if (!Number.isInteger(i) || !txSites[i]) return;
      txSites[i] = { ...txSites[i], label: String(inp.value || "").trim() || txSites[i].label };
      refreshGlobe();
    });
    txListEl.addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-tx-del]");
      if (!btn) return;
      const i = Number(btn.getAttribute("data-tx-del"));
      if (!Number.isInteger(i)) return;
      txSites = listedTxSites().filter((_, idx) => idx !== i);
      renderTxList();
      refreshGlobe();
    });
  }

  if (txPlaceBtn) {
    txPlaceBtn.addEventListener("click", () => {
      if (placingTx) {
        setPlacingTx(false);
        sayTx(t("tx_cancel"), true);
        return;
      }
      if (listedTxSites().length >= TX_MAX) {
        sayTx(t("tx_max"), false);
        return;
      }
      setPlacingTx(true);
      sayTx(t("tx_click"), true);
    });
  }

  if (txSaveBtn) {
    txSaveBtn.addEventListener("click", async () => {
      const tok = token();
      if (!tok) {
        sayTx(t("need_token"), false);
        return;
      }
      txSaveBtn.disabled = true;
      sayTx(t("saving"), true);
      try {
        const cur = await fetch("/api/settings").then((r) => r.json());
        const res = await fetch("/api/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json", "X-Admin-Token": tok },
          body: JSON.stringify({
            tx_khz: cur.tx_khz,
            ack1_khz: cur.ack1_khz,
            ack2_khz: cur.ack2_khz,
            qrg_tolerance_khz: cur.qrg_tolerance_khz,
            lead_minutes: cur.schedule_lead,
            duration_minutes: cur.duration_minutes,
            tx_sites: listedTxSites().map((s) => {
              const row = { label: s.label, lat: s.lat, lon: s.lon };
              if (s.loc) row.loc = s.loc;
              return row;
            }),
          }),
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          sayTx(body.detail || t("err_http", { status: res.status }), false);
          return;
        }
        sayTx(t("tx_saved"), true);
      } catch (err) {
        sayTx(String(err), false);
      } finally {
        txSaveBtn.disabled = false;
      }
    });
  }

  renderSkippers();
  renderVacList();
  renderTxList();

  document.querySelectorAll(".map-mode [data-map-mode]").forEach((btn) => {
    btn.addEventListener("click", () => applyMapMode(btn.getAttribute("data-map-mode")));
  });
  function lectureClass(text, inCoast) {
    const raw = String(text || "").trim();
    if (/^bulletin haute mer/i.test(raw) || /^high seas (forecast|bulletin)/i.test(raw))
      return { cls: "lecture--head", coast: false };
    if (
      /^avis de coup de vent/i.test(raw) ||
      /^avis\./i.test(raw) ||
      /^gale warning/i.test(raw) ||
      /^warning\./i.test(raw)
    )
      return { cls: "lecture--warn", coast: inCoast };
    if (/hors des sous-zones/i.test(raw) || /outside the fleet sub-zones/i.test(raw))
      return { cls: "lecture--warn-out", coast: false };
    if (/^situation générale/i.test(raw) || /^general situation/i.test(raw))
      return { cls: "lecture--syn", coast: false };
    if (/^tendance\b/i.test(raw) || /^outlook\b/i.test(raw)) return { cls: "lecture--out", coast: false };
    if (/^bulletin côtier/i.test(raw) || /^coastal forecast/i.test(raw))
      return { cls: "lecture--coast", coast: true };
    if (/^pas de prévision/i.test(raw) || /^no zone forecast/i.test(raw))
      return { cls: "lecture--empty", coast: false };
    if (inCoast) return { cls: "lecture--coast-zone", coast: true };
    return { cls: "lecture--zone", coast: false };
  }

  function lectureParagraphs(rows) {
    let inCoast = false;
    return (rows || [])
      .map((p) => {
        const hit = lectureClass(p, inCoast);
        inCoast = hit.coast;
        return "<p class=\"" + hit.cls + "\">" + esc(p) + "</p>";
      })
      .join("");
  }

  let metareaSpeechText = "";

  function renderMetareaTab(snap) {
    const body = document.getElementById("ggr-metarea-body") || document.getElementById("ggr-metarea-page");
    const host = document.getElementById("ggr-metarea-page");
    if (!body) return;
    if (!snap || !snap.ok) {
      metareaSpeechText = "";
      body.innerHTML =
        "<p class=\"err\">" +
        esc((snap && (snap.error || snap.lecture)) || t("metarea_err")) +
        "</p>";
      return;
    }
    const roman = snap.metarea || "—";
    const links = snap.links || {};
    const lis = [];
    if (links.metarea) {
      lis.push(
        '<li><a href="' +
          esc(links.metarea) +
          '" rel="noreferrer">' +
          esc(t("metarea_link_wwmiws", { roman: roman })) +
          "</a></li>"
      );
    }
    if (links.forecast) {
      lis.push(
        '<li><a href="' +
          esc(links.forecast) +
          '" rel="noreferrer">' +
          esc(t("metarea_link_forecast")) +
          "</a></li>"
      );
    }
    if (links.warning) {
      lis.push(
        '<li><a href="' +
          esc(links.warning) +
          '" rel="noreferrer">' +
          esc(t("metarea_link_warning")) +
          "</a></li>"
      );
    }
    if (links.json) {
      lis.push(
        '<li><a href="' +
          esc(links.json) +
          '" rel="noreferrer">' +
          esc(t("metarea_link_json")) +
          "</a></li>"
      );
    }
    const coastalLis = (snap.coastal || []).map((c) => {
      const label = (c.title || c.gts || t("coastal_short")).replace(/\s+$/, "");
      if (c.url) {
        return (
          '<li><a href="' +
          esc(c.url) +
          '" rel="noreferrer">' +
          esc(t("metarea_coastal", { label: label })) +
          "</a></li>"
        );
      }
      return "<li>" + esc(t("metarea_coastal", { label: label })) + "</li>";
    });
    const zones = (snap.fleet_zones || []).join(", ") || "—";
    const extra = (snap.extra_zones || []).join(", ");
    const sched = (snap.schedule_utc || []).join(" et ");
    const paras = lectureParagraphs(snap.paragraphs || []);
    const zoneMeta = snap.has_subzones
      ? "<p class=\"meta\">" +
        esc(t("metarea_subzones")) +
        " : <strong>" +
        esc(zones) +
        "</strong>" +
        (extra ? " · " + esc(t("metarea_coastal_near")) + " : <strong>" + esc(extra) + "</strong>" : "") +
        "</p>"
      : "<p class=\"meta\">" + esc(t("metarea_no_subzones")) + "</p>";
    const disclaimer =
      "<p class=\"ggr-metarea-om\" role=\"note\">" +
      esc(snap.disclaimer || "") +
      "</p>";
    const title = host ? host.querySelector("h1") : null;
    if (title) title.textContent = t("metarea_h1", { roman: roman });
    body.innerHTML =
      disclaimer +
      "<p class=\"meta\">" +
      esc(t("metarea_official")) +
      " : <strong>" +
      esc(snap.official_label || "—") +
      "</strong>" +
      (sched ? " " + esc(t("metarea_safetynet", { sched: sched })) : "") +
      ".</p>" +
      "<p class=\"meta\">" +
      esc(t("metarea_retrieved")) +
      " : " +
      esc(snap.retrieved_label || snap.wwmiws_date || "—") +
      (snap.gts ? " · GTS " + esc(snap.gts) : "") +
      "</p>" +
      zoneMeta +
      "<h2>" +
      esc(t("metarea_links_h2")) +
      "</h2>" +
      "<ul class=\"ggr-metarea-links\">" +
      lis.join("") +
      coastalLis.join("") +
      "</ul>" +
      "<h2>" +
      esc(t("metarea_lecture_h2")) +
      "</h2>" +
      "<div class=\"ggr-metarea-lecture\" lang=\"" +
      (document.documentElement.lang || "fr") +
      "\">" +
      paras +
      "</div>";
    metareaSpeechText = (snap.paragraphs || []).join(". ");
    bindBulletinSpeech();
  }

  function bindBulletinSpeech() {
    const host = document.getElementById("ggr-metarea-page");
    if (!host || host.dataset.speechBound === "1") return;
    host.dataset.speechBound = "1";
    const play = host.querySelector("#ggr-speech-play");
    const stop = host.querySelector("#ggr-speech-stop");
    const mp3 = host.querySelector("#ggr-speech-mp3");
    const sel = host.querySelector("#ggr-speech-voice");
    const audio = host.querySelector("#ggr-speech-audio");
    const dl = host.querySelector("#ggr-speech-dl");
    if (play) {
      play.addEventListener("click", () => {
        if (!window.speechSynthesis) return;
        window.speechSynthesis.cancel();
        if (audio) audio.pause();
        const u = new SpeechSynthesisUtterance(metareaSpeechText);
        u.lang = document.documentElement.lang === "en" ? "en-GB" : "fr-FR";
        const want = sel && sel.value ? String(sel.value) : "";
        const voices = window.speechSynthesis.getVoices() || [];
        const hit =
          voices.find((v) => want && (v.name || "").includes(want.split("-").pop() || "")) ||
          voices.find((v) => (v.lang || "").toLowerCase().startsWith(u.lang.slice(0, 2)));
        if (hit) u.voice = hit;
        window.speechSynthesis.speak(u);
      });
    }
    if (stop) {
      stop.addEventListener("click", () => {
        if (window.speechSynthesis) window.speechSynthesis.cancel();
        if (audio) {
          audio.pause();
          audio.currentTime = 0;
        }
      });
    }
    if (mp3) {
      mp3.addEventListener("click", () => {
        if (!metareaSpeechText) return;
        mp3.disabled = true;
        fetch("/api/tts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: metareaSpeechText, voice: sel ? sel.value : "" }),
        })
          .then((r) => {
            if (!r.ok) throw new Error(String(r.status));
            return r.blob();
          })
          .then((blob) => {
            const url = URL.createObjectURL(blob);
            if (dl) {
              if (dl.dataset.url) URL.revokeObjectURL(dl.dataset.url);
              dl.href = url;
              dl.dataset.url = url;
              dl.hidden = false;
            }
            if (audio) {
              audio.src = url;
              audio.play().catch(() => {});
            }
          })
          .catch(() => {
            mp3.disabled = false;
          })
          .finally(() => {
            mp3.disabled = false;
          });
      });
    }
  }

  function loadMetareaSnapshot() {
    fetch("/api/metarea")
      .then((r) => r.json())
      .then((snap) => {
        metareaSnap = snap;
        if (snap && snap.geojson && snap.geojson.type === "FeatureCollection") {
          subzoneFc = snap.geojson;
        }
        renderMetareaTab(snap);
        setSubzones(subzonesOn);
      })
      .catch(() => {
        renderMetareaTab({ ok: false, error: t("metarea_err") });
      });
  }

  const mapOpt = document.getElementById("map-opt");
  const mapOptToggle = document.getElementById("map-opt-toggle");
  function setMapOptOpen(on) {
    document.body.classList.toggle("map-opt-on", !!on);
    if (mapOpt) mapOpt.hidden = !on;
    if (mapOptToggle) {
      mapOptToggle.setAttribute("aria-expanded", on ? "true" : "false");
      mapOptToggle.setAttribute("aria-label", on ? t("layers_collapse") : t("layers_open"));
      mapOptToggle.setAttribute("title", on ? t("menu_collapse_short") : t("layers"));
    }
  }
  if (mapOptToggle) {
    mapOptToggle.addEventListener("click", () => {
      setMapOptOpen(!document.body.classList.contains("map-opt-on"));
    });
  }

  function loadKiwisAll() {
    fetch("/api/kiwis")
      .then((r) => r.json())
      .then((j) => {
        kiwisAll = (j && j.kiwis) || [];
        if (layers.sdrPotential) refreshGlobe();
      })
      .catch(() => {
        kiwisAll = [];
      });
  }

  function setLayer(key, on) {
    if (key === "metarea") {
      setMetarea(on);
      return;
    }
    if (key === "subzones") {
      setSubzones(on);
      return;
    }
    layers[key] = !!on;
    try {
      localStorage.setItem("ggr-layer-" + key, on ? "on" : "off");
    } catch {
      /* ignore */
    }
    if (key === "sdrPotential" && on && !kiwisAll.length) loadKiwisAll();
    if (key === "banner") {
      if (on && globe && mapMode === "3d") startGgrEquatorBanner(globe);
      applyBannerVisibility();
    }
    refreshGlobe();
  }

  function syncLabelButtons() {
    document.querySelectorAll("#map-opt [data-labels]").forEach((btn) => {
      const kind = btn.getAttribute("data-labels");
      const on = kind === "sdr" ? layers.labelsSdr : kind === "boats" ? layers.labelsBoats : false;
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.classList.toggle("is-on", on);
    });
  }

  function setLabels(kind, on) {
    if (kind === "sdr") {
      layers.labelsSdr = !!on;
      try {
        localStorage.setItem("ggr-layer-labels-sdr", on ? "on" : "off");
      } catch {
        /* ignore */
      }
    } else if (kind === "boats") {
      layers.labelsBoats = !!on;
      try {
        localStorage.setItem("ggr-layer-labels-boats", on ? "on" : "off");
      } catch {
        /* ignore */
      }
    } else {
      return;
    }
    syncLabelButtons();
    refreshGlobe();
  }

  function applyDisplay(d) {
    if (!d || typeof d !== "object") return;
    layers.banner = !!d.banner;
    layers.sdrActive = !!d.sdr_fleet;
    layers.sdrPotential = !!d.sdr_potential;
    layers.boats = !!d.boats;
    layers.labelsSdr = d.labels_sdr != null ? !!d.labels_sdr : layers.labelsSdr;
    layers.labelsBoats = d.labels_boats != null ? !!d.labels_boats : d.skippers != null ? !!d.skippers : layers.labelsBoats;
    try {
      localStorage.setItem("ggr-layer-banner", layers.banner ? "on" : "off");
      localStorage.setItem("ggr-layer-sdrActive", layers.sdrActive ? "on" : "off");
      localStorage.setItem("ggr-layer-sdrPotential", layers.sdrPotential ? "on" : "off");
      localStorage.setItem("ggr-layer-boats", layers.boats ? "on" : "off");
      localStorage.setItem("ggr-layer-labels-sdr", layers.labelsSdr ? "on" : "off");
      localStorage.setItem("ggr-layer-labels-boats", layers.labelsBoats ? "on" : "off");
    } catch {
      /* ignore */
    }
    setMetarea(!!d.metarea);
    setSubzones(!!d.subzones);
    if (layers.banner && globe && mapMode === "3d") startGgrEquatorBanner(globe);
    applyBannerVisibility();
    document.querySelectorAll("#map-opt [data-layer]").forEach((inp) => {
      const key = inp.getAttribute("data-layer");
      if (key === "metarea") inp.checked = metareaOn;
      else if (key === "subzones") inp.checked = subzonesOn;
      else if (Object.prototype.hasOwnProperty.call(layers, key)) inp.checked = !!layers[key];
    });
    syncLabelButtons();
    if (layers.sdrPotential && !kiwisAll.length) loadKiwisAll();
    refreshGlobe();
  }
  window.addEventListener("ggr-display", (ev) => applyDisplay(ev.detail));

  document.querySelectorAll("#map-opt [data-layer]").forEach((inp) => {
    const key = inp.getAttribute("data-layer");
    if (key === "metarea") inp.checked = metareaOn;
    else if (key === "subzones") inp.checked = subzonesOn;
    else if (Object.prototype.hasOwnProperty.call(layers, key)) inp.checked = !!layers[key];
    inp.addEventListener("change", () => setLayer(key, inp.checked));
  });
  document.querySelectorAll("#map-opt [data-labels]").forEach((btn) => {
    const kind = btn.getAttribute("data-labels");
    btn.addEventListener("click", () => {
      const cur = kind === "sdr" ? layers.labelsSdr : layers.labelsBoats;
      setLabels(kind, !cur);
    });
  });
  syncLabelButtons();
  loadKiwisAll();
  fetch("/static/geo/metareas.json")
    .then((r) => (r.ok ? r.json() : null))
    .then((fc) => {
      if (!fc || fc.type !== "FeatureCollection") return;
      metareaFc = fc;
      setMetarea(metareaOn);
    })
    .catch(() => {});
  loadMetareaSnapshot();
  bindBulletinSpeech();
  window.setInterval(loadMetareaSnapshot, 10 * 60 * 1000);
  applyMapMode(mapMode, { boot: true });

  const traficQ = new URLSearchParams(location.search).get("trafic") || new URLSearchParams(location.search).get("vac");
  if (traficQ) {
    const found = trafics.find((v) => v.id === traficQ) || (data.trafics || data.vacations || []).find((v) => v.id === traficQ);
    if (found) playTrafic(found);
  }
})();
