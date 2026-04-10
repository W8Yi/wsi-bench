const state = {
  models: [],
  encoders: [],
  selectedEncoder: "",
  selectedModelId: "",
  representatives: [],
  filteredRepresentatives: [],
  analytics: {
    available: false,
    summary: {},
    metrics: [],
    umap: [],
  },
  selectedSlideKey: "",
  selectedLatentIdx: null,
  selectedLatentStrategy: "",
  representativeStrategy: "",
  representativeMethod: "max_activation",
  showTechnical: false,
  scatterPoints: [],
  umapPoints: [],
};

const el = {
  encoderSelect: document.getElementById("encoderSelect"),
  modelSelect: document.getElementById("modelSelect"),
  repSearch: document.getElementById("repSearch"),
  refreshBtn: document.getElementById("refreshBtn"),
  summary: document.getElementById("summary"),
  repStrategySelect: document.getElementById("repStrategySelect"),
  repMethodSelect: document.getElementById("repMethodSelect"),
  repMeta: document.getElementById("repMeta"),
  repCards: document.getElementById("repCards"),
  latentGroupSelect: document.getElementById("latentGroupSelect"),
  clearLatentBtn: document.getElementById("clearLatentBtn"),
  detailBody: document.getElementById("detailBody"),
  toggleTechBtn: document.getElementById("toggleTechBtn"),
  modelHeading: document.getElementById("modelHeading"),
  modelNarrative: document.getElementById("modelNarrative"),
  focusMeta: document.getElementById("focusMeta"),
  prevalenceCanvas: document.getElementById("prevalenceCanvas"),
  umapCanvas: document.getElementById("umapCanvas"),
  histCanvas: document.getElementById("histCanvas"),
  scatterMeta: document.getElementById("scatterMeta"),
  umapMeta: document.getElementById("umapMeta"),
  latentProfile: document.getElementById("latentProfile"),
  methodStrip: document.getElementById("methodStrip"),
  cohortRows: document.getElementById("cohortRows"),
};

function esc(v) {
  return String(v)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function q(params) {
  const sp = new URLSearchParams(params);
  return sp.toString();
}

function badge(ok, text) {
  return `<span class="badge ${ok ? "ok" : "warn"}">${esc(text)}</span>`;
}

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const txt = await res.text();
    throw new Error(txt || `HTTP ${res.status}`);
  }
  return res.json();
}

function filteredModels() {
  if (!state.selectedEncoder) return state.models;
  return state.models.filter((m) => m.encoder === state.selectedEncoder);
}

function renderEncoderSelect() {
  el.encoderSelect.innerHTML = "";
  for (const enc of state.encoders) {
    const opt = document.createElement("option");
    opt.value = enc;
    opt.textContent = enc;
    el.encoderSelect.appendChild(opt);
  }
  if (!state.selectedEncoder && state.encoders.length > 0) {
    state.selectedEncoder = state.encoders[0];
  }
  el.encoderSelect.value = state.selectedEncoder;
}

function renderModelSelect() {
  const models = filteredModels();
  el.modelSelect.innerHTML = "";
  for (const m of models) {
    const opt = document.createElement("option");
    opt.value = m.model_id;
    opt.textContent = `${m.model_name} (${m.dataset || "dataset"})`;
    el.modelSelect.appendChild(opt);
  }
  if (!models.find((m) => m.model_id === state.selectedModelId)) {
    state.selectedModelId = models.length > 0 ? models[0].model_id : "";
  }
  el.modelSelect.value = state.selectedModelId;
}

