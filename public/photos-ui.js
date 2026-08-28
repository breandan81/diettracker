/* Progress photos + Grok vision gallery */
(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => [...document.querySelectorAll(sel)];

  let photos = [];
  let photoSeries = [];
  let visualChart = null;
  let visualZoomUi = null;
  let activePhotoId = null;
  let weightSeries = []; // [{date, weight|trend}]
  let heightIn = null;
  let photosAllowed = false;
  let photosUiEnabled = false;

  function fmt(n, d = 1) {
    if (n == null || Number.isNaN(n)) return "—";
    return Number(n).toFixed(d);
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      credentials: "include",
      ...opts,
    });
    if (res.status === 401) {
      location.href = "/login.html";
      throw new Error("Not authenticated");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || data.detail || res.statusText || "request failed");
    return data;
  }

  function applyPhotosAccessUI() {
    const form = $("#photo-form");
    const gate = $("#photo-invite-gate");
    const inviteForm = $("#photo-invite-form");
    if (form) form.style.display = photosAllowed ? "" : "none";
    if (gate) gate.style.display = photosAllowed ? "none" : "";
    if (inviteForm) inviteForm.style.display = photosAllowed ? "none" : "";
  }

  async function refreshPhotosAccess() {
    try {
      const me = await api("/api/auth/me");
      photosAllowed = !!(me.user && me.user.photos_allowed);
    } catch (_) {
      photosAllowed = false;
    }
    applyPhotosAccessUI();
  }

  function ensureTrackerIfHidden() {
    const photosView = $("#view-photos");
    if (photosView && !photosView.hidden) setView("tracker");
    else if (location.hash === "#photos") {
      try {
        history.replaceState(null, "", "#tracker");
      } catch (_) {}
    }
  }

  function setView(name) {
    const tracker = $("#view-tracker");
    const photosView = $("#view-photos");
    if (!tracker || !photosView) return;
    if (name === "photos" && !photosUiEnabled) {
      name = "tracker";
    }
    const isPhotos = name === "photos";
    tracker.hidden = isPhotos;
    photosView.hidden = !isPhotos;
    $$("#view-tabs .view-tab").forEach((b) => {
      if (b.dataset.view) b.classList.toggle("active", b.dataset.view === name);
    });
    if (isPhotos) {
      // Always reload from server — otherwise a fresh session/device shows an empty gallery
      // after we stopped calling loadPhotos() on init.
      refreshPhotosAccess()
        .then(() => loadPhotos())
        .then(() => renderGallery())
        .catch((e) => console.warn("photos load failed", e));
    } else if (photosUiEnabled) {
      renderVisualChart();
    }
    try {
      history.replaceState(null, "", isPhotos ? "#photos" : "#tracker");
    } catch (_) {}
  }

  function setXaiPill(st) {
    for (const id of ["xai-pill", "xai-pill-2"]) {
      const el = $(`#${id}`);
      if (!el) continue;
      if (st?.ok) {
        el.textContent = `Vision · ${st.model || "grok"}`;
        el.className = "pill up";
        el.title = "xAI vision configured";
      } else {
        el.textContent = "Vision offline";
        el.className = "pill down";
        el.title = "Set XAI_API_KEY / secrets.env";
      }
    }
  }

  function bmiFromLbIn(lb, inches) {
    if (lb == null || inches == null || !(inches > 0) || !(lb > 0)) return null;
    return (703 * Number(lb)) / (Number(inches) * Number(inches));
  }

  /** Scale BMI at each weigh-in we have (trend preferred, else raw). */
  function scaleBmiPoints() {
    if (!heightIn || !weightSeries.length) return [];
    const out = [];
    for (const e of weightSeries) {
      // Waist-only entries are not weigh-ins; their carried trend would plot a
      // duplicate BMI point on a day nobody stepped on the scale.
      if (e.weight == null) continue;
      const lb = e.trend != null ? e.trend : e.weight;
      const bmi = bmiFromLbIn(lb, heightIn);
      if (bmi == null || !e.date) continue;
      out.push({ x: e.date, y: Math.round(bmi * 10) / 10 });
    }
    return out;
  }

  window.hackDietPhotos = {
    setScaleContext({ series, height_in }) {
      if (series) weightSeries = series;
      if (height_in !== undefined) heightIn = height_in;
      if (photosUiEnabled) renderVisualChart();
    },
    setPhotoSeries(series) {
      photoSeries = series || [];
      if (photosUiEnabled) renderVisualChart();
    },
    setPhotosUiEnabled(on) {
      photosUiEnabled = !!on;
      if (!photosUiEnabled) ensureTrackerIfHidden();
    },
    ensureTrackerIfHidden,
    async refresh() {
      if (!photosUiEnabled) return;
      await loadPhotos();
    },
  };

  async function loadPhotos() {
    try {
      const [plist, series, vst] = await Promise.all([
        api("/api/photos"),
        api("/api/photos/series"),
        api("/api/vision/status"),
      ]);
      photos = plist.photos || [];
      photoSeries = series.series || [];
      if (plist.photos_allowed != null) photosAllowed = !!plist.photos_allowed;
      applyPhotosAccessUI();
      setXaiPill(vst);
      renderGallery();
      renderVisualChart();
    } catch (e) {
      console.error(e);
      setXaiPill({ ok: false });
    }
  }

  function renderGallery() {
    const el = $("#gallery");
    const count = $("#photo-count");
    if (count) count.textContent = `${photos.length} photo${photos.length === 1 ? "" : "s"}`;
    if (!el) return;
    if (!photos.length) {
      el.innerHTML = `<p class="empty">No photos yet — upload one above.</p>`;
      return;
    }
    // newest first
    const items = [...photos].reverse();
    el.innerHTML = items
      .map((p) => {
        const score = p.appearance_score != null ? fmt(p.appearance_score, 1) : "—";
        const bmi = p.bmi_point != null ? fmt(p.bmi_point, 1) : "—";
        return `<button type="button" class="gal-card" data-id="${p.id}">
          <img src="${p.image_url}" alt="" loading="lazy" />
          <div class="gal-meta">
            <div class="gal-date">${p.date}</div>
            <div class="gal-scores"><span>★ ${score}</span><span>BMI ${bmi}</span></div>
          </div>
        </button>`;
      })
      .join("");
  }

  function renderVisualChart() {
    const ctx = $("#chart-visual");
    if (!ctx || typeof Chart === "undefined") return;

    const appearance = photoSeries
      .filter((p) => p.appearance_score != null)
      .map((p) => ({ x: p.date, y: p.appearance_score }));
    const visualBmi = photoSeries
      .filter((p) => p.bmi_point != null)
      .map((p) => ({ x: p.date, y: p.bmi_point }));

    const datasets = [
      {
        label: "Appearance (1–10)",
        data: appearance,
        yAxisID: "yScore",
        borderColor: "rgba(224, 168, 92, 1)",
        backgroundColor: "rgba(224, 168, 92, 0.25)",
        showLine: true,
        tension: 0.2,
        pointRadius: 5,
        borderWidth: 2,
      },
      {
        label: "Visual BMI (Grok)",
        data: visualBmi,
        yAxisID: "yBmi",
        borderColor: "rgba(204, 102, 204, 1)",
        backgroundColor: "rgba(204, 102, 204, 0.2)",
        showLine: true,
        tension: 0.2,
        pointRadius: 5,
        borderWidth: 2,
      },
    ];

    const scalePts = scaleBmiPoints();
    if (scalePts.length) {
      datasets.push({
        label: "Scale BMI",
        data: scalePts,
        yAxisID: "yBmi",
        borderColor: "rgba(125, 211, 160, 0.9)",
        backgroundColor: "rgba(125, 211, 160, 0.9)",
        showLine: true,
        tension: 0.15,
        pointRadius: 4,
        pointHoverRadius: 6,
        borderWidth: 2,
      });
    }

    if (visualChart) {
      visualChart.data.datasets = datasets;
      visualChart.update("none");
      visualZoomUi?.sync();
      return;
    }

    visualChart = new Chart(ctx, {
      type: "line",
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "nearest", intersect: false, axis: "x" },
        plugins: {
          legend: {
            labels: {
              color: "#8b93a7",
              font: { family: "'IBM Plex Mono', monospace", size: 11 },
              boxWidth: 12,
            },
          },
          zoom:
            typeof HdChartZoom === "undefined"
              ? undefined
              : HdChartZoom.zoomOptions({
                  mode: "xy",
                  onChange: () => visualZoomUi?.sync(),
                }),
        },
        scales: {
          x: {
            type: "time",
            time: { unit: "day", tooltipFormat: "yyyy-MM-dd" },
            grid: { color: "rgba(120,160,220,0.08)" },
            ticks: { color: "#8b93a7", maxRotation: 0 },
          },
          yScore: {
            position: "left",
            min: 1,
            max: 10,
            title: { display: true, text: "Appearance", color: "#e0a85c" },
            grid: { color: "rgba(120,160,220,0.08)" },
            ticks: { color: "#e0a85c" },
          },
          yBmi: {
            position: "right",
            title: { display: true, text: "BMI", color: "#cc66cc" },
            grid: { drawOnChartArea: false },
            ticks: { color: "#cc66cc" },
          },
        },
        onResize: () => visualZoomUi?.sync(),
      },
    });

    if (!visualZoomUi && typeof HdChartZoom !== "undefined") {
      visualZoomUi = HdChartZoom.wireResetButton(
        $("#chart-visual-zoom-reset"),
        () => visualChart
      );
    }
  }

  function showProjection(p) {
    const wrap = $("#pd-proj-wrap");
    const img = $("#pd-proj-img");
    const imgs = document.querySelector(".pd-images");
    const msg = $("#pd-proj-msg");
    const goalScores = $("#pd-goal-scores");
    const compare = document.querySelector(".pd-compare-scores");
    // Never show At-goal vision scores — preview is visual-only
    if (goalScores) goalScores.hidden = true;
    compare?.classList.remove("has-goal");

    const hideProj = () => {
      // Clear src so mobile doesn't show a broken-image icon for an empty/404 projection
      if (img) {
        img.removeAttribute("src");
        img.alt = "";
      }
      if (wrap) {
        wrap.hidden = true;
        wrap.style.display = "none";
      }
      imgs?.classList.remove("has-projection");
      if (msg) msg.hidden = true;
    };

    if (p?.has_projection && p.projection_url) {
      if (img) {
        img.alt = "goal weight projection";
        img.onerror = () => {
          // File missing / 404 — hide entirely rather than a broken icon
          hideProj();
        };
        img.src = p.projection_url + "?t=" + Date.now();
      }
      const g = p.projection_goal_lb != null ? fmt(p.projection_goal_lb, 0) : "?";
      const cap = $("#pd-proj-caption");
      if (cap) {
        cap.innerHTML =
          `At goal (~${g} lb) <span class="pd-zoom-hint">click to zoom</span>`;
      }
      if (wrap) {
        wrap.hidden = false;
        wrap.style.display = "";
      }
      imgs?.classList.add("has-projection");

      if (msg) {
        const bits = [];
        if (p.projection_model) bits.push(`Imagine · ${p.projection_model}`);
        bits.push("visual preview only");
        msg.textContent = bits.join(" · ");
        msg.className = "hint ok";
        msg.hidden = false;
      }
    } else {
      hideProj();
    }
  }

  function openPhoto(id) {
    const p = photos.find((x) => x.id === id);
    if (!p) return;
    activePhotoId = id;
    $("#pd-img").src = p.image_url;
    $("#pd-title").textContent = `${p.date}${p.note ? " · " + p.note : ""}`;
    $("#pd-score").textContent =
      p.appearance_score != null ? `${fmt(p.appearance_score, 1)} / 10` : "—";
    const bmi = p.bmi_point;
    const lo = p.bmi_low;
    const hi = p.bmi_high;
    $("#pd-bmi").textContent =
      bmi != null
        ? `${fmt(bmi, 1)}${lo != null && hi != null ? ` (${fmt(lo, 1)}–${fmt(hi, 1)})` : ""}`
        : "—";
    $("#pd-conf").textContent = p.confidence_overall || p.bmi_confidence || "—";
    const just = p.appearance_justification || p.analysis?.appearance_rating?.justification || "";
    $("#pd-just").textContent = just;
    const obs = p.analysis?.observations || {};
    $("#pd-obs").textContent = [
      obs.face_softness && `face: ${obs.face_softness}`,
      obs.midsection && `midsection: ${obs.midsection}`,
      obs.overall_build && `build: ${obs.overall_build}`,
      obs.notes && `notes: ${obs.notes}`,
      p.model && `model: ${p.model}`,
    ]
      .filter(Boolean)
      .join("\n");
    showProjection(p);
    const dlg = $("#photo-dialog");
    dlg.showModal();
    // Keep "Now" at the top — focusing action buttons can scroll the dialog
    // and hide the real / Imagine photos on small viewports.
    dlg.scrollTop = 0;
    document.querySelector(".pd-images")?.scrollTo?.(0, 0);
  }

  // events
  $$("#view-tabs .view-tab[data-view]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.view === "photos" && !photosUiEnabled) return;
      setView(btn.dataset.view);
    });
  });
  document.querySelectorAll(".jump-photos").forEach((b) => {
    b.addEventListener("click", () => {
      if (!photosUiEnabled) return;
      setView("photos");
    });
  });

  $("#gallery")?.addEventListener("click", (ev) => {
    const card = ev.target.closest(".gal-card");
    if (!card) return;
    openPhoto(parseInt(card.dataset.id, 10));
  });

  function openLightbox(src, caption) {
    const box = $("#photo-lightbox");
    const img = $("#photo-lightbox-img");
    const cap = $("#photo-lightbox-cap");
    if (!box || !img || !src) return;
    img.src = src;
    img.alt = caption || "";
    if (cap) cap.textContent = caption || "";
    // showModal() puts this in the top layer above #photo-dialog
    if (typeof box.showModal === "function") {
      if (!box.open) box.showModal();
    } else {
      box.setAttribute("open", "");
    }
  }

  function closeLightbox() {
    const box = $("#photo-lightbox");
    if (!box) return;
    const img = $("#photo-lightbox-img");
    if (img) img.removeAttribute("src");
    if (typeof box.close === "function") {
      if (box.open) box.close();
    } else {
      box.removeAttribute("open");
    }
  }

  $("#pd-img")?.addEventListener("click", () => {
    const src = $("#pd-img")?.src;
    if (!src) return;
    openLightbox(src, "Now");
  });
  $("#pd-proj-img")?.addEventListener("click", () => {
    const src = $("#pd-proj-img")?.src;
    if (!src) return;
    const raw = ($("#pd-proj-caption")?.textContent || "At goal").replace(
      /\s*click to zoom\s*/i,
      ""
    ).trim();
    openLightbox(src, raw || "At goal");
  });
  $("#photo-lightbox-close")?.addEventListener("click", (ev) => {
    ev.stopPropagation();
    closeLightbox();
  });
  $("#photo-lightbox")?.addEventListener("click", (ev) => {
    // panel / empty area closes; clicking the image itself does not
    if (ev.target && ev.target.id === "photo-lightbox-img") return;
    closeLightbox();
  });
  // Native dialog Escape closes the topmost modal first (lightbox), then photo-dialog.
  $("#photo-dialog")?.addEventListener("close", () => closeLightbox());

  $("#pd-close")?.addEventListener("click", () => $("#photo-dialog").close());
  $("#pd-delete")?.addEventListener("click", async () => {
    if (!activePhotoId || !confirm("Delete this photo and its ratings?")) return;
    try {
      const data = await api(`/api/photos/${activePhotoId}`, { method: "DELETE" });
      photos = data.photos || [];
      photoSeries = data.photo_series || [];
      $("#photo-dialog").close();
      renderGallery();
      renderVisualChart();
    } catch (e) {
      alert(e.message);
    }
  });

  $("#pd-reanalyze")?.addEventListener("click", async () => {
    if (!activePhotoId) return;
    const btn = $("#pd-reanalyze");
    btn.disabled = true;
    btn.textContent = "Analyzing…";
    try {
      const data = await api(`/api/photos/${activePhotoId}/analyze`, { method: "POST" });
      photos = data.photos || [];
      photoSeries = data.photo_series || [];
      openPhoto(activePhotoId);
      renderGallery();
      renderVisualChart();
    } catch (e) {
      alert(e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "Re-analyze";
    }
  });

  $("#pd-project")?.addEventListener("click", async () => {
    if (!activePhotoId) return;
    const btn = $("#pd-project");
    const msg = $("#pd-proj-msg");
    btn.disabled = true;
    const prev = btn.textContent;
    btn.textContent = "Imagining…";
    if (msg) {
      msg.textContent = "Grok Imagine → goal preview (can take a bit)…";
      msg.className = "hint";
      msg.hidden = false;
    }
    try {
      const data = await api(`/api/photos/${activePhotoId}/project-goal`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      photos = data.photos || photos;
      // merge returned photo
      if (data.photo) {
        const i = photos.findIndex((x) => x.id === data.photo.id);
        if (i >= 0) photos[i] = data.photo;
        else photos.push(data.photo);
      }
      openPhoto(activePhotoId);
      renderGallery();
    } catch (e) {
      if (msg) {
        msg.textContent = e.message;
        msg.className = "hint err";
        msg.hidden = false;
      } else {
        alert(e.message);
      }
    } finally {
      btn.disabled = false;
      btn.textContent = prev;
    }
  });

  $("#p-file")?.addEventListener("change", () => {
    const f = $("#p-file").files?.[0];
    const wrap = $("#photo-preview");
    const img = $("#photo-preview-img");
    if (!f) {
      wrap.hidden = true;
      return;
    }
    img.src = URL.createObjectURL(f);
    wrap.hidden = false;
  });

  $("#photo-invite-form")?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const msg = $("#photo-msg");
    try {
      const data = await api("/api/auth/redeem-photo-invite", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: $("#photo-invite-code").value }),
      });
      photosAllowed = !!data.photos_allowed;
      applyPhotosAccessUI();
      if (msg) {
        msg.textContent = data.message || "Photo uploads unlocked";
        msg.className = "hint ok";
        msg.hidden = false;
      }
    } catch (e) {
      if (msg) {
        msg.textContent = e.message;
        msg.className = "hint err";
        msg.hidden = false;
      } else alert(e.message);
    }
  });

  $("#photo-form")?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const msg = $("#photo-msg");
    if (!photosAllowed) {
      if (msg) {
        msg.textContent = "Photo uploads are invite-only";
        msg.className = "hint err";
        msg.hidden = false;
      }
      return;
    }
    const file = $("#p-file").files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("date", $("#p-date").value);
    fd.append("note", $("#p-note").value || "");
    fd.append("analyze", $("#p-analyze").checked ? "1" : "0");

    const btn = $("#p-submit");
    btn.disabled = true;
    btn.textContent = $("#p-analyze").checked ? "Uploading + analyzing…" : "Uploading…";
    msg.hidden = true;
    try {
      const res = await fetch("/api/photos", { method: "POST", body: fd, credentials: "include" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || data.detail || res.statusText);
      photos = data.photos || [];
      photoSeries = data.photo_series || [];
      setXaiPill(data.xai);
      if (data.photo?.appearance_score != null) {
        msg.textContent =
          `Saved · appearance ${fmt(data.photo.appearance_score, 1)}/10 · visual BMI ${fmt(data.photo.bmi_point, 1)}`;
        msg.className = "hint ok";
      } else if (data.note && /fail|skip/i.test(data.note)) {
        msg.textContent = data.note;
        msg.className = "hint err";
      } else {
        msg.textContent = data.note || "Saved photo";
        msg.className = "hint ok";
      }
      msg.hidden = false;
      $("#p-note").value = "";
      $("#p-file").value = "";
      $("#photo-preview").hidden = true;
      renderGallery();
      renderVisualChart();
      if (data.photo?.id) openPhoto(data.photo.id);
    } catch (e) {
      msg.textContent = e.message;
      msg.className = "hint err";
      msg.hidden = false;
    } finally {
      btn.disabled = false;
      btn.textContent = "Upload";
    }
  });

  // init
  const today = (() => {
    const d = new Date();
    const off = d.getTimezoneOffset();
    return new Date(d.getTime() - off * 60000).toISOString().slice(0, 10);
  })();
  if ($("#p-date")) $("#p-date").value = today;

  // Photos UI stays dormant until app.js enables it for allowed accounts.
  if (location.hash === "#photos") {
    // Will no-op to tracker until setPhotosUiEnabled(true)
    setView("photos");
  }
})();
