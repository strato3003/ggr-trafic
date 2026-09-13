(() => {
  const el = document.getElementById("ggr-globe");
  const blob = document.getElementById("ggr-globe-data");
  if (!el || !blob) return;

  let data;
  try {
    data = JSON.parse(blob.textContent || "{}");
  } catch {
    return;
  }

  const TOKEN_KEY = "ggr-admin-token";
  const token = () => localStorage.getItem(TOKEN_KEY) || "";
  const boats = (data.boats || []).filter((b) => Number.isFinite(b.lat) && Number.isFinite(b.lon));
  const skippers = new Set(data.skippers || []);
  let includeFleet = !!data.include_fleet;
  const vacations = (data.vacations || []).filter((v) => Number.isFinite(v.lat) && Number.isFinite(v.lon));

  const selEl = document.getElementById("globe-sel");
  const vacsEl = document.getElementById("globe-vacs");
  const buddyVacsEl = document.getElementById("globe-buddy-vacs");
  const skipEl = document.getElementById("globe-skippers");
  const msgEl = document.getElementById("globe-buddy-msg");
  const includeEl = document.getElementById("globe-include-fleet");
  const playerBox = document.getElementById("globe-player");
  const playerTitle = document.getElementById("globe-player-title");
  const audioEl = document.getElementById("globe-audio");
  const videoEl = document.getElementById("globe-video");
  const chanEl = document.getElementById("globe-chan");
  const openVac = document.getElementById("globe-open-vac");
  const audioMsg = document.getElementById("globe-audio-msg");
  const wfLink = document.getElementById("globe-wf");
  const wfMsg = document.getElementById("globe-wf-msg");
  const chanLab = document.getElementById("globe-chan-lab");

  function mediaUrl(id, file) {
    return "/media/" + encodeURIComponent(id) + "/" + encodeURIComponent(file);
  }

  document.querySelectorAll(".globe-dock__tabs [data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".globe-dock__tabs [data-tab]").forEach((b) => b.classList.toggle("is-on", b === btn));
      document.querySelectorAll(".globe-panel").forEach((p) => {
        p.hidden = p.getAttribute("data-panel") !== btn.getAttribute("data-tab");
      });
    });
  });

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");
  }

  function showSel(html) {
    if (selEl) selEl.innerHTML = html;
    const tab = document.querySelector('.globe-dock__tabs [data-tab="sel"]');
    if (tab) tab.click();
  }

  function sayBuddy(text, ok) {
    if (!msgEl) return;
    msgEl.hidden = false;
    msgEl.textContent = text;
    msgEl.classList.toggle("err", !ok);
    msgEl.classList.toggle("ok", !!ok);
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

  function coverArc(bearings) {
    if (!bearings.length) return { start: 0, span: 24 };
    const s = bearings.map((b) => ((b % 360) + 360) % 360).sort((a, b) => a - b);
    let maxGap = 0;
    let gapAt = 0;
    for (let i = 0; i < s.length; i++) {
      const nxt = i + 1 < s.length ? s[i + 1] : s[0] + 360;
      const gap = nxt - s[i];
      if (gap > maxGap) {
        maxGap = gap;
        gapAt = i;
      }
    }
    const start = s[(gapAt + 1) % s.length];
    const span = Math.max(12, Math.min(140, 360 - maxGap + 10));
    return { start, span };
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
    (data.kiwis || []).forEach((k) => {
      if (!Number.isFinite(k.lat) || !Number.isFinite(k.lon)) return;
      rows.push({ ...k, lng: k.lon, kind: "kiwi" });
    });
    (data.buddy_kiwis || []).forEach((k) => {
      if (!Number.isFinite(k.lat) || !Number.isFinite(k.lon)) return;
      rows.push({ ...k, lng: k.lon, kind: "buddy_kiwi" });
    });
    const c = data.centroid || {};
    if (Number.isFinite(c.lat) && Number.isFinite(c.lon)) {
      rows.push({ ...c, lng: c.lon, kind: "centroid", name: "Centroïde flotte" });
    }
    const buddy = data.buddy || {};
    if (Number.isFinite(buddy.lat) && Number.isFinite(buddy.lon)) {
      rows.push({ ...buddy, lng: buddy.lon, kind: "buddy_cent", name: buddy.label || "Centroïde buddy" });
    }
    vacations.forEach((v) => {
      rows.push({
        ...v,
        lng: v.lon,
        kind: "vacation",
        name: v.title || v.id,
      });
    });
    return rows;
  }

  function markerEl(d) {
    const wrap = document.createElement("div");
    wrap.className = "globe-mark globe-mark--" + d.kind + (d.kind === "boat" && d.inBuddy ? " is-buddy" : "");
    wrap.style.cssText = "width:12px;height:12px;margin:0;padding:0;overflow:visible;";
    const icon = document.createElement("span");
    icon.className = "globe-mark__icon";
    if (d.kind === "boat") {
      icon.style.borderBottomColor = d.inBuddy ? "#e8c547" : d.colour || "#8aa4a8";
      const rot = Number.isFinite(d.heading) ? " rotate(" + d.heading + "deg)" : "";
      icon.style.transform = "translate(-50%,-50%)" + rot;
    } else if (d.kind === "vacation") {
      icon.style.background = d.is_buddy ? "#d4a84b" : "#d45c3a";
    }
    wrap.appendChild(icon);
    if ((d.kind === "boat" && d.inBuddy) || d.kind === "buddy_kiwi") {
      const name = document.createElement("span");
      name.className = "globe-mark__name";
      name.style.cssText = "position:absolute;left:14px;top:50%;transform:translateY(-50%);white-space:nowrap;";
      name.textContent = d.kind === "buddy_kiwi" ? d.site_label || d.loc || d.name || "Kiwi" : d.name || "";
      wrap.appendChild(name);
    }
    wrap.title = d.name || d.label || d.kind;
    icon.addEventListener("click", (ev) => {
      ev.stopPropagation();
      onPointClick(d);
    });
    return wrap;
  }

  function arcs() {
    const buddy = data.buddy || {};
    if (!Number.isFinite(buddy.lat) || !Number.isFinite(buddy.lon)) return [];
    return (data.buddy_kiwis || [])
      .filter((k) => Number.isFinite(k.lat) && Number.isFinite(k.lon))
      .map((k) => ({
        startLat: buddy.lat,
        startLng: buddy.lon,
        endLat: k.lat,
        endLng: k.lon,
        color: k.prop_zone === "nvis" ? "rgba(61,186,122,0.7)" : "rgba(110,201,224,0.55)",
        label: k.site_label || k.name,
      }));
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
        };
      })
      .filter(Boolean)
      .concat(coneArcs());
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

  function sectorArc(lat0, lon0, startBrg, span, rKm, steps) {
    const pts = [];
    for (let i = 0; i <= steps; i++) {
      const p = destPoint(lat0, lon0, startBrg + (span * i) / steps, rKm);
      pts.push([p[0], p[1]]);
    }
    return pts;
  }

  function coneArcs() {
    if (!boats.length) return [];
    const rows = [];
    const n = 11;
    sdrSites().forEach((k) => {
      const bearings = boats.map((b) => bearingDeg(k.lat, k.lon, b.lat, b.lon));
      const dists = boats.map((b) => haversineKm(k.lat, k.lon, b.lat, b.lon));
      const rMax = Math.max(...dists, 80) * 1.12;
      const { start, span } = coverArc(bearings);
      const steps = Math.max(18, Math.round(span / 2));
      const nvis = k.prop_zone === "nvis" || k._kind === "kiwi";
      const rgb = nvis ? "61,186,122" : "110,201,224";
      let sumW = 0;
      for (let i = 1; i <= n; i++) sumW += i;
      let r = 0;
      for (let i = 1; i <= n; i++) {
        r += (i / sumW) * rMax;
        rows.push({
          coords: sectorArc(k.lat, k.lon, start, span, r, steps),
          color: `rgba(${rgb},0.62)`,
        });
      }
      const left = destPoint(k.lat, k.lon, start, rMax);
      const right = destPoint(k.lat, k.lon, start + span, rMax);
      rows.push({ coords: [[k.lat, k.lon], [left[0], left[1]]], color: `rgba(${rgb},0.4)` });
      rows.push({ coords: [[k.lat, k.lon], [right[0], right[1]]], color: `rgba(${rgb},0.4)` });
    });
    return rows;
  }

  function fleetView() {
    const pts = boats.map((b) => [b.lat, b.lon]);
    const c = data.centroid || {};
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
    skipEl.innerHTML = boats
      .map((b) => {
        const on = boatInBuddy(b.name);
        return (
          `<li><label><input type="checkbox" data-skipper="${esc(b.name)}" ${on ? "checked" : ""}>` +
          ` ${esc(b.name)}${b.sail ? ` <span class="meta">(${esc(b.sail)})</span>` : ""}</label></li>`
        );
      })
      .join("");
    skipEl.querySelectorAll("input[data-skipper]").forEach((inp) => {
      inp.addEventListener("change", () => {
        const name = inp.getAttribute("data-skipper");
        if (inp.checked) skippers.add(name);
        else skippers.delete(name);
        refreshGlobe();
      });
    });
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
        return (
          `<li><button type="button" class="globe-vac" data-vid="${esc(v.id)}">` +
          `<span class="badge">${esc(tag)}</span> ${esc(when)} · ${esc(v.title || v.id)}</button></li>`
        );
      })
      .join("");
    root.querySelectorAll(".globe-vac").forEach((btn) => {
      btn.addEventListener("click", () => {
        const v = vacations.find((x) => x.id === btn.getAttribute("data-vid"));
        if (v) playVacation(v);
      });
    });
  }

  function renderVacList() {
    fillVacList(
      vacsEl,
      vacations.filter((v) => !v.is_buddy),
      "Aucun bulletin météo avec position."
    );
    fillVacList(
      buddyVacsEl,
      vacations.filter((v) => v.is_buddy),
      "Aucun buddy call avec position."
    );
  }

  function playVacation(v) {
    if (!playerBox || !audioEl || !chanEl) return;
    playerBox.hidden = false;
    if (playerTitle) playerTitle.textContent = v.title || v.id;
    if (openVac) openVac.href = "/vacations/" + encodeURIComponent(v.id);
    const chans = v.channels || [];
    if (chanLab) chanLab.classList.toggle("is-off", !chans.length);
    if (chans.length) {
      chanEl.innerHTML = chans
        .map((c, i) => {
          const qrg = Number.isFinite(c.freq_khz) ? Math.round(c.freq_khz) + " kHz" : "";
          const where = c.place || c.loc || c.site_label || c.kiwi || c.label || c.id || "";
          const audio = c.has_audio || c.audio ? "audio" : "pas d’audio";
          return `<option value="${i}">${esc([qrg, where, audio].filter(Boolean).join(" · "))}</option>`;
        })
        .join("");
    } else {
      chanEl.innerHTML = "";
    }
    const applyChan = () => {
      const ch = chans[Number(chanEl.value) || 0] || {};
      const audioFile = ch.audio;
      audioEl.pause();
      if (audioFile) {
        audioEl.src = mediaUrl(v.id, audioFile);
      } else {
        audioEl.removeAttribute("src");
      }
      audioEl.load();
      if (audioMsg) {
        audioMsg.hidden = !!audioFile;
        audioMsg.textContent = audioFile
          ? ""
          : (ch.place || ch.loc || "ce Kiwi") + " : pas d’audio.";
      }
      const vid = ch.video || null;
      const thumb = ch.thumb || null;
      if (videoEl) {
        if (vid) {
          videoEl.src = mediaUrl(v.id, vid);
          if (thumb) videoEl.poster = mediaUrl(v.id, thumb);
          else videoEl.removeAttribute("poster");
        } else {
          videoEl.removeAttribute("src");
          videoEl.removeAttribute("poster");
        }
        videoEl.load();
      }
      if (wfLink) {
        if (vid) {
          wfLink.hidden = false;
          wfLink.href = mediaUrl(v.id, vid);
        } else {
          wfLink.hidden = true;
          wfLink.removeAttribute("href");
        }
      }
      if (wfMsg) {
        wfMsg.hidden = !!vid;
        const where = ch.place || ch.loc || (Number.isFinite(ch.freq_khz) ? Math.round(ch.freq_khz) + " kHz" : "ce canal");
        wfMsg.textContent = vid
          ? ""
          : audioFile
            ? "Pas de waterfall sur " + where + " (audio seul)."
            : "Pas d’audio ni de waterfall · " + where;
      }
    };
    chanEl.onchange = applyChan;
    applyChan();
    const tabName = v.is_buddy ? "buddy" : "vac";
    const tab = document.querySelector('.globe-dock__tabs [data-tab="' + tabName + '"]');
    if (tab) tab.click();
    if (Number.isFinite(v.lat) && globe) {
      globe.pointOfView({ lat: v.lat, lng: v.lon, altitude: 1.6 }, 900);
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
          `${on ? "Retirer du centroïde buddy call" : "Ajouter au centroïde buddy call"}</button></p>`
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
      if (globe) globe.pointOfView({ lat: d.lat, lng: d.lon, altitude: 1.4 }, 800);
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
      if (globe) globe.pointOfView({ lat: d.lat, lng: d.lng, altitude: 1.5 }, 800);
      return;
    }
    if (d.kind === "vacation") {
      playVacation(d);
      return;
    }
    showSel(`<h3>${esc(d.name || d.label || d.kind)}</h3><p class="meta">${esc(d.fmt || "")}</p>`);
    if (globe && Number.isFinite(d.lat)) globe.pointOfView({ lat: d.lat, lng: d.lng, altitude: 1.8 }, 800);
  }

  let globe = null;

  function refreshGlobe() {
    if (!globe) return;
    globe.htmlElementsData(points());
    if (typeof globe.pathsData === "function") globe.pathsData(paths());
  }

  function initGlobe() {
    if (typeof Globe !== "function") {
      showSel("<p class=\"err\">Globe 3D indisponible (WebGL / script). Les tableaux restent utilisables plus bas.</p>");
      return;
    }
    try {
    const osmTile = (x, y, l) =>
      "https://" + ["a", "b", "c"][Math.abs(x + y) % 3] + ".tile.openstreetmap.org/" + l + "/" + x + "/" + y + ".png";
    globe = Globe()(el)
      .backgroundColor("#02050a")
      .backgroundImageUrl("https://unpkg.com/three-globe/example/img/night-sky.png")
      .showAtmosphere(true)
      .atmosphereColor("#7aa0c4")
      .atmosphereAltitude(0.08)
      .htmlElementsData(points())
      .htmlLat("lat")
      .htmlLng("lng")
      .htmlAltitude(0)
      .htmlElement(markerEl)
      .htmlTransitionDuration(0)
      .arcsData(arcs())
      .arcColor("color")
      .arcAltitudeAutoScale(0.1)
      .arcStroke(null)
      .arcDashLength(0)
      .arcDashGap(0)
      .arcDashAnimateTime(0)
      .pathsData(paths())
      .pathPoints("coords")
      .pathPointLat((p) => p[0])
      .pathPointLng((p) => p[1])
      .pathPointAlt(0)
      .pathColor((d) => d.color)
      .pathStroke(null)
      .pathTransitionDuration(0);

    if (typeof globe.globeTileEngineUrl === "function") {
      globe.globeTileEngineUrl(osmTile);
      if (typeof globe.globeTileEngineMaxLevel === "function") {
        globe.globeTileEngineMaxLevel(18);
      }
    } else {
      globe.globeImageUrl("https://unpkg.com/three-globe/example/img/earth-blue-marble.jpg");
    }

    if (typeof globe.pathResolution === "function") {
      globe.pathResolution(0.35);
    }

    if (typeof globe.htmlOcclude === "function") {
      try {
        globe.htmlOcclude(true);
      } catch {
        /* globe.gl < occlude API */
      }
    }

    const dest = fleetView();
    globe.pointOfView({ lat: dest.lat * 0.35 + 10, lng: dest.lng + 32, altitude: 2.4 }, 0);
    globe.controls().autoRotate = false;
    globe.controls().enableDamping = true;
    window.setTimeout(() => {
      if (globe) globe.pointOfView(dest, 4800);
    }, 450);

    const size = () => {
      globe.width(el.clientWidth);
      globe.height(el.clientHeight);
    };
    size();
    window.addEventListener("resize", size);
    el.addEventListener("pointerdown", () => {
      globe.controls().autoRotate = false;
    });
    } catch (err) {
      showSel("<p class=\"err\">Globe 3D : " + esc(err && err.message ? err.message : err) + "</p>");
    }
  }

  if (includeEl) {
    includeEl.addEventListener("change", () => {
      includeFleet = includeEl.checked;
    });
  }

  const saveBtn = document.getElementById("globe-save-buddy");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const tok = token();
      if (!tok) {
        sayBuddy("Jeton manquant : coller le jeton dans Réglages.", false);
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
            buddy_kiwi_count: cur.buddy_kiwi_count,
            buddy_include_fleet: includeFleet,
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

  renderSkippers();
  renderVacList();
  initGlobe();
})();
