"use strict";
/* Face Attendance Pro - the whole interface. Every piece of text from the server is inserted with textContent. */
const $ = (s, r = document) => r.querySelector(s);

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false || k === "value") continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (["checked", "disabled", "hidden", "selected", "multiple"].includes(k)) el[k] = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  if (props && props.value != null) el.value = props.value;
  return el;
}

const ICONS = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>',
  camera: '<path d="M3 8h4l2-3h6l2 3h4v12H3z"/><circle cx="12" cy="13" r="4"/>',
  scan: '<path d="M4 8V5a1 1 0 011-1h3M16 4h3a1 1 0 011 1v3M20 16v3a1 1 0 01-1 1h-3M8 20H5a1 1 0 01-1-1v-3"/><circle cx="12" cy="11" r="3"/><path d="M7 18c1-2 3-3 5-3s4 1 5 3"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2 20c0-3.5 3-5.5 7-5.5s7 2 7 5.5"/><path d="M16 4.5a3.5 3.5 0 010 7M18 14.8c2.4.6 4 2.2 4 5.2"/>',
  report: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h7M9 17h7"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3M14 9l2 2"/>',
  cog: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
};
const put = (parent, ...kids) => parent.append(...kids.flat(Infinity).filter(k => k != null && k !== false));
function icon(name) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.innerHTML = ICONS[name] || "";
  return s;
}

/* ---------------- state and helpers ---------------- */
const S = { me: null, csrf: null, org: "Face Attendance", needsSetup: false, info: {}, presets: {}, settings: null, cleanup: null };
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const can = (...p) => !!S.me && p.some(x => S.me.perms.includes(x));
const isStudent = () => !!(S.me && S.me.student_id);
const pageVisible = p => can(...p.any) && (!p.student || isStudent()) && (p.id !== "leaves" || can("manage_leaves") || (can("apply_leave") && isStudent()));
const initials = n => (n || "?").split(/\s+/).map(w => w[0] || "").slice(0, 2).join("").toUpperCase();
const t12 = t => { if (!t) return "-"; const [H, M] = t.split(":"); const hr = +H; return `${hr % 12 || 12}:${M} ${hr < 12 ? "AM" : "PM"}`; };
function today() {
  const tz = S.settings && S.settings.timezone;
  try { return new Date().toLocaleDateString("en-CA", tz ? { timeZone: tz } : undefined); } catch (e) { return new Date().toLocaleDateString("en-CA"); }
}
const shiftDays = (iso, n) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return d.toLocaleDateString("en-CA"); };
const niceDate = iso => new Date(iso + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });

async function api(path, opt = {}) {
  const method = opt.method || (opt.body !== undefined || opt.raw !== undefined || opt.form ? "POST" : "GET");
  const headers = { "X-Requested-With": "fetch" };
  if (S.csrf) headers["X-CSRF-Token"] = S.csrf;
  let body;
  if (opt.raw !== undefined) body = opt.raw;
  else if (opt.form) body = opt.form;
  else if (opt.body !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opt.body); }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opt.timeout || 20000);
  let res;
  try { res = await fetch(path, { method, headers, body, signal: ctrl.signal }); }
  catch (e) { throw new Error(e.name === "AbortError" ? "The server did not answer in time." : "Cannot reach the server."); }
  finally { clearTimeout(timer); }
  let data = null;
  if ((res.headers.get("content-type") || "").includes("json")) data = await res.json();
  if (!res.ok) {
    if (res.status === 401 && S.me && !opt.quiet) { S.me = null; S.csrf = null; render(); }
    const err = new Error((data && data.error) || `Something went wrong (${res.status}).`);
    err.status = res.status;
    throw err;
  }
  return data;
}

function toast(msg, kind) {
  const t = h("div", { class: "toast " + (kind || ""), text: msg });
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "bad" ? 6000 : 3500);
}
const fail = e => toast(e.message || String(e), "bad");

function openDialog(title, body, actions = [], opts = {}) {
  const dlg = h("dialog", { class: opts.wide ? "wide" : "" });
  const close = () => { if (dlg.open) dlg.close(); };
  dlg.addEventListener("close", () => { dlg.remove(); if (opts.onClose) opts.onClose(); });
  const foot = h("div", { class: "dlg-f" }, actions.map(a => h("button", {
    class: "btn " + (a.kind || ""), text: a.label,
    onclick: async ev => {
      const btn = ev.currentTarget;
      if (!a.run) return close();
      btn.disabled = true;
      try { if ((await a.run(close)) !== false && !a.keepOpen) close(); } catch (e) { fail(e); }
      btn.disabled = false;
    },
  })));
  dlg.append(h("div", { class: "dlg-h" }, h("h2", { text: title }), h("button", { class: "btn ghost sm right", "aria-label": "Close", text: "\u2715", onclick: close })),
    h("div", { class: "dlg-b" }, body), foot);
  document.body.append(dlg);
  dlg.showModal();
  return { dlg, close };
}
function confirmBox(title, text, label = "Delete") {
  return new Promise(resolve => {
    let done = false;
    openDialog(title, h("p", { text }), [
      { label: "Cancel", kind: "ghost" },
      { label, kind: "danger", run: () => { done = true; resolve(true); } },
    ], { onClose: () => { if (!done) resolve(false); } });
  });
}
function field(label, input, hint) { return h("div", { class: "field" }, h("label", { text: label }), input, hint ? h("div", { class: "small mut", text: hint }) : null); }
function badge(text, kind) { return h("span", { class: "badge " + (kind || ""), text }); }
const STATUS = { present: ["Present", "ok"], absent: ["Absent", "bad"], leave: ["On leave", "acc"], pending: ["Not yet", ""], holiday: ["Holiday", "warn"], off: ["Day off", ""] };
const statusBadge = s => badge((STATUS[s] || [s])[0], (STATUS[s] || [])[1]);
const ring = pct => h("div", { class: "ring " + (pct == null ? "" : pct >= 75 ? "good" : "low"), style: `--p:${pct || 0}` }, h("div", {}, h("b", { text: pct == null ? "-" : pct + "%" })));

/* ---------------- credentials card (shown once) ---------------- */
function showCredentials(name, userId, password, title = "Login created", onDone) {
  const body = h("div", {},
    h("p", { class: "small mut", text: "Passwords are stored in a scrambled form, so this is the only time this password can be shown. Give it to the person now. If it is lost, use Reset password to make a new one." }),
    h("div", { class: "cred", style: "margin-top:12px" },
      h("div", {}, h("span", { class: "mut small", text: "Name" }), h("div", { text: name })),
      h("div", {}, h("span", { class: "mut small", text: "User ID" }), h("div", {}, h("code", { text: userId }))),
      h("div", {}, h("span", { class: "mut small", text: "Password" }), h("div", {}, h("code", { text: password }))),
      h("div", {}, h("span", { class: "mut small", text: "Sign in at" }), h("div", { text: location.origin }))));
  openDialog(title, body, [
    { label: "Copy", kind: "ghost", keepOpen: true, run: async () => { try { await navigator.clipboard.writeText(`User ID: ${userId}\nPassword: ${password}\n${location.origin}`); toast("Copied", "ok"); } catch (e) { toast("Select the text and copy it by hand."); } return false; } },
    { label: "Download CSV", kind: "ghost", keepOpen: true, run: () => {
      const csv = `Name,User ID,Password\n"${name.replace(/"/g, '""')}","${userId}","${password}"\n`;
      const a = h("a", { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: `login-${userId}.csv` });
      a.click(); return false; } },
    { label: "Print", kind: "ghost", keepOpen: true, run: () => { window.print(); return false; } },
    { label: "Done", run: () => {} },
  ], { onClose: onDone });
}

