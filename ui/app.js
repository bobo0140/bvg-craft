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

/* true → зелено, false → червено, null → сиво (не е проверено) */
const cls = ok => ok === true ? "ok" : ok === false ? "bad" : "wait";
const mark = ok => ok === true ? "✓" : ok === false ? "✗" : "–";

function ago(sec) {
  if (sec == null) return "";
  if (sec < 60) return `${sec} сек`;
  if (sec < 3600) return `${Math.round(sec / 60)} мин`;
  return `${Math.round(sec / 3600)} ч`;
}

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

function rosterMap() {
  return Object.fromEntries((S.roster || []).map(r => [r.name, r]));
}

/* ---------- навигация ---------- */
$$(".nav").forEach(b => b.addEventListener("click", () => {
  $$(".nav").forEach(n => n.classList.toggle("is-active", n === b));
  $$(".view").forEach(v => v.classList.toggle("is-active",
    v.id === "view-" + b.dataset.view));
  if (b.dataset.view === "server") { loadVersions(); loadWorld(); }
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

  const R = rosterMap();
  $("#onlineList").innerHTML = S.online.length
    ? S.online.map(p => `<li>${head(p)}<span>${esc(p)}</span>${
        R[p] && R[p].ip ? `<small class="ip" title="${esc(R[p].ip_kind || "")}">${esc(R[p].ip)}</small>` : ""}</li>`).join("")
    : `<li class="empty">${S.ready ? "Никой не е влязъл." : S.running ? "Сървърът още зарежда." : "Сървърът не върви."}</li>`;

  const ev = S.event;
  $("#eventNow").innerHTML = ev
    ? `<strong>${esc(EVENT_NAMES[ev.key] || ev.title)}</strong><span>Остават ${ev.left} секунди</span>`
    : `<p class="empty">Нищо не върви в момента.</p>`;
  $("#randomEvent").textContent = ev ? "Спри събитието" : "Изненадай ги";
  $("#randomEvent").disabled = !S.ready || (!ev && !S.online.length);
}

function renderHealth() {
  $("#health").innerHTML = (S.health || []).map(h => `
    <div class="h-item ${cls(h.ok)}" title="${esc(h.detail)}">
      <span class="dot"></span><b>${esc(h.name)}</b><small>${esc(h.detail)}</small>
    </div>`).join("");
  const err = $("#stateError");
  err.hidden = !S.state_error;
  if (S.state_error) err.textContent = `Таблото не успя да се обнови: ${S.state_error}. Показвам последното известно.`;
}

function renderAi() {
  const h = (S.health || []).find(x => x.name === "Изкуствен разум") || {};
  const a = S.ai || {};
  const title = h.ok === true ? "Работи" : h.ok === false ? "Не работи" : "Още не е питан";
  const stats = a.ok || a.err
    ? `${plural(a.ok, "отговор", "отговора")}, ${plural(a.err, "грешка", "грешки")}${a.last_ok_ago != null ? `, последен преди ${ago(a.last_ok_ago)}` : ""}`
    : "";
  let tip = "";
  const d = String(h.detail || "");
  // При „работи" подробността е само броят — показваме статистиката вместо нея
  const line = h.ok === true ? stats : [d, stats].filter(Boolean).join(" · ");
  if (/key not valid|API_KEY_INVALID|401|403|Грешка 400/i.test(d))
    tip = "Ключът не е приет. Копирай го наново от aistudio.google.com/apikey и го постави долу.";
  else if (/Няма ключ/.test(d))
    tip = "Без ключ духовете посрещат и коментират с готови реплики, а събитията вървят нормално. За разговори сложи безплатен ключ.";
  else if (/лимит/i.test(d))
    tip = "Безплатният лимит се нулира всеки ден. Дотогава духовете ползват готови реплики.";
  const box = $("#aiStatus");
  box.className = "ai-status " + cls(h.ok);
  box.innerHTML = `<span class="dot"></span><div><b>${title}</b>
    <small>${esc(line)}</small>${tip ? `<small class="tip">${esc(tip)}</small>` : ""}</div>`;
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
    $("#progressText").textContent = `${p.verb || "Свалям"} ${p.what}... ${Math.round((p.value || 0) * 100)}%`;
  }
  syncImport();
}

function renderPlayers() {
  const wl = S.config.whitelist || [];
  const admins = S.config.admins || [];
  // Сървърът праща готов списък с IP; ако е по-стара версия — смятаме тук
  const list = S.roster || [...new Set([...wl, ...S.online])].map(n => ({
    name: n, online: S.online.includes(n), whitelisted: wl.includes(n),
    admin: admins.includes(n), ip: null, ip_kind: null, deaths: 0, visits: 0 }));
  list.sort((a, b) => (b.online - a.online) || a.name.localeCompare(b.name));
  $("#roster").innerHTML = list.length ? list.map(r => {
    const n = r.name;
    const meta = [
      r.ip ? `IP <span class="ip">${esc(r.ip)}</span><span class="kind">(${esc(r.ip_kind || "")})</span>`
           : "IP — още не е влизал",
      r.visits ? plural(r.visits, "влизане", "влизания") : "",
      r.deaths ? plural(r.deaths, "смърт", "смърти") : "",
    ].filter(Boolean).join(" · ");
    return `
    <li class="${r.online ? "is-live" : ""}">
      ${head(n)}
      <span><span class="name">${esc(n)}</span><span class="tags">
        ${r.online ? '<span class="tag live">в света</span>' : ""}
        ${r.admin ? '<span class="tag admin">админ</span>' : ""}
        ${!r.whitelisted ? '<span class="tag">не е в списъка</span>' : ""}
      </span><span class="meta">${meta}</span></span>
      <span class="row-actions">
        ${r.online ? `<button class="btn btn-sm" data-a="prank" data-n="${esc(n)}">Пусни номер</button>` : ""}
        <button class="btn btn-sm" data-a="admin" data-n="${esc(n)}">
          ${r.admin ? "Махни админ" : "Направи админ"}</button>
        ${r.whitelisted ? `<button class="btn btn-sm btn-danger" data-a="remove" data-n="${esc(n)}">Махни</button>`
          : `<button class="btn btn-sm" data-a="add" data-n="${esc(n)}">Добави в списъка</button>`}
      </span>
    </li>`;
  }).join("")
    : `<li><span></span><span class="empty">Списъкът е празен. Добави първо себе си.</span><span></span></li>`;

  $$("#roster [data-a]").forEach(b => b.addEventListener("click", () => {
    const n = b.dataset.n;
    if (b.dataset.a === "remove") call("whitelist_remove", n).then(refresh);
    if (b.dataset.a === "add") call("whitelist_add", n).then(refresh);
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
    : S.event ? `Сега върви „${EVENT_NAMES[S.event.key] || S.event.title}“. Изчакай да свърши.`
    : !n ? "Няма никой в света — събитията чакат поне един играч."
    : S.config.chaos ? `Пазителят пуска изненада на около ${S.config.director_minutes} минути. В света: ${n}.`
    : "Изненадите са спрени (Тихо). Можеш да пускаш събития ръчно.";
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

/* ---------- стар свят ---------- */
let worldCheck = null;          // последната успешна проверка на избран свят
let importSeen;                 // id на последния показан резултат

function worldLine(w) {
  const bits = [
    w.version ? `Minecraft ${w.version}` : "версия неизвестна",
    w.size_mb ? `${w.size_mb} MB` : "",
    w.last_played ? `играно на ${w.last_played}` : "",
    w.nether ? "с Нетер и Края" : "",
    w.hardcore ? "хардкор" : "",
  ].filter(Boolean);
  return `<b>${esc(w.name)}</b><span>${bits.map(esc).join(" · ")}</span>`;
}

async function loadWorld() {
  let r = null;
  try { r = await api.world_current(); } catch (e) { /* няма връзка */ }
  $("#worldNow").innerHTML = r && r.world
    ? `<span class="tagline">Сега:</span>${worldLine(r.world)}`
    : `<p class="empty">Още няма свят — ще се създаде при първото пускане, или внеси стария.</p>`;
}

async function inspectWorld(path) {
  path = (path || "").trim();
  if (!path) return;
  worldCheck = null;
  const box = $("#worldFound");
  box.hidden = false;
  box.innerHTML = `<p class="empty">Чета света...</p>`;
  let r = null;
  try { r = await api.world_inspect(path); } catch (e) { r = { ok: false, error: String(e) }; }
  if (!r || !r.ok) {
    box.innerHTML = `<div class="wcard"><p class="warn bad">${esc(r ? r.error : "Грешка")}</p>
      <p class="hint">Избери папката на стария сървър (там, където е server.properties), самата папка на света (с файл level.dat) или .zip с тях.</p></div>`;
    return;
  }
  worldCheck = r;
  const w = r.world, names = r.whitelist_names || [];
  const alt = (w.alternatives || []).length > 1
    ? `<p class="hint">Вътре има няколко свята (${esc(w.alternatives.join(", "))}). Взимам „${esc(w.folder)}“.</p>` : "";
  box.innerHTML = `
    <div class="wcard">
      <div class="wline">${worldLine(w)}</div>
      <p class="wpath">${esc(r.path)}</p>
      ${alt}
      ${r.blocked ? `<p class="warn bad">${esc(r.blocked)}</p>` : ""}
      ${(r.warnings || []).map(x => `<p class="warn">${esc(x)}</p>`).join("")}
      ${names.length ? `<label class="toggle"><input type="checkbox" id="mergeWl" checked><span></span>
        Пусни и ${names.length} играчи от стария списък (${esc(names.slice(0, 6).join(", "))}${names.length > 6 ? "…" : ""})</label>` : ""}
      <div class="actions">
        <button class="btn btn-primary" id="doImport" type="button">Внеси този свят</button>
        <span class="hint" id="importHint"></span>
      </div>
    </div>`;
  $("#doImport").addEventListener("click", doImport);
  syncImport();
}

function syncImport() {
  const b = $("#doImport");
  if (!b || !worldCheck || !S) return;
  const busy = !!(S.progress && S.progress.verb);
  b.disabled = !!worldCheck.blocked || S.running || busy;
  $("#importHint").textContent = worldCheck.blocked ? ""
    : S.running ? "Първо спри сървъра — светът не се сменя, докато върви."
    : busy ? "Внасям..." : "Сегашният свят отива в _backups. Нищо не се трие.";
}

async function doImport() {
  if (!worldCheck) return;
  const merge = $("#mergeWl") ? $("#mergeWl").checked : false;
  $("#doImport").disabled = true;
  const r = await call("world_import", worldCheck.path, merge);
  if (!r || !r.ok) syncImport();
}

function showImportResult(res) {
  const box = $("#worldFound");
  box.hidden = false;
  worldCheck = null;
  if (res.ok) {
    box.innerHTML = `<div class="wcard done">
      <div class="wline"><b>Светът е внесен</b></div>
      <p>Пусни сървъра. Първото зареждане на голям или по-стар свят отнема малко повече.</p>
      ${res.backup ? `<p class="wpath">Предишният свят е запазен в ${esc(res.backup)}</p>` : ""}
      ${(res.added || []).length ? `<p class="hint">Добавени в списъка: ${esc(res.added.join(", "))}</p>` : ""}
      <p class="hint">Ако духовете бяха призовани в стария свят, призови ги наново от раздел Духовете.</p>
    </div>`;
    toast("Светът е внесен.");
  } else {
    box.innerHTML = `<div class="wcard"><p class="warn bad">${esc(res.message || "Не се получи.")}</p>
      <p class="hint">Сегашният свят е оставен както си беше.</p></div>`;
    toast(res.message || "Внасянето не се получи.", true);
  }
  loadWorld();
}

$("#pickFolder").addEventListener("click", async () => {
  const r = await call("world_pick", "folder");
  if (r && r.path) { $("#worldPath").value = r.path; inspectWorld(r.path); }
});
$("#pickZip").addEventListener("click", async () => {
  const r = await call("world_pick", "zip");
  if (r && r.path) { $("#worldPath").value = r.path; inspectWorld(r.path); }
});
$("#worldPathForm").addEventListener("submit", e => {
  e.preventDefault();
  inspectWorld($("#worldPath").value);
});
$("#openServerDir").addEventListener("click", () => call("open_folder", "server"));

/* ---------- пълна проверка ---------- */
$("#diagBtn").addEventListener("click", async () => {
  const b = $("#diagBtn");
  b.disabled = true;
  b.textContent = "Проверявам...";
  $("#diagList").innerHTML = `<li class="empty">Пробвам командите на живия сървър. Отнема до 20 секунди.</li>`;
  const r = await call("diagnose");
  b.disabled = false;
  b.textContent = "Провери всичко";
  if (!r || !r.checks) { $("#diagList").innerHTML = ""; return; }
  $("#diagList").innerHTML = r.checks.map(c => `
    <li class="${cls(c.ok)}"><span class="mark">${mark(c.ok)}</span><span>${esc(c.name)}</span>${
      c.detail ? `<small>${esc(c.detail)}</small>` : ""}</li>`).join("");
  const bad = r.checks.filter(c => c.ok === false).length;
  toast(bad ? `${bad} от проверките не минаха — виж кои.` : "Всичко работи.", bad > 0);
});

/* ---------- обновяване ---------- */
async function refresh() {
  try {
    S = await api.state();
  } catch (e) { return; }
  if (!S) return;
  const steps = [fillForms, renderPower, renderHealth, renderHome, renderServer,
    renderPlayers, renderEvents, renderAi];
  // Една счупена част не бива да спира останалите
  for (const f of steps) {
    try { f(); } catch (e) { console.error(f.name, e); }
  }
  const active = document.activeElement;
  if (!$("#view-spirits").contains(active)) {
    try { renderSpirits(); } catch (e) { console.error(e); }
  }
  const res = S.import_result;
  const id = res ? res.id : null;
  if (importSeen === undefined) importSeen = id;     // старо от преди отварянето
  else if (id && id !== importSeen) { importSeen = id; showImportResult(res); }
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
