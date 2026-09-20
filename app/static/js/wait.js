(() => {
  /* Page d’attente : overlay + globe 3D décoratif si le globe interactif n’a pas démarré. */
  const WAIT_MS = 2400;
  let shown = false;
  let decor = null;

  function waitEl() {
    return document.getElementById("ggr-wait");
  }

  function forced() {
    return !!(
      document.body.getAttribute("data-ggr-force-wait") === "1" ||
      document.querySelector("[data-ggr-force-wait]")
    );
  }

  function hasCanvas() {
    return !!document.querySelector("#ggr-globe canvas");
  }

  function oceanUrl() {
    const c = document.createElement("canvas");
    c.width = 16;
    c.height = 8;
    const ctx = c.getContext("2d");
    if (ctx) {
      ctx.fillStyle = "#0a3558";
      ctx.fillRect(0, 0, 16, 8);
    }
    return c.toDataURL("image/png");
  }

  function bootDecor() {
    const el = document.getElementById("ggr-globe");
    if (!el || typeof Globe !== "function" || hasCanvas() || decor) return;
    try {
      const g = Globe()(el)
        .backgroundColor("#02050a")
        .showAtmosphere(true)
        .atmosphereColor("#5a7a98")
        .atmosphereAltitude(0.08)
        .globeImageUrl(oceanUrl());
      const ctrl = g.controls();
      ctrl.autoRotate = true;
      ctrl.autoRotateSpeed = 0.35;
      ctrl.enableZoom = false;
      g.pointOfView({ lat: 8, lng: -20, altitude: 2.35 }, 0);
      const size = () => {
        g.width(el.clientWidth);
        g.height(el.clientHeight);
      };
      size();
      window.addEventListener("resize", size);
      decor = g;
    } catch {
      /* globe.gl décoratif */
    }
  }

  function show(opts) {
    shown = true;
    document.body.classList.add("is-unavailable");
    const w = waitEl();
    if (w) w.hidden = false;
    window.dispatchEvent(new CustomEvent("ggr-wait", { detail: { on: true } }));
    if (opts && opts.decor === false) return;
    window.setTimeout(bootDecor, 0);
  }

  function hide() {
    shown = false;
    document.body.classList.remove("is-unavailable");
    const w = waitEl();
    if (w) w.hidden = true;
    window.dispatchEvent(new CustomEvent("ggr-wait", { detail: { on: false } }));
  }

  function ok() {
    if (forced()) return;
    hide();
  }

  function force(on) {
    const app = document.querySelector(".globe-app");
    if (on) {
      document.body.setAttribute("data-ggr-force-wait", "1");
      if (app) app.setAttribute("data-ggr-force-wait", "1");
      show({ decor: false });
      if (!hasCanvas()) window.setTimeout(bootDecor, 400);
    } else {
      document.body.removeAttribute("data-ggr-force-wait");
      if (app) app.removeAttribute("data-ggr-force-wait");
      hide();
    }
  }

  window.GgrWait = { show, ok, force };

  const q = new URLSearchParams(location.search);
  const waitQ = q.get("wait");
  if (waitQ === "1" || waitQ === "true" || waitQ === "oui") {
    force(true);
  } else if (document.body.classList.contains("is-unavailable") || forced()) {
    show({ decor: false });
  }

  window.addEventListener("error", (ev) => {
    const src = String((ev && ev.filename) || "");
    if (src.indexOf("/static/js/globe.js") >= 0) show();
  });

  window.setTimeout(() => {
    if (shown || forced()) {
      if (!hasCanvas()) bootDecor();
      return;
    }
    if (document.body.classList.contains("is-map-3d") && !hasCanvas()) show();
  }, WAIT_MS);
})();
