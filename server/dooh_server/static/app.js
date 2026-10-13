import { barChart, fmt, hideTip, lineChart } from "./charts.js";

// ---------------------------------------------------------------------------
// helpers

const $app = document.getElementById("app");
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const ROLE_RANK = { viewer: 1, editor: 2, admin: 3 };

const state = {
  token: localStorage.getItem("dooh_token"),
  me: null,
  accounts: [],
  filters: { account_id: "", screen_id: "", days: 7, start: "", end: "" },
};

async function api(path, { method = "GET", body, form, raw } = {}) {
  const headers = {};
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(path, { method, headers, body: payload });
  if (res.status === 401 && path !== "/api/v1/auth/login") {
    logout();
    throw new Error("Session expired");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : j.detail?.map?.((d) => d.msg).join(", ") || msg;
    } catch {}
    throw new Error(msg);
  }
  if (raw) return res;
  return res.status === 204 ? null : res.json();
}

function logout() {
  localStorage.removeItem("dooh_token");
  state.token = null;
  state.me = null;
  render();
}

function can(accountId, role) {
  const acc = state.accounts.find((a) => a.id === Number(accountId));
  return acc && ROLE_RANK[acc.role] >= ROLE_RANK[role];
}

function accountOptions(selected, { all = false, minRole = "viewer" } = {}) {
  const opts = state.accounts
    .filter((a) => ROLE_RANK[a.role] >= ROLE_RANK[minRole])
    .map((a) => `<option value="${a.id}" ${String(a.id) === String(selected) ? "selected" : ""}>${esc(a.path)}</option>`);
  if (all) opts.unshift(`<option value="">All accounts</option>`);
  return opts.join("");
}

function statusBadge(s) {
  if (!s.last_seen_at) return `<span class="status never"><span class="dot"></span>Never connected</span>`;
  return s.online
    ? `<span class="status online"><span class="dot"></span>Online</span>`
    : `<span class="status offline"><span class="dot"></span>Offline</span>`;
}

