// ==========================================================================
// PashuMitra — SIH Animal Disease Management (Owner + Vet + Govt portals)
// Vanilla JS SPA — no build step. Three separate role logins and role-scoped
// routes. Backend logic (Flask + SQLite) is preserved from the SIH2 build;
// GIS map, analytics, surveillance, campaigns and voice reporting are ported
// from the LivestockHealth app and wired to the REAL database.
// ==========================================================================

const API = "/api";
const ROLES = ["owner", "vet", "govt"];

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
    "role.owner": "Animal Owner", "role.vet": "Veterinarian", "role.govt": "Govt Officer",
    "role.owner.desc": "Register animals, report health issues, track prescriptions",
    "role.vet.desc": "Receive reports, diagnose, lab tests, prescriptions, vaccinations",
    "role.govt.desc": "State analytics, disease surveillance, GIS risk & AI early warning",
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
    "role.owner": "पशुमालक", "role.vet": "पशुवैद्यक", "role.govt": "सरकारी अधिकारी",
    "role.owner.desc": "प्राणी नोंदवा, आरोग्य अहवाल द्या, औषधे पहा",
    "role.vet.desc": "अहवाल स्वीकारा, निदान, प्रयोगशाळा, औषध, लसीकरण",
    "role.govt.desc": "राज्य विश्लेषण, रोग पाळत, जीआयएस धोका आणि एआय पूर्व चेतावणी",
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
  el.textContent = msg;
  el.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (el.className = "toast"), 2800);
}

async function api(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (state.token) headers.Authorization = "Bearer " + state.token;
  const res = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  let data = {};
  try { data = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) {
    if (res.status === 401) logout(true);
    throw new Error(data.error || "Something went wrong. Please try again.");
  }
  return data;
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
  if (["NEW", "ASSIGNED", "UNDER INVESTIGATION"].includes(s)) return "badge-red";
  if (["SAMPLE COLLECTED", "LAB PENDING", "DIAGNOSED", "TREATMENT", "FOLLOW-UP"].includes(s)) return "badge-orange";
  if (["RECOVERED", "CLOSED"].includes(s)) return "badge-green";
  return "badge-blue";
}
function severityBadgeClass(sev) {
  const s = (sev || "").toLowerCase();
  if (s === "high" || s === "critical") return "badge-red";
  if (s === "medium") return "badge-orange";
  return "badge-blue";
}
function riskBadgeClass(level) {
  return level === "High Risk" ? "badge-red" : level === "Moderate Risk" ? "badge-orange" : "badge-green";
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
  return `
  <div class="app-header">
    ${opts.back ? `<button class="header-icon-btn" onclick="history.back()">←</button>`
      : `<button class="header-icon-btn" onclick="location.hash='${notifHref}'">🔔${opts.notif ? '<span class="dot"></span>' : ''}</button>`}
    <h1>${title}</h1>
    <button class="header-icon-btn" onclick="location.hash='${profileHref}'">👤</button>
  </div>`;
}

function bottomNav(active) {
  const role = getUserRole();
  let items = [];
  if (role === "owner") {
    items = [[`#/owner/dashboard`, "🏠", t("nav.dashboard")], [`#/owner/animals`, "🐄", t("nav.animals")],
      [`#/owner/report`, "🚑", t("nav.reporting")], [`#/owner/cases`, "🩺", t("nav.cases")],
      [`#/owner/prescriptions`, "💊", t("nav.rx")], [`#/owner/notifications`, "🔔", t("nav.alerts")]];
  } else if (role === "vet") {
    items = [[`#/vet/dashboard`, "🏠", t("nav.dashboard")], [`#/vet/reports`, "📋", t("nav.reports")],
      [`#/vet/cases`, "🩺", t("nav.cases")], [`#/vet/campaigns`, "💉", t("nav.campaigns")],
      [`#/vet/search`, "🔍", t("nav.search")], [`#/vet/notifications`, "🔔", t("nav.alerts")]];
  } else if (role === "govt") {
    items = [[`#/govt/dashboard`, "📊", t("nav.analytics")], [`#/govt/gis`, "🗺️", t("nav.gis")],
      [`#/govt/surveillance`, "🌐", t("nav.surveillance")], [`#/govt/ai`, "🧠", t("nav.ai")],
      [`#/govt/campaigns`, "💉", t("nav.campaigns")], [`#/govt/notifications`, "🔔", t("nav.alerts")]];
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

  // ---- role-based authorization at the ROUTE level (backend also enforces) ----
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
route("#/", () => renderRoleSelect());
route("#/login/:role", ({ role }) => renderAuth("login", role));
route("#/register/:role", ({ role }) => renderAuth("register", role));

const ROLE_META = {
  owner: { emoji: "🧑‍🌾", label: "role.owner", color: "#1fa971" },
  vet: { emoji: "🩺", label: "role.vet", color: "#2f6fed" },
  govt: { emoji: "🏛️", label: "role.govt", color: "#8e24aa" },
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
        toast("Registration successful!");
        location.hash = homeFor(role);
      } catch (err) { toast(err.message, true); }
    });
  }
}

function loginForm(role) {
  const demo = { owner: "rajesh@example.com", vet: "vet1@example.com", govt: "govt@example.com" }[role];
  return `
  <form id="loginForm">
    <div class="field"><label>Email or Mobile Number</label><input name="identifier" required placeholder="you@example.com" autocomplete="username" /></div>
    <div class="field"><label>Password</label><input name="password" type="password" required placeholder="••••••••" autocomplete="current-password" /></div>
    <button class="btn btn-primary" type="submit">${t("btn.login")}</button>
    <div class="auth-switch">${t("auth.newHere")} <a onclick="location.hash='#/register/${role}'">${t("auth.createAccount")}</a></div>
    <div class="demo-box"><b>Demo account</b><br/>${demo} / password123</div>
  </form>`;
}

function registerForm(role) {
  const showSpec = role === "vet";
  return `
  <form id="registerForm">
    <div class="field"><label>Full Name</label><input name="full_name" required /></div>
    <div class="form-row">
      <div class="field"><label>Mobile Number</label><input name="mobile" required pattern="[0-9]{10}" placeholder="10-digit number" /></div>
      <div class="field"><label>Email</label><input name="email" type="email" required /></div>
    </div>
    <div class="form-row">
      <div class="field"><label>Password</label><input name="password" type="password" required minlength="6" /></div>
      <div class="field"><label>Confirm Password</label><input name="confirm_password" type="password" required minlength="6" /></div>
    </div>
    ${showSpec ? `<div class="field"><label>Specialization</label><input name="specialization" placeholder="e.g. Livestock Medicine" /></div>` : ""}
    <div class="form-row">
      <div class="field"><label>Village</label><input name="village" /></div>
      <div class="field"><label>Block / Taluk</label><input name="block" /></div>
    </div>
    <div class="form-row">
      <div class="field"><label>District</label><input name="district" placeholder="e.g. Pune" /></div>
      <div class="field"><label>State</label><input name="state" value="Maharashtra" /></div>
    </div>
    <button class="btn btn-primary" type="submit">${t("btn.register")}</button>
    <div class="auth-switch">${t("auth.haveAccount")} <a onclick="location.hash='#/login/${role}'">${t("btn.login")}</a></div>
  </form>`;
}

