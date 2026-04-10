const state = {
  models: [],
  encoders: [],
  selectedEncoder: "",
  selectedModelId: "",
  representatives: [],
  filteredRepresentatives: [],
  selectedSlideKey: "",
  selectedLatentIdx: null,
  selectedLatentStrategy: "",
  representativeStrategy: "",
  representativeMethod: "max_activation",
  showTechnical: false,
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

function renderSummary(summary, config) {
  const chips = [
    ["Model", summary.model_name || config.model_name || summary.model_id],
    ["Encoder", summary.encoder],
    ["Dataset", summary.dataset || "-"],
    ["Slides", summary.total_slides],
    ["Latents", summary.total_latents],
    ["Prototype rows", summary.total_prototype_rows || 0],
    ["Rep method", summary.rep_method || "max_activation"],
    ["Rep latents", summary.rep_latents || 0],
    ["Rep slide cov", `${Number(summary.rep_slide_coverage || 0).toFixed(1)}%`],
    ["Rep mean slide spread", Number(summary.rep_mean_unique_slides_per_latent || 0).toFixed(2)],
    ["Act p50", Number(summary.activation_p50 || 0).toFixed(3)],
    ["Act p95", Number(summary.activation_p95 || 0).toFixed(3)],
    ["Tail ratio", Number(summary.activation_tail_ratio || 0).toFixed(3)],
    ["Latent HHI", Number(summary.latent_concentration_hhi || 0).toFixed(4)],
  ];
  el.summary.innerHTML = chips.map(([k, v]) => `<div class="metric"><strong>${esc(k)}</strong>${esc(v)}</div>`).join("");
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

function renderRepresentatives() {
  applyRepresentativeFilter();
  const rows = state.filteredRepresentatives;
  el.repMeta.textContent = `${rows.length} / ${state.representatives.length}`;
  el.repCards.innerHTML = "";

  for (const r of rows) {
    const article = document.createElement("article");
    article.className = "rep-card";
    if (state.selectedLatentIdx === r.latent_idx && state.selectedLatentStrategy === (r.latent_strategy || "")) {
      article.classList.add("selected");
    }
    article.innerHTML = `
      <div class="rep-image">
        <img loading="lazy" src="${tileUrl(r, r.slide_key, 320)}" alt="representative tile" />
      </div>
      <div class="rep-body">
        <div class="rep-title">latent ${esc(r.latent_idx)} <span>${esc(r.latent_group || "-")}</span></div>
        <div class="rep-stats">${esc(r.latent_strategy || "-")} | ${esc(r.representative_method || state.representativeMethod || "-")}</div>
        <div class="rep-stats">score ${Number(r.method_score || r.activation || 0).toFixed(3)} | support ${esc(r.slide_support_count || 0)}</div>
        <div class="rep-slide">${esc(r.slide_key || "-")}</div>
      </div>
    `;
    article.addEventListener("click", () => {
      state.selectedLatentIdx = r.latent_idx;
      state.selectedLatentStrategy = r.latent_strategy || "";
      state.selectedSlideKey = r.slide_key;
      renderRepresentatives();
      loadSlideDetail(r.slide_key);
    });
    el.repCards.appendChild(article);
  }

  if (rows.length === 0) {
    el.repCards.innerHTML = `<p class="meta">No representative latents for current filter.</p>`;
  }
}

function renderDetailPlaceholder(msg) {
  el.detailBody.innerHTML = `<p class="meta">${esc(msg)}</p>`;
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

function renderSlideDetail(data) {
  const slide = data.slide;
  const topLatents = data.top_latents || [];
  let tiles = data.tiles || [];

  if (state.selectedLatentIdx !== null) {
    tiles = tiles.filter((t) => t.latent_idx === state.selectedLatentIdx);
  }

  const latentRows = topLatents.slice(0, 12).map((r) => (
    `<tr>
      <td>${esc(r.latent_idx)}</td>
      <td>${esc(r.latent_group || "-")}</td>
      <td>${Number(r.max_activation || 0).toFixed(3)}</td>
      <td>${esc(r.count || 0)}</td>
    </tr>`
  )).join("");

  const tileCards = tiles.slice(0, 24).map((t) => {
    const stat = (t.source === "prototype" || t.source === "support")
      ? `act ${Number(t.activation || 0).toFixed(3)}`
      : `attn ${Number(t.attention || 0).toFixed(3)}`;
    const strategy = t.latent_strategy ? ` | ${esc(t.latent_strategy)}` : "";
    const method = t.representative_method ? ` | ${esc(t.representative_method)}` : "";
    return `
      <article class="tile-card">
        <img loading="lazy" src="${tileUrl(t, slide.slide_key, data.tile_size || 256)}" alt="tile" />
        <div class="tile-meta">
          <div><strong>${esc(t.source)}</strong> ${esc(stat)}${strategy}${method}</div>
          <div>latent: ${t.latent_idx === null || t.latent_idx === undefined ? "-" : esc(t.latent_idx)}</div>
          <div>idx ${esc(t.tile_index)} @ (${esc(t.coord_x)}, ${esc(t.coord_y)})</div>
        </div>
      </article>
    `;
  }).join("");

  const slidePath = slide.slide_path || "(not resolved)";
  const tileStatusBadge = slide.prototype_tiles > 0 ? badge(true, "prototype tiles") : badge(false, "no prototype tiles");

  el.detailBody.innerHTML = `
    <section class="kv">
      <div class="row"><strong>Slide</strong><span>${esc(slide.slide_key)}</span></div>
      <div class="row"><strong>Case</strong><span>${esc(slide.case_id || "-")}</span></div>
      <div class="row"><strong>Top activation</strong><span>${Number(slide.top_activation || 0).toFixed(3)}</span></div>
      <div class="row"><strong>Top attention</strong><span>${Number(slide.top_attention || 0).toFixed(4)}</span></div>
      <div class="row"><strong>Tile status</strong><span>${tileStatusBadge}</span></div>
    </section>

    <section>
      <h3>Top Latents</h3>
      <div class="table-wrap" style="max-height: 190px;">
        <table>
          <thead>
            <tr><th>Latent</th><th>Group</th><th>Max Act</th><th>Count</th></tr>
          </thead>
          <tbody>${latentRows || `<tr><td colspan="4" class="meta">No latent rows.</td></tr>`}</tbody>
        </table>
      </div>
    </section>

    <section>
      <h3>Representative Tiles ${state.selectedLatentIdx !== null ? `(latent ${state.selectedLatentIdx})` : ""}</h3>
      <div class="tile-grid">
        ${tileCards || `<p class="meta">No tiles for current filter.</p>`}
      </div>
    </section>

    <section id="technicalBlock" class="technical ${state.showTechnical ? "" : "hidden"}">
      <div><strong>slide_path</strong>: ${esc(slidePath)}</div>
    </section>
  `;
}

async function loadSlideDetail(slideKey) {
  try {
    renderDetailPlaceholder("Loading slide detail...");
    const data = await fetchJson(`/api/sae/slide?${q({
      model_id: state.selectedModelId,
      slide_key: slideKey,
      method: state.representativeMethod,
      strategy: state.representativeStrategy || state.selectedLatentStrategy,
    })}`);
    if (slideKey !== state.selectedSlideKey) {
      return;
    }
    renderSlideDetail(data);
  } catch (err) {
    renderDetailPlaceholder(`Failed to load slide detail: ${err.message}`);
  }
}

async function loadModelData() {
  if (!state.selectedModelId) {
    renderSummary({}, {});
    state.representatives = [];
    renderRepresentatives();
    renderDetailPlaceholder("No model selected.");
    return;
  }

  try {
    const [summaryData, repData] = await Promise.all([
      fetchJson(`/api/sae/summary?${q({ model_id: state.selectedModelId })}`),
      fetchJson(`/api/sae/representatives?${q({
        model_id: state.selectedModelId,
        method: state.representativeMethod,
        strategy: state.representativeStrategy,
        limit: 256,
      })}`),
    ]);

    renderSummary(summaryData.summary || {}, summaryData.config || {});
    state.representatives = repData.rows || [];

    renderRepresentativeMethodSelect(repData.available_methods || []);
    renderRepresentativeStrategySelect(repData.available_strategies || []);
    renderLatentGroupSelect(state.representatives);
    renderRepresentatives();

    state.selectedSlideKey = "";
    state.selectedLatentStrategy = "";
    renderDetailPlaceholder("Select a representative latent tile to inspect details.");
  } catch (err) {
    el.summary.innerHTML = `<div class="metric"><strong>Error</strong>${esc(err.message)}</div>`;
    state.representatives = [];
    renderRepresentatives();
    renderDetailPlaceholder("Failed to load model data.");
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
    el.summary.innerHTML = `<div class="metric"><strong>Error</strong>${esc(err.message)}</div>`;
    renderDetailPlaceholder("Failed to initialize SAE models.");
  }
}

el.encoderSelect.addEventListener("change", () => {
  state.selectedEncoder = el.encoderSelect.value;
  renderModelSelect();
  loadModelData();
});

el.modelSelect.addEventListener("change", () => {
  state.selectedModelId = el.modelSelect.value;
  state.selectedLatentIdx = null;
  loadModelData();
});

el.repSearch.addEventListener("input", () => {
  renderRepresentatives();
});

el.latentGroupSelect.addEventListener("change", () => {
  renderRepresentatives();
});

el.clearLatentBtn.addEventListener("click", () => {
  state.selectedLatentIdx = null;
  state.selectedLatentStrategy = "";
  state.selectedSlideKey = "";
  renderRepresentatives();
  renderDetailPlaceholder("Select a representative latent tile to inspect details.");
});

el.repMethodSelect.addEventListener("change", () => {
  state.representativeMethod = el.repMethodSelect.value || "max_activation";
  loadModelData();
});

el.repStrategySelect.addEventListener("change", () => {
  state.representativeStrategy = el.repStrategySelect.value || "";
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

el.refreshBtn.addEventListener("click", () => {
  loadBootstrap();
});

loadBootstrap();
