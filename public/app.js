/* τrend — frontend */
(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => [...document.querySelectorAll(sel)];

  let series = [];
  let summary = {};
  let settings = {};
  let range = "90";
  let chart;
  let chartZoomUi = null;
  let lastMood = "idle";
  let coachStyle = localStorage.getItem("hd_coach_style") || "pep";
  let aiCoach = null; // last LLM coach payload
  let aiPinned = false; // keep AI text until next generate
  let koboldOk = false;

  const MOOD_IMAGES = {
    idle: "/img/mood-idle.jpg",
    crushing: "/img/mood-crushing.jpg",
    losing: "/img/mood-losing.jpg",
    steady: "/img/mood-steady.jpg",
    gaining: "/img/mood-gaining.jpg",
    goal: "/img/mood-goal.jpg",
  };
  let currentMoodImg = "idle";

  function fmt(n, digits = 1) {
    if (n == null || Number.isNaN(n)) return "—";
    return Number(n).toFixed(digits);
  }

  function signClass(n) {
    if (n == null || Number.isNaN(n) || Math.abs(n) < 1e-9) return "";
    return n < 0 ? "loss" : "gain";
  }

  function todayISO() {
    const d = new Date();
    const off = d.getTimezoneOffset();
    const local = new Date(d.getTime() - off * 60000);
    return local.toISOString().slice(0, 10);
  }

  /** datetime-local value in local timezone */
  function nowLocalInput() {
    const d = new Date();
    const off = d.getTimezoneOffset();
    return new Date(d.getTime() - off * 60000).toISOString().slice(0, 16);
  }

  function toLocalInput(iso) {
    if (!iso) return nowLocalInput();
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) {
      // date-only
      return String(iso).slice(0, 10) + "T12:00";
    }
    const off = d.getTimezoneOffset();
    return new Date(d.getTime() - off * 60000).toISOString().slice(0, 16);
  }

  function fromLocalInput(val) {
    // treat as local wall time → ISO with offset
    if (!val) return null;
    const d = new Date(val);
    return d.toISOString();
  }

  function formatWhen(e) {
    const iso = e.logged_at || e.date;
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16);
    return d.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function daysBetweenISO(a, b) {
    const da = new Date(a + "T12:00:00");
    const db = new Date(b + "T12:00:00");
    return Math.round((db - da) / 86400000);
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
    });
    if (res.status === 401) {
      location.href = "/login.html";
      throw new Error("Not authenticated");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.error || data.detail || res.statusText || "request failed");
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function applyState(payload) {
    if (payload.series) series = payload.series;
    if (payload.entries) series = payload.entries;
    if (payload.summary) summary = payload.summary;
    if (payload.settings) settings = payload.settings;
    if (payload.half_life_days != null && summary) {
      summary.half_life_days = payload.half_life_days;
    }
    render();
  }

  function applyPhotosNav(user) {
    // Hide Photos chrome unless the feature is on AND this account may use it.
    // (Admins always allowed server-side; me.photos_allowed already reflects that.)
    const enabled =
      !!(user && user.photos_feature_enabled !== false && user.photos_allowed);
    const tab = $("#photos-tab");
    const visual = $("#panel-visual-ratings");
    const tag = $("#tagline");
    if (tab) tab.hidden = !enabled;
    if (visual) visual.hidden = !enabled;
    if (tag) {
      tag.textContent = enabled
        ? "Time-aware EMA trend · gap-tolerant · kcal from slope · Grok photo ratings"
        : "Time-aware EMA trend · gap-tolerant · kcal from slope";
    }
    // If Photos was open but access went away, snap back to tracker
    if (!enabled && window.hackDietPhotos && typeof window.hackDietPhotos.ensureTrackerIfHidden === "function") {
      window.hackDietPhotos.ensureTrackerIfHidden();
    } else if (!enabled && location.hash === "#photos") {
      location.hash = "";
      const photosView = $("#view-photos");
      const tracker = $("#view-tracker");
      if (photosView) photosView.hidden = true;
      if (tracker) tracker.hidden = false;
    }
    if (window.hackDietPhotos && typeof window.hackDietPhotos.setPhotosUiEnabled === "function") {
      window.hackDietPhotos.setPhotosUiEnabled(enabled);
    }
  }

  async function loadAll() {
    let meUser = null;
    try {
      const me = await api("/api/auth/me");
      meUser = me.user;
      const adminLink = $("#admin-link");
      // Admin tab only for allowlisted admins (ADMIN_USER_IDS)
      if (adminLink) adminLink.hidden = !(me.user && me.user.is_admin);
      applyPhotosNav(me.user);
    } catch (_) {
      applyPhotosNav(null);
    }
    const [trend, sets] = await Promise.all([
      api("/api/trend"),
      api("/api/settings"),
    ]);
    settings = sets;
    series = trend.series || [];
    summary = trend.summary || {};
    if (window.hackDietPhotos && meUser && meUser.photos_allowed) {
      if (trend.photo_series) window.hackDietPhotos.setPhotoSeries(trend.photo_series);
      window.hackDietPhotos.setScaleContext({
        series: series,
        height_in: settings.height_in ?? null,
      });
    }
    render();
    loadIngestTokens().catch(() => {});
  }

  function render() {
    renderStats();
    renderCoach();
    renderSettings();
    renderTable();
    renderChart();
  }

  async function refreshKoboldStatus() {
    const pill = $("#kobold-pill");
    try {
      const st = await api("/api/coach/status");
      koboldOk = !!st.ok;
      if (!pill) return;
      if (st.ok) {
        const short = (st.model || "grok").replace(/^grok-/, "grok-");
        const u = st.usage_today || {};
        const lim = st.limits || {};
        pill.textContent = `Grok · ${short}`;
        pill.title = `coach ${u.coach || 0}/${lim.coach ?? "?"} today · ${st.model || "xAI"}`;
        pill.className = "kobold-pill up";
      } else {
        pill.textContent = "Grok offline";
        pill.title = st.error || "XAI_API_KEY not configured";
        pill.className = "kobold-pill down";
      }
    } catch (e) {
      koboldOk = false;
      if (pill) {
        pill.textContent = "Grok offline";
        pill.title = e.message;
        pill.className = "kobold-pill down";
      }
    }
  }

  async function loadCachedCoach() {
    try {
      const data = await api("/api/coach");
      if (data.status) {
        koboldOk = !!data.status.ok;
        const pill = $("#kobold-pill");
        if (pill && data.status.ok) {
          pill.textContent = `Grok · ${data.status.model || "xai"}`;
          pill.className = "kobold-pill up";
        }
      }
      if (data.coach && data.coach.title) {
        aiCoach = data.coach;
        aiPinned = true;
      }
    } catch (_) {
      /* ignore */
    }
  }

  function setCoachQuotaNotice(text) {
    const meta = $("#coach-meta");
    if (!meta) return;
    if (!text) {
      // restore normal meta from aiCoach if present
      if (aiCoach) applyAiCoachToDom(true);
      else {
        meta.textContent = "";
        meta.hidden = true;
      }
      meta.classList.remove("quota-hit");
      return;
    }
    meta.textContent = text;
    meta.hidden = false;
    meta.classList.add("quota-hit");
  }

  async function requestPepTalk(opts = {}) {
    const btn = $("#btn-pep");
    const label = $("#btn-pep-label");
    if (btn) btn.classList.add("busy");
    if (label) label.textContent = "Thinking";
    setCoachQuotaNotice(null);
    try {
      const data = await api("/api/coach", {
        method: "POST",
        body: JSON.stringify({ style: coachStyle }),
      });
      // Keep previous text if server somehow returns empty coach
      if (data.coach) {
        aiCoach = data.coach;
        aiPinned = true;
        applyAiCoachToDom(true);
      }
      if (opts.celebrate !== false && aiCoach?.toast) {
        burstFX(aiCoach.toast);
      }
      await refreshKoboldStatus();
      return aiCoach;
    } catch (e) {
      if (e.status === 429) {
        // Leave title/message as-is; explain quota under the coach copy
        const lim = e.data?.limits?.coach;
        const used = e.data?.usage_today?.coach;
        const detail =
          (typeof e.data?.detail === "string" && e.data.detail) ||
          e.data?.error ||
          e.message ||
          "Daily AI coach quota exceeded.";
        let notice =
          used != null && lim != null
            ? `AI quota for today exceeded (${used}/${lim} coach calls). Previous pep talk kept — try again tomorrow.`
            : `AI quota for today exceeded — previous pep talk kept. ${detail}`;
        if (!aiCoach && e.data?.coach) {
          aiCoach = e.data.coach;
          aiPinned = true;
          applyAiCoachToDom(true);
        }
        setCoachQuotaNotice(notice);
        await refreshKoboldStatus();
        return aiCoach;
      }
      const msg = $("#form-msg");
      if (msg) {
        msg.textContent = "Coach failed: " + e.message;
        msg.className = "hint err";
        msg.hidden = false;
      } else {
        alert("Coach failed: " + e.message);
      }
      await refreshKoboldStatus();
      throw e;
    } finally {
      if (btn) btn.classList.remove("busy");
      if (label) label.textContent = "Pep talk";
    }
  }

  function applyAiCoachToDom(force) {
    if (!aiCoach || (!aiPinned && !force)) return;
    if (aiCoach.badge) $("#mood-badge").textContent = aiCoach.badge;
    if (aiCoach.title) $("#coach-title").textContent = aiCoach.title;
    if (aiCoach.message) {
      const el = $("#coach-msg");
      el.textContent = aiCoach.message;
      el.classList.add("ai-sourced");
    }
    const meta = $("#coach-meta");
    if (meta && !meta.classList.contains("quota-hit")) {
      const bits = [];
      if (aiCoach.style) bits.push(aiCoach.style);
      if (aiCoach.model) bits.push(String(aiCoach.model).replace(/^koboldcpp\//, ""));
      const focus =
        (aiCoach.context && aiCoach.context.coach_goals) ||
        settings.coach_goals ||
        "";
      if (focus) bits.push("focus: " + String(focus).slice(0, 60));
      if (aiCoach.generated_at) {
        bits.push(String(aiCoach.generated_at).replace("T", " ").replace("+00:00", "Z"));
      }
      meta.textContent = bits.length ? "via Grok · " + bits.join(" · ") : "";
      meta.hidden = !bits.length;
    }
  }

  // ---- motivation / mood ----

  function computeMood() {
    const s = summary || {};
    const goal = settings.goal_weight;
    const trend = s.trend;
    const rate = s.rate_lb_per_day;
    const count = s.count || 0;

    if (!count || trend == null) {
      return {
        mood: "idle",
        badge: "STAND BY",
        title: "Ready when you are",
        msg: "Log a weigh-in to wake the signal processor. Gaps are fine — the EMA half-life has your back.",
        progress: 0,
        atGoal: false,
      };
    }

    let atGoal = false;

    // progress toward goal if we know a starting point
    let progress = 0;
    let lbLeft = null;
    let etaDays = null;
    if (goal != null && goal !== "" && series.length >= 1) {
      const start = series[0].trend ?? series[0].weight;
      const span = start - goal;
      if (Math.abs(span) > 0.05) {
        // works for lose-weight goals (start > goal) and gain goals
        progress = ((start - trend) / span) * 100;
        progress = Math.max(0, Math.min(100, progress));
      }
      lbLeft = trend - goal;
      if (rate != null && Math.abs(rate) > 1e-6) {
        // days until trend hits goal if current rate continues
        const need = goal - trend; // negative if need to lose
        if ((need < 0 && rate < 0) || (need > 0 && rate > 0)) {
          etaDays = Math.abs(need / rate);
        }
      }
      // at goal if trend is at or past the goal in the intended direction
      if (Math.abs(trend - goal) < 0.15) {
        progress = 100;
        atGoal = true;
      } else if (span > 0 && trend <= goal) {
        // losing toward lower goal and overshot
        progress = 100;
        atGoal = true;
      } else if (span < 0 && trend >= goal) {
        progress = 100;
        atGoal = true;
      }
    }

    if (atGoal) {
      return {
        mood: "goal",
        badge: "GOAL REACHED",
        title: pick([
          "You made it to the summit!",
          "Flag planted. Absolute legend.",
          "Target acquired. Trend locked.",
        ]),
        msg: pick([
          "Maintain with small corrections — that's the whole trend trick. Don't celebrate with a surplus (unless you want to).",
          "The hard part is staying here. Watch the trend, not the daily noise.",
        ]),
        progress: 100,
        atGoal: true,
        lbLeft: 0,
        etaDays: 0,
      };
    }

    // rate thresholds (lb/day)
    let mood = "steady";
    if (rate != null) {
      if (rate <= -0.15) mood = "crushing"; // ~1+ lb/wk loss
      else if (rate <= -0.03) mood = "losing";
      else if (rate >= 0.05) mood = "gaining";
      else mood = "steady";
    }

    const kcal = s.kcal_per_day;
    const lines = moodCopy(mood, { rate, kcal, lbLeft, etaDays, progress, goal });
    return {
      mood,
      ...lines,
      progress,
      atGoal: false,
      lbLeft,
      etaDays,
    };
  }

  function pick(arr) {
    return arr[Math.floor(Math.random() * arr.length)];
  }

  // Stable pick from string so UI doesn't flicker every render
  function pickStable(arr, key) {
    let h = 0;
    const s = String(key);
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return arr[h % arr.length];
  }

  function moodCopy(mood, ctx) {
    const rateW = ctx.rate != null ? fmt(ctx.rate * 7, 2) : "—";
    const kcal = ctx.kcal != null ? Math.round(ctx.kcal) : null;
    const left =
      ctx.lbLeft != null ? `${fmt(Math.abs(ctx.lbLeft), 1)} lb` : null;
    const eta =
      ctx.etaDays != null && ctx.etaDays < 400
        ? ctx.etaDays < 2
          ? "about a day"
          : `~${Math.round(ctx.etaDays)} days`
        : null;

    if (mood === "crushing") {
      return {
        badge: "ROCKET MODE",
        title: pickStable(
          ["Trend is diving — keep the fire.", "Fat cells filing complaints.", "Throttle wide open."],
          summary.latest_date + "c"
        ),
        msg:
          (kcal != null
            ? `Estimated ${Math.abs(kcal)} kcal/day deficit · ${rateW} lb/wk. `
            : "") +
          (left && eta
            ? `${left} to goal at this pace (${eta}).`
            : left
              ? `${left} to goal.`
              : "The EMA is buying what you're selling."),
      };
    }
    if (mood === "losing") {
      return {
        badge: "ON TRAIL",
        title: pickStable(
          ["Steady climb. Signal is green.", "The slope is your friend.", "Quiet progress — perfect."],
          summary.latest_date + "l"
        ),
        msg:
          (kcal != null
            ? `~${Math.abs(kcal)} kcal/day deficit · ${rateW} lb/wk. `
            : "") +
          (left && eta
            ? `${left} to go · ETA ${eta} if you hold the line.`
            : "Ignore day-to-day bounce. Trust the trend line."),
      };
    }
    if (mood === "gaining") {
      return {
        badge: "COURSE CORRECT",
        title: pickStable(
          ["Trend drifting up — small nudge time.", "Noise or signal? Probably signal.", "Recalibrate the intake."],
          summary.latest_date + "g"
        ),
        msg:
          (kcal != null && kcal > 0
            ? `~${kcal} kcal/day surplus · ${rateW} lb/wk. `
            : "") +
          "No panic — that's why we smooth. Trim a bit or move a bit; re-check in a few weigh-ins.",
      };
    }
    // steady
    return {
      badge: "HOLDING PATTERN",
      title: pickStable(
        ["Weight stable. Control system online.", "Maintenance mode engaged.", "Flat trend — pure equilibrium."],
        summary.latest_date + "s"
      ),
      msg:
        "You're near energy balance. To lose, open a modest deficit; to hold, stay the course. The chart doesn't lie (slowly).",
    };
  }

  function weighInStreak() {
    if (!series.length) return 0;
    // count distinct dates going backward, allowing 1-day gaps as "on cadence"
    // streak = consecutive calendar days with a weigh-in ending at latest
    const dates = [...new Set(series.map((e) => e.date))].sort();
    if (!dates.length) return 0;
    let streak = 1;
    for (let i = dates.length - 1; i > 0; i--) {
      const gap = daysBetweenISO(dates[i - 1], dates[i]);
      if (gap === 1) streak++;
      else if (gap === 2) streak++; // every-other-day still counts as engaged
      else break;
    }
    return streak;
  }

  function longestGapDays() {
    if (series.length < 2) return 0;
    let max = 0;
    for (let i = 1; i < series.length; i++) {
      const g = daysBetweenISO(series[i - 1].date, series[i].date);
      if (g > max) max = g;
    }
    return max;
  }

  function netLost() {
    if (series.length < 2) return 0;
    const a = series[0].trend ?? series[0].weight;
    const b = series[series.length - 1].trend ?? series[series.length - 1].weight;
    return a - b; // positive = lost
  }

  function setMoodImage(mood) {
    const img = $("#mood-img");
    const frame = $("#mood-frame");
    if (!img) return;
    const key = MOOD_IMAGES[mood] ? mood : "idle";
    const src = MOOD_IMAGES[key];
    if (key === currentMoodImg && img.getAttribute("src") === src) return;
    currentMoodImg = key;
    if (frame) frame.classList.add("swap");
    const next = new Image();
    next.onload = () => {
      img.src = src;
      requestAnimationFrame(() => {
        if (frame) frame.classList.remove("swap");
      });
    };
    next.onerror = () => {
      img.src = MOOD_IMAGES.idle;
      if (frame) frame.classList.remove("swap");
    };
    next.src = src;
  }

  function renderCoach() {
    const coach = $("#coach");
    if (!coach) return;
    const info = computeMood();
    const prev = lastMood;
    lastMood = info.mood;

    coach.dataset.mood = info.mood;
    document.body.className = "mood-" + info.mood;

    // Rule-based copy unless an AI pep talk is pinned
    if (aiPinned && aiCoach) {
      applyAiCoachToDom(true);
    } else {
      $("#mood-badge").textContent = info.badge;
      $("#coach-title").textContent = info.title;
      const msgEl = $("#coach-msg");
      msgEl.textContent = info.msg;
      msgEl.classList.remove("ai-sourced");
      const meta = $("#coach-meta");
      if (meta) meta.hidden = true;
    }

    // goal meter
    const goal = settings.goal_weight;
    const fill = $("#goal-fill");
    const marker = $("#goal-you");
    const pct = info.progress || 0;
    fill.style.width = pct + "%";
    marker.style.left = pct + "%";

    const chip = $("#progress-chip");
    if (goal == null || goal === "") {
      $("#goal-pct-label").textContent = "set a goal →";
      $("#goal-left").textContent = "no goal set";
      $("#goal-eta").textContent = "settings below";
      if (chip) chip.hidden = true;
    } else {
      $("#goal-pct-label").textContent = `${fmt(pct, 0)}%`;
      if (info.atGoal) {
        $("#goal-left").textContent = "at goal";
        $("#goal-eta").textContent = "maintain ✨";
      } else if (info.lbLeft != null) {
        const dir = info.lbLeft > 0 ? "to lose" : "to gain";
        $("#goal-left").textContent = `${fmt(Math.abs(info.lbLeft), 1)} lb ${dir}`;
        $("#goal-eta").textContent =
          info.etaDays != null && info.etaDays < 500
            ? `ETA ~${Math.round(info.etaDays)}d`
            : "ETA —";
      }
      if (chip) {
        chip.hidden = false;
        $("#progress-chip-pct").textContent = `${fmt(pct, 0)}%`;
      }
    }

    setMoodImage(info.mood);

    // Fat-burn throttle: RIGHT = more burn (red).
    // App kcal is negative for deficit; invert so burn increases to the right.
    // Examples: -1000 kcal → left 95% (red); 0 → 50%; +1000 → 5% (blue).
    const kcal = summary.kcal_per_day;
    const needle = $("#throttle-needle");
    const tLabel = $("#throttle-label");
    if (!needle) {
      /* no-op */
    } else if (kcal == null || summary.count === 0) {
      needle.style.left = "50%";
      if (tLabel) tLabel.textContent = "idle";
    } else {
      const k = Number(kcal);
      const burn = -k; // positive burn when kcal deficit
      const clampedBurn = Math.max(-1000, Math.min(1000, burn));
      const leftPct = 50 + (clampedBurn / 1000) * 45;
      needle.style.left = leftPct + "%";
      needle.style.transform = "";
      if (tLabel) {
        if (k < -50) tLabel.textContent = `${Math.round(k)} · burning`;
        else if (k > 50) tLabel.textContent = `+${Math.round(k)} · storing`;
        else tLabel.textContent = `${Math.round(k)} · balanced`;
      }
    }

    // badges
    const streak = weighInStreak();
    const lost = netLost();
    const gap = longestGapDays();
    const badges = [];

    badges.push({
      ico: "📅",
      text: streak >= 2 ? `${streak}-day log streak` : "start a streak",
      cls: streak >= 3 ? "on" : streak >= 1 ? "" : "dim",
    });
    if (lost > 0.3) {
      badges.push({
        ico: "📉",
        text: `${fmt(lost, 1)} lb off the trend`,
        cls: "on",
      });
    } else if (lost < -0.3) {
      badges.push({
        ico: "📈",
        text: `+${fmt(-lost, 1)} lb on the trend`,
        cls: "hot",
      });
    } else if (series.length >= 2) {
      badges.push({ ico: "➖", text: "trend flat", cls: "" });
    }
    if (summary.count >= 7) {
      badges.push({ ico: "🔬", text: `${summary.count} samples`, cls: "on" });
    } else if (summary.count > 0) {
      badges.push({
        ico: "🔬",
        text: `${summary.count}/7 samples to trust the slope`,
        cls: summary.count >= 4 ? "" : "dim",
      });
    }
    if (gap >= 5) {
      badges.push({
        ico: "🕳️",
        text: `survived ${gap}d gap`,
        cls: "on",
      });
    }
    if (info.mood === "crushing") {
      badges.push({ ico: "🚀", text: "rocket deficit", cls: "on" });
    }
    if (info.mood === "goal") {
      badges.push({ ico: "🏆", text: "summit", cls: "hot" });
    }

    $("#badges").innerHTML = badges
      .map(
        (b) =>
          `<span class="badge ${b.cls}"><span class="ico">${b.ico}</span>${b.text}</span>`
      )
      .join("");

    // celebrate mood upgrades
    if (
      (info.mood === "crushing" && prev !== "crushing" && prev !== "idle") ||
      (info.mood === "goal" && prev !== "goal")
    ) {
      burstFX(info.mood === "goal" ? "🏆 SUMMIT!" : "🚀 TREND ON FIRE");
    }
  }

  function burstFX(toastText) {
    const layer = $("#fx-layer");
    if (!layer) return;
    const colors = ["#7dd3a0", "#5b9fd4", "#e0a85c", "#e07070", "#cfe6ff", "#cc99cc"];
    const cx = window.innerWidth / 2;
    const cy = window.innerHeight * 0.28;
    for (let i = 0; i < 36; i++) {
      const el = document.createElement("div");
      el.className = "fx-bit";
      const angle = (Math.PI * 2 * i) / 36 + Math.random() * 0.4;
      const dist = 80 + Math.random() * 160;
      el.style.left = cx + "px";
      el.style.top = cy + "px";
      el.style.background = colors[i % colors.length];
      el.style.setProperty("--dx", Math.cos(angle) * dist + "px");
      el.style.setProperty("--dy", Math.sin(angle) * dist + "px");
      el.style.setProperty("--rot", 200 + Math.random() * 400 + "deg");
      el.style.width = 6 + Math.random() * 8 + "px";
      el.style.height = 6 + Math.random() * 8 + "px";
      el.style.borderRadius = Math.random() > 0.5 ? "50%" : "2px";
      layer.appendChild(el);
      setTimeout(() => el.remove(), 1300);
    }
    if (toastText) {
      const t = document.createElement("div");
      t.className = "fx-toast";
      t.textContent = toastText;
      layer.appendChild(t);
      setTimeout(() => t.remove(), 1500);
    }
  }

  function celebrateLog(data) {
    const kcal = data.summary?.kcal_per_day;
    const rate = data.summary?.rate_lb_per_day;
    if (rate != null && rate < -0.03) {
      burstFX(pick(["✦ LOGGED", "📉 TREND LIKES THIS", "⚡ SIGNAL UPDATED"]));
    } else if (series.length === 1 || (data.summary?.count === 1)) {
      burstFX("✦ FIRST SAMPLE");
    } else {
      // subtle sparkle only
      const layer = $("#fx-layer");
      if (!layer) return;
      for (let i = 0; i < 10; i++) {
        const el = document.createElement("div");
        el.className = "fx-bit";
        el.style.left = window.innerWidth / 2 + (Math.random() - 0.5) * 120 + "px";
        el.style.top = "30%" ;
        el.style.background = "#5b9fd4";
        el.style.setProperty("--dx", (Math.random() - 0.5) * 80 + "px");
        el.style.setProperty("--dy", -(40 + Math.random() * 60) + "px");
        el.style.setProperty("--rot", "180deg");
        layer.appendChild(el);
        setTimeout(() => el.remove(), 1200);
      }
    }
  }

  function bmiMarkerPct(bmi) {
    // Bar is 4 equal-width category bands. Map BMI into the matching band
    // so 29.1 (near top of overweight) sits near the right edge of that band,
    // not the left (which a linear 15–40 scale wrongly did).
    //   under:  <18.5  →  0–25%   (use 15–18.5 for travel inside band)
    //   normal: 18.5–25 → 25–50%
    //   over:   25–30   → 50–75%
    //   obese:  ≥30     → 75–100% (use 30–40 for travel inside band)
    const v = Number(bmi);
    if (!Number.isFinite(v)) return 0;
    const band = (lo, hi, startPct) => {
      const t = (v - lo) / (hi - lo);
      return startPct + Math.max(0, Math.min(1, t)) * 25;
    };
    if (v < 18.5) return band(15, 18.5, 0);
    if (v < 25) return band(18.5, 25, 25);
    if (v < 30) return band(25, 30, 50);
    return band(30, 40, 75);
  }

  /**
   * Body-fat verdict bar — the fat% twin of the BMI bar.
   *
   * The cutoffs are sex- and age-specific (see bf_axis.js), so unlike BMI there
   * is nothing to draw until the profile says who this is: 26% is healthy for a
   * 44yo woman and overfat for a 44yo man.
   */
  function renderBfBands(bf) {
    const bar = $("#bf-bar");
    const marker = $("#bf-marker");
    const rangeEl = $("#s-bf-range");
    const valueEl = $("#s-bf");
    if (!bar || !rangeEl) return;

    const cat =
      typeof HdBfAxis === "undefined"
        ? null
        : HdBfAxis.bodyFatCategory(settings.sex, settings.age, bf);

    if (!cat) {
      bar.hidden = true;
      rangeEl.textContent =
        bf != null && !settings.sex ? "set sex in settings for ranges" : "";
      if (valueEl) valueEl.className = "stat-value";
      return;
    }

    bar.hidden = false;
    if (valueEl) valueEl.className = "stat-value bf-" + cat.key;
    cat.bands.forEach((b) => {
      const seg = $("#bf-seg-" + b.key);
      if (seg) seg.title = b.title;
    });
    rangeEl.textContent = `${cat.label} · healthy ${cat.healthy.low}–${cat.healthy.high}% for ${cat.sexLabel} ${cat.bracket}`;
    if (marker) marker.style.left = cat.markerPct + "%";
  }

  /**
   * Waist verdict bar — waist-to-height, so it scales with the same height
   * already stored for BMI and needs no sex table (see waist_axis.js).
   *
   * Waist is typed in by hand, so the newest weigh-in usually has none: the
   * tile shows the smoothed value and says how long ago the tape came out,
   * because a six-week-old measurement should not read like this morning's.
   */
  function renderWaist() {
    const s = summary || {};
    const valueEl = $("#s-waist");
    const subEl = $("#s-waist-sub");
    const bar = $("#waist-bar");
    const marker = $("#waist-marker");
    const rangeEl = $("#s-waist-range");
    if (!valueEl) return;

    const shown = s.waist_trend != null ? s.waist_trend : s.latest_waist;
    if (shown == null) {
      valueEl.textContent = "—";
      valueEl.className = "stat-value";
      subEl.textContent = "log one below when you measure";
      if (bar) bar.hidden = true;
      rangeEl.textContent = "";
      return;
    }

    valueEl.textContent = `${fmt(shown, 1)} in`;
    const age = daysAgo(s.latest_waist_at);
    const last =
      s.latest_waist != null ? `last ${fmt(s.latest_waist, 1)} in` : "trend";
    subEl.textContent = age == null ? last : `${last} · ${age}`;

    const cat =
      typeof HdWaistAxis === "undefined"
        ? null
        : HdWaistAxis.waistCategory(shown, settings.height_in, settings.sex);

    if (!cat) {
      valueEl.className = "stat-value";
      if (bar) bar.hidden = true;
      // Only blame the profile when the profile is actually the problem: this
      // branch is also where a failed waist_axis.js load lands, and telling
      // someone to set a height they already set sends them the wrong way.
      rangeEl.textContent = settings.height_in
        ? ""
        : "set height in settings for ranges";
      return;
    }

    valueEl.className = "stat-value waist-" + cat.key;
    if (bar) bar.hidden = false;
    cat.bands.forEach((b) => {
      const seg = $("#waist-seg-" + b.key);
      if (seg) seg.title = b.title;
    });
    if (marker) marker.style.left = cat.markerPct + "%";
    rangeEl.textContent =
      `${cat.label} · WHtR ${cat.ratio.toFixed(2)} · keep under ` +
      `${fmt(cat.healthyIn.high, 1)} in`;
    rangeEl.title =
      `Waist-to-height ratio: half your height (${fmt(cat.healthyIn.high, 1)} in) ` +
      `is the healthy ceiling.` +
      (cat.who
        ? ` For comparison the WHO cutoff for ${cat.who.sexLabel}s is ` +
          `${cat.who.increasedIn} in (raised) / ${cat.who.highIn} in (high).`
        : "");
  }

  /** "today" / "3d ago" / "5w ago" for a sparse hand-entered reading. */
  function daysAgo(iso) {
    if (!iso) return null;
    const t = new Date(iso).getTime();
    if (!Number.isFinite(t)) return null;
    const d = (Date.now() - t) / 86400000;
    if (d < 1) return "today";
    if (d < 14) return `${Math.round(d)}d ago`;
    return `${Math.round(d / 7)}w ago`;
  }

  function renderStats() {
    const s = summary || {};
    $("#s-trend").textContent =
      s.trend != null ? `${fmt(s.trend, 1)} lb` : "—";
    $("#s-raw").textContent =
      s.latest_weight != null
        ? `last ${fmt(s.latest_weight, 1)} lb · ${formatWhen({ logged_at: s.latest_logged_at, date: s.latest_date })} · ${s.count || 0} logs`
        : "no weigh-ins yet";

    const bfEl = $("#s-bf");
    const bfSub = $("#s-bf-sub");
    if (bfEl) {
      const bf = s.body_fat_trend != null ? s.body_fat_trend : s.latest_body_fat;
      if (bf != null) {
        bfEl.textContent = `${fmt(bf, 1)}%`;
        bfSub.textContent =
          s.body_fat_trend != null && s.latest_body_fat != null
            ? `trend · last ${fmt(s.latest_body_fat, 1)}%`
            : "EMA when scale sends BF";
      } else {
        bfEl.textContent = "—";
        bfSub.textContent = "from scale when available";
      }
      renderBfBands(bf);
    }
    renderWaist();

    const rWeek = s.rate_lb_per_week;
    const rateEl = $("#s-rate");
    rateEl.textContent = rWeek != null ? `${fmt(rWeek, 2)} lb/wk` : "—";
    rateEl.className = "stat-value " + signClass(rWeek);
    // A rate without its uncertainty invites reading noise as progress: over a
    // short window the 95% band can straddle zero, and the honest reading is
    // then "cannot tell yet" rather than whatever the point estimate says.
    // provisional means only "too few points to fit a line" — see trend.py.
    const rateSub = $("#s-rate-day");
    if (s.rate_lb_per_day == null) {
      rateSub.textContent = "— lb/day";
      rateSub.title = "";
    } else {
      const se = s.rate_se_lb_per_day;
      const band = se != null ? ` ±${fmt(1.96 * se * 7, 2)} lb/wk` : "";
      rateSub.textContent =
        `${fmt(s.rate_lb_per_day, 3)} lb/day${band}` +
        (s.rate_provisional ? " · provisional" : "");
      rateSub.title =
        se != null
          ? `95% confidence: ${fmt((rWeek - 1.96 * se * 7), 2)} to ` +
            `${fmt((rWeek + 1.96 * se * 7), 2)} lb/wk` +
            (s.rate_window_days != null
              ? ` · fitted over ${fmt(s.rate_window_days, 0)} days`
              : "")
          : "not enough weigh-ins to estimate uncertainty";
    }

    // kcal is rate x 3500 — one signal, not two — so it inherits the rate's
    // uncertainty scaled by the same constant. Worth showing: the band is
    // routinely hundreds of kcal, which is the difference between "eat less"
    // and "you are already fine".
    const kcal = s.kcal_per_day;
    const kEl = $("#s-kcal");
    const kSub = $("#s-kcal-sub");
    if (kcal == null) {
      kEl.textContent = "—";
      kEl.className = "stat-value";
      kEl.title = "";
      if (kSub) kSub.textContent = "kcal/day";
    } else {
      const label = kcal < -1 ? "deficit" : kcal > 1 ? "surplus" : "balanced";
      const seK = s.rate_se_lb_per_day != null ? 1.96 * s.rate_se_lb_per_day * 3500 : null;
      kEl.textContent = `${kcal > 0 ? "+" : ""}${fmt(kcal, 0)}`;
      kEl.className = "stat-value " + signClass(kcal);
      kEl.title =
        `${label} · from a least-squares fit to your weigh-ins` +
        (s.rate_window_days != null ? ` over ${fmt(s.rate_window_days, 0)} days` : "") +
        (seK != null
          ? ` · 95% confidence ${fmt(kcal - seK, 0)} to ${fmt(kcal + seK, 0)}`
          : "");
      if (kSub) {
        kSub.textContent = seK != null ? `kcal/day ±${fmt(seK, 0)}` : "kcal/day";
      }
    }

    // BMI tile (from trend weight + height setting)
    const bmiEl = $("#s-bmi");
    const catEl = $("#s-bmi-cat");
    const rangeEl = $("#s-bmi-range");
    const bar = $("#bmi-bar");
    const marker = $("#bmi-marker");
    const bmi = s.bmi;
    if (bmi && bmi.bmi != null) {
      bmiEl.textContent = fmt(bmi.bmi, 1);
      bmiEl.className = "stat-value bmi-" + (bmi.category_key || "normal");
      catEl.textContent = `${bmi.category} · ${bmi.height_label || ""}`;
      if (bmi.healthy_weight_lb) {
        rangeEl.textContent = `healthy ${fmt(bmi.healthy_weight_lb.low, 0)}–${fmt(bmi.healthy_weight_lb.high, 0)} lb`;
      } else {
        rangeEl.textContent = "";
      }
      if (bar) bar.hidden = false;
      if (marker) marker.style.left = bmiMarkerPct(bmi.bmi) + "%";
    } else {
      bmiEl.textContent = "—";
      bmiEl.className = "stat-value";
      catEl.textContent = "set height in settings";
      rangeEl.textContent = "";
      if (bar) bar.hidden = true;
    }
    if (window.hackDietPhotos) {
      window.hackDietPhotos.setScaleContext({
        series,
        height_in: settings.height_in ?? null,
      });
    }
  }

  function heightPartsFromInches(total) {
    if (total == null || total === "") return { ft: "", inch: "" };
    const n = Number(total);
    if (!Number.isFinite(n)) return { ft: "", inch: "" };
    const ft = Math.floor(n / 12);
    const inch = Math.round((n - ft * 12) * 2) / 2; // nearest 0.5
    return { ft, inch };
  }

  function inchesFromParts(ft, inch) {
    const f = parseFloat(ft);
    const i = parseFloat(inch);
    if (!Number.isFinite(f) && !Number.isFinite(i)) return null;
    const ff = Number.isFinite(f) ? f : 0;
    const ii = Number.isFinite(i) ? i : 0;
    const total = ff * 12 + ii;
    return total > 0 ? total : null;
  }

  function renderSettings() {
    if (settings.half_life_days != null) {
      $("#set-half").value = settings.half_life_days;
    }
    $("#set-goal").value =
      settings.goal_weight != null && settings.goal_weight !== ""
        ? settings.goal_weight
        : "";
    const parts = heightPartsFromInches(settings.height_in);
    $("#set-height-ft").value = parts.ft;
    $("#set-height-in").value = parts.inch;
    if ($("#set-sex")) $("#set-sex").value = settings.sex || "";
    if ($("#set-age")) $("#set-age").value = settings.age != null ? settings.age : "";
    if ($("#set-athlete")) $("#set-athlete").checked = !!settings.athlete;
    if ($("#set-coach-goals")) {
      $("#set-coach-goals").value = settings.coach_goals || "";
    }
  }

  function renderTable() {
    const tbody = $("#hist tbody");
    if (!series.length) {
      tbody.innerHTML = `<tr><td colspan="8" class="empty">No entries yet — log a weight above.</td></tr>`;
      return;
    }
    // newest first
    const rows = [...series].reverse();
    tbody.innerHTML = rows
      .map((e) => {
        const gap =
          e.gap_days == null
            ? "—"
            : e.gap_days < 0.01
              ? "~0"
              : e.gap_days < 1
                ? fmt(e.gap_days * 24, 1) + "h"
                : fmt(e.gap_days, 2) + "d";
        const bf =
          e.body_fat != null
            ? fmt(e.body_fat, 1)
            : e.body_fat_trend != null
              ? "(" + fmt(e.body_fat_trend, 1) + ")"
              : "—";
        // Parenthesised = carried forward from an earlier measurement, same
        // convention the BF column already uses.
        const waist =
          e.waist != null
            ? fmt(e.waist, 1)
            : e.waist_trend != null
              ? "(" + fmt(e.waist_trend, 1) + ")"
              : "—";
        const kcal =
          e.kcal_per_day == null
            ? "—"
            : `${e.kcal_per_day > 0 ? "+" : ""}${fmt(e.kcal_per_day, 0)}`;
        const kClass = signClass(e.kcal_per_day);
        return `<tr data-id="${e.id}">
          <td>${formatWhen(e)}</td>
          <td class="num">${fmt(e.weight, 1)}</td>
          <td class="num">${e.weight == null ? "(" + fmt(e.trend, 2) + ")" : fmt(e.trend, 2)}</td>
          <td class="num">${bf}</td>
          <td class="num">${waist}</td>
          <td class="num">${gap}</td>
          <td class="num ${kClass}">${kcal}</td>
          <td><button type="button" class="linkish edit-btn" data-id="${e.id}">edit</button></td>
        </tr>`;
      })
      .join("");
  }

  function filterSeriesByRange(all) {
    if (range === "all" || !all.length) return all;
    const days = parseInt(range, 10);
    if (!days) return all;
    const last = all[all.length - 1];
    const end = new Date(last.logged_at || last.date + "T12:00:00");
    const start = new Date(end);
    start.setDate(start.getDate() - days);
    return all.filter((e) => new Date(e.logged_at || e.date + "T12:00:00") >= start);
  }

  function trendZoomOptions() {
    if (typeof HdChartZoom === "undefined") return undefined;
    return HdChartZoom.zoomOptions({ mode: "xy", onChange: () => chartZoomUi?.sync() });
  }

  function renderChart() {
    const ctx = $("#chart");
    if (!ctx || typeof Chart === "undefined") return;

    const data = filterSeriesByRange(series);
    const xOf = (e) => e.logged_at || e.date;
    // Waist-only entries carry no weight, and their carried-forward trend would
    // just duplicate the previous point — leave both series to the weigh-ins.
    const weighed = data.filter((e) => e.weight != null);
    const raw = weighed.map((e) => ({ x: xOf(e), y: e.weight }));
    const trend = weighed
      .filter((e) => e.trend != null)
      .map((e) => ({ x: xOf(e), y: e.trend }));
    const bfRaw = data
      .filter((e) => e.body_fat != null)
      .map((e) => ({ x: xOf(e), y: e.body_fat }));
    const bfTrend = data
      .filter((e) => e.body_fat_trend != null)
      .map((e) => ({ x: xOf(e), y: e.body_fat_trend }));
    // Only rows that actually carry a tape measurement. waist_trend is carried
    // forward onto every weigh-in in between, which would draw a staircase
    // rather than a trend.
    const waistRows = data.filter((e) => e.waist != null);
    const waistRaw = waistRows.map((e) => ({ x: xOf(e), y: e.waist }));
    const waistTrend = waistRows
      .filter((e) => e.waist_trend != null)
      .map((e) => ({ x: xOf(e), y: e.waist_trend }));

    const goal = settings.goal_weight;
    const datasets = [
      {
        label: "Weight",
        data: raw,
        yAxisID: "yLb",
        showLine: false,
        pointRadius: 4,
        pointHoverRadius: 6,
        backgroundColor: "rgba(224, 168, 92, 0.9)",
        borderColor: "rgba(224, 168, 92, 0.9)",
        order: 3,
      },
      {
        label: "Weight EMA",
        data: trend,
        yAxisID: "yLb",
        showLine: true,
        pointRadius: 0,
        borderWidth: 2.5,
        borderColor: "rgba(91, 159, 212, 1)",
        backgroundColor: "rgba(91, 159, 212, 0.15)",
        tension: 0.15,
        order: 2,
      },
    ];

    if (bfRaw.length) {
      datasets.push({
        label: "Body fat %",
        data: bfRaw,
        yAxisID: "yBf",
        showLine: false,
        pointRadius: 4,
        pointHoverRadius: 6,
        backgroundColor: "rgba(224, 112, 112, 0.95)",
        borderColor: "rgba(224, 112, 112, 0.95)",
        order: 1,
      });
    }
    if (bfTrend.length) {
      datasets.push({
        label: "BF% EMA",
        data: bfTrend,
        yAxisID: "yBf",
        showLine: true,
        pointRadius: 0,
        borderWidth: 2,
        borderColor: "rgba(240, 140, 140, 0.95)",
        borderDash: [4, 3],
        tension: 0.15,
        order: 0,
      });
    }

    if (waistRaw.length) {
      datasets.push({
        label: "Waist",
        data: waistRaw,
        yAxisID: "yIn",
        showLine: false,
        pointRadius: 4,
        pointHoverRadius: 6,
        pointStyle: "rectRot",
        backgroundColor: "rgba(167, 139, 250, 0.95)",
        borderColor: "rgba(167, 139, 250, 0.95)",
        order: 1,
      });
    }
    // One measurement is a dot, not a trend — no line until there are two.
    if (waistTrend.length > 1) {
      datasets.push({
        label: "Waist EMA",
        data: waistTrend,
        yAxisID: "yIn",
        showLine: true,
        pointRadius: 0,
        borderWidth: 2,
        borderColor: "rgba(167, 139, 250, 0.85)",
        borderDash: [2, 3],
        tension: 0.15,
        order: 0,
      });
    }

    if (goal != null && goal !== "" && data.length) {
      datasets.push({
        label: "Goal weight",
        data: [
          { x: xOf(data[0]), y: goal },
          { x: xOf(data[data.length - 1]), y: goal },
        ],
        yAxisID: "yLb",
        showLine: true,
        pointRadius: 0,
        borderWidth: 1.5,
        borderDash: [6, 4],
        borderColor: "rgba(125, 211, 160, 0.7)",
        order: 4,
      });
    }

    // Waist moves in tenths of an inch; without a floor on the span a flat
    // fortnight would fill the whole axis and look like a collapse.
    const showWaist = waistRaw.length > 0;
    const waistAxis = (() => {
      const vals = waistRaw.concat(waistTrend).map((p) => p.y);
      if (!vals.length) return { min: 28, max: 44 };
      let min = Math.floor(Math.min.apply(null, vals) - 1);
      let max = Math.ceil(Math.max.apply(null, vals) + 1);
      if (max - min < 6) {
        const mid = (max + min) / 2;
        min = Math.floor(mid - 3);
        max = Math.ceil(mid + 3);
      }
      return { min: Math.max(0, min), max };
    })();

    const showBf = bfRaw.length > 0 || bfTrend.length > 0;
    const bfSamples = bfRaw.concat(bfTrend).map((p) => p.y);
    const bfAxis =
      typeof HdBfAxis !== "undefined"
        ? HdBfAxis.bodyFatAxisRange(settings.sex, settings.age, bfSamples)
        : { min: 6, max: 35, label: "lean→obese" };

    const bfHint = $("#chart-bf-hint");
    if (bfHint) {
      bfHint.textContent = showBf
        ? `BF axis fixed to ${bfAxis.label} — not auto-zoomed to today's range.`
        : "";
    }

    if (chart) {
      chart.data.datasets = datasets;
      chart.options.scales.yBf.display = showBf;
      chart.options.scales.yIn.display = showWaist;
      // The band is the default view, not a cage — leave a manual zoom alone.
      // Re-applying it here would also desync the plugin's saved "original"
      // bounds, so reset would no longer land where the user started.
      if (!chart.isZoomedOrPanned?.()) {
        chart.options.scales.yBf.min = bfAxis.min;
        chart.options.scales.yBf.max = bfAxis.max;
        chart.options.scales.yIn.min = waistAxis.min;
        chart.options.scales.yIn.max = waistAxis.max;
      }
      chart.update("none");
      chartZoomUi?.sync();
      return;
    }

    chart = new Chart(ctx, {
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
          tooltip: {
            callbacks: {
              label(ctx) {
                const v = ctx.parsed.y;
                const id = ctx.dataset.yAxisID;
                if (id === "yBf") {
                  return `${ctx.dataset.label}: ${Number(v).toFixed(1)}%`;
                }
                if (id === "yIn") {
                  return `${ctx.dataset.label}: ${Number(v).toFixed(1)} in`;
                }
                return `${ctx.dataset.label}: ${Number(v).toFixed(2)} lb`;
              },
            },
          },
          zoom: trendZoomOptions(),
        },
        scales: {
          x: {
            type: "time",
            time: { tooltipFormat: "yyyy-MM-dd HH:mm" },
            grid: { color: "rgba(120,160,220,0.08)" },
            ticks: { color: "#8b93a7", maxRotation: 0, autoSkipPadding: 16 },
          },
          yLb: {
            position: "left",
            grid: { color: "rgba(120,160,220,0.08)" },
            ticks: {
              color: "#8b93a7",
              callback: (v) => v + " lb",
            },
            title: {
              display: true,
              text: "Weight",
              color: "#5b9fd4",
            },
          },
          yIn: {
            position: "right",
            display: showWaist,
            min: waistAxis.min,
            max: waistAxis.max,
            grid: { drawOnChartArea: false },
            ticks: {
              color: "#a78bfa",
              callback: (v) => v + '"',
            },
            title: {
              display: true,
              text: "Waist",
              color: "#a78bfa",
            },
          },
          yBf: {
            position: "right",
            display: showBf,
            min: bfAxis.min,
            max: bfAxis.max,
            grid: { drawOnChartArea: false },
            ticks: {
              color: "#e07070",
              callback: (v) => v + "%",
            },
            title: {
              display: true,
              text: "Body fat",
              color: "#e07070",
            },
          },
        },
        onResize: () => chartZoomUi?.sync(),
      },
    });

    if (!chartZoomUi && typeof HdChartZoom !== "undefined") {
      chartZoomUi = HdChartZoom.wireResetButton($("#chart-zoom-reset"), () => chart);
    }
  }

  // --- events ---

  $("#log-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const msg = $("#form-msg");
    msg.hidden = true;
    const bfRaw = $("#f-bf")?.value;
    const waistRaw = $("#f-waist")?.value;
    const body = {
      logged_at: fromLocalInput($("#f-when").value),
      weight: parseFloat($("#f-weight").value),
      body_fat: bfRaw === "" || bfRaw == null ? null : parseFloat(bfRaw),
      waist: waistRaw === "" || waistRaw == null ? null : parseFloat(waistRaw),
      note: $("#f-note").value || null,
    };
    try {
      const data = await api("/api/weights", {
        method: "POST",
        body: JSON.stringify(body),
      });
      applyState(data);
      $("#f-note").value = "";
      if ($("#f-bf")) $("#f-bf").value = "";
      if ($("#f-waist")) $("#f-waist").value = "";
      msg.textContent = `Saved ${fmt(body.weight, 1)} lb`;
      msg.className = "hint ok";
      msg.hidden = false;
      // unpin old AI copy so rule-based mood can update, then optional auto-pep
      aiPinned = false;
      renderCoach();
      celebrateLog(data);
      if ($("#auto-pep")?.checked) {
        requestPepTalk({ celebrate: true }).catch(() => {});
      }
    } catch (e) {
      msg.textContent = e.message;
      msg.className = "hint err";
      msg.hidden = false;
    }
  });

  // Waist on its own: weight arrives from the scale on its own schedule, so a
  // tape measurement should not have to be edited onto someone else's row.
  $("#waist-form")?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const msg = $("#waist-msg");
    msg.hidden = true;
    const raw = $("#w-waist").value;
    if (raw === "") return;
    const body = {
      logged_at: fromLocalInput($("#w-when").value),
      waist: parseFloat(raw),
    };
    try {
      const data = await api("/api/weights", {
        method: "POST",
        body: JSON.stringify(body),
      });
      applyState(data);
      $("#w-waist").value = "";
      $("#w-when").value = nowLocalInput();
      msg.textContent = `Logged ${fmt(body.waist, 1)} in`;
      msg.className = "hint ok";
      msg.hidden = false;
    } catch (e) {
      msg.textContent = e.message;
      msg.className = "hint err";
      msg.hidden = false;
    }
  });

  $("#settings-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const half = parseFloat($("#set-half").value);
    const goalRaw = $("#set-goal").value;
    const heightIn = inchesFromParts(
      $("#set-height-ft").value,
      $("#set-height-in").value
    );
    const ageRaw = $("#set-age")?.value;
    const coachGoalsRaw = ($("#set-coach-goals")?.value || "").trim();
    const body = {
      half_life_days: half,
      goal_weight: goalRaw === "" ? null : parseFloat(goalRaw),
      height_in: heightIn,
      sex: $("#set-sex")?.value || null,
      age: ageRaw === "" || ageRaw == null ? null : parseInt(ageRaw, 10),
      athlete: !!$("#set-athlete")?.checked,
      coach_goals: coachGoalsRaw || null,
    };
    const msg = $("#settings-msg");
    try {
      const data = await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify(body),
      });
      applyState(data);
      settings = data.settings || settings;
      renderSettings();
      if (msg) {
        const focus = (settings.coach_goals || "").trim();
        msg.textContent = focus
          ? `Saved — coach focus: “${focus.slice(0, 80)}”`
          : "Saved.";
        msg.className = "hint";
        msg.hidden = false;
      }
    } catch (e) {
      if (msg) {
        msg.textContent = e.message || "Save failed";
        msg.className = "hint err";
        msg.hidden = false;
      } else {
        alert(e.message);
      }
    }
  });

  async function loadIngestTokens() {
    const list = $("#ingest-list");
    if (!list) return;
    try {
      const data = await api("/api/ingest-tokens");
      list.innerHTML = "";
      const tokens = data.tokens || [];
      if (!tokens.length) {
        list.innerHTML = "<li>No active tokens yet.</li>";
        return;
      }
      for (const t of tokens) {
        const li = document.createElement("li");
        const used = t.last_used_at ? ` · last used ${t.last_used_at.slice(0, 16)}` : " · never used";
        li.innerHTML = `<code>#${t.id}</code> ${t.label || "token"}${used} `;
        const rev = document.createElement("button");
        rev.type = "button";
        rev.className = "btn ghost";
        rev.textContent = "Revoke";
        rev.style.padding = "2px 8px";
        rev.style.fontSize = "12px";
        rev.addEventListener("click", async () => {
          if (!confirm(`Revoke token #${t.id}?`)) return;
          try {
            await api(`/api/ingest-tokens/${t.id}`, { method: "DELETE" });
            await loadIngestTokens();
          } catch (e) {
            alert(e.message);
          }
        });
        li.appendChild(rev);
        list.appendChild(li);
      }
    } catch (_) {
      list.innerHTML = "<li>Could not load tokens.</li>";
    }
  }

  $("#btn-ingest-create")?.addEventListener("click", async () => {
    const msg = $("#ingest-msg");
    try {
      const data = await api("/api/ingest-tokens", {
        method: "POST",
        body: JSON.stringify({ label: ($("#ingest-label")?.value || "").trim() || null }),
      });
      if (msg) {
        msg.hidden = false;
        msg.className = "msg ok";
        msg.textContent = `Token created — copy now: ${data.token}`;
      }
      if ($("#ingest-label")) $("#ingest-label").value = "";
      await loadIngestTokens();
      try {
        await navigator.clipboard.writeText(data.token);
        if (msg) msg.textContent += " (copied to clipboard)";
      } catch (_) {}
    } catch (e) {
      if (msg) {
        msg.hidden = false;
        msg.className = "msg err";
        msg.textContent = e.message;
      } else {
        alert(e.message);
      }
    }
  });

  $$("#range-tabs .tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      $$("#range-tabs .tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      range = btn.dataset.range;
      // A new time window is a fresh view — drop any manual zoom with it.
      chart?.resetZoom?.("none");
      renderChart();
    });
  });

  $("#hist").addEventListener("click", (ev) => {
    const btn = ev.target.closest(".edit-btn");
    if (!btn) return;
    const id = parseInt(btn.dataset.id, 10);
    const entry = series.find((e) => e.id === id);
    if (!entry) return;
    $("#e-id").value = entry.id;
    $("#e-when").value = toLocalInput(entry.logged_at || entry.date);
    $("#e-weight").value = entry.weight != null ? entry.weight : "";
    $("#e-bf").value = entry.body_fat != null ? entry.body_fat : "";
    if ($("#e-waist")) $("#e-waist").value = entry.waist != null ? entry.waist : "";
    $("#e-note").value = entry.note || "";
    $("#edit-dialog").showModal();
  });

  $("#e-cancel").addEventListener("click", () => $("#edit-dialog").close());

  $("#edit-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const id = parseInt($("#e-id").value, 10);
    const bfRaw = $("#e-bf").value;
    const waistRaw = $("#e-waist")?.value ?? "";
    const weightRaw = $("#e-weight").value;
    const body = {
      logged_at: fromLocalInput($("#e-when").value),
      weight: weightRaw === "" ? null : parseFloat(weightRaw),
      body_fat: bfRaw === "" ? null : parseFloat(bfRaw),
      waist: waistRaw === "" ? null : parseFloat(waistRaw),
      note: $("#e-note").value || null,
    };
    try {
      const data = await api(`/api/weights/${id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      });
      applyState(data);
      $("#edit-dialog").close();
    } catch (e) {
      alert(e.message);
    }
  });

  $("#e-delete").addEventListener("click", async () => {
    const id = parseInt($("#e-id").value, 10);
    if (!confirm("Delete this weigh-in?")) return;
    try {
      const data = await api(`/api/weights/${id}`, { method: "DELETE" });
      applyState(data);
      $("#edit-dialog").close();
    } catch (e) {
      alert(e.message);
    }
  });

  $("#import-zip")?.addEventListener("change", async (ev) => {
    const input = ev.target;
    const file = input.files && input.files[0];
    input.value = "";
    const msg = $("#import-msg");
    if (!file) return;
    const ok = confirm(
      "Import this backup ZIP into your account?\n\n" +
        "Matching weigh-ins and photos are updated (not duplicated).\n" +
        "New items are added. Settings keys in the ZIP overwrite yours."
    );
    if (!ok) return;
    const fd = new FormData();
    fd.append("file", file);
    try {
      if (msg) {
        msg.hidden = false;
        msg.className = "hint";
        msg.textContent = "Importing…";
      }
      const res = await fetch("/api/import", { method: "POST", body: fd, credentials: "include" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
      applyState(data);
      if (data.settings) settings = data.settings;
      if (window.hackDietPhotos && data.photo_series) {
        window.hackDietPhotos.setPhotoSeries(data.photo_series);
      }
      const im = data.import || {};
      const parts = [
        `settings ${im.settings_upserted || 0}`,
        `weights +${im.weights_inserted || 0}/~${im.weights_updated || 0}`,
        `photos +${im.photos_inserted || 0}/~${im.photos_updated || 0}`,
      ];
      if (msg) {
        msg.hidden = false;
        msg.className = "hint";
        msg.style.color = "var(--accent-2, inherit)";
        msg.textContent = "Import OK — " + parts.join(" · ");
        if ((im.errors || []).length) {
          msg.textContent += ` (${im.errors.length} warning(s))`;
        }
      }
      await loadAll();
    } catch (e) {
      if (msg) {
        msg.hidden = false;
        msg.className = "hint err";
        msg.textContent = e.message || String(e);
      } else {
        alert(e.message || String(e));
      }
    }
  });

  $("#export-csv").addEventListener("click", () => {
    const header =
      "date,weight_lb,trend_lb,body_fat_pct,waist_in,gap_days,alpha," +
      "rate_lb_per_day,kcal_per_day,note\n";
    const lines = series.map((e) =>
      [
        e.date,
        e.weight,
        e.trend,
        e.body_fat ?? "",
        e.waist ?? "",
        e.gap_days,
        e.alpha,
        e.rate_lb_per_day,
        e.kcal_per_day,
        JSON.stringify(e.note || ""),
      ].join(",")
    );
    const blob = new Blob([header + lines.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `trend-${todayISO()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  });

  $("#btn-logout")?.addEventListener("click", async () => {
    try {
      await api("/api/auth/logout", { method: "POST", body: "{}" });
    } catch (_) {}
    location.href = "/login.html";
  });

  // AI coach controls
  $("#btn-pep")?.addEventListener("click", () => {
    requestPepTalk().catch(() => {});
  });

  $$("#style-tabs .tab").forEach((btn) => {
    if (btn.dataset.style === coachStyle) {
      $$("#style-tabs .tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
    }
    btn.addEventListener("click", () => {
      $$("#style-tabs .tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      coachStyle = btn.dataset.style;
      localStorage.setItem("hd_coach_style", coachStyle);
    });
  });

  const autoPep = $("#auto-pep");
  if (autoPep) {
    autoPep.checked = localStorage.getItem("hd_auto_pep") === "1";
    autoPep.addEventListener("change", () => {
      localStorage.setItem("hd_auto_pep", autoPep.checked ? "1" : "0");
    });
  }

  // init
  if ($("#f-when")) $("#f-when").value = nowLocalInput();
  if ($("#w-when")) $("#w-when").value = nowLocalInput();
  Promise.all([loadAll(), loadCachedCoach(), refreshKoboldStatus()])
    .then(() => {
      if (aiCoach) renderCoach();
    })
    .catch((e) => {
      console.error(e);
      $("#form-msg").textContent = "Failed to load: " + e.message;
      $("#form-msg").className = "hint err";
      $("#form-msg").hidden = false;
    });
})();
