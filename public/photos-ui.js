/* Progress photos + Grok vision gallery */
(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => [...document.querySelectorAll(sel)];

  let photos = [];
  let photoSeries = [];
  let visualChart = null;
  let activePhotoId = null;
  let weightSeries = []; // [{date, weight|trend}]
  let heightIn = null;

  function fmt(n, d = 1) {
    if (n == null || Number.isNaN(n)) return "—";
    return Number(n).toFixed(d);
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText || "request failed");
    return data;
  }

  function setView(name) {
    const tracker = $("#view-tracker");
    const photosView = $("#view-photos");
    if (!tracker || !photosView) return;
    const isPhotos = name === "photos";
    tracker.hidden = isPhotos;
    photosView.hidden = !isPhotos;
    $$("#view-tabs .view-tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.view === name);
    });
    if (isPhotos) renderGallery();
    else renderVisualChart();
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
      renderVisualChart();
    },
    setPhotoSeries(series) {
      photoSeries = series || [];
      renderVisualChart();
    },
    async refresh() {
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
      },
    });
  }

  function showProjection(p) {
    const wrap = $("#pd-proj-wrap");
    const imgs = document.querySelector(".pd-images");
    const msg = $("#pd-proj-msg");
    if (p?.has_projection && p.projection_url) {
      $("#pd-proj-img").src = p.projection_url + "?t=" + Date.now();
      const g = p.projection_goal_lb != null ? fmt(p.projection_goal_lb, 0) : "?";
      $("#pd-proj-caption").textContent = `At goal (~${g} lb)`;
      wrap.hidden = false;
      imgs?.classList.add("has-projection");
      if (msg) {
        msg.textContent = p.projection_model
          ? `Imagine · ${p.projection_model}`
          : "Goal projection ready";
        msg.className = "hint ok";
        msg.hidden = false;
      }
    } else {
      wrap.hidden = true;
      imgs?.classList.remove("has-projection");
      if (msg) msg.hidden = true;
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
    $("#photo-dialog").showModal();
  }

  // events
  $$("#view-tabs .view-tab").forEach((btn) => {
    btn.addEventListener("click", () => setView(btn.dataset.view));
  });
  document.querySelectorAll(".jump-photos").forEach((b) => {
    b.addEventListener("click", () => setView("photos"));
  });

  $("#gallery")?.addEventListener("click", (ev) => {
    const card = ev.target.closest(".gal-card");
    if (!card) return;
    openPhoto(parseInt(card.dataset.id, 10));
  });

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
      msg.textContent = "Grok Imagine is editing this photo toward your goal weight…";
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

  $("#photo-form")?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const msg = $("#photo-msg");
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
      const res = await fetch("/api/photos", { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || res.statusText);
      photos = data.photos || [];
      photoSeries = data.photo_series || [];
      setXaiPill(data.xai);
      msg.textContent = data.photo?.appearance_score != null
        ? `Saved · appearance ${fmt(data.photo.appearance_score, 1)}/10 · visual BMI ${fmt(data.photo.bmi_point, 1)}`
        : "Saved photo";
      msg.className = "hint ok";
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

  if (location.hash === "#photos") setView("photos");
  loadPhotos();
})();