function ago(iso) {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso + "Z").getTime()) / 1000;
  if (s < 90) return `${Math.round(s)} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

function localDate(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function rangeParams(override = {}) {
  const f = { ...state.filters, ...override };
  const q = new URLSearchParams({ tz });
  if (f.account_id) q.set("account_id", f.account_id);
  if (f.screen_id) q.append("screen_id", f.screen_id);
  if (f.days) {
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - (f.days - 1));
    q.set("start", localDate(start));
    q.set("end", localDate(end));
  } else {
    if (f.start) q.set("start", f.start);
    if (f.end) q.set("end", f.end);
  }
  return q;
}

// ---------------------------------------------------------------------------
// shell & routing

let liveTimer;

const ROUTES = {
  dashboard: { label: "Dashboard", view: viewDashboard },
  screens: { label: "Screens", view: viewScreens },
  brands: { label: "Brands", view: viewBrands },
  accounts: { label: "Accounts & users", view: viewAccounts },
};

function currentRoute() {
  const [name, arg] = location.hash.replace(/^#\/?/, "").split("/");
  return { name: ROUTES[name] || name === "screen" ? name : "dashboard", arg };
}

window.addEventListener("hashchange", () => render());

async function render() {
  hideTip();
  clearInterval(liveTimer);
  if (!state.token) return viewLogin();
  if (!state.me) {
    try {
      [state.me, state.accounts] = await Promise.all([api("/api/v1/auth/me"), api("/api/v1/accounts")]);
    } catch (e) {
      return viewLogin(e.message);
    }
  }
  const route = currentRoute();
  const nav = Object.entries(ROUTES)
    .map(([k, r]) => `<a href="#/${k}" class="${route.name === k || (k === "screens" && route.name === "screen") ? "active" : ""}">${r.label}</a>`)
    .join("");
  $app.innerHTML = `
    <div class="shell">
      <aside class="sidebar">
        <div class="brand-mark">DOOH Analytics</div>
        <nav class="nav">${nav}</nav>
        <div class="spacer"></div>
        <div class="userbox">
          <div>${esc(state.me.name || state.me.email)}</div>
          <div class="muted">${state.me.is_superadmin ? "Super-admin" : esc(state.me.email)}</div>
          <p><a href="#" id="logout">Sign out</a></p>
        </div>
      </aside>
      <main id="main"></main>
    </div>`;
  document.getElementById("logout").onclick = (e) => {
    e.preventDefault();
    logout();
  };
  const main = document.getElementById("main");
  try {
    if (route.name === "screen") await viewScreenDetail(main, Number(route.arg));
    else await ROUTES[route.name].view(main);
  } catch (e) {
    main.innerHTML = `<div class="card error">${esc(e.message)}</div>`;
  }
}

function viewLogin(error) {
  $app.innerHTML = `
    <div class="login card">
      <h1>DOOH Audience Analytics</h1>
      <p class="muted">Sign in to view your screens.</p>
      <form id="login">
        <label class="field">Email<input name="email" type="email" autocomplete="username" required></label>
        <label class="field">Password<input name="password" type="password" autocomplete="current-password" required></label>
        <button class="primary" type="submit">Sign in</button>
        <div class="error" id="login-error">${esc(error || "")}</div>
      </form>
    </div>`;
  document.getElementById("login").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const r = await api("/api/v1/auth/login", { method: "POST", body: { email: fd.get("email"), password: fd.get("password") } });
      state.token = r.access_token;
      localStorage.setItem("dooh_token", r.access_token);
      render();
    } catch (err) {
      document.getElementById("login-error").textContent = err.message;
    }
  };
}

// ---------------------------------------------------------------------------
// dashboard

function filtersBar(screens, { showAccount = true, showScreen = true } = {}) {
  const f = state.filters;
  const presets = [1, 7, 30, 90]
    .map((d) => `<button data-days="${d}" class="${f.days === d ? "on" : ""}">${d === 1 ? "Today" : `${d} days`}</button>`)
    .join("");
  return `
    <div class="filters">
      ${showAccount ? `<select id="f-account" aria-label="Account">${accountOptions(f.account_id, { all: true })}</select>` : ""}
      ${showScreen ? `<select id="f-screen" aria-label="Screen"><option value="">All screens</option>${screens
        .map((s) => `<option value="${s.id}" ${String(s.id) === String(f.screen_id) ? "selected" : ""}>${esc(s.name)}</option>`)
        .join("")}</select>` : ""}
      <div class="seg" id="f-days">${presets}</div>
      <input type="date" id="f-start" value="${f.days ? "" : esc(f.start)}" aria-label="Start date">
      <input type="date" id="f-end" value="${f.days ? "" : esc(f.end)}" aria-label="End date">
      <a id="f-export" class="small" href="#">Export CSV</a>
    </div>`;
}

function bindFilters(root, rerender, override = {}) {
  const f = state.filters;
  root.querySelector("#f-account")?.addEventListener("change", (e) => {
    f.account_id = e.target.value;
    f.screen_id = "";
    rerender();
  });
  root.querySelector("#f-screen")?.addEventListener("change", (e) => {
    f.screen_id = e.target.value;
    rerender();
  });
  root.querySelectorAll("#f-days button").forEach((b) =>
    b.addEventListener("click", () => {
      f.days = Number(b.dataset.days);
      rerender();
    })
  );
  for (const id of ["f-start", "f-end"]) {
    root.querySelector("#" + id).addEventListener("change", () => {
      const s = root.querySelector("#f-start").value;
      const e = root.querySelector("#f-end").value;
      if (s && e) {
        Object.assign(f, { days: 0, start: s, end: e });
        rerender();
      }
    });
  }
  root.querySelector("#f-export").addEventListener("click", async (e) => {
    e.preventDefault();
    const res = await api(`/api/v1/analytics/export.csv?${rangeParams(override)}`, { raw: true });
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: "audience-events.csv" });
    a.click();
    URL.revokeObjectURL(url);
  });
}

function kpiTiles(t) {
  const tiles = [
    ["Impressions", fmt.int(t.impressions), "people passing the screen"],
    ["Viewers", fmt.int(t.viewers), `${fmt.pct(t.view_rate)} looked at the screen`],
    ["Avg. attention", fmt.sec(t.avg_attention_seconds), "per viewer"],
    ["Total attention", fmt.sec(t.total_attention_seconds), "all viewers combined"],
    ["Avg. dwell", fmt.sec(t.avg_dwell_seconds), "time in camera view"],
    ["Branded bags", fmt.int(t.branded_bag_carriers), `${fmt.pct(t.branded_bag_rate)} of people`],
  ];
  return `<div class="kpis">${tiles
    .map(([l, v, s]) => `<div class="card kpi"><div class="label">${l}</div><div class="value">${v}</div><div class="sub">${s}</div></div>`)
    .join("")}</div>`;
}

function analyticsBody(data) {
  const brandRows = data.brands
    .map(
      (b) => `<tr><td>${esc(b.brand)}</td><td class="num">${fmt.int(b.carriers)}</td><td class="num">${b.view_rate == null ? "–" : fmt.pct(b.view_rate)}</td><td class="num">${b.avg_attention_seconds == null ? "–" : fmt.sec(b.avg_attention_seconds)}</td></tr>`
    )
    .join("");
  return `
    ${kpiTiles(data.totals)}
    <div class="card" style="margin-bottom:16px">
      <h2>Audience over time</h2>
      <div class="legend">
        <span><span class="swatch" style="background:var(--series-1)"></span>Impressions</span>
        <span><span class="swatch" style="background:var(--series-2)"></span>Viewers</span>
      </div>
      <div id="c-time"></div>
    </div>
    <div class="grid cols-2" style="margin-bottom:16px">
      <div class="card"><h2>Viewers by hour of day</h2><div id="c-hour"></div></div>
      <div class="card"><h2>Viewers by age</h2><div id="c-age"></div>
        <h2 style="margin-top:16px">Viewers by gender</h2><div id="c-gender"></div>
        <p class="muted small">Estimated from face analysis; "unknown" when no face was visible.</p></div>
    </div>
    <div class="card" style="margin-bottom:16px"><h2>Branded shopping bags</h2>
      <div class="grid cols-2"><div id="c-brands"></div>
        <div class="table-wrap"><table><thead><tr><th>Brand</th><th class="num">Carriers</th><th class="num">View rate</th><th class="num">Avg. attention</th></tr></thead>
        <tbody>${brandRows || `<tr><td colspan="4" class="muted">No bags detected in this period.</td></tr>`}</tbody></table></div>
      </div>
    </div>`;
}

function drawCharts(root, data) {
  const ts = data.timeseries;
  const hourly = ts.granularity === "hour";
  const xLabel = (t, long) => {
    const d = new Date(hourly ? t : t + "T00:00");
    if (hourly) return long ? d.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : d.toLocaleTimeString([], { hour: "2-digit" });
    return d.toLocaleDateString([], long ? { weekday: "short", month: "short", day: "numeric" } : { month: "short", day: "numeric" });
  };
  const css = getComputedStyle(document.documentElement);
  const c1 = css.getPropertyValue("--series-1").trim();
  const c2 = css.getPropertyValue("--series-2").trim();
  lineChart(root.querySelector("#c-time"), {
    points: ts.points,
    series: [
      { key: "impressions", label: "Impressions", color: c1 },
      { key: "viewers", label: "Viewers", color: c2 },
    ],
    xLabel,
    ariaLabel: "Impressions and viewers over time",
  });
  barChart(root.querySelector("#c-hour"), {
    color: c1,
    ariaLabel: "Viewers by hour of day",
    data: ts.hour_of_day.map((h) => ({
      label: String(h.hour).padStart(2, "0"),
      value: h.viewers,
      tip: `<div class="tip-title">${String(h.hour).padStart(2, "0")}:00–${String(h.hour).padStart(2, "0")}:59</div><div class="tip-row">Viewers<b>${fmt.int(h.viewers)}</b></div><div class="tip-row">Impressions<b>${fmt.int(h.impressions)}</b></div>`,
    })),
  });
  barChart(root.querySelector("#c-age"), {
    horizontal: true,
    color: c1,
    ariaLabel: "Viewers by age group",
    data: data.demographics.age.map((a) => ({
      label: a.bucket,
      value: a.viewers,
      tip: `<div class="tip-title">Age ${esc(a.bucket)}</div><div class="tip-row">Viewers<b>${fmt.int(a.viewers)}</b></div><div class="tip-row">Impressions<b>${fmt.int(a.impressions)}</b></div><div class="tip-row">Avg. attention<b>${fmt.sec(a.avg_attention_seconds)}</b></div>`,
    })),
  });
  const g = data.demographics.gender;
  barChart(root.querySelector("#c-gender"), {
    horizontal: true,
    color: c1,
    ariaLabel: "Viewers by gender",
    data: ["female", "male", "unknown"].map((k) => ({
      label: k[0].toUpperCase() + k.slice(1),
      value: g[k]?.viewers || 0,
      tip: `<div class="tip-title">${k}</div><div class="tip-row">Viewers<b>${fmt.int(g[k]?.viewers || 0)}</b></div><div class="tip-row">Impressions<b>${fmt.int(g[k]?.impressions || 0)}</b></div><div class="tip-row">Avg. attention<b>${fmt.sec(g[k]?.avg_attention_seconds || 0)}</b></div>`,
    })),
  });
  barChart(root.querySelector("#c-brands"), {
    horizontal: true,
    color: c1,
    ariaLabel: "People carrying branded bags, by brand",
    data: data.brands.slice(0, 8).map((b) => ({ label: b.brand, value: b.carriers })),
  });
}

async function viewDashboard(main) {
  const draw = async () => {
    const screens = await api(`/api/v1/screens${state.filters.account_id ? `?account_id=${state.filters.account_id}` : ""}`);
    if (state.filters.screen_id && !screens.some((s) => String(s.id) === String(state.filters.screen_id))) state.filters.screen_id = "";
    main.innerHTML = `
      <div class="page-head"><h1>Audience dashboard</h1><span class="muted small">Times shown in ${esc(tz)}</span></div>
      ${filtersBar(screens)}
      <div id="body"><div class="muted">Loading…</div></div>`;
    bindFilters(main, draw);
    const data = await api(`/api/v1/analytics/summary?${rangeParams()}`);
    const body = main.querySelector("#body");
    const rows = data.screens
      .map((s) => {
        const meta = screens.find((x) => x.id === s.screen_id);
        return `<tr class="clickable" data-id="${s.screen_id}"><td>${esc(s.name)}<div class="muted small">${esc(s.location)}</div></td>
          <td>${meta ? statusBadge(meta) : ""}</td><td class="num">${fmt.int(s.impressions)}</td><td class="num">${fmt.int(s.viewers)}</td>
          <td class="num">${fmt.pct(s.view_rate)}</td><td class="num">${fmt.sec(s.avg_attention_seconds)}</td><td class="num">${fmt.int(s.branded_bag_carriers)}</td></tr>`;
      })
      .join("");
    body.innerHTML =
      analyticsBody(data) +
      `<div class="card"><h2>Screens</h2><div class="table-wrap"><table>
        <thead><tr><th>Screen</th><th>Status</th><th class="num">Impressions</th><th class="num">Viewers</th><th class="num">View rate</th><th class="num">Avg. attention</th><th class="num">Branded bags</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="7" class="muted">No screens yet.</td></tr>`}</tbody></table></div></div>`;
    body.querySelectorAll("tr.clickable").forEach((tr) => (tr.onclick = () => (location.hash = `#/screen/${tr.dataset.id}`)));
    drawCharts(body, data);
  };
  await draw();
}

