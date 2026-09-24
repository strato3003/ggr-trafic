window.GgrMixer = (function () {
  let handle = null;
  const t = function (key, vars) {
    return window.GGR_t ? window.GGR_t(key, vars) : key;
  };

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
  spec = spec.slice(0, 12);

  const vacId = opts.vacId || root.getAttribute("data-vid") || "";
  const wantFocus = opts.focus != null ? String(opts.focus) : "";
  const wantZoom = !!(opts.zoom || wantFocus);
  const wantTRaw =
    opts.t != null && String(opts.t) !== ""
      ? String(opts.t)
      : new URLSearchParams(location.search).get("t") ||
        new URLSearchParams(location.search).get("at") ||
        "";
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
  const audioLoadTxt = document.getElementById("mix-audio-load-txt") || audioLoadEl;
  const audioLoadFill = document.getElementById("mix-audio-load-fill");
  let audioWarming = false;
  let audioLoadPoll = 0;
  const seekEl = document.getElementById("mix-seek");
  const loopBtn = document.getElementById("mix-loop");
  const loopLab = document.getElementById("mix-loop-lab");
  const loopHint = document.getElementById("mix-loop-hint");
  const unzoomBtn = document.getElementById("mix-unzoom");
  const copyBtn = document.getElementById("mix-copy-link");
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
  let seekGate = false;
  let playGen = 0;
  let trLastTickT = null;
  const ac = new AbortController();
  const sig = { signal: ac.signal };
  const ICO_EXPAND =
    '<svg class="mix__ico" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>';
  const ICO_COLLAPSE =
    '<svg class="mix__ico" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z"/></svg>';
  const ICO_LOOP =
    '<svg class="mix__ico" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/></svg>';
  const ICO_LINK =
    '<svg class="mix__ico" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M3.9 12a5 5 0 0 1 5-5h4v2h-4a3 3 0 1 0 0 6h4v2h-4a5 5 0 0 1-5-5zm7-1h6v2h-6v-2zm4.1-4h4a5 5 0 0 1 0 10h-4v-2h4a3 3 0 1 0 0-6h-4V7z"/></svg>';
  if (loopBtn && !loopBtn.querySelector("svg")) loopBtn.insertAdjacentHTML("afterbegin", ICO_LOOP);
  if (unzoomBtn && !unzoomBtn.querySelector("svg")) unzoomBtn.insertAdjacentHTML("afterbegin", ICO_COLLAPSE);
  if (copyBtn && !copyBtn.querySelector("svg")) copyBtn.insertAdjacentHTML("afterbegin", ICO_LINK);

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");
  }

  function parseDeepTime(raw) {
    const s = String(raw || "").trim();
    if (!s) return NaN;
    if (/^\d+(\.\d+)?$/.test(s)) return Number(s);
    const m = s.match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?$/);
    if (!m || !Number.isFinite(startMs)) return NaN;
    const d = new Date(startMs);
    d.setUTCHours(Number(m[1]), Number(m[2]), m[3] != null ? Number(m[3]) : 0, 0);
    return (d.getTime() - startMs) / 1000;
  }

  function shareUrl() {
    if (!vacId) return location.href;
    const u = new URL("/trafic/" + encodeURIComponent(vacId), location.origin);
    if (focusId) {
      u.searchParams.set("zoom", "1");
      u.searchParams.set("ch", String(focusId));
    }
    const sec = Math.floor(nowT());
    if (sec > 0) u.searchParams.set("t", String(sec));
    return u.toString();
  }

  function flashStatus(msg) {
    if (!statusEl || !msg) return;
    const prev = statusEl.textContent;
    statusEl.textContent = msg;
    window.setTimeout(() => {
      if (statusEl && statusEl.textContent === msg) statusEl.textContent = prev || "";
    }, 2200);
  }

  async function copyShareLink() {
    const url = shareUrl();
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(url);
      } else {
        const ta = document.createElement("textarea");
        ta.value = url;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
      }
      flashStatus(t("link_copied"));
      if (copyBtn) copyBtn.title = t("link_copied");
    } catch {
      flashStatus(url);
    }
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

  function clockTrack() {
    if (focusId) {
      const hit = tracks.find((tr) => tr.id === focusId && tr.el && !tr.dead);
      if (hit) return hit;
    }
    return tracks.find((tr) => tr.el && !tr.dead) || null;
  }

  function nowT() {
    if (seekGate) return t0;
    if (playing) {
      const tr = clockTrack();
      const el = tr && tr.el;
      if (el && Number.isFinite(el.currentTime)) {
        const t = el.currentTime;
        return duration ? Math.min(duration, Math.max(0, t)) : Math.max(0, t);
      }
      return t0;
    }
    return t0;
  }

  function loopBounds() {
    if (!loopOk()) return null;
    const a = Math.min(loopA, loopB);
    const b = Math.max(loopA, loopB);
    return { a: a, b: b };
  }

  function clampLoopTime(t) {
    let x = Math.max(0, Number.isFinite(t) ? t : 0);
    // Ne pas tronquer sur timeSpan()=1 tant que duration est encore inconnue
    // (sinon un deep-link ?t=900 se retrouve à 1 s).
    if (duration > 0) x = Math.min(duration, x);
    const lp = loopOn ? loopBounds() : null;
    if (!lp) return x;
    if (x < lp.a) return lp.a;
    if (x >= lp.b - 0.04) return lp.a;
    return x;
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
    drawWave(tr);
  }

  function computePeaks(samples, cols) {
    const n = samples.length;
    const mins = new Float32Array(cols);
    const maxs = new Float32Array(cols);
    if (n < 2) return { mins: mins, maxs: maxs };
    const step = n / cols;
    for (let i = 0; i < cols; i++) {
      const a = Math.floor(i * step);
      const b = Math.min(n, Math.floor((i + 1) * step) + 1);
      let lo = 1;
      let hi = -1;
      for (let j = a; j < b; j++) {
        const v = samples[j] / 32768;
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
      mins[i] = lo;
      maxs[i] = hi;
    }
    return { mins: mins, maxs: maxs };
  }

  function drawWave(tr) {
    const canvas = tr.wave;
    if (!canvas || focusId !== tr.id) return;
    const wrap = canvas.parentElement;
    const cssW = Math.max(64, (wrap && wrap.clientWidth) || canvas.clientWidth || 140);
    const cssH = Math.max(28, canvas.clientHeight || 48);
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.floor(cssW * dpr);
    const h = Math.floor(cssH * dpr);
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    if (!w || !h) return;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#121a22";
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = "rgba(212, 230, 242, 0.18)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, h / 2);
    ctx.lineTo(w, h / 2);
    ctx.stroke();
    const peaks = tr.peaks;
    if (!peaks) return;
    const mid = h / 2;
    const amp = mid * 0.92;
    ctx.fillStyle = "#4aa8e0";
    const cols = peaks.mins.length;
    for (let x = 0; x < w; x++) {
      const i = Math.min(cols - 1, Math.floor((x / w) * cols));
      const y1 = mid - peaks.maxs[i] * amp;
      const y2 = mid - peaks.mins[i] * amp;
      ctx.fillRect(x, y1, 1, Math.max(1, y2 - y1));
    }
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

  /** Portion utile autour de currentTime (deep-link / seek milieu). */
  function bufferAroundPct(el) {
    if (!el || !el.buffered || !el.buffered.length) return 0;
    const cur = Number.isFinite(el.currentTime) ? el.currentTime : 0;
    const dur = Number.isFinite(el.duration) && el.duration > 0 ? el.duration : 0;
    const need = Math.min(12, dur ? Math.max(4, dur * 0.02) : 12);
    try {
      for (let i = 0; i < el.buffered.length; i++) {
        const a = el.buffered.start(i);
        const b = el.buffered.end(i);
        if (cur + 0.35 < a || cur - 0.35 > b) continue;
        const ahead = Math.max(0, b - cur);
        return Math.min(100, Math.round((ahead / need) * 100));
      }
    } catch {
      /* ignore */
    }
    return 0;
  }

  /**
   * Jauge « prêt à jouer » autour de la tête (pas le % fichier entier).
   * Monotone + creep réseau pour ne jamais rester figée.
   */
  function trackLoadPct(tr) {
    const el = tr && tr.el;
    if (!el) return Number(tr && tr.loadPct) || 0;
    const prev = Number(tr.loadPct) || 0;
    const around = bufferAroundPct(el);
    const whole = bufferedPct(el);
    const rs = el.readyState || 0;
    let next = prev;
    if (around > 0) next = Math.max(next, around);
    else if (whole > 0) next = Math.max(next, Math.min(85, whole));
    if (rs >= 4) next = Math.max(next, 95);
    else if (rs >= 3) next = Math.max(next, 70);
    else if (rs >= 2) next = Math.max(next, 45);
    else if (rs >= 1) next = Math.max(next, 12);
    // Creep tant que le navigateur charge (évite 1 % / 12 % figés).
    if (el.networkState === 2 && next < 92) next = Math.min(92, next + 1.2);
    else if (rs < 2 && next < 18) next = Math.min(18, next + 0.6);
    if (playheadCovered(tr)) next = Math.max(next, 100);
    return next;
  }

  function bufferedAt(el, t) {
    if (!el || !el.buffered || !el.buffered.length) return false;
    const cur = Number.isFinite(t) ? t : Number.isFinite(el.currentTime) ? el.currentTime : 0;
    try {
      for (let i = 0; i < el.buffered.length; i++) {
        const a = el.buffered.start(i);
        const b = el.buffered.end(i);
        if (a - 0.35 <= cur && cur + 1.2 <= b) return true;
      }
    } catch {
      /* ignore */
    }
    return false;
  }

  function playheadCovered(tr) {
    const el = tr && tr.el;
    if (!el) return false;
    if ((el.readyState || 0) >= 3 && bufferAroundPct(el) >= 50) return true;
    if ((el.readyState || 0) >= 4) return true;
    const cur = Number.isFinite(el.currentTime) ? el.currentTime : t0;
    return bufferedAt(el, cur);
  }

  function trackPlayable(tr) {
    const el = tr && tr.el;
    if (!el) return false;
    return (el.readyState || 0) >= 2 && (playheadCovered(tr) || bufferAroundPct(el) >= 25);
  }

  function setAudioLoadPoll(on) {
    if (on) {
      if (audioLoadPoll) return;
      audioLoadPoll = window.setInterval(updateAudioLoad, 100);
      return;
    }
    if (!audioLoadPoll) return;
    clearInterval(audioLoadPoll);
    audioLoadPoll = 0;
  }

  function hideGlobalAudioLoad() {
    if (!audioLoadEl) return;
    audioLoadEl.hidden = true;
    if (audioLoadTxt && audioLoadTxt !== audioLoadEl) audioLoadTxt.textContent = "";
    else audioLoadEl.textContent = "";
    if (audioLoadFill) audioLoadFill.style.width = "0%";
    setAudioLoadPoll(false);
  }

  function updateAudioLoad() {
    const live = tracks.filter((tr) => tr.el && !tr.dead && liveForPlay(tr));
    const canPlay = live.filter((tr) => trackPlayable(tr)).length;
    const seekGap = live.some((tr) => (tr.el.seeking || seekGate) && !playheadCovered(tr));
    const starving = playing && live.some((tr) => !trackPlayable(tr));
    const warmingGap = audioWarming && live.some((tr) => !trackPlayable(tr));
    const busy = !!(live.length && (audioWarming || seekGap || starving || warmingGap));

    if (audioWarming && !playing && live.length && live.every((tr) => trackPlayable(tr))) {
      audioWarming = false;
    }

    live.forEach((tr) => {
      // Extract waterfall : ne pas écraser sa jauge, mais garder le poll actif.
      if (tr.extractBusy) return;
      if (!busy || trackPlayable(tr)) {
        setTrackLoad(tr, 100, { done: true });
        return;
      }
      const pct = Math.max(1, Math.round(trackLoadPct(tr)));
      tr.loadPct = pct;
      const label = seekGap && !audioWarming ? t("audio_seek") : audioWarming && !playing ? t("audio_prep") : t("audio_load");
      setTrackLoad(tr, pct, { label: label });
    });

    if (!audioLoadEl) {
      setAudioLoadPoll(busy || live.some((tr) => tr.extractBusy));
      return;
    }
    if (!live.length || !busy) {
      if (!busy) audioWarming = false;
      hideGlobalAudioLoad();
      // Extraire encore ? poll pour les overlays piste.
      if (live.some((tr) => tr.extractBusy)) setAudioLoadPoll(true);
      return;
    }
    const pcts = live.map((tr) => {
      if (tr.extractBusy) return Number(tr.loadPct) || 0;
      return trackPlayable(tr) ? 100 : trackLoadPct(tr);
    });
    const avg = pcts.length ? Math.round(pcts.reduce((a, b) => a + b, 0) / pcts.length) : 0;
    let msg;
    if (seekGap && !avg) msg = t("audio_seek");
    else if (audioWarming && !playing) msg = t("audio_prep") + " " + avg + " % · " + canPlay + "/" + live.length;
    else if (avg) msg = t("audio_buf", { pct: avg, ready: canPlay, n: live.length });
    else msg = t("audio_load_n", { ready: canPlay, n: live.length });
    audioLoadEl.hidden = false;
    if (audioLoadTxt && audioLoadTxt !== audioLoadEl) audioLoadTxt.textContent = msg;
    else audioLoadEl.textContent = msg;
    if (audioLoadFill) {
      audioLoadFill.style.width = Math.max(2, avg || Math.round((canPlay / Math.max(1, live.length)) * 100)) + "%";
    }
    audioLoadEl.title = msg;
    setAudioLoadPoll(true);
  }

  function warmAudioAt(off) {
    const live = tracks.filter((tr) => tr.el && !tr.dead);
    if (!live.length) return;
    audioWarming = true;
    live.forEach((tr) => {
      tr.loadPct = 0;
      try {
        tr.el.preload = "auto";
        if (!tr.el.getAttribute("src") && tr.src) tr.el.src = media(tr.src);
        // Relance le buffer sans détruire le média déjà partiellement chargé.
        if ((tr.el.readyState || 0) < 2) tr.el.load();
      } catch {
        /* ignore */
      }
    });
    updateAudioLoad();
    Promise.all(live.map((tr) => waitSeek(tr, off))).then(() => {
      const tid = window.setInterval(() => {
        updateAudioLoad();
        const ok = live.every((tr) => trackPlayable(tr));
        if (ok || playing) {
          clearInterval(tid);
          if (!playing) audioWarming = false;
          updateAudioLoad();
        }
      }, 200);
      window.setTimeout(() => {
        clearInterval(tid);
        if (!playing) audioWarming = false;
        updateAudioLoad();
      }, 25000);
    });
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

  function liveForPlay(tr) {
    if (!tr || !tr.el || tr.dead) return false;
    if (focusId && tr.id !== focusId) return false;
    return true;
  }

  function waitSeek(tr, off) {
    return new Promise((resolve) => {
      if (!tr.el) {
        resolve();
        return;
      }
      const el = tr.el;
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        el.removeEventListener("seeked", onSeeked);
        el.removeEventListener("loadedmetadata", onMeta);
        resolve();
      };
      const onSeeked = () => {
        if (Math.abs((Number.isFinite(el.currentTime) ? el.currentTime : 0) - off) < 0.45) finish();
      };
      const doSeek = () => {
        if (Math.abs((Number.isFinite(el.currentTime) ? el.currentTime : 0) - off) < 0.45) {
          finish();
          return;
        }
        el.addEventListener("seeked", onSeeked);
        seekElTo(tr, off);
        window.setTimeout(finish, 800);
      };
      const onMeta = () => doSeek();
      if (el.readyState >= 1 && Number.isFinite(el.duration)) {
        doSeek();
        return;
      }
      el.addEventListener("loadedmetadata", onMeta);
      window.setTimeout(finish, 4000);
    });
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

  function startSources(offset) {
    const gen = ++playGen;
    const off = clampLoopTime(Math.max(0, offset || 0));
    t0 = off;
    playing = true;
    seekGate = true;
    noteReplayPlay();
    const all = tracks.filter((tr) => tr.el && !tr.dead);
    const needWarm = all.some((tr) => !trackPlayable(tr));
    audioWarming = needWarm;
    all.forEach((tr) => {
      try {
        tr.el.preload = "auto";
        if (!tr.el.getAttribute("src") && tr.src) tr.el.src = media(tr.src);
      } catch {
        /* ignore */
      }
      // Débloquer l’autoplay sans laisser les pistes définitivement mutées.
      applyGain(tr);
      const silentWarm = !!(focusId && tr.id !== focusId) || !!tr.muted;
      tr.el.muted = true;
      const p = tr.el.play();
      if (p && typeof p.then === "function") {
        p.then(() => {
          if (gen !== playGen) return;
          if (!silentWarm) applyGain(tr);
        }).catch(() => {
          try {
            if ((tr.el.readyState || 0) < 1) tr.el.load();
          } catch {
            /* ignore */
          }
        });
      }
    });
    updateAudioLoad();
    setPlayUi(true);
    cancelAnimationFrame(raf);
    clearInterval(tickTimer);
    updateHead();
    const goPlay = () => {
      if (gen !== playGen || !playing) return;
      seekGate = false;
      t0 = off;
      all.forEach((tr) => {
        if (focusId && tr.id !== focusId) {
          try {
            tr.el.pause();
          } catch {
            /* ignore */
          }
          applyGain(tr);
          return;
        }
        seekElTo(tr, off);
        applyGain(tr);
        const kick = () => {
          if (gen !== playGen || !playing) return;
          applyGain(tr);
          tr.el.play().catch(() => {});
        };
        tr.el.play().then(kick).catch(() => {
          tr.el.addEventListener("canplay", kick, { once: true });
          window.setTimeout(kick, 400);
        });
      });
      audioWarming = false;
      updateAudioLoad();
      tickTimer = setInterval(tick, 50);
      raf = requestAnimationFrame(function loop() {
        tick();
        if (playing) raf = requestAnimationFrame(loop);
      });
    };
    Promise.all(all.map((tr) => waitSeek(tr, off))).then(() => {
      if (gen !== playGen || !playing) return;
      const clock = clockTrack();
      const cur = clock && clock.el && Number.isFinite(clock.el.currentTime) ? clock.el.currentTime : off;
      const lp = loopOn ? loopBounds() : null;
      if (lp && clock && clock.el && clock.el.readyState >= 1 && (cur < lp.a - 1 || cur >= lp.b - 0.02)) {
        all.forEach((tr) => seekElTo(tr, off));
        window.setTimeout(goPlay, 80);
        return;
      }
      goPlay();
    });
    window.setTimeout(() => {
      if (gen === playGen && seekGate) goPlay();
    }, needWarm ? 1800 : 350);
  }

  function setPlayUi(on) {
    playBtn.setAttribute("aria-pressed", on ? "true" : "false");
    playBtn.setAttribute("aria-label", on ? t("pause") : t("play"));
    playBtn.title = on ? t("pause") : t("play");
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

  function pauseAt(t) {
    playGen += 1;
    seekGate = false;
    audioWarming = false;
    t0 = Math.max(0, Math.min(duration || t, t));
    playing = false;
    tracks.forEach((tr) => {
      if (tr.el) tr.el.pause();
      if (!tr.extractBusy) setTrackLoad(tr, 100, { done: true });
    });
    setPlayUi(false);
    cancelAnimationFrame(raf);
    clearInterval(tickTimer);
    updateHead();
    updateAudioLoad();
  }

  function tick() {
    if (!playing) return;
    if (seekGate) {
      updateHead();
      updateAudioLoad();
      return;
    }
    const clock = clockTrack();
    if (clock && clock.el && clock.el.readyState < 2) {
      // Buffering : la tête reste, la jauge avance (creep).
      updateHead();
      updateAudioLoad();
      return;
    }
    const t = nowT();
    // Si currentTime ne bouge plus alors qu’on joue → famine buffer.
    if (clock && clock.el && !clock.el.paused && Number.isFinite(clock.el.currentTime)) {
      const cur = clock.el.currentTime;
      if (trLastTickT == null) trLastTickT = cur;
      if (Math.abs(cur - trLastTickT) < 0.001 && (clock.el.readyState || 0) < 3) {
        updateAudioLoad();
      }
      trLastTickT = cur;
    }
    updateHead();
    updateAudioLoad();
    const lp = loopOn ? loopBounds() : null;
    if (lp) {
      if (t >= lp.b - 0.02 || t < lp.a - 1) {
        restartLoop(lp.a);
        return;
      }
    }
    if (duration && t >= duration - 0.03) pauseAt(duration);
  }

  function restartLoop(a) {
    tracks.forEach((tr) => {
      if (!tr.el) return;
      try {
        tr.el.pause();
      } catch {
        /* ignore */
      }
    });
    startSources(a);
  }

  function previewSeek(t) {
    const raw = Math.max(0, Number.isFinite(t) ? t : 0);
    t0 = clampLoopTime(duration > 0 ? Math.min(duration, raw) : raw);
    if (duration > 0) seekEl.max = String(duration);
    else if (t0 > (Number(seekEl.max) || 0)) seekEl.max = String(Math.max(t0, 1));
    seekEl.value = String(t0);
    if (playing) {
      playGen += 1;
      playing = false;
      seekGate = false;
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
    const span = timeSpan();
    if (!loopOk()) {
      loopA = 0;
      loopB = span;
      return loopOk();
    }
    if (duration > 2 && loopB <= 1.05 && loopA >= 0 && loopA < 1.05) {
      if (loopB - loopA < 0.98) {
        loopA *= duration;
        loopB *= duration;
      } else {
        loopA = 0;
        loopB = duration;
      }
    }
    loopA = Math.max(0, Math.min(loopA, span));
    loopB = Math.max(loopA + 0.12, Math.min(loopB, span));
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
      const aPct = (a / span) * 100;
      const bPct = (b / span) * 100;
      tr.loopEl.style.left = "0";
      tr.loopEl.style.width = "100%";
      const dimL = tr.loopEl.querySelector(".mix__loop-dim--l");
      const dimR = tr.loopEl.querySelector(".mix__loop-dim--r");
      const sel = tr.loopEl.querySelector(".mix__loop-sel");
      if (dimL) {
        dimL.style.left = "0";
        dimL.style.width = aPct + "%";
      }
      if (sel) {
        sel.style.left = aPct + "%";
        sel.style.width = Math.max(0, bPct - aPct) + "%";
      }
      if (dimR) {
        dimR.style.left = bPct + "%";
        dimR.style.width = Math.max(0, 100 - bPct) + "%";
      }
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
      btn.setAttribute("aria-label", on ? t("menu_collapse_short") : t("fullscreen"));
      btn.setAttribute("title", on ? t("menu_collapse_short") : t("fullscreen"));
    });
  }

  function setFocus(id) {
    focusId = id || null;
    document.body.classList.toggle("kiwi-mix-focus", !!focusId);
    deskEl.querySelectorAll(".mix__ch").forEach((el) => {
      el.classList.toggle("is-focus", focusId && el.getAttribute("data-id") === focusId);
    });
    tracks.forEach((tr) => {
      const on = !!(focusId && tr.id === focusId);
      if (tr.wave) tr.wave.hidden = !on;
      if (tr.legend) tr.legend.hidden = !on;
    });
    if (!focusId) {
      loopOn = false;
    } else {
      const tr = tracks.find((row) => row.id === focusId);
      if (tr && !tr.dead) loadWave(tr);
    }
    updateLoopUi();
    if (playing) startSources(clampLoopTime(t0));
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
      const wrap = tr.loopEl.parentElement || tr.canvas;
      const t = pointerTime(ev, wrap);
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
      else if (playing) startSources(clampLoopTime(nowT()));
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

  function bindCanvasSeek(canvas, tr) {
    if (!canvas) return;
    canvas.title = t("seek_right_click");
    canvas.addEventListener("pointerdown", (ev) => {
      if (!duration) return;
      if (ev.button === 2) return;
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
    canvas.addEventListener("contextmenu", (ev) => {
      if (!duration) return;
      ev.preventDefault();
      const was = playing;
      if (was) pauseAt(nowT());
      previewSeek(pointerTime(ev, canvas));
      if (was) startSources(t0);
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
  seekEl.addEventListener(
    "contextmenu",
    (ev) => {
      if (!duration) return;
      ev.preventDefault();
      const was = playing;
      if (was) pauseAt(nowT());
      previewSeek(pointerTime(ev, seekEl));
      if (was) startSources(t0);
    },
    sig
  );
  if (copyBtn) {
    copyBtn.addEventListener("click", () => {
      void copyShareLink();
    }, sig);
  }

  function applyGain(tr) {
    const silent = !!(tr.muted || (focusId && tr.id !== focusId));
    const vol = silent ? 0 : Number.isFinite(tr.volume) ? tr.volume : 1;
    if (tr.el) {
      tr.el.muted = silent;
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
          '<label class="mix__fader"><span>' +
          t("vol") +
          '</span><input type="range" min="0" max="150" value="100" step="1" data-act="vol"></label>' +
          '<button type="button" class="mix__mute" data-act="mute" aria-pressed="false">' +
          t("mute") +
          "</button>" +
          '<button type="button" class="mix__zoom mix__ico-btn" data-act="zoom" aria-label="' +
          t("fullscreen") +
          '" title="' +
          t("fullscreen") +
          '">' +
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
          '<canvas class="mix__wave" hidden aria-label="Amplitude ' +
          esc(qrg) +
          '"></canvas>' +
          '<p class="mix__wf-legend" hidden>USB 0 Hz (bas) → 2,7 kHz (haut) · noir/bleu = bruit · cyan/vert = signal · jaune/blanc = fort</p>' +
          (tr.dead
            ? ""
            : '<div class="mix__wf-load" hidden>' +
              '<i class="mix__wf-load-fill" aria-hidden="true"></i>' +
              '<span class="mix__wf-load-txt">' +
              t("audio_load") +
              " · 0 %</span></div>") +
          '<div class="mix__loop" hidden>' +
          '<div class="mix__loop-dim mix__loop-dim--l"></div>' +
          '<div class="mix__loop-sel">' +
          '<button type="button" class="mix__loop-h mix__loop-h--a" aria-label="Borne A de la boucle"></button>' +
          '<button type="button" class="mix__loop-h mix__loop-h--b" aria-label="Borne B de la boucle"></button>' +
          "</div>" +
          '<div class="mix__loop-dim mix__loop-dim--r"></div>' +
          "</div>" +
          '<div class="mix__head"></div>' +
          "</div></div>"
        );
      })
      .join("");
    deskEl.querySelectorAll(".mix__ch").forEach((el, i) => {
      const tr = tracks[i];
      tr.canvas = el.querySelector("canvas.mix__wf");
      tr.wave = el.querySelector("canvas.mix__wave");
      tr.legend = el.querySelector(".mix__wf-legend");
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
      bindCanvasSeek(tr.canvas, tr);
      bindCanvasSeek(tr.wave, tr);
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
    else startSources(clampLoopTime(duration && duration - t0 < 0.08 ? 0 : t0));
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
          if (playing) startSources(clampLoopTime(nowT()));
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
    const was = duration;
    duration = Math.max(duration, sec);
    seekEl.min = "0";
    seekEl.max = String(duration);
    seekEl.step = "0.05";
    playBtn.disabled = false;
    if (loopOn) ensureLoopRange();
    if (deepTimePending && duration > was) applyDeepTime();
    updateHead();
    updateLoopUi();
  }

  function setTrackLoad(tr, pct, opts) {
    const el = tr && tr.loadEl;
    if (!el) return;
    const done = !!(opts && opts.done);
    if (done || (tr && tr.dead)) {
      el.hidden = true;
      tr.loadPct = 100;
      return;
    }
    const n = Math.max(0, Math.min(100, Math.round(Number(pct) || 0)));
    tr.loadPct = n;
    el.hidden = false;
    const fill = el.querySelector(".mix__wf-load-fill");
    const txt = el.querySelector(".mix__wf-load-txt");
    const label = (opts && opts.label) || t("audio_load");
    if (fill) fill.style.width = n + "%";
    if (txt) txt.textContent = label + " · " + n + " %";
    else el.textContent = label + " · " + n + " %";
  }

  function setExtract(tr, pct, done) {
    if (!tr) return;
    if (done) {
      tr.extractBusy = false;
      setTrackLoad(tr, 100, { done: true });
      // Réaffiche la jauge audio si le buffer n’est pas encore jouable.
      updateAudioLoad();
      return;
    }
    tr.extractBusy = true;
    setTrackLoad(tr, pct, { label: t("audio_extract") });
    setAudioLoadPoll(true);
  }

  function attachAudio(tr) {
    if (tr.dead || !tr.src) {
      if (!tr.storedWf) bakeSpec(tr, false);
      drawTrack(tr);
      return;
    }
    const url = media(tr.src);
    tr.el = new Audio(url);
    // auto : stream immédiat (son + jauge buffer), sans attendre un prefetch blob.
    tr.el.preload = "auto";
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
      updateAudioLoad();
    });
    tr.el.addEventListener("timeupdate", () => {
      if (!playing || seekGate || !loopOn || !liveForPlay(tr)) return;
      if (tr.el.readyState < 2) return;
      const lp = loopBounds();
      if (!lp) return;
      const cur = tr.el.currentTime;
      if (!Number.isFinite(cur)) return;
      if (cur >= lp.b - 0.02 || cur < lp.a - 1) restartLoop(lp.a);
    });
    tr.el.addEventListener("ended", () => {
      if (!playing) return;
      if (loopOn && loopOk()) {
        restartLoop(Math.min(loopA, loopB));
        return;
      }
      const clock = clockTrack();
      if (clock && clock.el === tr.el) pauseAt(duration || tr.el.currentTime || 0);
    });
    try {
      tr.el.load();
    } catch {
      /* ignore */
    }
  }

  function pcmName(src) {
    return String(src || "").replace(/\.(mp3|m4a|aac)$/i, ".wav");
  }

  async function fetchWav(tr, quiet) {
    if (tr.dead || !tr.src) return null;
    const pcm = tr.wav || pcmName(tr.src);
    if (!pcm) return null;
    if (!quiet) setExtract(tr, 0);
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
          if (!quiet) {
            if (total) setExtract(tr, (received / total) * 45);
            else setExtract(tr, Math.min(40, 5 + received / 350000));
          }
        }
        raw = await new Blob(chunks).arrayBuffer();
      } else {
        raw = await res.arrayBuffer();
      }
      if (!quiet) setExtract(tr, 45);
      return wavMono16(raw);
    } catch (err) {
      if (quiet) return null;
      tr.place = (tr.place || tr.id) + " " + t("wf_fail");
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

  async function loadWave(tr) {
    if (!tr || tr.dead || tr.peaks || tr.waveBusy) return;
    tr.waveBusy = true;
    try {
      let wav = await fetchWav(tr, true);
      if (!wav) wav = await fetchWav(tr, true);
      if (!wav) return;
      tr.peaks = computePeaks(wav.samples, 1024);
      if (focusId === tr.id) drawWave(tr);
    } finally {
      tr.waveBusy = false;
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
    // Pics type Audacity dès qu’on a le PCM (zoom + piste onde).
    if (!tr.peaks) tr.peaks = computePeaks(wav.samples, 1024);
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

  function resolveFocusId(want) {
    const raw = String(want || "").trim();
    if (!raw && !wantZoom) return null;
    if (raw) {
      const byId = tracks.find((tr) => tr.id === raw && !tr.dead);
      if (byId) return byId.id;
      const idx = Number(raw);
      if (Number.isInteger(idx) && idx >= 0 && idx < tracks.length && !tracks[idx].dead) {
        return tracks[idx].id;
      }
    }
    const live = tracks.find((tr) => !tr.dead && tr.src);
    return live ? live.id : null;
  }

  function applyDeepFocus() {
    if (!wantZoom && !wantFocus) return;
    const id = resolveFocusId(wantFocus);
    if (id) setFocus(id);
  }

  let deepTimePending = !!String(wantTRaw || "").trim();

  function applyDeepTime() {
    const sec = parseDeepTime(wantTRaw);
    if (!Number.isFinite(sec) || sec < 0) {
      deepTimePending = false;
      return;
    }
    const t = duration > 0 ? Math.min(duration, sec) : sec;
    previewSeek(t);
    if (duration > 0) {
      deepTimePending = false;
      warmAudioAt(t0);
    }
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
        extractBusy: false,
        loadPct: 0,
      });
    });
    if (!tracks.length) {
      if (statusEl) statusEl.textContent = t("no_tracks");
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
    if (statusEl) statusEl.textContent = t("wf_loading");
    playBtn.disabled = true;
    await Promise.all(tracks.map((tr) => loadStoredWf(tr)));
    const painted = tracks.filter((tr) => tr.storedWf).length;
    tracks.forEach(attachAudio);
    audioWarming = true;
    updateAudioLoad();
    void Promise.all(tracks.map((tr) => waitDuration(tr, deepTimePending ? 12000 : 20000))).then(() => {
      if (deepTimePending) applyDeepTime();
      else {
        audioWarming = false;
        updateAudioLoad();
      }
    });
    tracks.forEach((tr) => {
      if (tr.storedWf || tr.dead) return;
      fetchWav(tr).then((wav) => paintSpec(tr, wav));
    });
    const live = tracks.filter((tr) => !tr.dead).length;
    if (painted || live) {
      playBtn.disabled = live === 0;
      if (statusEl)
        statusEl.textContent = t("mix_ready", { live: live, n: tracks.length });
      updateHead();
      applyDeepFocus();
      if (!deepTimePending) applyDeepTime();
      return;
    }
    if (statusEl) statusEl.textContent = t("no_audio");
    playBtn.disabled = true;
    applyDeepFocus();
    if (!deepTimePending) applyDeepTime();
  }

  function destroy() {
    playing = false;
    audioWarming = false;
    setAudioLoadPoll(false);
    cancelAnimationFrame(raf);
    clearInterval(tickTimer);
    tracks.forEach((tr) => {
      if (tr.el) {
        tr.el.pause();
        tr.el.removeAttribute("src");
        try {
          tr.el.load();
        } catch {
          /* ignore */
        }
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
      hideGlobalAudioLoad();
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
    const q = new URLSearchParams(location.search);
    const zoom = q.get("zoom") === "1" || q.get("zoom") === "true" || q.has("ch");
    const focus = q.get("ch") || q.get("focus") || (zoom ? "0" : "");
    const deepT = q.get("t") || q.get("at") || "";
    mount({ root: root, zoom: zoom, focus: focus, t: deepT });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootFromDom);
  } else {
    bootFromDom();
  }

  return { mount: mount, unmount: unmount };
})();
