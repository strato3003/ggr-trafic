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
  let metareaOn = true;
  try {
    metareaOn = localStorage.getItem("ggr-metarea") !== "off";
  } catch {
    metareaOn = true;
  }
  const metareaBtn = document.getElementById("ggr-metarea");

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

  function setPanelOpen(on) {
    document.body.classList.toggle("kiwi-panel-off", !on);
    if (kiwiToggle) {
      kiwiToggle.setAttribute("aria-expanded", on ? "true" : "false");
      kiwiToggle.setAttribute("aria-label", on ? "Replier le panneau" : "Déplier le panneau");
      kiwiToggle.setAttribute("title", on ? "Replier" : "Déplier");
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

  if (kiwiToggle) {
    kiwiToggle.addEventListener("click", () => {
      setPanelOpen(document.body.classList.contains("kiwi-panel-off"));
    });
  }
  document.body.classList.add("is-map-3d");
  document.body.classList.remove("is-map-2d");

  function parseHash() {
    const h = (location.hash || "#trafic").replace(/^#/, "");
    if (h === "setup") return "setup";
    if (h === "apropos" || h === "a-propos") return "apropos";
    return "trafic";
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
    document.body.classList.remove("kiwi-mix-on");
  }

  function showTab(name) {
    const tab = name || "trafic";
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
  }

  let introAboutArmed = true;
  const traficQBoot = new URLSearchParams(location.search).get("trafic") || new URLSearchParams(location.search).get("vac");
  if (traficQBoot) introAboutArmed = false;
  const bootTab = parseHash();
  if (introAboutArmed && bootTab !== "setup") {
    if (bootTab === "apropos") history.replaceState(null, "", "#trafic");
    setPanelOpen(false);
  } else {
    introAboutArmed = false;
    setPanelOpen(true);
  }

  document.querySelectorAll(".kiwi-tabs [data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      introAboutArmed = false;
      showTab(btn.getAttribute("data-tab"));
      setPanelOpen(true);
    });
  });
  window.addEventListener("hashchange", () => {
    showTab(parseHash());
  });
  showTab(parseHash());

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

  function labelAlongDeg(brg) {
    // Texte parallèle à la ligne (bearing depuis le nord) ; rester à l'endroit.
    let a = brg - 90;
    const n = ((a % 360) + 360) % 360;
    if (n > 90 && n < 270) a += 180;
    return a;
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
      rows.push({
        ...b,
        lng: b.lon,
        kind: "boat",
        inBuddy: boatInBuddy(b.name),
      });
    });
    const seenSdr = new Set();
    const sdrKey = (k) => String(k.id || k.host || k.lat.toFixed(3) + "," + k.lon.toFixed(3));
    (data.buddy_kiwis || []).forEach((k) => {
      if (!Number.isFinite(k.lat) || !Number.isFinite(k.lon)) return;
      seenSdr.add(sdrKey(k));
      rows.push({ ...k, lng: k.lon, kind: "buddy_kiwi" });
    });
    (data.kiwis || []).forEach((k) => {
      if (!Number.isFinite(k.lat) || !Number.isFinite(k.lon)) return;
      if (seenSdr.has(sdrKey(k))) return;
      seenSdr.add(sdrKey(k));
      rows.push({ ...k, lng: k.lon, kind: "kiwi" });
    });
    const c = fleetCenter();
    if (c) {
      rows.push({ lat: c.lat, lon: c.lon, lng: c.lon, kind: "centroid", name: "Centroïde" });
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
      rows.push({ ...s, lng: s.lon, kind: "tx", _idx: i, name: s.label || "Émission" });
    });
    kiwiKmPoints().forEach((p) => rows.push(p));
    txKmPoints().forEach((p) => rows.push(p));
    htmlBannerPoints().forEach((p) => rows.push(p));
    metareaLabelPoints().forEach((p) => rows.push(p));
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
      `<header>${col}<div><p class="globe-skipper-tip__kicker">Skipper</p><h3>${esc(d.name || "—")}</h3></div></header>` +
      `<div class="globe-skipper-tip__grid">` +
      row("Voile", d.sail) +
      row("Rang", d.rank != null ? String(d.rank) : "") +
      row("Statut", d.status) +
      row("Pavillon", [d.flag, d.country].filter(Boolean).join(" · ")) +
      row("Bateau", d.model) +
      row("Propriétaire", d.owner) +
      row("Position", fmtLatLon(d.lat, d.lon)) +
      row("Cap", deg(d.heading)) +
      row("SOG", kn(d.sog_kn)) +
      row("VMG", kn(d.vmg_kn)) +
      row("DTF", nm(d.dtf_nm)) +
      row("24 h", nm(d.d24_nm)) +
      row("DMG", nm(d.dmg_nm)) +
      row("GPS", d.gps_at) +
      row("Arrivée est.", d.finish_at) +
      row("Vent", vent) +
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
    wrap.className = "globe-mark globe-mark--" + d.kind + (d.kind === "boat" && d.inBuddy ? " is-buddy" : "");
    wrap.style.cssText =
      d.kind === "boat"
        ? "width:22px;height:22px;margin:0;padding:0;overflow:visible;pointer-events:auto;"
        : d.kind === "metarea"
          ? "width:auto;height:auto;margin:0;padding:0;overflow:visible;pointer-events:none;"
          : "width:12px;height:12px;margin:0;padding:0;overflow:visible;";
    if (d.kind === "metarea") {
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
      const rot = Number.isFinite(d.alongDeg) ? d.alongDeg : 0;
      km.style.transform = "translate(-50%,-50%) rotate(" + rot + "deg)";
      wrap.appendChild(km);
      wrap.title = d.name || "";
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
    }
    wrap.appendChild(icon);
    if ((d.kind === "boat" && d.inBuddy) || d.kind === "kiwi" || d.kind === "buddy_kiwi" || d.kind === "tx") {
      const name = document.createElement("span");
      name.className = "globe-mark__name";
      name.style.cssText = "position:absolute;left:14px;top:50%;transform:translateY(-50%);white-space:nowrap;";
      name.textContent = d.kind === "kiwi" || d.kind === "buddy_kiwi" ? sdrLabel(d) : d.name || "";
      wrap.appendChild(name);
    }
    if (d.kind !== "boat") {
      wrap.title = d.kind === "kiwi" || d.kind === "buddy_kiwi" ? sdrLabel(d) : d.name || d.label || d.kind;
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
    (data.buddy_kiwis || []).forEach((k) => add(k, "buddy_kiwi"));
    (data.kiwis || []).forEach((k) => add(k, "kiwi"));
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

  function geodesicDashed(lat1, lon1, lat2, lon2, color) {
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
        color,
        stroke: 2,
      });
      along = dashEnd + SDR_GAP_KM;
    }
    return out;
  }

  function kiwiLinkPaths() {
    const center = fleetCenter();
    if (!center) return [];
    const rows = [];
    sdrSites().forEach((k) => {
      const nvis = k.prop_zone === "nvis" || k._kind === "kiwi";
      // Hex : Line2 three-globe parse mal les rgba() (traits invisibles).
      const color = nvis ? "#3dba7a" : "#6ec9e0";
      geodesicDashed(center.lat, center.lon, k.lat, k.lon, color).forEach((p) => rows.push(p));
    });
    return rows;
  }

  function kiwiKmPoints() {
    const center = fleetCenter();
    if (!center) return [];
    return sdrSites().map((k) => {
      const km = haversineKm(center.lat, center.lon, k.lat, k.lon);
      const brg = bearingDeg(center.lat, center.lon, k.lat, k.lon);
      const mid = destPoint(center.lat, center.lon, brg, km / 2);
      return {
        lat: mid[0],
        lon: mid[1],
        lng: mid[1],
        kind: "link_km",
        name: Math.round(km) + " km",
        alongDeg: labelAlongDeg(brg),
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
        const mid = destPoint(s.lat, s.lon, aim.az, aim.km / 2);
        return {
          lat: mid[0],
          lon: mid[1],
          lng: mid[1],
          kind: "tx_km",
          name: Math.round(aim.km) + " km · " + fmtAz(aim.az),
          alongDeg: labelAlongDeg(aim.az),
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
          ? "Cliquer la carte… (annuler)"
          : "Cliquer le globe… (annuler)"
        : "Poser un QTH sur le globe";
    }
  }

  function renderTxList() {
    if (!txListEl) return;
    const rows = listedTxSites();
    if (!rows.length) {
      txListEl.innerHTML = "<li class=\"meta\">Aucun QTH. Poser un point sur le globe (max " + TX_MAX + ").</li>";
      return;
    }
    txListEl.innerHTML = rows
      .map((s, i) => {
        const aim = txAim(s);
        const stats = aim
          ? Math.round(aim.km) + " km · az. " + fmtAz(aim.az)
          : "centroïde indisponible";
        return (
          "<li>" +
          "<input type=\"text\" maxlength=\"64\" data-tx-label=\"" +
          i +
          "\" value=\"" +
          esc(s.label || "") +
          "\" aria-label=\"Nom du QTH\">" +
          "<span class=\"tx-list__aim\">" +
          esc(stats) +
          "</span>" +
          "<button type=\"button\" class=\"btn\" data-tx-del=\"" +
          i +
          "\">Retirer</button>" +
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
        const when = (v.started_at || "").replace("T", " ").slice(0, 16);
        const tag = v.is_buddy ? "Buddy call" : v.is_test ? "Test" : "Bulletin météo";
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
    fillVacList(vacsEl, all, "Aucun trafic enregistré.");
  }

  function mixerTracks(v) {
    return (v.channels || []).map((c, i) => ({
      id: c.id || String(i),
      src: c.audio || "",
      freq_khz: c.freq_khz,
      place: c.place || c.loc,
      site_label: c.site_label,
      label: c.label,
      has_audio: !!(c.has_audio || c.audio),
      waterfall: c.waterfall || "",
    }));
  }

  async function playTrafic(v) {
    introAboutArmed = false;
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
    let tracks = mixerTracks(v);
    if (!tracks.length) {
      try {
        const full = await fetch("/api/trafic/" + encodeURIComponent(v.id)).then((r) => r.json());
        if (openVid !== v.id) return;
        if (Array.isArray(full.mixer_tracks) && full.mixer_tracks.length) tracks = full.mixer_tracks;
        else if ((full.channels || []).length) tracks = mixerTracks(full);
      } catch {
        /* carte globe */
      }
    }
    if (openVid !== v.id) return;
    mixRoot.hidden = false;
    document.body.classList.add("kiwi-mix-on");
    if (window.GgrMixer) {
      window.GgrMixer.mount({
        root: mixRoot,
        tracks: tracks,
        vacId: v.id,
        started: v.started_at,
      });
    }
    li.scrollIntoView({ block: "nearest" });
    if (Number.isFinite(v.lat)) lookAt(v.lat, v.lon, 1.6, 900);
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
        `<p class="badge">Skipper</p><h3>${esc(d.name)}</h3>` +
          (d.owner || d.model || d.sail
            ? `<p class="meta">${esc([d.owner, d.model, d.sail ? "voile " + d.sail : ""].filter(Boolean).join(" · "))}</p>`
            : "") +
          (d.country ? `<p class="meta">${esc(d.country)}</p>` : "") +
          yb("GPS", d.gps_at) +
          yb("Vitesse", spd) +
          yb("Vent", vent) +
          yb("DTF", nm(d.dtf_nm)) +
          yb("24 h", nm(d.d24_nm)) +
          yb("DMG", nm(d.dmg_nm)) +
          yb("VMG", kn(d.vmg_kn)) +
          yb("Rang", d.rank != null ? String(d.rank) : null) +
          yb("Arrivée est.", d.finish_at) +
          (d.status && d.status !== "RACING" ? yb("Statut", d.status) : "") +
          `<p class="settings__actions"><button type="button" class="btn" id="globe-toggle-skip">` +
          `${on ? "Retirer du centroïde" : "Ajouter au centroïde"}</button></p>`
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
    if (d.kind === "kiwi" || d.kind === "buddy_kiwi") {
      const zone = d.prop_zone ? ` · zone ${esc(d.prop_zone)}` : "";
      const km = d.site_km != null ? d.site_km : d.distance_km;
      showSel(
        `<p class="badge">${d.kind === "buddy_kiwi" ? "Kiwi buddy call" : "Kiwi bulletin météo"}</p>` +
          `<h3>${esc(d.name)}</h3>` +
          `<p class="meta">${esc(d.loc || "")}${d.site_label ? " · " + esc(d.site_label) : ""}</p>` +
          `<p>${km != null ? km + " km" : ""} · SNR HF ${esc(d.snr_hf)} · ${esc(d.free_slots)}/${esc(d.users_max)} places${zone}</p>` +
          (d.url ? `<p><a href="${esc(d.url)}" rel="noreferrer">Ouvrir le KiwiSDR</a></p>` : "")
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
        `<p class="badge">Émission bulletin</p><h3>${esc(d.name || d.label || "QTH")}</h3>` +
          `<p class="meta">${esc(pos)}</p>` +
          (aim
            ? `<p>Distance centroïde : <strong>${Math.round(aim.km)} km</strong></p>` +
              `<p>Azimut antenne (vrai nord) : <strong>${esc(fmtAz(aim.az))}</strong></p>`
            : "<p class=\"hint\">Centroïde indisponible.</p>")
      );
      lookAt(d.lat, d.lon, 1.6, 800);
      return;
    }
    showSel(`<h3>${esc(d.name || d.label || d.kind)}</h3><p class="meta">${esc(d.fmt || "")}</p>`);
    lookAt(d.lat, d.lng || d.lon, 1.8, 800);
  }

  let globe = null;
  let map = null;
  let mapLayers = null;

  function altitudeToZoom(alt) {
    const a = Number(alt);
    if (!Number.isFinite(a)) return 5;
    return Math.max(3, Math.min(11, Math.round(9.2 - a * 2.4)));
  }

  function lookAt(lat, lng, altitude, ms) {
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
    if (mapMode === "2d" && map) {
      map.flyTo([lat, lng], altitudeToZoom(altitude), { duration: Math.max(0.2, (ms || 800) / 1000) });
      return;
    }
    if (globe) globe.pointOfView({ lat, lng, altitude: altitude || 1.5 }, ms || 800);
  }

  function placeTxAt(lat, lng) {
    if (listedTxSites().length >= TX_MAX) {
      sayTx("Cinq QTH d’émission au maximum.", false);
      setPlacingTx(false);
      return;
    }
    txSites = listedTxSites().concat([
      {
        label: "Émission " + (listedTxSites().length + 1),
        lat: Math.round(lat * 1e5) / 1e5,
        lon: Math.round(lng * 1e5) / 1e5,
      },
    ]);
    setPlacingTx(false);
    renderTxList();
    refreshGlobe();
    sayTx("QTH posé. Sauver pour mémoriser.", true);
    showTab("setup");
  }

  // Bandeau GGR : or #DEB200 + Montserrat 800 (h1 goldengloberace.com).
  const GGR_BANNER = "GGR 2026 - trafic HF";
  const BANNER_HOLD_MS = 2000;
  const BANNER_INTRO_MS = 7500;
  const BANNER_OPACITY = 0.55;
  const BANNER_OPACITY_UNDER = 0.08;
  const BANNER_FLEET_LAT = 18;
  const bannerHtml = { on: false, lng0: 0, opacity: BANNER_OPACITY };
  let bannerStarting = false;
  let bannerBelt = null;
  let bannerIntroDone = false;
  let bannerWatchOn = false;

  function applyBannerVisibility() {
    if (bannerBelt && bannerBelt.mesh) bannerBelt.mesh.visible = !metareaOn;
  }

  function htmlBannerPoints() {
    if (!bannerHtml.on || metareaOn) return [];
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
    mesh.visible = !metareaOn;
    scene.add(mesh);
    return { mesh, mat };
  }

  function runBannerAnim(g, belt) {
    bannerBelt = belt;
    const start = performance.now();
    const frame = (now) => {
      const elapsed = now - start;
      const t = (elapsed - BANNER_HOLD_MS) / BANNER_INTRO_MS;
      if (belt) {
        if (t < 1) belt.mesh.rotation.y = elapsed * 0.00055;
      } else if (t < 1) {
        bannerHtml.lng0 = (elapsed * 0.032) % 360;
        if (g) g.htmlElementsData(points());
      }
      applyBannerOpacity(g, belt, true);
      if (t < 1) {
        window.requestAnimationFrame(frame);
      } else {
        bannerIntroDone = true;
        watchBannerOpacity(g, belt);
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

  function scheduleIntroCamera(g, dest) {
    window.setTimeout(() => {
      if (g && dest) g.pointOfView(dest, BANNER_INTRO_MS);
    }, BANNER_HOLD_MS);
    const wantAbout = parseHash() === "trafic";
    if (!wantAbout) return;
    window.setTimeout(() => {
      if (!introAboutArmed) return;
      showTab("apropos");
      setPanelOpen(true);
    }, BANNER_HOLD_MS + BANNER_INTRO_MS);
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
    if (!metareaOn || !metareaFc) return [];
    return metareaFc.features || [];
  }

  function metareaLabelPoints() {
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

  function paintMetareaMap() {
    if (!map || typeof L === "undefined") return;
    if (map.metareaLayer) {
      map.removeLayer(map.metareaLayer);
      map.metareaLayer = null;
    }
    if (!metareaOn || !metareaFc) return;
    if (typeof map.getPane === "function" && !map.getPane("metarea")) {
      map.createPane("metarea");
      map.getPane("metarea").style.zIndex = 350;
    }
    const grp = L.layerGroup();
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
    grp.addTo(map);
    map.metareaLayer = grp;
  }

  function setMetarea(on) {
    metareaOn = !!on;
    try {
      localStorage.setItem("ggr-metarea", metareaOn ? "on" : "off");
    } catch {
      /* ignore */
    }
    if (metareaBtn) {
      metareaBtn.classList.toggle("is-on", metareaOn);
      metareaBtn.setAttribute("aria-pressed", metareaOn ? "true" : "false");
    }
    applyBannerVisibility();
    if (globe && typeof globe.polygonsData === "function") globe.polygonsData(metareaPolygons());
    if (globe) globe.htmlElementsData(points());
    paintMetareaMap();
  }

  function refreshMap() {
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
        interactive: false,
      }).addTo(mapLayers);
    });
    points()
      .filter((d) => d.kind !== "ggr_banner" && d.kind !== "metarea")
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
            iconSize: boat ? [22, 22] : [12, 12],
            iconAnchor: boat ? [11, 11] : [6, 6],
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
  }

  function refreshGlobe() {
    if (globe) {
      globe.htmlElementsData(points());
      if (typeof globe.pathsData === "function") globe.pathsData(paths());
      if (typeof globe.polygonsData === "function") globe.polygonsData(metareaPolygons());
      if (typeof globe.arcsData === "function") globe.arcsData([]);
    }
    refreshMap();
  }

  function sizeGlobe() {
    if (!globe || !el) return;
    globe.width(el.clientWidth);
    globe.height(el.clientHeight);
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

  function initMap() {
    if (!mapEl || map || typeof L === "undefined" || typeof L.map !== "function") return;
    const dest = fleetView();
    map = L.map(mapEl, {
      zoomControl: true,
      attributionControl: false,
      worldCopyJump: true,
    });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
    }).addTo(map);
    map.setView([dest.lat, dest.lng], altitudeToZoom(dest.altitude));
    if (typeof map.getPane === "function" && !map.getPane("metarea")) {
      map.createPane("metarea");
      map.getPane("metarea").style.zIndex = 350;
    }
    mapLayers = L.layerGroup().addTo(map);
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
      if (!map) initMap();
      else {
        map.invalidateSize();
        refreshMap();
      }
      pauseGlobe();
      return;
    }
    if (first3d) {
      initGlobe({ intro: boot });
      requestAnimationFrame(sizeGlobe);
    } else {
      resumeGlobe();
    }
  }

  function initGlobe(opts) {
    const intro = !opts || opts.intro !== false;
    if (typeof Globe !== "function") {
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
        globe.polygonCapColor((d) => hexToRgba((d.properties && d.properties.fill) || "#c9a227", 0.4));
      if (typeof globe.polygonSideColor === "function") globe.polygonSideColor(() => "rgba(8,16,24,0.18)");
      if (typeof globe.polygonStrokeColor === "function")
        globe.polygonStrokeColor((d) => (d.properties && d.properties.stroke) || "#1a1a1a");
      if (typeof globe.polygonAltitude === "function") globe.polygonAltitude(0.003);
      if (typeof globe.polygonCapCurvatureResolution === "function") globe.polygonCapCurvatureResolution(4);
      if (typeof globe.onPolygonClick === "function") {
        globe.onPolygonClick((poly) => {
          if (placingTx) return;
          const p = poly && poly.properties;
          if (!p) return;
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
    if (intro) {
      globe.pointOfView({ lat: 8, lng: dest.lng + 40, altitude: 2.7 }, 0);
      scheduleIntroCamera(globe, dest);
    } else {
      globe.pointOfView(dest, 0);
    }
    globe.controls().autoRotate = false;
    globe.controls().enableDamping = true;
    tuneOsmTiles(globe);
    if (intro) {
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
      showSel("<p class=\"err\">Globe 3D : " + esc(err && err.message ? err.message : err) + "</p>");
      if (introAboutArmed && parseHash() === "trafic") {
        showTab("apropos");
        setPanelOpen(true);
      }
    }
  }

  const saveBtn = document.getElementById("globe-save-buddy");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const tok = token();
      if (!tok) {
        sayBuddy("Jeton manquant : coller le jeton dans Setup.", false);
        return;
      }
      saveBtn.disabled = true;
      sayBuddy("Sauvegarde…", true);
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
            buddy_time_utc: cur.buddy_time_utc,
            buddy_lead: cur.buddy_lead,
            buddy_duration_minutes: cur.buddy_duration_minutes,
            buddy_kiwi_count: 4,
            buddy_skippers: [...skippers],
          }),
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          sayBuddy(body.detail || `Erreur ${res.status}`, false);
          return;
        }
        sayBuddy("Centroïde enregistré. Rechargement des Kiwi…", true);
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
        sayTx("Pose annulée.", true);
        return;
      }
      if (listedTxSites().length >= TX_MAX) {
        sayTx("Cinq QTH d’émission au maximum.", false);
        return;
      }
      setPlacingTx(true);
      sayTx("Cliquer la carte ou le globe pour poser un QTH.", true);
    });
  }

  if (txSaveBtn) {
    txSaveBtn.addEventListener("click", async () => {
      const tok = token();
      if (!tok) {
        sayTx("Jeton manquant : coller le jeton dans Setup.", false);
        return;
      }
      txSaveBtn.disabled = true;
      sayTx("Sauvegarde…", true);
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
            tx_sites: listedTxSites().map((s) => ({ label: s.label, lat: s.lat, lon: s.lon })),
          }),
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          sayTx(body.detail || `Erreur ${res.status}`, false);
          return;
        }
        sayTx("QTH d’émission enregistrés.", true);
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
  if (metareaBtn) {
    metareaBtn.classList.toggle("is-on", metareaOn);
    metareaBtn.setAttribute("aria-pressed", metareaOn ? "true" : "false");
    metareaBtn.addEventListener("click", () => setMetarea(!metareaOn));
  }
  fetch("/static/geo/metareas.json")
    .then((r) => (r.ok ? r.json() : null))
    .then((fc) => {
      if (!fc || fc.type !== "FeatureCollection") return;
      metareaFc = fc;
      setMetarea(metareaOn);
    })
    .catch(() => {});
  applyMapMode(mapMode, { boot: true });

  const traficQ = new URLSearchParams(location.search).get("trafic") || new URLSearchParams(location.search).get("vac");
  if (traficQ) {
    const found = trafics.find((v) => v.id === traficQ) || (data.trafics || data.vacations || []).find((v) => v.id === traficQ);
    if (found) playTrafic(found);
  }
})();