// ---------------------------------------------------------------------------
// single screen dashboard (live status + analytics)

async function viewScreenDetail(main, id) {
  const screen = await api(`/api/v1/screens/${id}`);
  const only = { screen_id: String(id), account_id: "" };
  const draw = async () => {
    main.innerHTML = `
      <div class="page-head"><div><h1>${esc(screen.name)}</h1><div class="muted">${esc(screen.location)}</div></div>
        <a href="#/screens">All screens</a></div>
      <div class="card" style="margin-bottom:16px"><h2>Right now</h2><div id="live"></div></div>
      ${filtersBar([], { showAccount: false, showScreen: false })}
      <div id="body"><div class="muted">Loading…</div></div>`;
    bindFilters(main, draw, only);
    const data = await api(`/api/v1/analytics/summary?${rangeParams(only)}`);
    const body = main.querySelector("#body");
    body.innerHTML = analyticsBody(data);
    drawCharts(body, data);
  };
  const live = async () => {
    const box = document.getElementById("live");
    if (!box) return clearInterval(liveTimer);
    const s = await api(`/api/v1/screens/${id}`);
    // A stale status from an offline screen would be misleading, so blank it.
    const st = s.online ? s.last_status || {} : {};
    const tile = (l, v) => `<div><div class="muted small">${l}</div><div style="font-size:22px;font-weight:600">${v}</div></div>`;
    box.innerHTML = `<div class="live-grid">
      ${tile("Status", statusBadge(s))}
      ${tile("People in view", fmt.int(st.people_now))}
      ${tile("Looking now", fmt.int(st.viewers_now))}
      ${tile("Camera", st.mode === "simulator" ? "Simulated" : st.camera_ok === false ? "Fault" : st.camera_ok ? "OK" : "–")}
      ${tile("Processing", st.fps != null ? `${st.fps} fps` : "–")}
      ${tile("Upload queue", fmt.int(st.queue_depth))}
      ${tile("Last heartbeat", ago(s.last_seen_at))}
      ${tile("Agent", esc(s.agent_version || "–"))}
    </div>`;
  };
  await draw();
  await live();
  liveTimer = setInterval(() => live().catch(() => {}), 10000);
}