function renderSummary(summary, config, analyticsSummary) {
  const chips = [
    ["Model", summary.model_name || config.model_name || summary.model_id || "-"],
    ["Encoder", summary.encoder || "-"],
    ["Dataset", summary.dataset || "-"],
    ["Slides", summary.total_slides || 0],
    ["Cases", analyticsSummary.total_cases || summary.total_cases || 0],
    ["Alive latents", analyticsSummary.alive_latents || 0],
    ["Representative rows", summary.total_representative_rows || 0],
    ["Support rows", summary.total_support_rows || 0],
    ["Selected union", analyticsSummary.selected_latent_union || 0],
    ["Rep slide coverage", `${Number(summary.rep_slide_coverage || 0).toFixed(1)}%`],
    ["Median activation", Number(summary.activation_p50 || 0).toFixed(3)],
    ["Tail ratio", Number(summary.activation_tail_ratio || 0).toFixed(3)],
  ];
  el.summary.innerHTML = chips.map(([k, v]) => `<div class="metric"><strong>${esc(k)}</strong><span>${esc(v)}</span></div>`).join("");

  el.modelHeading.textContent = summary.model_name || config.model_name || "SAE Atlas";
  const splitText = analyticsSummary.split ? `The current atlas is built on the ${analyticsSummary.split} split. ` : "";
  const analyticsText = state.analytics.available
    ? `Analytics include prevalence, latent geometry, cohort enrichment, and slide-max histograms.`
    : `Analytics bundle not loaded yet, so this view falls back to representative tiles and slide detail only.`;
  el.modelNarrative.textContent = `${splitText}${analyticsText}`;
}

function renderRepresentativeMethodSelect(methods) {
  const available = (methods || []).filter(Boolean);
  const current = state.representativeMethod || "max_activation";
  el.repMethodSelect.innerHTML = "";
  for (const m of available.length > 0 ? available : ["max_activation"]) {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m.replaceAll("_", " ");
    el.repMethodSelect.appendChild(opt);
  }
  if (![...el.repMethodSelect.options].some((o) => o.value === current)) {
    state.representativeMethod = el.repMethodSelect.options[0]?.value || "max_activation";
  }
  el.repMethodSelect.value = state.representativeMethod;
}

function renderRepresentativeStrategySelect(strategies) {
  const available = (strategies || []).filter(Boolean);
  const current = state.representativeStrategy || "";
  el.repStrategySelect.innerHTML = `<option value="">All strategies</option>`;
  for (const s of available) {
    const opt = document.createElement("option");
    opt.value = s;
    opt.textContent = s.replaceAll("_", " ");
    el.repStrategySelect.appendChild(opt);
  }
  if (![...el.repStrategySelect.options].some((o) => o.value === current)) {
    state.representativeStrategy = "";
  }
  el.repStrategySelect.value = state.representativeStrategy;
}

function renderLatentGroupSelect(rows) {
  const groups = Array.from(new Set(rows.map((r) => r.latent_group).filter(Boolean))).sort();
  const current = el.latentGroupSelect.value || "";
  el.latentGroupSelect.innerHTML = `<option value="">All groups</option>`;
  for (const g of groups) {
    const opt = document.createElement("option");
    opt.value = g;
    opt.textContent = g;
    el.latentGroupSelect.appendChild(opt);
  }
  if (groups.includes(current)) {
    el.latentGroupSelect.value = current;
  }
}

function applyRepresentativeFilter() {
  const group = (el.latentGroupSelect.value || "").toLowerCase();
  const qv = (el.repSearch.value || "").trim().toLowerCase();
  state.filteredRepresentatives = state.representatives.filter((r) => {
    if (group && String(r.latent_group || "").toLowerCase() !== group) return false;
    if (!qv) return true;
    return (
      String(r.latent_idx).toLowerCase().includes(qv)
      || String(r.slide_key || "").toLowerCase().includes(qv)
      || String(r.case_id || "").toLowerCase().includes(qv)
      || String(r.latent_strategy || "").toLowerCase().includes(qv)
    );
  });
}

function tileUrl(tile, slideKey, tileSize) {
  return `/api/sae/tile?${q({
    model_id: state.selectedModelId,
    slide_key: slideKey,
    x: tile.coord_x,
    y: tile.coord_y,
    size: tileSize,
    tile_index: tile.tile_index,
  })}`;
}

