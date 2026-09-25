// ==========================================================================
// PashuMitra — Animal Disease Management & Surveillance Platform
// Integrated Pashu Health Chain:
// 1. Animal Health Identity (QR, Passport, Reproductive, Medication & Allergies)
// 2. Digital Biological Sample Tracking (GPS, Time, Chain of Custody)
// 3. Laboratory Diagnostic Portal (Receiving, Acceptance, Testing, Reports)
// 4. Veterinary Decision Support & AI Clinical Guidance
// 5. Structured Treatment Responses & Farm-Level Intelligence
// 6. National Disease Intelligence & Surveillance Network
// ==========================================================================

const API = "/api";
const ROLES = ["owner", "vet", "govt", "lab"];

const state = {
  token: localStorage.getItem("token") || null,
  user: JSON.parse(localStorage.getItem("user") || "null"),
  lang: localStorage.getItem("pm_lang") || "en",
  route: "#/",
};

function getUserRole() {
  const role = state.user && state.user.role;
  return ROLES.includes(role) ? role : null;
}
function homeFor(role) { return `#/${role}/dashboard`; }

// ------------------------------------------------------------------ i18n --
const I18N = {
  en: {
    "app.tagline": "Animal Disease Reporting & Veterinary Care — Maharashtra",
    "nav.dashboard": "Home", "nav.analytics": "Analytics", "nav.gis": "GIS Risk Map",
    "nav.surveillance": "Surveillance", "nav.ai": "AI Risk", "nav.reporting": "Report",
    "nav.animals": "Animals", "nav.cases": "Cases", "nav.lab": "Lab", "nav.rx": "Rx",
    "nav.alerts": "Alerts", "nav.reports": "Reports", "nav.search": "Search",
    "nav.campaigns": "Campaigns", "nav.stock": "Stock", "nav.diseases": "Info",
    "nav.queue": "Queue", "nav.national": "National", "nav.scan": "Scan QR",
    "role.owner": "Animal Owner", "role.vet": "Veterinarian", "role.govt": "Govt Officer",
    "role.lab": "Laboratory Staff",
    "role.owner.desc": "Register animals, report health issues, track prescriptions & QR passport",
    "role.vet.desc": "Receive reports, diagnose, sample collection, lab tests, prescriptions & AI CDS",
    "role.govt.desc": "State analytics, disease surveillance, GIS risk, clusters & national surveillance",
    "role.lab.desc": "Sample intake, verification, biological testing, result entry & verified lab reports",
    "btn.login": "Login", "btn.register": "Register", "btn.logout": "Logout",
    "btn.save": "Save", "btn.submit": "Submit", "btn.create": "Create",
    "auth.choose": "Choose your portal", "auth.newHere": "New here?",
    "auth.haveAccount": "Already registered?", "auth.createAccount": "Create an account",
    "lang.label": "Language",
  },
  mr: {
    "app.tagline": "पशुधन रोग अहवाल आणि पशुवैद्यकीय सेवा — महाराष्ट्र",
    "nav.dashboard": "मुख्यपृष्ठ", "nav.analytics": "विश्लेषण", "nav.gis": "जीआयएस धोका नकाशा",
    "nav.surveillance": "रोग पाळत", "nav.ai": "एआय धोका", "nav.reporting": "अहवाल",
    "nav.animals": "प्राणी", "nav.cases": "प्रकरणे", "nav.lab": "प्रयोगशाळा", "nav.rx": "औषध",
    "nav.alerts": "इशारे", "nav.reports": "अहवाल", "nav.search": "शोध",
    "nav.campaigns": "लसीकरण मोहीम", "nav.stock": "साठा", "nav.diseases": "माहिती",
    "nav.queue": "रांग", "nav.national": "राष्ट्रीय", "nav.scan": "स्कॅन",
    "role.owner": "पशुमालक", "role.vet": "पशुवैद्यक", "role.govt": "सरकारी अधिकारी",
    "role.lab": "प्रयोगशाळा कर्मचारी",
    "role.owner.desc": "प्राणी नोंदवा, आरोग्य अहवाल द्या, औषधे व क्यूआर पासपोर्ट पहा",
    "role.vet.desc": "अहवाल स्वीकारा, नमुना संकलन, निदान, प्रयोगशाळा, औषध व एआय सल्ला",
    "role.govt.desc": "राज्य विश्लेषण, रोग पाळत, जीआयएस धोका आणि राष्ट्रीय पूर्व चेतावणी",
    "role.lab.desc": "नमुना स्वीकृती, पडताळणी, जैविक चाचण्या, निकाल नोंदणी आणि अहवाल",
    "btn.login": "लॉगिन", "btn.register": "नोंदणी", "btn.logout": "बाहेर पडा",
    "btn.save": "जतन करा", "btn.submit": "सादर करा", "btn.create": "तयार करा",
    "auth.choose": "तुमचे पोर्टल निवडा", "auth.newHere": "नवीन आहात?",
    "auth.haveAccount": "आधीच नोंदणी केली आहे?", "auth.createAccount": "खाते तयार करा",
    "lang.label": "भाषा",
  },
};
function t(key) {
  return (I18N[state.lang] && I18N[state.lang][key]) || I18N.en[key] || key;
}
window.setLang = function (lang) {
  state.lang = lang;
  localStorage.setItem("pm_lang", lang);
  router();
};
function langToggle() {
  return `<div class="lang-toggle">
    <span>${t("lang.label")}:</span>
    <button type="button" class="${state.lang === "en" ? "active" : ""}" onclick="setLang('en')">English</button>
    <button type="button" class="${state.lang === "mr" ? "active" : ""}" onclick="setLang('mr')">मराठी</button>
  </div>`;
}

// ---------------------------------------------------------------- utils --
function toast(msg, isError = false) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = msg;
  el.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (el.className = "toast"), 3200);
}

// --------------------------------------- offline queue synchronization --
function getOfflineQueue() {
  try { return JSON.parse(localStorage.getItem("pashu_offline_queue") || "[]"); } catch (e) { return []; }
}
function setOfflineQueue(q) {
  localStorage.setItem("pashu_offline_queue", JSON.stringify(q));
}
function queueOfflineAction(path, method, body) {
  const q = getOfflineQueue();
  const txnId = "client_txn_" + Date.now() + "_" + Math.random().toString(36).slice(2, 8);
  let action = "UNKNOWN";
  if (path.includes("/cases") && method === "POST") action = "CREATE_CASE";
  else if (path.includes("/samples") && method === "POST") action = "COLLECT_SAMPLE";
  else if (path.includes("/treatment-responses")) action = "RECORD_TREATMENT";
  else if (path.includes("/animals") && method === "POST") action = "REGISTER_ANIMAL";
  q.push({ client_txn_id: txnId, path, method, action, payload: body, timestamp: new Date().toISOString() });
  setOfflineQueue(q);
}
async function syncOfflineQueue() {
  const q = getOfflineQueue();
  if (!q.length) return;
  if (!state.token) return;
  try {
    const res = await fetch(API + "/sync/queue", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + state.token },
      body: JSON.stringify({ items: q })
    });
    if (res.ok) {
      const data = await res.json();
      setOfflineQueue([]);
      toast(`🟢 Synchronized ${data.synced_count} field operation(s) successfully!`);
      router();
    }
  } catch (e) {}
}
window.syncOfflineQueue = syncOfflineQueue;
window.addEventListener("online", syncOfflineQueue);

async function api(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (state.token) headers.Authorization = "Bearer " + state.token;
  try {
    const res = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
    let data = {};
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok) {
      if (res.status === 401) logout(true);
      const err = new Error(data.error || "Something went wrong. Please try again.");
      err.data = data;
      err.status = res.status;
      throw err;
    }
    return data;
  } catch (err) {
    if (!navigator.onLine && ["POST", "PUT"].includes(method)) {
      queueOfflineAction(path, method, body);
      toast("Offline mode: Operation queued locally for auto-sync.", false);
      return { ok: true, offline_queued: true };
    }
    throw err;
  }
}

function setAuth(token, user) {
  state.token = token; state.user = user;
  localStorage.setItem("token", token);
  localStorage.setItem("user", JSON.stringify(user));
}

function logout(silent) {
  state.token = null; state.user = null;
  localStorage.removeItem("token"); localStorage.removeItem("user");
  location.hash = "#/";
  if (!silent) toast("Logged out successfully");
}
window.logout = logout;

function fmtDate(d) {
  if (!d) return "—";
  try { return new Date(d).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }); }
  catch (e) { return d; }
}
function statusBadgeClass(status) {
  const s = (status || "").toUpperCase();
  if (["NEW", "ASSIGNED", "UNDER INVESTIGATION", "REJECTED"].includes(s)) return "badge-red";
  if (["SAMPLE COLLECTED", "LAB PENDING", "DIAGNOSED", "TREATMENT", "FOLLOW-UP", "READY_FOR_PICKUP", "PICKED_UP", "IN_TRANSIT", "ARRIVED_AT_LAB", "LAB_RECEIVED", "TESTING", "RESULT_READY"].includes(s)) return "badge-orange";
  if (["RECOVERED", "CLOSED", "COMPLETED", "VERIFIED"].includes(s)) return "badge-green";
  return "badge-blue";
}
function severityBadgeClass(sev) {
  const s = (sev || "").toLowerCase();
  if (s === "high" || s === "critical") return "badge-red";
  if (s === "medium" || s === "moderate") return "badge-orange";
  return "badge-blue";
}
function riskBadgeClass(level) {
  return level === "High Risk" || level === "Critical" ? "badge-red" : level === "Moderate Risk" || level === "Moderate" ? "badge-orange" : "badge-green";
}
function emptyState(msg) { return `<div class="empty-state">${msg}</div>`; }
function statCard(num, lbl) { return `<div class="stat-card"><div class="num">${num}</div><div class="lbl">${lbl}</div></div>`; }
function iconItem(emoji, label, href) {
  return `<div class="icon-item" onclick="location.hash='${href}'"><div class="icon-circle">${emoji}</div><span>${label}</span></div>`;
}

// ---------------------------------------------------------------- shell --
function header(title, opts = {}) {
  const role = getUserRole();
  const notifHref = role ? `#/${role}/notifications` : "#/";
  const profileHref = role ? `#/${role}/profile` : "#/";
  const qCount = getOfflineQueue().length;
  return `
  <div class="app-header">
    ${opts.back ? `<button class="header-icon-btn" onclick="history.back()">←</button>`
      : `<button class="header-icon-btn" onclick="location.hash='${notifHref}'">🔔${opts.notif ? '<span class="dot"></span>' : ''}</button>`}
    <h1>${title}</h1>
    <button class="header-icon-btn" onclick="location.hash='${profileHref}'">👤</button>
  </div>
  ${qCount > 0 ? `
    <div style="text-align:center;margin-top:6px">
      <span class="sync-indicator" onclick="syncOfflineQueue()">⚡ ${qCount} action(s) queued offline · Tap to sync</span>
    </div>` : ""}`;
}

function bottomNav(active) {
  const role = getUserRole();
  let items = [];
  if (role === "owner") {
    items = [
      [`#/owner/dashboard`, "🏠", t("nav.dashboard")],
      [`#/owner/animals`, "🐄", t("nav.animals")],
      [`#/owner/report`, "🚑", t("nav.reporting")],
      [`#/owner/cases`, "🩺", t("nav.cases")],
      [`#/owner/prescriptions`, "💊", t("nav.rx")],
      [`#/owner/notifications`, "🔔", t("nav.alerts")]
    ];
  } else if (role === "vet") {
    items = [
      [`#/vet/dashboard`, "🏠", t("nav.dashboard")],
      [`#/vet/reports`, "📋", t("nav.reports")],
      [`#/vet/cases`, "🩺", t("nav.cases")],
      [`#/vet/campaigns`, "💉", t("nav.campaigns")],
      [`#/vet/search`, "🔍", t("nav.search")],
      [`#/vet/notifications`, "🔔", t("nav.alerts")]
    ];
  } else if (role === "govt") {
    items = [
      [`#/govt/dashboard`, "📊", t("nav.analytics")],
      [`#/govt/gis`, "🗺️", t("nav.gis")],
      [`#/govt/surveillance`, "🌐", t("nav.surveillance")],
      [`#/govt/ai`, "🧠", t("nav.ai")],
      [`#/govt/national`, "🇮🇳", t("nav.national")],
      [`#/govt/notifications`, "🔔", t("nav.alerts")]
    ];
  } else if (role === "lab") {
    items = [
      [`#/lab/dashboard`, "🏠", t("nav.dashboard")],
      [`#/lab/queue`, "🧪", t("nav.queue")],
      [`#/scan`, "📷", t("nav.scan")],
      [`#/lab/lab-reports`, "📋", t("nav.reports")],
      [`#/lab/notifications`, "🔔", t("nav.alerts")]
    ];
  }
  return `<div class="bottom-nav">${items.map(([href, ic, lbl]) =>
    `<button class="nav-item ${active === href ? "active" : ""}" onclick="location.hash='${href}'"><span class="ic">${ic}</span>${lbl}</button>`
  ).join("")}</div>`;
}

function render(html) { document.getElementById("app").innerHTML = html; window.scrollTo(0, 0); }

function barChart(items) {
  if (!items || !items.length) return emptyState("No data yet.");
  const max = Math.max(...items.map(i => i.value), 1);
  return items.map(i => `
    <div style="margin:10px 0">
      <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px"><span>${i.label}</span><b>${i.value}</b></div>
      <div style="background:#e8eaf3;border-radius:6px;height:12px;overflow:hidden">
        <div style="width:${Math.max((i.value / max) * 100, 2)}%;height:12px;background:#3f51b5;border-radius:6px"></div>
      </div>
    </div>`).join("");
}
const PIE_COLORS = ["#3f51b5", "#e53935", "#43a047", "#fb8c00", "#8e24aa", "#00acc1", "#6d4c41", "#f4511e"];
function pieChart(items) {
  if (!items || !items.length) return emptyState("No data yet.");
  const total = items.reduce((s, i) => s + i.value, 0) || 1;
  let acc = 0;
  const stops = items.map((i, idx) => {
    const from = (acc / total) * 360; acc += i.value; const to = (acc / total) * 360;
    return `${PIE_COLORS[idx % PIE_COLORS.length]} ${from}deg ${to}deg`;
  }).join(", ");
  const legend = items.map((i, idx) => `
    <div style="display:flex;align-items:center;gap:6px;font-size:13px">
      <span style="width:12px;height:12px;border-radius:3px;background:${PIE_COLORS[idx % PIE_COLORS.length]};display:inline-block;flex:none"></span>
      <span>${i.label} — ${i.value} (${Math.round((i.value / total) * 100)}%)</span>
    </div>`).join("");
  return `<div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap">
      <div style="width:150px;height:150px;border-radius:50%;background:conic-gradient(${stops});flex:none"></div>
      <div style="display:flex;flex-direction:column;gap:6px">${legend}</div>
    </div>`;
}

// ------------------------------------------------------------- routing --
const routes = {};
function route(path, handler, roles) { routes[path] = { handler, roles }; }

function isPublic(path) {
  return path === "#/" || path.startsWith("#/login") || path.startsWith("#/register");
}

async function router() {
  const hash = location.hash || "#/";
  const [path, query] = hash.split("?");
  const params = Object.fromEntries(new URLSearchParams(query || ""));

  // Not logged in -> only public routes allowed
  if (!state.token && !isPublic(path)) { location.hash = "#/"; return; }
  // Logged in -> bounce away from public routes to the correct role dashboard
  if (state.token && isPublic(path)) { location.hash = homeFor(getUserRole() || "owner"); return; }

  const matched = Object.keys(routes).find((r) => {
    const rp = r.split("/").map((s) => (s.startsWith(":") ? "(.+)" : s));
    return new RegExp("^" + rp.join("\\/") + "$").test(path);
  });

  if (!matched) {
    render(`${header("Not found", { back: true })}<div class="loading">Page not found. <a class="link" onclick="location.hash='${homeFor(getUserRole() || "owner")}'">Go home</a></div>`);
    return;
  }

  const def = routes[matched];
  const role = getUserRole();
  if (def.roles && role && !def.roles.includes(role)) {
    toast("Access denied — that page belongs to another role.", true);
    location.hash = homeFor(role);
    return;
  }

  const parts = matched.split("/");
  const pathParts = path.split("/");
  const routeParams = {};
  parts.forEach((p, i) => { if (p.startsWith(":")) routeParams[p.slice(1)] = decodeURIComponent(pathParts[i]); });

  try {
    await def.handler({ ...params, ...routeParams });
  } catch (e) {
    console.error(e);
    render(`${header("Error", { back: true })}<div class="loading">⚠️ ${e.message}</div>`);
  }
}
window.addEventListener("hashchange", router);
window.addEventListener("DOMContentLoaded", router);

// ================================================================= AUTH ==
const ROLE_META = {
  owner: { emoji: "🧑‍🌾", label: "role.owner", color: "#1fa971" },
  vet: { emoji: "🩺", label: "role.vet", color: "#2f6fed" },
  govt: { emoji: "🏛️", label: "role.govt", color: "#8e24aa" },
  lab: { emoji: "🔬", label: "role.lab", color: "#00838f" },
};

function renderRoleSelect() {
  render(`
  <div class="auth-wrap">
    <div class="auth-logo">
      <div class="emoji">🐄</div>
      <h2>PashuMitra</h2>
      <p>${t("app.tagline")}</p>
    </div>
    <div class="section-title" style="text-align:center;margin-bottom:14px">${t("auth.choose")}</div>
    <div class="role-cards">
      ${ROLES.map(r => `
        <div class="role-card" style="border-left:6px solid ${ROLE_META[r].color}" onclick="location.hash='#/login/${r}'">
          <div class="role-card-emoji">${ROLE_META[r].emoji}</div>
          <div class="role-card-body">
            <div class="role-card-title">${t(ROLE_META[r].label)}</div>
            <div class="role-card-desc">${t(ROLE_META[r].label + ".desc")}</div>
          </div>
          <div class="role-card-go">→</div>
        </div>`).join("")}
    </div>
    ${langToggle()}
  </div>`);
}

function validRole(role) { return ROLES.includes(role); }

function renderAuth(mode, role) {
  if (!validRole(role)) { location.hash = "#/"; return; }
  const isLogin = mode === "login";
  const meta = ROLE_META[role];
  render(`
  <div class="auth-wrap">
    <div class="auth-logo">
      <div class="emoji">${meta.emoji}</div>
      <h2>PashuMitra · ${t(meta.label)}</h2>
      <p>${t("app.tagline")}</p>
    </div>
    <div class="role-banner" style="background:${meta.color}1a;color:${meta.color}">
      ${meta.emoji} ${t(meta.label)} portal
    </div>
    ${isLogin ? loginForm(role) : registerForm(role)}
    ${langToggle()}
  </div>`);

  if (isLogin) {
    document.getElementById("loginForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = Object.fromEntries(new FormData(e.target));
      try {
        const data = await api("/auth/login", { method: "POST", body: fd });
        if (data.user.role !== role) {
          toast(`These credentials belong to the ${data.user.role} portal. Please use the correct login.`, true);
          return;
        }
        setAuth(data.token, data.user);
        toast(`Welcome back, ${data.user.full_name.split(" ")[0]}!`);
        location.hash = homeFor(role);
      } catch (err) { toast(err.message, true); }
    });
  } else {
    document.getElementById("registerForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = Object.fromEntries(new FormData(e.target));
      fd.role = role;
      try {
        const data = await api("/auth/register", { method: "POST", body: fd });
        setAuth(data.token, data.user);
        toast(`Account created for ${data.user.full_name}!`);
        location.hash = homeFor(role);
      } catch (err) { toast(err.message, true); }
    });
  }
}

function loginForm(role) {
  return `
  <form id="loginForm">
    <div class="field"><label>Email or Mobile</label><input name="identifier" required /></div>
    <div class="field"><label>Password</label><input name="password" type="password" required /></div>
    <button class="btn btn-primary" type="submit">${t("btn.login")}</button>
    <div class="auth-switch">${t("auth.newHere")} <a onclick="location.hash='#/register/${role}'">${t("auth.createAccount")}</a></div>
  </form>`;
}

