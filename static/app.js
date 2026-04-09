const TABS = [
  { id: "all", label: "All" },
  { id: "missing", label: "Missing Features" },
  { id: "with", label: "With Features" },
  { id: "thumb_issues", label: "Thumbnail Issues" },
];

const state = {
  rows: [],
  selectedPath: "",
  thumbnailEnabled: false,
  previewToken: 0,
  activeTab: "all",
  detailsClosed: false,
  thumbStatusByPath: {},
  query: "",
};

const el = {
  refreshBtn: document.getElementById("refreshBtn"),
  resetBtn: document.getElementById("resetBtn"),
  toggleDetailsBtn: document.getElementById("toggleDetailsBtn"),
  queryInput: document.getElementById("queryInput"),
  summary: document.getElementById("summary"),
  statusTabs: document.getElementById("statusTabs"),
  tbody: document.querySelector("#recordsTable tbody"),
  details: document.getElementById("details"),
  detailsPanel: document.getElementById("detailsPanel"),
};

function esc(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function bytesToGB(bytes) {
  return (bytes / (1024 ** 3)).toFixed(2);
}

function getThumbState(record) {
  return state.thumbStatusByPath[record.slide.path] || "unknown";
}

function matchesQuery(record, q) {
  if (!q) {
    return true;
  }
  const hay = [
    record.slide.filename,
    record.slide.case_id,
    record.slide.cohort,
    record.slide.slide_id,
    ...record.encoders,
  ].join(" ").toLowerCase();
  return hay.includes(q);
}

function matchesTab(record, tabId) {
  if (tabId === "missing") {
    return record.feature_count === 0;
  }
  if (tabId === "with") {
    return record.feature_count > 0;
  }
  if (tabId === "thumb_issues") {
    const t = getThumbState(record);
    return t === "fail" || t === "timeout";
  }
  return true;
}

function currentBaseRows() {
  const q = state.query.trim().toLowerCase();
  return state.rows.filter((r) => matchesQuery(r, q));
}

function currentRows() {
  const base = currentBaseRows();
  return base.filter((r) => matchesTab(r, state.activeTab));
}

function tabCounts() {
  const base = currentBaseRows();
  return {
    all: base.length,
    missing: base.filter((r) => r.feature_count === 0).length,
    with: base.filter((r) => r.feature_count > 0).length,
    thumb_issues: base.filter((r) => {
      const t = getThumbState(r);
      return t === "fail" || t === "timeout";
    }).length,
  };
}

function renderSummary(payload) {
  const chips = [
    `<div class="metric-chip"><strong>Slides</strong>${payload.slide_count}</div>`,
    `<div class="metric-chip"><strong>Matched</strong>${payload.matched_feature_count}</div>`,
    `<div class="metric-chip"><strong>Unmatched</strong>${payload.unmatched_feature_count}</div>`,
    `<div class="metric-chip"><strong>Thumb</strong>${payload.thumbnail_enabled ? "enabled" : "disabled"}</div>`,
    `<div class="metric-chip"><strong>Slides Root</strong>${esc((payload.slides_root || []).join(", ") || "(none)")}</div>`,
    `<div class="metric-chip"><strong>Features Root</strong>${esc((payload.features_root || []).join(", ") || "(none)")}</div>`,
  ];
  el.summary.innerHTML = chips.join("");
}

function renderTabs() {
  const counts = tabCounts();
  el.statusTabs.innerHTML = TABS.map((tab) => {
    const active = tab.id === state.activeTab ? "active" : "";
    return `<button type="button" class="status-tab ${active}" data-tab="${tab.id}">${tab.label} (${counts[tab.id] ?? 0})</button>`;
  }).join("");
}

function setDetailsCollapsed(collapsed) {
  state.detailsClosed = collapsed;
  document.body.classList.toggle("details-collapsed", collapsed);
  el.toggleDetailsBtn.textContent = collapsed ? "Show Details" : "Hide Details";
}

function renderTable() {
  const rows = currentRows().slice().sort((a, b) => {
    const cohortA = String(a.slide.cohort || "UNKNOWN");
    const cohortB = String(b.slide.cohort || "UNKNOWN");
    const byCohort = cohortA.localeCompare(cohortB);
    if (byCohort !== 0) return byCohort;
    return String(a.slide.filename || "").localeCompare(String(b.slide.filename || ""));
  });
  el.tbody.innerHTML = "";

  for (const r of rows) {
    const selected = state.selectedPath && state.selectedPath === r.slide.path ? "selected" : "";
    const encoders = r.encoders.length > 0 ? esc(r.encoders.join(", ")) : "-";
    const featureBadge = r.feature_count === 0
      ? `<span class="badge warn">Missing</span>`
      : `<span class="badge ok">${r.feature_count} files</span>`;

    const tr = document.createElement("tr");
    tr.className = selected;
    tr.innerHTML = `
      <td>${esc(r.slide.filename)}</td>
      <td>${esc(r.slide.case_id)}</td>
      <td>${esc(r.slide.cohort || "UNKNOWN")}</td>
      <td>${featureBadge}</td>
      <td class="encoders-cell">${encoders}</td>
      <td>${bytesToGB(r.slide.size_bytes)}</td>
    `;

    tr.addEventListener("click", () => {
      state.selectedPath = r.slide.path;
      setDetailsCollapsed(false);
      renderTable();
      renderDetails(r);
    });

    el.tbody.appendChild(tr);
  }

  if (rows.length === 0) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="6" class="meta">No slides match current filter.</td>`;
    el.tbody.appendChild(tr);
  }
}

function renderDetailsClosed() {
  el.details.innerHTML = `
    <div class="panel-head">
      <h2>Details</h2>
      <button id="closeDetailsBtn" type="button" class="ghost-btn">Close</button>
    </div>
    <p class="meta">Details hidden. Click <strong>Show Details</strong> or select a row.</p>
  `;
}

function renderDetails(record) {
  const previewId = `preview-${Date.now()}-${Math.floor(Math.random() * 10000)}`;
  const featureStatus = record.feature_count === 0
    ? `<span class="badge warn">Missing features</span>`
    : `<span class="badge ok">${record.feature_count} feature files</span>`;

  const encoderText = record.encoders.length > 0 ? esc(record.encoders.join(", ")) : "-";
  const featureItems = record.features.length === 0
    ? `<p class="missing">No matched feature files.</p>`
    : `<ul class="feature-list">${record.features.map((f) => `<li class="feature-item">${esc(f.filename)}</li>`).join("")}</ul>`;

  el.details.innerHTML = `
    <div class="panel-head">
      <h2>Details</h2>
      <button id="closeDetailsBtn" type="button" class="ghost-btn">Close</button>
    </div>

    <div class="preview-wrap">
      <div id="${previewId}" class="meta">Loading thumbnail...</div>
    </div>

    <div class="info-grid">
      <div class="info-row"><strong>Slide</strong><span>${esc(record.slide.filename)}</span></div>
      <div class="info-row"><strong>Slide ID</strong><span>${esc(record.slide.slide_id)}</span></div>
      <div class="info-row"><strong>Case</strong><span>${esc(record.slide.case_id)}</span></div>
      <div class="info-row"><strong>Cohort</strong><span>${esc(record.slide.cohort || "UNKNOWN")}</span></div>
      <div class="info-row"><strong>Size</strong><span>${bytesToGB(record.slide.size_bytes)} GB</span></div>
      <div class="info-row"><strong>Status</strong><span>${featureStatus}</span></div>
      <div class="info-row"><strong>Encoders</strong><span>${encoderText}</span></div>
    </div>

    <div>
      <strong>Features</strong>
      ${featureItems}
    </div>

    <div class="technical-toggle">
      <button type="button" class="ghost-btn" id="toggleTechnicalBtn">Show Technical Path</button>
      <div id="technicalBlock" class="technical-block" hidden>
        <div>${esc(record.slide.path)}</div>
      </div>
    </div>
  `;

  const slot = document.getElementById(previewId);
  if (slot) {
    loadPreview(record.slide.path, slot);
  }
}

function setThumbStatus(slidePath, status) {
  if (state.thumbStatusByPath[slidePath] === status) {
    return;
  }
  state.thumbStatusByPath[slidePath] = status;
  renderTabs();
  if (state.activeTab === "thumb_issues") {
    renderTable();
  }
}

async function fetchThumbnail(url, signal) {
  const res = await fetch(url, { signal });
  return res;
}

async function loadPreview(slidePath, slotEl) {
  const myToken = ++state.previewToken;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 12000);

  const realUrl = `/api/thumbnail?fallback=0&size=192&path=${encodeURIComponent(slidePath)}`;
  const fallbackUrl = `/api/thumbnail?fallback=1&size=192&path=${encodeURIComponent(slidePath)}`;

  try {
    let res = await fetchThumbnail(realUrl, controller.signal);

    if (myToken !== state.previewToken || !slotEl.isConnected) {
      return;
    }

    if (!res.ok) {
      setThumbStatus(slidePath, res.status === 504 ? "timeout" : "fail");
      res = await fetchThumbnail(fallbackUrl, controller.signal);
      if (!res.ok) {
        const txt = await res.text();
        slotEl.className = "missing";
        slotEl.textContent = txt || `Thumbnail failed (${res.status}).`;
        return;
      }
    } else {
      setThumbStatus(slidePath, "ok");
    }

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const img = document.createElement("img");
    img.className = "preview-img";
    img.alt = "Slide thumbnail";
    img.src = url;
    slotEl.replaceWith(img);
  } catch (err) {
    if (myToken !== state.previewToken || !slotEl.isConnected) {
      return;
    }
    setThumbStatus(slidePath, "timeout");
    slotEl.className = "missing";
    if (err && err.name === "AbortError") {
      slotEl.textContent = "Thumbnail request timed out.";
    } else {
      slotEl.textContent = "Thumbnail request failed.";
    }
  } finally {
    clearTimeout(timeoutId);
  }
}

function renderAll() {
  renderTabs();
  renderTable();
  if (state.detailsClosed) {
    renderDetailsClosed();
  }
}

function resetFilters() {
  state.query = "";
  state.activeTab = "all";
  el.queryInput.value = "";
  renderAll();
}

async function loadIndex() {
  el.summary.innerHTML = `<div class="metric-chip"><strong>Status</strong>Indexing files...</div>`;
  const res = await fetch("/api/index");
  const payload = await res.json();
  state.rows = payload.records || [];
  state.thumbnailEnabled = Boolean(payload.thumbnail_enabled);
  state.selectedPath = "";
  renderSummary(payload);
  renderAll();
}

el.refreshBtn.addEventListener("click", loadIndex);
el.resetBtn.addEventListener("click", resetFilters);

el.queryInput.addEventListener("input", () => {
  state.query = el.queryInput.value || "";
  renderAll();
});

el.statusTabs.addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-tab]");
  if (!btn) {
    return;
  }
  state.activeTab = btn.dataset.tab;
  renderAll();
});

el.details.addEventListener("click", (ev) => {
  if (ev.target && ev.target.id === "closeDetailsBtn") {
    setDetailsCollapsed(true);
    renderDetailsClosed();
    return;
  }
  if (ev.target && ev.target.id === "toggleTechnicalBtn") {
    const block = document.getElementById("technicalBlock");
    if (!block) {
      return;
    }
    const hidden = block.hasAttribute("hidden");
    if (hidden) {
      block.removeAttribute("hidden");
      ev.target.textContent = "Hide Technical Path";
    } else {
      block.setAttribute("hidden", "");
      ev.target.textContent = "Show Technical Path";
    }
  }
});

el.toggleDetailsBtn.addEventListener("click", () => {
  const collapsed = !state.detailsClosed;
  setDetailsCollapsed(collapsed);
  if (collapsed) {
    renderDetailsClosed();
  }
});

loadIndex().catch((err) => {
  el.summary.innerHTML = `<div class="metric-chip"><strong>Error</strong>${esc(err.message)}</div>`;
});
