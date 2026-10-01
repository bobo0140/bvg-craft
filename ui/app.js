/* BVG Craft — интерфейсът. Говори с Python през window.pywebview.api. */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
let api = null;
let S = null;               // последното състояние от сървъра
let logSeen = 0;
let logFilter = "all";
let formsFilled = false;

const SPIRIT_TEXT = {
  keeper: "Водещият. Обявява събития, задава гатанки, раздава награди.",
  builder: "Строи каквото поискаш — къщи, кули, мостове.",
  trickster: "Пакостник. Кокошки на главата, левитация, номера.",
  trader: "Разменя предмети. Пазари се жестоко.",
};

const EVENT_TEXT = {
  meteors: "Метеорити падат около играчите. Вътре може да има руда.",
  bounty: "Един играч свети. Който го победи, печели.",
  treasure: "Скрито съкровище. Подсказки топло-студено.",
  undead: "Полунощ, буря и чудовища. Наградени са оцелелите.",
  gravity: "40 секунди скачане до небето.",
  chickens: "Вали пилета. Нищо повече.",
  giant: "Гигант три пъти по-голям. Който го повали, печели.",
  riddle: "Гатанка в чата. Първият верен отговор печели.",
  race: "Координати и старт. Първият стигнал печели.",
};

const EVENT_NAMES = {
  meteors: "Метеоритен дъжд", bounty: "Лов на глави", treasure: "Съкровище",
  undead: "Нощ на мъртвите", gravity: "Гравитацията се счупи",
  chickens: "Вали пилета", giant: "Гигантът", riddle: "Гатанка",
  race: "Състезание",
};

const CHAOS_TEXT = [
  "Духовете само отговарят. Без изненади.",
  "Рядко събитие. Шегаджията мирува.",
  "Събития и номера от Шегаджията. Препоръчително.",
  "Чести събития, тежки изпитания, много номера.",
];

/* ---------- помощни ---------- */
function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("bad", bad);
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 3200);
}

async function call(name, ...args) {
  try {
    const r = await api[name](...args);
    if (r && r.ok === false && r.error) toast(r.error, true);
    else if (r && r.message) toast(r.message, r.ok === false);
    return r;
  } catch (e) {
    toast("Грешка: " + e, true);
    return null;
  }
}

const HEAD_COLORS = ["#c08552", "#7a9e5c", "#5c7fa8", "#a8695c", "#8f6fa8",
  "#b89a54", "#5c9ea0", "#9c6b84"];
function head(name) {
  let h = 2166136261;                         // FNV-1a — разпръсква по-равно
  for (const ch of String(name)) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619) >>> 0; }
  return `<span class="head" style="--h:${HEAD_COLORS[h % HEAD_COLORS.length]}"></span>`;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------- навигация ---------- */
$$(".nav").forEach(b => b.addEventListener("click", () => {
  $$(".nav").forEach(n => n.classList.toggle("is-active", n === b));
  $$(".view").forEach(v => v.classList.toggle("is-active",
    v.id === "view-" + b.dataset.view));
  if (b.dataset.view === "server") loadVersions();
}));

/* ---------- рисуване ---------- */
function renderPower() {
  const lamp = $("#lamp"), txt = $("#powerText"), btn = $("#powerBtn");
  lamp.className = "lamp" + (S.ready ? " on" : S.running ? " starting" : "");
  txt.textContent = S.ready ? `Върви — ${S.online.length} в света`
    : S.running ? "Пуска се..." : "Спрян";
  btn.textContent = S.running ? "Спри сървъра" : "Пусни сървъра";
  btn.classList.toggle("btn-primary", !S.running);
  btn.classList.toggle("btn-danger", S.running);
}

