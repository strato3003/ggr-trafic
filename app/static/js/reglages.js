(() => {
  const form = document.getElementById("reglages-form");
  const banner = document.getElementById("settings-banner");
  const recordBtn = document.getElementById("record-now");
  const recordQrgBtn = document.getElementById("record-qrg");
  if (!form) return;

  const TOKEN_KEY = "ggr-admin-token";
  const tokenInput = form.elements.namedItem("admin_token");
  if (tokenInput && !tokenInput.value) {
    tokenInput.value = localStorage.getItem(TOKEN_KEY) || "";
  }

  const slot = (name) => form.querySelector(`.settings-msg[data-for="${name}"]`);

  const say = (name, text, ok) => {
    const el = slot(name);
    if (el) {
      el.hidden = false;
      el.textContent = text;
      el.classList.toggle("err", !ok);
      el.classList.toggle("ok", !!ok);
      el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
    if (banner) {
      banner.hidden = false;
      banner.textContent = text;
      banner.classList.toggle("err", !ok);
      banner.classList.toggle("ok", !!ok);
    }
  };

  const busy = (btn, on) => {
    if (!btn) return;
    btn.disabled = on;
  };

  const payload = () => {
    const skipperBoxes = [...form.querySelectorAll('input[name="buddy_skipper"]:checked')];
    const skipperText = form.elements.namedItem("buddy_skippers_text");
    const skippers = skipperBoxes.length
      ? skipperBoxes.map((el) => el.value)
      : skipperText
        ? String(skipperText.value || "")
            .split("\n")
            .map((s) => s.trim())
            .filter(Boolean)
        : [];
    const enabledEl = form.elements.namedItem("buddy_enabled");
    const tx_sites = [];
    for (let i = 0; i < 5; i++) {
      const labelEl = form.elements.namedItem("tx_label_" + i);
      const locEl = form.elements.namedItem("tx_loc_" + i);
      const latEl = form.elements.namedItem("tx_lat_" + i);
      const lonEl = form.elements.namedItem("tx_lon_" + i);
      tx_sites.push({
        label: labelEl ? String(labelEl.value || "").trim() : "",
        loc: locEl ? String(locEl.value || "").trim() : "",
        lat: latEl && String(latEl.value).trim() !== "" ? Number(latEl.value) : "",
        lon: lonEl && String(lonEl.value).trim() !== "" ? Number(lonEl.value) : "",
      });
    }
    return {
      tx_khz: Number(form.elements.namedItem("tx_khz").value),
      ack1_khz: Number(form.elements.namedItem("ack1_khz").value),
      ack2_khz: Number(form.elements.namedItem("ack2_khz").value),
      qrg_tolerance_khz: Number(form.elements.namedItem("qrg_tolerance_khz").value),
      lead_minutes: Number(form.elements.namedItem("lead_minutes").value),
      duration_minutes: Number(form.elements.namedItem("duration_minutes").value),
      buddy_enabled: !!(enabledEl && enabledEl.checked),
      buddy_main_khz: Number(form.elements.namedItem("buddy_main_khz").value),
      buddy_alt_khz: Number(form.elements.namedItem("buddy_alt_khz").value),
      buddy_time_utc: form.elements.namedItem("buddy_time_utc").value,
      buddy_lead: Number(form.elements.namedItem("buddy_lead").value),
      buddy_duration_minutes: Number(form.elements.namedItem("buddy_duration_minutes").value),
      buddy_kiwi_count: Number((form.elements.namedItem("buddy_kiwi_count") || { value: 4 }).value) || 4,
      buddy_skippers: skippers,
      tx_sites,
      display: {
        banner: !!(form.elements.namedItem("display_banner") && form.elements.namedItem("display_banner").checked),
        sdr_fleet: !!(form.elements.namedItem("display_sdr_fleet") && form.elements.namedItem("display_sdr_fleet").checked),
        sdr_potential: !!(form.elements.namedItem("display_sdr_potential") && form.elements.namedItem("display_sdr_potential").checked),
        skippers: !!(form.elements.namedItem("display_skippers") && form.elements.namedItem("display_skippers").checked),
        boats: !!(form.elements.namedItem("display_boats") && form.elements.namedItem("display_boats").checked),
        metarea: !!(form.elements.namedItem("display_metarea") && form.elements.namedItem("display_metarea").checked),
        subzones: !!(form.elements.namedItem("display_subzones") && form.elements.namedItem("display_subzones").checked),
      },
      unavailable: !!(form.elements.namedItem("unavailable") && form.elements.namedItem("unavailable").checked),
    };
  };

  const headers = () => {
    const token = (tokenInput && tokenInput.value) || "";
    if (token) localStorage.setItem(TOKEN_KEY, token);
    const h = { "Content-Type": "application/json" };
    if (token) h["X-Admin-Token"] = token;
    return h;
  };

  const _detail = (data) => {
    const d = data && data.detail;
    if (Array.isArray(d)) {
      return d.map((x) => x.msg || JSON.stringify(x)).join(" ");
    }
    return d;
  };

  const saveSettings = async () => {
    const res = await fetch("/api/settings", {
      method: "PUT",
      headers: headers(),
      body: JSON.stringify(payload()),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(_detail(data) || `Erreur ${res.status}`);
    }
    if (window.GgrWait) window.GgrWait.force(!!data.unavailable);
    return data;
  };

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = document.getElementById("save-qrg");
    busy(btn, true);
    say("save", "Sauvegarde en cours…", true);
    try {
      const data = await saveSettings();
      say(
        "save",
        `Fréquences mémorisées : ${data.tx_mhz} / ${data.ack1_mhz} / ${data.ack2_mhz} MHz · buddy ${data.buddy_main_khz} / ${data.buddy_alt_khz} kHz à ${data.buddy_time_utc} TU` +
          ` · ${(data.tx_sites || []).length} QTH d’émission.`,
        true
      );
    } catch (err) {
      say("save", String(err), false);
    } finally {
      busy(btn, false);
    }
  });

  if (recordQrgBtn) {
    recordQrgBtn.addEventListener("click", async () => {
      busy(recordQrgBtn, true);
      say("qrg", "Contact du serveur, démarrage du test…", true);
      const huntEl = form.elements.namedItem("test_hunt");
      try {
        const res = await fetch("/api/trafic/record", {
          method: "POST",
          headers: headers(),
          body: JSON.stringify({
            freq_khz: Number(form.elements.namedItem("test_freq").value),
            duration_minutes: Number(form.elements.namedItem("test_duration_minutes").value),
            hunt: !!(huntEl && huntEl.checked),
            qrg_tolerance_khz: Number(form.elements.namedItem("qrg_tolerance_khz").value),
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.status === 202) {
          say(
            "qrg",
            `Test radio lancé sur ${data.freq_mhz} MHz pendant ${data.duration_minutes} min` +
              `${data.hunt ? " (chasse USB ± QRM)" : ""}. Elle apparaîtra dans Trafic à la fin.`,
            true
          );
          return;
        }
        say("qrg", _detail(data) || `Erreur ${res.status}`, false);
      } catch (err) {
        say("qrg", String(err), false);
      } finally {
        busy(recordQrgBtn, false);
      }
    });
  }

  if (recordBtn) {
    recordBtn.addEventListener("click", async () => {
      busy(recordBtn, true);
      say("vac", "Sauvegarde des QRG puis démarrage du trafic…", true);
      try {
        await saveSettings();
        const res = await fetch("/api/trafic/record", {
          method: "POST",
          headers: headers(),
          body: JSON.stringify({
            duration_minutes: Number(form.elements.namedItem("duration_minutes").value),
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.status === 202) {
          say(
            "vac",
            `Trafic complet lancé (${data.duration_minutes} min) : 4 KiwiSDR répartis. Liste Trafic à la fin.`,
            true
          );
          return;
        }
        say("vac", _detail(data) || `Erreur ${res.status}`, false);
      } catch (err) {
        say("vac", String(err), false);
      } finally {
        busy(recordBtn, false);
      }
    });
  }

  const saveBuddyBtn = document.getElementById("save-buddy");
  if (saveBuddyBtn) {
    saveBuddyBtn.addEventListener("click", async () => {
      busy(saveBuddyBtn, true);
      say("buddy", "Sauvegarde du buddy call…", true);
      try {
        const data = await saveSettings();
        say(
          "buddy",
          `Buddy call mémorisé : ${data.buddy_main_khz} / ${data.buddy_alt_khz} kHz à ${data.buddy_time_utc} TU` +
            ` · ${data.buddy_enabled ? "actif" : "désactivé"}` +
            ` · centroïde ${ (data.buddy_skippers || []).join(", ") || "aucun skipper" }` +
            ` · 4 Kiwi.`,
          true
        );
      } catch (err) {
        say("buddy", String(err), false);
      } finally {
        busy(saveBuddyBtn, false);
      }
    });
  }

  const saveDisplayBtn = document.getElementById("save-display");
  if (saveDisplayBtn) {
    saveDisplayBtn.addEventListener("click", async () => {
      busy(saveDisplayBtn, true);
      say("display", "Sauvegarde de l’affichage…", true);
      try {
        const data = await saveSettings();
        const d = (data && data.display) || payload().display;
        window.dispatchEvent(new CustomEvent("ggr-display", { detail: d }));
        say("display", "Affichage globe mémorisé (défauts pour les visiteurs).", true);
      } catch (err) {
        say("display", String(err), false);
      } finally {
        busy(saveDisplayBtn, false);
      }
    });
  }

  const saveWaitBtn = document.getElementById("save-wait");
  if (saveWaitBtn) {
    saveWaitBtn.addEventListener("click", async () => {
      busy(saveWaitBtn, true);
      say("wait", "Sauvegarde de la page d’attente…", true);
      try {
        const data = await saveSettings();
        say(
          "wait",
          data.unavailable
            ? "Page d’attente activée : les visiteurs voient le globe flouté."
            : "Page d’attente désactivée.",
          true
        );
      } catch (err) {
        say("wait", String(err), false);
      } finally {
        busy(saveWaitBtn, false);
      }
    });
  }
})();
