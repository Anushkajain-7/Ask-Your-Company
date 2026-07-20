// Shared helpers for auth + API calls. Loaded on every page.
const API_BASE = ""; // same-origin: frontend is served by the FastAPI app

function getToken() {
  return localStorage.getItem("atc_token");
}
function setSession(token, email, workspace) {
  localStorage.setItem("atc_token", token);
  localStorage.setItem("atc_email", email);
  localStorage.setItem("atc_workspace", workspace);
}
function clearSession() {
  localStorage.removeItem("atc_token");
  localStorage.removeItem("atc_email");
  localStorage.removeItem("atc_workspace");
}
function requireAuth() {
  if (!getToken()) window.location.href = "/login.html";
}
function redirectIfAuthed() {
  if (getToken()) window.location.href = "/index.html";
}

async function api(path, options = {}) {
  const headers = options.headers || {};
  if (!(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(API_BASE + path, { ...options, headers });
  if (res.status === 401 && path !== "/api/auth/login") {
    clearSession();
    window.location.href = "/login.html";
    throw new Error("Not authenticated");
  }
  if (!res.ok) {
    let detail = "Request failed";
    try {
      const body = await res.json();
      detail = formatApiError(body.detail || detail);
    } catch (_) {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

function formatApiError(detail) {
  if (!detail) return "Request failed";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map(formatValidationIssue).filter(Boolean).join(" ") || "Request failed";
  }
  if (typeof detail === "object") {
    if (typeof detail.msg === "string") return detail.msg;
    if (typeof detail.detail === "string") return detail.detail;
    return JSON.stringify(detail);
  }
  return String(detail);
}

function formatValidationIssue(issue) {
  if (typeof issue === "string") return issue;
  if (!issue || typeof issue !== "object") return "";
  const msg = issue.msg || formatApiError(issue);
  if (!Array.isArray(issue.loc)) return msg;
  const field = issue.loc.filter((part) => part !== "body").join(".");
  return field ? `${field}: ${msg}` : msg;
}

async function doLogout() {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch (_) {
    // even if the call fails, still clear locally
  }
  clearSession();
  window.location.href = "/login.html";
}
