window.GgrMixer = (function () {
  let handle = null;

  function unmount() {
    if (handle && typeof handle.destroy === "function") handle.destroy();
    handle = null;
  }

  function mount(opts) {
    unmount();
    handle = boot(opts || {});
    return handle;
  }

  function boot(opts) {
  const root = opts.root || document.getElementById("mix");
  if (!root) return null;
  let spec = Array.isArray(opts.tracks) ? opts.tracks : null;
  if (!spec) {
    const dataEl = document.getElementById("ggr-mix-data");
    if (dataEl) {
      try {
        spec = JSON.parse(dataEl.textContent || "[]");
      } catch {
        spec = [];
      }
    }
  }
  if (!Array.isArray(spec)) spec = [];
  spec = spec.slice(0, 6);

  const vacId = opts.vacId || root.getAttribute("data-vid") || "";
  const startMs = (function parseStart() {
    const iso = opts.started || root.getAttribute("data-started") || "";
    if (iso) {
      const t = Date.parse(iso);
      if (Number.isFinite(t)) return t;
    }
    const m = String(vacId).match(/^(\d{4}-\d{2}-\d{2}T)(\d{2})(\d{2})Z/);
    if (m) return Date.parse(m[1] + m[2] + ":" + m[3] + ":00Z");
    return NaN;
  })();
  const media = (file) =>
    "/media/" + encodeURIComponent(vacId) + "/" + encodeURIComponent(file);

  const statusEl = document.getElementById("mix-status");
  const deskEl = document.getElementById("mix-strips");
  const playBtn = document.getElementById("mix-play");
  const timeEl = document.getElementById("mix-time");
  const audioLoadEl = document.getElementById("mix-audio-load");
  const seekEl = document.getElementById("mix-seek");
  const loopBtn = document.getElementById("mix-loop");
  const loopLab = document.getElementById("mix-loop-lab");
  const loopHint = document.getElementById("mix-loop-hint");
  const unzoomBtn = document.getElementById("mix-unzoom");
  const onFocus = typeof opts.onFocus === "function" ? opts.onFocus : null;
  if (!deskEl || !playBtn || !seekEl) return null;
  setPlayUi(false);

  const TIME = 256;
  const FREQ = 128;
  const FFT = 512;
  const fftRe = new Float32Array(FFT);
  const fftIm = new Float32Array(FFT);
  const specSheet = document.createElement("canvas");
  specSheet.width = TIME;
  specSheet.height = FREQ;

  const tracks = [];
  let duration = 0;
  let playing = false;
  let t0 = 0;
  let dragging = false;
  let seekDragging = false;
  let resumeAfterDrag = false;
  let raf = 0;
  let tickTimer = 0;
  let playNoted = false;
  let focusId = null;
  let loopA = NaN;
  let loopB = NaN;
  let loopOn = false;
  let handleDrag = null;
  const ac = new AbortController();
  const sig = { signal: ac.signal };
  const ICO_EXPAND =
    '<svg class="mix__ico" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>';
  const ICO_COLLAPSE =
    '<svg class="mix__ico" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z"/></svg>';
  const ICO_LOOP =
    '<svg class="mix__ico" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/></svg>';
  if (loopBtn && !loopBtn.querySelector("svg")) loopBtn.insertAdjacentHTML("afterbegin", ICO_LOOP);
  if (unzoomBtn && !unzoomBtn.querySelector("svg")) unzoomBtn.insertAdjacentHTML("afterbegin", ICO_COLLAPSE);

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");
  }

  function fmtTu(offsetSec) {
    const sec = Math.max(0, offsetSec || 0);
    if (!Number.isFinite(startMs)) {
      const m = Math.floor(sec / 60);
      const s = Math.floor(sec % 60);
      return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
    }
    const d = new Date(startMs + sec * 1000);
    const p = (n) => String(n).padStart(2, "0");
    return p(d.getUTCHours()) + ":" + p(d.getUTCMinutes()) + ":" + p(d.getUTCSeconds()) + " TU";
  }

  function fftMag(src, offset, n, scale) {
    scale = scale || 1;
    const re = fftRe;
    const im = fftIm;
    re.fill(0);
    im.fill(0);
    const last = Math.max(n - 1, 1);
    for (let i = 0; i < n; i++) {
      const w = 0.5 * (1 - Math.cos((2 * Math.PI * i) / last));
      re[i] = (src[offset + i] || 0) * scale * w;
    }
    let j = 0;
    for (let i = 0; i < n; i++) {
      if (i < j) {
        const tr = re[i];
        re[i] = re[j];
        re[j] = tr;
      }
      let m = n >> 1;
      while (m >= 1 && j >= m) {
        j -= m;
        m >>= 1;
      }
      j += m;
    }
    for (let size = 2; size <= n; size <<= 1) {
      const half = size >> 1;
      const step = (-2 * Math.PI) / size;
      for (let i = 0; i < n; i += size) {
        for (let k = 0; k < half; k++) {
          const ang = step * k;
          const cr = Math.cos(ang);
          const ci = Math.sin(ang);
          const ur = re[i + k];
          const ui = im[i + k];
          const vr = re[i + k + half];
          const vi = im[i + k + half];
          const tr = cr * vr - ci * vi;
          const ti = cr * vi + ci * vr;
          re[i + k] = ur + tr;
          im[i + k] = ui + ti;
          re[i + k + half] = ur - tr;
          im[i + k + half] = ui - ti;
        }
      }
    }
    const mag = new Float32Array(n / 2);
    for (let k = 0; k < n / 2; k++) mag[k] = Math.hypot(re[k], im[k]);
    return mag;
  }

  function wavMono16(ab) {
    const dv = new DataView(ab);
    if (dv.byteLength < 44) return null;
    const tag = (o) =>
      String.fromCharCode(dv.getUint8(o), dv.getUint8(o + 1), dv.getUint8(o + 2), dv.getUint8(o + 3));
    if (tag(0) !== "RIFF" || tag(8) !== "WAVE") return null;
    let o = 12;
    let sr = 12000;
    let bits = 16;
    let ch = 1;
    let dataOff = 0;
    let dataLen = 0;
    while (o + 8 <= dv.byteLength) {
      const id = tag(o);
      const sz = dv.getUint32(o + 4, true);
      const body = o + 8;
      if (id === "fmt ") {
        ch = dv.getUint16(body + 2, true) || 1;
        sr = dv.getUint32(body + 4, true) || 12000;
        bits = dv.getUint16(body + 14, true) || 16;
      } else if (id === "data") {
        dataOff = body;
        dataLen = sz;
        break;
      }
      o = body + sz + (sz & 1);
    }
    if (!dataOff || bits !== 16) return null;
    const frame = 2 * Math.max(1, ch);
    const n = Math.min(
      Math.floor(dataLen / frame),
      Math.floor((dv.byteLength - dataOff) / frame)
    );
    if (n <= 0) return null;
    if (ch === 1 && dataOff % 2 === 0) {
      return { samples: new Int16Array(ab, dataOff, n), sampleRate: sr };
    }
    const samples = new Int16Array(n);
    for (let i = 0; i < n; i++) samples[i] = dv.getInt16(dataOff + i * frame, true);
    return { samples, sampleRate: sr };
  }

  function dbToUnit(db) {
    return Math.pow(Math.max(0, Math.min(1, (db + 80) / 60)), 0.72);
  }

  async function spectrogram(channel, sampleRate, scale, onTick) {
    const n = channel.length;
    const img = new Float32Array(TIME * FREQ);
    img.fill(-120);
    if (n < FFT) return img;
    const nyquist = sampleRate / 2;
    const fMax = Math.min(2700, nyquist);
    const binMax = Math.max(2, Math.floor((fMax / nyquist) * (FFT / 2)));
    const sc = scale || 1;
    for (let t = 0; t < TIME; t++) {
      const start = Math.min(n - FFT, Math.floor((t / TIME) * (n - FFT)));
      const mag = fftMag(channel, start, FFT, sc);
      const row = t * FREQ;
      for (let f = 0; f < FREQ; f++) {
        const bin = 1 + Math.floor((f / FREQ) * (binMax - 1));
        img[row + f] = 20 * Math.log10(mag[bin] + 1e-9);
      }
      if (onTick && (t % 16 === 0 || t === TIME - 1)) {
        await onTick(img, true, (t + 1) / TIME);
      }
    }
    const sample = [];
    const step = Math.max(1, Math.floor(img.length / 4096));
    for (let i = 0; i < img.length; i += step) sample.push(img[i]);
    sample.sort((a, b) => a - b);
    const lo = sample[Math.floor(sample.length * 0.25)] || -80;
    const hi = Math.max(sample[Math.floor(sample.length * 0.99)] || -20, lo + 12);
    const span = Math.max(8, hi - lo);
    for (let i = 0; i < img.length; i++) {
      const u = Math.max(0, Math.min(1, (img[i] - lo) / span));
      img[i] = Math.pow(u, 0.72);
    }
    if (onTick) await onTick(img, false, 1);
    return img;
  }

  function kiwiColor(t, out) {
    const u = Math.max(0, Math.min(1, t));
    let r;
    let g;
    let b;
    if (u < 0.13) {
      const k = u / 0.13;
      r = 0;
      g = 0;
      b = 8 + 72 * k;
    } else if (u < 0.32) {
      const k = (u - 0.13) / 0.19;
      r = 0;
      g = 8 + 28 * k;
      b = 80 + 145 * k;
    } else if (u < 0.5) {
      const k = (u - 0.32) / 0.18;
      r = 0;
      g = 36 + 184 * k;
      b = 225 - 55 * k;
    } else if (u < 0.68) {
      const k = (u - 0.5) / 0.18;
      r = 16 + 48 * k;
      g = 220 - 10 * k;
      b = 170 - 152 * k;
    } else if (u < 0.84) {
      const k = (u - 0.68) / 0.16;
      r = 64 + 176 * k;
      g = 210 + 30 * k;
      b = 18;
    } else {
      const k = (u - 0.84) / 0.16;
      r = 240 + 15 * k;
      g = 240 + 15 * k;
      b = 18 + 237 * k;
    }
    out[0] = r;
    out[1] = g;
    out[2] = b;
  }

  function masterEl() {
    return (
      tracks.find((tr) => tr.el && !tr.el.paused && Number.isFinite(tr.el.currentTime)) ||
      (tracks[0] && tracks[0].el) ||
      null
    );
  }

  function nowT() {
    if (playing) {
      let t = t0;
      tracks.forEach((tr) => {
        if (tr.el && Number.isFinite(tr.el.currentTime) && !tr.el.paused) {
          t = Math.max(t, tr.el.currentTime);
        }
      });
      return duration ? Math.min(duration, t) : t;
    }
    return t0;
  }

  function resizeCanvas(tr) {
    const canvas = tr.canvas;
    if (!canvas) return false;
    const wrap = canvas.parentElement;
    const cssW = Math.max(64, (wrap && wrap.clientWidth) || canvas.clientWidth || 140);
    const cssH = Math.max(18, (wrap && wrap.clientHeight) || canvas.clientHeight || 28);
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.floor(cssW * dpr);
    const h = Math.floor(cssH * dpr);
    if (canvas.width === w && canvas.height === h) return false;
    canvas.width = w;
    canvas.height = h;
    return true;
  }

  function bakeSpec(tr, asDb) {
    const img = tr.spec;
    if (!img) return;
    const ctx = specSheet.getContext("2d");
    const idata = ctx.createImageData(TIME, FREQ);
    const px = idata.data;
    const rgb = [0, 0, 0];
    const dim = tr.muted ? 0.22 : 1;
    for (let y = 0; y < FREQ; y++) {
      const f = FREQ - 1 - y;
      for (let x = 0; x < TIME; x++) {
        let v = img[x * FREQ + f] || 0;
        if (asDb) v = dbToUnit(v);
        kiwiColor(v * dim, rgb);
        const off = (y * TIME + x) * 4;
        px[off] = rgb[0];
        px[off + 1] = rgb[1];
        px[off + 2] = rgb[2];
        px[off + 3] = 255;
      }
    }
    ctx.putImageData(idata, 0, 0);
    if (!tr.specBmp) {
      tr.specBmp = document.createElement("canvas");
      tr.specBmp.width = TIME;
      tr.specBmp.height = FREQ;
    }
    tr.specBmp.getContext("2d").drawImage(specSheet, 0, 0);
  }

  function drawTrack(tr) {
    const canvas = tr.canvas;
    if (!canvas) return;
    resizeCanvas(tr);
    const w = canvas.width;
    const h = canvas.height;
    if (!w || !h) return;
    const ctx2d = canvas.getContext("2d");
    ctx2d.imageSmoothingEnabled = false;
    ctx2d.fillStyle = "#000";
    ctx2d.fillRect(0, 0, w, h);
    if (tr.specBmp) ctx2d.drawImage(tr.specBmp, 0, 0, w, h);
  }

  function draw() {
    tracks.forEach(drawTrack);
    updateHead();
  }

  function bufferedPct(el) {
    if (!el || !el.buffered || !el.buffered.length) return 0;
    const dur = el.duration;
    if (!Number.isFinite(dur) || dur <= 0) return 0;
    try {
      return Math.min(100, (el.buffered.end(el.buffered.length - 1) / dur) * 100);
    } catch {
      return 0;
    }
  }

  function updateAudioLoad() {
    if (!audioLoadEl) return;
    const live = tracks.filter((t) => t.el && !t.dead);
    if (!live.length) {
      audioLoadEl.hidden = true;
      audioLoadEl.textContent = "";
      return;
    }
    const HAVE_FUTURE = 3;
    const ready = live.filter((t) => t.el.readyState >= HAVE_FUTURE).length;
    const loading = live.some((t) => t.el.networkState === 2);
    const starving = playing && live.some((t) => t.el.readyState < HAVE_FUTURE);
    if (!loading && !starving && ready === live.length) {
      audioLoadEl.hidden = true;
      audioLoadEl.textContent = "";
      return;
    }
    if (!playing && !loading) {
      audioLoadEl.hidden = true;
      audioLoadEl.textContent = "";
      return;
    }
    const pcts = live.map((t) => bufferedPct(t.el)).filter((p) => p > 0);
    const avg = pcts.length ? Math.round(pcts.reduce((a, b) => a + b, 0) / pcts.length) : 0;
    audioLoadEl.hidden = false;
    if (starving && avg && avg < 100) audioLoadEl.textContent = "Buffer " + avg + " %";
    else if (avg) audioLoadEl.textContent = "Audio " + avg + " % · " + ready + "/" + live.length;
    else audioLoadEl.textContent = "Chargement audio " + ready + "/" + live.length;
    audioLoadEl.title = "Fichiers WAV USB en cours de chargement dans le navigateur";
  }

  function updateHead() {
    if (!duration) return;
    const t = nowT();
    const pct = Math.max(0, Math.min(1, t / duration));
    tracks.forEach((tr) => {
      if (tr.head) tr.head.style.left = pct * 100 + "%";
    });
    if (!seekDragging) seekEl.value = String(t);
    if (timeEl) timeEl.textContent = fmtTu(t) + " · " + fmtTu(duration);
    seekEl.max = String(duration);
  }

  function seekElTo(tr, off) {
    if (!tr.el) return;
    try {
      const maxOff = Number.isFinite(tr.el.duration) ? Math.max(0, tr.el.duration - 0.05) : off;
      tr.el.currentTime = Math.min(Math.max(0, off), maxOff);
    } catch {
      /* metadata pas encore prête */
    }
  }

  function ensureReady(tr, off) {
    if (!tr.el) return;
    applyGain(tr);
    const go = () => {
      if (!playing) return;
      tr.el.play().catch(() => {
        tr.el.addEventListener("canplay", go, { once: true });
      });
    };
    const cur = tr.el.currentTime;
    if (Number.isFinite(tr.el.duration) && Math.abs((Number.isFinite(cur) ? cur : 0) - off) > 0.15) {
      tr.el.addEventListener("seeked", go, { once: true });
      seekElTo(tr, off);
      setTimeout(() => {
        if (playing && tr.el.paused) go();
      }, 400);
      return;
    }
    seekElTo(tr, off);
    go();
  }

  function setPlayUi(on) {
    playBtn.setAttribute("aria-pressed", on ? "true" : "false");
    playBtn.setAttribute("aria-label", on ? "Pause" : "Lecture");
    playBtn.title = on ? "Pause" : "Lecture";
  }

  function noteReplayPlay() {
    if (playNoted || !vacId) return;
    playNoted = true;
    try {
      fetch("/api/trafic/" + encodeURIComponent(vacId) + "/play", {
        method: "POST",
        keepalive: true,
        headers: { Accept: "application/json" },
      }).catch(function () {});
    } catch {
      /* ignore */
    }
  }

  function startSources(offset) {
    const off = Math.max(0, offset || 0);
    t0 = off;
    playing = true;
    noteReplayPlay();
    tracks.forEach((tr) => {
      if (tr.el && tr.el.preload !== "auto") tr.el.preload = "auto";
      ensureReady(tr, off);
    });
    updateAudioLoad();
    setPlayUi(true);
    cancelAnimationFrame(raf);
    clearInterval(tickTimer);
    tick();
    tickTimer = setInterval(tick, 100);
    raf = requestAnimationFrame(function loop() {
      tick();
      if (playing) raf = requestAnimationFrame(loop);
    });
  }

  function pauseAt(t) {
    t0 = Math.max(0, Math.min(duration || t, t));
    playing = false;
    tracks.forEach((tr) => {
      if (tr.el) tr.el.pause();
    });
    setPlayUi(false);
    cancelAnimationFrame(raf);
    clearInterval(tickTimer);
    updateHead();
    updateAudioLoad();
  }

  function tick() {
    if (!playing) return;
    const t = nowT();
    updateHead();
    if (loopOn && loopOk()) {
      const b = Math.max(loopA, loopB);
      if (t >= b - 0.04) {
        startSources(Math.min(loopA, loopB));
        return;
      }
    }
    if (duration && t >= duration - 0.03) pauseAt(duration);
  }

  function previewSeek(t) {
    t0 = Math.max(0, Math.min(duration || t, t));
    seekEl.value = String(t0);
    if (duration) seekEl.max = String(duration);
    if (playing) {
      playing = false;
      tracks.forEach((tr) => {
        if (tr.el) tr.el.pause();
      });
      cancelAnimationFrame(raf);
      clearInterval(tickTimer);
    }
    tracks.forEach((tr) => {
      if (!tr.el) return;
      try {
        tr.el.currentTime = t0;
      } catch {
        /* ignore */
      }
    });
    updateHead();
  }

  function pointerTime(ev, canvas) {
    const rect = canvas.getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (ev.clientX - rect.left) / Math.max(1, rect.width)));
    return x * timeSpan();
  }

  function timeSpan() {
    return duration > 0 ? duration : 1;
  }

  function loopOk() {
    return Number.isFinite(loopA) && Number.isFinite(loopB) && loopB - loopA > 0.12;
  }

  function ensureLoopRange() {
    if (loopOk()) {
      if (duration > 2 && loopB <= 1.05) {
        loopA = 0;
        loopB = duration;
      }
      return true;
    }
    loopA = 0;
    loopB = timeSpan();
    return loopOk();
  }

  function updateLoopUi() {
    const span = timeSpan();
    const ok = loopOk();
    const show = !!(focusId && loopOn);
    tracks.forEach((tr) => {
      if (!tr.loopEl) return;
      const onThis = show && tr.id === focusId;
      tr.loopEl.hidden = !onThis;
      if (!onThis || !span) return;
      const a = ok ? Math.min(loopA, loopB) : 0;
      const b = ok ? Math.max(loopA, loopB) : span;
      tr.loopEl.style.left = (a / span) * 100 + "%";
      tr.loopEl.style.width = ((b - a) / span) * 100 + "%";
    });
    if (loopLab) {
      loopLab.hidden = !(show && ok);
      if (show && ok) loopLab.textContent = fmtTu(Math.min(loopA, loopB)) + " → " + fmtTu(Math.max(loopA, loopB));
    }
    if (loopHint) loopHint.hidden = true;
    if (loopBtn) {
      loopBtn.hidden = !focusId;
      loopBtn.setAttribute("aria-pressed", focusId && loopOn ? "true" : "false");
    }
    if (unzoomBtn) unzoomBtn.hidden = !focusId;
    deskEl.querySelectorAll('[data-act="zoom"]').forEach((btn) => {
      const ch = btn.closest(".mix__ch");
      const on = !!(focusId && ch && ch.getAttribute("data-id") === focusId);
      btn.innerHTML = on ? ICO_COLLAPSE : ICO_EXPAND;
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.setAttribute("aria-label", on ? "Replier" : "Plein écran");
      btn.setAttribute("title", on ? "Replier" : "Plein écran");
    });
  }

  function setFocus(id) {
    focusId = id || null;
    document.body.classList.toggle("kiwi-mix-focus", !!focusId);
    deskEl.querySelectorAll(".mix__ch").forEach((el) => {
      el.classList.toggle("is-focus", focusId && el.getAttribute("data-id") === focusId);
    });
    if (!focusId) {
      loopOn = false;
    }
    updateLoopUi();
    if (onFocus) onFocus(focusId);
    window.dispatchEvent(new Event("resize"));
    requestAnimationFrame(() => tracks.forEach(drawTrack));
  }

  function bindLoopHandles(tr) {
    if (!tr.loopEl) return;
    const ha = tr.loopEl.querySelector(".mix__loop-h--a");
    const hb = tr.loopEl.querySelector(".mix__loop-h--b");
    if (!ha || !hb) return;
    const start = (which, ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (!duration && !timeSpan()) return;
      handleDrag = which;
      resumeAfterDrag = playing;
      if (playing) pauseAt(nowT());
      ev.currentTarget.setPointerCapture(ev.pointerId);
    };
    const move = (ev) => {
      if (!handleDrag) return;
      const t = pointerTime(ev, tr.canvas);
      const gap = 0.2;
      const span = timeSpan();
      const a = Number.isFinite(loopA) ? loopA : 0;
      const b = Number.isFinite(loopB) ? loopB : span;
      if (handleDrag === "a") loopA = Math.max(0, Math.min(t, b - gap));
      else loopB = Math.min(span, Math.max(t, a + gap));
      updateLoopUi();
    };
    const stop = () => {
      if (!handleDrag) return;
      handleDrag = null;
      if (resumeAfterDrag) startSources(Math.min(loopA, loopB));
      resumeAfterDrag = false;
    };
    ha.addEventListener("pointerdown", (ev) => start("a", ev));
    hb.addEventListener("pointerdown", (ev) => start("b", ev));
    ha.addEventListener("pointermove", move);
    hb.addEventListener("pointermove", move);
    ha.addEventListener("pointerup", stop);
    hb.addEventListener("pointerup", stop);
    ha.addEventListener("pointercancel", stop);
    hb.addEventListener("pointercancel", stop);
  }

  function bindCanvasSeek(tr) {
    const canvas = tr.canvas;
    canvas.addEventListener("pointerdown", (ev) => {
      if (!duration) return;
      resumeAfterDrag = playing;
      dragging = true;
      canvas.setPointerCapture(ev.pointerId);
      previewSeek(pointerTime(ev, canvas));
    });
    canvas.addEventListener("pointermove", (ev) => {
      if (!dragging) return;
      previewSeek(pointerTime(ev, canvas));
    });
    canvas.addEventListener("pointerup", () => {
      dragging = false;
      if (resumeAfterDrag) startSources(t0);
      resumeAfterDrag = false;
    });
    canvas.addEventListener("pointercancel", () => {
      dragging = false;
      resumeAfterDrag = false;
    });
  }

  seekEl.addEventListener("pointerdown", () => {
    resumeAfterDrag = playing;
    dragging = true;
    seekDragging = true;
    if (playing) pauseAt(nowT());
  }, sig);
  seekEl.addEventListener("input", () => {
    previewSeek(Number(seekEl.value) || 0);
  }, sig);
  seekEl.addEventListener("change", () => {
    dragging = false;
    seekDragging = false;
    if (resumeAfterDrag) startSources(t0);
    resumeAfterDrag = false;
  }, sig);

  function applyGain(tr) {
    const vol = tr.muted ? 0 : Number.isFinite(tr.volume) ? tr.volume : 1;
    if (tr.el) {
      tr.el.muted = !!tr.muted;
      tr.el.volume = Math.min(1, Math.max(0, vol));
    }
  }

  function renderDesk() {
    const stage = document.getElementById("mix-stage");
    if (stage) stage.hidden = true;
    deskEl.classList.add("mix__desk");
    deskEl.innerHTML = tracks
      .map((tr) => {
        const qrg = Number.isFinite(tr.freq_khz) ? Math.round(tr.freq_khz) + " kHz" : "";
        const where = tr.place || tr.site_label || tr.label || tr.id;
        const deadCls = tr.dead ? " is-dead is-mute" : "";
        return (
          '<div class="mix__ch' +
          deadCls +
          '" data-id="' +
          esc(tr.id) +
          '">' +
          '<div class="mix__ch-ctrl">' +
          '<label class="mix__fader"><span>Vol</span><input type="range" min="0" max="150" value="100" step="1" data-act="vol"></label>' +
          '<button type="button" class="mix__mute" data-act="mute" aria-pressed="false">Mute</button>' +
          '<button type="button" class="mix__zoom mix__ico-btn" data-act="zoom" aria-label="Plein écran" title="Plein écran">' +
          ICO_EXPAND +
          "</button>" +
          '<div class="mix__meta"><strong>' +
          esc(qrg) +
          "</strong><span>" +
          esc(where) +
          "</span></div>" +
          "</div>" +
          '<div class="mix__wf-wrap">' +
          '<canvas class="mix__wf" aria-label="Waterfall USB ' +
          esc(qrg) +
          " " +
          esc(where) +
          '"></canvas>' +
          (tr.dead
            ? ""
            : '<div class="mix__wf-load"' +
              (tr.waterfall ? " hidden" : "") +
              ">Extraction bande son en cours… 0 %</div>") +
          '<div class="mix__loop" hidden>' +
          '<button type="button" class="mix__loop-h mix__loop-h--a" aria-label="Borne A de la boucle"></button>' +
          '<button type="button" class="mix__loop-h mix__loop-h--b" aria-label="Borne B de la boucle"></button>' +
          "</div>" +
          '<div class="mix__head"></div>' +
          "</div></div>"
        );
      })
      .join("");
    deskEl.querySelectorAll(".mix__ch").forEach((el, i) => {
      const tr = tracks[i];
      tr.canvas = el.querySelector("canvas");
      tr.head = el.querySelector(".mix__head");
      tr.loadEl = el.querySelector(".mix__wf-load");
      tr.loopEl = el.querySelector(".mix__loop");
      const muteBtn = el.querySelector('[data-act="mute"]');
      const vol = el.querySelector('[data-act="vol"]');
      const zoomBtn = el.querySelector('[data-act="zoom"]');
      if (tr.dead) {
        if (muteBtn) muteBtn.disabled = true;
        if (vol) vol.disabled = true;
        if (zoomBtn) zoomBtn.disabled = true;
        return;
      }
      bindCanvasSeek(tr);
      bindLoopHandles(tr);
      if (zoomBtn) {
        zoomBtn.addEventListener("click", () => setFocus(focusId === tr.id ? null : tr.id));
      }
      muteBtn.addEventListener("click", () => {
        tr.muted = !tr.muted;
        muteBtn.setAttribute("aria-pressed", tr.muted ? "true" : "false");
        el.classList.toggle("is-mute", tr.muted);
        applyGain(tr);
        if (!tr.storedWf) bakeSpec(tr, !!tr.specDb);
        drawTrack(tr);
      });
      vol.addEventListener("input", (ev) => {
        const v = Number(ev.target.value);
        tr.volume = Number.isFinite(v) ? v / 100 : 1;
        applyGain(tr);
      });
      applyGain(tr);
    });
  }

  function playToggle() {
    if (playing) pauseAt(nowT());
    else {
      let t = duration && duration - t0 < 0.08 ? 0 : t0;
      if (loopOn && loopOk()) {
        const a = Math.min(loopA, loopB);
        const b = Math.max(loopA, loopB);
        if (t < a || t >= b - 0.04) t = a;
      }
      startSources(t);
    }
  }

  playBtn.addEventListener("click", playToggle, sig);
  if (loopBtn) {
    loopBtn.addEventListener(
      "click",
      () => {
        if (!focusId) return;
        if (loopOn) {
          loopOn = false;
        } else {
          loopOn = true;
          ensureLoopRange();
        }
        updateLoopUi();
      },
      sig
    );
  }
  if (unzoomBtn) {
    unzoomBtn.addEventListener("click", () => setFocus(null), sig);
  }

  function onKey(ev) {
    if (ev.code !== "Space") return;
    if (ev.target && /input|textarea|select|button/i.test(ev.target.tagName)) return;
    ev.preventDefault();
    playBtn.click();
  }
  function onResize() {
    tracks.forEach(drawTrack);
  }
  window.addEventListener("keydown", onKey, sig);
  window.addEventListener("resize", onResize, sig);
  const ro = typeof ResizeObserver === "function" ? new ResizeObserver(onResize) : null;
  if (ro) ro.observe(deskEl);

  function noteDuration(sec) {
    if (!Number.isFinite(sec) || sec <= 0) return;
    duration = Math.max(duration, sec);
    seekEl.min = "0";
    seekEl.max = String(duration);
    seekEl.step = "0.05";
    playBtn.disabled = false;
    if (loopOn) ensureLoopRange();
    updateHead();
    updateLoopUi();
  }

  function setExtract(tr, pct, done) {
    const el = tr.loadEl;
    if (!el) return;
    if (done || tr.dead) {
      el.hidden = true;
      return;
    }
    const n = Math.max(0, Math.min(100, Math.round(pct)));
    el.hidden = false;
    el.textContent = "Extraction bande son en cours… " + n + " %";
  }

  function attachAudio(tr) {
    if (tr.dead || !tr.src) {
      if (!tr.storedWf) bakeSpec(tr, false);
      drawTrack(tr);
      return;
    }
    const url = media(tr.src);
    tr.el = new Audio(url);
    tr.el.preload = "none";
    tr.el.controls = false;
    tr.el.hidden = true;
    tr.el.setAttribute("aria-hidden", "true");
    tr.el.playsInline = true;
    let bin = document.getElementById("mix-audio-bin");
    if (!bin) {
      bin = document.createElement("div");
      bin.id = "mix-audio-bin";
      bin.hidden = true;
      root.appendChild(bin);
    }
    bin.appendChild(tr.el);
    ["progress", "canplay", "canplaythrough", "waiting", "playing", "stalled", "loadeddata", "loadstart"].forEach(
      (ev) => {
        tr.el.addEventListener(ev, updateAudioLoad);
      }
    );
    tr.el.addEventListener("loadedmetadata", () => {
      noteDuration(tr.el.duration);
      if (!playing) seekElTo(tr, t0);
    });
    tr.el.addEventListener("ended", () => {
      if (playing && masterEl() === tr.el) pauseAt(duration || tr.el.currentTime || 0);
    });
  }

  function pcmName(src) {
    return String(src || "").replace(/\.(mp3|m4a|aac)$/i, ".wav");
  }

  async function fetchWav(tr) {
    if (tr.dead || !tr.src) return null;
    const pcm = tr.wav || pcmName(tr.src);
    if (!pcm) return null;
    setExtract(tr, 0);
    try {
      const res = await fetch(media(pcm));
      if (!res.ok) throw new Error("HTTP " + res.status);
      const total = Number(res.headers.get("content-length")) || 0;
      let raw;
      if (res.body) {
        const reader = res.body.getReader();
        const chunks = [];
        let received = 0;
        while (true) {
          const step = await reader.read();
          if (step.done) break;
          chunks.push(step.value);
          received += step.value.byteLength;
          if (total) setExtract(tr, (received / total) * 45);
          else setExtract(tr, Math.min(40, 5 + received / 350000));
        }
        raw = await new Blob(chunks).arrayBuffer();
      } else {
        raw = await res.arrayBuffer();
      }
      setExtract(tr, 45);
      return wavMono16(raw);
    } catch (err) {
      tr.place = (tr.place || tr.id) + " (échec waterfall)";
      const meta =
        tr.canvas &&
        tr.canvas.closest(".mix__ch") &&
        tr.canvas.closest(".mix__ch").querySelector(".mix__meta span");
      if (meta) meta.textContent = tr.place;
      setExtract(tr, 0, true);
      console.warn("Mixage piste", tr.id, err);
      return null;
    }
  }

  async function loadStoredWf(tr) {
    const name = tr.waterfall || (tr.id ? "waterfall-" + tr.id + ".png" : "");
    if (tr.dead || !name || !vacId) return false;
    tr.waterfall = name;
    const url = media(name);
    try {
      const bag = (window.GgrWfCache = window.GgrWfCache || {});
      let blob = bag[url] || null;
      if (!blob) {
        const res = await fetch(url, { cache: "force-cache", priority: "high" });
        if (!res.ok) return false;
        blob = await res.blob();
        if (blob && blob.size > 128) bag[url] = blob;
      }
      if (!blob || blob.size < 128) return false;
      const bmp = await createImageBitmap(blob);
      if (!tr.specBmp) {
        tr.specBmp = document.createElement("canvas");
        tr.specBmp.width = TIME;
        tr.specBmp.height = FREQ;
      }
      const ctx = tr.specBmp.getContext("2d");
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(bmp, 0, 0, TIME, FREQ);
      if (bmp.close) bmp.close();
      tr.storedWf = true;
      setExtract(tr, 100, true);
      drawTrack(tr);
      return true;
    } catch {
      return false;
    }
  }

  function waitDuration(tr, ms) {
    if (tr.dead || !tr.el) return Promise.resolve();
    if (Number.isFinite(tr.el.duration) && tr.el.duration > 0) {
      noteDuration(tr.el.duration);
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        clearTimeout(tid);
        tr.el.removeEventListener("loadedmetadata", onMeta);
        tr.el.removeEventListener("error", onMeta);
        resolve();
      };
      const onMeta = () => {
        noteDuration(tr.el.duration);
        finish();
      };
      const tid = setTimeout(finish, ms || 20000);
      tr.el.addEventListener("loadedmetadata", onMeta, { once: true });
      tr.el.addEventListener("error", onMeta, { once: true });
    });
  }

  async function paintSpec(tr, wav) {
    if (!wav) {
      tr.specDb = false;
      bakeSpec(tr, false);
      drawTrack(tr);
      setExtract(tr, 0, true);
      return;
    }
    noteDuration(wav.samples.length / wav.sampleRate);
    tr.specDb = true;
    setExtract(tr, 45);
    tr.spec = await spectrogram(wav.samples, wav.sampleRate, 1 / 32768, async (img, asDb, frac) => {
      tr.spec = img;
      tr.specDb = asDb;
      bakeSpec(tr, asDb);
      drawTrack(tr);
      setExtract(tr, 45 + Math.max(0, frac || 0) * 55);
      await new Promise((r) => setTimeout(r, 0));
    });
    tr.specDb = false;
    bakeSpec(tr, false);
    drawTrack(tr);
    setExtract(tr, 100, true);
  }

  async function load() {
    root.hidden = false;
    spec.forEach((row, i) => {
      const src = row.src || "";
      const dead = !src || row.has_audio === false;
      tracks.push({
        id: row.id || String(i),
        src: src,
        wav: row.wav || "",
        freq_khz: row.freq_khz,
        place: row.place,
        site_label: row.site_label,
        label: row.label,
        spec: new Float32Array(TIME * FREQ),
        el: null,
        dead: dead,
        muted: dead,
        volume: dead ? 0 : 1,
        canvas: null,
        head: null,
        loadEl: null,
        specDb: false,
        waterfall: row.waterfall || (row.id ? "waterfall-" + row.id + ".png" : ""),
        storedWf: false,
      });
    });
    if (!tracks.length) {
      if (statusEl) statusEl.textContent = "Aucune voie sur ce trafic.";
      playBtn.disabled = true;
      return;
    }
    renderDesk();
    requestAnimationFrame(() => {
      tracks.forEach((tr) => {
        if (!tr.storedWf) bakeSpec(tr, false);
        drawTrack(tr);
      });
    });
    if (statusEl) statusEl.textContent = "Waterfall USB…";
    playBtn.disabled = true;
    await Promise.all(tracks.map((tr) => loadStoredWf(tr)));
    const painted = tracks.filter((tr) => tr.storedWf).length;
    tracks.forEach(attachAudio);
    void Promise.all(tracks.map((tr) => waitDuration(tr, 20000)));
    tracks.forEach((tr) => {
      if (tr.storedWf || tr.dead) return;
      fetchWav(tr).then((wav) => paintSpec(tr, wav));
    });
    const live = tracks.filter((tr) => !tr.dead).length;
    if (painted || live) {
      playBtn.disabled = live === 0;
      if (statusEl)
        statusEl.textContent =
          live +
          "/" +
          tracks.length +
          " voies audio · waterfall USB 0–2,7 kHz (début à gauche) · mute / volume.";
      updateHead();
      return;
    }
    if (statusEl) statusEl.textContent = "Aucune piste audio décodable (voies grisées).";
    playBtn.disabled = true;
  }

  function destroy() {
    playing = false;
    cancelAnimationFrame(raf);
    clearInterval(tickTimer);
    tracks.forEach((tr) => {
      if (tr.el) {
        tr.el.pause();
        tr.el.removeAttribute("src");
        tr.el.load();
      }
    });
    document.body.classList.remove("kiwi-mix-focus");
    if (onFocus) onFocus(null);
    if (loopBtn) loopBtn.hidden = true;
    if (loopLab) loopLab.hidden = true;
    if (loopHint) loopHint.hidden = true;
    if (unzoomBtn) unzoomBtn.hidden = true;
    playBtn.removeEventListener("click", playToggle);
    window.removeEventListener("keydown", onKey);
    window.removeEventListener("resize", onResize);
    if (ro) ro.disconnect();
    ac.abort();
    if (audioLoadEl) {
      audioLoadEl.hidden = true;
      audioLoadEl.textContent = "";
    }
    const bin = document.getElementById("mix-audio-bin");
    if (bin) bin.remove();
    if (deskEl) deskEl.innerHTML = "";
  }

  load();
  return { destroy: destroy };
  }

  function bootFromDom() {
    const root = document.getElementById("mix");
    const dataEl = document.getElementById("ggr-mix-data");
    if (!root || !dataEl || root.getAttribute("data-ggr-mix") === "host") return;
    mount({ root: root });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootFromDom);
  } else {
    bootFromDom();
  }

  return { mount: mount, unmount: unmount };
})();