// ---------------------------------------------------------------------------
// screens management

function installSnippet(key) {
  return `# /etc/dooh-edge/edge.toml on the thin client
[server]
url = "${location.origin}"
device_key = "${key}"`;
}

async function viewScreens(main) {
  const screens = await api("/api/v1/screens");
  const editable = state.accounts.filter((a) => ROLE_RANK[a.role] >= 2);
  const accName = (id) => state.accounts.find((a) => a.id === id)?.path || id;
  const rows = screens
    .map(
      (s) => `<tr data-id="${s.id}">
      <td><a href="#/screen/${s.id}">${esc(s.name)}</a><div class="muted small">${esc(s.location)}</div></td>
      <td class="small">${esc(accName(s.account_id))}</td>
      <td>${statusBadge(s)}<div class="muted small">${ago(s.last_seen_at)}</div></td>
      <td class="small"><code>${esc(s.key_prefix)}</code>${s.is_active ? "" : ` <span class="tag">disabled</span>`}</td>
      <td><div class="row-actions">
        ${can(s.account_id, "editor") ? `<button data-act="toggle">${s.is_active ? "Disable" : "Enable"}</button><button data-act="rotate">New key</button>` : ""}
        ${can(s.account_id, "admin") ? `<button class="danger" data-act="delete">Delete</button>` : ""}
      </div></td></tr>`
    )
    .join("");
  main.innerHTML = `
    <div class="page-head"><h1>Screens</h1></div>
    ${editable.length ? `<div class="card" style="margin-bottom:16px"><h2>Add a screen</h2>
      <form class="inline" id="new-screen">
        <label class="field">Account<select name="account_id">${accountOptions("", { minRole: "editor" })}</select></label>
        <label class="field">Name<input name="name" required placeholder="Atrium portrait 1"></label>
        <label class="field">Location<input name="location" placeholder="Ground floor"></label>
        <label class="field">Timezone<input name="timezone" value="${esc(tz)}"></label>
        <button class="primary">Create</button>
      </form><div id="key-out"></div></div>` : ""}
    <div class="card"><div class="table-wrap"><table>
      <thead><tr><th>Screen</th><th>Account</th><th>Status</th><th>Key</th><th></th></tr></thead>
      <tbody>${rows || `<tr><td colspan="5" class="muted">No screens yet.</td></tr>`}</tbody></table></div></div>`;

  const showKey = (s) => {
    main.querySelector("#key-out") || main.insertAdjacentHTML("afterbegin", `<div id="key-out"></div>`);
    main.querySelector("#key-out").innerHTML = `<div class="notice"><b>Device key for “${esc(s.name)}”.</b>
      Copy it now – it is not shown again.<pre>${esc(installSnippet(s.device_key))}</pre></div>`;
  };
  main.querySelector("#new-screen")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(e.target));
    try {
      const s = await api("/api/v1/screens", { method: "POST", body: { ...fd, account_id: Number(fd.account_id) } });
      await viewScreens(main);
      showKey(s);
      state.accounts = await api("/api/v1/accounts");
    } catch (err) {
      alert(err.message);
    }
  });
  main.querySelectorAll("button[data-act]").forEach((b) =>
    b.addEventListener("click", async () => {
      const id = b.closest("tr").dataset.id;
      const s = screens.find((x) => String(x.id) === id);
      try {
        if (b.dataset.act === "toggle") {
          await api(`/api/v1/screens/${id}`, { method: "PATCH", body: { is_active: !s.is_active } });
          await viewScreens(main);
        } else if (b.dataset.act === "rotate") {
          if (!confirm(`Issue a new key for “${s.name}”? The old key stops working immediately.`)) return;
          const r = await api(`/api/v1/screens/${id}/rotate-key`, { method: "POST" });
          await viewScreens(main);
          showKey(r);
        } else if (b.dataset.act === "delete") {
          if (!confirm(`Delete “${s.name}” and all of its audience data?`)) return;
          await api(`/api/v1/screens/${id}`, { method: "DELETE" });
          await viewScreens(main);
        }
      } catch (err) {
        alert(err.message);
      }
    })
  );
}

