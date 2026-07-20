requireAuth();

const SOURCE_ICONS = {
  markdown: "📄", pdf: "📕", slack_json: "💬", csv: "📊", xlsx: "📊",
};

function visibilityLabel(role) {
  const value = role || "all";
  if (value === "all") return "all roles";
  if (value === "admin") return "admins only";
  return `${value} role`;
}

function statusLabel(status) {
  if (status === "needs_review") return "needs review";
  return status || "unknown";
}

function statusDotClass(status) {
  if (status === "ready") return "green";
  if (status === "failed") return "red";
  return "orange";
}

// ---------- Top nav / user menu ----------
function initTopNav() {
  const email = localStorage.getItem("atc_email") || "";
  const workspace = localStorage.getItem("atc_workspace") || "";
  document.getElementById("workspacePill").textContent = workspace;
  document.getElementById("avatarInitials").textContent = (email[0] || "?").toUpperCase();
  document.getElementById("userInfo").textContent = email;

  document.getElementById("avatarBtn").addEventListener("click", (e) => {
    e.stopPropagation();
    document.getElementById("userMenu").classList.toggle("hidden");
  });
  document.addEventListener("click", () => document.getElementById("userMenu").classList.add("hidden"));
  document.getElementById("logoutBtn").addEventListener("click", doLogout);

  document.querySelectorAll(".nav-tabs button").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
}