function registerForm(role) {
  return `
  <form id="registerForm">
    <div class="field"><label>Full Name</label><input name="full_name" required /></div>
    <div class="form-row">
      <div class="field"><label>Mobile</label><input name="mobile" required /></div>
      <div class="field"><label>Email</label><input name="email" type="email" required /></div>
    </div>
    <div class="form-row">
      <div class="field"><label>Password</label><input name="password" type="password" minlength="6" required /></div>
      <div class="field"><label>Confirm</label><input name="confirm_password" type="password" minlength="6" required /></div>
    </div>
    ${role === "vet" || role === "lab" ? `<div class="field"><label>Specialization</label><input name="specialization" placeholder="e.g. Pathology / Epidemiology" /></div>` : ""}
    <div class="form-row">
      <div class="field"><label>Village</label><input name="village" /></div>
      <div class="field"><label>Block</label><input name="block" /></div>
    </div>
    <div class="field"><label>District</label><input name="district" placeholder="e.g. Pune" required /></div>
    <button class="btn btn-primary" type="submit">${t("btn.register")}</button>
    <div class="auth-switch">${t("auth.haveAccount")} <a onclick="location.hash='#/login/${role}'">${t("btn.login")}</a></div>
  </form>`;
}

route("#/", () => renderRoleSelect());
route("#/login/:role", ({ role }) => renderAuth("login", role));
route("#/register/:role", ({ role }) => renderAuth("register", role));

// ============================================================ DASHBOARDS ==
route("#/owner/dashboard", () => ownerDashboard(), ["owner"]);
route("#/vet/dashboard", () => vetDashboard(), ["vet"]);
route("#/govt/dashboard", () => govtDashboard(), ["govt"]);
route("#/lab/dashboard", () => labDashboard(), ["lab"]);

async function ownerDashboard() {
  render(`${header("PashuMitra")}<div class="loading">Loading your dashboard…</div>`);
  const summary = await api("/owner/summary");
  render(`
    ${header("PashuMitra")}
    <div class="hello-banner"><div style="margin-top:-16px;font-size:14px;opacity:0.9">Welcome back,</div><div style="font-size:19px;font-weight:800">${state.user.full_name} 👋</div></div>
    <div class="stat-grid">
      ${statCard(summary.animals, "My Animals")}
      ${statCard(summary.active_cases, "Active Cases")}
      ${statCard(summary.vaccinations_due, "Vax Due")}
      ${statCard(summary.lab_reports, "Lab Reports")}
      ${statCard(summary.prescriptions, "Prescriptions")}
      ${statCard("MH", "State: Maharashtra")}
    </div>
    <div class="section-card">
      <div class="section-title">Quick Actions</div>
      <div class="icon-grid">
        ${iconItem("➕", "Add Animal", "#/owner/animals/new")}
        ${iconItem("🐑", "Add Herd", "#/owner/herds/new")}
        ${iconItem("🏷️", "Scan QR", "#/scan")}
        ${iconItem("🚑", "Report Issue", "#/owner/report")}
        ${iconItem("🎤", "Voice Report", "#/owner/report?voice=1")}
        ${iconItem("🐄", "My Animals", "#/owner/animals")}
        ${iconItem("🩺", "My Cases", "#/owner/cases")}
        ${iconItem("🧪", "Lab Reports", "#/owner/lab-reports")}
        ${iconItem("💊", "Prescriptions", "#/owner/prescriptions")}
        ${iconItem("💉", "Vax Campaigns", "#/owner/campaigns")}
        ${iconItem("📖", "Disease Info", "#/owner/diseases")}
        ${iconItem("🐑", "My Herds", "#/owner/herds")}
        ${iconItem("🔔", "Notifications", "#/owner/notifications")}
      </div>
    </div>
    ${bottomNav("#/owner/dashboard")}
  `);
}

async function vetDashboard() {
  render(`${header("Vet Dashboard")}<div class="loading">Loading…</div>`);
  const summary = await api("/vet/summary");
  render(`
    ${header("Vet Dashboard")}
    <div class="hello-banner"><div style="margin-top:-16px;font-size:14px;opacity:0.9">Welcome,</div><div style="font-size:19px;font-weight:800">${state.user.full_name} 🩺</div></div>
    <div class="stat-grid">
      ${statCard(summary.new_cases, "🔴 New Cases")}
      ${statCard(summary.vaccinations_due, "🟠 Vax Due")}
      ${statCard(summary.lab_pending, "🧪 Lab Pending")}
      ${statCard(summary.user_reports, "📋 Total Reports")}
      ${statCard(summary.followups, "💊 Follow-ups")}
      ${statCard("MH", "State: Maharashtra")}
    </div>
    <div class="section-card">
      <div class="section-title">Today's Tasks</div>
      <div class="icon-grid">
        ${iconItem("📷", "Scan QR", "#/scan")}
        ${iconItem("📋", "User Reports", "#/vet/reports")}
        ${iconItem("🩺", "All Cases", "#/vet/cases")}
        ${iconItem("🧪", "Lab Reports", "#/vet/lab-reports")}
        ${iconItem("🔍", "Search Herd/Animal", "#/vet/search")}
        ${iconItem("💉", "Record Vaccination", "#/vet/vaccination/new")}
        ${iconItem("🗓️", "Vax Campaigns", "#/vet/campaigns")}
        ${iconItem("🌐", "Surveillance", "#/vet/surveillance")}
        ${iconItem("🚨", "Farm Alerts", "#/vet/farm-alerts")}
        ${iconItem("💊", "Prescriptions", "#/vet/prescriptions")}
        ${iconItem("📖", "Disease Info", "#/vet/diseases")}
        ${iconItem("🔔", "Notifications", "#/vet/notifications")}
      </div>
    </div>
    ${bottomNav("#/vet/dashboard")}
  `);
}

async function govtDashboard() {
  render(`${header("Govt Analytics")}<div class="loading">Loading state analytics…</div>`);
  const a = await api("/govt/analytics");
  render(`
    ${header("Govt Analytics")}
    <div class="hello-banner"><div style="margin-top:-16px;font-size:14px;opacity:0.9">Maharashtra Animal Disease & Vaccine Dashboard</div><div style="font-size:19px;font-weight:800">${state.user.full_name} 🏛️</div></div>
    <div class="stat-grid">
      ${statCard(a.totals.cases, "Total Cases")}
      ${statCard(a.totals.active, "Active Cases")}
      ${statCard(a.totals.animals, "Animals Registered")}
      ${statCard(a.totals.districts, "Districts Reporting")}
    </div>
    <div class="section-card">
      <div class="section-title">🗺️ GIS Risk Map & Surveillance</div>
      <div class="meta">Live district-level disease risk plotted on the Maharashtra map, plus full case surveillance and AI early warning.</div>
      <div class="btn-row" style="margin-top:12px">
        <button class="btn btn-primary btn-sm" onclick="location.hash='#/govt/gis'">Open GIS Map</button>
        <button class="btn btn-ghost btn-sm" onclick="location.hash='#/govt/surveillance'">Surveillance</button>
        <button class="btn btn-ghost btn-sm" onclick="location.hash='#/govt/national'">🇮🇳 National</button>
      </div>
    </div>
    <div class="section-card">
      <div class="section-title">📊 Cases Arisen by District</div>
      ${barChart(a.cases_by_district)}
    </div>
    <div class="section-card">
      <div class="section-title">🥧 Most Spread Diseases</div>
      ${pieChart(a.disease_spread)}
    </div>
    <div class="section-card">
      <div class="section-title">🚨 Herd & Farm Alerts</div>
      <button class="btn btn-ghost btn-sm" onclick="location.hash='#/govt/farm-alerts'">View Farm Disease Alerts</button>
    </div>
    <div class="section-card">
      <div class="section-title">🧠 AI Early Warning System</div>
      <div class="meta">Disease risk prediction &amp; outbreak detection — trained ML model scored on your real district case data.</div>
      <button class="btn btn-primary btn-sm" style="margin-top:10px" onclick="location.hash='#/govt/ai'">Open AI Risk Analysis</button>
    </div>
    ${bottomNav("#/govt/dashboard")}
  `);
}

async function labDashboard() {
  render(`${header("Laboratory Portal")}<div class="loading">Loading lab workstation…</div>`);
  const sum = await api("/lab/summary");
  render(`
    ${header("Laboratory Portal")}
    <div class="hello-banner"><div style="margin-top:-16px;font-size:14px;opacity:0.9">Regional Veterinary Diagnostics</div><div style="font-size:19px;font-weight:800">${state.user.full_name} 🔬</div></div>
    <div class="stat-grid">
      ${statCard(sum.pending_receiving, "📥 Intake Pending")}
      ${statCard(sum.in_testing, "🧪 In Testing")}
      ${statCard(sum.completed_today, "✅ Released Today")}
      ${statCard(sum.rejected_samples, "⚠️ Rejections")}
    </div>
    <div class="section-card">
      <div class="section-title">Quick Actions</div>
      <div class="icon-grid">
        ${iconItem("📷", "Scan / Receive", "#/scan")}
        ${iconItem("🧪", "Sample Queue", "#/lab/queue")}
        ${iconItem("📋", "All Reports", "#/lab/lab-reports")}
        ${iconItem("🔔", "Notifications", "#/lab/notifications")}
      </div>
    </div>
    ${bottomNav("#/lab/dashboard")}
  `);
}

// ======================================================== LABORATORY QUEUE & DETAIL ==
route("#/lab/queue", async () => {
  render(`${header("Sample Queue", { back: true })}<div class="loading">Loading samples…</div>`);
  const queue = await api("/lab/queue");
  render(`
    ${header("Diagnostic Intake & Testing", { back: true })}
    <div class="section-card">
      <div class="section-title">🧪 Biological Specimens Queue (${queue.length})</div>
      ${queue.length === 0 ? emptyState("No samples in queue.") : queue.map(s => `
        <div class="list-card" onclick="location.hash='#/lab/samples/${s.id}'">
          <div class="row1">
            <span class="title">${s.sample_code}</span>
            <span class="badge ${statusBadgeClass(s.status)}">${s.status}</span>
          </div>
          <div class="meta"><b>Animal:</b> ${s.animal_code} (${s.species}) · <b>Type:</b> ${s.sample_type}</div>
          <div class="meta">Case ${s.case_no} · Collected: ${fmtDate(s.collected_at)} by ${s.collector_name || "Vet"}</div>
        </div>
      `).join("")}
    </div>
    ${bottomNav("#/lab/queue")}
  `);
}, ["lab", "vet", "govt"]);

async function labSampleDetailView(id) {
  render(`${header("Sample Detail", { back: true })}<div class="loading">Loading sample…</div>`);
  const s = await api(`/samples/${id}`);
  render(`
    ${header("Sample " + s.sample_code, { back: true })}
    <div class="section-card">
      <div class="row1" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <div style="font-size:18px;font-weight:800">${s.sample_code}</div>
        <span class="badge ${statusBadgeClass(s.status)}">${s.status}</span>
      </div>
      <div class="detail-grid">
        <div><b>Sample Type</b>${s.sample_type}</div>
        <div><b>Case Number</b><a class="link" onclick="location.hash='#/vet/cases/${s.case_id}'">${s.case_no}</a></div>
        <div><b>Animal Code</b>${s.animal_code} (${s.species})</div>
        <div><b>Collection GPS</b>${s.collection_lat ? `${s.collection_lat.toFixed(4)}° N, ${s.collection_lng.toFixed(4)}° E` : "—"} ${s.is_manual_location ? "(Manual)" : "(Device GPS)"}</div>
        <div><b>Collected On</b>${fmtDate(s.collected_at)}</div>
        <div><b>Transporter</b>${s.transporter_name || "—"} ${s.transporter_phone ? `(${s.transporter_phone})` : ""}</div>
      </div>
      ${s.collection_notes ? `<div style="margin-top:10px"><b class="small-muted">Collection Notes:</b><div style="font-size:13px">${s.collection_notes}</div></div>` : ""}
      <div style="text-align:center;margin-top:14px">
        <div class="qr-image-wrap"><img src="${s.qr_image}" alt="Sample QR" /></div>
        <div class="small-muted">Sample QR Tag (${s.qr_token.slice(0, 16)}…)</div>
      </div>
    </div>

    <!-- WORKFLOW ACTION CARD -->
    <div class="section-card">
      <div class="section-title">⚙️ Laboratory Processing Workflow</div>
      ${s.status === "COLLECTED" || s.status === "READY_FOR_PICKUP" || s.status === "PICKED_UP" || s.status === "IN_TRANSIT" || s.status === "ARRIVED_AT_LAB" ? `
        <div class="meta" style="margin-bottom:12px">Specimen is awaiting laboratory intake inspection:</div>
        <button class="btn btn-primary" onclick="labAccept(${s.id})">✅ Accept Specimen (Mark LAB_RECEIVED)</button>
        <div style="margin-top:10px">
          <button class="btn btn-outline" onclick="document.getElementById('rejectBox').style.display='block'">❌ Reject Specimen</button>
        </div>
        <div id="rejectBox" style="display:none;margin-top:10px;background:#fde6e4;padding:12px;border-radius:12px">
          <div class="field"><label>Rejection Reason</label><input id="rejectReasonInput" placeholder="e.g. Hemolyzed, broken seal, delayed transport" /></div>
          <button class="btn btn-outline btn-sm" style="background:#fff" onclick="labReject(${s.id})">Confirm Rejection</button>
        </div>
      ` : s.status === "LAB_RECEIVED" ? `
        <div class="meta" style="margin-bottom:12px">Specimen accepted in lab intake. Assign to testing bench:</div>
        <button class="btn btn-primary" onclick="labStartTesting(${s.id})">🧪 Begin Diagnostic Testing (Mark TESTING)</button>
      ` : s.status === "TESTING" ? `
        <div class="subheading">🧬 Structured Result Entry</div>
        <form id="labResultForm">
          <div class="form-row">
            <div class="field"><label>Test Name</label><input name="test_name" placeholder="e.g. HS Culture Test / FMD ELISA" required /></div>
            <div class="field"><label>Test Type</label><select name="test_type"><option>Serology</option><option>Bacteriology</option><option>Molecular</option><option>Hematology</option></select></div>
          </div>
          <div class="form-row">
            <div class="field"><label>Test Method</label><input name="test_method" placeholder="e.g. ELISA / PCR / Culture" /></div>
            <div class="field"><label>Qualitative Result</label><select name="result"><option>NEGATIVE</option><option>POSITIVE</option><option>INCONCLUSIVE</option></select></div>
          </div>
          <div class="form-row">
            <div class="field"><label>Quantitative Value</label><input name="quantitative_result" type="number" step="0.01" placeholder="e.g. 0.05" /></div>
            <div class="field"><label>Units</label><input name="units" placeholder="e.g. OD / g/dL / titer" /></div>
          </div>
          <div class="field"><label>Abnormal Flag</label><select name="abnormal_flag"><option>Normal</option><option>Positive</option><option>High</option><option>Low</option><option>Abnormal</option></select></div>
          <div class="field"><label>Reference Range Note</label><input name="reference_range_text" placeholder="e.g. Cutoff OD &gt; 0.3 is Positive" /></div>
          <div class="field"><label>Technician Comments</label><textarea name="comments"></textarea></div>
          <button class="btn btn-primary" type="submit">Save Diagnostic Results</button>
        </form>
      ` : s.status === "RESULT_READY" ? `
        <div class="meta" style="margin-bottom:12px">Results entered and ready for official verification:</div>
        <button class="btn btn-primary" onclick="labVerify(${s.id})">✅ Verify &amp; Release Official Report</button>
      ` : `
        <div class="meta">🟢 Official report verified and published to veterinarian and owner.</div>
      `}
    </div>

    <!-- CHAIN OF CUSTODY TIMELINE -->
    <div class="section-card">
      <div class="subheading">⛓️ Chain of Custody &amp; Transport Timeline</div>
      <div class="timeline">
        ${(s.custody_events || []).map(e => `
          <div class="timeline-item">
            <div class="timeline-dot"></div>
            <div class="timeline-body">
              <div class="t-status">${e.status} — ${e.action}</div>
              <div class="t-note">${e.notes || ""} (${e.actor_name || "System"} · ${e.actor_role || ""})</div>
              <div class="t-date">${fmtDate(e.timestamp)}</div>
            </div>
          </div>
        `).join("")}
      </div>
    </div>
    ${bottomNav("#/lab/queue")}
  `);

  if (document.getElementById("labResultForm")) {
    document.getElementById("labResultForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const body = Object.fromEntries(new FormData(e.target));
        await api(`/samples/${id}/results`, { method: "POST", body });
        toast("Diagnostic results saved!");
        labSampleDetailView(id);
      } catch (err) { toast(err.message, true); }
    });
  }
}
route("#/lab/samples/:id", ({ id }) => labSampleDetailView(id), ["lab", "vet", "govt"]);

window.labAccept = async function(id) {
  try { await api(`/samples/${id}/receive`, { method: "POST", body: { action: "accept" } }); toast("Specimen accepted!"); labSampleDetailView(id); }
  catch (e) { toast(e.message, true); }
};
window.labReject = async function(id) {
  const reason = (document.getElementById("rejectReasonInput")?.value || "").trim();
  if (!reason) return toast("Please enter rejection reason", true);
  try { await api(`/samples/${id}/receive`, { method: "POST", body: { action: "reject", rejection_reason: reason } }); toast("Sample rejected and notifications sent"); labSampleDetailView(id); }
  catch (e) { toast(e.message, true); }
};
window.labStartTesting = async function(id) {
  try { await api(`/samples/${id}/test`, { method: "POST" }); toast("Diagnostic testing started"); labSampleDetailView(id); }
  catch (e) { toast(e.message, true); }
};
window.labVerify = async function(sampleId) {
  try {
    const s = await api(`/samples/${sampleId}`);
    const reports = await api("/lab/reports");
    const rep = reports.find(r => r.sample_id === Number(sampleId));
    if (rep) {
      await api(`/lab/reports/${rep.id}/verify`, { method: "POST" });
      toast("Report verified & published! Vet notified.");
    } else {
      toast("Report released!");
    }
    labSampleDetailView(sampleId);
  } catch (e) { toast(e.message, true); }
};

// ======================================================== UNIVERSAL QR SCANNER ==
let cameraStream = null;
let cameraTimer = null;

function renderScanner() {
  const role = getUserRole() || "owner";
  render(`
    ${header("Scan QR Identity", { back: true })}
    <div class="section-card">
      <div class="section-title">📷 Live Camera Scanner</div>
      <div class="meta" style="margin-bottom:12px">Point device camera at Animal Passport QR or Biological Sample QR:</div>
      <div class="scanner-video-wrap">
        <video id="scanVideo" playsinline autoplay muted></video>
        <div class="scanner-reticle"></div>
      </div>
      <div class="btn-row" style="margin-top:14px">
        <button class="btn btn-primary btn-sm" id="btnStartScan" onclick="startCameraScanner()">Start Camera</button>
        <button class="btn btn-outline btn-sm" id="btnStopScan" onclick="stopCameraScanner()">Stop Camera</button>
      </div>
      <div id="scanStatus" class="meta" style="margin-top:8px">Camera idle.</div>
    </div>
    <div class="section-card">
      <div class="section-title">📁 Upload QR Code Photo</div>
      <div class="field">
        <input type="file" id="qrFileInput" accept="image/*" capture="environment" onchange="handleFileScan(this)" />
      </div>
    </div>
    <div class="section-card">
      <div class="section-title">⌨️ Manual Identifier Fallback</div>
      <div class="meta" style="margin-bottom:8px">Enter Animal Code (e.g. MH-PUN-000001) or Sample ID (e.g. SMP-MH-PUN-000101):</div>
      <div class="form-row">
        <div class="field" style="margin:0"><input id="manualLookupInput" placeholder="e.g. MH-PUN-000001" /></div>
        <button class="btn btn-primary btn-sm" style="width:auto" onclick="handleManualLookup()">Lookup</button>
      </div>
    </div>
    ${bottomNav(homeFor(role))}
  `);
}
route("#/scan", () => renderScanner());