// ---------------------------------------------------------------------------
// brands

async function viewBrands(main, accountId) {
  accountId = accountId || state.filters.account_id || state.accounts[0]?.id;
  if (!accountId) {
    main.innerHTML = `<div class="card muted">You have no accounts yet.</div>`;
    return;
  }
  const brands = await api(`/api/v1/accounts/${accountId}/brands`);
  const editable = can(accountId, "editor");
  const rows = brands
    .map(
      (b) => `<tr data-id="${b.id}"><td>${esc(b.name)} ${b.inherited ? `<span class="tag">inherited</span>` : ""}</td>
      <td class="small">${b.templates.length ? b.templates.map((t) => esc(t.filename)).join(", ") : `<span class="muted">none</span>`}</td>
      <td class="small">${b.colors.length} colour range(s)</td>
      <td><div class="row-actions">${editable && !b.inherited ? `
        <label class="small"><input type="file" accept="image/png,image/jpeg,image/webp" data-act="upload" hidden><span class="tag" style="cursor:pointer">Upload logo</span></label>
        <button class="danger" data-act="delete">Delete</button>` : ""}</div></td></tr>`
    )
    .join("");
  main.innerHTML = `
    <div class="page-head"><h1>Brands</h1><select id="b-account">${accountOptions(accountId)}</select></div>
    <p class="muted">Shopping bags are matched against these logos (and optional colour ranges) on each thin client.
      Brands defined on a parent account apply to all of its sub-accounts. Upload a clear, tightly-cropped logo image.</p>
    ${editable ? `<div class="card" style="margin-bottom:16px"><form class="inline" id="new-brand">
      <label class="field">Brand name<input name="name" required></label><button class="primary">Add brand</button></form></div>` : ""}
    <div class="card"><div class="table-wrap"><table><thead><tr><th>Brand</th><th>Logo templates</th><th>Colours</th><th></th></tr></thead>
      <tbody>${rows || `<tr><td colspan="4" class="muted">No brands configured.</td></tr>`}</tbody></table></div></div>`;
  main.querySelector("#b-account").onchange = (e) => viewBrands(main, e.target.value);
  main.querySelector("#new-brand")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api(`/api/v1/accounts/${accountId}/brands`, { method: "POST", body: { name: new FormData(e.target).get("name") } });
      await viewBrands(main, accountId);
    } catch (err) {
      alert(err.message);
    }
  });
  main.querySelectorAll("[data-act]").forEach((elm) => {
    const id = elm.closest("tr").dataset.id;
    if (elm.dataset.act === "upload")
      elm.addEventListener("change", async () => {
        const form = new FormData();
        form.append("file", elm.files[0]);
        try {
          await api(`/api/v1/brands/${id}/templates`, { method: "POST", form });
          await viewBrands(main, accountId);
        } catch (err) {
          alert(err.message);
        }
      });
    else
      elm.addEventListener("click", async () => {
        if (!confirm("Delete this brand?")) return;
        await api(`/api/v1/brands/${id}`, { method: "DELETE" });
        await viewBrands(main, accountId);
      });
  });
}