function renderHome() {
  $("#homeTitle").textContent = S.ready ? "Светът е буден"
    : S.running ? "Светът се събужда" : "Светът спи";
  $("#homeLede").textContent = S.ready
    ? `Minecraft ${S.mc_version || ""} — ${S.online.length} от ${S.config.max_players} играчи.`
    : S.running ? "Генерира се светът. Първия път отнема минута-две."
    : "Пусни сървъра, за да оживеят духовете.";

  $("#banners").innerHTML = Object.entries(S.characters).map(([k, c]) => `
    <article class="banner c-${k} ${c.enabled ? "" : "dim"}">
      <div class="glyph" aria-hidden="true"></div>
      <h3>${esc(c.name)}</h3>
      <p>${esc(SPIRIT_TEXT[k] || "")}</p>
      <p class="where ${c.enabled ? "placed" : ""}">
        ${c.enabled ? "В света" : "Още не е призован"}</p>
    </article>`).join("");

  $("#onlineList").innerHTML = S.online.length
    ? S.online.map(p => `<li>${head(p)}${esc(p)}</li>`).join("")
    : `<li class="empty">${S.ready ? "Никой не е влязъл." : "Сървърът не върви."}</li>`;

  const ev = S.event;
  $("#eventNow").innerHTML = ev
    ? `<strong>${esc(EVENT_NAMES[ev.key] || ev.title)}</strong><span>Остават ${ev.left} секунди</span>`
    : `<p class="empty">Нищо не върви в момента.</p>`;
  $("#randomEvent").textContent = ev ? "Спри събитието" : "Изненадай ги";
  $("#randomEvent").disabled = !S.ready;
}

function renderServer() {
  const ver = S.mc_version;
  const checks = [
    { ok: !!S.paper, title: "Сървър",
      text: S.paper ? `Paper ${ver}` : "Не е свален",
      action: S.paper ? "Свали наново" : "Свали Paper", fn: "get_paper" },
    { ok: S.java_ok, title: "Java",
      text: S.java ? `Java ${S.java}${S.java_ok ? "" : ` — трябва ${S.java_needed}`}`
        : `Няма — трябва ${S.java_needed}`,
      action: S.java_ok ? null : `Свали Java ${S.java_needed}`, fn: "get_java" },
    { ok: !!ver, title: "Лаунчер",
      text: ver ? `Избери версия ${ver}` : "Първо свали сървъра", action: null },
  ];
  $("#checks").innerHTML = checks.map(c => `
    <div class="check ${c.ok ? "ok" : "bad"}">
      <b>${c.title}</b><span>${esc(c.text)}</span>
      ${c.action ? `<button class="btn btn-sm" data-fn="${c.fn}">${c.action}</button>` : ""}
    </div>`).join("");
  $$("#checks [data-fn]").forEach(b => b.addEventListener("click", async () => {
    b.disabled = true;
    const ver = $("#mc_version").value || null;
    // Не пращаме празен аргумент — pywebview го превръща в null
    if (b.dataset.fn === "get_paper") await call("get_paper", ver);
    else await call(b.dataset.fn);
  }));

  const p = S.progress;
  $("#progress").hidden = !p;
  if (p) {
    $("#bar").style.width = Math.round((p.value || 0) * 100) + "%";
    $("#progressText").textContent = `Свалям ${p.what}...`;
  }
}

function renderPlayers() {
  const wl = S.config.whitelist || [];
  const admins = S.config.admins || [];
  const live = new Set(S.online);
  const all = [...new Set([...wl, ...S.online])];
  $("#roster").innerHTML = all.length ? all.map(n => `
    <li>
      ${head(n)}
      <span><span class="name">${esc(n)}</span><span class="tags">
        ${live.has(n) ? '<span class="tag live">в света</span>' : ""}
        ${admins.includes(n) ? '<span class="tag admin">админ</span>' : ""}
        ${!wl.includes(n) ? '<span class="tag">не е в списъка</span>' : ""}
      </span></span>
      <span class="row-actions">
        ${live.has(n) ? `<button class="btn btn-sm" data-a="prank" data-n="${esc(n)}">Пусни номер</button>` : ""}
        <button class="btn btn-sm" data-a="admin" data-n="${esc(n)}">
          ${admins.includes(n) ? "Махни админ" : "Направи админ"}</button>
        ${wl.includes(n) ? `<button class="btn btn-sm btn-danger" data-a="remove" data-n="${esc(n)}">Махни</button>` : ""}
      </span>
    </li>`).join("")
    : `<li><span></span><span class="empty">Списъкът е празен. Добави първо себе си.</span><span></span></li>`;

  $$("#roster [data-a]").forEach(b => b.addEventListener("click", () => {
    const n = b.dataset.n;
    if (b.dataset.a === "remove") call("whitelist_remove", n).then(refresh);
    if (b.dataset.a === "admin") call("toggle_admin", n).then(refresh);
    if (b.dataset.a === "prank") call("prank", n);
  }));

  const net = S.config.open_to_network;
  $("#joinInfo").innerHTML = net
    ? `В същата мрежа приятелите влизат на <code>${esc(S.ip)}</code>. Отвън — през Radmin VPN, ZeroTier или playit.gg.`
    : `Сървърът е само за този компютър — влизаш на <code>127.0.0.1</code>. За приятели включи „Приятели от мрежата“ в раздел Сървър.`;
}