window.handleManualLookup = function() {
  const input = (document.getElementById("manualLookupInput")?.value || "").trim();
  if (!input) return toast("Please enter an identifier", true);
  resolveScannedPayload(input);
};

window.resolveScannedPayload = async function(payload) {
  const role = getUserRole() || "owner";
  toast(`Looking up: ${payload.slice(0, 24)}…`);
  try {
    if (payload.includes("SAMPLE:") || payload.startsWith("SMP-")) {
      const s = await api(`/samples/lookup-qr?payload=${encodeURIComponent(payload)}`);
      if (role === "lab") {
        location.hash = `#/lab/samples/${s.id}`;
      } else {
        location.hash = `#/${role}/cases/${s.case_id}`;
      }
    } else {
      const a = await api(`/animals/lookup-qr?payload=${encodeURIComponent(payload)}`);
      location.hash = `#/${role}/animals/${a.id}`;
    }
  } catch (e) {
    toast(e.message, true);
  }
};

window.handleFileScan = function(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async function(e) {
    const b64 = e.target.result;
    try {
      const res = await api("/qr/decode", { method: "POST", body: { image: b64 } });
      if (res.decoded && res.payload) {
        toast("QR code recognized!");
        resolveScannedPayload(res.payload);
      } else {
        toast("No readable QR code found in photo", true);
      }
    } catch (err) { toast(err.message, true); }
  };
  reader.readAsDataURL(file);
};

window.startCameraScanner = async function() {
  const video = document.getElementById("scanVideo");
  const status = document.getElementById("scanStatus");
  if (!video) return;
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
    video.srcObject = cameraStream;
    status.textContent = "🟢 Camera active. Position QR within the green square.";
    
    if ("BarcodeDetector" in window) {
      const detector = new BarcodeDetector({ formats: ["qr_code"] });
      cameraTimer = setInterval(async () => {
        try {
          const barcodes = await detector.detect(video);
          if (barcodes.length > 0) {
            const raw = barcodes[0].rawValue;
            stopCameraScanner();
            resolveScannedPayload(raw);
          }
        } catch (e) {}
      }, 500);
    } else {
      const canvas = document.createElement("canvas");
      const ctx = canvas.getContext("2d");
      cameraTimer = setInterval(async () => {
        if (!video.videoWidth) return;
        canvas.width = 300; canvas.height = 300;
        ctx.drawImage(video, 0, 0, 300, 300);
        const dataUrl = canvas.toDataURL("image/jpeg", 0.7);
        try {
          const res = await api("/qr/decode", { method: "POST", body: { image: dataUrl } });
          if (res.decoded && res.payload) {
            stopCameraScanner();
            resolveScannedPayload(res.payload);
          }
        } catch (e) {}
      }, 1500);
    }
  } catch (err) {
    status.textContent = "⚠️ Camera permission denied or not available. Please use image upload or manual lookup.";
  }
};

window.stopCameraScanner = function() {
  if (cameraTimer) clearInterval(cameraTimer);
  if (cameraStream) {
    cameraStream.getTracks().forEach(t => t.stop());
    cameraStream = null;
  }
  const status = document.getElementById("scanStatus");
  if (status) status.textContent = "Camera stopped.";
};

// ==================================================== GOVT: ANALYTICS ====
route("#/govt/analytics", async () => {
  render(`${header("Analytics & Reports", { back: true })}<div class="loading">Loading analytics…</div>`);
  const [a, geo] = await Promise.all([api("/govt/analytics"), api("/govt/geo")]);
  const districtRows = geo.map(d => `
    <tr>
      <td><b>${d.district}</b></td>
      <td><span class="badge ${riskBadgeClass(d.risk_level)}">${d.risk_level}</span></td>
      <td>${d.cases}</td>
      <td>${d.active}</td>
      <td>${d.high_severity}</td>
      <td>${d.affected_animals}</td>
      <td>${d.animal_population}</td>
    </tr>`).join("");
  render(`
    ${header("Analytics & Reports", { back: true })}
    <div class="section-card">
      <div class="section-title">📊 Key Metrics (Live Database)</div>
      <div class="stat-grid" style="margin:0">
        ${statCard(a.totals.cases, "Total Cases")}
        ${statCard(a.totals.active, "Active Cases")}
        ${statCard(a.totals.animals, "Animals Registered")}
        ${statCard(a.totals.districts, "Reporting Districts")}
      </div>
    </div>
    <div class="section-card">
      <div class="section-title">📈 Cases Arisen by District</div>
      ${barChart(a.cases_by_district)}
    </div>
    <div class="section-card">
      <div class="section-title">🥧 Disease Spread Distribution</div>
      ${pieChart(a.disease_spread)}
    </div>
    <div class="section-card">
      <div class="section-title">📋 District Risk & Case Table</div>
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr><th>District</th><th>Risk</th><th>Cases</th><th>Active</th><th>High Sev</th><th>Affected</th><th>Pop</th></tr></thead>
          <tbody>${districtRows}</tbody>
        </table>
      </div>
    </div>
    <div class="section-card">
      <div class="section-title">💉 Vaccine Stock Summary</div>
      ${Object.keys(a.vaccine_stock).length === 0 ? emptyState("No stock data yet.") :
        Object.entries(a.vaccine_stock).map(([dist, items]) => `
          <div style="margin:8px 0">
            <b>${dist}</b>: ${items.map(i => `${i.vaccine} (${i.doses} doses)`).join(", ")}
          </div>`).join("")}
      <button class="btn btn-ghost btn-sm" style="margin-top:10px" onclick="location.hash='#/govt/stock'">Manage Vaccine Stock</button>
    </div>
    ${bottomNav("#/govt/dashboard")}
  `);
}, ["govt"]);

// ========================================================= GOVT: GIS MAP ==
let gisState = { geo: [], locations: [], outline: null, disease: "All", risks: ["High Risk", "Moderate Risk", "Low Risk"], map: null, layer: null, showClusters: false, clusters: [] };

route("#/govt/gis", async () => {
  render(`${header("GIS Risk Map", { back: true })}<div class="loading">Loading map & live district data…</div>`);
  const [geo, locations, outline, clusterData] = await Promise.all([
    api("/govt/geo"),
    fetch("/maharashtra_locations.json").then(r => r.json()).catch(() => []),
    fetch("/maharashtra_state.geojson").then(r => r.json()).catch(() => null),
    api("/govt/clusters").catch(() => ({ clusters: [] })),
  ]);
  gisState = { ...gisState, geo, locations, outline, clusters: clusterData.clusters || [], map: null, layer: null };
  const diseases = ["All", ...Array.from(new Set(geo.flatMap(d => d.diseases.map(x => x.label))))];
  render(`
    ${header("GIS Risk Map", { back: true })}
    <div class="gis-status ${navigator.onLine ? "online" : "offline"}">
      ${navigator.onLine ? "🟢 ONLINE / LIVE DATA — synchronized with central server." : "🟠 OFFLINE MODE — showing cached boundaries."}
    </div>
    <div class="section-card" style="padding-bottom:8px">
      <div class="gis-controls">
        <div class="field" style="margin-bottom:8px"><label>Disease filter</label>
          <select id="gisDisease">${diseases.map(d => `<option ${d === gisState.disease ? "selected" : ""}>${d}</option>`).join("")}</select>
        </div>
        <div class="gis-risk-toggles">
          ${["High Risk", "Moderate Risk", "Low Risk"].map(r => `
            <label class="risk-chip"><input type="checkbox" data-risk="${r}" ${gisState.risks.includes(r) ? "checked" : ""}/> <span class="dot-${r === "High Risk" ? "red" : r === "Moderate Risk" ? "orange" : "green"}"></span>${r}</label>`).join("")}
          <label class="risk-chip"><input type="checkbox" id="gisClusterToggle" ${gisState.showClusters ? "checked" : ""} /> <b>📍 DBSCAN Clusters</b></label>
        </div>
      </div>
    </div>
    <div class="section-card" style="padding:0;overflow:hidden">
      <div id="gisMap" class="gis-map"></div>
    </div>
    <div class="section-card">
      <div class="section-title">🚨 Risk Summary (filtered)</div>
      <div id="gisSummary"></div>
    </div>
    <div class="section-card">
      <div class="section-title">📍 Top Affected Districts</div>
      <div id="gisTop"></div>
    </div>
    ${bottomNav("#/govt/gis")}
  `);
  initGisMap();
  document.getElementById("gisDisease").addEventListener("change", e => { gisState.disease = e.target.value; drawGis(); });
  document.getElementById("gisClusterToggle").addEventListener("change", e => { gisState.showClusters = e.target.checked; drawGis(); });
  document.querySelectorAll(".gis-risk-toggles input[data-risk]").forEach(cb =>
    cb.addEventListener("change", () => {
      const r = cb.dataset.risk;
      gisState.risks = cb.checked ? [...new Set([...gisState.risks, r])] : gisState.risks.filter(x => x !== r);
      drawGis();
    }));
}, ["govt", "vet"]);

function initGisMap() {
  if (typeof L === "undefined") {
    document.getElementById("gisMap").innerHTML = emptyState("Map library failed to load.");
    return;
  }
  const map = L.map("gisMap").setView([19.7515, 75.7139], 7);
  if (navigator.onLine) {
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; OpenStreetMap contributors', maxZoom: 18,
    }).addTo(map);
  }
  if (gisState.outline) {
    L.geoJSON(gisState.outline, { style: { color: "#3b82f6", weight: 2, fillOpacity: 0.04, fillColor: "#3b82f6" } }).addTo(map);
  }
  gisState.map = map;
  gisState.layer = L.layerGroup().addTo(map);
  drawGis();
  setTimeout(() => map.invalidateSize(), 200);
}

function drawGis() {
  const { map, layer, geo, locations, disease, risks, showClusters, clusters } = gisState;
  if (!map || !layer) return;
  layer.clearLayers();
  const colorFor = r => r === "High Risk" ? "#e2483f" : r === "Moderate Risk" ? "#e08a1e" : "#1fa971";
  const filtered = geo.filter(d => {
    if (!risks.includes(d.risk_level)) return false;
    if (disease !== "All" && !d.diseases.some(x => x.label === disease)) return false;
    return true;
  });

  filtered.forEach(d => {
    const loc = locations.find(l => l.district.toLowerCase() === d.district.toLowerCase());
    if (!loc) return;
    const radius = d.risk_level === "High Risk" ? 20 : d.risk_level === "Moderate Risk" ? 15 : 10;
    L.circleMarker([loc.lat, loc.lng], { radius, color: colorFor(d.risk_level), fillColor: colorFor(d.risk_level), fillOpacity: 0.6 })
      .bindTooltip(`<div style="font-weight:700">${d.district}</div>
        <div style="color:${colorFor(d.risk_level)};font-weight:700;font-size:12px">${d.risk_level}</div>
        <div style="font-size:12px">Cases: ${d.cases} · Active: ${d.active}</div>
        <div style="font-size:12px">Affected animals: ${d.affected_animals}</div>
        <div style="font-size:12px">Diseases: ${d.diseases.map(x => x.label).join(", ") || "—"}</div>`)
      .addTo(layer);
  });

  // FEATURE GROUP 17: REAL SPATIOTEMPORAL DBSCAN CLUSTERS
  if (showClusters && clusters && clusters.length) {
    clusters.forEach(c => {
      L.circleMarker([c.lat, c.lng], {
        radius: Math.max(14, c.cases * 8),
        color: "#8e24aa",
        fillColor: "#e1bee7",
        fillOpacity: 0.7,
        weight: 3,
        dashArray: "4, 4"
      }).bindTooltip(`
        <div style="font-weight:800;color:#8e24aa">📍 Spatiotemporal Cluster: ${c.cluster_id}</div>
        <div style="font-size:12px"><b>District:</b> ${c.district}</div>
        <div style="font-size:12px"><b>Cases:</b> ${c.cases} active</div>
        <div style="font-size:12px"><b>Diseases:</b> ${c.diseases ? c.diseases.join(", ") : "HS"}</div>
        <div style="font-size:11px;color:#666">Method: ${c.method || "DBSCAN (haversine)"}</div>
      `).addTo(layer);
    });
  }

  const high = filtered.filter(d => d.risk_level === "High Risk").length;
  const mod = filtered.filter(d => d.risk_level === "Moderate Risk").length;
  const low = filtered.filter(d => d.risk_level === "Low Risk").length;
  document.getElementById("gisSummary").innerHTML = `
    <div class="stat-grid" style="margin:0">
      ${statCard(high, "🔴 High Risk")}
      ${statCard(mod, "🟠 Moderate")}
      ${statCard(low, "🟢 Low Risk")}
    </div>`;
  const top = [...filtered].sort((a, b) => b.affected_animals - a.affected_animals).slice(0, 5);
  document.getElementById("gisTop").innerHTML = top.length === 0 ? emptyState("No matching districts.") :
    top.map(d => `<div class="list-card" style="cursor:default">
      <div class="row1"><span class="title">${d.district}</span><span class="badge ${riskBadgeClass(d.risk_level)}">${d.risk_level}</span></div>
      <div class="meta">${d.affected_animals} affected · ${d.cases} cases · ${d.high_severity} high-severity</div>
    </div>`).join("");
}

// ================================================ SURVEILLANCE (govt/vet) =
function surveillanceView(role) {
  route(`#/${role}/surveillance`, async () => {
    render(`${header("Surveillance", { back: true })}<div class="loading">Loading surveillance cases…</div>`);
    const [cases, geo] = await Promise.all([api("/cases"), api("/govt/geo")]);
    render(`
      ${header("Disease Surveillance", { back: true })}
      <div class="section-card">
        <div class="section-title">🌐 Active Disease Clusters (by District)</div>
        <div class="meta" style="margin-bottom:12px">Real-time aggregate data reported across field veterinarians and livestock owners:</div>
        <div class="stat-grid" style="margin:0 0 14px 0">
          ${statCard(cases.length, "Total Reports")}
          ${statCard(cases.filter(c => !["CLOSED", "RECOVERED"].includes(c.status)).length, "Active Cases")}
          ${statCard(geo.filter(g => g.risk_level === "High Risk").length, "High Risk Dist.")}
        </div>
        ${geo.map(g => `
          <div class="list-card" style="cursor:default">
            <div class="row1"><span class="title">${g.district}</span><span class="badge ${riskBadgeClass(g.risk_level)}">${g.risk_level}</span></div>
            <div class="meta">Cases: ${g.cases} (${g.active} active) · Affected: ${g.affected_animals} · High severity: ${g.high_severity}</div>
            <div class="meta">Top Diseases: ${g.diseases.map(d => `${d.label} (${d.value})`).join(", ") || "—"}</div>
          </div>`).join("")}
      </div>
      <div class="section-card">
        <div class="section-title">📋 Recent Incident Reports</div>
        ${cases.slice(0, 10).map(c => `
          <div class="list-card" onclick="location.hash='#/${role}/cases/${c.id}'">
            <div class="row1"><span class="title">${c.case_no}</span><span class="badge ${statusBadgeClass(c.status)}">${c.status}</span></div>
            <div class="meta">${c.animal ? c.animal.animal_code : "—"} · ${c.disease_suspected || c.diagnosis || "Unspecified"} · ${fmtDate(c.created_at)}</div>
          </div>`).join("")}
      </div>
      ${bottomNav(`#/${role}/surveillance`)}
    `);
  }, [role]);
}
surveillanceView("govt"); surveillanceView("vet");

// ======================================================== NATIONAL SURVEILLANCE ==
function nationalSurveillanceView() {
  route("#/govt/national", async () => {
    render(`${header("National Surveillance", { back: true })}<div class="loading">Loading national data…</div>`);
    const data = await api("/national/surveillance");
    render(`
      ${header("National Surveillance", { back: true })}
      <div class="section-card">
        <div class="section-title">🇮🇳 India Livestock Health Chain Hierarchy</div>
        <div class="meta" style="font-size:12px;margin-bottom:12px"><b>Surveillance Layer:</b> Country → State → District → Block → Herd → Animal</div>
        <div class="stat-grid" style="margin:0">
          ${statCard(data.total_national_animals, "National Animals")}
          ${statCard(data.total_national_cases, "National Cases")}
          ${statCard(data.total_national_active, "Active Episodes")}
          ${statCard(data.states.length, "Federated States")}
        </div>
      </div>

      <div class="section-card">
        <div class="section-title">🏛️ State Surveillance Nodes</div>
        <div class="table-wrap">
          <table class="data-table">
            <thead><tr><th>State</th><th>Node Status</th><th>Animals</th><th>Active</th><th>Reporting Risk</th></tr></thead>
            <tbody>
              ${data.states.map(s => `
                <tr>
                  <td><b>${s.state}</b></td>
                  <td>${s.reporting_status}</td>
                  <td>${s.animals_registered}</td>
                  <td>${s.active_cases}</td>
                  <td><span class="badge ${s.active_cases > 0 ? "badge-orange" : "badge-blue"}">${s.risk_index}</span></td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      </div>

      <div class="section-card">
        <div class="subheading">🚨 National &amp; Cross-State Outbreak Alerts</div>
        ${data.national_alerts.length === 0 ? emptyState("No active national alerts.") :
          data.national_alerts.map(a => `
          <div class="list-card" style="cursor:default">
            <div class="row1">
              <span class="title">${a.title}</span>
              <span class="badge ${a.severity === 'CRITICAL' ? 'badge-red' : 'badge-orange'}">${a.severity}</span>
            </div>
            <div class="meta"><b>Disease:</b> ${a.disease} · <b>State:</b> ${a.state} (${a.district || "Statewide"})</div>
            <div class="meta">${a.description}</div>
            <div class="meta" style="color:var(--primary);margin-top:4px"><b>Measures:</b> ${a.recommended_measures}</div>
          </div>
        `).join("")}
      </div>

      <div class="section-card">
        <div class="subheading">+ Issue National Outbreak Alert</div>
        <form id="nationalAlertForm">
          <div class="field"><label>Alert Title</label><input name="title" placeholder="e.g. Western Zone FMD Movement Advisory" required /></div>
          <div class="form-row">
            <div class="field"><label>Disease</label><input name="disease" placeholder="e.g. FMD" required /></div>
            <div class="field"><label>State</label><input name="state" value="Maharashtra" required /></div>
          </div>
          <div class="form-row">
            <div class="field"><label>Severity</label><select name="severity"><option>HIGH</option><option>CRITICAL</option><option>MODERATE</option></select></div>
            <div class="field"><label>Affected Count</label><input name="affected_count" type="number" value="1" /></div>
          </div>
          <div class="field"><label>Alert Description</label><textarea name="description" placeholder="Summary of outbreak pattern and epicenter coordinates"></textarea></div>
          <div class="field"><label>Recommended Measures</label><input name="recommended_measures" placeholder="e.g. Quarantine border, ring vaccination" /></div>
          <button class="btn btn-primary" type="submit">Publish National Alert</button>
        </form>
      </div>
      ${bottomNav("#/govt/dashboard")}
    `);

    document.getElementById("nationalAlertForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const body = Object.fromEntries(new FormData(e.target));
        await api("/national/alerts", { method: "POST", body });
        toast("National alert published!");
        nationalSurveillanceView();
      } catch (err) { toast(err.message, true); }
    });
  }, ["govt", "vet"]);
}
nationalSurveillanceView();

