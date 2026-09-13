(() => {
  const root = document.getElementById("mix");
  const dataEl = document.getElementById("ggr-mix-data");
  if (!root || !dataEl) return;

  let spec;
  try {
    spec = JSON.parse(dataEl.textContent || "[]");
  } catch {
    return;
  }
  if (!Array.isArray(spec) || !spec.length) return;

  const vacId = root.getAttribute("data-vid") || "";
  const media = (file) =>
    "/media/" + encodeURIComponent(vacId) + "/" + encodeURIComponent(file);

  const statusEl = document.getElementById("mix-status");
  const deskEl = document.getElementById("mix-strips");
  const playBtn = document.getElementById("mix-play");
  const timeEl = document.getElementById("mix-time");
  const seekEl = document.getElementById("mix-seek");
  if (!deskEl || !playBtn || !seekEl) return;

  const TIME = 420;
  const FREQ = 128;
  const FFT = 512;
  const WF_H = 288;

  const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const tracks = [];
  let duration = 0;
  let playing = false;
  let t0 = 0;
  let startedAt = 0;
  let sources = [];
  let dragging = false;
  let resumeAfterDrag = false;
  let raf = 0;

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");
  }

  function fmtTime(s) {
    s = Math.max(0, s || 0);
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return String(m).padStart(2, "0") + ":" + String(sec).padStart(2, "0");
  }

  function fftMag(src, offset, n) {
    const re = new Float32Array(n);
    const im = new Float32Array(n);
    const last = Math.max(n - 1, 1);
    for (let i = 0; i < n; i++) {
      const w = 0.5 * (1 - Math.cos((2 * Math.PI * i) / last));
      re[i] = (src[offset + i] || 0) * w;
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

  function spectrogram(channel, sampleRate) {
    const n = channel.length;
    const img = new Float32Array(TIME * FREQ);
    if (n < FFT) return img;
    const nyquist = sampleRate / 2;
    const fMax = Math.min(2700, nyquist);
    const binMax = Math.max(2, Math.floor((fMax / nyquist) * (FFT / 2)));
    for (let t = 0; t < TIME; t++) {
      const start = Math.min(n - FFT, Math.floor((t / TIME) * (n - FFT)));
      const mag = fftMag(channel, start, FFT);
      const row = t * FREQ;
      for (let f = 0; f < FREQ; f++) {
        const bin = 1 + Math.floor((f / FREQ) * (binMax - 1));
        img[row + f] = 20 * Math.log10(mag[bin] + 1e-9);
      }
    }
    const sorted = Array.from(img).sort((a, b) => a - b);
    const lo = sorted[Math.floor(sorted.length * 0.25)] || -80;
    const hi = Math.max(sorted[Math.floor(sorted.length * 0.99)] || -20, lo + 12);
    const span = Math.max(8, hi - lo);
    for (let i = 0; i < img.length; i++) {
      const u = Math.max(0, Math.min(1, (img[i] - lo) / span));
      img[i] = Math.pow(u, 0.72);
    }
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

  function nowT() {
    if (!playing) return t0;
    return Math.min(duration, t0 + (audioCtx.currentTime - startedAt));
  }

  function resizeCanvas(tr) {
    const canvas = tr.canvas;
    if (!canvas) return;
    const cssW = Math.max(96, canvas.clientWidth || 140);
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.floor(cssW * dpr);
    canvas.height = Math.floor(WF_H * dpr);
    canvas.style.height = WF_H + "px";
  }

  function drawTrack(tr) {
    const canvas = tr.canvas;
    if (!canvas) return;
    const ctx2d = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    if (!w || !h) return;
    const idata = ctx2d.createImageData(w, h);
    const px = idata.data;
    const rgb = [0, 0, 0];
    const img = tr.spec;
    const dim = tr.muted ? 0.22 : 1;
    for (let row = 0; row < h; row++) {
      const t = Math.min(TIME - 1, Math.floor((row / h) * TIME));
      const base = t * FREQ;
      for (let col = 0; col < w; col++) {
        const f = Math.min(FREQ - 1, Math.floor((col / w) * FREQ));
        kiwiColor(img[base + f] * dim, rgb);
        const off = (row * w + col) * 4;
        px[off] = rgb[0];
        px[off + 1] = rgb[1];
        px[off + 2] = rgb[2];
        px[off + 3] = 255;
      }
    }
    ctx2d.putImageData(idata, 0, 0);
  }

  function draw() {
    tracks.forEach(drawTrack);
    updateHead();
  }

  function updateHead() {
    if (!duration) return;
    const t = nowT();
    const pct = Math.max(0, Math.min(1, t / duration));
    tracks.forEach((tr) => {
      if (tr.head) tr.head.style.top = pct * 100 + "%";
    });
    if (!dragging) seekEl.value = String(t);
    if (timeEl) timeEl.textContent = fmtTime(t) + " / " + fmtTime(duration);
    seekEl.max = String(duration);
  }

  function stopSources() {
    sources.forEach((s) => {
      try {
        s.stop();
      } catch {
        /* déjà arrêté */
      }
    });
    sources = [];
  }

  function startSources(offset) {
    stopSources();
    const when = audioCtx.currentTime;
    tracks.forEach((tr) => {
      if (!tr.buffer) return;
      const maxOff = Math.max(0, tr.buffer.duration - 0.05);
      const off = Math.min(Math.max(0, offset), maxOff);
      if (off >= tr.buffer.duration) return;
      applyGain(tr);
      const src = audioCtx.createBufferSource();
      src.buffer = tr.buffer;
      src.connect(tr.gain);
      src.start(when, off);
      sources.push(src);
    });
    startedAt = when;
    t0 = offset;
    playing = true;
    playBtn.textContent = "Pause";
    playBtn.setAttribute("aria-pressed", "true");
    tick();
  }

  function pauseAt(t) {
    t0 = Math.max(0, Math.min(duration, t));
    playing = false;
    stopSources();
    playBtn.textContent = "Lecture";
    playBtn.setAttribute("aria-pressed", "false");
    cancelAnimationFrame(raf);
    updateHead();
  }

  function tick() {
    if (!playing) return;
    const t = nowT();
    updateHead();
    if (t >= duration - 0.03) {
      pauseAt(duration);
      return;
    }
    raf = requestAnimationFrame(tick);
  }

  function previewSeek(t) {
    t0 = Math.max(0, Math.min(duration, t));
    if (playing) {
      stopSources();
      playing = false;
      cancelAnimationFrame(raf);
    }
    updateHead();
  }

  function pointerTime(ev, canvas) {
    const rect = canvas.getBoundingClientRect();
    const y = Math.max(0, Math.min(1, (ev.clientY - rect.top) / rect.height));
    return y * duration;
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
    if (playing) pauseAt(nowT());
  });
  seekEl.addEventListener("input", () => {
    previewSeek(Number(seekEl.value) || 0);
  });
  seekEl.addEventListener("change", () => {
    dragging = false;
    if (resumeAfterDrag) startSources(t0);
    resumeAfterDrag = false;
  });

  function applyGain(tr) {
    const vol = tr.muted ? 0 : Number.isFinite(tr.volume) ? tr.volume : 1;
    tr.gain.gain.value = vol;
  }

  function renderDesk() {
    const stage = document.getElementById("mix-stage");
    if (stage) stage.hidden = true;
    deskEl.classList.add("mix__desk");
    deskEl.innerHTML = tracks
      .map((tr) => {
        const qrg = Number.isFinite(tr.freq_khz) ? Math.round(tr.freq_khz) + " kHz" : "";
        const where = tr.place || tr.site_label || tr.label || tr.id;
        return (
          '<div class="mix__ch" data-id="' +
          esc(tr.id) +
          '">' +
          '<div class="mix__wf-wrap">' +
          '<canvas class="mix__wf" aria-label="Waterfall USB ' +
          esc(qrg) +
          " " +
          esc(where) +
          '"></canvas>' +
          '<div class="mix__head"></div>' +
          "</div>" +
          '<p class="mix__axis">0 Hz · USB · 2,7 kHz</p>' +
          '<div class="mix__ch-ctrl">' +
          '<div class="mix__meta"><strong>' +
          esc(qrg) +
          "</strong><span>" +
          esc(where) +
          "</span></div>" +
          '<button type="button" class="mix__mute" data-act="mute" aria-pressed="false">Mute</button>' +
          '<label class="mix__fader"><span>Vol</span><input type="range" min="0" max="150" value="100" step="1" data-act="vol"></label>' +
          "</div></div>"
        );
      })
      .join("");
    deskEl.querySelectorAll(".mix__ch").forEach((el, i) => {
      const tr = tracks[i];
      tr.canvas = el.querySelector("canvas");
      tr.head = el.querySelector(".mix__head");
      bindCanvasSeek(tr);
      el.querySelector('[data-act="mute"]').addEventListener("click", () => {
        tr.muted = !tr.muted;
        const btn = el.querySelector('[data-act="mute"]');
        btn.setAttribute("aria-pressed", tr.muted ? "true" : "false");
        el.classList.toggle("is-mute", tr.muted);
        applyGain(tr);
        drawTrack(tr);
      });
      const vol = el.querySelector('[data-act="vol"]');
      vol.addEventListener("input", (ev) => {
        const v = Number(ev.target.value);
        tr.volume = Number.isFinite(v) ? v / 100 : 1;
        applyGain(tr);
      });
      applyGain(tr);
    });
  }

  function playToggle() {
    const go = () => {
      if (playing) pauseAt(nowT());
      else startSources(duration - t0 < 0.08 ? 0 : t0);
    };
    if (audioCtx.state === "suspended") {
      audioCtx.resume().then(go);
      return;
    }
    go();
  }

  playBtn.addEventListener("click", playToggle);

  window.addEventListener("keydown", (ev) => {
    if (ev.code !== "Space") return;
    if (ev.target && /input|textarea|select|button/i.test(ev.target.tagName)) return;
    ev.preventDefault();
    playBtn.click();
  });

  window.addEventListener("resize", () => {
    tracks.forEach(resizeCanvas);
    draw();
  });

  async function load() {
    root.hidden = false;
    statusEl.textContent = "Chargement des pistes audio…";
    const master = audioCtx.destination;
    for (let i = 0; i < spec.length; i++) {
      const row = spec[i];
      statusEl.textContent = "Piste " + (i + 1) + " / " + spec.length + "…";
      const gain = audioCtx.createGain();
      gain.connect(master);
      const tr = {
        id: row.id || String(i),
        freq_khz: row.freq_khz,
        place: row.place,
        site_label: row.site_label,
        label: row.label,
        buffer: null,
        spec: new Float32Array(TIME * FREQ),
        gain,
        muted: false,
        volume: 1,
        canvas: null,
        head: null,
      };
      try {
        const res = await fetch(media(row.src));
        if (!res.ok) throw new Error("HTTP " + res.status);
        const raw = await res.arrayBuffer();
        const buf = await audioCtx.decodeAudioData(raw.slice(0));
        tr.buffer = buf;
        duration = Math.max(duration, buf.duration);
        await new Promise((r) => setTimeout(r, 0));
        tr.spec = spectrogram(buf.getChannelData(0), buf.sampleRate);
      } catch (err) {
        tr.place = (tr.place || tr.id) + " (échec chargement)";
        console.warn("Mixage piste", row.id, err);
      }
      tracks.push(tr);
    }
    if (!duration) {
      statusEl.textContent = "Aucune piste audio décodable.";
      playBtn.disabled = true;
      return;
    }
    seekEl.min = "0";
    seekEl.max = String(duration);
    seekEl.step = "0.05";
    seekEl.value = "0";
    renderDesk();
    tracks.forEach(resizeCanvas);
    draw();
    statusEl.textContent =
      tracks.length +
      " voies · waterfall USB 0–2,7 kHz (temps vers le bas) · mute / volume par Kiwi.";
    updateHead();
  }

  load();
})();