// ============================================================ DASHBOARD ==
route("#/owner/dashboard", () => ownerDashboard(), ["owner"]);
route("#/vet/dashboard", () => vetDashboard(), ["vet"]);
route("#/govt/dashboard", () => govtDashboard(), ["govt"]);

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
        ${iconItem("📋", "User Reports", "#/vet/reports")}
        ${iconItem("🩺", "All Cases", "#/vet/cases")}
        ${iconItem("🧪", "Lab Reports", "#/vet/lab-reports")}
        ${iconItem("🔍", "Search Herd/Animal", "#/vet/search")}
        ${iconItem("💉", "Record Vaccination", "#/vet/vaccination/new")}
        ${iconItem("🗓️", "Vax Campaigns", "#/vet/campaigns")}
        ${iconItem("🌐", "Surveillance", "#/vet/surveillance")}
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
      <div class="section-title">📈 Full Analytics & Reports</div>
      <button class="btn btn-ghost btn-sm" onclick="location.hash='#/govt/analytics'">Open Analytics Dashboard</button>
    </div>
    <div class="section-card">
      <div class="section-title">🧠 AI Early Warning System</div>
      <div class="meta">Disease risk prediction &amp; outbreak detection — trained ML model scored on your real district case data.</div>
      <button class="btn btn-primary btn-sm" style="margin-top:10px" onclick="location.hash='#/govt/ai'">Open AI Risk Analysis</button>
    </div>
    ${bottomNav("#/govt/dashboard")}
  `);
}

// ==================================================== GOVT: ANALYTICS ====
route("#/govt/analytics", async () => {
  render(`${header("Analytics & Reports", { back: true })}<div class="loading">Loading analytics…</div>`);
  const [a, geo] = await Promise.all([api("/govt/analytics"), api("/govt/geo")]);
  const districtRows = geo.map(d => `
    <tr>
      <td><b>${d.district}</b></td>
      <td>${d.cases}</td>
      <td>${d.active}</td>
      <td>${d.affected_animals}</td>
      <td>${d.animal_population}</td>
      <td><span class="badge ${riskBadgeClass(d.risk_level)}">${d.risk_level}</span></td>
    </tr>`).join("");
  render(`
    ${header("Analytics & Reports", { back: true })}
    <div class="stat-grid">
      ${statCard(a.totals.cases, "Total Cases")}
      ${statCard(a.totals.active, "Active")}
      ${statCard(a.totals.animals, "Animals")}
      ${statCard(a.totals.districts, "Districts")}
    </div>
    <div class="section-card">
      <div class="section-title">📋 District Data Table (live from database)</div>
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr><th>District</th><th>Cases</th><th>Active</th><th>Affected</th><th>Population</th><th>Risk</th></tr></thead>
          <tbody>${districtRows || `<tr><td colspan="6">${emptyState("No district data yet.")}</td></tr>`}</tbody>
        </table>
      </div>
    </div>
    <div class="section-card">
      <div class="section-title">📊 Cases by District</div>
      ${barChart(a.cases_by_district)}
    </div>
    <div class="section-card">
      <div class="section-title">📊 Cases by Status</div>
      ${barChart(a.cases_by_status)}
    </div>
    <div class="section-card">
      <div class="section-title">🥧 Disease Spread</div>
      ${pieChart(a.disease_spread)}
    </div>
    <div class="section-card">
      <div class="section-title">📍 Disease Spread per Area</div>
      ${a.disease_by_district.length === 0 ? emptyState("No case data yet.") :
        a.disease_by_district.map(d => `<div class="meta" style="margin:4px 0">📍 <b>${d.district}</b> — ${d.disease}: ${d.value} case(s)</div>`).join("")}
    </div>
    <div class="section-card">
      <div class="section-title">💉 Vaccine Stock Availability (doses)</div>
      ${barChart(a.stock.map(s => ({ label: `${s.district} · ${s.vaccine}`, value: s.doses_available })))}
      <button class="btn btn-ghost btn-sm" style="margin-top:10px" onclick="location.hash='#/govt/stock'">Manage Stock</button>
    </div>
    ${bottomNav("#/govt/dashboard")}
  `);
}, ["govt"]);

// ======================================================== GOVT: GIS ======
let gisState = { geo: [], locations: [], outline: null, disease: "All", risks: ["High Risk", "Moderate Risk", "Low Risk"], map: null, layer: null };

route("#/govt/gis", async () => {
  render(`${header("GIS Risk Map", { back: true })}<div class="loading">Loading map & live district data…</div>`);
  const [geo, locations, outline] = await Promise.all([
    api("/govt/geo"),
    fetch("/maharashtra_locations.json").then(r => r.json()).catch(() => []),
    fetch("/maharashtra_state.geojson").then(r => r.json()).catch(() => null),
  ]);
  gisState = { ...gisState, geo, locations, outline, map: null, layer: null };
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
  document.querySelectorAll(".gis-risk-toggles input").forEach(cb =>
    cb.addEventListener("change", () => {
      const r = cb.dataset.risk;
      gisState.risks = cb.checked ? [...new Set([...gisState.risks, r])] : gisState.risks.filter(x => x !== r);
      drawGis();
    }));
}, ["govt"]);

function initGisMap() {
  if (typeof L === "undefined") {
    document.getElementById("gisMap").innerHTML = emptyState("Map library failed to load (needs internet on first load).");
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
  const { map, layer, geo, locations, disease, risks } = gisState;
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
    render(`${header("Disease Surveillance", { back: true })}<div class="loading">Loading cases…</div>`);
    const cases = await api("/cases");
    render(`
      ${header("Disease Surveillance", { back: true })}
      <div class="section-card" style="padding-bottom:8px">
        <div class="small-muted" style="margin-bottom:10px">All reported cases across the state, pulled live from the database.</div>
        <div class="form-row">
          <div class="field"><label>District</label><select id="svDistrict"><option value="">All</option></select></div>
          <div class="field"><label>Status</label><select id="svStatus"><option value="">All</option></select></div>
        </div>
        <div class="field"><input id="svSearch" placeholder="🔍 Search disease, symptoms, case no…" /></div>
      </div>
      <div class="section-card"><div id="svList"></div></div>
      ${bottomNav(role === "govt" ? "#/govt/surveillance" : "#/vet/dashboard")}
    `);
    const districts = Array.from(new Set(cases.map(c => (c.animal && c.animal.district) || "Unknown"))).sort();
    const statuses = Array.from(new Set(cases.map(c => c.status))).sort();
    const dSel = document.getElementById("svDistrict");
    const sSel = document.getElementById("svStatus");
    districts.forEach(d => dSel.add(new Option(d, d)));
    statuses.forEach(s => sSel.add(new Option(s, s)));
    function draw() {
      const dq = dSel.value, sq = sSel.value, q = document.getElementById("svSearch").value.toLowerCase().trim();
      const rows = cases.filter(c => {
        const dist = (c.animal && c.animal.district) || "Unknown";
        if (dq && dist !== dq) return false;
        if (sq && c.status !== sq) return false;
        if (q) {
          const hay = `${c.case_no} ${c.symptoms || ""} ${c.disease_suspected || ""} ${c.diagnosis || ""}`.toLowerCase();
          if (!hay.includes(q)) return false;
        }
        return true;
      });
      document.getElementById("svList").innerHTML = rows.length === 0 ? emptyState("No cases match the filters.") :
        rows.map(c => `
        <div class="list-card" onclick="location.hash='#/${role}/cases/${c.id}'">
          <div class="row1"><span class="title">${c.case_no}</span><span class="badge ${statusBadgeClass(c.status)}">${c.status}</span></div>
          <div class="meta">${c.animal ? c.animal.animal_code : "—"} · 📍 ${(c.animal && c.animal.district) || "Unknown"} · ${c.symptoms || ""}</div>
          <div class="tag-row">
            <span class="badge ${severityBadgeClass(c.severity)}">${c.severity || "—"}</span>
            <span class="badge badge-blue">${c.disease_suspected || c.diagnosis || "Unspecified"}</span>
            <span class="badge badge-blue">${fmtDate(c.created_at)}</span>
          </div>
        </div>`).join("");
    }
    dSel.addEventListener("change", draw); sSel.addEventListener("change", draw);
    document.getElementById("svSearch").addEventListener("input", draw);
    draw();
  }, [role]);
}
surveillanceView("govt");
surveillanceView("vet");

// ============================================ VACCINATION CAMPAIGNS ======
function campaignsView(role) {
  const canManage = role === "vet" || role === "govt";
  route(`#/${role}/campaigns`, async () => {
    render(`${header("Vaccination Campaigns", { back: true })}<div class="loading">Loading campaigns…</div>`);
    const camps = await api("/campaigns");
    render(`
      ${header("Vaccination Campaigns", { back: true })}
      ${canManage ? `
      <div class="section-card">
        <div class="section-title">➕ ${role === "govt" ? "Create / Plan Campaign" : "Log Campaign"}</div>
        <form id="campForm">
          <div class="field"><label>Campaign Name</label><input name="name" required placeholder="e.g. FMD Drive — Pune" /></div>
          <div class="form-row">
            <div class="field"><label>District</label><input name="district" placeholder="e.g. Pune" ${role === "govt" ? "" : `value="${state.user.district || ""}"`} /></div>
            <div class="field"><label>Vaccine</label><select name="vaccine"><option>FMD</option><option>HS</option><option>BQ</option><option>Brucellosis</option><option>PPR</option><option>LSD</option><option>Other</option></select></div>
          </div>
          <div class="form-row">
            <div class="field"><label>Target Animals</label><input name="target_animals" type="number" min="0" value="0" /></div>
            <div class="field"><label>Status</label><select name="status"><option>PLANNED</option><option>ACTIVE</option><option>PAUSED</option><option>COMPLETED</option><option>CANCELLED</option></select></div>
          </div>
          <div class="form-row">
            <div class="field"><label>Start Date</label><input name="start_date" type="date" /></div>
            <div class="field"><label>End Date</label><input name="end_date" type="date" /></div>
          </div>
          <div class="field"><label>Notes</label><input name="notes" /></div>
          <button class="btn btn-primary" type="submit">${t("btn.create")} Campaign</button>
        </form>
      </div>` : ""}
      <div class="section-card">
        <div class="section-title">🗓️ ${role === "owner" ? "Campaigns in your district" : "All Campaigns"}</div>
        <div id="campList">${camps.length === 0 ? emptyState("No campaigns yet.") : camps.map(c => campCard(c, canManage)).join("")}</div>
      </div>
      ${bottomNav(canManage ? `#/${role}/campaigns` : "#/owner/dashboard")}
    `);
    if (canManage) {
      document.getElementById("campForm").addEventListener("submit", async (e) => {
        e.preventDefault();
        try {
          const body = Object.fromEntries(new FormData(e.target));
          body.target_animals = Number(body.target_animals);
          const c = await api("/campaigns", { method: "POST", body });
          toast(`Campaign ${c.campaign_code} created!`);
          router();
        } catch (err) { toast(err.message, true); }
      });
      bindCampaignManage(camps);
    }
  }, [role]);
}
function campCard(c, canManage) {
  const pct = c.target_animals > 0 ? Math.min(100, Math.round((c.doses_administered / c.target_animals) * 100)) : 0;
  return `<div class="list-card" style="cursor:default">
    <div class="row1"><span class="title">${c.name}</span><span class="badge ${campStatusClass(c.status)}">${c.status}</span></div>
    <div class="meta">${c.campaign_code} · 📍 ${c.district || "—"} · 💉 ${c.vaccine}</div>
    <div class="meta">🗓 ${fmtDate(c.start_date)} → ${fmtDate(c.end_date)}</div>
    <div class="progress-wrap"><div class="progress-bar" style="width:${pct}%"></div></div>
    <div class="meta">${c.doses_administered} / ${c.target_animals} doses (${pct}%)</div>
    ${c.notes ? `<div class="meta">${c.notes}</div>` : ""}
    ${canManage ? `<div style="margin-top:8px"><button class="btn btn-ghost btn-sm" data-camp-manage="${c.id}">Update status / doses</button></div>` : ""}
  </div>`;
}
function campStatusClass(s) {
  return s === "ACTIVE" ? "badge-green" : s === "COMPLETED" ? "badge-blue" : s === "CANCELLED" ? "badge-red" : "badge-orange";
}
function bindCampaignManage(camps) {
  document.querySelectorAll("[data-camp-manage]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = Number(btn.dataset.campManage);
      const c = camps.find(x => x.id === id);
      const status = prompt(`Update status for ${c.campaign_code}\n(PLANNED, ACTIVE, PAUSED, COMPLETED, CANCELLED)`, c.status);
      if (status === null) return;
      const doses = prompt("Doses administered so far:", c.doses_administered);
      if (doses === null) return;
      try {
        await api(`/campaigns/${id}`, { method: "PUT", body: { status: status.toUpperCase(), doses_administered: Number(doses) } });
        toast("Campaign updated");
        router();
      } catch (err) { toast(err.message, true); }
    });
  });
}
campaignsView("owner");
campaignsView("vet");
campaignsView("govt");

// ==================================================== GOVT: VAX STOCK ====
route("#/govt/stock", async () => {
  render(`${header("Vaccine Stock", { back: true })}<div class="loading">Loading…</div>`);
  const a = await api("/govt/analytics");
  render(`
    ${header("Vaccine Stock", { back: true })}
    <div class="section-card">
      <div class="subheading">💉 Update / Add Stock</div>
      <form id="stockForm">
        <div class="form-row">
          <div class="field"><label>District</label><input name="district" required placeholder="e.g. Pune" /></div>
          <div class="field"><label>Vaccine</label><input name="vaccine" required placeholder="e.g. FMD" /></div>
        </div>
        <div class="field"><label>Doses Available</label><input name="doses_available" type="number" min="0" required placeholder="e.g. 500" /></div>
        <button class="btn btn-primary" type="submit">Save Stock</button>
      </form>
    </div>
    <div class="section-card">
      <div class="subheading">Current Stock by District</div>
      ${a.stock.length === 0 ? emptyState("No stock recorded.") : a.stock.map(s => `
        <div class="list-card" style="cursor:default">
          <div class="row1"><span class="title">${s.district} · ${s.vaccine}</span><span class="badge ${s.doses_available > 0 ? "badge-green" : "badge-red"}">${s.doses_available} doses</span></div>
          <div class="meta">Updated ${fmtDate(s.updated_at)}</div>
        </div>`).join("")}
    </div>
    ${bottomNav("#/govt/dashboard")}
  `);
  document.getElementById("stockForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const body = Object.fromEntries(new FormData(e.target));
      body.doses_available = Number(body.doses_available);
      await api("/govt/stock", { method: "PUT", body });
      toast("Vaccine stock saved"); router();
    } catch (err) { toast(err.message, true); }
  });
}, ["govt"]);

// ------------------------------------------------- AI early warning page --
const AI_DISEASES = ["FMD", "LSD (Lumpy Skin)", "Brucellosis", "PPR", "HS (Haemorrhagic Septicaemia)", "Anthrax", "Rabies"];
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
    box.innerHTML = `<div class="loading">Running AI prediction on live district data…</div>`;
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
          <div class="section-title">🗄 Live Data Used (from database)</div>
          <div class="meta">Animals registered: <b>${feat.animal_population}</b></div>
          <div class="meta">Affected animals: <b>${feat.affected_animals}</b></div>
          <div class="meta">New cases (30 days): <b>${feat.new_cases}</b> · Previous cases: <b>${feat.previous_cases}</b></div>
          <div class="meta">Vaccination coverage: <b>${Math.round((feat.vaccination_coverage || 0) * 100)}%</b></div>
          <div class="meta">Case growth rate: <b>${feat.cases_growth_rate}</b></div>
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
    const riskCls = { Critical: "badge-red", High: "badge-red", Moderate: "badge-orange", Low: "badge-green" };
    render(`
      ${header("Disease Info", { back: true })}
      <div class="section-card"><div class="field"><input id="diseaseSearch" placeholder="🔍 Search disease (English or मराठी)…" /></div></div>
      <div id="diseaseList">
        ${diseases.map(d => `
          <div class="section-card disease-card" data-search="${(d.name_en + " " + d.name_mr + " " + (d.aliases || []).join(" ")).toLowerCase()}">
            <div class="row1" style="display:flex;justify-content:space-between;align-items:center;gap:8px">
              <span class="title" style="font-weight:700">${d.name_en}</span>
              <span class="badge ${riskCls[d.riskLevel] || "badge-green"}">${d.riskLevel}</span>
            </div>
            <div class="meta" style="font-size:15px;margin:2px 0 6px">${d.name_mr}</div>
            <div class="meta">${d.description_en}</div>
            <div class="meta" style="margin-top:6px">🦠 <b>${d.category}</b> · Species: ${(d.affectedSpecies || []).join(", ")}</div>
            <div class="meta">💉 Vaccine preventable: <b>${d.vaccinePreventable ? "Yes" : "No"}</b>${d.zoonotic ? " · ⚠️ Zoonotic" : ""}</div>
            <details style="margin-top:8px">
              <summary class="link" style="cursor:pointer;font-size:13px">Symptoms, transmission & prevention</summary>
              <div class="meta" style="margin-top:6px"><b>Symptoms:</b> ${(d.symptoms_en || []).join(" · ")}</div>
              <div class="meta"><b>Symptoms (मराठी):</b> ${(d.symptoms_mr || []).join(" · ")}</div>
              <div class="meta" style="margin-top:4px"><b>Transmission:</b> ${d.transmission_en}</div>
              <div class="meta"><b>Prevention:</b> ${d.prevention_en}</div>
            </details>
          </div>`).join("")}
      </div>
      ${bottomNav(`#/${role}/dashboard`)}
    `);
    document.getElementById("diseaseSearch").addEventListener("input", (e) => {
      const q = e.target.value.toLowerCase().trim();
      document.querySelectorAll(".disease-card").forEach(card => {
        card.style.display = !q || card.dataset.search.includes(q) ? "" : "none";
      });
    });
  }, [role]);
}
diseasesView("owner"); diseasesView("vet"); diseasesView("govt");

// ============================================================== PROFILE ==
function profileView(role) {
  route(`#/${role}/profile`, () => {
    const u = state.user;
    const roleLabel = { owner: "Animal Owner", vet: "Veterinarian", govt: "Government Officer" }[u.role];
    render(`
      ${header("My Profile", { back: true })}
      <div class="section-card">
        <div class="detail-grid">
          <div><b>Name</b>${u.full_name}</div>
          <div><b>Role</b>${roleLabel}</div>
          <div><b>Mobile</b>${u.mobile}</div>
          <div><b>Email</b>${u.email}</div>
          <div><b>Village</b>${u.village || "—"}</div>
          <div><b>Block</b>${u.block || "—"}</div>
          <div><b>District</b>${u.district || "—"}</div>
          <div><b>State</b>${u.state || "Maharashtra"}</div>
          ${u.specialization ? `<div><b>Specialization</b>${u.specialization}</div>` : ""}
        </div>
      </div>
      <div class="section-card">
        <div class="subheading">🌐 ${t("lang.label")}</div>
        ${langToggle()}
      </div>
      <div class="section-card">
        <button class="btn btn-outline" onclick="logout()">${t("btn.logout")}</button>
      </div>
      ${bottomNav("")}
    `);
  }, [role]);
}
profileView("owner"); profileView("vet"); profileView("govt");

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
        <div class="list-card" style="cursor:default">
          <div class="row1"><span class="title">${h.herd_code}</span><span class="badge badge-blue">${h.animal_count} animals</span></div>
          <div class="meta">📍 ${h.village || "—"}, ${h.block || "—"}, ${h.district || "—"}</div>
        </div>`).join("")}
    </div>
    ${bottomNav("#/owner/dashboard")}
  `);
}, ["owner"]);

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
animalsListView("owner");

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
      </div>
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
  }, [role]);
}
animalRecordView("owner"); animalRecordView("vet"); animalRecordView("govt");