function renderSpirits() {
  const opts = S.online.map(p => `<option>${esc(p)}</option>`).join("");
  $("#spirits").innerHTML = Object.entries(S.characters).map(([k, c]) => `
    <article class="spirit c-${k}">
      <div class="spirit-head">
        <div>
          <h3>${esc(c.name)}</h3>
          <p class="calls">Вика се с: ${c.aliases.map(esc).join(", ")}</p>
        </div>
        ${c.enabled ? `<button class="link-danger" data-a="unplace" data-k="${k}">Прибери</button>` : ""}
      </div>
      <p class="lede">${esc(SPIRIT_TEXT[k] || "")}</p>
      <div class="row">
        <select data-k="${k}" class="who" ${S.ready ? "" : "disabled"}>
          ${opts || "<option value=''>Никой не е в света</option>"}
        </select>
        <button class="btn" data-a="place" data-k="${k}" ${S.ready && S.online.length ? "" : "disabled"}>
          ${c.enabled ? "Премести до него" : "Призови до него"}</button>
      </div>
      <form class="inline talk" data-k="${k}">
        <input placeholder="Кажи му нещо като собственик" ${S.ready ? "" : "disabled"}>
        <button class="btn" ${S.ready ? "" : "disabled"}>Кажи</button>
      </form>
    </article>`).join("");

  $$("#spirits [data-a=place]").forEach(b => b.addEventListener("click", () => {
    const who = $(`#spirits select[data-k="${b.dataset.k}"]`).value;
    call("place", b.dataset.k, who).then(refresh);
  }));
  $$("#spirits [data-a=unplace]").forEach(b => b.addEventListener("click",
    () => call("remove", b.dataset.k).then(refresh)));
  $$("#spirits .talk").forEach(f => f.addEventListener("submit", e => {
    e.preventDefault();
    const inp = $("input", f);
    if (!inp.value.trim()) return;
    call("say_as", f.dataset.k, inp.value.trim());
    toast("Изпратено. Отговорът ще дойде в играта.");
    inp.value = "";
  }));
}

function renderEvents() {
  const n = S.online.length;
  $("#eventGrid").innerHTML = S.events.map(ev => {
    const blocked = !S.ready || n < ev.min_players || !!S.event;
    const why = !S.ready ? "" : n < ev.min_players
      ? ` Иска поне ${ev.min_players} играчи.` : "";
    return `<button class="ev" data-k="${ev.key}" ${blocked ? "disabled" : ""}>
      <b>${esc(EVENT_NAMES[ev.key] || ev.title)}</b><span>${esc(EVENT_TEXT[ev.key] || "")}${why}</span>
    </button>`;
  }).join("");
  $("#eventNote").textContent = !S.ready ? "Пусни сървъра, за да пускаш събития."
    : S.event ? `Сега върви „${EVENT_NAMES[S.event.key] || S.event.title}“. Изчакай да свърши.` : "";
  $$("#eventGrid .ev").forEach(b => b.addEventListener("click",
    () => call("event", b.dataset.k).then(refresh)));
  $("#chaosNote").textContent = CHAOS_TEXT[S.config.chaos] || "";
}

function fillForms() {
  if (formsFilled) return;
  formsFilled = true;
  const c = S.config;
  for (const f of ["#serverForm", "#aiForm"]) {
    $$(`${f} [name]`).forEach(el => {
      if (el.type === "checkbox") el.checked = !!c[el.name];
      else if (el.type === "password") el.placeholder = c[el.name + "_set"]
        ? "Запазен — остави празно, за да не го сменяш" : el.placeholder;
      else el.value = c[el.name] ?? "";
    });
  }
  $("#chaos").value = c.chaos;
  $("#director_minutes").value = c.director_minutes;
}

function collect(form) {
  const out = {};
  $$("[name]", form).forEach(el => {
    if (el.type === "checkbox") out[el.name] = el.checked;
    else if (el.dataset.type === "int") out[el.name] = parseInt(el.value, 10);
    else if (el.type === "password") { if (el.value) out[el.name] = el.value; }
    else out[el.name] = el.value;
  });
  return out;
}