function currentRepresentativeForFocus() {
  return state.representatives.find((r) =>
    r.latent_idx === state.selectedLatentIdx
    && String(r.latent_strategy || "") === String(state.selectedLatentStrategy || "")
    && String(r.representative_method || "") === String(state.representativeMethod || "")
  ) || state.representatives.find((r) =>
    r.latent_idx === state.selectedLatentIdx
    && String(r.latent_strategy || "") === String(state.selectedLatentStrategy || "")
  ) || null;
}

function renderFocusMeta() {
  if (state.selectedLatentIdx === null) {
    el.focusMeta.innerHTML = `<span class="meta-pill">No latent selected</span>`;
    return;
  }
  const rep = currentRepresentativeForFocus();
  const pills = [
    `<span class="meta-pill">latent ${esc(state.selectedLatentIdx)}</span>`,
    `<span class="meta-pill">${esc(state.selectedLatentStrategy || "strategy not set")}</span>`,
  ];
  if (rep?.slide_key) pills.push(`<span class="meta-pill">${esc(rep.slide_key)}</span>`);
  el.focusMeta.innerHTML = pills.join("");
}

function renderRepresentatives() {
  applyRepresentativeFilter();
  const rows = state.filteredRepresentatives;
  const maxScore = Math.max(...rows.map((r) => Number(r.method_score || r.activation || 0)), 1);
  el.repMeta.textContent = `${rows.length} / ${state.representatives.length}`;
  el.repCards.innerHTML = "";

  if (rows.length === 0) {
    el.repCards.innerHTML = `<p class="meta">No representative latents for the current filter.</p>`;
    return;
  }

  for (const r of rows) {
    const article = document.createElement("article");
    article.className = "rep-card";
    if (state.selectedLatentIdx === r.latent_idx && state.selectedLatentStrategy === (r.latent_strategy || "")) {
      article.classList.add("selected");
    }
    const width = `${Math.max(8, (Number(r.method_score || r.activation || 0) / maxScore) * 100)}%`;
    article.innerHTML = `
      <img loading="lazy" src="${tileUrl(r, r.slide_key, 224)}" alt="representative tile" />
      <div class="rep-body">
        <div class="rep-title">latent ${esc(r.latent_idx)} <span>${esc(r.latent_group || "-")}</span></div>
        <div class="rep-statline">${esc(r.latent_strategy || "-")} • ${esc(r.representative_method || state.representativeMethod || "-")}</div>
        <div class="rep-statline">score ${Number(r.method_score || r.activation || 0).toFixed(3)} • activation ${Number(r.activation || 0).toFixed(3)}</div>
        <div class="spark"><span style="width:${width}"></span></div>
        <div class="rep-slide">${esc(r.slide_key || "-")}</div>
      </div>
    `;
    article.addEventListener("click", () => {
      selectLatent(r.latent_idx, r.latent_strategy || "", r.slide_key || "");
    });
    el.repCards.appendChild(article);
  }
}

function resizeCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(320, Math.floor(rect.width || canvas.width || 320));
  const height = Math.max(220, Math.floor(rect.height || canvas.height || 220));
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, width, height };
}

function drawEmptyCanvas(canvas, title) {
  const { ctx, width, height } = resizeCanvas(canvas);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#efe6d8";
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = "#5f696a";
  ctx.font = "600 16px 'Avenir Next', sans-serif";
  ctx.fillText(title, 24, height / 2);
}

function nearestPoint(points, x, y, threshold = 16) {
  let best = null;
  let bestDist = threshold * threshold;
  for (const point of points) {
    const dx = point.cx - x;
    const dy = point.cy - y;
    const dist = dx * dx + dy * dy;
    if (dist <= bestDist) {
      best = point;
      bestDist = dist;
    }
  }
  return best;
}