// ======================================================== FARM ALERTS VIEW ==
function farmAlertsView(role) {
  route(`#/${role}/farm-alerts`, async () => {
    render(`${header("Farm Alerts", { back: true })}<div class="loading">Loading farm alerts…</div>`);
    const alerts = await api("/farm-alerts");
    render(`
      ${header("Herd & Farm Intelligence Alerts", { back: true })}
      <div class="section-card">
        <div class="section-title">🚨 Active Herd Health Warnings</div>
        <div class="meta" style="margin-bottom:12px">Automated triggers detecting localized cluster acceleration and vaccination gaps:</div>
        ${alerts.length === 0 ? emptyState("No active farm-level disease alerts.") : alerts.map(a => `
          <div class="list-card" style="cursor:default">
            <div class="row1">
              <span class="title">${a.herd_code} — ${a.disease}</span>
              <span class="badge ${riskBadgeClass(a.risk_level)}">${a.risk_level}</span>
            </div>
            <div class="meta"><b>Location:</b> ${a.district} · <b>Trigger:</b> ${a.trigger_reason}</div>
            <div class="meta"><b>Recommended Action:</b> ${a.recommended_action || "—"}</div>
            ${a.supporting_evidence ? `<div class="small-muted">Evidence: ${a.supporting_evidence}</div>` : ""}
            <div class="row1" style="margin-top:8px">
              <span class="badge badge-blue">Status: ${a.status}</span>
              ${role !== "owner" && a.status === "ACTIVE" ? `
                <div style="display:flex;gap:6px">
                  <button class="btn btn-outline btn-sm" onclick="ackFarmAlert(${a.id})">Acknowledge</button>
                  <button class="btn btn-primary btn-sm" onclick="resolveFarmAlert(${a.id})">Resolve</button>
                </div>
              ` : ""}
            </div>
          </div>
        `).join("")}
      </div>
      ${bottomNav(homeFor(role))}
    `);
  }, [role]);
}
farmAlertsView("owner"); farmAlertsView("vet"); farmAlertsView("govt");

window.ackFarmAlert = async function(id) {
  try { await api(`/farm-alerts/${id}/acknowledge`, { method: "POST" }); toast("Alert acknowledged."); router(); }
  catch (e) { toast(e.message, true); }
};
window.resolveFarmAlert = async function(id) {
  try { await api(`/farm-alerts/${id}/resolve`, { method: "POST" }); toast("Alert resolved."); router(); }
  catch (e) { toast(e.message, true); }
};

// ==================================================== VAX CAMPAIGNS =====
function campaignsView(role) {
  route(`#/${role}/campaigns`, async () => {
    render(`${header("Campaigns", { back: true })}<div class="loading">Loading vaccination campaigns…</div>`);
    const camps = await api("/campaigns");
    const canManage = role === "govt";
    render(`
      ${header("Vaccination Campaigns", { back: true })}
      <div class="section-card">
        ${canManage ? `<button class="btn btn-primary" style="margin-bottom:14px" id="btnNewCamp">+ Create Campaign</button>` : ""}
        ${camps.length === 0 ? emptyState("No vaccination campaigns scheduled.") : camps.map(c => campCard(c, canManage || role === "vet")).join("")}
      </div>
      <div id="campModalWrap"></div>
      ${bottomNav(`#/${role}/campaigns`)}
    `);
    bindCampaignManage(camps, role);
  }, [role]);
}
campaignsView("owner"); campaignsView("vet"); campaignsView("govt");

function campCard(c, canUpdate) {
  const pct = c.target_animals ? Math.min(100, Math.round((c.doses_administered / c.target_animals) * 100)) : 0;
  return `
    <div class="list-card" style="cursor:default">
      <div class="row1"><span class="title">${c.name}</span><span class="badge ${campStatusClass(c.status)}">${c.status}</span></div>
      <div class="meta"><b>${c.vaccine}</b> · ${c.district} · ${c.campaign_code}</div>
      <div class="meta">Dates: ${fmtDate(c.start_date)} → ${fmtDate(c.end_date)}</div>
      <div class="progress-wrap"><div class="progress-bar" style="width:${pct}%"></div></div>
      <div class="row1" style="margin-top:2px"><span class="meta">${c.doses_administered} / ${c.target_animals} doses (${pct}%)</span>
        ${canUpdate ? `<button class="btn btn-ghost btn-sm btn-edit-camp" data-id="${c.id}">Update</button>` : ""}
      </div>
    </div>`;
}
function campStatusClass(s) {
  if (s === "ACTIVE") return "badge-green";
  if (s === "PLANNED") return "badge-blue";
  return "badge-orange";
}
function bindCampaignManage(camps, role) {
  document.querySelectorAll(".btn-edit-camp").forEach(b => {
    b.addEventListener("click", () => {
      const camp = camps.find(x => x.id === Number(b.dataset.id));
      if (!camp) return;
      renderCampModal(camp, role === "govt");
    });
  });
  const btnNew = document.getElementById("btnNewCamp");
  if (btnNew) btnNew.addEventListener("click", () => renderCampModal(null, true));
}

function renderCampModal(camp, isGovt) {
  const isEdit = !!camp;
  const wrap = document.getElementById("campModalWrap");
  wrap.innerHTML = `
    <div class="section-card" style="border:2px solid var(--primary-light)">
      <div class="subheading">${isEdit ? "Update Campaign: " + camp.campaign_code : "Create New Campaign"}</div>
      <form id="campForm">
        ${!isEdit ? `
          <div class="field"><label>Campaign Name</label><input name="name" required placeholder="e.g. FMD Drive Haveli" /></div>
          <div class="form-row">
            <div class="field"><label>District</label><input name="district" required placeholder="e.g. Pune" /></div>
            <div class="field"><label>Vaccine</label><select name="vaccine"><option>FMD</option><option>HS</option><option>BQ</option><option>Brucellosis</option></select></div>
          </div>
          <div class="form-row">
            <div class="field"><label>Target Animals</label><input name="target_animals" type="number" required /></div>
            <div class="field"><label>Start Date</label><input name="start_date" type="date" required /></div>
          </div>
          <div class="field"><label>End Date</label><input name="end_date" type="date" required /></div>
        ` : `
          <div class="form-row">
            <div class="field"><label>Doses Administered</label><input name="doses_administered" type="number" value="${camp.doses_administered}" required /></div>
            <div class="field"><label>Status</label><select name="status">
              ${["PLANNED", "ACTIVE", "COMPLETED", "CANCELLED"].map(s => `<option ${s === camp.status ? "selected" : ""}>${s}</option>`).join("")}
            </select></div>
          </div>
        `}
        <div class="field"><label>Notes</label><textarea name="notes">${isEdit ? camp.notes || "" : ""}</textarea></div>
        <div class="btn-row">
          <button class="btn btn-primary btn-sm" type="submit">${isEdit ? "Save Changes" : "Create Campaign"}</button>
          <button class="btn btn-outline btn-sm" type="button" onclick="document.getElementById('campModalWrap').innerHTML=''">Cancel</button>
          ${isEdit && isGovt ? `<button class="btn btn-outline btn-sm" style="color:var(--red);border-color:var(--red)" type="button" id="btnDelCamp">Delete</button>` : ""}
        </div>
      </form>
    </div>`;

  document.getElementById("campForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(e.target));
    try {
      if (isEdit) {
        await api(`/campaigns/${camp.id}`, { method: "PUT", body: fd });
        toast("Campaign updated!");
      } else {
        await api("/campaigns", { method: "POST", body: fd });
        toast("Campaign created!");
      }
      router();
    } catch (err) { toast(err.message, true); }
  });

  const delBtn = document.getElementById("btnDelCamp");
  if (delBtn) delBtn.addEventListener("click", async () => {
    if (!confirm(`Delete campaign ${camp.campaign_code}?`)) return;
    try { await api(`/campaigns/${camp.id}`, { method: "DELETE" }); toast("Campaign deleted"); router(); }
    catch (err) { toast(err.message, true); }
  });
}

// ======================================================== VACCINE STOCK ==
route("#/govt/stock", async () => {
  render(`${header("Vaccine Stock", { back: true })}<div class="loading">Loading stock…</div>`);
  const a = await api("/govt/analytics");
  const stock = a.vaccine_stock || {};
  render(`
    ${header("Vaccine Stock", { back: true })}
    <div class="section-card">
      <div class="section-title">📦 Current District Inventories</div>
      ${Object.keys(stock).length === 0 ? emptyState("No stock recorded yet.") :
        Object.entries(stock).map(([dist, items]) => `
          <div class="list-card" style="cursor:default">
            <div class="row1"><span class="title">${dist}</span></div>
            <div class="tag-row">
              ${items.map(i => `<span class="badge badge-blue">${i.vaccine}: ${i.doses} doses</span>`).join("")}
            </div>
          </div>`).join("")}
    </div>
    <div class="section-card">
      <div class="subheading">+ Add or Update Stock</div>
      <form id="stockForm">
        <div class="form-row">
          <div class="field"><label>District</label><input name="district" required placeholder="e.g. Pune" /></div>
          <div class="field"><label>Vaccine</label><select name="vaccine"><option>FMD</option><option>HS</option><option>BQ</option><option>Brucellosis</option></select></div>
        </div>
        <div class="field"><label>Doses Available</label><input name="doses" type="number" required placeholder="e.g. 1500" /></div>
        <button class="btn btn-primary" type="submit">Update Stock</button>
      </form>
    </div>
    ${bottomNav("#/govt/dashboard")}
  `);
  document.getElementById("stockForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const body = Object.fromEntries(new FormData(e.target));
      await api("/govt/stock", { method: "PUT", body });
      toast("Vaccine stock saved"); router();
    } catch (err) { toast(err.message, true); }
  });
}, ["govt"]);

// ========================================================= GOVT: AI RISK ==
const AI_DISEASES = ["HS", "FMD", "BQ", "LSD", "Brucellosis", "PPR"];

route("#/govt/ai", async () => {
  render(`${header("AI Early Warning", { back: true })}<div class="loading">Loading AI system…</div>`);
  const [districts, modelStatus] = await Promise.all([
    api("/govt/ai/districts"),
    api("/govt/ai/status").catch(() => ({ online: false })),
  ]);
  render(`
    ${header("AI Early Warning", { back: true })}
    <div class="section-card">
      <div class="section-title">🧠 Disease Risk Prediction</div>
      <div class="meta" style="margin-bottom:10px">
        ${modelStatus.online
          ? `AI model <b>online</b> — ${modelStatus.model}, accuracy ${Math.round(modelStatus.accuracy * 100)}% (ROC-AUC ${modelStatus.roc_auc})`
          : `⚠️ AI model <b>offline</b>. Start the ml-backend service (port 8000) to enable predictions.`}
      </div>
      <form id="aiForm">
        <div class="form-row">
          <div class="field"><label>District</label>
            <select name="district" required>
              ${districts.length ? districts.map(d => `<option>${d}</option>`).join("") : `<option value="">No districts yet</option>`}
            </select>
          </div>
          <div class="field"><label>Disease</label>
            <select name="disease" required>${AI_DISEASES.map(d => `<option>${d}</option>`).join("")}</select>
          </div>
        </div>
        <button class="btn btn-primary" type="submit" ${modelStatus.online ? "" : "disabled"}>Predict Risk</button>
      </form>
    </div>
    <div id="aiResults"></div>
    ${bottomNav("#/govt/ai")}
  `);
  document.getElementById("aiForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    if (!f.district) return toast("No district data available yet", true);
    const box = document.getElementById("aiResults");
    box.innerHTML = `<div class="loading">Running AI prediction on live district & weather data…</div>`;
    try {
      const qs = `district=${encodeURIComponent(f.district)}&disease=${encodeURIComponent(f.disease)}`;
      const [pred, outbreak] = await Promise.all([api(`/govt/ai/predict?${qs}`), api(`/govt/ai/outbreak?${qs.split("&")[0]}`).catch(() => null)]);
      const feat = pred.features_used || {};
      box.innerHTML = `
        <div class="section-card">
          <div class="section-title">📈 ${f.disease} — ${f.district}</div>
          <div class="stat-grid">
            ${statCard(pred.risk_score + "%", "Risk Score")}
            <div class="stat-card"><div class="num" style="font-size:18px"><span class="badge ${riskBadgeClass(pred.risk_level)}">${pred.risk_level}</span></div><div class="lbl">Risk Level</div></div>
            ${statCard(pred.trend, "Trend")}
            ${statCard(pred.predicted_cases, "Predicted Cases (14d)")}
          </div>
        </div>
        <div class="section-card">
          <div class="section-title">🎯 Top Risk Factors (model importance)</div>
          ${barChart((pred.top_risk_factors || []).map(t2 => ({ label: `${t2.factor} (value: ${t2.value})`, value: Math.round(t2.impact * 100) })))}
        </div>
        <div class="section-card">
          <div class="section-title">✅ Recommended Actions</div>
          ${(pred.recommended_actions || []).map(a => `<div class="meta" style="margin:4px 0">• ${a}</div>`).join("")}
        </div>
        ${outbreak ? `
        <div class="section-card">
          <div class="section-title">🚨 Outbreak Detection (Isolation Forest)</div>
          <div class="meta">Detected: <b>${outbreak.outbreak_detected ? "YES ⚠️" : "No"}</b> · Severity: <b>${outbreak.severity}</b> · Anomaly score: ${outbreak.anomaly_score}</div>
        </div>` : ""}
        <div class="section-card">
          <div class="section-title">🗄 Live Data & Weather Used (from database)</div>
          <div class="meta">Animals registered: <b>${feat.animal_population}</b></div>
          <div class="meta">Affected animals: <b>${feat.affected_animals}</b></div>
          <div class="meta">New cases (30 days): <b>${feat.new_cases}</b> · Growth rate: <b>${feat.cases_growth_rate}</b></div>
          <div class="meta">Vaccination coverage: <b>${Math.round((feat.vaccination_coverage || 0) * 100)}%</b></div>
          <div class="meta">Weather Temperature: <b>${feat.temperature}°C</b> · Humidity: <b>${feat.humidity}%</b> · Rainfall: <b>${feat.rainfall} mm</b></div>
          <div class="meta small-muted">Weather Source: ${feat.weather_source || "Open-Meteo API"}</div>
        </div>`;
    } catch (err) {
      box.innerHTML = `<div class="section-card"><div class="meta">⚠️ ${err.message}</div></div>`;
    }
  });
}, ["govt"]);

// ------------------------------------------------------- disease library --
function diseasesView(role) {
  route(`#/${role}/diseases`, async () => {
    render(`${header("Disease Info", { back: true })}<div class="loading">Loading disease library…</div>`);
    const diseases = await api("/diseases");
    render(`
      ${header("Disease Information", { back: true })}
      <div class="section-card">
        <div class="field" style="margin-bottom:12px">
          <input id="diseaseSearch" placeholder="Search diseases (e.g. FMD, लाळ खुरकूत, Anthrax)…" />
        </div>
        <div id="diseaseList"></div>
      </div>
      ${bottomNav(role === "govt" ? "#/govt/dashboard" : `#/${role}/dashboard`)}
    `);
    const list = document.getElementById("diseaseList");
    function renderList(items) {
      if (!items.length) { list.innerHTML = emptyState("No matching diseases."); return; }
      list.innerHTML = items.map(d => `
        <div class="list-card" style="cursor:default">
          <div class="row1"><span class="title">${d.name_en} <span style="font-weight:400;color:var(--muted)">(${d.name_mr})</span></span>
            <span class="badge ${d.species.includes("Cattle") ? "badge-blue" : "badge-orange"}">${d.species.join(", ")}</span>
          </div>
          <div class="meta" style="color:var(--text);margin-top:4px"><b>${t("nav.reporting")} / Symptoms:</b> ${d.symptoms_en.join(", ")}</div>
          <div class="meta" style="font-style:italic">${d.symptoms_mr.join(", ")}</div>
          <div class="meta" style="margin-top:4px"><b>Prevention:</b> ${d.prevention_en.join("; ")}</div>
        </div>`).join("");
    }
    renderList(diseases);
    document.getElementById("diseaseSearch").addEventListener("input", e => {
      const q = e.target.value.toLowerCase().trim();
      if (!q) return renderList(diseases);
      renderList(diseases.filter(d =>
        d.name_en.toLowerCase().includes(q) || d.name_mr.includes(q) ||
        d.symptoms_en.some(s => s.toLowerCase().includes(q)) ||
        d.symptoms_mr.some(s => s.includes(q)) ||
        (d.aliases || []).some(a => a.toLowerCase().includes(q))
      ));
    });
  }, [role]);
}
diseasesView("owner"); diseasesView("vet"); diseasesView("govt"); diseasesView("lab");

// --------------------------------------------------------------- profile --
function profileView(role) {
  route(`#/${role}/profile`, () => {
    const u = state.user || {};
    render(`
      ${header("My Profile", { back: true })}
      <div class="section-card">
        <div style="text-align:center;margin-bottom:16px">
          <div style="font-size:52px">${ROLE_META[role].emoji}</div>
          <div style="font-size:18px;font-weight:800">${u.full_name || "User"}</div>
          <div class="badge badge-blue" style="margin-top:4px">${t(ROLE_META[role].label)}</div>
        </div>
        <div class="detail-grid">
          <div><b>Mobile</b>${u.mobile || "—"}</div>
          <div><b>Email</b>${u.email || "—"}</div>
          <div><b>Village</b>${u.village || "—"}</div>
          <div><b>Block</b>${u.block || "—"}</div>
          <div><b>District</b>${u.district || "—"}</div>
          <div><b>Specialization</b>${u.specialization || "General"}</div>
        </div>
      </div>
      <div class="section-card">
        <button class="btn btn-outline" onclick="logout()">${t("btn.logout")}</button>
      </div>
      ${bottomNav("")}
    `);
  }, [role]);
}
profileView("owner"); profileView("vet"); profileView("govt"); profileView("lab");

// ============================================================ HERDS =====
route("#/owner/herds", async () => {
  render(`${header("My Herds", { back: true })}<div class="loading">Loading…</div>`);
  const herds = await api("/herds");
  render(`
    ${header("My Herds", { back: true })}
    <div class="section-card">
      <button class="btn btn-primary" style="margin-bottom:14px" onclick="location.hash='#/owner/herds/new'">+ Add Herd</button>
      ${herds.length === 0 ? emptyState("No herds yet. Add your first herd.") :
        herds.map(h => `
        <div class="list-card" onclick="location.hash='#/owner/herds/${h.id}'">
          <div class="row1"><span class="title">${h.herd_code}</span><span class="badge badge-blue">${h.animal_count} animals</span></div>
          <div class="meta">📍 ${h.village || "—"}, ${h.block || "—"}, ${h.district || "—"}</div>
          <div class="meta" style="color:var(--primary)">Tap for Farm Disease Intelligence &rarr;</div>
        </div>`).join("")}
    </div>
    ${bottomNav("#/owner/dashboard")}
  `);
}, ["owner"]);

async function herdDetailView(role, id) {
  render(`${header("Herd Intelligence", { back: true })}<div class="loading">Loading herd telemetry…</div>`);
  const hi = await api(`/herds/${id}/intelligence`);
  render(`
    ${header("Herd " + hi.herd_code, { back: true })}
    <div class="section-card">
      <div class="row1" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <span class="title" style="font-size:18px">${hi.herd_code}</span>
        <span class="badge ${riskBadgeClass(hi.risk_level)}">${hi.risk_level}</span>
      </div>
      <div class="detail-grid">
        <div><b>Location</b>${hi.village}, ${hi.district}</div>
        <div><b>Total Animals</b>${hi.total_animals}</div>
        <div><b>Active Cases</b>${hi.active_cases}</div>
        <div><b>Vaccination Coverage</b>${Math.round(hi.vaccination_coverage * 100)}%</div>
      </div>
    </div>
    <div class="section-card">
      <div class="subheading">🚨 Herd Alerts (${hi.alerts.length})</div>
      ${hi.alerts.length === 0 ? emptyState("No active alerts for this herd.") : hi.alerts.map(a => `
        <div class="list-card" style="cursor:default">
          <div class="row1"><span class="title">${a.disease}</span><span class="badge ${riskBadgeClass(a.risk_level)}">${a.risk_level}</span></div>
          <div class="meta">${a.trigger_reason}</div>
          <div class="meta">Action: ${a.recommended_action}</div>
        </div>
      `).join("")}
    </div>
    ${bottomNav(homeFor(role))}
  `);
}
route("#/:role/herds/:id", ({ role, id }) => herdDetailView(role, id), ["owner", "vet", "govt"]);

route("#/owner/herds/new", () => {
  render(`
    ${header("Add Herd", { back: true })}
    <div class="section-card">
      <form id="herdForm">
        <div class="form-row">
          <div class="field"><label>Village</label><input name="village" value="${state.user.village || ""}" /></div>
          <div class="field"><label>Block</label><input name="block" value="${state.user.block || ""}" /></div>
        </div>
        <div class="field"><label>District</label><input name="district" value="${state.user.district || ""}" /></div>
        <button class="btn btn-primary" type="submit">Create Herd</button>
      </form>
    </div>`);
  document.getElementById("herdForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const body = Object.fromEntries(new FormData(e.target));
      const herd = await api("/herds", { method: "POST", body });
      toast(`Herd ${herd.herd_code} created!`);
      location.hash = "#/owner/herds";
    } catch (err) { toast(err.message, true); }
  });
}, ["owner"]);