// =========================================================== REPORT =====
route("#/owner/report", async ({ animal, voice }) => {
  const animals = await api("/animals");
  const preselected = animals.find(a => String(a.id) === String(animal));
  render(`
    ${header("Report Health Issue", { back: true })}
    <div class="section-card">
      ${animals.length === 0 ? emptyState("Add an animal first before reporting an issue.") : `
      ${preselected ? `<div class="small-muted" style="margin-bottom:10px">Reporting for <b>${preselected.animal_code} — ${preselected.animal_name || preselected.animal_type}</b>.</div>` : ""}
      <form id="reportForm">
        <div class="field"><label>Animal</label>
          <select name="animal_id" required>${animals.map(a => `<option value="${a.animal_code}" ${preselected && a.id === preselected.id ? "selected" : ""}>${a.animal_code} — ${a.animal_name || a.animal_type || a.species}</option>`).join("")}</select>
        </div>
        <div class="field"><label>Symptoms</label><input name="symptoms" id="symptomsInput" required placeholder="e.g. Fever, Reduced eating" /></div>
        <div class="field"><label>Disease Suspected (optional)</label><input name="disease_suspected" placeholder="e.g. FMD" /></div>
        <div class="field"><label>Severity</label><select name="severity"><option>Low</option><option selected>Medium</option><option>High</option></select></div>
        <div class="field"><label>Description</label><textarea name="description" id="descInput" placeholder="Describe what you observed..."></textarea></div>
        <button class="btn btn-primary" type="submit">Submit Report</button>
      </form>`}
    </div>

    <div class="section-card">
      <div class="subheading">🎤 Voice Field Report (offline Whisper)</div>
      <div class="small-muted" style="margin-bottom:10px">Describe the animal's condition by voice — it is transcribed on-device and fills the report. First use downloads the model (needs internet once); after that it works offline.</div>
      <div class="voice-box">
        <button id="recBtn" class="btn btn-outline btn-sm" type="button">🎤 Start Recording</button>
        <span id="voiceStatus" class="small-muted">Idle</span>
      </div>
      <div id="voiceProgress" class="small-muted" style="margin-top:8px"></div>
      <div id="voiceTranscript" class="voice-transcript" style="display:none"></div>
    </div>

    <div class="section-card">
      <div class="subheading">📞 Can't type? Call a vet (IVR voice help)</div>
      <div class="small-muted" style="margin-bottom:10px">Tap Call to speak directly with a veterinarian — they can register the report for you over the phone (IVR).</div>
      <div id="vetContacts"><div class="loading">Loading vet contacts…</div></div>
    </div>`);
  loadVetContacts();
  setupVoiceReport();
  if (voice) setTimeout(() => document.getElementById("recBtn") && document.getElementById("recBtn").scrollIntoView({ behavior: "smooth", block: "center" }), 200);
  const form = document.getElementById("reportForm");
  if (form) form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const body = Object.fromEntries(new FormData(e.target));
      body.reported_through = window.__voiceUsed ? "Voice App" : "Mobile App";
      const c = await api("/cases", { method: "POST", body });
      window.__voiceUsed = false;
      toast(`Case ${c.case_no} created and sent to the veterinary team!`);
      location.hash = "#/owner/cases/" + c.id;
    } catch (err) { toast(err.message, true); }
  });
}, ["owner"]);

