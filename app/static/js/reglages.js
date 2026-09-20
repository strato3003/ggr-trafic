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
      const latEl = form.elements.namedItem("tx_lat_" + i);
      const lonEl = form.elements.namedItem("tx_lon_" + i);
      tx_sites.push({
        label: labelEl ? String(labelEl.value || "").trim() : "",
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
    };
  };

  const headers = () => {
    const token = (tokenInput && tokenInput.value) || "";
    if (token) localStorage.setItem(TOKEN_KEY, token);
    return {
      "Content-Type": "application/json",
      "X-Admin-Token": token,
    };
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

  const visitIp = document.getElementById("visit-ip");
  const visitLoad = document.getElementById("visit-load");
  const visitBody = document.getElementById("visit-body");
  const visitMsg = document.getElementById("visit-msg");
  const fmtTu = (iso) => {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso || "—";
    const p = (n) => String(n).padStart(2, "0");
    return (
      p(d.getUTCDate()) +
      "/" +
      p(d.getUTCMonth() + 1) +
      " " +
      p(d.getUTCHours()) +
      ":" +
      p(d.getUTCMinutes()) +
      ":" +
      p(d.getUTCSeconds()) +
      " TU"
    );
  };
  const loadVisits = async (ip) => {
    if (!visitBody) return;
    if (visitMsg) {
      visitMsg.hidden = false;
      visitMsg.textContent = "Chargement…";
      visitMsg.classList.remove("err");
    }
    const q = (ip || "").trim();
    const url = "/api/visits?limit=200" + (q ? "&ip=" + encodeURIComponent(q) : "");
    const res = await fetch(url, { headers: headers() });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(_detail(data) || "Jeton invalide");
    }
    const rows = data.events || [];
    visitBody.replaceChildren();
    if (!rows.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 5;
      td.textContent = q ? "Aucune activité pour cette IP." : "Aucune visite enregistrée.";
      tr.appendChild(td);
      visitBody.appendChild(tr);
    } else {
      rows.forEach((ev) => {
        const tr = document.createElement("tr");
        const action = ev.kind === "replay" ? "Replay" : "Page";
        const cityLine = [ev.city, ev.postal, ev.region].filter(Boolean).join(" · ");
        const line1 = [cityLine, ev.country].filter(Boolean).join(", ") || "—";
        const line2 = [ev.isp, ev.ptr].filter(Boolean).join(" · ");
        const lieu = line2 ? line1 + "\n" + line2 : line1;
        const cells = [fmtTu(ev.ts), ev.ip || "—", lieu, action, ev.target || "—"];
        cells.forEach((text, i) => {
          const td = document.createElement("td");
          td.textContent = text;
          if (i === 1) {
            td.className = "visit-log__ip";
            td.title = "Filtrer cette IP";
            td.addEventListener("click", () => {
              if (visitIp) visitIp.value = String(ev.ip || "");
              loadVisits(ev.ip).catch((err) => {
                if (visitMsg) {
                  visitMsg.hidden = false;
                  visitMsg.textContent = String(err);
                  visitMsg.classList.add("err");
                }
              });
            });
          }
          if (i === 2) {
            td.className = "visit-log__lieu";
            const coords = [ev.latitude, ev.longitude].filter(Boolean).join(", ");
            if (coords) td.title = coords + " (préfixe FAI)";
          }
          tr.appendChild(td);
        });
        visitBody.appendChild(tr);
      });
    }
    if (visitMsg) {
      visitMsg.hidden = false;
      visitMsg.textContent = rows.length + " événement(s)";
      visitMsg.classList.remove("err");
    }
  };
  if (visitLoad) {
    visitLoad.addEventListener("click", () => {
      loadVisits(visitIp && visitIp.value).catch((err) => {
        if (visitMsg) {
          visitMsg.hidden = false;
          visitMsg.textContent = String(err);
          visitMsg.classList.add("err");
        }
      });
    });
  }
})();