// =========================================================== ANIMALS ====
function animalsListView(role) {
  route(`#/${role}/animals`, async () => {
    render(`${header("Animals", { back: true })}<div class="loading">Loading…</div>`);
    const animals = await api("/animals");
    render(`
      ${header(role === "owner" ? "My Animals" : "Animals", { back: true })}
      <div class="section-card">
        ${role === "owner" ? `<button class="btn btn-primary" style="margin-bottom:14px" onclick="location.hash='#/owner/animals/new'">+ Add Animal</button>` : ""}
        ${animals.length === 0 ? emptyState("No animals registered yet.") :
          animals.map(a => `
          <div class="list-card" onclick="location.hash='#/${role}/animals/${a.id}'">
            <div class="row1"><span class="title">${a.animal_code}</span><span class="badge ${a.status === 'Healthy' ? 'badge-green' : 'badge-orange'}">${a.status}</span></div>
            <div class="meta">${a.animal_name || "—"} · ${a.animal_type || a.species || "—"} · ${a.breed || "—"} · ${(a.gender || a.sex || "—")} · ${(a.age || a.age_years) ? (a.age || a.age_years) + " yrs" : "—"}</div>
            ${role === "owner" ? `<div style="margin-top:8px;display:flex;gap:8px">
              <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();location.hash='#/owner/report?animal=${a.id}'">Report Issue</button>
              <button class="btn btn-outline btn-sm" onclick="event.stopPropagation();deleteAnimal(${a.id},'${a.animal_code}')"> Delete</button>
            </div>` : ""}
          </div>`).join("")}
      </div>
      ${bottomNav(role === "owner" ? "#/owner/animals" : `#/${role}/dashboard`)}
    `);
  }, [role]);
}
animalsListView("owner"); animalsListView("vet"); animalsListView("govt"); animalsListView("lab");

route("#/owner/animals/new", async () => {
  const herds = await api("/herds").catch(() => []);
  render(`
    ${header("Add Animal", { back: true })}
    <div class="section-card">
      <form id="animalForm">
        <div class="form-row">
          <div class="field"><label>Animal Name</label><input name="animal_name" required /></div>
          <div class="field"><label>Animal Type</label>
            <select name="animal_type" required><option value="">Select</option>
              <option>Cattle</option><option>Buffalo</option><option>Goat</option><option>Sheep</option><option>Other</option></select>
          </div>
        </div>
        <div class="form-row">
          <div class="field"><label>Breed</label><input name="breed" /></div>
          <div class="field"><label>Gender</label><select name="gender"><option>Female</option><option>Male</option></select></div>
          <div class="field"><label>Age</label><input name="age" type="number" step="0.5" min="0" /></div>
        </div>
        <div class="field"><label>Herd (optional)</label>
          <select name="herd_id"><option value="">No herd</option>${herds.map(h => `<option value="${h.id}">${h.herd_code}</option>`).join("")}</select>
        </div>
        <div class="form-row">
          <div class="field"><label>Owner Name</label><input name="owner_name" value="${state.user.full_name || ""}" required /></div>
          <div class="field"><label>Mobile</label><input name="mobile" value="${state.user.mobile || ""}" required /></div>
        </div>
        <div class="form-row">
          <div class="field"><label>Village</label><input name="village" value="${state.user.village || ""}" /></div>
          <div class="field"><label>District</label><input name="district" value="${state.user.district || ""}" /></div>
        </div>
        <button class="btn btn-primary" type="submit">Register Animal</button>
      </form>
    </div>`);
  document.getElementById("animalForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const body = Object.fromEntries(new FormData(e.target));
      const animal = await api("/animals", { method: "POST", body });
      toast(`Animal ${animal.animal_code} registered!`);
      location.hash = "#/owner/animals/" + animal.id;
    } catch (err) { toast(err.message, true); }
  });
}, ["owner"]);

// Animal health record (shared render, role-scoped routes)
function animalRecordView(role) {
  route(`#/${role}/animals/:id`, async ({ id }) => {
    render(`${header("Animal Record", { back: true })}<div class="loading">Loading health passport…</div>`);
    const a = await api(`/animals/${id}`);
    const isVet = role === "vet";
    const canEdit = isVet || role === "owner";

    const reproLatest = a.reproductive_records && a.reproductive_records.length ? a.reproductive_records[0] : null;
    const cds = a.ai_decision_support;

    render(`
      ${header("Digital Health Record", { back: true })}
      <div class="section-card">
        <div class="row1" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <div style="font-size:18px;font-weight:800">${a.animal_code}</div>
          <span class="badge ${a.status === 'Healthy' ? 'badge-green' : 'badge-orange'}">${a.status}</span>
        </div>
        <div class="detail-grid">
          <div><b>Animal Name</b>${a.animal_name || "—"}</div>
          <div><b>Animal Type</b>${a.animal_type || a.species || "—"}</div>
          <div><b>Breed</b>${a.breed || "—"}</div>
          <div><b>Gender</b>${a.gender || a.sex || "—"}</div>
          <div><b>Age</b>${(a.age || a.age_years) ? (a.age || a.age_years) + " yrs" : "—"}</div>
          <div><b>Owner Name</b>${a.owner_name || "—"}</div>
          <div><b>Mobile</b>${a.mobile || "—"}</div>
          <div><b>Herd</b>${a.herd ? a.herd.herd_code : "—"}</div>
          <div><b>Location</b>${a.village || "—"}, ${a.district || "—"}</div>
        </div>
        <div style="margin-top:14px">
          <button class="btn btn-ghost btn-sm" onclick="showQrModal(${a.id})">🏷️ Digital QR Passport &amp; Printable Tag</button>
        </div>
      </div>

      <!-- FEATURE GROUP 2: REPRODUCTIVE HEALTH -->
      <div class="section-card">
        <div class="subheading" style="justify-content:space-between">
          <span>🩺 Reproductive Health &amp; Gestation</span>
          <span class="badge badge-blue">${reproLatest ? reproLatest.pregnancy_status : "Not Pregnant"}</span>
        </div>
        <div class="detail-grid" style="margin-top:8px">
          <div><b>Breeding Date</b>${reproLatest && reproLatest.breeding_date ? fmtDate(reproLatest.breeding_date) : "—"}</div>
          <div><b>Expected Delivery</b>${reproLatest && reproLatest.expected_delivery_date ? fmtDate(reproLatest.expected_delivery_date) : "—"}</div>
          <div><b>Prior Pregnancies</b>${reproLatest ? reproLatest.previous_pregnancies : 0}</div>
          <div><b>Total Offspring</b>${reproLatest ? reproLatest.offspring_count : 0}</div>
        </div>
        ${reproLatest && reproLatest.breeding_notes ? `<div class="small-muted" style="margin-top:6px"><b>Notes:</b> ${reproLatest.breeding_notes}</div>` : ""}
        ${canEdit ? `
          <button class="btn btn-outline btn-sm" style="margin-top:10px" onclick="document.getElementById('reproFormWrap').style.display='block'">+ Record Reproductive Event</button>
          <div id="reproFormWrap" style="display:none;margin-top:12px;background:#f8f9fe;padding:12px;border-radius:12px">
            <form id="reproForm">
              <div class="form-row">
                <div class="field"><label>Event</label><select name="event_type"><option>Pregnancy Check</option><option>AI</option><option>Natural Service</option><option>Calving</option><option>Abortion</option></select></div>
                <div class="field"><label>Pregnancy Status</label><select name="pregnancy_status"><option>Confirmed Pregnant</option><option>Suspected</option><option>Not Pregnant</option><option>Lactating</option><option>Dry</option><option>Miscarried/Aborted</option></select></div>
              </div>
              <div class="form-row">
                <div class="field"><label>Breeding / Mating Date</label><input name="breeding_date" type="date" value="${new Date().toISOString().slice(0, 10)}" /></div>
                <div class="field"><label>Expected Delivery</label><input name="expected_delivery_date" type="date" placeholder="Auto-calculated if blank" /></div>
              </div>
              <div class="field"><label>Notes</label><input name="breeding_notes" placeholder="e.g. AI straw batch #, rectal palpation findings" /></div>
              <button class="btn btn-primary btn-sm" type="submit">Save Reproductive Record</button>
            </form>
          </div>
        ` : ""}
      </div>

      <!-- FEATURE GROUP 3: MEDICATION & ALLERGY PROFILE -->
      <div class="section-card">
        <div class="subheading">💊 Medication &amp; Allergy Profile</div>
        <div style="margin-bottom:8px">
          <b>Known Drug Allergies:</b>
          ${(!a.allergies || a.allergies.length === 0) ? `<div class="small-muted" style="margin:4px 0">No known drug allergies on record.</div>` :
            a.allergies.map(al => `
              <div class="conflict-box" style="margin:6px 0;padding:8px 12px">
                <b>⚠️ ${al.allergen} (${al.allergy_severity} Allergy)</b>
                <div>Reaction: ${al.reaction} · Recorded: ${fmtDate(al.date_recorded)}</div>
                ${al.notes ? `<div class="small-muted">${al.notes}</div>` : ""}
              </div>
            `).join("")}
        </div>
        ${isVet ? `
          <button class="btn btn-outline btn-sm" onclick="document.getElementById('allergyFormWrap').style.display='block'">+ Add Known Allergy</button>
          <div id="allergyFormWrap" style="display:none;margin-top:12px;background:#f8f9fe;padding:12px;border-radius:12px">
            <form id="allergyForm">
              <div class="form-row">
                <div class="field"><label>Allergen Name</label><input name="allergen" placeholder="e.g. Penicillin, NSAID, Sulfa" required /></div>
                <div class="field"><label>Severity</label><select name="allergy_severity"><option>Moderate</option><option>Severe</option><option>Life-Threatening</option><option>Mild</option></select></div>
              </div>
              <div class="field"><label>Observed Reaction</label><input name="reaction" placeholder="e.g. Anaphylaxis, facial edema, urticaria" required /></div>
              <div class="field"><label>Clinical Notes</label><input name="notes" placeholder="Contraindications or cross-reactivity notes" /></div>
              <button class="btn btn-primary btn-sm" type="submit">Save Allergy Profile</button>
            </form>
          </div>
        ` : ""}
      </div>

      <!-- FEATURE GROUP 11, 12, 13: INDIVIDUAL ANIMAL AI DECISION SUPPORT -->
      ${cds ? `
        <div class="section-card">
          <div class="subheading">🧠 AI Clinical Decision Support</div>
          <div class="stat-grid" style="margin:0 0 10px 0">
            ${statCard(cds.risk_score + "%", "Clinical Risk")}
            <div class="stat-card"><div class="num" style="font-size:16px"><span class="badge ${riskBadgeClass(cds.risk_level)}">${cds.risk_level}</span></div><div class="lbl">Risk Classification</div></div>
            ${statCard(cds.confidence + "%", "Confidence")}
          </div>
          <div class="demo-box" style="margin:8px 0;background:#fff8e1;border-left:4px solid #ffb300;color:#795548">
            <b>⚠️ Clinical Guidance Notice:</b> ${cds.disclaimer}
          </div>
          <div style="margin-top:10px">
            <b>🚨 Detected Findings &amp; Risk Indicators:</b>
            ${cds.abnormal_findings.map(f => `<div class="meta" style="margin:4px 0">• ${f}</div>`).join("")}
          </div>
          <div style="margin-top:10px">
            <b>📋 Suggested Diagnostic Next Steps:</b>
            ${cds.suggested_next_steps.map(s => `<div class="meta" style="margin:4px 0">• ${s}</div>`).join("")}
          </div>
          <div style="margin-top:10px">
            <b>🗓️ Follow-up Recommendations:</b>
            ${cds.follow_up_recommendations.map(r => `<div class="meta" style="margin:4px 0">• ${r}</div>`).join("")}
          </div>
        </div>
      ` : ""}

      <!-- EXISTING PASSPORT CARDS -->
      <div class="section-card">
        <div class="subheading">🩺 Previous Cases</div>
        ${a.cases.length === 0 ? emptyState("No cases recorded.") : a.cases.map(c => `
          <div class="list-card" onclick="location.hash='#/${role}/cases/${c.id}'">
            <div class="row1"><span class="title">${c.case_no}</span><span class="badge ${statusBadgeClass(c.status)}">${c.status}</span></div>
            <div class="meta">${c.symptoms || ""} · ${fmtDate(c.created_at)}</div>
          </div>`).join("")}
      </div>
      <div class="section-card">
        <div class="subheading">💉 Vaccination History</div>
        ${a.vaccinations.length === 0 ? emptyState("No vaccinations recorded.") : a.vaccinations.map(v => `
          <div class="list-card" style="cursor:default">
            <div class="row1"><span class="title">${v.vaccine}</span><span class="badge badge-blue">Next: ${fmtDate(v.next_due_date)}</span></div>
            <div class="meta">Given: ${fmtDate(v.date_given)} · By ${v.vet_name || "—"}</div>
          </div>`).join("")}
      </div>
      <div class="section-card">
        <div class="subheading">🧪 Laboratory Reports</div>
        ${a.lab_reports.length === 0 ? emptyState("No lab reports yet.") : a.lab_reports.map(l => `
          <div class="list-card" style="cursor:default">
            <div class="row1"><span class="title">${l.report_no}</span><span class="badge ${l.result === 'NEGATIVE' ? 'badge-green' : 'badge-red'}">${l.result || "Pending"}</span></div>
            <div class="meta">${l.test_name || ""} · Sample: ${l.sample || "—"} · ${fmtDate(l.test_date)}</div>
          </div>`).join("")}
      </div>
      <div class="section-card">
        <div class="subheading">💊 Prescriptions</div>
        ${a.prescriptions.length === 0 ? emptyState("No prescriptions yet.") : a.prescriptions.map(p => `
          <div class="list-card" style="cursor:default">
            <div class="row1"><span class="title">${p.medicine}</span><span class="badge badge-blue">${p.dosage || ""}</span></div>
            <div class="meta">${p.frequency || ""} · ${p.duration || ""} · By ${p.vet_name || "—"}</div>
          </div>`).join("")}
      </div>
      ${bottomNav(role === "owner" ? "#/owner/animals" : `#/${role}/dashboard`)}
    `);

    if (document.getElementById("reproForm")) {
      document.getElementById("reproForm").addEventListener("submit", async (e) => {
        e.preventDefault();
        try {
          const body = Object.fromEntries(new FormData(e.target));
          await api(`/animals/${id}/reproductive`, { method: "POST", body });
          toast("Reproductive record saved!");
          animalRecordView(role);
        } catch (err) { toast(err.message, true); }
      });
    }

    if (document.getElementById("allergyForm")) {
      document.getElementById("allergyForm").addEventListener("submit", async (e) => {
        e.preventDefault();
        try {
          const body = Object.fromEntries(new FormData(e.target));
          await api(`/animals/${id}/allergies`, { method: "POST", body });
          toast("Allergy record added!");
          animalRecordView(role);
        } catch (err) { toast(err.message, true); }
      });
    }
  }, [role]);
}
animalRecordView("owner"); animalRecordView("vet"); animalRecordView("govt"); animalRecordView("lab");

// QR Code Passport Modal
window.showQrModal = async function(id) {
  try {
    const data = await api(`/animals/${id}/qr`);
    const old = document.getElementById("qrModal");
    if (old) old.remove();
    const div = document.createElement("div");
    div.id = "qrModal";
    div.className = "qr-modal";
    div.innerHTML = `
      <div class="qr-modal-content">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <b>🏷️ Digital Animal Health Tag</b>
          <button style="border:none;background:none;font-size:20px;cursor:pointer" onclick="document.getElementById('qrModal').remove()">✕</button>
        </div>
        <div style="font-size:17px;font-weight:800;color:var(--primary)">${data.animal_code}</div>
        <div class="small-muted">${data.species} · Token: ${data.qr_token.slice(0, 16)}…</div>
        <div class="qr-image-wrap">
          <img src="${data.qr_image}" alt="Animal Health Passport QR" />
        </div>
        <div class="btn-row" style="margin-top:12px">
          <a class="btn btn-ghost btn-sm" href="${data.qr_image}" download="${data.animal_code}_QR.png" style="flex:1;text-decoration:none">⬇️ Download</a>
          <button class="btn btn-primary btn-sm" style="flex:1" onclick="window.print()">🖨️ Print Card</button>
        </div>
        ${getUserRole() !== "owner" ? `<button class="btn btn-outline btn-sm" style="margin-top:10px;width:100%" onclick="regenAnimalQr(${id})">🔄 Regenerate QR Identity</button>` : ""}
      </div>
    `;
    document.body.appendChild(div);
  } catch (e) { toast(e.message, true); }
};

window.regenAnimalQr = async function(id) {
  if (!confirm("Regenerate QR token for this animal? Any prior physical tag code will be invalidated.")) return;
  try {
    await api(`/animals/${id}/qr`, { method: "POST" });
    toast("New QR code generated!");
    document.getElementById("qrModal")?.remove();
    router();
  } catch (e) { toast(e.message, true); }
};

// =========================================================== REPORT =====
route("#/owner/report", async ({ animal, voice }) => {
  render(`${header("Report Health Issue", { back: true })}<div class="loading">Loading…</div>`);
  const [animals, vets] = await Promise.all([api("/animals"), api("/vets")]);
  const defaultAnimal = animal ? animals.find(a => a.id === Number(animal)) : null;

  render(`
    ${header("Report Health Issue", { back: true })}
    <div class="section-card">
      <div class="section-title">Describe Symptoms</div>
      <div class="meta" style="margin-bottom:12px">Report sickness or abnormal behavior to your local veterinarian dispensary.</div>

      ${voice ? `
      <div class="voice-box">
        <button id="micBtn" class="btn btn-primary btn-sm" type="button">🎙️ Tap to Speak (AI Whisper)</button>
        <span id="voiceStatus" class="small-muted">Press microphone to record speech</span>
      </div>
      <div id="voiceTranscriptWrap" style="display:none" class="voice-transcript">
        <b>Transcribed Voice:</b>
        <div id="voiceTranscriptText"></div>
      </div>` : ""}

      <form id="caseForm">
        <div class="field"><label>Animal</label>
          <select name="animal_id" required>
            <option value="">Select your animal</option>
            ${animals.map(a => `<option value="${a.id}" ${defaultAnimal && defaultAnimal.id === a.id ? "selected" : ""}>${a.animal_code} — ${a.animal_name || a.species}</option>`).join("")}
          </select>
        </div>
        <div class="field"><label>Assign Veterinarian (optional)</label>
          <select name="vet_id">
            <option value="">Nearest available veterinarian</option>
            ${vets.map(v => `<option value="${v.id}">${v.full_name} (${v.district})</option>`).join("")}
          </select>
        </div>
        <div class="field"><label>Symptoms Observed</label>
          <input name="symptoms" id="symptomsInput" placeholder="e.g. High fever, drooling, blisters, not eating" required />
        </div>
        <div class="form-row">
          <div class="field"><label>Severity</label>
            <select name="severity"><option>Low</option><option selected>Medium</option><option>High</option><option>Critical</option></select>
          </div>
          <div class="field"><label>Suspected Disease</label>
            <input name="disease_suspected" placeholder="e.g. FMD / HS (if known)" />
          </div>
        </div>
        <div class="field"><label>Additional Details</label>
          <textarea name="description" id="descInput" placeholder="How many days has the animal shown these signs? Any others in herd affected?"></textarea>
        </div>
        <button class="btn btn-primary" type="submit">Submit Report</button>
      </form>
    </div>
    ${bottomNav("#/owner/report")}
  `);

  if (voice) setupVoiceReport();

  document.getElementById("caseForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const body = Object.fromEntries(new FormData(e.target));
      if (voice) body.reported_through = "Voice App";
      const c = await api("/cases", { method: "POST", body });
      toast(`Report placed: ${c.case_no}`);
      location.hash = `#/owner/cases/${c.id}`;
    } catch (err) { toast(err.message, true); }
  });
}, ["owner"]);

