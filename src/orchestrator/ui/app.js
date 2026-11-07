/* Shared control-plane frontend: auth guard, API client, toasts, dialogs. */
const store = {
  get token() { return sessionStorage.getItem("winai_token"); },
  set token(v) { v ? sessionStorage.setItem("winai_token", v) : sessionStorage.removeItem("winai_token"); },
};

async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (store.token) headers["Authorization"] = "Bearer " + store.token;
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (res.status === 401) {
    store.token = null;
    if (!location.pathname.endsWith("/login")) location.href = "/login";
    throw new Error((data && data.detail) || "Session expired. Please log in again.");
  }
  if (!res.ok) throw new Error((data && data.detail) || ("Request failed: " + res.status));
  return data;
}

function toast(msg, isErr) {
  document.querySelectorAll(".toast").forEach((t) => t.remove());
  const el = document.createElement("div");
  el.className = "toast" + (isErr ? " err" : "");
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function statusBadge(s) {
  const cls = s === "COMPLETED" || s === "active" ? "ok"
    : (s === "FAILED" || s === "disabled" ? "bad" : "warn");
  return `<span class="badge ${cls}">${esc(s)}</span>`;
}

async function requireAuth(roles) {
  if (!store.token) { location.href = "/login"; throw new Error("redirect"); }
  const me = await api("/me");
  if (roles && roles.length && !roles.some((r) => me.roles.includes(r))) {
    document.body.innerHTML = `<main><div class="panel"><h2>Forbidden</h2>
      <p>Your account (${esc(me.email)}, roles: ${esc(me.roles.join(", "))}) cannot access this page.</p>
      <p><a href="/user">Go to user home</a> · <a href="/login">Switch account</a></p></div></main>`;
    throw new Error("forbidden");
  }
  return me;
}

async function logout() {
  try { await api("/auth/logout", { method: "POST" }); } catch (e) { /* ignore */ }
  store.token = null;
  location.href = "/login";
}

function confirmDialog(title, body) {
  return new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.innerHTML = `<h3 style="margin-top:0">${esc(title)}</h3><p>${esc(body)}</p>
      <div style="display:flex;gap:8px;justify-content:flex-end">
      <button class="secondary" id="c-no">Cancel</button>
      <button class="danger" id="c-yes">Confirm</button></div>`;
    document.body.appendChild(dlg);
    dlg.querySelector("#c-no").onclick = () => { dlg.close(); dlg.remove(); resolve(false); };
    dlg.querySelector("#c-yes").onclick = () => { dlg.close(); dlg.remove(); resolve(true); };
    dlg.showModal();
  });
}

function setLoading(el, on, text) {
  el.innerHTML = on ? `<div class="spinner">${esc(text || "Loading…")}</div>` : "";
}