/* ---------- лог ---------- */
function renderLog(items) {
  const box = $("#log");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  const frag = document.createDocumentFragment();
  for (const e of items) {
    const d = document.createElement("div");
    d.className = `l ${e.level}`;
    d.dataset.level = e.level;
    d.innerHTML = `<span class="t">${e.t}</span><span class="s">${esc(e.src)}</span><span class="m">${esc(e.msg)}</span>`;
    applyFilter(d);
    frag.appendChild(d);
  }
  box.appendChild(frag);
  while (box.children.length > 1500) box.removeChild(box.firstChild);
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function applyFilter(d) {
  const lv = d.dataset.level;
  d.hidden = (logFilter === "game" && lv === "console")
    || (logFilter === "problems" && !["warn", "error"].includes(lv));
}

$$("#logFilters .chip").forEach(c => c.addEventListener("click", () => {
  logFilter = c.dataset.f;
  $$("#logFilters .chip").forEach(x => x.classList.toggle("is-on", x === c));
  $$("#log .l").forEach(applyFilter);
}));

/* ---------- действия ---------- */
$("#powerBtn").addEventListener("click", async () => {
  if (!S) return;
  $("#powerBtn").disabled = true;
  if (S.running) await call("stop");
  else await call("start");
  setTimeout(() => { $("#powerBtn").disabled = false; refresh(); }, 1200);
});

$("#randomEvent").addEventListener("click", () => {
  if (S.event) return call("stop_event").then(refresh);
  const free = S.events.filter(e => S.online.length >= e.min_players);
  if (!free.length) return toast("Няма достатъчно играчи за събитие.", true);
  const pick = free[Math.floor(Math.random() * free.length)];
  call("event", pick.key).then(refresh);
});

$("#serverForm").addEventListener("submit", async e => {
  e.preventDefault();
  const r = await call("save", collect(e.target));
  if (r && r.ok) toast("Запазено. Важи от следващото пускане.");
  refresh();
});

$("#aiForm").addEventListener("submit", async e => {
  e.preventDefault();
  const r = await call("save", collect(e.target));
  if (r && r.ok) toast("Запазено.");
  $$("#aiForm [type=password]").forEach(i => i.value = "");
  formsFilled = false;
  await refresh();
});

$("#testAi").addEventListener("click", async () => {
  $("#aiResult").textContent = "Питам...";
  const r = await api.test_ai();
  $("#aiResult").textContent = r && r.ok ? `Работи: „${r.say}“` : `Не работи: ${r ? r.error : ""}`;
});

$("#chaos").addEventListener("input", e => {
  $("#chaosNote").textContent = CHAOS_TEXT[e.target.value];
});
$("#chaos").addEventListener("change", e =>
  call("save", { chaos: parseInt(e.target.value, 10) }).then(refresh));
$("#director_minutes").addEventListener("change", e =>
  call("save", { director_minutes: parseInt(e.target.value, 10) }));

$("#addPlayer").addEventListener("submit", async e => {
  e.preventDefault();
  const n = $("#newPlayer").value.trim();
  if (!n) return;
  if (!/^[A-Za-z0-9_]{1,16}$/.test(n)) {
    return toast("Името в Minecraft е само с латински букви, цифри и _.", true);
  }
  const r = await call("whitelist_add", n);
  if (r && r.ok) { $("#newPlayer").value = ""; toast(`${n} може да влиза.`); }
  refresh();
});

$("#consoleForm").addEventListener("submit", e => {
  e.preventDefault();
  const v = $("#consoleInput").value;
  if (v.trim()) call("console", v);
  $("#consoleInput").value = "";
});

async function loadVersions() {
  const sel = $("#mc_version");
  if (sel.options.length > 1) return;
  const list = await api.versions();
  for (const v of list || []) {
    const o = document.createElement("option");
    o.value = v; o.textContent = v;
    sel.appendChild(o);
  }
  sel.value = S?.config.mc_version || "";
}

/* ---------- обновяване ---------- */
async function refresh() {
  try {
    S = await api.state();
  } catch (e) { return; }
  fillForms();
  renderPower();
  renderHome();
  renderServer();
  renderPlayers();
  renderEvents();
  const active = document.activeElement;
  if (!$("#view-spirits").contains(active)) renderSpirits();
}

async function pollLogs() {
  try {
    const items = await api.logs(logSeen);
    if (items && items.length) {
      logSeen = items[items.length - 1].id;
      renderLog(items);
    }
  } catch (e) { /* прозорецът се затваря */ }
}

function boot() {
  api = window.pywebview.api;
  refresh();
  pollLogs();
  setInterval(refresh, 1500);
  setInterval(pollLogs, 700);
}

if (window.pywebview && window.pywebview.api) boot();
else window.addEventListener("pywebviewready", boot);