// ---------------------------------------------------------------------------
// accounts & members

async function viewAccounts(main, selectedId) {
  state.accounts = await api("/api/v1/accounts");
  selectedId = Number(selectedId || state.accounts[0]?.id);
  const depth = (a) => a.path.split(" / ").length - 1;
  const rows = state.accounts
    .map(
      (a) => `<tr class="clickable" data-id="${a.id}" ${a.id === selectedId ? `style="background:var(--page)"` : ""}>
      <td style="padding-left:${10 + depth(a) * 18}px">${esc(a.name)}</td><td><span class="tag">${a.role}</span></td><td class="num">${a.screen_count}</td></tr>`
    )
    .join("");
  const sel = state.accounts.find((a) => a.id === selectedId);
  main.innerHTML = `
    <div class="page-head"><h1>Accounts & users</h1></div>
    <div class="grid cols-2">
      <div class="card"><h2>Accounts</h2>
        <p class="muted small">Users see the account they are added to and every sub-account below it – never parents or siblings.</p>
        <div class="table-wrap"><table><thead><tr><th>Account</th><th>Your role</th><th class="num">Screens</th></tr></thead><tbody>${rows}</tbody></table></div>
        ${state.me.is_superadmin ? `<form class="inline" id="new-root" style="margin-top:12px"><label class="field">New top-level account<input name="name" required></label><button>Create</button></form>` : ""}
      </div>
      <div class="card" id="acc-detail"></div>
    </div>`;
  main.querySelectorAll("tr.clickable").forEach((tr) => (tr.onclick = () => viewAccounts(main, tr.dataset.id)));
  main.querySelector("#new-root")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    await api("/api/v1/accounts", { method: "POST", body: { name: new FormData(e.target).get("name") } });
    await viewAccounts(main, selectedId);
  });
  if (sel) await accountDetail(main.querySelector("#acc-detail"), sel, () => viewAccounts(main, selectedId));
}