function switchTab(tab) {
  document.querySelectorAll(".nav-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
  document.getElementById(`tab-${tab}`).classList.add("active");
  if (tab === "sources") loadSourcesTab();
  if (tab === "evaluations") loadEvaluations();
  if (tab === "audit") loadAuditLog();
}

// ---------- Sources (Ask tab sidebar + right panel) ----------
async function loadSourceList() {
  const sources = await api("/api/sources");
  const list = document.getElementById("sourceList");
  const coverageList = document.getElementById("coverageList");
  list.innerHTML = "";
  coverageList.innerHTML = "";

  if (sources.length === 0) {
    list.innerHTML = `<div class="empty-state">No sources yet. Go to <b>Sources</b> to add your wiki, PDFs, Slack exports, or spreadsheets.</div>`;
  }

  sources.forEach((s) => {
    const barClass = s.coverage_pct === 100 ? "" : "partial";
    const dotClass = s.coverage_pct === 100 ? "green" : "orange";
    const visibility = visibilityLabel(s.visible_to_roles);
    list.innerHTML += `
      <div class="source-card">
        <div class="source-head">
          <div class="source-name"><span class="source-icon icon-${s.source_type}">${SOURCE_ICONS[s.source_type] || "📄"}</span> ${s.name}</div>
          <div class="source-pct">${s.coverage_pct}%</div>
        </div>
        <div class="source-sub">${s.document_count} document(s) · ${visibility}</div>
        <div class="bar-track"><div class="bar-fill ${barClass}" style="width:${s.coverage_pct}%"></div></div>
        <div class="status-row"><span class="dot ${dotClass}"></span> ${s.status}</div>
      </div>`;

    coverageList.innerHTML += `
      <div class="coverage-row">
        <div class="coverage-name"><span>${SOURCE_ICONS[s.source_type] || "📄"}</span> ${s.name}</div>
        <div class="coverage-pct">${s.coverage_pct}%</div>
      </div>
      <div class="bar-track" style="margin-bottom:10px;"><div class="bar-fill ${barClass}" style="width:${s.coverage_pct}%"></div></div>`;
  });

  document.getElementById("isolationLine").textContent = `Scoped to “${localStorage.getItem("atc_workspace")}”`;
  return sources;
}

// ---------- Ask ----------
async function askQuestion() {
  const question = document.getElementById("question").value.trim();
  if (!question) return;

  const askBtn = document.getElementById("askBtn");
  askBtn.disabled = true;
  askBtn.textContent = "Thinking…";

  try {
    const res = await api("/api/ask", { method: "POST", body: JSON.stringify({ question }) });
    renderAnswer(res);
  } catch (err) {
    alert("Couldn't get an answer: " + err.message);
  } finally {
    askBtn.disabled = false;
    askBtn.innerHTML = "🔒 Ask securely";
  }
}

function renderAnswer(res) {
  document.getElementById("askEmptyState").classList.add("hidden");
  const card = document.getElementById("answerCard");
  card.classList.remove("hidden");

  document.getElementById("answerText").textContent = res.answer;

  const badgeWrap = document.getElementById("citationBadges");
  badgeWrap.innerHTML = "";
  const seen = new Set();
  res.citations.forEach((c) => {
    const key = c.source_name + c.locator;
    if (seen.has(key)) return;
    seen.add(key);
    badgeWrap.innerHTML += `<span class="badge badge-${c.source_type}">[${c.source_name} ${c.locator}]</span>`;
  });

  const provList = document.getElementById("provenanceList");
  provList.innerHTML = "";
  res.citations.forEach((c, i) => {
    provList.innerHTML += `
      <div class="claim-row">
        <div class="claim-num">${i + 1}</div>
        <div class="claim-text">${escapeHtml(c.text_preview)}</div>
        <div class="claim-source">${SOURCE_ICONS[c.source_type] || "📄"} ${c.source_name} · ${c.locator}</div>
        <div class="claim-score">${Math.round(c.score * 100)}%</div>
      </div>`;
  });
  if (res.citations.length === 0) {
    provList.innerHTML = `<div class="empty-state">No matching passages were found in this workspace's ingested sources.</div>`;
  }

  const confEl = document.getElementById("confidenceNum");
  confEl.textContent = res.confidence + "%";
  document.getElementById("confidenceBar").style.width = res.confidence + "%";
  document.getElementById("confidenceLabel").textContent =
    res.confidence >= 80 ? "High confidence" : res.confidence >= 50 ? "Medium confidence" : "Low confidence — verify manually";

  document.getElementById("generatedAt").textContent = "Generated just now" + (res.used_fallback ? " (offline/fallback mode)" : "");
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// ---------- Sources tab (create + upload) ----------
async function loadSourcesTab() {
  const sources = await loadSourceList();
  const select = document.getElementById("uploadSourceSelect");
  select.innerHTML = sources.map((s) => `<option value="${s.id}">${s.name} (${s.source_type}, ${visibilityLabel(s.visible_to_roles)})</option>`).join("");
}

async function createSource() {
  const name = document.getElementById("newSourceName").value.trim();
  const source_type = document.getElementById("newSourceType").value;
  const visible_to_roles = document.getElementById("newSourceVisibility").value;
  const errEl = document.getElementById("sourceError");
  errEl.textContent = "";
  if (!name) { errEl.textContent = "Give the source a name."; return; }
  try {
    await api(`/api/sources?name=${encodeURIComponent(name)}&source_type=${source_type}&visible_to_roles=${encodeURIComponent(visible_to_roles)}`, { method: "POST" });
    document.getElementById("newSourceName").value = "";
    document.getElementById("newSourceVisibility").value = "all";
    await loadSourcesTab();
  } catch (err) {
    errEl.textContent = err.message;
  }
}

async function uploadDocument() {
  const sourceId = document.getElementById("uploadSourceSelect").value;
  const fileInput = document.getElementById("uploadFile");
  const errEl = document.getElementById("uploadError");
  errEl.textContent = "";
  if (!sourceId) { errEl.textContent = "Create a source first."; return; }
  if (!fileInput.files.length) { errEl.textContent = "Choose a file."; return; }

  const form = new FormData();
  form.append("file", fileInput.files[0]);

  const uploadBtn = document.getElementById("uploadBtn");
  uploadBtn.disabled = true;
  uploadBtn.textContent = "Ingesting…";
  try {
    const doc = await api(`/api/sources/${sourceId}/documents`, { method: "POST", body: form });
    document.getElementById("uploadedDocs").innerHTML =
      `<div class="status-row"><span class="dot ${statusDotClass(doc.status)}"></span> ${doc.filename} — ${statusLabel(doc.status)}${doc.error ? ": " + doc.error : ""}</div>` +
      document.getElementById("uploadedDocs").innerHTML;
    fileInput.value = "";
    await loadSourcesTab();
  } catch (err) {
    errEl.textContent = err.message;
  } finally {
    uploadBtn.disabled = false;
    uploadBtn.innerHTML = "⬆ Upload &amp; ingest";
  }
}

// ---------- Evaluations tab ----------
async function loadEvaluations() {
  const logs = await api("/api/audit-log?limit=200");
  const el = document.getElementById("evalStats");
  if (logs.length === 0) {
    el.innerHTML = `<div class="empty-state">No queries yet. Ask a few questions, then check back here.</div>`;
    return;
  }
  const avgConf = (logs.reduce((a, l) => a + l.confidence, 0) / logs.length).toFixed(1);
  const low = logs.filter((l) => l.confidence < 50).length;
  const recentRows = logs.slice(0, 20).map((l) => `
    <tr style="border-bottom:1px solid var(--border);">
      <td style="padding:8px;">${new Date(l.created_at).toLocaleString()}</td>
      <td style="padding:8px;">${escapeHtml(l.question)}</td>
      <td style="padding:8px;">${l.confidence}%</td>
    </tr>`).join("");
  el.innerHTML = `
    <div class="panel" style="flex:1;min-width:160px;"><div class="trust-sub">Total queries</div><div class="confidence-num">${logs.length}</div></div>
    <div class="panel" style="flex:1;min-width:160px;"><div class="trust-sub">Avg confidence</div><div class="confidence-num">${avgConf}%</div></div>
    <div class="panel" style="flex:1;min-width:160px;"><div class="trust-sub">Low-confidence answers</div><div class="confidence-num">${low}</div></div>
    <div style="width:100%;margin-top:12px;overflow:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead>
          <tr style="text-align:left;color:var(--text-dim);border-bottom:1px solid var(--border);">
            <th style="padding:8px;">When</th><th style="padding:8px;">Question</th><th style="padding:8px;">Confidence</th>
          </tr>
        </thead>
        <tbody>${recentRows}</tbody>
      </table>
    </div>`;
}

// ---------- Audit log tab ----------
async function loadAuditLog() {
  const logs = await api("/api/audit-log?limit=100");
  const rows = document.getElementById("auditRows");
  rows.innerHTML = logs.map((l) => `
    <tr style="border-bottom:1px solid var(--border);">
      <td style="padding:8px;">${new Date(l.created_at).toLocaleString()}</td>
      <td style="padding:8px;">${l.user_email}</td>
      <td style="padding:8px;">${escapeHtml(l.question)}</td>
      <td style="padding:8px;">${l.confidence}%</td>
    </tr>`).join("") || `<tr><td colspan="4" style="padding:16px;color:var(--text-dim);">No queries logged yet.</td></tr>`;
}

// ---------- Wire up ----------
initTopNav();
loadSourceList();
document.getElementById("askBtn").addEventListener("click", askQuestion);
document.getElementById("question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) askQuestion();
});
document.getElementById("goManageSources").addEventListener("click", () => switchTab("sources"));
document.getElementById("createSourceBtn").addEventListener("click", createSource);
document.getElementById("uploadBtn").addEventListener("click", uploadDocument);