/* ---------------- camera ---------------- */
function makeCamera(opts) {
  const video = h("video", { playsinline: "", autoplay: "" });
  video.muted = true;
  const canvas = h("canvas");
  const ph = h("div", { class: "ph", text: "The camera is off." });
  const box = h("div", { class: "cam" }, video, canvas, ph);
  const off = document.createElement("canvas");
  let running = false, busy = false, timer = null, stream = null, fails = 0, sent = 0;
  const status = t => opts.status && opts.status(t);

  function draw(d) {
    const cw = box.clientWidth, ch = box.clientHeight;
    canvas.width = cw; canvas.height = ch;
    const g = canvas.getContext("2d");
    const vw = video.videoWidth || d.w, vh = video.videoHeight || d.h;
    const sc = Math.min(cw / vw, ch / vh), ox = (cw - vw * sc) / 2, oy = (ch - vh * sc) / 2, k = vw * sc / d.w;
    g.lineWidth = 3; g.font = "600 15px system-ui,sans-serif";
    const colors = { ok: "#16a34a", bad: "#dc2626", warn: "#f59e0b", enroll: "#f59e0b" };
    for (const f of d.faces || []) {
      const x = ox + f.x * k, y = oy + f.y * k, w = f.w * k, hh = f.h * k;
      g.strokeStyle = g.fillStyle = colors[f.state] || "#dc2626";
      g.strokeRect(x, y, w, hh);
      const label = f.name || "";
      const tw = g.measureText(label).width + 10, ty = Math.max(y, 22);
      g.fillRect(x, ty - 22, tw, 22);
      g.fillStyle = "#fff"; g.fillText(label, x + 5, ty - 6);
    }
  }

  async function tick() {
    if (!running || busy || !video.videoWidth) return;
    busy = true;
    try {
      const W = 640, H = Math.round(W * video.videoHeight / video.videoWidth);
      off.width = W; off.height = H;
      off.getContext("2d").drawImage(video, 0, 0, W, H);
      const blob = await new Promise(r => off.toBlob(r, "image/jpeg", 0.75));
      if (!blob) throw new Error("could not read a picture from the camera");
      const d = await api(opts.endpoint(), { raw: blob, timeout: 8000, quiet: false });
      fails = 0; sent++;
      draw(d);
      status(`Server saw ${d.faces.length} face(s) in ${d.ms || 0} ms. Pictures sent: ${sent}.`);
      if (opts.onResult) opts.onResult(d);
    } catch (e) {
      status("Problem: " + e.message);
      if (++fails >= 3 && opts.onLost) opts.onLost(e);
      if (e.status === 401 || e.status === 403) stop();
    }
    busy = false;
  }

  async function start() {
    if (running) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      toast("The camera needs a secure address (https://, or localhost on this computer).", "bad"); return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" }, audio: false });
      video.srcObject = stream; await video.play();
      running = true; ph.hidden = true; fails = 0;
      timer = setInterval(tick, opts.interval || 300);
      if (opts.onStart) opts.onStart();
    } catch (e) { toast("Could not open the camera: " + e.message, "bad"); }
  }
  function stop() {
    running = false; clearInterval(timer);
    if (stream) stream.getTracks().forEach(t => t.stop());
    stream = null; video.srcObject = null; ph.hidden = false;
    canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
    if (opts.onStop) opts.onStop();
  }
  return { el: box, start, stop, isOn: () => running };
}

/* ---------------- sign in / first-time setup ---------------- */
async function loadMe() {
  const d = await api("/api/me", { quiet: true });
  S.me = d.user; S.csrf = d.csrf; S.org = d.org_name || S.org; S.needsSetup = d.needs_setup;
  S.info = d.perms_info || {}; S.presets = d.presets || {};
  S.settings = S.me ? await api("/api/settings") : null;
}

function authScreen(setup) {
  const err = h("div", { class: "err", role: "alert" });
  const f = {
    org: h("input", { placeholder: "Your school or company", autocomplete: "organization" }),
    name: h("input", { placeholder: "Your name", autocomplete: "name" }),
    uid: h("input", { placeholder: setup ? "For example: admin" : "Your User ID", autocomplete: "username", autocapitalize: "none", spellcheck: "false" }),
    pwd: h("input", { type: "password", placeholder: setup ? "At least 6 characters" : "Your password", autocomplete: setup ? "new-password" : "current-password" }),
    pwd2: h("input", { type: "password", placeholder: "Type it again", autocomplete: "new-password" }),
  };
  const show = h("label", { class: "check small", style: "margin-top:6px" }, h("input", { type: "checkbox", onchange: e => { f.pwd.type = f.pwd2.type = e.target.checked ? "text" : "password"; } }), "Show password");
  const btn = h("button", { class: "btn", style: "width:100%", text: setup ? "Create admin account" : "Sign in" });
  async function go(ev) {
    ev.preventDefault(); err.textContent = ""; btn.disabled = true;
    try {
      if (setup) {
        if (f.pwd.value !== f.pwd2.value) throw new Error("The two passwords are different.");
        await api("/api/setup", { body: { org_name: f.org.value, name: f.name.value, user_id: f.uid.value, password: f.pwd.value } });
      } else await api("/api/login", { body: { user_id: f.uid.value, password: f.pwd.value } });
      await loadMe(); location.hash = ""; render();
    } catch (e) { err.textContent = e.message; }
    btn.disabled = false;
  }
  return h("div", { class: "auth" }, h("form", { class: "card", onsubmit: go },
    h("img", { class: "logo", src: "/icon-192.png", alt: "" }),
    h("h1", { text: setup ? "Welcome. Set up your admin account" : S.org }),
    h("p", { class: "mut", style: "margin:4px 0 16px", text: setup ? "This account can add everyone else and decide what each person may use." : "Sign in with your User ID and password." }),
    setup ? field("Organisation name", f.org) : null, setup ? field("Your name", f.name) : null,
    field("User ID", f.uid), field("Password", f.pwd), setup ? field("Confirm password", f.pwd2) : null, show, err, btn));
}

/* ---------------- shell, navigation ---------------- */
const PAGES = [
  { id: "dashboard", title: "Dashboard", icon: "grid", any: ["view_reports", "take_attendance", "manage_leaves", "manage_students"], fn: pageDashboard },
  { id: "me", title: "My attendance", icon: "user", any: ["view_own"], student: true, fn: pageMe },
  { id: "live", title: "Live camera", icon: "camera", any: ["take_attendance"], fn: pageLive },
  { id: "checkin", title: "My check-in", icon: "scan", any: ["self_checkin"], student: true, fn: pageCheckin },
  { id: "students", title: "Students", icon: "users", any: ["manage_students", "view_reports"], fn: pageStudents },
  { id: "reports", title: "Reports", icon: "report", any: ["view_reports"], fn: pageReports },
  { id: "leaves", title: "Leave", icon: "calendar", any: ["manage_leaves", "apply_leave"], fn: pageLeaves },
  { id: "users", title: "Users & access", icon: "key", any: ["manage_users"], fn: pageUsers },
  { id: "settings", title: "Settings", icon: "cog", any: ["manage_settings"], fn: pageSettings },
];
let installPrompt = null;
window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); installPrompt = e; const b = $("#installBtn"); if (b) b.hidden = false; });

function cleanup() { if (S.cleanup) { try { S.cleanup(); } catch (e) { /* ignore */ } S.cleanup = null; } }

