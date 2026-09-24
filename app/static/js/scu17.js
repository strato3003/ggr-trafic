/**
 * GgrScu17 — interface Yaesu SCU-17 (USB audio + CAT série) côté navigateur.
 *
 * Repli strict : aucune exception non gérée ; si matériel absent, API non supportée
 * ou permission refusée, l’appli continue sans radio.
 *
 * Audio sortie : HTMLMediaElement.setSinkId (écoute via SCU-17).
 * Audio entrée : getUserMedia (enregistrement ACK depuis le TRX / beam).
 * CAT : navigator.serial (baud 38400 par défaut, commandes Yaesu ASCII « FA »).
 */
window.GgrScu17 = (function () {
  const LABEL_RE = /scu[\s_-]?17|yaesu|ft-?\d|ftdx|cp210/i;
  const BAUD = 38400;
  const t = function (key, vars) {
    return window.GGR_t ? window.GGR_t(key, vars) : key;
  };

  /** @type {'unsupported'|'denied'|'absent'|'ready'|'connected'|'recording'} */
  let state = "absent";
  let serialPort = null;
  let serialWriter = null;
  let serialReader = null;
  let audioOutId = "";
  let audioInId = "";
  let mediaStream = null;
  let mediaRecorder = null;
  let recordChunks = [];
  const listeners = new Set();

  function caps() {
    const md = navigator.mediaDevices || null;
    return {
      secure: typeof window.isSecureContext === "boolean" ? window.isSecureContext : location.protocol === "https:",
      serial: !!(navigator.serial && typeof navigator.serial.requestPort === "function"),
      sinkId: typeof HTMLMediaElement !== "undefined" && "setSinkId" in HTMLMediaElement.prototype,
      enumerate: !!(md && typeof md.enumerateDevices === "function"),
      getUserMedia: !!(md && typeof md.getUserMedia === "function"),
      mediaRecorder: typeof MediaRecorder !== "undefined",
    };
  }

  function emit() {
    const snap = snapshot();
    listeners.forEach((fn) => {
      try {
        fn(snap);
      } catch {
        /* UI listener : ne jamais remonter */
      }
    });
    document.querySelectorAll("[data-scu17-status]").forEach((el) => {
      el.setAttribute("data-scu17-state", snap.state);
      el.classList.toggle("is-on", snap.state === "connected" || snap.state === "recording");
      el.classList.toggle("is-ready", snap.state === "ready");
      el.classList.toggle("is-off", snap.state === "absent" || snap.state === "unsupported" || snap.state === "denied");
      const lab = el.querySelector("[data-scu17-label]");
      if (lab) lab.textContent = statusLabel(snap.state);
    });
  }

  function statusLabel(s) {
    const map = {
      unsupported: t("scu17_unsupported"),
      denied: t("scu17_denied"),
      absent: t("scu17_absent"),
      ready: t("scu17_ready"),
      connected: t("scu17_connected"),
      recording: t("scu17_recording"),
    };
    return map[s] || s;
  }

  function setState(next) {
    if (state === next) {
      emit();
      return;
    }
    state = next;
    emit();
  }

  function snapshot() {
    const c = caps();
    return {
      state: state,
      caps: c,
      audioOutId: audioOutId || "",
      audioInId: audioInId || "",
      serialOpen: !!serialPort,
      canRecord: !!(c.getUserMedia && c.mediaRecorder && (audioInId || state === "connected" || state === "ready")),
    };
  }

  function looksLikeScu(label, group) {
    const s = String(label || "") + " " + String(group || "");
    return LABEL_RE.test(s);
  }

  async function listAudioDevices() {
    const c = caps();
    if (!c.enumerate) return { inputs: [], outputs: [] };
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      const inputs = [];
      const outputs = [];
      all.forEach((d) => {
        const row = {
          id: d.deviceId,
          label: d.label || d.deviceId || "",
          group: d.groupId || "",
          scu: looksLikeScu(d.label, d.groupId),
        };
        if (d.kind === "audioinput") inputs.push(row);
        if (d.kind === "audiooutput") outputs.push(row);
      });
      return { inputs: inputs, outputs: outputs };
    } catch {
      return { inputs: [], outputs: [] };
    }
  }

  async function refresh() {
    const c = caps();
    if (!c.secure || (!c.sinkId && !c.serial && !c.getUserMedia)) {
      setState("unsupported");
      return snapshot();
    }
    const { inputs, outputs } = await listAudioDevices();
    const scuIn = inputs.find((d) => d.scu);
    const scuOut = outputs.find((d) => d.scu);
    if (scuIn) audioInId = scuIn.id;
    if (scuOut) audioOutId = scuOut.id;
    if (serialPort || mediaStream) {
      setState(mediaRecorder && mediaRecorder.state === "recording" ? "recording" : "connected");
      return snapshot();
    }
    if (scuIn || scuOut) {
      setState("ready");
      return snapshot();
    }
    setState("absent");
    return snapshot();
  }

  /**
   * Demande les permissions micro (débloque les labels devices) sans lever.
   */
  async function unlockLabels() {
    const c = caps();
    if (!c.getUserMedia) return { ok: false, reason: "unsupported" };
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      stream.getTracks().forEach((tr) => tr.stop());
      await refresh();
      return { ok: true };
    } catch (err) {
      const name = err && err.name ? String(err.name) : "";
      if (name === "NotAllowedError" || name === "PermissionDeniedError") setState("denied");
      else await refresh();
      return { ok: false, reason: name || "denied" };
    }
  }

  async function applySinkTo(el, deviceId) {
    if (!el || typeof el.setSinkId !== "function") return { ok: false, reason: "unsupported" };
    const id = deviceId || audioOutId;
    if (!id) return { ok: false, reason: "absent" };
    try {
      await el.setSinkId(id);
      return { ok: true, deviceId: id };
    } catch (err) {
      return { ok: false, reason: (err && err.name) || "sink_failed" };
    }
  }

  /** Applique la sortie SCU-17 à tous les <audio> du mixeur (best-effort). */
  async function routeMixerOutput() {
    const c = caps();
    if (!c.sinkId || !audioOutId) return { ok: false, reason: audioOutId ? "unsupported" : "absent" };
    const nodes = document.querySelectorAll("audio, #mix-audio-bin audio");
    let n = 0;
    for (const el of nodes) {
      const r = await applySinkTo(el, audioOutId);
      if (r.ok) n += 1;
    }
    return { ok: n > 0, count: n };
  }

  async function openSerial(opts) {
    const c = caps();
    if (!c.serial) {
      setState(state === "ready" || state === "connected" ? state : "unsupported");
      return { ok: false, reason: "unsupported" };
    }
    try {
      const port =
        opts && opts.port
          ? opts.port
          : await navigator.serial.requestPort(opts && opts.filters ? { filters: opts.filters } : {});
      await port.open({ baudRate: (opts && opts.baudRate) || BAUD });
      serialPort = port;
      serialWriter = port.writable ? port.writable.getWriter() : null;
      serialReader = port.readable ? port.readable.getReader() : null;
      setState("connected");
      return { ok: true };
    } catch (err) {
      const name = err && err.name ? String(err.name) : "";
      if (name === "NotFoundError") {
        await refresh();
        return { ok: false, reason: "absent" };
      }
      if (name === "NotAllowedError") {
        setState("denied");
        return { ok: false, reason: "denied" };
      }
      await refresh();
      return { ok: false, reason: name || "serial_failed" };
    }
  }

  async function closeSerial() {
    try {
      if (serialReader) {
        try {
          await serialReader.cancel();
        } catch {
          /* ignore */
        }
        try {
          serialReader.releaseLock();
        } catch {
          /* ignore */
        }
      }
      if (serialWriter) {
        try {
          await serialWriter.close();
        } catch {
          /* ignore */
        }
        try {
          serialWriter.releaseLock();
        } catch {
          /* ignore */
        }
      }
      if (serialPort) {
        try {
          await serialPort.close();
        } catch {
          /* ignore */
        }
      }
    } finally {
      serialPort = null;
      serialWriter = null;
      serialReader = null;
      await refresh();
    }
    return { ok: true };
  }

  /** Commande CAT Yaesu ASCII FA (kHz → FA14.135000;) — best-effort. */
  async function setFreqKhz(khz) {
    if (!serialWriter) return { ok: false, reason: "not_connected" };
    const f = Number(khz);
    if (!Number.isFinite(f) || f < 100 || f > 56000) return { ok: false, reason: "bad_freq" };
    const hz = Math.round(f * 1000);
    const body = String(hz).padStart(8, "0").slice(-8);
    const cmd = "FA" + body + ";";
    try {
      await serialWriter.write(new TextEncoder().encode(cmd));
      return { ok: true, cmd: cmd };
    } catch {
      return { ok: false, reason: "write_failed" };
    }
  }

  async function startInput(deviceId) {
    const c = caps();
    if (!c.getUserMedia) return { ok: false, reason: "unsupported" };
    stopInput();
    const id = deviceId || audioInId;
    const constr = id
      ? { audio: { deviceId: { exact: id } }, video: false }
      : { audio: true, video: false };
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia(constr);
      if (id) audioInId = id;
      setState("connected");
      return { ok: true, stream: mediaStream };
    } catch (err) {
      const name = err && err.name ? String(err.name) : "";
      if (name === "NotAllowedError") setState("denied");
      else await refresh();
      return { ok: false, reason: name || "getUserMedia_failed" };
    }
  }

  function stopInput() {
    if (mediaRecorder && mediaRecorder.state === "recording") {
      try {
        mediaRecorder.stop();
      } catch {
        /* ignore */
      }
    }
    mediaRecorder = null;
    recordChunks = [];
    if (mediaStream) {
      mediaStream.getTracks().forEach((tr) => {
        try {
          tr.stop();
        } catch {
          /* ignore */
        }
      });
    }
    mediaStream = null;
  }

  async function startRecordAck(opts) {
    const c = caps();
    if (!c.mediaRecorder) return { ok: false, reason: "unsupported" };
    if (!mediaStream) {
      const opened = await startInput(opts && opts.deviceId);
      if (!opened.ok) return opened;
    }
    recordChunks = [];
    try {
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : MediaRecorder.isTypeSupported("audio/webm")
          ? "audio/webm"
          : "";
      mediaRecorder = mime ? new MediaRecorder(mediaStream, { mimeType: mime }) : new MediaRecorder(mediaStream);
      mediaRecorder.ondataavailable = (ev) => {
        if (ev.data && ev.data.size) recordChunks.push(ev.data);
      };
      mediaRecorder.start(250);
      setState("recording");
      return { ok: true };
    } catch (err) {
      return { ok: false, reason: (err && err.name) || "recorder_failed" };
    }
  }

  function stopRecordAck() {
    return new Promise((resolve) => {
      if (!mediaRecorder || mediaRecorder.state === "inactive") {
        resolve({ ok: false, reason: "not_recording", blob: null });
        return;
      }
      mediaRecorder.onstop = () => {
        const type = (mediaRecorder && mediaRecorder.mimeType) || "audio/webm";
        const blob = recordChunks.length ? new Blob(recordChunks, { type: type }) : null;
        mediaRecorder = null;
        recordChunks = [];
        setState(serialPort || mediaStream ? "connected" : "ready");
        resolve({ ok: !!blob, blob: blob, mime: type });
      };
      try {
        mediaRecorder.stop();
      } catch {
        resolve({ ok: false, reason: "stop_failed", blob: null });
      }
    });
  }

  /**
   * Envoie l’ACK local vers le trafic (opérateur authentifié).
   * @param {string} vacId
   * @param {Blob} blob
   */
  async function uploadLocalAck(vacId, blob) {
    if (!vacId || !blob || !blob.size) return { ok: false, reason: "empty" };
    try {
      const fd = new FormData();
      const ext = (blob.type || "").indexOf("webm") >= 0 ? "webm" : "bin";
      fd.append("file", blob, "ack-local." + ext);
      const headers = { Accept: "application/json" };
      try {
        const tok = localStorage.getItem("ggr-admin-token");
        if (tok) headers["X-Admin-Token"] = tok;
      } catch {
        /* ignore */
      }
      const res = await fetch("/api/trafic/" + encodeURIComponent(vacId) + "/local-ack", {
        method: "POST",
        body: fd,
        headers: headers,
        credentials: "same-origin",
      });
      if (!res.ok) {
        let detail = String(res.status);
        try {
          const j = await res.json();
          if (j && j.detail) detail = String(j.detail);
        } catch {
          /* ignore */
        }
        return { ok: false, reason: detail };
      }
      const body = await res.json().catch(() => ({}));
      return { ok: true, meta: body };
    } catch (err) {
      return { ok: false, reason: (err && err.message) || "network" };
    }
  }

  function onChange(fn) {
    if (typeof fn === "function") listeners.add(fn);
    return function off() {
      listeners.delete(fn);
    };
  }

  function bindUi(root) {
    const box = root || document;
    box.querySelectorAll("[data-scu17-connect]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          await unlockLabels();
          await openSerial();
          await routeMixerOutput();
          await startInput();
        } finally {
          btn.disabled = false;
          emit();
        }
      });
    });
    box.querySelectorAll("[data-scu17-disconnect]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        stopInput();
        await closeSerial();
        emit();
      });
    });
    emit();
  }

  // Observation des périphériques (branché / débranché)
  try {
    if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
      navigator.mediaDevices.addEventListener("devicechange", () => {
        refresh();
      });
    }
  } catch {
    /* ignore */
  }

  // Boot non bloquant
  try {
    const c = caps();
    if (!c.secure) setState("unsupported");
    else refresh();
  } catch {
    setState("unsupported");
  }

  return {
    caps: caps,
    snapshot: snapshot,
    statusLabel: statusLabel,
    refresh: refresh,
    unlockLabels: unlockLabels,
    openSerial: openSerial,
    closeSerial: closeSerial,
    setFreqKhz: setFreqKhz,
    applySinkTo: applySinkTo,
    routeMixerOutput: routeMixerOutput,
    startInput: startInput,
    stopInput: stopInput,
    startRecordAck: startRecordAck,
    stopRecordAck: stopRecordAck,
    uploadLocalAck: uploadLocalAck,
    onChange: onChange,
    bindUi: bindUi,
    listAudioDevices: listAudioDevices,
  };
})();