async function accountDetail(box, acc, refresh) {
  const isAdmin = acc.role === "admin";
  box.innerHTML = `<h2>${esc(acc.path)}</h2>
    ${isAdmin ? `
      <form class="inline" id="rename"><label class="field">Name<input name="name" value="${esc(acc.name)}" required></label><button>Rename</button>
      <button type="button" class="danger" id="del-acc">Delete account</button></form>
      <form class="inline" id="new-sub" style="margin-top:12px"><label class="field">New sub-account (client)<input name="name" required></label><button class="primary">Create</button></form>
      <h3 style="margin-top:20px">Members</h3><div id="members" class="muted">Loading…</div>
      <h3 style="margin-top:16px">Add member</h3>
      <form class="inline" id="add-member">
        <label class="field">Email<input name="email" type="email" required></label>
        <label class="field">Name<input name="name"></label>
        <label class="field">Role<select name="role"><option>viewer</option><option>editor</option><option>admin</option></select></label>
        <label class="field">Password (new users)<input name="password" type="password" minlength="10" autocomplete="new-password"></label>
        <button class="primary">Add</button>
      </form>
      <p class="muted small">viewer – dashboards only · editor – also screens & brands · admin – also users & sub-accounts</p>`
      : `<p class="muted">You have ${acc.role} access to this account.</p>`}`;
  if (!isAdmin) return;
  const guard = (fn) => async (e) => {
    e.preventDefault();
    try {
      await fn(e);
    } catch (err) {
      alert(err.message);
    }
  };
  box.querySelector("#rename").onsubmit = guard(async (e) => {
    await api(`/api/v1/accounts/${acc.id}`, { method: "PATCH", body: { name: new FormData(e.target).get("name") } });
    refresh();
  });
  box.querySelector("#del-acc").onclick = guard(async () => {
    if (!confirm(`Delete account “${acc.name}”?`)) return;
    await api(`/api/v1/accounts/${acc.id}`, { method: "DELETE" });
    refresh();
  });
  box.querySelector("#new-sub").onsubmit = guard(async (e) => {
    await api("/api/v1/accounts", { method: "POST", body: { name: new FormData(e.target).get("name"), parent_id: acc.id } });
    refresh();
  });
  box.querySelector("#add-member").onsubmit = guard(async (e) => {
    const fd = Object.fromEntries(new FormData(e.target));
    if (!fd.password) delete fd.password;
    await api(`/api/v1/accounts/${acc.id}/members`, { method: "POST", body: fd });
    e.target.reset();
    loadMembers();
  });
  const loadMembers = async () => {
    const members = await api(`/api/v1/accounts/${acc.id}/members`);
    const mbox = box.querySelector("#members");
    mbox.classList.remove("muted");
    mbox.innerHTML = members.length
      ? `<table><tbody>${members
          .map(
            (m) => `<tr data-uid="${m.user_id}"><td>${esc(m.name || m.email)}<div class="muted small">${esc(m.email)}</div></td>
          <td><select data-act="role">${["viewer", "editor", "admin"].map((r) => `<option ${r === m.role ? "selected" : ""}>${r}</option>`).join("")}</select></td>
          <td><div class="row-actions"><button class="danger" data-act="remove">Remove</button></div></td></tr>`
          )
          .join("")}</tbody></table>`
      : `<span class="muted">No direct members. Users with access to a parent account can also see this one.</span>`;
    mbox.querySelectorAll("[data-act=role]").forEach((s) =>
      s.addEventListener("change", guard(async () => {
        await api(`/api/v1/accounts/${acc.id}/members/${s.closest("tr").dataset.uid}`, { method: "PATCH", body: { role: s.value } });
      }))
    );
    mbox.querySelectorAll("[data-act=remove]").forEach((b) =>
      b.addEventListener("click", guard(async () => {
        if (!confirm("Remove this member's access?")) return;
        await api(`/api/v1/accounts/${acc.id}/members/${b.closest("tr").dataset.uid}`, { method: "DELETE" });
        loadMembers();
      }))
    );
  };
  await loadMembers();
}

render();