async function loadVetContacts() {
  const box = document.getElementById("vetContacts");
  if (!box) return;
  try {
    const vets = await api("/vets");
    box.innerHTML = vets.length === 0 ? emptyState("No veterinarians registered yet.") : vets.map(v => `
      <div class="list-card" style="cursor:default">
        <div class="row1"><span class="title">🩺 ${v.full_name}</span><span class="badge badge-blue">${v.specialization || "Veterinarian"}</span></div>
        <div class="meta">📍 ${v.district || "—"} · 📞 ${v.mobile}</div>
        <div style="margin-top:8px"><a class="btn btn-primary btn-sm" style="text-decoration:none;display:inline-block" href="tel:${v.mobile}">📞 Call Now</a></div>
      </div>`).join("");
  } catch (e) { box.innerHTML = emptyState("Could not load vet contacts."); }
}

// ---------------------------------------------------- voice (whisper) ----
let voiceWorker = null, mediaRecorder = null, audioChunks = [];
function getVoiceWorker() {
  if (!voiceWorker) {
    voiceWorker = new Worker("/whisper-worker.js", { type: "module" });
    voiceWorker.addEventListener("message", onVoiceMessage);
  }
  return voiceWorker;
}
function setVoiceStatus(txt) { const el = document.getElementById("voiceStatus"); if (el) el.textContent = txt; }
function onVoiceMessage(e) {
  const m = e.data;
  const prog = document.getElementById("voiceProgress");
  if (m.type === "progress") {
    if (prog && m.data && m.data.status === "progress" && typeof m.data.progress === "number") {
      prog.textContent = `Loading model… ${m.data.progress}%`;
    } else if (prog && m.data && m.data.status) {
      prog.textContent = `Loading model… ${m.data.status}`;
    }
  } else if (m.type === "ready") {
    setVoiceStatus("Model ready"); if (prog) prog.textContent = "";
  } else if (m.type === "result") {
    setVoiceStatus("Transcribed ✓");
    const txt = (m.text || "").trim();
    const box = document.getElementById("voiceTranscript");
    if (box) { box.style.display = "block"; box.textContent = `“${txt}”`; }
    const desc = document.getElementById("descInput");
    const sym = document.getElementById("symptomsInput");
    if (desc && txt) { desc.value = (desc.value ? desc.value + " " : "") + txt; }
    if (sym && txt && !sym.value) { sym.value = txt.slice(0, 80); }
    window.__voiceUsed = true;
  } else if (m.type === "error") {
    setVoiceStatus("Error");
    if (prog) prog.textContent = "⚠️ " + m.error;
    toast("Voice transcription failed: " + m.error, true);
  }
}
function setupVoiceReport() {
  const btn = document.getElementById("recBtn");
  if (!btn) return;
  // Pre-warm the model in the background
  try { getVoiceWorker().postMessage({ type: "load" }); } catch (e) { /* ignore */ }
  btn.addEventListener("click", async () => {
    if (mediaRecorder && mediaRecorder.state === "recording") { mediaRecorder.stop(); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioChunks = [];
      mediaRecorder = new MediaRecorder(stream);
      mediaRecorder.ondataavailable = (ev) => audioChunks.push(ev.data);
      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach(tr => tr.stop());
        btn.textContent = "🎤 Start Recording";
        setVoiceStatus("Transcribing…");
        const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType });
        try {
          const audio = await decodeToFloat32(blob);
          getVoiceWorker().postMessage({ type: "transcribe", audio, language: "english" });
        } catch (err) {
          setVoiceStatus("Error");
          toast("Could not process audio: " + err.message, true);
        }
      };
      mediaRecorder.start();
      btn.textContent = "⏹ Stop Recording";
      setVoiceStatus("Recording… speak now");
    } catch (err) {
      setVoiceStatus("Mic denied");
      toast("Microphone access denied or unavailable.", true);
    }
  });
}
async function decodeToFloat32(blob) {
  const arrayBuf = await blob.arrayBuffer();
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx();
  const decoded = await ctx.decodeAudioData(arrayBuf);
  const targetRate = 16000;
  const input = decoded.getChannelData(0);
  const ratio = decoded.sampleRate / targetRate;
  const outLen = Math.floor(input.length / ratio);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) out[i] = input[Math.floor(i * ratio)];
  ctx.close();
  return out;
}