// Whisper voice worker handling
let voiceWorker = null;
function getVoiceWorker() {
  if (!voiceWorker) {
    voiceWorker = new Worker("whisper-worker.js", { type: "module" });
    voiceWorker.onmessage = onVoiceMessage;
  }
  return voiceWorker;
}
function setVoiceStatus(txt) { const el = document.getElementById("voiceStatus"); if (el) el.textContent = txt; }
function onVoiceMessage(e) {
  const { status, text, error } = e.data || {};
  if (status === "ready") setVoiceStatus("AI Speech Model ready. Speak now.");
  else if (status === "transcribing") setVoiceStatus("Transcribing Marathi/English speech…");
  else if (status === "done") {
    setVoiceStatus("Transcribed!");
    const tWrap = document.getElementById("voiceTranscriptWrap");
    const tBox = document.getElementById("voiceTranscriptText");
    const symInp = document.getElementById("symptomsInput");
    if (tWrap && tBox) { tWrap.style.display = "block"; tBox.textContent = text; }
    if (symInp && !symInp.value) symInp.value = text;
  } else if (status === "error") setVoiceStatus("⚠️ Speech Error: " + (error || "failed"));
}

function setupVoiceReport() {
  const btn = document.getElementById("micBtn");
  if (!btn) return;
  let mediaRec = null, chunks = [];
  btn.addEventListener("click", async () => {
    if (mediaRec && mediaRec.state === "recording") {
      mediaRec.stop();
      btn.textContent = "🎙️ Tap to Speak (AI Whisper)";
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      chunks = [];
      mediaRec = new MediaRecorder(stream);
      mediaRec.ondataavailable = ev => chunks.push(ev.data);
      mediaRec.onstop = async () => {
        setVoiceStatus("Processing voice audio…");
        const blob = new Blob(chunks, { type: "audio/wav" });
        const ab = await blob.arrayBuffer();
        const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        const decoded = await ctx.decodeAudioData(ab);
        const floatData = decoded.getChannelData(0);
        getVoiceWorker().postMessage({ audio: floatData });
      };
      mediaRec.start();
      btn.textContent = "⏹️ Stop Recording";
      setVoiceStatus("🔴 Recording… describe symptoms clearly.");
    } catch (err) {
      setVoiceStatus("Microphone access denied: " + err.message);
    }
  });
}

// ============================================================ CASES =====
function casesListView(role) {
  route(`#/${role}/cases`, async () => {
    render(`${header("Cases", { back: true })}<div class="loading">Loading cases…</div>`);
    const cases = await api("/cases");
    render(`
      ${header("Active Case Tracking", { back: true })}
      <div class="section-card">
        ${cases.length === 0 ? emptyState("No active cases.") : cases.map(c => `
          <div class="list-card" onclick="location.hash='#/${role}/cases/${c.id}'">
            <div class="row1"><span class="title">${c.case_no}</span><span class="badge ${statusBadgeClass(c.status)}">${c.status}</span></div>
            <div class="meta"><b>Animal:</b> ${c.animal ? c.animal.animal_code : "—"} · ${c.symptoms || "—"}</div>
            <div class="meta">Owner: ${c.owner ? c.owner.full_name : "—"} · Reported: ${fmtDate(c.created_at)}</div>
          </div>`).join("")}
      </div>
      ${bottomNav(role === "owner" ? "#/owner/cases" : `#/${role}/dashboard`)}
    `);
  }, [role]);
}
casesListView("owner"); casesListView("vet"); casesListView("govt");

route("#/vet/reports", async () => {
  render(`${header("Incoming Reports", { back: true })}<div class="loading">Loading…</div>`);
  const reports = await api("/vet/reports");
  render(`
    ${header("Incoming Reports", { back: true })}
    <div class="section-card">
      <div class="section-title">Farmer Reported Incidents</div>
      ${reports.length === 0 ? emptyState("No reports yet.") : reports.map(r => `
        <div class="list-card" onclick="location.hash='#/vet/cases/${r.id}'">
          <div class="row1"><span class="title">${r.case_no}</span><span class="badge ${severityBadgeClass(r.severity)}">${r.severity} severity</span></div>
          <div class="meta"><b>Symptoms:</b> ${r.symptoms || "—"}</div>
          <div class="meta">Owner: ${r.owner ? r.owner.full_name + " (" + (r.owner.mobile || "") + ")" : "—"}</div>
          <div class="meta">Reported: ${fmtDate(r.created_at)} · Status: <span class="badge ${statusBadgeClass(r.status)}">${r.status}</span></div>
        </div>`).join("")}
    </div>
    ${bottomNav("#/vet/reports")}
  `);
}, ["vet"]);

// Case Detail view
function caseDetailView(role) {
  route(`#/${role}/cases/:id`, async ({ id }) => {
    render(`${header("Case Detail", { back: true })}<div class="loading">Loading…</div>`);
    const c = await api(`/cases/${id}`);
    const isVet = role === "vet";
    const samples = c.samples || [];
    const trs = c.treatment_responses || [];
    const allergies = c.animal_allergies || [];

    render(`
      ${header(c.case_no, { back: true })}
      <div class="section-card">
        <div class="row1" style="justify-content:space-between;display:flex;align-items:center;margin-bottom:8px">
          <span class="badge ${statusBadgeClass(c.status)}">${c.status}</span>
          <span class="badge ${severityBadgeClass(c.severity)}">${c.severity} severity</span>
        </div>
        <div class="detail-grid">
          <div><b>Animal</b><a class="link" onclick="location.hash='#/${role}/animals/${c.animal.id}'">${c.animal.animal_code}</a></div>
          <div><b>Herd</b>${c.herd ? c.herd.herd_code : "—"}</div>
          <div><b>Owner</b>${c.owner.full_name} (${c.owner.mobile || ""})</div>
          <div><b>Reported via</b>${c.reported_through || "Mobile App"}</div>
          <div><b>Symptoms</b>${c.symptoms || "—"}</div>
          <div><b>Disease Suspected</b>${c.disease_suspected || "—"}</div>
          <div><b>Diagnosis</b>${c.diagnosis || "—"}</div>
          <div><b>Treatment</b>${c.treatment || "—"}</div>
          <div><b>Vet Assigned</b>${c.vet_name || "Unassigned"}</div>
          <div><b>Reported On</b>${fmtDate(c.created_at)}</div>
        </div>
        ${c.description ? `<div style="margin-top:10px"><b class="small-muted">Description:</b><div style="font-size:13.5px">${c.description}</div></div>` : ""}
      </div>

      <!-- ALLERGY ALERT BANNER IF APPLICABLE -->
      ${allergies.length > 0 ? `
        <div class="section-card" style="padding:12px">
          <div class="conflict-box" style="margin:0">
            <b>⚠️ Patient Drug Allergy Alert:</b>
            ${allergies.map(a => `${a.allergen} (${a.allergy_severity}: ${a.reaction})`).join(", ")}
          </div>
        </div>
      ` : ""}

      <div id="trackWrap"></div>

      <!-- VET ACTIONS -->
      ${isVet ? vetCaseActions(c, allergies) : ""}

      <!-- DIGITAL SAMPLES & CHAIN OF CUSTODY -->
      <div class="section-card">
        <div class="subheading" style="justify-content:space-between">
          <span>🧪 Digital Biological Samples (${samples.length})</span>
          ${isVet ? `<button class="btn btn-ghost btn-sm" onclick="document.getElementById('sampleCollectWrap').style.display='block'">+ Collect Sample</button>` : ""}
        </div>
        ${isVet ? `
          <div id="sampleCollectWrap" style="display:none;margin-top:12px;background:#f8f9fe;padding:12px;border-radius:12px">
            <form id="sampleCollectForm">
              <div class="form-row">
                <div class="field"><label>Sample Type</label><select name="sample_type"><option>Blood Sample</option><option>Nasal Swab</option><option>Tissue Biopsy</option><option>Milk Sample</option><option>Fecal Sample</option></select></div>
                <div class="field"><label>Collection Notes</label><input name="collection_notes" placeholder="e.g. Sterile EDTA tube" /></div>
              </div>
              <div class="field">
                <label>Geolocation (GPS)</label>
                <div class="btn-row">
                  <button class="btn btn-outline btn-sm" type="button" onclick="captureSampleGps()">📍 Capture Device GPS</button>
                  <span id="gpsStatusTxt" class="small-muted" style="align-self:center">GPS idle</span>
                </div>
                <input type="hidden" name="collection_lat" id="inpSampleLat" />
                <input type="hidden" name="collection_lng" id="inpSampleLng" />
                <input type="hidden" name="is_manual_location" id="inpSampleManual" value="0" />
              </div>
              <button class="btn btn-primary btn-sm" type="submit">Confirm &amp; Generate Sample QR</button>
            </form>
          </div>
        ` : ""}
        ${samples.length === 0 ? emptyState("No biological specimens collected yet.") :
          samples.map(s => `
          <div class="list-card" style="cursor:default">
            <div class="row1">
              <span class="title">${s.sample_code} (${s.sample_type})</span>
              <span class="badge ${statusBadgeClass(s.status)}">${s.status}</span>
            </div>
            <div class="meta">Collected: ${fmtDate(s.collected_at)} ${s.collection_lat ? `· Lat: ${s.collection_lat.toFixed(4)}, Lng: ${s.collection_lng.toFixed(4)}` : ""}</div>
            <div class="row1" style="margin-top:6px">
              <button class="btn btn-ghost btn-sm" onclick="showSampleQrModal(${s.id})">🔍 QR &amp; Chain of Custody</button>
              ${isVet && ["COLLECTED", "READY_FOR_PICKUP"].includes(s.status) ? `
                <button class="btn btn-outline btn-sm" onclick="advanceTransport(${s.id})">Mark In Transit</button>
              ` : ""}
            </div>
          </div>
        `).join("")}
      </div>

      <!-- STRUCTURED TREATMENT RESPONSE -->
      <div class="section-card">
        <div class="subheading" style="justify-content:space-between">
          <span>📋 Structured Treatment Responses (${trs.length})</span>
          ${isVet ? `<button class="btn btn-ghost btn-sm" onclick="document.getElementById('trFormWrap').style.display='block'">+ Record Response</button>` : ""}
        </div>
        ${isVet ? `
          <div id="trFormWrap" style="display:none;margin-top:12px;background:#f8f9fe;padding:12px;border-radius:12px">
            <form id="trForm">
              <div class="field"><label>Patient Response</label>
                <select name="response">
                  <option value="improved">Improved</option>
                  <option value="unchanged">Unchanged</option>
                  <option value="worsened">Worsened</option>
                  <option value="recovered">Recovered (Case Solved)</option>
                  <option value="adverse_reaction">Adverse Reaction</option>
                  <option value="treatment_discontinued">Treatment Discontinued</option>
                  <option value="follow_up_required">Follow-up Required</option>
                </select>
              </div>
              <div class="field"><label>Objective Clinical Observations</label><textarea name="objective_observations" placeholder="e.g. Temp 101.3°F, normal rumination, appetite restored"></textarea></div>
              <div class="field"><label>Clinical Notes</label><input name="notes" placeholder="e.g. Complete 3-day course" /></div>
              <button class="btn btn-primary btn-sm" type="submit">Save Treatment Evaluation</button>
            </form>
          </div>
        ` : ""}
        ${trs.length === 0 ? emptyState("No treatment evaluations recorded yet.") :
          trs.map(tr => `
          <div class="list-card" style="cursor:default">
            <div class="row1"><span class="title">Response: ${tr.response}</span><span class="badge ${statusBadgeClass(tr.response)}">${fmtDate(tr.response_date)}</span></div>
            <div class="meta"><b>Observations:</b> ${tr.objective_observations || "—"}</div>
            ${tr.notes ? `<div class="small-muted">Notes: ${tr.notes}</div>` : ""}
          </div>
        `).join("")}
      </div>

      <!-- LABORATORY REPORTS -->
      <div class="section-card">
        <div class="subheading">🧬 Verified Laboratory Reports</div>
        ${c.lab_reports.length === 0 ? emptyState("No verified laboratory reports released yet.") :
          c.lab_reports.map(l => `
          <div class="list-card" style="cursor:default">
            <div class="row1"><span class="title">${l.report_no} — ${l.test_name}</span><span class="badge ${l.result === 'NEGATIVE' ? 'badge-green' : 'badge-red'}">${l.result}</span></div>
            <div class="meta"><b>Method:</b> ${l.test_method || "Standard"} · <b>Status:</b> ${l.verification_status || "VERIFIED"}</div>
            ${l.quantitative_result !== null ? `<div class="meta"><b>Value:</b> ${l.quantitative_result} ${l.units || ""} (Ref: ${l.reference_range_text || "Normal"})</div>` : ""}
            ${l.notes || l.comments ? `<div class="small-muted">${l.notes || l.comments}</div>` : ""}
          </div>
        `).join("")}
      </div>

      <!-- PRESCRIPTIONS -->
      <div class="section-card">
        <div class="subheading">💊 Prescriptions</div>
        ${c.prescriptions.length === 0 ? emptyState("No prescriptions issued yet.") : c.prescriptions.map(p => `
          <div class="list-card" style="cursor:default">
            <div class="row1">
              <span class="title">${p.medicine}</span>
              <span class="badge badge-blue">${p.dosage || ""}</span>
            </div>
            <div class="meta">${p.frequency || ""} for ${p.duration || ""} · Follow-up: ${fmtDate(p.follow_up_date)}</div>
            ${p.allergy_override ? `<div class="conflict-box" style="margin:4px 0;padding:6px"><b>⚠️ Allergy Override Granted:</b> ${p.override_reason}</div>` : ""}
            ${p.instructions ? `<div class="meta">${p.instructions}</div>` : ""}
          </div>`).join("")}
      </div>

      <!-- CASE TIMELINE -->
      <div class="section-card">
        <div class="subheading">🕒 Case Timeline</div>
        <div class="timeline">
          ${c.updates.map(u => `
            <div class="timeline-item">
              <div class="timeline-dot"></div>
              <div class="timeline-body">
                <div class="t-status">${u.status}</div>
                <div class="t-note">${u.note || ""} — ${u.updated_by || ""}</div>
                <div class="t-date">${fmtDate(u.created_at)}</div>
              </div>
            </div>`).join("")}
        </div>
      </div>

      ${isVet && ["RECOVERED", "CLOSED"].includes(c.status) ? `
      <div class="section-card">
        <div class="subheading">🗑 Case Solved — Close-out</div>
        <div class="small-muted" style="margin-bottom:10px">This case is solved. You can remove the report permanently.</div>
        <button class="btn btn-outline" onclick="deleteCase(${c.id},'${c.case_no}')">Delete Report</button>
      </div>` : ""}

      ${bottomNav(`#/${role}/cases`)}
    `);

    if (isVet) bindVetCaseActions(c, allergies);
    loadTracking(c, role);
  }, [role]);
}
caseDetailView("owner"); caseDetailView("vet"); caseDetailView("govt");

window.showSampleQrModal = async function(sid) {
  try {
    const s = await api(`/samples/${sid}`);
    const old = document.getElementById("sampleQrModal");
    if (old) old.remove();
    const div = document.createElement("div");
    div.id = "sampleQrModal";
    div.className = "qr-modal";
    div.innerHTML = `
      <div class="qr-modal-content">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <b>🧪 Digital Biological Sample Tag</b>
          <button style="border:none;background:none;font-size:20px;cursor:pointer" onclick="document.getElementById('sampleQrModal').remove()">✕</button>
        </div>
        <div style="font-size:16px;font-weight:800;color:var(--primary)">${s.sample_code}</div>
        <div class="small-muted">${s.sample_type} · Animal: ${s.animal_code}</div>
        <div class="qr-image-wrap">
          <img src="${s.qr_image}" alt="Sample QR" />
        </div>
        <div class="small-muted" style="margin-bottom:8px">Token: ${s.qr_token.slice(0, 16)}…</div>
        <div style="text-align:left;max-height:140px;overflow-y:auto;border-top:1px solid #eee;padding-top:6px">
          <b>Chain of Custody:</b>
          ${(s.custody_events || []).map(e => `<div style="font-size:11px;margin:3px 0">• <b>${e.status}:</b> ${e.action} (${e.actor_name})</div>`).join("")}
        </div>
        <button class="btn btn-outline btn-sm" style="margin-top:12px;width:100%" onclick="window.print()">🖨️ Print Specimen Label</button>
      </div>
    `;
    document.body.appendChild(div);
  } catch (e) { toast(e.message, true); }
};

window.advanceTransport = async function(sid) {
  const courier = prompt("Enter Transporter / Courier name:", "Cold Chain Express");
  if (!courier) return;
  try {
    await api(`/samples/${sid}/transport`, { method: "POST", body: { status: "IN_TRANSIT", transporter_name: courier } });
    toast("Sample marked IN_TRANSIT");
    router();
  } catch (e) { toast(e.message, true); }
};

window.captureSampleGps = function() {
  const txt = document.getElementById("gpsStatusTxt");
  if (!navigator.geolocation) {
    txt.textContent = "Geolocation unsupported. Manual fallback used.";
    document.getElementById("inpSampleManual").value = "1";
    return;
  }
  txt.textContent = "Requesting device coordinates…";
  navigator.geolocation.getCurrentPosition(
    pos => {
      document.getElementById("inpSampleLat").value = pos.coords.latitude;
      document.getElementById("inpSampleLng").value = pos.coords.longitude;
      document.getElementById("inpSampleManual").value = "0";
      txt.textContent = `🟢 GPS Captured: ${pos.coords.latitude.toFixed(4)}, ${pos.coords.longitude.toFixed(4)}`;
    },
    err => {
      txt.textContent = "⚠️ GPS permission denied. Using district centroid fallback.";
      document.getElementById("inpSampleManual").value = "1";
    },
    { timeout: 8000 }
  );
};

// ============================ LIVE FIELD-VISIT TRACKING ===================
let trackTimer = null, trackMap = null, trackVet = null, trackLine = null, trackCtx = null;

function trackStages(t) {
  const s = t.visit ? t.visit.status : null;
  const onWay = s === "ON_THE_WAY";
  const arrived = s === "ARRIVED" || s === "COMPLETED";
  const completed = s === "COMPLETED";
  return [
    ["📝 Report placed", "badge-green"],
    ["✅ Vet accepted", s ? "badge-green" : "badge-blue"],
    ["🚗 On the way", onWay ? "badge-orange" : arrived || completed ? "badge-green" : "badge-blue"],
    ["📍 Arrived", arrived ? "badge-green" : "badge-blue"],
    ["💊 Visit done", completed ? "badge-green" : "badge-blue"],
  ];
}

function trackingCardHTML(t, c, role) {
  const stages = trackStages(t);
  const v = t.visit;
  return `
    <div class="section-card">
      <div class="subheading">🚗 Live Field-Visit Tracking</div>
      <div class="tag-row" style="margin-bottom:12px">
        ${stages.map(([lbl, cls]) => `<span class="badge ${cls}">${lbl}</span>`).join("")}
      </div>
      <div id="trackMap" style="height:240px;width:100%;border-radius:14px;overflow:hidden;background:#e5e5e5"></div>
      ${v ? `
        <div class="row1" style="margin-top:10px;font-size:13px">
          <div><b>ETA:</b> ${v.status === "ON_THE_WAY" ? `${Math.ceil(t.eta_seconds / 60)} mins` : v.status === "ARRIVED" ? "Arrived" : "Completed"}</div>
          <div><b>Veterinarian:</b> ${t.vet ? t.vet.full_name : "Assigned"}</div>
        </div>
      ` : ""}
      ${role === "vet" ? visitControlButtons(v) : ""}
    </div>`;
}

function visitControlButtons(v) {
  if (!v) return `<button class="btn btn-primary" style="margin-top:10px" id="btnStartTrip">🚗 Start Field Visit (I'm on the way)</button>`;
  if (v.status === "ON_THE_WAY") return `<button class="btn btn-primary" style="margin-top:10px" id="btnArrived">📍 Mark Arrived at Farm</button>`;
  if (v.status === "ARRIVED") return `<button class="btn btn-primary" style="margin-top:10px" id="btnCompleteTrip">💊 Complete Physical Visit</button>`;
  return "";
}

function initTrackMap(t) {
  if (typeof L === "undefined") return;
  const el = document.getElementById("trackMap");
  if (!el) return;
  if (trackMap) { trackMap.remove(); trackMap = null; }
  const cur = t.current_position || t.destination;
  trackMap = L.map("trackMap").setView([cur.lat, cur.lng], 13);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18 }).addTo(trackMap);
  L.marker([t.destination.lat, t.destination.lng]).addTo(trackMap).bindTooltip("📍 Animal Location");
  if (t.origin) L.marker([t.origin.lat, t.origin.lng]).addTo(trackMap).bindTooltip("Dispensary");
  trackVet = L.circleMarker([cur.lat, cur.lng], { radius: 9, color: "#2f6fed", fillColor: "#2f6fed", fillOpacity: 0.9 }).addTo(trackMap);
  if (t.origin && t.destination) trackLine = L.polyline([[t.origin.lat, t.origin.lng], [t.destination.lat, t.destination.lng]], { color: "#2f6fed", dashArray: "5, 8" }).addTo(trackMap);
}

function updateTrackMap(t) {
  if (!trackMap || !trackVet) return;
  const cur = t.current_position;
  trackVet.setLatLng([cur.lat, cur.lng]);
}

async function loadTracking(c, role) {
  const wrap = document.getElementById("trackWrap");
  if (!wrap) return;
  try {
    const t = await api(`/cases/${c.id}/track`);
    wrap.innerHTML = trackingCardHTML(t, c, role);
    if (t.destination) initTrackMap(t);
    bindVisitControls(c, role);
    if (trackTimer) clearInterval(trackTimer);
    if (t.visit && t.visit.status === "ON_THE_WAY") {
      trackTimer = setInterval(async () => {
        try {
          const fresh = await api(`/cases/${c.id}/track`);
          updateTrackMap(fresh);
          if (fresh.visit && fresh.visit.status !== "ON_THE_WAY") clearInterval(trackTimer);
        } catch (e) {}
      }, 3000);
    }
  } catch (err) { wrap.innerHTML = ""; }
}

function bindVisitControls(c, role) {
  if (role !== "vet") return;
  const startBtn = document.getElementById("btnStartTrip");
  if (startBtn) startBtn.addEventListener("click", async () => {
    try { await api(`/cases/${c.id}/visit`, { method: "POST" }); toast("Visit started!"); loadTracking(c, role); }
    catch (err) { toast(err.message, true); }
  });
  const arrBtn = document.getElementById("btnArrived");
  if (arrBtn) arrBtn.addEventListener("click", async () => {
    try { await api(`/cases/${c.id}/visit`, { method: "PUT", body: { status: "ARRIVED" } }); toast("Arrived at farm!"); loadTracking(c, role); }
    catch (err) { toast(err.message, true); }
  });
  const compBtn = document.getElementById("btnCompleteTrip");
  if (compBtn) compBtn.addEventListener("click", async () => {
    try { await api(`/cases/${c.id}/visit`, { method: "PUT", body: { status: "COMPLETED" } }); toast("Visit marked completed!"); loadTracking(c, role); }
    catch (err) { toast(err.message, true); }
  });
}

function vetCaseActions(c, allergies = []) {
  const statuses = ["ASSIGNED", "UNDER INVESTIGATION", "SAMPLE COLLECTED", "LAB PENDING", "DIAGNOSED", "TREATMENT", "FOLLOW-UP", "RECOVERED", "CLOSED"];
  return `
    <div class="section-card">
      <div class="subheading">✏️ Case Actions &amp; Diagnosis</div>
      <form id="statusForm">
        <div class="field"><label>Status</label><select name="status">${statuses.map(s => `<option ${s === c.status ? "selected" : ""}>${s}</option>`).join("")}</select></div>
        <div class="field"><label>Diagnosis</label><input name="diagnosis" value="${c.diagnosis || ""}" /></div>
        <div class="field"><label>Treatment Notes</label><textarea name="treatment">${c.treatment || ""}</textarea></div>
        <div class="field"><label>Update Note</label><input name="note" placeholder="What changed?" /></div>
        <button class="btn btn-primary" type="submit">Save Case Update</button>
      </form>
    </div>
    <div class="section-card">
      <div class="subheading">💊 Create E-Prescription</div>
      <form id="rxForm">
        <div class="field"><label>Diagnosis</label><input name="diagnosis" value="${c.diagnosis || ""}" /></div>
        <div class="form-row">
          <div class="field"><label>Medicine</label><input name="medicine" id="rxMedicineInput" required /></div>
          <div class="field"><label>Dosage</label><input name="dosage" placeholder="e.g. 0.5 mg/kg" /></div>
        </div>
        <div class="form-row">
          <div class="field"><label>Frequency</label><input name="frequency" placeholder="e.g. Once daily" /></div>
          <div class="field"><label>Duration</label><input name="duration" placeholder="e.g. 5 days" /></div>
        </div>
        <div class="field"><label>Instructions</label><textarea name="instructions"></textarea></div>
        <div class="field"><label>Follow-up Date</label><input name="follow_up_date" type="date" /></div>
        <div id="rxConflictPrompt" style="display:none;margin-bottom:12px"></div>
        <button class="btn btn-primary" id="btnIssueRx" type="submit">Issue Prescription</button>
      </form>
    </div>`;
}

function bindVetCaseActions(c, allergies = []) {
  const sf = document.getElementById("statusForm");
  if (sf) {
    sf.addEventListener("submit", async (e) => {
      e.preventDefault();
      try { await api(`/cases/${c.id}`, { method: "PUT", body: Object.fromEntries(new FormData(e.target)) }); toast("Case updated successfully"); router(); }
      catch (err) { toast(err.message, true); }
    });
  }

  const scf = document.getElementById("sampleCollectForm");
  if (scf) {
    scf.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const body = Object.fromEntries(new FormData(e.target));
        body.case_id = c.id;
        await api("/samples", { method: "POST", body });
        toast("Digital sample collected with GPS & QR!");
        router();
      } catch (err) { toast(err.message, true); }
    });
  }

  const trf = document.getElementById("trForm");
  if (trf) {
    trf.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const body = Object.fromEntries(new FormData(e.target));
        await api(`/cases/${c.id}/treatment-responses`, { method: "POST", body });
        toast("Treatment response saved!");
        router();
      } catch (err) { toast(err.message, true); }
    });
  }

  const rf = document.getElementById("rxForm");
  if (rf) {
    rf.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = Object.fromEntries(new FormData(e.target));
      body.case_id = c.id;
      try {
        await api("/prescriptions", { method: "POST", body });
        toast("E-prescription issued successfully!");
        router();
      } catch (err) {
        if (err.data && err.data.conflict) {
          const promptBox = document.getElementById("rxConflictPrompt");
          promptBox.style.display = "block";
          promptBox.innerHTML = `
            <div class="conflict-box">
              <b>${err.data.error}</b>
              <div style="margin-top:6px">Authorized Override Reason (Mandatory to override allergy warning):</div>
              <input id="overrideReasonInp" placeholder="Enter clinical justification for override" style="margin-top:4px" />
              <button class="btn btn-outline btn-sm" style="margin-top:8px;background:#fff" type="button" id="btnConfirmOverride">Confirm Override &amp; Prescribe</button>
            </div>
          `;
          document.getElementById("btnConfirmOverride").addEventListener("click", async () => {
            const reason = (document.getElementById("overrideReasonInp")?.value || "").trim();
            if (!reason) return toast("Override reason is required", true);
            body.override = true;
            body.override_reason = reason;
            try {
              await api("/prescriptions", { method: "POST", body });
              toast("Prescription issued with recorded allergy override!");
              router();
            } catch (ex) { toast(ex.message, true); }
          });
        } else {
          toast(err.message, true);
        }
      }
    });
  }
}