function render() {
  cleanup();
  const app = $("#app"); app.replaceChildren();
  document.title = S.org;
  if (S.needsSetup) { app.append(authScreen(true)); return; }
  if (!S.me) { app.append(authScreen(false)); return; }
  const pages = PAGES.filter(pageVisible);
  if (!pages.length) {
    app.append(h("div", { class: "auth" }, h("div", { class: "card" }, h("h1", { text: "No features yet" }),
      h("p", { class: "mut", style: "margin:8px 0 14px", text: "Your account has no features switched on. Ask the admin to give you access." }),
      h("button", { class: "btn ghost", text: "Log out", onclick: logout }))));
    return;
  }
  const wanted = (location.hash || "").replace(/^#\/?/, "");
  const page = pages.find(p => p.id === wanted) || pages[0];
  const content = h("div", {});
  const nav = h("nav", { class: "nav" }, pages.map(p => h("a", { href: "#/" + p.id, class: p.id === page.id ? "on" : "" }, icon(p.icon), h("span", { text: p.title }))));
  const side = h("aside", { class: "side" }, h("div", { class: "brand" }, h("img", { src: "/icon-192.png", alt: "" }), h("span", { text: S.org })), nav,
    h("button", { id: "installBtn", class: "btn ghost sm install", hidden: !installPrompt, text: "Install app", onclick: async () => { if (installPrompt) { installPrompt.prompt(); await installPrompt.userChoice; installPrompt = null; $("#installBtn").hidden = true; } } }));
  const head = h("div", { class: "head" }, h("h1", { text: page.title }),
    h("div", { class: "who right" }, h("b", { text: S.me.name }), badge(S.me.role, "acc"),
      h("button", { class: "btn ghost sm", text: "Password", onclick: passwordDialog }), h("button", { class: "btn ghost sm", text: "Log out", onclick: logout })));
  app.append(h("div", { class: "shell" }, side, h("main", { class: "main" }, head, content)));
  page.fn(content).catch(fail);
}
window.addEventListener("hashchange", render);

async function logout() { try { await api("/api/logout", { body: {} }); } catch (e) { /* ignore */ } S.me = null; S.csrf = null; location.hash = ""; await loadMe().catch(() => {}); render(); }

function passwordDialog() {
  const o = h("input", { type: "password", autocomplete: "current-password" }), n = h("input", { type: "password", autocomplete: "new-password" }), n2 = h("input", { type: "password", autocomplete: "new-password" });
  openDialog("Change my password", h("div", {}, field("Current password", o), field("New password", n, "At least 6 characters"), field("New password again", n2)), [
    { label: "Cancel", kind: "ghost" },
    { label: "Change password", run: async () => {
      if (n.value !== n2.value) throw new Error("The two new passwords are different.");
      await api("/api/change-password", { body: { old: o.value, new: n.value } });
      await loadMe(); toast("Password changed", "ok");
    } },
  ]);
}

/* ---------------- dashboard ---------------- */
async function pageDashboard(root) {
  const d = await api("/api/dashboard");
  const stat = (n, l, kind) => h("div", { class: "card stat" }, h("span", { class: "n", text: n }), h("span", { class: "l", text: l }), kind ? badge(kind[0], kind[1]) : null);
  put(root, 
    !d.trained ? h("div", { class: "note warn", style: "margin-bottom:14px", text: "No faces are trained yet, so the camera cannot recognise anyone. Add students and enrol their faces on the Students page." }) : null,
    d.pending_leaves && can("manage_leaves") ? h("div", { class: "note", style: "margin-bottom:14px" }, `${d.pending_leaves} leave request(s) are waiting. `, h("a", { href: "#/leaves", text: "Review them" })) : null,
    h("div", { class: "grid g4" },
      stat(d.total_students, "Active students"), stat(d.present_today, "Present today"),
      stat(d.is_working_day ? d.absent_today : "-", d.is_working_day ? "Not arrived / absent" : "Not a working day"),
      stat(d.on_leave_today, "On approved leave"), stat(d.is_working_day ? d.attendance_rate + "%" : "-", "Today's attendance"),
      stat(d.average_percent == null ? "-" : d.average_percent + "%", "Average over time")),
    h("div", { class: "grid g2", style: "margin-top:14px" },
      h("div", { class: "card" }, h("h2", { text: "Below 75% attendance" }),
        d.low.length ? h("div", { class: "tablewrap" }, h("table", {}, d.low.map(s => h("tr", {}, h("td", {}, h("div", { class: "person" }, h("span", { class: "av", text: initials(s.name) }), h("div", {}, h("div", { text: s.name }), h("div", { class: "small mut", text: s.roll })))), h("td", { class: "act" }, badge(s.percent + "%", "bad")))))) : h("div", { class: "empty", text: "Everyone is at 75% or above." })),
      h("div", { class: "card" }, h("h2", { text: "Recent activity" }),
        d.recent.length ? h("div", { class: "tablewrap" }, h("table", {}, h("tr", {}, ["Name", "Date", "Entry", "Exit"].map(x => h("th", { text: x }))), d.recent.map(r => h("tr", {}, h("td", { text: r.name }), h("td", { text: niceDate(r.date) }), h("td", { text: t12(r.entry) }), h("td", { text: t12(r.exit) }))))) : h("div", { class: "empty", text: "No attendance yet." }))));
}

/* ---------------- a student's own page ---------------- */
function historyTable(rows) {
  return h("div", { class: "tablewrap" }, h("table", {}, h("tr", {}, ["Date", "Status", "Entry", "Exit"].map(x => h("th", { text: x }))),
    rows.map(r => h("tr", {}, h("td", { text: niceDate(r.date) }), h("td", {}, statusBadge(r.status)), h("td", { text: t12(r.entry) }), h("td", { text: t12(r.exit) })))));
}
function summaryView(d) {
  return h("div", {}, h("div", { class: "row", style: "gap:22px;align-items:center" }, ring(d.percent),
    h("div", { class: "grid g4", style: "flex:1;min-width:240px" },
      ...[["Present", d.present], ["Absent", d.absent], ["Leave days", d.leave], ["Days counted", d.counted_days]].map(([l, n]) => h("div", { class: "stat" }, h("span", { class: "n", text: n }), h("span", { class: "l", text: l }))))),
    h("div", { class: "note", style: "margin:14px 0", text: "Approved leave days are not counted at all, so while a student is on leave the percentage stays exactly where it was: it does not go down, and it does not go up." }),
    historyTable(d.history));
}
async function pageMe(root) {
  const d = await api("/api/my");
  const t = d.today;
  put(root, h("div", { class: "card" }, h("h2", { text: d.student.name + (d.student.roll ? "  (" + d.student.roll + ")" : "") }),
    h("div", { class: "row small mut", style: "margin:4px 0 14px" }, (d.student.branch ? d.student.branch + "  \u00b7  " : "") + "Counting since " + niceDate(d.start)),
    h("div", { class: "big" + (t && t.status === "present" ? " ok" : ""), style: "margin-bottom:14px" }, h("div", {}, h("b", { text: "Today: " }), t ? [statusBadge(t.status), "  Entry ", t12(t.entry), "   Exit ", t12(t.exit)] : "-")),
    summaryView(d)));
}

/* ---------------- live attendance camera ---------------- */
function modePicker(onchange) {
  return h("select", { onchange: e => onchange(e.target.value), "aria-label": "Mode" },
    h("option", { value: "auto", text: "Auto: first sighting is entry, a later one is exit" }),
    h("option", { value: "entry", text: "Entry only" }), h("option", { value: "exit", text: "Exit only" }));
}
function eventSayer(banner, refresh) {
  const shown = {};
  let holdUntil = 0;   // keep a green "entered / left" message on screen for a few seconds
  const say = (kind, ...parts) => { banner.className = "big " + kind; banner.replaceChildren(h("div", {}, ...parts)); };
  return events => {
    for (const e of events || []) {
      const key = e.student_id + ":" + e.type, now = Date.now();
      const success = e.type === "entry" || e.type === "exit";
      if (shown[key] && now - shown[key] < 10000) continue;
      if (!success && now < holdUntil) continue;
      shown[key] = now;
      if (success) holdUntil = now + 6000;
      if (e.type === "entry") { say("ok", h("b", { text: e.name }), " entered at " + t12(e.time)); toast(`${e.name}: entry ${t12(e.time)}`, "ok"); }
      else if (e.type === "exit") { say("ok", h("b", { text: e.name }), " left at " + t12(e.time)); toast(`${e.name}: exit ${t12(e.time)}`, "ok"); }
      else if (e.type === "already") say("", h("b", { text: e.name }), " is already marked (entry " + t12(e.time) + ").");
      else if (e.type === "too_soon") say("", h("b", { text: e.name }), " was marked at " + t12(e.time) + ". The exit is recorded when seen again later.");
      else if (e.type === "no_entry") say("warn", h("b", { text: e.name }), " has no entry today, so no exit was recorded.");
      if (refresh && (e.type === "entry" || e.type === "exit")) refresh();
    }
  };
}

async function pageLive(root) {
  let mode = "auto";
  const banner = h("div", { class: "big" }, h("div", { text: "Press Start camera. Faces are recognised and attendance is marked automatically." }));
  const diag = h("div", { class: "small mut", style: "margin-top:8px", role: "status" });
  const list = h("div");
  const btn = h("button", { class: "btn", text: "Start camera" });
  async function refresh() {
    const d = await api("/api/attendance/today");
    list.replaceChildren(d.rows.length ? h("div", { class: "tablewrap" }, h("table", {}, h("tr", {}, ["Name", "Entry", "Exit", "Time in"].map(x => h("th", { text: x }))),
      d.rows.map(r => h("tr", {}, h("td", {}, h("div", {}, r.name), h("div", { class: "small mut", text: r.roll })), h("td", { text: t12(r.entry) }), h("td", { text: t12(r.exit) }), h("td", { text: r.duration || "-" }))))) : h("div", { class: "empty", text: "Nobody has been marked yet today." }));
    return d;
  }
  const say = eventSayer(banner, () => refresh().catch(() => {}));
  const cam = makeCamera({
    endpoint: () => `/api/recognize?mode=${mode}`, status: t => { diag.textContent = t; },
    onResult: d => say(d.events),
    onLost: () => { banner.className = "big warn"; banner.replaceChildren(h("div", { text: "Lost connection to the server. Check your internet." })); },
    onStart: () => { btn.textContent = "Stop camera"; }, onStop: () => { btn.textContent = "Start camera"; },
  });
  btn.onclick = () => (cam.isOn() ? cam.stop() : cam.start());
  put(root, h("div", { class: "split" },
    h("div", { class: "card" }, cam.el, h("div", { class: "row", style: "margin-top:12px" }, btn, h("div", { style: "flex:1;min-width:220px" }, modePicker(v => { mode = v; }))), diag, h("div", { style: "margin-top:12px" }, banner)),
    h("div", { class: "card" }, h("h2", { style: "margin-bottom:8px", text: "Today" }), list)));
  const first = await refresh();
  if (!first.trained) banner.className = "big warn", banner.replaceChildren(h("div", { text: "No faces are trained yet. Enrol students on the Students page first." }));
  const poll = setInterval(() => refresh().catch(() => {}), 30000);
  S.cleanup = () => { cam.stop(); clearInterval(poll); };
}

async function pageCheckin(root) {
  let mode = "auto";
  const banner = h("div", { class: "big" }, h("div", { text: "Press Start camera and look at it. Only your own face is accepted." }));
  const diag = h("div", { class: "small mut", style: "margin-top:8px", role: "status" });
  const btn = h("button", { class: "btn", text: "Start camera" });
  const say = eventSayer(banner);
  const cam = makeCamera({
    endpoint: () => `/api/selfcheck?mode=${mode}`, status: t => { diag.textContent = t; },
    onResult: d => say(d.events), onStart: () => { btn.textContent = "Stop camera"; }, onStop: () => { btn.textContent = "Start camera"; },
  });
  btn.onclick = () => (cam.isOn() ? cam.stop() : cam.start());
  put(root, h("div", { class: "card", style: "max-width:720px" }, cam.el,
    h("div", { class: "row", style: "margin-top:12px" }, btn, h("div", { style: "flex:1;min-width:220px" }, modePicker(v => { mode = v; }))), diag, h("div", { style: "margin-top:12px" }, banner)));
  S.cleanup = () => cam.stop();
}

/* ---------------- students ---------------- */
function enrolDialog(st, done) {
  const SHOTS = 40;
  let capturing = false, startPhotos = st.photos || 0, last = st.photos || 0, finished = false;
  const msg = h("p", { class: "small", role: "status", text: "Press Start camera, face it, then press Start capture." });
  const fill = h("span"), bar = h("div", { class: "bar", style: "margin:10px 0" }, fill);
  const diag = h("div", { class: "small mut" });
  const capBtn = h("button", { class: "btn", text: "Start capture", disabled: true });
  const camBtn = h("button", { class: "btn ghost", text: "Start camera" });
  let ref;
  const cam = makeCamera({
    endpoint: () => `/api/enroll/frame?student=${st.id}` + (capturing ? "" : "&preview=1"),
    status: t => { diag.textContent = t; },
    onStart: () => { camBtn.textContent = "Stop camera"; capBtn.disabled = false; },
    onStop: () => { camBtn.textContent = "Start camera"; capBtn.disabled = true; capturing = false; capBtn.textContent = "Start capture"; },
    onResult: async d => {
      last = d.photos;
      if (!capturing) { msg.textContent = d.faces.length ? "Face found. Press Start capture." : "No face found. Look straight at the camera and add some light."; return; }
      const got = Math.max(0, last - startPhotos);
      fill.style.width = Math.min(100, got / SHOTS * 100) + "%";
      if (got >= SHOTS && !finished) {
        finished = true; capturing = false; cam.stop();
        msg.textContent = "Photos saved. Training the model...";
        try { const r = await api("/api/train", { body: {}, timeout: 240000 }); toast(r.msg, r.ok ? "ok" : ""); if (done) done(); if (ref) ref.close(); } catch (e) { fail(e); }
      } else msg.textContent = !d.faces.length ? `No face found (${got} of ${SHOTS}). Look at the camera and add light.` : d.faces[0].w / d.w < 0.14 ? `Move a little closer (${got} of ${SHOTS}).` : `Capturing ${got} of ${SHOTS}. Turn your head slightly.`;
    },
  });
  camBtn.onclick = () => (cam.isOn() ? cam.stop() : cam.start());
  capBtn.onclick = () => {
    if (capturing) { capturing = false; capBtn.textContent = "Start capture"; return; }
    startPhotos = last; capturing = true; capBtn.textContent = "Stop capture"; fill.style.width = "0";
  };
  ref = openDialog("Enrol face: " + st.name, h("div", {}, cam.el, h("div", { class: "row", style: "margin-top:10px" }, camBtn, capBtn), bar, msg, diag), [{ label: "Close", kind: "ghost" }],
    { wide: true, onClose: () => { cam.stop(); if (done) done(); } });
}

async function studentSummaryDialog(st) {
  const d = await api(`/api/students/${st.id}/summary`);
  openDialog(`${st.name}${st.roll ? " (" + st.roll + ")" : ""}`, summaryView(d), [{ label: "Close" }], { wide: true });
}

async function pageStudents(root) {
  const edit = can("manage_students"), canView = can("view_reports");
  let data, q = "";
  const body = h("div"), count = h("span"), trainedBadge = h("span");
  const search = h("input", { placeholder: "Search name or roll", "aria-label": "Search", style: "max-width:260px", oninput: e => { q = e.target.value.toLowerCase(); draw(); } });
  async function load() { data = await api("/api/students"); draw(); }

  function addDialog() {
    const f = { name: h("input", { placeholder: "Full name" }), roll: h("input", { placeholder: "Roll number" }), branch: h("input", { placeholder: "Branch or class" }),
      joined: h("input", { type: "date", value: today() }), uid: h("input", { placeholder: "Leave empty to use the roll number", autocapitalize: "none" }) };
    const mk = h("input", { type: "checkbox", checked: true });
    openDialog("Add a student", h("div", {}, h("div", { class: "fields" }, field("Name", f.name), field("Roll number", f.roll), field("Branch / class", f.branch), field("Joined on", f.joined, "Attendance is counted from this date")),
      h("label", { class: "check", style: "margin-top:6px" }, mk, "Also create a login (User ID and password) for this student"), field("User ID for the login", f.uid)), [
      { label: "Cancel", kind: "ghost" },
      { label: "Add student", run: async () => {
        const r = await api("/api/students", { body: { name: f.name.value, roll: f.roll.value, branch: f.branch.value, joined: f.joined.value, create_login: mk.checked, user_id: f.uid.value } });
        await load();
        const st = data.students.find(s => s.id === r.id);
        const next = () => { if (edit) enrolDialog(st, load); };
        if (r.login) showCredentials(f.name.value, r.login.user_id, r.login.password, "Student added", next); else next();
      } },
    ]);
  }
  function editDialog(st) {
    const f = { name: h("input", { value: st.name }), roll: h("input", { value: st.roll }), branch: h("input", { value: st.branch }), joined: h("input", { type: "date", value: st.joined }) };
    const act = h("input", { type: "checkbox", checked: st.active });
    openDialog("Edit " + st.name, h("div", {}, h("div", { class: "fields" }, field("Name", f.name), field("Roll number", f.roll), field("Branch / class", f.branch), field("Joined on", f.joined)),
      h("label", { class: "check" }, act, "Active (an inactive student is not recognised and not counted)")), [
      { label: "Delete photos", kind: "danger", run: async () => { if (await confirmBox("Delete photos", `Delete all face photos of ${st.name}? They will not be recognised until enrolled again.`)) { await api(`/api/students/${st.id}/photos`, { method: "DELETE" }); await load(); toast("Photos deleted"); } else return false; } },
      { label: "Cancel", kind: "ghost" },
      { label: "Save", run: async () => { await api(`/api/students/${st.id}`, { method: "PUT", body: { name: f.name.value, roll: f.roll.value, branch: f.branch.value, joined: f.joined.value, active: act.checked } }); await load(); toast("Saved", "ok"); } },
    ]);
  }
  function loginDialog(st) {
    const uid = h("input", { value: (st.roll || "").toLowerCase(), autocapitalize: "none" });
    openDialog("Create a login for " + st.name, field("User ID", uid, "3 to 32 letters, numbers, dot, dash or underscore. A password is made for you."), [
      { label: "Cancel", kind: "ghost" },
      { label: "Create login", run: async () => {
        const r = await api("/api/users", { body: { user_id: uid.value, name: st.name, role: "student", student_id: st.id } });
        await load(); showCredentials(st.name, r.user_id, r.password);
      } },
    ]);
  }
  async function remove(st) {
    if (!(await confirmBox("Delete student", `Delete ${st.name}, their face photos, attendance and login? This cannot be undone.`))) return;
    await api(`/api/students/${st.id}`, { method: "DELETE" }); await load(); toast("Student deleted");
  }
  function draw() {
    count.textContent = `${data.students.length} / ${data.limit} students`;
    trainedBadge.replaceChildren(badge(data.trained ? "Model trained" : "Model not trained", data.trained ? "ok" : "warn"));
    const rows = data.students.filter(s => !q || s.name.toLowerCase().includes(q) || (s.roll || "").toLowerCase().includes(q));
    body.replaceChildren(rows.length ? h("div", { class: "tablewrap" }, h("table", {}, h("tr", {}, ["Student", "Branch", "Photos", "Login", "Status", ""].map(x => h("th", { text: x }))),
      rows.map(s => h("tr", {},
        h("td", {}, h("div", { class: "person" }, h("span", { class: "av", text: initials(s.name) }), h("div", {}, h("div", { text: s.name }), h("div", { class: "small mut", text: s.roll || "no roll number" })))),
        h("td", { text: s.branch || "-" }), h("td", {}, badge(s.photos + " photos", s.photos ? "ok" : "warn")),
        h("td", {}, s.login ? h("code", { text: s.login }) : h("span", { class: "mut", text: "none" })), h("td", {}, badge(s.active ? "Active" : "Inactive", s.active ? "" : "bad")),
        h("td", { class: "act" },
          canView ? h("button", { class: "btn ghost sm", text: "View", onclick: () => studentSummaryDialog(s).catch(fail) }) : null,
          edit ? [h("button", { class: "btn ghost sm", text: "Enrol face", onclick: () => enrolDialog(s, load) }), h("button", { class: "btn ghost sm", text: "Edit", onclick: () => editDialog(s) }),
            !s.login && can("manage_users") ? h("button", { class: "btn ghost sm", text: "Create login", onclick: () => loginDialog(s) }) : null,
            h("button", { class: "btn danger sm", text: "Delete", onclick: () => remove(s).catch(fail) })] : null))))) : h("div", { class: "empty", text: data.students.length ? "No student matches your search." : "No students yet. Press Add student." }));
  }
  put(root, h("div", { class: "card" }, h("div", { class: "row", style: "margin-bottom:12px" }, search, count, trainedBadge,
    h("span", { class: "right row" }, edit ? [h("button", { class: "btn ghost", text: "Train model", onclick: async () => { try { const r = await api("/api/train", { body: {}, timeout: 240000 }); toast(r.msg, r.ok ? "ok" : ""); await load(); } catch (e) { fail(e); } } }),
      h("button", { class: "btn", text: "Add student", onclick: addDialog })] : null)), body));
  await load();
}

/* ---------------- reports ---------------- */
async function pageReports(root) {
  const edit = can("edit_attendance");
  const f = { from: h("input", { type: "date", value: today() }), to: h("input", { type: "date", value: today() }),
    search: h("input", { placeholder: "Name or roll" }), branch: h("input", { placeholder: "Branch" }),
    status: h("select", {}, [["", "All"], ["present", "Present"], ["absent", "Absent"], ["leave", "On leave"], ["pending", "Not yet"], ["holiday", "Holiday"]].map(([v, l]) => h("option", { value: v, text: l }))) };
  const body = h("div"), info = h("div", { class: "small mut" }), csv = h("a", { class: "btn ghost", text: "Download CSV", download: "" });
  let students = [];
  const query = () => new URLSearchParams({ from: f.from.value, to: f.to.value, search: f.search.value, branch: f.branch.value, status: f.status.value }).toString();
  async function load() {
    const d = await api("/api/attendance?" + query());
    csv.href = "/api/attendance/export.csv?" + query();
    info.textContent = `${d.rows.length} row(s) from ${niceDate(d.start)} to ${niceDate(d.end)}`;
    body.replaceChildren(d.rows.length ? h("div", { class: "tablewrap" }, h("table", {}, h("tr", {}, ["Date", "Student", "Branch", "Entry", "Exit", "Time in", "Status", ""].map(x => h("th", { text: x }))),
      d.rows.map(r => h("tr", {}, h("td", { class: "nowrap", text: niceDate(r.date) }), h("td", {}, h("div", { text: r.name }), h("div", { class: "small mut", text: r.roll })), h("td", { text: r.branch || "-" }),
        h("td", { text: t12(r.entry) }), h("td", { text: t12(r.exit) }), h("td", { text: r.duration || "-" }), h("td", {}, statusBadge(r.status)),
        h("td", { class: "act" }, edit ? [h("button", { class: "btn ghost sm", text: "Edit", onclick: () => manual(r) }),
          r.entry ? h("button", { class: "btn danger sm", text: "Remove", onclick: async () => { if (await confirmBox("Remove attendance", `Remove ${r.name}'s record for ${niceDate(r.date)}?`, "Remove")) { await api(`/api/attendance/${r.student_id}/${r.date}`, { method: "DELETE" }); load().catch(fail); } } }) : null] : null))))) : h("div", { class: "empty", text: "No rows for these filters." }));
  }
  function manual(r) {
    const sel = h("select", {}, students.map(s => h("option", { value: s.id, text: s.name + (s.roll ? " (" + s.roll + ")" : "") })));
    if (r) sel.value = r.student_id;
    const dt = h("input", { type: "date", value: r ? r.date : today() }), en = h("input", { type: "time", value: r && r.entry ? r.entry.slice(0, 5) : "09:00" }), ex = h("input", { type: "time", value: r && r.exit ? r.exit.slice(0, 5) : "" });
    openDialog("Add or correct attendance", h("div", { class: "fields" }, field("Student", sel), field("Date", dt), field("Entry time", en), field("Exit time (optional)", ex)), [
      { label: "Cancel", kind: "ghost" },
      { label: "Save", run: async () => { await api("/api/attendance/manual", { body: { student_id: +sel.value, date: dt.value, entry: en.value, exit: ex.value } }); toast("Saved", "ok"); await load(); } },
    ]);
  }
  put(root, h("div", { class: "card" }, h("div", { class: "fields" }, field("From", f.from), field("To", f.to), field("Search", f.search), field("Branch", f.branch), field("Status", f.status)),
    h("div", { class: "row", style: "margin-bottom:12px" }, h("button", { class: "btn", text: "Show", onclick: () => load().catch(fail) }), csv,
      edit ? h("button", { class: "btn ghost", text: "Add or correct attendance", onclick: () => manual(null) }) : null, h("span", { class: "right" }, info)), body));
  if (edit) students = (await api("/api/students")).students.filter(s => s.active);
  await load();
}

/* ---------------- leave ---------------- */
async function pageLeaves(root) {
  const staff = can("manage_leaves"), apply = can("apply_leave") && !!S.me.student_id;
  let filter = staff ? "pending" : "all", students = [];
  const list = h("div"), tabs = h("div", { class: "row", style: "margin-bottom:10px" });
  async function decide(l, status) { await api(`/api/leaves/${l.id}/decision`, { body: { status } }); toast(status === "approved" ? "Approved" : "Rejected", "ok"); await load(); }
  async function load() {
    const all = await api("/api/leaves");
    const rows = all.filter(l => filter === "all" || l.status === filter);
    tabs.replaceChildren(...["pending", "approved", "rejected", "all"].map(k => h("button", { class: "btn sm " + (filter === k ? "" : "ghost"), text: k[0].toUpperCase() + k.slice(1) + (k === "pending" ? ` (${all.filter(l => l.status === "pending").length})` : ""), onclick: () => { filter = k; load().catch(fail); } })));
    list.replaceChildren(rows.length ? h("div", { class: "tablewrap" }, h("table", {}, h("tr", {}, [staff ? "Student" : "", "Dates", "Reason", "Status", ""].map(x => h("th", { text: x }))),
      rows.map(l => h("tr", {}, staff ? h("td", {}, h("div", { text: l.name }), h("div", { class: "small mut", text: l.roll })) : h("td", {}),
        h("td", { class: "nowrap", text: l.from === l.to ? niceDate(l.from) : niceDate(l.from) + "  to  " + niceDate(l.to) }), h("td", { text: l.reason || "-" }),
        h("td", {}, badge(l.status, l.status === "approved" ? "ok" : l.status === "rejected" ? "bad" : "warn"), l.decided_by ? h("div", { class: "small mut", text: "by " + l.decided_by }) : null),
        h("td", { class: "act" }, staff ? [l.status !== "approved" ? h("button", { class: "btn sm", text: "Approve", onclick: () => decide(l, "approved").catch(fail) }) : null,
          l.status !== "rejected" ? h("button", { class: "btn ghost sm", text: "Reject", onclick: () => decide(l, "rejected").catch(fail) }) : null,
          h("button", { class: "btn danger sm", text: "Delete", onclick: async () => { if (await confirmBox("Delete request", "Delete this leave request?")) { await api(`/api/leaves/${l.id}`, { method: "DELETE" }); await load(); } } })]
          : l.status === "pending" ? h("button", { class: "btn danger sm", text: "Cancel request", onclick: async () => { await api(`/api/leaves/${l.id}`, { method: "DELETE" }); toast("Request cancelled"); await load(); } }) : null))))) : h("div", { class: "empty", text: "Nothing here." }));
  }
  function applyForm() {
    const a = h("input", { type: "date", value: today() }), b = h("input", { type: "date", value: today() }), why = h("input", { placeholder: "Reason (optional)", maxlength: 200 });
    return h("div", { class: "card", style: "margin-bottom:14px" }, h("h2", { style: "margin-bottom:8px", text: "Apply for leave" }), h("div", { class: "fields" }, field("From", a), field("To", b), field("Reason", why)),
      h("button", { class: "btn", text: "Send request", onclick: async () => { try { await api("/api/leaves", { body: { from: a.value, to: b.value, reason: why.value } }); toast("Request sent", "ok"); why.value = ""; await load(); } catch (e) { fail(e); } } }));
  }
  function staffAdd() {
    const sel = h("select", {}, students.map(s => h("option", { value: s.id, text: s.name + (s.roll ? " (" + s.roll + ")" : "") })));
    const a = h("input", { type: "date", value: today() }), b = h("input", { type: "date", value: today() }), why = h("input", { placeholder: "Reason" });
    const ok = h("input", { type: "checkbox", checked: true });
    openDialog("Add leave for a student", h("div", {}, h("div", { class: "fields" }, field("Student", sel), field("From", a), field("To", b), field("Reason", why)), h("label", { class: "check" }, ok, "Approve it now")), [
      { label: "Cancel", kind: "ghost" },
      { label: "Add leave", run: async () => { await api("/api/leaves", { body: { student_id: +sel.value, from: a.value, to: b.value, reason: why.value, approve: ok.checked } }); toast("Leave added", "ok"); await load(); } },
    ]);
  }
  put(root, apply ? applyForm() : null,
    h("div", { class: "note", style: "margin-bottom:14px", text: "Approved leave days are not counted in the attendance percentage at all. During approved leave the percentage stays exactly where it was: it does not go down, and it does not go up. Pending and rejected requests change nothing." }),
    h("div", { class: "card" }, h("div", { class: "row" }, h("h2", { text: staff ? "Leave requests" : "My requests" }), staff ? h("button", { class: "btn ghost sm right", text: "Add leave for a student", onclick: staffAdd }) : null), tabs, list));
  if (staff) students = (await api("/api/students")).students.filter(s => s.active);
  await load();
}

/* ---------------- users and access (admin) ---------------- */
function permBoxes(selected, disabled) {
  const boxes = {};
  const wrap = h("div", { style: "display:grid;gap:2px" });
  for (const [key, text] of Object.entries(S.info)) {
    boxes[key] = h("input", { type: "checkbox", checked: selected.includes(key), disabled });
    wrap.append(h("label", { class: "check" }, boxes[key], h("span", {}, h("b", { text: text }))));
  }
  return { el: wrap, get: () => Object.keys(boxes).filter(k => boxes[k].checked), set: list => Object.keys(boxes).forEach(k => { boxes[k].checked = list.includes(k); }), lock: v => Object.values(boxes).forEach(b => { b.disabled = v; }) };
}
const SHORT = { take_attendance: "Live camera", self_checkin: "Own check-in", view_own: "Own attendance", apply_leave: "Apply for leave", view_reports: "All reports", edit_attendance: "Edit attendance", manage_leaves: "Approve leave", manage_students: "Manage students", manage_settings: "Settings" };
const slug = n => n.toLowerCase().replace(/[^a-z0-9]+/g, ".").replace(/^\.+|\.+$/g, "").slice(0, 24);

async function pageUsers(root) {
  let users = [], students = [];
  const body = h("div");
  async function load() {
    [users, students] = await Promise.all([api("/api/users"), api("/api/students").then(r => r.students).catch(() => [])]);
    draw();
  }
  function addDialog() {
    let touched = false;
    const f = { name: h("input", { placeholder: "Full name", oninput: e => { if (!touched) f.uid.value = slug(e.target.value); } }),
      uid: h("input", { placeholder: "For example: sharma.m", autocapitalize: "none", spellcheck: "false", oninput: () => { touched = true; } }),
      role: h("select", {}, [["teacher", "Teacher / staff"], ["student", "Student"], ["admin", "Admin (can do everything)"]].map(([v, l]) => h("option", { value: v, text: l }))),
      pwd: h("input", { placeholder: "Leave empty to make one for me", autocomplete: "new-password" }),
      student: h("select", {}, h("option", { value: "", text: "Choose a student" }), students.filter(s => !s.login).map(s => h("option", { value: s.id, text: s.name + (s.roll ? " (" + s.roll + ")" : "") }))) };
    const studentField = field("Student this login belongs to", f.student);
    const perms = permBoxes(S.presets.teacher || [], false);
    const sync = () => { const r = f.role.value; perms.set(S.presets[r] || []); perms.lock(r === "admin"); studentField.hidden = r !== "student"; };
    f.role.onchange = sync; sync();
    const gen = h("button", { class: "btn ghost sm", type: "button", text: "Make a password", onclick: () => { const c = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"; f.pwd.value = Array.from(crypto.getRandomValues(new Uint8Array(10)), x => c[x % c.length]).join(""); } });
    openDialog("Add a user", h("div", {}, h("div", { class: "fields" }, field("Name", f.name), field("User ID", f.uid, "Letters, numbers, dot, dash, underscore"), field("Role", f.role), field("Password", f.pwd)), h("div", { class: "row", style: "margin:-4px 0 10px" }, gen), studentField,
      h("h3", { style: "margin:6px 0 8px", text: "What this person can use" }), perms.el), [
      { label: "Cancel", kind: "ghost" },
      { label: "Add user", run: async () => {
        const r = await api("/api/users", { body: { name: f.name.value, user_id: f.uid.value, role: f.role.value, password: f.pwd.value || undefined, student_id: f.student.value ? +f.student.value : undefined, perms: perms.get() } });
        await load(); showCredentials(f.name.value, r.user_id, r.password, "User added");
      } },
    ], { wide: true });
  }
  function editDialog(u) {
    const name = h("input", { value: u.name }), act = h("input", { type: "checkbox", checked: u.active });
    const role = h("select", {}, [["teacher", "Teacher / staff"], ["student", "Student"], ["admin", "Admin"]].map(([v, l]) => h("option", { value: v, text: l })));
    role.value = u.role;
    const perms = permBoxes(u.role === "admin" ? Object.keys(S.info) : u.perms, u.role === "admin");
    role.onchange = () => { perms.set(S.presets[role.value] || []); perms.lock(role.value === "admin"); };
    openDialog("Access for " + u.user_id, h("div", {}, h("div", { class: "fields" }, field("Name", name), field("Role", role)), h("label", { class: "check", style: "margin-bottom:10px" }, act, "Account is active (turn off to block this person from signing in)"),
      h("h3", { style: "margin:6px 0 8px", text: "What this person can use" }), perms.el), [
      { label: "Cancel", kind: "ghost" },
      { label: "Save", run: async () => { await api(`/api/users/${encodeURIComponent(u.user_id)}`, { method: "PUT", body: { name: name.value, role: role.value, active: act.checked, perms: perms.get() } }); await load(); toast("Saved", "ok"); } },
    ], { wide: true });
  }
  async function reset(u) {
    if (!(await confirmBox("Reset password", `Make a new password for ${u.name} (${u.user_id})? The old password stops working and they are signed out.`, "Reset"))) return;
    const r = await api(`/api/users/${encodeURIComponent(u.user_id)}/reset-password`, { body: {} });
    showCredentials(u.name, r.user_id, r.password, "New password");
  }
  async function remove(u) {
    if (!(await confirmBox("Delete user", `Delete the login ${u.user_id} (${u.name})? They can no longer sign in. A student's attendance records are kept.`))) return;
    await api(`/api/users/${encodeURIComponent(u.user_id)}`, { method: "DELETE" }); await load(); toast("User deleted");
  }
  function draw() {
    body.replaceChildren(h("div", { class: "tablewrap" }, h("table", {}, h("tr", {}, ["Person", "User ID", "Role", "Can use", "Status", ""].map(x => h("th", { text: x }))),
      users.map(u => h("tr", {},
        h("td", {}, h("div", { class: "person" }, h("span", { class: "av", text: initials(u.name) }), h("div", {}, h("div", { text: u.name }), u.student_name ? h("div", { class: "small mut", text: "Student " + (u.roll || "") }) : null))),
        h("td", {}, h("code", { text: u.user_id })), h("td", {}, badge(u.role, u.role === "admin" ? "acc" : "")),
        h("td", {}, u.role === "admin" ? badge("Everything", "acc") : h("div", { class: "chips" }, u.perms.length ? u.perms.map(p => badge(SHORT[p] || p, "")) : h("span", { class: "mut", text: "nothing yet" }))),
        h("td", {}, badge(u.active ? "Active" : "Blocked", u.active ? "ok" : "bad")),
        h("td", { class: "act" }, h("button", { class: "btn ghost sm", text: "Access", onclick: () => editDialog(u) }), h("button", { class: "btn ghost sm", text: "Reset password", onclick: () => reset(u).catch(fail) }),
          u.user_id === S.me.user_id ? null : h("button", { class: "btn danger sm", text: "Delete", onclick: () => remove(u).catch(fail) })))))));
  }
  put(root, h("div", { class: "card" }, h("div", { class: "row", style: "margin-bottom:12px" }, h("h2", { text: "Everyone who can sign in" }), h("button", { class: "btn right", text: "Add user", onclick: addDialog })),
    h("p", { class: "small mut", style: "margin-bottom:10px", text: "Each person has a User ID and a password. Passwords are stored scrambled, so they cannot be read back. When you add someone, or reset a password, the new password is shown once for you to hand over." }), body));
  await load();
}

/* ---------------- settings ---------------- */
async function pageSettings(root) {
  const s = await api("/api/settings");
  const f = { org: h("input", { value: s.org_name }), limit: h("input", { type: "number", min: 1, value: s.student_limit }), gap: h("input", { type: "number", min: 1, value: s.min_gap_minutes }),
    tz: h("input", { value: s.timezone, list: "tzs" }), term: h("input", { type: "date", value: s.term_start }), thr: h("input", { type: "range", min: 40, max: 100, value: s.threshold }) };
  const thrLabel = h("b", { text: s.threshold }); f.thr.oninput = () => { thrLabel.textContent = f.thr.value; };
  const days = DAYS.map((d, i) => h("input", { type: "checkbox", checked: s.working_days.includes(i) }));
  const hol = h("div"), hd = h("input", { type: "date" }), hl = h("input", { placeholder: "Name, for example Diwali" });
  async function drawHolidays() {
    const cur = (await api("/api/settings")).holidays;
    hol.replaceChildren(cur.length ? h("div", { class: "chips" }, cur.map(x => h("span", { class: "badge" }, niceDate(x.date) + (x.label ? " - " + x.label : "") + "  ", h("a", { href: "#", text: "remove", onclick: async e => { e.preventDefault(); await api("/api/holidays/" + x.date, { method: "DELETE" }); drawHolidays().catch(fail); } })))) : h("span", { class: "mut small", text: "No holidays added." }));
  }
  const restore = h("input", { type: "file", accept: ".db" });
  const cloudBox = h("div");
  async function drawCloud() {
    if (!can("backup")) return;
    const c = await api("/api/cloud");
    const when = c.last_sync ? new Date(c.last_sync * 1000).toLocaleString() : "not yet";
    if (!c.configured) cloudBox.replaceChildren(h("p", { class: "small mut", text: "Not set up. Free web hosts erase their disk when they restart, which would delete everything. To keep an encrypted copy safe in Upstash, add UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN and BACKUP_KEY on the server (see the README). If you run this on your own computer you do not need it." }));
    else if (c.state === "ok") cloudBox.replaceChildren(
      h("div", { class: "row", style: "margin-bottom:8px" }, badge("On", "ok"), h("span", { class: "small", text: "Last saved: " + when }), c.pending ? badge("changes waiting", "warn") : null),
      h("p", { class: "small mut", style: "margin-bottom:10px", text: "Everything is encrypted before it leaves this server. Changes are saved about a minute after the last one. If this server restarts with an empty disk, it fills itself from the cloud copy." }),
      c.error ? h("p", { class: "err", text: "Last problem: " + c.error }) : null,
      h("button", { class: "btn ghost", text: "Save now", onclick: async ev => { ev.target.disabled = true; try { await api("/api/cloud/sync", { body: {} }); toast("Saved to the cloud", "ok"); } catch (e) { fail(e); } await drawCloud(); } }));
    else cloudBox.replaceChildren(h("div", { class: "row", style: "margin-bottom:8px" }, badge(c.state === "off" ? "Off" : "Problem", "bad")), h("p", { class: "err", text: c.error || "The cloud copy is starting." }));
  }
  put(root, 
    h("div", { class: "card" }, h("h2", { style: "margin-bottom:10px", text: "General" }), h("datalist", { id: "tzs" }, ["Asia/Kolkata", "Asia/Dubai", "Asia/Dhaka", "Asia/Kathmandu", "Asia/Karachi", "Asia/Singapore", "Europe/London", "America/New_York", "UTC"].map(z => h("option", { value: z }))),
      h("div", { class: "fields" }, field("Organisation name", f.org), field(`Student limit (now ${s.student_count})`, f.limit, "No more students can be added after this number."), field("Time zone", f.tz, "All times are shown in this zone."), field("Count attendance from", f.term, "Optional. Leave empty to count from each student's joining date."))),
    h("div", { class: "card" }, h("h2", { style: "margin-bottom:10px", text: "Attendance rules" }),
      field("Working days", h("div", { class: "row" }, DAYS.map((d, i) => h("label", { class: "check", style: "margin:0" }, days[i], d)))),
      h("div", { class: "fields" }, field("Exit is recorded after (minutes)", f.gap, "In Auto mode, a later sighting this long after entry sets the exit time."),
        field("Face match strictness", h("div", {}, f.thr, h("div", { class: "small mut" }, "Distance limit ", thrLabel, ". Lower is stricter (fewer wrong matches, more misses).")))),
      h("div", { class: "row", style: "margin-top:6px" }, h("button", { class: "btn", text: "Save settings", onclick: async () => {
        try {
          await api("/api/settings", { method: "PUT", body: { org_name: f.org.value, student_limit: +f.limit.value, min_gap_minutes: +f.gap.value, timezone: f.tz.value, term_start: f.term.value, threshold: +f.thr.value, working_days: days.map((c, i) => c.checked ? i : -1).filter(i => i >= 0) } });
          await loadMe(); toast("Settings saved", "ok"); render();
        } catch (e) { fail(e); } } }))),
    h("div", { class: "card" }, h("h2", { style: "margin-bottom:10px", text: "Holidays" }), hol, h("div", { class: "row", style: "margin-top:12px" }, hd, hl, h("button", { class: "btn ghost", text: "Add holiday", onclick: async () => { try { await api("/api/holidays", { body: { date: hd.value, label: hl.value } }); hl.value = ""; await drawHolidays(); } catch (e) { fail(e); } } }))),
    can("backup") ? h("div", { class: "card" }, h("h2", { style: "margin-bottom:6px", text: "Cloud copy (Upstash)" }), cloudBox) : null,
    can("backup") ? h("div", { class: "card" }, h("h2", { style: "margin-bottom:6px", text: "Backup file" }),
      h("p", { class: "small mut", style: "margin-bottom:10px", text: "The backup holds all students, face photos, attendance, leave and logins. Free web hosts erase their disk when they restart, so download a backup regularly." }),
      h("div", { class: "row" }, h("a", { class: "btn ghost", href: "/api/backup", download: "", text: "Download backup" }), restore,
        h("button", { class: "btn danger", text: "Restore from backup", onclick: async () => {
          if (!restore.files[0]) return toast("Choose a backup file first.", "bad");
          if (!(await confirmBox("Restore backup", "This replaces everything with the contents of the file, including logins. You will have to sign in again.", "Restore"))) return;
          try { const fd = new FormData(); fd.append("file", restore.files[0]); const r = await api("/api/restore", { form: fd, timeout: 120000 }); toast(r.msg, "ok"); S.me = null; S.csrf = null; await loadMe(); render(); } catch (e) { fail(e); } } }))) : null);
  await drawHolidays();
  await drawCloud();
}

/* ---------------- start ---------------- */
(async function boot() {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  try { await loadMe(); }
  catch (e) {
    $("#app").append(h("div", { class: "auth" }, h("div", { class: "card" }, h("h1", { text: "Cannot reach the server" }), h("p", { class: "mut", style: "margin:8px 0 14px", text: e.message }), h("button", { class: "btn", text: "Try again", onclick: () => location.reload() }))));
    return;
  }
  render();
})();
