(() => {
  const TOKEN_KEY = "ggr-admin-token";
  const token = () => localStorage.getItem(TOKEN_KEY) || "";

  const pad = (n) => String(n).padStart(2, "0");

  document.querySelectorAll(".js-countdown[data-iso]").forEach((el) => {
    const target = new Date(el.dataset.iso);
    const tick = () => {
      const ms = target.getTime() - Date.now();
      if (ms <= 0) {
        el.textContent = "en cours / voir demain";
        return;
      }
      const s = Math.floor(ms / 1000);
      const d = Math.floor(s / 86400);
      const h = Math.floor((s % 86400) / 3600);
      const m = Math.floor((s % 3600) / 60);
      const sec = s % 60;
      el.textContent = (d ? `${d} j ` : "") + `${pad(h)}:${pad(m)}:${pad(sec)}`;
    };
    tick();
    setInterval(tick, 1000);
  });

  (function bindRecording() {
    const nodes = () => document.querySelectorAll(".js-rec");
    if (!nodes().length) return;
    const first = document.querySelector(".js-rec");
    let rec = {
      recording: first && first.classList.contains("is-live"),
      recording_label: "Enregistrement",
      recording_ends_at: first ? first.dataset.ends || "" : "",
      next_recording_at: first ? first.dataset.next || "" : "",
    };
    function hms(ms) {
      const s = Math.max(0, Math.floor(ms / 1000));
      return pad(Math.floor(s / 3600)) + ":" + pad(Math.floor((s % 3600) / 60)) + ":" + pad(s % 60);
    }
    function paint() {
      nodes().forEach((el) => {
        const live = !!rec.recording;
        el.classList.toggle("is-live", live);
        el.classList.toggle("is-next", !live);
        const label = el.querySelector(".js-rec-label");
        const clock = el.querySelector(".js-rec-clock");
        if (live) {
          const name = rec.recording_label || "Enregistrement";
          if (label) label.textContent = name + " en cours";
          if (rec.recording_ends_at) el.dataset.ends = rec.recording_ends_at;
          const ends = Date.parse(el.dataset.ends || "");
          if (clock) clock.textContent = Number.isFinite(ends) ? hms(ends - Date.now()) : "—";
          return;
        }
        if (label) label.textContent = "Prochain enregistrement";
        if (rec.next_recording_at) el.dataset.next = rec.next_recording_at;
        const next = Date.parse(el.dataset.next || "");
        if (!clock) return;
        if (!Number.isFinite(next)) {
          clock.textContent = "—";
          return;
        }
        const remain = next - Date.now();
        clock.textContent = remain <= 0 ? "imminent" : hms(remain);
      });
    }
    paint();
    setInterval(paint, 1000);
    const sync = () => {
      fetch("/health", { cache: "no-store" })
        .then((res) => (res.ok ? res.json() : null))
        .then((st) => {
          if (!st) return;
          rec = {
            recording: !!st.recording,
            recording_label: st.recording_label || "Enregistrement",
            recording_ends_at: st.recording_ends_at || "",
            next_recording_at: st.next_recording_at || rec.next_recording_at,
          };
          paint();
        })
        .catch(() => {});
    };
    sync();
    setInterval(sync, 5000);
  })();

  const hasToken = !!token();
  document.querySelectorAll(".js-del-vac").forEach((btn) => {
    btn.hidden = !hasToken;
  });
  document.querySelectorAll(".js-del-need-token").forEach((n) => {
    n.hidden = hasToken;
  });

  (function bindUtcClocks() {
    const pad = (n) => String(n).padStart(2, "0");
    function startMsOf(iso) {
      const t = Date.parse(iso || "");
      return Number.isFinite(t) ? t : NaN;
    }
    function fmtTu(startMs, sec) {
      const s = Math.max(0, sec || 0);
      if (!Number.isFinite(startMs)) {
        return pad(Math.floor(s / 60)) + ":" + pad(Math.floor(s % 60));
      }
      const d = new Date(startMs + s * 1000);
      return pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()) + ":" + pad(d.getUTCSeconds()) + " TU";
    }
    function enhanceTuPlayer(media, startMs) {
      if (!media) return;
      media.controls = false;
      media.removeAttribute("controls");
      media._tuStartMs = startMs;
      let ui = media.nextElementSibling;
      if (!ui || !ui.classList.contains("tu-player")) {
        ui = document.createElement("div");
        ui.className = "tu-player";
        ui.innerHTML =
          '<button type="button" class="tu-player__play" aria-pressed="false">Lecture</button>' +
          '<span class="media-tu__clock">— TU</span>' +
          '<label class="tu-player__seek">Heure TU<input type="range" min="0" max="1" step="0.05" value="0"></label>';
        media.insertAdjacentElement("afterend", ui);
        const playBtn = ui.querySelector(".tu-player__play");
        const clock = ui.querySelector(".media-tu__clock");
        const seek = ui.querySelector("input");
        let seeking = false;
        const tick = () => {
          const t = Number.isFinite(media.currentTime) ? media.currentTime : 0;
          const dur = Number.isFinite(media.duration) ? media.duration : 0;
          const origin = media._tuStartMs;
          clock.textContent = dur ? fmtTu(origin, t) + " · " + fmtTu(origin, dur) : fmtTu(origin, t);
          if (dur) {
            seek.max = String(dur);
            if (!seeking) seek.value = String(t);
          }
          const on = !media.paused && !media.ended;
          playBtn.textContent = on ? "Pause" : "Lecture";
          playBtn.setAttribute("aria-pressed", on ? "true" : "false");
        };
        playBtn.addEventListener("click", () => {
          if (media.paused) media.play().catch(() => {});
          else media.pause();
        });
        seek.addEventListener("pointerdown", () => {
          seeking = true;
        });
        seek.addEventListener("input", () => {
          media.currentTime = Number(seek.value) || 0;
          tick();
        });
        seek.addEventListener("change", () => {
          seeking = false;
        });
        ["timeupdate", "loadedmetadata", "seeked", "play", "pause", "ended", "durationchange"].forEach((ev) => {
          media.addEventListener(ev, tick);
        });
        tick();
      }
    }
    window.ggrEnhanceTuPlayer = enhanceTuPlayer;
    window.ggrFmtTu = fmtTu;

    const host = document.querySelector("article.vac[data-started]");
    const pageStart = host ? startMsOf(host.getAttribute("data-started")) : NaN;
    document.querySelectorAll("article.vac audio, article.vac video").forEach((el) => {
      enhanceTuPlayer(el, pageStart);
    });
    document.querySelectorAll(".media-tu__clock").forEach((p) => {
      if (p.closest(".tu-player")) return;
      p.remove();
    });
  })();

  document.querySelectorAll(".js-del-vac").forEach((btn) => {
    btn.addEventListener("click", async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const id = btn.getAttribute("data-delete-trafic") || btn.getAttribute("data-delete-vacation");
      const tok = token();
      if (!id) return;
      if (!tok) {
        window.alert("Suppression refusée : renseigne le jeton dans Réglages, puis recharge.");
        return;
      }
      if (!window.confirm(`Supprimer définitivement ${id} (audio, vidéo, dossier) ?`)) return;
      btn.disabled = true;
      try {
        const res = await fetch("/api/trafic/" + encodeURIComponent(id) + "/delete", {
          method: "POST",
          headers: { "X-Admin-Token": tok },
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          if (window.location.pathname.indexOf("/trafic/") === 0 || window.location.pathname.indexOf("/vacations/") === 0) {
            window.location.href = "/";
          } else {
            const card = btn.closest(".card");
            if (card) card.remove();
          }
          return;
        }
        const detail = Array.isArray(data.detail)
          ? data.detail.map((x) => x.msg || x).join(" ")
          : data.detail;
        window.alert(detail || `Suppression impossible (${res.status})`);
      } catch (err) {
        window.alert(String(err));
      } finally {
        btn.disabled = false;
      }
    });
  });
})();