// ======================================================== SEARCH & VAX ====
route("#/vet/search", () => {
  render(`
    ${header("Search Herds & Animals", { back: true })}
    <div class="section-card">
      <div class="field"><label>Search by Tag, Mobile or Owner</label>
        <input id="searchInput" placeholder="e.g. MH-PUN-000123 or 9800000001" />
      </div>
      <div id="searchResults"></div>
    </div>
    ${bottomNav("#/vet/search")}
  `);
  document.getElementById("searchInput").addEventListener("input", async (e) => {
    const q = e.target.value.trim();
    if (!q) { document.getElementById("searchResults").innerHTML = ""; return; }
    try {
      const res = await api(`/vet/search?q=${encodeURIComponent(q)}`);
      document.getElementById("searchResults").innerHTML = `
        <div class="subheading">Animals (${res.animals.length})</div>
        ${res.animals.map(a => `
          <div class="list-card" onclick="location.hash='#/vet/animals/${a.id}'">
            <div class="row1"><span class="title">${a.animal_code}</span><span class="badge ${a.status === 'Healthy' ? 'badge-green' : 'badge-orange'}">${a.status}</span></div>
            <div class="meta">${a.animal_name || a.species} · Owner: ${a.owner_name} (${a.mobile})</div>
          </div>`).join("")}
        <div class="subheading" style="margin-top:14px">Herds (${res.herds.length})</div>
        ${res.herds.map(h => `<div class="list-card" style="cursor:default"><div class="row1"><span class="title">${h.herd_code}</span></div><div class="meta">${h.village}, ${h.district}</div></div>`).join("")}
      `;
    } catch (err) {}
  });
}, ["vet"]);

route("#/vet/vaccination/new", () => {
  render(`
    ${header("Update Vaccination", { back: true })}
    <div class="section-card">
      <form id="vacForm">
        <div class="field"><label>Animal ID</label><input name="animal_id" placeholder="e.g. MH-PUN-000123" required /></div>
        <div class="field"><label>Vaccine</label><select name="vaccine"><option>FMD</option><option>HS</option><option>BQ</option><option>Brucellosis</option><option>Other</option></select></div>
        <div class="form-row">
          <div class="field"><label>Date Given</label><input name="date_given" type="date" value="${new Date().toISOString().slice(0, 10)}" /></div>
          <div class="field"><label>Next Due Date</label><input name="next_due_date" type="date" /></div>
        </div>
        <button class="btn btn-primary" type="submit">Save Vaccination</button>
      </form>
    </div>
    ${bottomNav("#/vet/dashboard")}
  `);
  document.getElementById("vacForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("/vaccinations", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }); toast("Vaccination recorded!"); location.hash = "#/vet/dashboard"; }
    catch (err) { toast(err.message, true); }
  });
}, ["vet"]);