// ============================================================= CASES ====
function casesListView(role) {
  route(`#/${role}/cases`, async () => {
    render(`${header("Cases", { back: true })}<div class="loading">Loading…</div>`);
    const cases = await api("/cases");
    render(`
      ${header(role === "owner" ? "My Cases" : "All Cases", { back: true })}
      <div class="section-card">
        ${cases.length === 0 ? emptyState("No cases yet.") : cases.map(c => `
          <div class="list-card" onclick="location.hash='#/${role}/cases/${c.id}'">
            <div class="row1"><span class="title">${c.case_no}</span><span class="badge ${statusBadgeClass(c.status)}">${c.status}</span></div>
            <div class="meta">${c.animal ? c.animal.animal_code : "—"} · ${c.symptoms || ""}</div>
            <div class="tag-row">
              <span class="badge ${severityBadgeClass(c.severity)}">${c.severity || "—"}</span>
              <span class="badge badge-blue">${c.reported_through === "IVR" ? "📞 IVR" : c.reported_through === "Voice App" ? "🎤 Voice" : "📱 Mobile App"}</span>
            </div>
          </div>`).join("")}
      </div>
      ${bottomNav(role === "owner" ? "#/owner/cases" : `#/${role}/dashboard`)}
    `);
  }, [role]);
}
casesListView("owner"); casesListView("vet"); casesListView("govt");

route("#/vet/reports", async () => {
  render(`${header("User Reports", { back: true })}<div class="loading">Loading…</div>`);
  const cases = await api("/vet/reports");
  render(`
    ${header("User Reports", { back: true })}
    <div class="section-card">
      <div class="small-muted" style="margin-bottom:10px">All health issues reported by animal owners, pulled live from the database.</div>
      ${cases.length === 0 ? emptyState("No reports yet.") : cases.map(c => `
        <div class="list-card" onclick="location.hash='#/vet/cases/${c.id}'">
          <div class="row1"><span class="title">${c.case_no}</span><span class="badge ${statusBadgeClass(c.status)}">${c.status}</span></div>
          <div class="meta">${c.animal.animal_code}${c.herd ? " · " + c.herd.herd_code : ""} · ${c.symptoms || ""}</div>
          <div class="meta">Owner: ${c.owner.full_name} (${c.owner.mobile})</div>
          <div class="tag-row">
            <span class="badge ${severityBadgeClass(c.severity)}">${c.severity}</span>
            <span class="badge badge-blue">${c.reported_through === "IVR" ? "📞 IVR" : c.reported_through === "Voice App" ? "🎤 Voice" : "📱 Mobile App"}</span>
          </div>
        </div>`).join("")}
    </div>
    ${bottomNav("#/vet/reports")}
  `);
}, ["vet"]);

// Case detail (shared render; vet gets actions, owner/govt read-only)
function caseDetailView(role) {
  route(`#/${role}/cases/:id`, async ({ id }) => {
    render(`${header("Case Detail", { back: true })}<div class="loading">Loading…</div>`);
    const c = await api(`/cases/${id}`);
    const isVet = role === "vet";
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
          <div><b>Owner</b>${c.owner.full_name}</div>
          <div><b>Reported via</b>${c.reported_through === "IVR" ? "📞 IVR" : c.reported_through === "Voice App" ? "🎤 Voice" : "📱 Mobile App"}</div>
          <div><b>Symptoms</b>${c.symptoms || "—"}</div>
          <div><b>Disease Suspected</b>${c.disease_suspected || "—"}</div>
          <div><b>Diagnosis</b>${c.diagnosis || "—"}</div>
          <div><b>Treatment</b>${c.treatment || "—"}</div>
          <div><b>Vet Assigned</b>${c.vet_name || "Unassigned"}</div>
          <div><b>Reported On</b>${fmtDate(c.created_at)}</div>
        </div>
        ${c.description ? `<div style="margin-top:10px"><b class="small-muted">Description:</b><div style="font-size:13.5px">${c.description}</div></div>` : ""}
      </div>
      <div id="trackWrap"></div>
      ${isVet ? vetCaseActions(c) : ""}
      <div class="section-card">
        <div class="subheading">🧪 Laboratory</div>
        ${c.lab_requests.map(l => `<div class="list-card" style="cursor:default"><div class="row1"><span class="title">${l.test_requested || "Test"}</span><span class="badge badge-blue">${l.status}</span></div><div class="meta">Sample: ${l.sample_type || "—"} · Priority: ${l.priority}</div></div>`).join("")}
        ${c.lab_reports.map(l => `<div class="list-card" style="cursor:default"><div class="row1"><span class="title">${l.report_no}</span><span class="badge ${l.result === 'NEGATIVE' ? 'badge-green' : 'badge-red'}">${l.result || "Pending"}</span></div><div class="meta">${l.test_name} · ${fmtDate(l.test_date)} · ${l.notes || ""}</div></div>`).join("")}
        ${c.lab_requests.length === 0 && c.lab_reports.length === 0 ? emptyState("No lab activity yet.") : ""}
      </div>
      <div class="section-card">
        <div class="subheading">💊 Prescriptions</div>
        ${c.prescriptions.length === 0 ? emptyState("No prescriptions issued yet.") : c.prescriptions.map(p => `
          <div class="list-card" style="cursor:default">
            <div class="row1"><span class="title">${p.medicine}</span><span class="badge badge-blue">${p.dosage || ""}</span></div>
            <div class="meta">${p.frequency || ""} for ${p.duration || ""} · Follow-up: ${fmtDate(p.follow_up_date)}</div>
            <div class="meta">${p.instructions || ""}</div>
          </div>`).join("")}
      </div>
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
        <div class="small-muted" style="margin-bottom:10px">This case is solved. You can remove the report from the system permanently.</div>
        <button class="btn btn-outline" onclick="deleteCase(${c.id},'${c.case_no}')">Delete Report</button>
      </div>` : ""}
      ${bottomNav(`#/${role}/cases`)}
    `);
    if (isVet) bindVetCaseActions(c);
    loadTracking(c, role);
  }, [role]);
}
caseDetailView("owner"); caseDetailView("vet"); caseDetailView("govt");

// ============================ LIVE FIELD-VISIT TRACKING (Zomato-style) ====
let trackTimer = null, trackMap = null, trackVet = null, trackLine = null, trackCtx = null;

function trackStages(t) {
  const s = t.visit ? t.visit.status : null;
  const done = p => p ? "badge-green" : "badge-blue";
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

async function loadTracking(c, role) {
  const wrap = document.getElementById("trackWrap");
  if (!wrap) return;
  if (trackTimer) { clearInterval(trackTimer); trackTimer = null; }
  trackMap = trackVet = trackLine = null;
  try {
    const t = await api(`/cases/${c.id}/track`);
    trackCtx = { c, role };
    wrap.innerHTML = trackingCardHTML(t, c, role);
    if (t.visit) initTrackMap(t);
    bindVisitControls(c, role);
    if (t.visit && t.visit.status === "ON_THE_WAY") {
      trackTimer = setInterval(() => pollTrack(c, role), 2500);
    }
  } catch (e) { wrap.innerHTML = ""; }
}

function trackingCardHTML(t, c, role) {
  const isOwner = role === "owner";
  const v = t.visit;
  if (!v) {
    return `<div class="section-card">
      <div class="subheading">🛰 Live Vet Tracking</div>
      ${emptyState(isOwner ? "No vet has accepted this report yet. You'll see live GPS here once the vet starts travelling to your farm." : "No active field visit for this case.")}
      ${role === "vet" ? `<button class="btn btn-primary" id="visitStart">🚗 Start Field Visit (I'm on the way)</button>` : ""}
    </div>`;
  }
  const pct = Math.round((t.progress || 0) * 100);
  const etaMin = Math.ceil((t.eta_seconds || 0) / 60);
  const statusLabel = { ON_THE_WAY: "🚗 Vet is on the way", ARRIVED: "📍 Vet has arrived", COMPLETED: "💊 Visit completed", CANCELLED: "⛔ Visit cancelled" }[v.status];
  return `<div class="section-card">
    <div class="subheading">🛰 Live Vet Tracking</div>
    <div class="tag-row" style="margin-bottom:10px">
      ${trackStages(t).map(([lbl, cls]) => `<span class="badge ${cls}">${lbl}</span>`).join("")}
    </div>
    <div class="row1" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
      <b style="font-size:14px">${statusLabel}</b>
      <span class="small-muted" id="trackEta">${v.status === "ON_THE_WAY" ? `ETA ~${etaMin} min` : v.status === "ARRIVED" ? "At the farm now" : "Done"}</span>
    </div>
    <div class="progress-wrap"><div class="progress-bar" id="trackProg" style="width:${pct}%"></div></div>
    <div class="small-muted" style="margin:6px 0 10px"> ${t.vet ? t.vet.name : "Vet"} · 📍 ${t.owner ? t.owner.address : ""}</div>
    <div id="trackMap" class="gis-map" style="height:240px;border-radius:14px"></div>
    <div class="btn-row" style="margin-top:12px">
      ${isOwner && t.vet && t.vet.mobile ? `<a class="btn btn-primary" href="tel:${t.vet.mobile}">📞 Call Vet</a>` : ""}
      ${role === "vet" ? visitControlButtons(v) : ""}
    </div>
  </div>`;
}

function visitControlButtons(v) {
  if (v.status === "ON_THE_WAY") return `
    <button class="btn btn-primary" id="visitArrive">📍 Mark Arrived</button>
    <button class="btn btn-outline" id="visitCancel">⛔ Cancel</button>`;
  if (v.status === "ARRIVED") return `<button class="btn btn-primary" id="visitComplete">💊 Complete Visit</button>`;
  return "";
}

function initTrackMap(t) {
  if (typeof L === "undefined") return;
  const to = [t.to.lat, t.to.lng];
  const from = [t.from.lat, t.from.lng];
  trackMap = L.map("trackMap", { zoomControl: false }).setView(to, 11);
  if (navigator.onLine) L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "&copy; OpenStreetMap" }).addTo(trackMap);
  L.circleMarker(to, { radius: 9, color: "#1fa971", fillColor: "#1fa971", fillOpacity: 0.9 }).bindTooltip("🏠 Your farm").addTo(trackMap);
  L.circleMarker(from, { radius: 7, color: "#3d4db8", fillColor: "#3d4db8", fillOpacity: 0.7 }).bindTooltip("🩺 Vet start").addTo(trackMap);
  trackLine = L.polyline([from, [t.vet_lat, t.vet_lng]], { color: "#e08a1e", weight: 3, dashArray: "6 6" }).addTo(trackMap);
  trackVet = L.circleMarker([t.vet_lat, t.vet_lng], { radius: 10, color: "#e08a1e", fillColor: "#e08a1e", fillOpacity: 0.95 }).bindTooltip("🚗 Vet (live)").addTo(trackMap);
  setTimeout(() => trackMap && trackMap.invalidateSize(), 200);
}

async function pollTrack(c, role) {
  try {
    const t = await api(`/cases/${c.id}/track`);
    if (!t.visit || t.visit.status !== "ON_THE_WAY") { clearInterval(trackTimer); trackTimer = null; loadTracking(c, role); return; }
    if (trackVet) trackVet.setLatLng([t.vet_lat, t.vet_lng]);
    if (trackLine) trackLine.setLatLngs([[t.from.lat, t.from.lng], [t.vet_lat, t.vet_lng]]);
    const bar = document.getElementById("trackProg"); if (bar) bar.style.width = Math.round((t.progress || 0) * 100) + "%";
    const eta = document.getElementById("trackEta"); if (eta) eta.textContent = `ETA ~${Math.ceil((t.eta_seconds || 0) / 60)} min`;
  } catch (e) { /* keep last known position */ }
}

function bindVisitControls(c, role) {
  const wire = (id, status, msg) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("click", async () => {
      try { await api(`/cases/${c.id}/visit`, { method: "PUT", body: { status } }); toast(msg); loadTracking(c, role); }
      catch (err) { toast(err.message, true); }
    });
  };
  wire("visitArrive", "ARRIVED", "Marked as arrived");
  wire("visitComplete", "COMPLETED", "Visit completed");
  wire("visitCancel", "CANCELLED", "Visit cancelled");
  const start = document.getElementById("visitStart");
  if (start) start.addEventListener("click", async () => {
    try { await api(`/cases/${c.id}/visit`, { method: "POST", body: { travel_seconds: 240 } }); toast("Visit started — owner can now track you live"); loadTracking(c, role); }
    catch (err) { toast(err.message, true); }
  });
}

function vetCaseActions(c) {
  const statuses = ["NEW", "ASSIGNED", "UNDER INVESTIGATION", "SAMPLE COLLECTED", "LAB PENDING", "DIAGNOSED", "TREATMENT", "FOLLOW-UP", "RECOVERED", "CLOSED"];
  return `
    <div class="section-card">
      <div class="subheading">🩺 Update Case</div>
      <form id="statusForm">
        <div class="field"><label>Status</label><select name="status">${statuses.map(s => `<option ${s === c.status ? "selected" : ""}>${s}</option>`).join("")}</select></div>
        <div class="field"><label>Diagnosis</label><input name="diagnosis" value="${c.diagnosis || ""}" /></div>
        <div class="field"><label>Treatment Notes</label><textarea name="treatment">${c.treatment || ""}</textarea></div>
        <div class="field"><label>Update Note</label><input name="note" placeholder="What changed?" /></div>
        <button class="btn btn-primary" type="submit">Save Update</button>
      </form>
    </div>
    <div class="section-card">
      <div class="subheading">🧪 Request Lab Test</div>
      <form id="labReqForm">
        <div class="form-row">
          <div class="field"><label>Sample Type</label><input name="sample_type" placeholder="e.g. Blood" required /></div>
          <div class="field"><label>Priority</label><select name="priority"><option>Normal</option><option>High</option></select></div>
        </div>
        <div class="field"><label>Test Requested</label><input name="test_requested" placeholder="e.g. HS Culture Test" required /></div>
        <div class="field"><label>Notes</label><input name="notes" /></div>
        <button class="btn btn-ghost" type="submit">Send Lab Request</button>
      </form>
    </div>
    <div class="section-card">
      <div class="subheading">🧬 Enter Lab Report</div>
      <form id="labRepForm">
        ${c.lab_requests.length ? `<div class="field"><label>Linked Lab Request</label><select name="lab_request_id">${c.lab_requests.map(l => `<option value="${l.id}">${l.test_requested} (${l.status})</option>`).join("")}</select></div>` : ""}
        <div class="form-row">
          <div class="field"><label>Sample</label><input name="sample" placeholder="Blood / Swab" /></div>
          <div class="field"><label>Test Name</label><input name="test_name" required /></div>
        </div>
        <div class="form-row">
          <div class="field"><label>Result</label><select name="result"><option>NEGATIVE</option><option>POSITIVE</option><option>INCONCLUSIVE</option></select></div>
          <div class="field"><label>Test Date</label><input name="test_date" type="date" value="${new Date().toISOString().slice(0, 10)}" /></div>
        </div>
        <div class="field"><label>Notes</label><input name="notes" /></div>
        <button class="btn btn-ghost" type="submit">Save Lab Report</button>
      </form>
    </div>
    <div class="section-card">
      <div class="subheading">💊 Create E-Prescription</div>
      <form id="rxForm">
        <div class="field"><label>Diagnosis</label><input name="diagnosis" value="${c.diagnosis || ""}" /></div>
        <div class="form-row">
          <div class="field"><label>Medicine</label><input name="medicine" required /></div>
          <div class="field"><label>Dosage</label><input name="dosage" placeholder="e.g. 0.5 mg/kg" /></div>
        </div>
        <div class="form-row">
          <div class="field"><label>Frequency</label><input name="frequency" placeholder="e.g. Once daily" /></div>
          <div class="field"><label>Duration</label><input name="duration" placeholder="e.g. 5 days" /></div>
        </div>
        <div class="field"><label>Instructions</label><textarea name="instructions"></textarea></div>
        <div class="field"><label>Follow-up Date</label><input name="follow_up_date" type="date" /></div>
        <button class="btn btn-primary" type="submit">Issue Prescription</button>
      </form>
    </div>`;
}

function bindVetCaseActions(c) {
  document.getElementById("statusForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api(`/cases/${c.id}`, { method: "PUT", body: Object.fromEntries(new FormData(e.target)) }); toast("Case updated successfully"); router(); }
    catch (err) { toast(err.message, true); }
  });
  document.getElementById("labReqForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { const body = Object.fromEntries(new FormData(e.target)); body.case_id = c.id; await api("/lab/requests", { method: "POST", body }); toast("Lab request sent"); router(); }
    catch (err) { toast(err.message, true); }
  });
  document.getElementById("labRepForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { const body = Object.fromEntries(new FormData(e.target)); body.case_id = c.id; await api("/lab/reports", { method: "POST", body }); toast("Lab report saved"); router(); }
    catch (err) { toast(err.message, true); }
  });
  document.getElementById("rxForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { const body = Object.fromEntries(new FormData(e.target)); body.case_id = c.id; await api("/prescriptions", { method: "POST", body }); toast("E-prescription issued"); router(); }
    catch (err) { toast(err.message, true); }
  });
}

// ====================================================== VET: SEARCH =====
route("#/vet/search", () => {
  render(`
    ${header("Search Herd/Animal", { back: true })}
    <div class="section-card">
      <form id="searchForm" class="field" style="display:flex;gap:8px">
        <input name="q" placeholder="Animal ID or Herd ID..." style="flex:1" />
        <button class="btn btn-primary btn-sm" type="submit">Search</button>
      </form>
      <div id="searchResults"></div>
    </div>
    ${bottomNav("#/vet/search")}
  `);
  document.getElementById("searchForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = new FormData(e.target).get("q");
    const res = await api(`/vet/search?q=${encodeURIComponent(q)}`);
    document.getElementById("searchResults").innerHTML = `
      ${res.animals.length ? `<div class="subheading">Animals</div>` + res.animals.map(a => `
        <div class="list-card" onclick="location.hash='#/vet/animals/${a.id}'"><div class="row1"><span class="title">${a.animal_code}</span><span class="badge badge-blue">${a.species}</span></div><div class="meta">${a.village || ""}, ${a.district || ""}</div></div>`).join("") : ""}
      ${res.herds.length ? `<div class="subheading">Herds</div>` + res.herds.map(h => `
        <div class="list-card" style="cursor:default"><div class="row1"><span class="title">${h.herd_code}</span></div><div class="meta">${h.village || ""}, ${h.district || ""}</div></div>`).join("") : ""}
      ${(!res.animals.length && !res.herds.length) ? emptyState("No matches found.") : ""}
    `;
  });
}, ["vet"]);

// ==================================================== VET: VACCINATION ==
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
prescriptionsView("owner"); prescriptionsView("vet");

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
      ${bottomNav(role === "owner" ? "#/owner/lab-reports" : `#/${role}/dashboard`)}
    `);
  }, [role]);
}
labReportsView("owner"); labReportsView("vet");

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
notificationsView("owner"); notificationsView("vet"); notificationsView("govt");

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
