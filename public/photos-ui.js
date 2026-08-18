/* Progress photos + Grok vision gallery */
(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => [...document.querySelectorAll(sel)];

  let photos = [];
  let photoSeries = [];
  let visualChart = null;
  let activePhotoId = null;
  let scaleBmiPoint = null;

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

  window.hackDietPhotos = {
    setScaleBmi(bmi) {
      scaleBmiPoint = bmi;
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

    if (scaleBmiPoint != null && photoSeries.length) {
      datasets.push({
        label: "Scale BMI (now)",
        data: [
          { x: photoSeries[0].date, y: scaleBmiPoint },
          {
            x: photoSeries[photoSeries.length - 1].date,
            y: scaleBmiPoint,
          },
        ],
        yAxisID: "yBmi",
        borderColor: "rgba(125, 211, 160, 0.7)",
        borderDash: [6, 4],
        pointRadius: 0,
        borderWidth: 1.5,
        showLine: true,
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