function selectLatent(latentIdx, strategy, slideKey = "") {
  state.selectedLatentIdx = latentIdx;
  state.selectedLatentStrategy = strategy || "";
  state.selectedSlideKey = slideKey || "";
  renderRepresentatives();
  renderFocusMeta();
  redrawCharts();
  loadFocusData();
}

function pickStrategyForMetric(metric) {
  const selected = Array.isArray(metric.selected_strategies) ? metric.selected_strategies : [];
  if (state.representativeStrategy && selected.includes(state.representativeStrategy)) {
    return state.representativeStrategy;
  }
  return selected[0] || "";
}

function drawScatter() {
  const rows = state.analytics.metrics || [];
  if (!rows.length) {
    drawEmptyCanvas(el.prevalenceCanvas, "Analytics not available for this model.");
    state.scatterPoints = [];
    return;
  }
  const { ctx, width, height } = resizeCanvas(el.prevalenceCanvas);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#fffdf8";
  ctx.fillRect(0, 0, width, height);

  const margin = { left: 58, right: 24, top: 20, bottom: 42 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  const maxX = Math.max(...rows.map((r) => Number(r.slide_prevalence || 0)), 0.05);
  const maxY = Math.max(...rows.map((r) => Number(r.mean_positive_activation || 0)), 0.05);

  ctx.strokeStyle = "#cabda8";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(margin.left, margin.top);
  ctx.lineTo(margin.left, margin.top + plotH);
  ctx.lineTo(margin.left + plotW, margin.top + plotH);
  ctx.stroke();

  ctx.fillStyle = "#6d6558";
  ctx.font = "12px 'Avenir Next', sans-serif";
  ctx.fillText("slide prevalence", margin.left + plotW - 96, height - 12);
  ctx.save();
  ctx.translate(16, margin.top + plotH / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText("mean positive activation", 0, 0);
  ctx.restore();

  const selectedSet = new Set(state.representatives.map((r) => `${r.latent_idx}`));
  state.scatterPoints = rows.map((row) => {
    const x = margin.left + (Number(row.slide_prevalence || 0) / maxX) * plotW;
    const y = margin.top + plotH - (Number(row.mean_positive_activation || 0) / maxY) * plotH;
    return { ...row, cx: x, cy: y };
  });

  for (const point of state.scatterPoints) {
    const isFocused = point.latent_idx === state.selectedLatentIdx;
    const isSelected = selectedSet.has(String(point.latent_idx));
    ctx.beginPath();
    ctx.fillStyle = isFocused ? "#c26d2d" : (isSelected ? "rgba(39,79,75,0.78)" : "rgba(113, 115, 109, 0.24)");
    ctx.arc(point.cx, point.cy, isFocused ? 4.5 : (isSelected ? 2.8 : 1.8), 0, Math.PI * 2);
    ctx.fill();
  }

  el.scatterMeta.textContent = `${rows.length} latents • click a point to focus`;
}

function drawUmap() {
  const rows = state.analytics.umap || [];
  if (!rows.length) {
    drawEmptyCanvas(el.umapCanvas, "UMAP not available for this model.");
    state.umapPoints = [];
    return;
  }
  const { ctx, width, height } = resizeCanvas(el.umapCanvas);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#fffdf8";
  ctx.fillRect(0, 0, width, height);

  const margin = 20;
  const xs = rows.map((r) => Number(r.umap_x || 0));
  const ys = rows.map((r) => Number(r.umap_y || 0));
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);

  state.umapPoints = rows.map((row) => {
    const x = margin + ((Number(row.umap_x || 0) - minX) / Math.max(maxX - minX, 1e-6)) * (width - margin * 2);
    const y = margin + ((Number(row.umap_y || 0) - minY) / Math.max(maxY - minY, 1e-6)) * (height - margin * 2);
    return { ...row, cx: x, cy: y };
  });

  for (const point of state.umapPoints) {
    const selectedStrategies = Array.isArray(point.selected_strategies) ? point.selected_strategies : [];
    const isFocused = point.latent_idx === state.selectedLatentIdx;
    const isSelected = selectedStrategies.length > 0;
    ctx.beginPath();
    ctx.fillStyle = isFocused ? "#c26d2d" : (isSelected ? "rgba(144, 99, 45, 0.74)" : "rgba(120, 128, 125, 0.22)");
    ctx.arc(point.cx, point.cy, isFocused ? 4.5 : (isSelected ? 2.6 : 1.8), 0, Math.PI * 2);
    ctx.fill();
  }

  el.umapMeta.textContent = `${rows.length} alive latents • selected latents highlighted`;
}

function drawHistogram(hist) {
  if (!hist || !Array.isArray(hist.counts) || !hist.counts.length) {
    drawEmptyCanvas(el.histCanvas, "No histogram available for the selected latent.");
    return;
  }
  const { ctx, width, height } = resizeCanvas(el.histCanvas);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#fffdf8";
  ctx.fillRect(0, 0, width, height);

  const margin = { left: 34, right: 14, top: 16, bottom: 30 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  const maxCount = Math.max(...hist.counts, 1);
  const barW = plotW / hist.counts.length;
  ctx.fillStyle = "rgba(39,79,75,0.85)";
  hist.counts.forEach((count, idx) => {
    const h = (Number(count) / maxCount) * plotH;
    ctx.fillRect(margin.left + idx * barW + 1, margin.top + plotH - h, Math.max(1, barW - 2), h);
  });
  ctx.fillStyle = "#6d6558";
  ctx.font = "12px 'Avenir Next', sans-serif";
  ctx.fillText(hist.histogram_unit || "slide_max_activation", margin.left, height - 8);
}

function renderLatentProfile(detail) {
  const metric = detail.metric_row || {};
  const summary = detail.summary_row || {};
  const cards = [
    ["Latent", detail.latent_idx],
    ["Strategy", detail.strategy || "n/a"],
    ["Slide prevalence", Number(metric.slide_prevalence || 0).toFixed(3)],
    ["Case prevalence", Number(metric.case_prevalence || 0).toFixed(3)],
    ["Mean positive activation", Number(metric.mean_positive_activation || summary.mean_activation || 0).toFixed(3)],
    ["Cohort entropy", Number(metric.cohort_entropy || 0).toFixed(3)],
    ["Support tiles", summary.count || 0],
    ["Unique slides", summary.unique_slides || 0],
  ];
  el.latentProfile.innerHTML = `
    <div class="profile-grid">
      ${cards.map(([k, v]) => `<div class="profile-card"><strong>${esc(k)}</strong><span>${esc(v)}</span></div>`).join("")}
    </div>
  `;

  const reps = detail.representatives || [];
  if (!reps.length) {
    el.methodStrip.innerHTML = `<p class="meta">No representative rows were found for this latent.</p>`;
  } else {
    el.methodStrip.innerHTML = reps.map((r) => `
      <article class="method-card">
        <img loading="lazy" src="${tileUrl(r, r.slide_key, 128)}" alt="representative ${esc(r.representative_method)}" />
        <div>
          <strong>${esc((r.representative_method || "").replaceAll("_", " "))}</strong>
          <div class="meta">score ${Number(r.method_score || 0).toFixed(3)} • activation ${Number(r.activation || 0).toFixed(3)}</div>
          <div class="meta">${esc(r.slide_key || "-")}</div>
        </div>
      </article>
    `).join("");
  }

  drawHistogram(detail.histogram || {});
  renderCohortTable(detail.cohort_rows || []);
}

function renderCohortTable(rows) {
  if (!rows.length) {
    el.cohortRows.innerHTML = `<tr><td colspan="4" class="meta">No cohort enrichment rows for the selected latent.</td></tr>`;
    return;
  }
  el.cohortRows.innerHTML = rows.slice(0, 12).map((r) => `
    <tr>
      <td>${esc(r.cohort || "-")}</td>
      <td>${Number(r.enrichment_ratio || 0).toFixed(2)}</td>
      <td>${Number(r.prevalence_in_cohort || 0).toFixed(3)}</td>
      <td>${esc(r.slides_with_activation || 0)} / ${esc(r.slides_in_cohort || 0)}</td>
    </tr>
  `).join("");
}

function renderSlideDetail(data, detail) {
  const slide = data.slide || {};
  const slidePath = slide.slide_path || "(not resolved)";
  const topLatents = data.top_latents || [];
  let tiles = data.tiles || [];
  if (state.selectedLatentIdx !== null) {
    tiles = tiles.filter((t) => t.latent_idx === state.selectedLatentIdx);
  }
  const slideStatsMap = new Map((detail?.slide_stats || []).map((row) => [row.slide_key, row]));
  const topSlideRows = (detail?.slide_stats || []).slice(0, 10).map((row) => `
    <tr class="${row.slide_key === slide.slide_key ? "is-current" : ""}">
      <td>${esc(row.slide_key)}</td>
      <td>${Number(row.slide_max_activation || 0).toFixed(3)}</td>
      <td>${esc(row.positive_tile_count || 0)}</td>
      <td>${esc(row.cohort || "-")}</td>
    </tr>
  `).join("");
  const tileCards = tiles.slice(0, 24).map((t) => {
    const strategy = t.latent_strategy ? ` • ${esc(t.latent_strategy)}` : "";
    const method = t.representative_method ? ` • ${esc(t.representative_method)}` : "";
    const stat = `activation ${Number(t.activation || 0).toFixed(3)}`;
    return `
      <article class="tile-card">
        <img loading="lazy" src="${tileUrl(t, slide.slide_key, data.tile_size || 256)}" alt="support tile" />
        <div class="tile-meta">
          <div><strong>${esc(t.source || "support")}</strong> ${esc(stat)}${strategy}${method}</div>
          <div>latent ${t.latent_idx === null || t.latent_idx === undefined ? "-" : esc(t.latent_idx)}</div>
          <div>idx ${esc(t.tile_index)} @ (${esc(t.coord_x)}, ${esc(t.coord_y)})</div>
        </div>
      </article>
    `;
  }).join("");

  const slideSpecific = slideStatsMap.get(slide.slide_key) || {};
  el.detailBody.innerHTML = `
    <div class="detail-stack">
      <section class="kv-grid">
        <div class="kv-card"><strong>Slide</strong><span>${esc(slide.slide_key || "-")}</span></div>
        <div class="kv-card"><strong>Case</strong><span>${esc(slide.case_id || "-")}</span></div>
        <div class="kv-card"><strong>Top activation</strong><span>${Number(slide.top_activation || slideSpecific.slide_max_activation || 0).toFixed(3)}</span></div>
        <div class="kv-card"><strong>Positive tiles</strong><span>${esc(slideSpecific.positive_tile_count || "-")}</span></div>
      </section>

      <section>
        <div class="mini-head">Top firing slides for this latent</div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Slide</th><th>Max act</th><th>Positive tiles</th><th>Cohort</th></tr></thead>
            <tbody>${topSlideRows || `<tr><td colspan="4" class="meta">No slide analytics for this latent.</td></tr>`}</tbody>
          </table>
        </div>
      </section>

      <section>
        <div class="mini-head">Support tiles on the current slide</div>
        <div class="support-grid">
          ${tileCards || `<p class="meta">No support tiles for the current filter on this slide.</p>`}
        </div>
      </section>

      <section id="technicalBlock" class="${state.showTechnical ? "" : "hidden"}">
        <div class="kv-card"><strong>slide_path</strong><span>${esc(slidePath)}</span></div>
      </section>
    </div>
  `;
}

function renderDetailPlaceholder(msg) {
  el.detailBody.innerHTML = `<p class="meta">${esc(msg)}</p>`;
}

function redrawCharts() {
  drawScatter();
  drawUmap();
}

async function loadFocusData() {
  if (state.selectedLatentIdx === null) {
    el.latentProfile.innerHTML = `<p class="meta">Select a representative latent tile or chart point to inspect latent-specific analytics.</p>`;
    el.methodStrip.innerHTML = `<p class="meta">Representative methods will appear here for the selected latent.</p>`;
    renderCohortTable([]);
    drawEmptyCanvas(el.histCanvas, "No latent selected.");
    renderDetailPlaceholder("Select a representative latent tile to inspect its slide-level support tiles.");
    renderFocusMeta();
    return;
  }

  try {
    renderFocusMeta();
    renderDetailPlaceholder("Loading slide-level evidence...");
    const detail = await fetchJson(`/api/sae/latent?${q({
      model_id: state.selectedModelId,
      latent_idx: state.selectedLatentIdx,
      strategy: state.selectedLatentStrategy,
      method: state.representativeMethod,
    })}`);
    renderLatentProfile(detail);

    const slideKey = state.selectedSlideKey || detail.default_slide_key || currentRepresentativeForFocus()?.slide_key || "";
    if (!slideKey) {
      renderDetailPlaceholder("No representative slide resolved for the selected latent.");
      return;
    }
    state.selectedSlideKey = slideKey;
    const slide = await fetchJson(`/api/sae/slide?${q({
      model_id: state.selectedModelId,
      slide_key: slideKey,
      method: state.representativeMethod,
      strategy: state.selectedLatentStrategy,
    })}`);
    renderSlideDetail(slide, detail);
  } catch (err) {
    el.latentProfile.innerHTML = `<p class="meta">Failed to load latent detail: ${esc(err.message)}</p>`;
    renderCohortTable([]);
    drawEmptyCanvas(el.histCanvas, "Failed to load histogram.");
    renderDetailPlaceholder(`Failed to load slide detail: ${err.message}`);
  }
}

async function loadModelData() {
  if (!state.selectedModelId) {
    renderDetailPlaceholder("No model selected.");
    return;
  }

  try {
    const [summaryData, repData, analyticsData] = await Promise.all([
      fetchJson(`/api/sae/summary?${q({ model_id: state.selectedModelId })}`),
      fetchJson(`/api/sae/representatives?${q({
        model_id: state.selectedModelId,
        method: state.representativeMethod,
        strategy: state.representativeStrategy,
        limit: 512,
      })}`),
      fetchJson(`/api/sae/analytics?${q({ model_id: state.selectedModelId })}`),
    ]);

    state.analytics = {
      available: !!analyticsData.available,
      summary: analyticsData.summary || {},
      metrics: analyticsData.all_latent_metrics || [],
      umap: analyticsData.latent_umap || [],
    };
    state.representatives = repData.rows || [];
    renderSummary(summaryData.summary || {}, summaryData.config || {}, state.analytics.summary || {});
    renderRepresentativeMethodSelect(repData.available_methods || []);
    renderRepresentativeStrategySelect(repData.available_strategies || []);
    renderLatentGroupSelect(state.representatives);
    renderRepresentatives();
    renderFocusMeta();
    redrawCharts();

    const current = currentRepresentativeForFocus();
    if (!current && state.representatives.length > 0) {
      const first = state.representatives[0];
      state.selectedLatentIdx = first.latent_idx;
      state.selectedLatentStrategy = first.latent_strategy || "";
      state.selectedSlideKey = first.slide_key || "";
      renderRepresentatives();
      renderFocusMeta();
      redrawCharts();
    }
    await loadFocusData();
  } catch (err) {
    el.summary.innerHTML = `<div class="metric"><strong>Error</strong><span>${esc(err.message)}</span></div>`;
    renderDetailPlaceholder("Failed to load model data.");
    drawEmptyCanvas(el.prevalenceCanvas, "Failed to load analytics.");
    drawEmptyCanvas(el.umapCanvas, "Failed to load analytics.");
    drawEmptyCanvas(el.histCanvas, "Failed to load analytics.");
  }
}

async function loadBootstrap() {
  try {
    const data = await fetchJson("/api/sae/models");
    state.models = data.models || [];
    state.encoders = data.encoders || [];
    if (state.encoders.length === 0) {
      state.encoders = ["none"];
    }
    state.selectedEncoder = state.encoders[0];
    renderEncoderSelect();
    renderModelSelect();
    await loadModelData();
  } catch (err) {
    el.summary.innerHTML = `<div class="metric"><strong>Error</strong><span>${esc(err.message)}</span></div>`;
    renderDetailPlaceholder("Failed to initialize SAE models.");
    drawEmptyCanvas(el.prevalenceCanvas, "Failed to initialize analytics.");
    drawEmptyCanvas(el.umapCanvas, "Failed to initialize analytics.");
    drawEmptyCanvas(el.histCanvas, "Failed to initialize analytics.");
  }
}

function clickPointFactory(kind) {
  return (evt) => {
    const canvas = kind === "scatter" ? el.prevalenceCanvas : el.umapCanvas;
    const points = kind === "scatter" ? state.scatterPoints : state.umapPoints;
    if (!points.length) return;
    const rect = canvas.getBoundingClientRect();
    const x = evt.clientX - rect.left;
    const y = evt.clientY - rect.top;
    const hit = nearestPoint(points, x, y);
    if (!hit) return;
    const strategy = pickStrategyForMetric(hit);
    const rep = state.representatives.find((r) => r.latent_idx === hit.latent_idx && (!strategy || r.latent_strategy === strategy));
    selectLatent(hit.latent_idx, strategy, rep?.slide_key || "");
  };
}

el.encoderSelect.addEventListener("change", () => {
  state.selectedEncoder = el.encoderSelect.value;
  renderModelSelect();
  state.selectedLatentIdx = null;
  state.selectedLatentStrategy = "";
  state.selectedSlideKey = "";
  loadModelData();
});

el.modelSelect.addEventListener("change", () => {
  state.selectedModelId = el.modelSelect.value;
  state.selectedLatentIdx = null;
  state.selectedLatentStrategy = "";
  state.selectedSlideKey = "";
  loadModelData();
});

el.repSearch.addEventListener("input", () => renderRepresentatives());
el.latentGroupSelect.addEventListener("change", () => renderRepresentatives());

el.clearLatentBtn.addEventListener("click", () => {
  state.selectedLatentIdx = null;
  state.selectedLatentStrategy = "";
  state.selectedSlideKey = "";
  renderRepresentatives();
  renderFocusMeta();
  loadFocusData();
  redrawCharts();
});

el.repMethodSelect.addEventListener("change", () => {
  state.representativeMethod = el.repMethodSelect.value || "max_activation";
  loadModelData();
});

el.repStrategySelect.addEventListener("change", () => {
  state.representativeStrategy = el.repStrategySelect.value || "";
  state.selectedLatentIdx = null;
  state.selectedLatentStrategy = "";
  state.selectedSlideKey = "";
  loadModelData();
});

el.toggleTechBtn.addEventListener("click", () => {
  state.showTechnical = !state.showTechnical;
  el.toggleTechBtn.textContent = state.showTechnical ? "Hide technical" : "Show technical";
  const block = document.getElementById("technicalBlock");
  if (block) {
    block.classList.toggle("hidden", !state.showTechnical);
  }
});

el.refreshBtn.addEventListener("click", () => loadBootstrap());
el.prevalenceCanvas.addEventListener("click", clickPointFactory("scatter"));
el.umapCanvas.addEventListener("click", clickPointFactory("umap"));
window.addEventListener("resize", () => redrawCharts());

loadBootstrap();
