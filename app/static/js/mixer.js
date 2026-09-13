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
  const canvas = document.getElementById("mix-wf");
  const headEl = document.getElementById("mix-head");
  const stripsEl = document.getElementById("mix-strips");
  const playBtn = document.getElementById("mix-play");
  const timeEl = document.getElementById("mix-time");
  const seekEl = document.getElementById("mix-seek");
  const stageEl = document.getElementById("mix-stage");
  if (!canvas || !stripsEl || !playBtn || !seekEl) return;

  const COLS = 960;
  const ROWS = 56;
  const FFT = 512;
  const BAND_H = 72;
  const TINTS = ["#3dba7a", "#c9a227", "#5ec8ff", "#d45c3a", "#b07cff", "#7ad0b0"];

  const ctx2d = canvas.getContext("2d");
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
    const img = new Float32Array(COLS * ROWS);
    if (n < FFT) return img;
    const nyquist = sampleRate / 2;
    const fMax = Math.min(2700, nyquist);
    const binMax = Math.max(2, Math.floor((fMax / nyquist) * (FFT / 2)));
    for (let x = 0; x < COLS; x++) {
      const start = Math.min(n - FFT, Math.floor((x / COLS) * (n - FFT)));
      const mag = fftMag(channel, start, FFT);
      for (let y = 0; y < ROWS; y++) {
        const bin = 1 + Math.floor((y / ROWS) * (binMax - 1));
        const db = 20 * Math.log10(mag[bin] + 1e-9);
        img[x + (ROWS - 1 - y) * COLS] = db;
      }
    }
    const sorted = Array.from(img).sort((a, b) => a - b);
    const p25 = sorted[Math.floor(sorted.length * 0.25)] || -80;
    const p99 = sorted[Math.floor(sorted.length * 0.99)] || -20;
    const lo = p25;
    const hi = Math.max(p99, lo + 12);
    const span = Math.max(8, hi - lo);
    for (let i = 0; i < img.length; i++) {
      const u = Math.max(0, Math.min(1, (img[i] - lo) / span));
      img[i] = Math.pow(u, 0.72);
    }
    return img;
  }

  function pixelColor(t, tint, out) {
    const u = Math.max(0, Math.min(1, t));
    let r;
    let g;
    let b;
    if (u < 0.25) {
      const k = u / 0.25;
      r = 4 + 8 * k;
      g = 10 + 30 * k;
      b = 18 + 70 * k;
    } else if (u < 0.55) {
      const k = (u - 0.25) / 0.3;
      r = 12 + 20 * k;
      g = 40 + 150 * k;
      b = 88 + 80 * k;
    } else if (u < 0.8) {
      const k = (u - 0.55) / 0.25;
      r = 32 + 170 * k;
      g = 190 - 20 * k;
      b = 168 - 120 * k;
    } else {
      const k = (u - 0.8) / 0.2;
      r = 202 + 50 * k;
      g = 170 + 70 * k;
      b = 48 + 180 * k;
    }
    out[0] = r * 0.62 + tint[0] * 0.38;
    out[1] = g * 0.62 + tint[1] * 0.38;
    out[2] = b * 0.62 + tint[2] * 0.38;
  }

  function tintRgb(hex) {
    return [
      parseInt(hex.slice(1, 3), 16),
      parseInt(hex.slice(3, 5), 16),
      parseInt(hex.slice(5, 7), 16),
    ];
  }

  function nowT() {
    if (!playing) return t0;
    return Math.min(duration, t0 + (audioCtx.currentTime - startedAt));
  }

  function resizeCanvas() {
    const n = Math.max(tracks.length, 1);
    const cssW = Math.max(320, (stageEl && stageEl.clientWidth) || canvas.clientWidth || 640);
    const cssH = n * BAND_H;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.floor(cssW * dpr);
    canvas.height = Math.floor(cssH * dpr);
    canvas.style.width = cssW + "px";
    canvas.style.height = cssH + "px";
  }

  function draw() {
    const n = tracks.length;
    if (!n) return;
    const w = canvas.width;
    const h = canvas.height;
    if (!w || !h) return;
    const idata = ctx2d.createImageData(w, h);
    const px = idata.data;
    const rgb = [0, 0, 0];
    const bandPx = Math.floor(h / n);
    for (let i = 0; i < n; i++) {
      const tr = tracks[i];
      const img = tr.spec;
      const tint = tintRgb(TINTS[i % TINTS.length]);
      const dim = tr.muted ? 0.28 : 1;
      const y0 = i * bandPx;
      for (let col = 0; col < w; col++) {
        const x = Math.min(COLS - 1, Math.floor((col / w) * COLS));
        for (let row = 0; row < bandPx; row++) {
          const y = Math.min(ROWS - 1, Math.floor((row / bandPx) * ROWS));
          pixelColor(img[x + y * COLS] * dim, tint, rgb);
          const off = ((y0 + row) * w + col) * 4;
          px[off] = rgb[0];
          px[off + 1] = rgb[1];
          px[off + 2] = rgb[2];
          px[off + 3] = 255;
        }
      }
    }
    ctx2d.putImageData(idata, 0, 0);
    updateHead();
  }

  function updateHead() {
    if (!duration) return;
    const t = nowT();
    const pct = Math.max(0, Math.min(1, t / duration));
    if (headEl) headEl.style.left = pct * 100 + "%";
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
      if (offset >= tr.buffer.duration) return;
      const src = audioCtx.createBufferSource();
      src.buffer = tr.buffer;
      src.connect(tr.gain);
      src.start(when, offset);
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

  canvas.addEventListener("pointerdown", (ev) => {
    if (!duration) return;
    resumeAfterDrag = playing;
    dragging = true;
    canvas.setPointerCapture(ev.pointerId);
    previewSeek(pointerTime(ev));
  });
  canvas.addEventListener("pointermove", (ev) => {
    if (!dragging) return;
    previewSeek(pointerTime(ev));
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
    tr.gain.gain.value = tr.muted ? 0 : tr.volume;
  }

  function renderStrips() {
    stripsEl.innerHTML = tracks
      .map((tr, i) => {
        const qrg = Number.isFinite(tr.freq_khz) ? Math.round(tr.freq_khz) + " kHz" : "";
        const where = tr.place || tr.site_label || tr.label || tr.id;
        return (
          '<div class="mix__strip" data-id="' +
          tr.id +
          '" style="--tint:' +
          TINTS[i % TINTS.length] +
          '">' +
          '<button type="button" class="mix__mute" data-act="mute" aria-pressed="false">Muet</button>' +
          '<div class="mix__meta"><strong>' +
          esc(qrg) +
          "</strong><span>" +
          esc(where) +
          "</span></div>" +
          '<label class="mix__vol">Volume <input type="range" min="0" max="150" value="100" data-act="vol"></label>' +
          "</div>"
        );
      })
      .join("");
    stripsEl.querySelectorAll(".mix__strip").forEach((el, i) => {
      const tr = tracks[i];
      el.querySelector('[data-act="mute"]').addEventListener("click", () => {
        tr.muted = !tr.muted;
        const btn = el.querySelector('[data-act="mute"]');
        btn.textContent = "Muet";
        btn.setAttribute("aria-pressed", tr.muted ? "true" : "false");
        el.classList.toggle("is-mute", tr.muted);
        applyGain(tr);
        draw();
      });
      el.querySelector('[data-act="vol"]').addEventListener("input", (ev) => {
        tr.volume = Number(ev.target.value) / 100;
        applyGain(tr);
      });
    });
  }

  function pointerTime(ev) {
    const rect = canvas.getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
    return x * duration;
  }

  playBtn.addEventListener("click", async () => {
    await audioCtx.resume();
    if (playing) pauseAt(nowT());
    else startSources(t0 >= duration ? 0 : t0);
  });

  window.addEventListener("keydown", (ev) => {
    if (ev.code !== "Space") return;
    if (ev.target && /input|textarea|select|button/i.test(ev.target.tagName)) return;
    ev.preventDefault();
    playBtn.click();
  });

  window.addEventListener("resize", () => {
    resizeCanvas();
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
        spec: new Float32Array(COLS * ROWS),
        gain,
        muted: false,
        volume: 1,
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
    renderStrips();
    resizeCanvas();
    draw();
    statusEl.textContent =
      tracks.length +
      " voix · spectrogramme USB 0–2,7 kHz · mute pour isoler une conversation.";
    updateHead();
  }

  load();
})();