// ======================================================= PRESCRIPTIONS ==
function prescriptionsView(role) {
  route(`#/${role}/prescriptions`, async () => {
    render(`${header("Prescriptions", { back: true })}<div class="loading">Loading…</div>`);
    const rx = await api("/prescriptions");
    render(`
      ${header("Prescriptions", { back: true })}
      <div class="section-card">
        ${rx.length === 0 ? emptyState("No prescriptions yet.") : rx.map(p => `
          <div class="list-card" onclick="location.hash='#/${role}/cases/${p.case_id}'">
            <div class="row1"><span class="title">${p.medicine}</span><span class="badge badge-blue">${p.case_no}</span></div>
            <div class="meta">${p.animal_code} · ${p.dosage || ""} · ${p.frequency || ""}</div>
            <div class="meta">By ${p.vet_name || "—"} · Follow-up: ${fmtDate(p.follow_up_date)}</div>
          </div>`).join("")}
      </div>
      ${bottomNav(role === "owner" ? "#/owner/prescriptions" : `#/${role}/dashboard`)}
    `);
  }, [role]);
}
prescriptionsView("owner"); prescriptionsView("vet"); prescriptionsView("lab");

// ======================================================= LAB REPORTS ====
function labReportsView(role) {
  route(`#/${role}/lab-reports`, async () => {
    render(`${header("Lab Reports", { back: true })}<div class="loading">Loading…</div>`);
    const reps = await api("/lab/reports");
    render(`
      ${header("Lab Reports", { back: true })}
      <div class="section-card">
        ${reps.length === 0 ? emptyState("No lab reports yet.") : reps.map(l => `
          <div class="list-card" onclick="location.hash='#/${role}/cases/${l.case_id}'">
            <div class="row1"><span class="title">${l.report_no}</span><span class="badge ${l.result === 'NEGATIVE' ? 'badge-green' : 'badge-red'}">${l.result || "Pending"}</span></div>
            <div class="meta">${l.animal_code}${l.animal_name ? " · " + l.animal_name : ""} · ${l.test_name || ""}</div>
            <div class="meta">Case ${l.case_no} · Sample: ${l.sample || "—"} · ${fmtDate(l.test_date)}</div>
            ${l.notes ? `<div class="meta">${l.notes}</div>` : ""}
          </div>`).join("")}
      </div>
      ${bottomNav(role === "owner" ? "#/owner/lab-reports" : role === "lab" ? "#/lab/lab-reports" : `#/${role}/dashboard`)}
    `);
  }, [role]);
}
labReportsView("owner"); labReportsView("vet"); labReportsView("lab");

// ======================================================= NOTIFICATIONS ==
function notificationsView(role) {
  route(`#/${role}/notifications`, async () => {
    render(`${header("Notifications", { back: true })}<div class="loading">Loading…</div>`);
    const notes = await api("/notifications");
    render(`
      ${header("Notifications", { back: true })}
      <div class="section-card">
        ${notes.length === 0 ? emptyState("You're all caught up!") : notes.map(n => `
          <div class="list-card" style="${n.is_read ? "opacity:0.6" : ""}" onclick="markRead(${n.id})">
            <div class="row1"><span class="title">${iconForType(n.type)} ${n.type.toUpperCase()}</span>${n.is_read ? "" : '<span class="badge badge-red">NEW</span>'}</div>
            <div class="meta">${n.message}</div>
            <div class="meta">${fmtDate(n.created_at)}</div>
          </div>`).join("")}
      </div>
      ${bottomNav(`#/${role}/notifications`)}
    `);
  }, [role]);
}
notificationsView("owner"); notificationsView("vet"); notificationsView("govt"); notificationsView("lab");

function iconForType(t2) { return { case: "🩺", lab: "🧪", prescription: "💊", vaccination: "💉" }[t2] || "🔔"; }

window.markRead = async function (id) {
  try { await api(`/notifications/${id}/read`, { method: "PUT" }); router(); } catch (e) { }
};
window.deleteAnimal = async function (id, code) {
  if (!confirm(`Delete animal ${code}? This also removes its cases, lab reports and prescriptions.`)) return;
  try { await api(`/animals/${id}`, { method: "DELETE" }); toast(`Animal ${code} deleted`); router(); }
  catch (err) { toast(err.message, true); }
};
window.deleteCase = async function (id, caseNo) {
  if (!confirm(`Delete report ${caseNo}? This removes the case, its lab reports and prescriptions permanently.`)) return;
  try { await api(`/cases/${id}`, { method: "DELETE" }); toast(`Report ${caseNo} deleted`); location.hash = "#/vet/reports"; }
  catch (err) { toast(err.message, true); }
};

// ======================================================== IVR REPORTING SYSTEM (PRODUCTION-GRADE) ==
// Minimal UI extensions for IVR - preserves existing design system

function ivrStatusBadge(status) {
  const s = (status||"").toUpperCase();
  if (["RECEIVED","AI_SUMMARIZED","VET_NOTIFIED"].includes(s)) return "badge-blue";
  if (["PARTIALLY_COMPLETED","DUPLICATE_FLAGGED"].includes(s)) return "badge-orange";
  if (["HIGH","CRITICAL"].includes(s)) return "badge-red";
  return statusBadgeClass(s);
}
function urgencyBadge(urg) {
  const u=(urg||"").toUpperCase();
  if (u==="CRITICAL") return "badge-red";
  if (u==="HIGH") return "badge-red";
  if (u==="MEDIUM") return "badge-orange";
  return "badge-green";
}

// Inject IVR quick-action cards into existing dashboards (non-destructive)
const _origGovtDashboard = govtDashboard;
govtDashboard = async function() {
  await _origGovtDashboard();
  // Append IVR section after existing content without redesign
  const appEl = document.getElementById("app");
  if (!appEl || document.getElementById("ivr-govt-extra")) return;
  const extra = document.createElement("div");
  extra.id = "ivr-govt-extra";
  extra.innerHTML = `
    <div class="section-card">
      <div class="section-title">📞 IVR Reporting Channel</div>
      <div class="meta" style="margin-bottom:12px"> Farmer phone-based reporting: multilingual IVR (English/Telugu/Hindi/Marathi), DTMF+speech, auto-report generation, vet bridging, and government surveillance integration.</div>
      <div class="icon-grid">
        ${iconItem("📞", "IVR Reports", "#/govt/ivr-reports")}
        ${iconItem("📊", "IVR Analytics", "#/govt/ivr-analytics")}
        ${iconItem("📋", "Call History", "#/govt/ivr-calls")}
        ${iconItem("⚙️", "IVR Config", "#/govt/ivr-config")}
      </div>
    </div>`;
  // Insert before bottomNav
  const bnav = appEl.querySelector(".bottom-nav");
  if (bnav) appEl.insertBefore(extra, bnav);
  else appEl.appendChild(extra);
};

const _origVetDashboard = vetDashboard;
vetDashboard = async function() {
  await _origVetDashboard();
  const appEl = document.getElementById("app");
  if (!appEl || document.getElementById("ivr-vet-extra")) return;
  const extra = document.createElement("div");
  extra.id = "ivr-vet-extra";
  extra.innerHTML = `
    <div class="section-card">
      <div class="section-title">📞 IVR Farmer Reports</div>
      <div class="meta" style="margin-bottom:12px">Phone-based reports appear as normal cases with source=IVR. Language, location (farmer-provided/GPS), and AI summary are preserved for veterinary review.</div>
      <div class="icon-grid">
        ${iconItem("📞", "IVR Reports", "#/vet/ivr-reports")}
        ${iconItem("📋", "Call History", "#/vet/ivr-calls")}
        ${iconItem("🧠", "AI Summary", "#/govt/ivr-analytics")}
      </div>
    </div>`;
  const bnav = appEl.querySelector(".bottom-nav");
  if (bnav) appEl.insertBefore(extra, bnav);
  else appEl.appendChild(extra);
};

const _origOwnerDashboard = ownerDashboard;
ownerDashboard = async function() {
  await _origOwnerDashboard();
  const appEl = document.getElementById("app");
  if (!appEl || document.getElementById("ivr-owner-extra")) return;
  try {
    const ivrInfo = await api("/ivr/info").catch(()=>null);
    const ivrNum = ivrInfo && ivrInfo.config ? ivrInfo.config.ivr_phone_number : "Not configured";
    const extra = document.createElement("div");
    extra.id = "ivr-owner-extra";
    extra.innerHTML = `
      <div class="section-card">
        <div class="section-title">📞 IVR Helpline</div>
        <div class="meta">You can also report animal health issues by calling the PashuMitra IVR helpline. No smartphone required.</div>
        <div style="margin-top:10px;background:#e7effe;border-radius:12px;padding:12px;text-align:center">
          <div style="font-size:13px;color:var(--muted)">IVR Phone Number</div>
          <div style="font-size:18px;font-weight:800;color:var(--primary)">${ivrNum}</div>
          <div class="small-muted" style="margin-top:4px">Multilingual: English · Telugu · Hindi · Marathi · Press 9 to repeat, 0 to go back</div>
        </div>
        <div class="btn-row" style="margin-top:12px">
          <button class="btn btn-ghost btn-sm" onclick="location.hash='#/owner/ivr-reports'">View My IVR Reports</button>
        </div>
      </div>`;
    const bnav = appEl.querySelector(".bottom-nav");
    if (bnav) appEl.insertBefore(extra, bnav);
    else appEl.appendChild(extra);
  } catch(e) {}
};

// ---------------- IVR Reports List ----------------
function ivrReportsView(role) {
  route(`#/${role}/ivr-reports`, async () => {
    render(`${header("IVR Reports", {back:true})}<div class="loading">Loading IVR reports…</div>`);
    const reports = await api("/ivr/reports");
    render(`
      ${header("IVR Reports — " + role, {back:true})}
      <div class="section-card">
        <div class="section-title">📞 IVR Reports (${reports.length}) — Source: Phone Call</div>
        <div class="meta" style="margin-bottom:12px">Each report was auto-generated from an IVR phone survey and linked to a normal veterinary case. Urgency is rule-based + AI-assisted, never overriding veterinarian judgment.</div>
        ${reports.length===0 ? emptyState("No IVR reports yet. Farmer calls will appear here automatically.") : reports.map(r=>`
          <div class="list-card" onclick="location.hash='#/${role}/ivr-reports/${r.id}'">
            <div class="row1">
              <span class="title">${r.report_no} <span class="small-muted">via ${r.language||'en'}</span></span>
              <span class="badge ${urgencyBadge(r.urgency)}">${r.urgency||'MEDIUM'}</span>
            </div>
            <div class="meta"><b>Caller:</b> ${r.caller_number ? r.caller_number.slice(0,3)+'****'+r.caller_number.slice(-4) : 'Unknown'} · <b>Animal:</b> ${r.animal_species||'—'} x${r.animal_count||1} · <b>Status:</b> <span class="badge ${ivrStatusBadge(r.status)}">${r.status||'RECEIVED'}</span></div>
            <div class="meta"><b>Location:</b> ${r.location_village||'—'}, ${r.location_district||'—'} (${r.location_source||'FARMER_PROVIDED'}) · ${fmtDate(r.created_at)}</div>
            <div class="meta"><b>Problem:</b> ${r.main_problem||'—'} · <b>Severity:</b> ${r.severity||'—'} · ${r.symptoms? r.symptoms.slice(0,80):''}</div>
            ${r.is_duplicate ? `<div style="margin-top:4px"><span class="badge badge-orange">Possible duplicate</span></div>` : ``}
            ${r.case_id ? `<div class="small-muted" style="margin-top:4px">Linked case: ${r.case_id} → Tap to review</div>` : ``}
          </div>
        `).join("")}
      </div>
      ${role==="govt" ? `<div class="section-card"><button class="btn btn-ghost btn-sm" onclick="location.hash='#/govt/ivr-analytics'">View IVR Analytics</button></div>` : ``}
      ${bottomNav(`#/${role}/ivr-reports`)}
    `);
  }, [role]);
}
ivrReportsView("owner"); ivrReportsView("vet"); ivrReportsView("govt");

// ---------------- IVR Report Detail ----------------
function ivrReportDetailView(role) {
  route(`#/${role}/ivr-reports/:id`, async ({id}) => {
    render(`${header("IVR Report", {back:true})}<div class="loading">Loading report…</div>`);
    const r = await api(`/ivr/reports/${id}`);
    const s = r.ai_structured || {};
    render(`
      ${header(r.report_no, {back:true})}
      <div class="section-card">
        <div class="row1" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <span class="badge ${urgencyBadge(r.urgency)}">${r.urgency} priority</span>
          <span class="badge ${ivrStatusBadge(r.status)}">${r.status}</span>
        </div>
        <div class="detail-grid">
          <div><b>Call ID</b>${r.call_sid||'—'}</div>
          <div><b>Language</b>${r.language||'—'}</div>
          <div><b>Caller</b>${r.caller_number||'—'}</div>
          <div><b>Reported</b>${fmtDate(r.created_at)}</div>
          <div><b>Animal Species</b>${r.animal_species||'—'}</div>
          <div><b>Breed</b>${r.animal_breed||'—'}</div>
          <div><b>Age</b>${r.animal_age||'—'}</div>
          <div><b>Sex</b>${r.animal_sex||'—'}</div>
          <div><b>Count Affected</b>${r.animal_count||1}</div>
          <div><b>Pregnant</b>${r.is_pregnant||'—'}</div>
          <div><b>Main Problem</b>${r.main_problem||'—'}</div>
          <div><b>Severity</b>${r.severity||'—'}</div>
          <div><b>Duration</b>${r.duration||'—'} days</div>
          <div><b>Eating</b>${r.eating_status||'—'}</div>
          <div><b>Drinking</b>${r.drinking_status||'—'}</div>
          <div><b>Temperature</b>${r.temperature||'—'}</div>
          <div><b>Vaccination</b>${r.vaccination_status||'—'}</div>
          <div><b>Location Village</b>${r.location_village||'—'}</div>
          <div><b>District</b>${r.location_district||'—'}</div>
          <div><b>State</b>${r.location_state||'—'}</div>
          <div><b>Location Source</b>${r.location_source||'NOT_AVAILABLE'}</div>
          <div><b>Accuracy</b>${r.location_accuracy||'—'}</div>
          ${r.location_lat ? `<div><b>GPS Lat</b>${r.location_lat}</div><div><b>GPS Lng</b>${r.location_lng}</div>` : ``}
        </div>
        ${r.symptoms ? `<div style="margin-top:10px"><b class="small-muted">Symptoms:</b><div style="font-size:13px">${r.symptoms}</div></div>` : ``}
        ${r.additional_description && r.additional_description!=="Not provided" ? `<div style="margin-top:8px"><b class="small-muted">Additional:</b><div style="font-size:13px">${r.additional_description}</div></div>` : ``}
        ${r.is_duplicate ? `<div class="conflict-box" style="margin-top:10px"><b>⚠️ Possible duplicate of report #${r.duplicate_of_report_id}</b><div>Same caller + species + problem within 24h. Verify before creating duplicate case actions.</div></div>` : ``}
      </div>

      <div class="section-card">
        <div class="subheading">🧠 AI-Generated Summary — veterinarian verification required</div>
        <div style="background:#fff8e1;border-left:4px solid #ffb300;padding:12px;border-radius:8px;font-size:13px;white-space:pre-wrap">${r.ai_summary||'No summary available'}</div>
        ${s && s.animal ? `<div class="detail-grid" style="margin-top:10px">
          <div><b>AI Urgency</b>${s.urgency||'—'}</div>
          <div><b>Follow-up Required</b>${s.follow_up_required ? 'Yes' : 'No'}</div>
        </div>` : ``}
        <div class="small-muted" style="margin-top:6px">Structured JSON validated before storage. Missing fields are “Not provided”, never hallucinated.</div>
      </div>

      ${r.transcript_full ? `<div class="section-card"><div class="subheading">🗣️ Transcript (where permitted)</div><div style="font-size:13px;white-space:pre-wrap;background:#f8f9fe;padding:12px;border-radius:12px">${r.transcript_full.slice(0,2000)}</div></div>` : ``}

      ${r.case_id ? `<div class="section-card">
        <div class="subheading">🔗 Linked Veterinary Case</div>
        <div class="meta">This IVR report auto-created case <b>${r.case_id}</b> in the existing case system. It is visible to vets and government dashboards as a normal report with source=IVR.</div>
        <button class="btn btn-primary btn-sm" style="margin-top:10px" onclick="location.hash='#/${role}/cases/${r.case_id}'">Open Linked Case</button>
      </div>` : ``}

      <div class="section-card">
        <div class="subheading">⚙️ Report Lifecycle</div>
        <div class="meta" style="margin-bottom:8px">Status flow: RECEIVED → AI_SUMMARIZED → VET_NOTIFIED → UNDER_REVIEW → VET_CONTACTED → ACTION_RECOMMENDED → FOLLOW_UP → RESOLVED → CLOSED</div>
        ${role!=="owner" ? `
          <div class="field"><label>Update Status</label><select id="ivrStatusSel">
            ${["RECEIVED","AI_SUMMARIZED","VET_NOTIFIED","UNDER_REVIEW","VET_CONTACTED","ACTION_RECOMMENDED","FOLLOW_UP","RESOLVED","CLOSED","DUPLICATE_FLAGGED"].map(st=>`<option ${st===r.status?"selected":""}>${st}</option>`).join("")}
          </select></div>
          <div class="field"><label>Note (optional)</label><input id="ivrStatusNote" placeholder="e.g. Vet contacted farmer, prescribed ORS" /></div>
          <button class="btn btn-primary btn-sm" onclick="updateIvrStatus(${r.id}, document.getElementById('ivrStatusSel').value, document.getElementById('ivrStatusNote').value)">Update Status</button>
        ` : `<div class="small-muted">Status: <b>${r.status}</b> — a veterinarian will update the case as they review it.</div>`}
      </div>
      ${bottomNav(`#/${role}/ivr-reports`)}
    `);
  }, [role]);
}
ivrReportDetailView("owner"); ivrReportDetailView("vet"); ivrReportDetailView("govt");
window.updateIvrStatus = async function(id, status, note) {
  try { await api(`/ivr/reports/${id}/status`, {method:"PUT", body:{status, note}}); toast("IVR report status updated"); router(); }
  catch(e){ toast(e.message,true); }
};

// ---------------- IVR Call History ----------------
function ivrCallsView(role) {
  route(`#/${role}/ivr-calls`, async () => {
    render(`${header("IVR Call History", {back:true})}<div class="loading">Loading call history…</div>`);
    const calls = await api("/ivr/calls");
    const reports = await api("/ivr/reports").catch(()=>[]);
    const reportByCall = Object.fromEntries(reports.map(r=>[r.call_sid, r]));
    render(`
      ${header("IVR Call History", {back:true})}
      <div class="section-card">
        <div class="section-title">📋 IVR Calls (${calls.length})</div>
        <div class="meta" style="margin-bottom:12px">Every call’s caller ID, language, menu choice, vet connection, survey progress, and resulting report are traceable. Partial disconnects are preserved as PARTIALLY_COMPLETED.</div>
        ${calls.length===0 ? emptyState("No IVR calls yet.") : calls.map(c=> {
          const rep = reportByCall[c.call_sid];
          return `<div class="list-card" onclick="location.hash='#/${role}/ivr-calls/${c.call_sid}'">
            <div class="row1"><span class="title">${c.call_sid}</span><span class="badge ${statusBadgeClass(c.status)}">${c.status}</span></div>
            <div class="meta"><b>Caller:</b> ${c.caller_number_normalized ? c.caller_number_normalized.slice(0,3)+'****'+c.caller_number_normalized.slice(-4) : 'Hidden/Unknown'} → <b>IVR:</b> ${c.ivr_phone_number||'—'} · <b>Lang:</b> ${c.language||'—'} · ${fmtDate(c.created_at)} · ${c.duration_seconds||0}s</div>
            <div class="meta"><b>Report:</b> ${rep ? rep.report_no + ' (' + rep.urgency + ')' : 'No report yet (abandoned/failed)'}${c.is_mock ? ' · <span class="small-muted">Mock call</span>' : ''}</div>
          </div>`;
        }).join("")}
      </div>
      ${bottomNav(`#/${role}/ivr-calls`)}
    `);
  }, [role]);
}
ivrCallsView("vet"); ivrCallsView("govt");

route("#/vet/ivr-calls/:sid", async ({sid}) => {
  render(`${header("Call Detail", {back:true})}<div class="loading">Loading…</div>`);
  const data = await api(`/ivr/calls/${sid}`);
  const c=data.call, s=data.session, responses=data.responses||[], events=data.events||[];
  render(`
    ${header("Call " + c.call_sid.slice(0,12), {back:true})}
    <div class="section-card">
      <div class="row1" style="justify-content:space-between;display:flex;align-items:center;margin-bottom:8px">
        <span class="badge ${statusBadgeClass(c.status)}">${c.status}</span>
        <span class="badge badge-blue">${c.language||'en'}</span>
      </div>
      <div class="detail-grid">
        <div><b>Caller ID</b>${c.caller_number_normalized||c.caller_number||'Hidden'}</div>
        <div><b>IVR Number</b>${c.ivr_phone_number||'—'}</div>
        <div><b>Provider</b>${c.provider||'—'}</div>
        <div><b>Duration</b>${c.duration_seconds||0}s</div>
        <div><b>Recording</b>${c.recording_enabled ? 'Enabled' : 'Disabled'} ${c.recording_consent ? '(consent obtained)' : ''}</div>
        <div><b>Current State</b>${s ? s.current_state : '—'}</div>
      </div>
    </div>
    <div class="section-card">
      <div class="subheading">📝 Survey Responses (${responses.length})</div>
      ${responses.length===0 ? emptyState("No survey responses captured.") : responses.map(r=>`
        <div class="list-card" style="cursor:default">
          <div class="row1"><span class="title">${r.question_key}</span><span class="badge badge-blue">${r.answer_source}</span></div>
          <div class="meta"><b>Q:</b> ${r.question_text||''}</div>
          <div class="meta"><b>A:</b> ${r.answer_normalized||'Not provided'} <span class="small-muted"> (raw: ${r.answer_raw||''})</span></div>
        </div>
      `).join("")}
    </div>
    <div class="section-card">
      <div class="subheading">🕒 Call Events (audit trail)</div>
      <div class="timeline">${events.map(e=>`
        <div class="timeline-item"><div class="timeline-dot"></div><div class="timeline-body"><div class="t-status">${e.event_type}</div><div class="t-note">${e.details||''} ${e.from_state ? `(${e.from_state} → ${e.to_state})` : ''}</div><div class="t-date">${fmtDate(e.created_at)}</div></div></div>
      `).join("")}</div>
    </div>
    ${data.report ? `<div class="section-card"><button class="btn btn-primary btn-sm" onclick="location.hash='#/vet/ivr-reports/${data.report.id}'">View Generated Report</button></div>` : ``}
    ${bottomNav("#/vet/ivr-calls")}
  `);
}, ["vet","govt"]);

// ---------------- IVR Analytics ----------------
route("#/govt/ivr-analytics", async () => {
  render(`${header("IVR Analytics", {back:true})}<div class="loading">Loading IVR analytics…</div>`);
  const a = await api("/ivr/analytics");
  render(`
    ${header("IVR Analytics", {back:true})}
    <div class="section-card">
      <div class="section-title">📊 IVR Performance — Live Database</div>
      <div class="stat-grid" style="margin:0">
        ${statCard(a.total_calls||0, "Total Calls")}
        ${statCard(a.reports_generated||0, "Reports Generated")}
        ${statCard(a.vet_connections||0, "Vet Connected")}
        ${statCard(a.high_priority||0, "High Priority")}
        ${statCard(a.vet_unavailable||0, "Vet Unavailable")}
        ${statCard(Math.round(a.avg_call_duration||0)+"s", "Avg Duration")}
      </div>
    </div>
    <div class="section-card">
      <div class="section-title">📈 Reports by District</div>
      ${barChart(a.by_district)}
    </div>
    <div class="section-card">
      <div class="section-title">🥧 Reports by Problem Category</div>
      ${pieChart(a.by_problem)}
    </div>
    <div class="section-card">
      <div class="section-title">📋 Call Outcomes</div>
      <div class="stat-grid" style="margin:0">
        ${statCard(a.completed_calls||0, "Completed")}
        ${statCard(a.partial_reports||0, "Partial/Abandoned")}
        ${statCard(a.failed_calls||0, "Failed")}
      </div>
    </div>
    ${a.daily && a.daily.length ? `<div class="section-card"><div class="section-title">📅 Daily Trend (last 7 days)</div><div class="table-wrap"><table class="data-table"><thead><tr><th>Date</th><th>Calls</th><th>Reports</th><th>High Pri</th></tr></thead><tbody>${a.daily.map(d=>`<tr><td>${d.date}</td><td>${d.total_calls}</td><td>${d.reports_generated}</td><td>${d.high_priority_reports}</td></tr>`).join("")}</tbody></table></div></div>` : ``}
    ${bottomNav("#/govt/ivr-analytics")}
  `);
}, ["govt","vet"]);

route("#/vet/ivr-analytics", async () => { location.hash="#/govt/ivr-analytics"; }, ["vet"]);

// ---------------- IVR Config (govt only) ----------------
route("#/govt/ivr-config", async () => {
  render(`${header("IVR Configuration", {back:true})}<div class="loading">Loading config…</div>`);
  const cfg = await api("/ivr/config");
  const tel = cfg.telephony || {};
  render(`
    ${header("IVR Configuration", {back:true})}
    <div class="section-card">
      <div class="section-title">⚙️ Telephony & IVR Settings</div>
      <div class="meta" style="margin-bottom:12px">Provider credentials are environment variables (never hard-coded). Phone number is IVR_PHONE_NUMBER. Survey questions are configurable without redeploy.</div>
      <div class="detail-grid">
        <div><b>IVR Phone</b>${tel.ivr_phone_number||'NOT_CONFIGURED'}</div>
        <div><b>Provider</b>${tel.telephony_provider||'mock'}</div>
        <div><b>Recording</b>${tel.recording_enabled ? 'Enabled' : 'Disabled'}</div>
        <div><b>AI Enabled</b>${tel.ai_enabled ? 'Yes' : 'No'}</div>
        <div><b>STT Provider</b>${tel.stt_provider||'whisper'}</div>
        <div><b>Languages</b>${(tel.supported_languages||[]).join(", ")}</div>
        <div><b>Provider Ready</b>${tel.provider_configured ? 'Yes' : 'Mock/dev'}</div>
        <div><b>Async</b>${tel.async_enabled ? 'Yes' : 'No'}</div>
      </div>
      ${tel.ivr_phone_number==="NOT_CONFIGURED" ? `<div class="conflict-box" style="margin-top:12px"><b>⚠️ Production requires IVR_PHONE_NUMBER</b><div>Set IVR_PHONE_NUMBER, TELEPHONY_PROVIDER, TELEPHONY_ACCOUNT_ID, TELEPHONY_AUTH_TOKEN, TELEPHONY_PHONE_NUMBER as environment variables. Current provider is mock (dev). See deployment docs.</div></div>` : ``}
    </div>
    <div class="section-card">
      <div class="subheading">📝 Survey Questions (configurable)</div>
      <div class="meta" style="margin-bottom:8px">Questions are multilingual and support DTMF+speech with 9=repeat, 0=back, #=skip. Pregnancy questions are conditional on species.</div>
      <div style="max-height:240px;overflow-y:auto;background:#f8f9fe;padding:10px;border-radius:12px;font-size:12px">
        <pre style="white-space:pre-wrap;margin:0">${JSON.stringify(cfg.survey, null, 2).slice(0,3000)}</pre>
      </div>
      <div class="small-muted" style="margin-top:8px">To modify, use PUT /api/ivr/config (govt only) with updated JSON.</div>
    </div>
    <div class="section-card">
      <div class="section-title">🔒 Security & Compliance</div>
      <div class="meta">• Webhook signature verification (Twilio/Exotel HMAC) · Rate limiting · Input validation · Audit logging · Idempotency · Role-based access · PII masking · Call recording consent disclosure · Location source accuracy labeling (never fake GPS)</div>
    </div>
    ${bottomNav("#/govt/ivr-config")}
  `);
}, ["govt"]);

// Owner IVR reports are via ivrReportsView("owner") already registered above

