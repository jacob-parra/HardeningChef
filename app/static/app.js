"use strict";

const STORE_KEY = "hardeningchef.v1";
let CATALOG = null;
let state = { os: null, configs: {} };
let lastResult = null;
let countdownTimer = null;

const $ = (sel, el = document) => el.querySelector(sel);
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid);
  return el;
};

// Render `code` spans in catalog text without using innerHTML.
function richText(text) {
  const frag = document.createDocumentFragment();
  String(text || "").split(/(`[^`]+`)/).forEach((part) => {
    if (part.startsWith("`") && part.endsWith("`")) frag.append(h("code", { text: part.slice(1, -1) }));
    else frag.append(part);
  });
  return frag;
}

// ------------------------------------------------------------------ state ---
function defaultsFor(osId) {
  const cfg = {};
  for (const sec of CATALOG.sections[osId]) {
    cfg[sec.id] = {};
    for (const opt of sec.options) {
      let v = structuredClone(opt.default);
      if (opt.type === "portmap") {
        const full = {};
        for (const p of opt.ports) full[p.id] = (v && v[p.id]) || 0;
        v = full;
      }
      cfg[sec.id][opt.id] = v;
    }
  }
  return cfg;
}

function mergeConfig(osId, given) {
  const cfg = defaultsFor(osId);
  for (const [sid, opts] of Object.entries(given || {})) {
    if (!cfg[sid] || typeof opts !== "object") continue;
    for (const [oid, v] of Object.entries(opts)) {
      if (!(oid in cfg[sid])) continue;
      cfg[sid][oid] = typeof cfg[sid][oid] === "object" && !Array.isArray(cfg[sid][oid]) ? { ...cfg[sid][oid], ...v } : v;
    }
  }
  return cfg;
}

function save() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch (_) { /* storage unavailable */ }
}

function load() {
  try {
    const s = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
    if (s && s.os && CATALOG.sections[s.os]) {
      state.os = s.os;
      for (const id of Object.keys(CATALOG.sections)) state.configs[id] = mergeConfig(id, s.configs && s.configs[id]);
      return;
    }
  } catch (_) { /* ignore */ }
  state.os = CATALOG.oses[0].id;
  for (const os of CATALOG.oses) state.configs[os.id] = defaultsFor(os.id);
}

const cfg = () => state.configs[state.os];

// ----------------------------------------------------------------- render ---
function renderOsList() {
  const list = $("#os-list");
  list.replaceChildren();
  for (const os of CATALOG.oses) {
    const sel = os.id === state.os;
    list.append(h("button", {
      type: "button", role: "radio", "aria-checked": String(sel), class: "os" + (sel ? " selected" : ""),
      onclick: () => { state.os = os.id; save(); renderAll(); },
    },
      h("span", { class: "os-name", text: os.name }),
      h("span", { class: "os-meta", text: `${os.family === "debian" ? "APT" : "DNF"} · ${os.firewall} · ${os.mac === "apparmor" ? "AppArmor" : "SELinux"}` }),
    ));
  }
}

function renderSections() {
  const root = $("#sections");
  root.replaceChildren();
  for (const sec of CATALOG.sections[state.os]) root.append(renderSection(sec));
}

function renderSection(sec) {
  const values = cfg()[sec.id];
  const enabledOpt = sec.options.find((o) => o.id === "enabled");
  const on = !enabledOpt || values.enabled;
  const card = h("article", { class: "section" + (on ? "" : " off"), id: `sec-${sec.id}` });

  const head = h("div", { class: "section-head" },
    h("div", {}, h("h3", { text: sec.title }), h("p", { class: "muted" }, richText(sec.intro))),
    enabledOpt ? toggle(`${sec.id}.enabled`, values.enabled, (v) => { values.enabled = v; save(); renderSections(); }, enabledOpt.label) : null,
  );
  card.append(head);
  if (!on) return card;

  if (sec.fixed && sec.fixed.length) {
    card.append(h("details", { class: "fixed" },
      h("summary", { text: `Always applied (${sec.fixed.length} settings)` }),
      h("table", {}, h("tbody", {}, sec.fixed.map((f) => h("tr", {},
        h("td", {}, h("code", { text: f.setting })),
        h("td", {}, h("code", { text: f.value })),
        h("td", { class: "muted", text: f.why || "" }),
      )))),
    ));
  }

  const body = h("div", { class: "opts" });
  for (const opt of sec.options) {
    if (opt.id === "enabled") continue;
    if (opt.requires && !values[opt.requires]) continue;
    body.append(renderOption(sec, opt, values));
  }
  card.append(body);
  return card;
}

function toggle(id, checked, onchange, label) {
  return h("label", { class: "switch", title: label },
    h("input", { type: "checkbox", id, checked, onchange: (e) => onchange(e.target.checked), "aria-label": label }),
    h("span", { class: "slider" }),
  );
}

function renderOption(sec, opt, values) {
  const id = `opt-${sec.id}-${opt.id}`;
  const v = values[opt.id];
  const set = (nv, rerender = false) => { values[opt.id] = nv; save(); if (rerender) renderSections(); };
  const labelEl = h("label", { for: id, class: "opt-label" }, opt.label, opt.danger ? h("span", { class: "pill danger", text: "caution" }) : null);
  let control;

  switch (opt.type) {
    case "bool": {
      const dependents = sec.options.some((o) => o.requires === opt.id);
      return h("div", { class: "opt opt-bool" },
        h("label", { class: "check" },
          h("input", { type: "checkbox", id, checked: v, onchange: (e) => set(e.target.checked, dependents) }),
          h("span", {}, opt.label, opt.danger ? h("span", { class: "pill danger", text: "caution" }) : null)),
        meta(opt));
    }
    case "int":
      control = h("input", { type: "number", id, value: v, min: opt.min, max: opt.max, step: 1,
        oninput: (e) => set(e.target.value === "" ? "" : Number(e.target.value)) });
      break;
    case "text":
      control = h("input", { type: "text", id, value: v, placeholder: opt.placeholder || "", spellcheck: "false", autocomplete: "off",
        oninput: (e) => set(e.target.value) });
      break;
    case "textarea":
      control = h("textarea", { id, rows: 3, maxlength: opt.maxlength || 1000, oninput: (e) => set(e.target.value) });
      control.value = v;
      break;
    case "choice":
      control = h("select", { id, onchange: (e) => set(e.target.value) },
        opt.choices.map((c) => h("option", { value: c.value, selected: c.value === v, text: c.label })));
      break;
    case "multichoice":
      control = h("div", { class: "chips", id },
        opt.choices.map((c) => h("label", { class: "chip" },
          h("input", { type: "checkbox", checked: v.includes(c.value), onchange: (e) => {
            const s = new Set(values[opt.id]);
            e.target.checked ? s.add(c.value) : s.delete(c.value);
            set([...s]);
          } }), h("span", { text: c.label }))));
      break;
    case "list":
      control = h("textarea", { id, rows: opt.id.endsWith("keys") ? 3 : 2, placeholder: opt.placeholder || "", spellcheck: "false",
        class: "mono", oninput: (e) => set(e.target.value.split(/[\n,]/).map((x) => x.trim()).filter(Boolean)) });
      control.value = v.join("\n");
      if (opt.id === "new_admin_keys") control = keygenControl(control, values, set);
      break;
    case "portmap":
      return h("div", { class: "opt opt-wide" }, h("span", { class: "opt-label", text: opt.label }), portTable(opt, v, set));
    default:
      control = h("span", { text: `unsupported: ${opt.type}` });
  }
  return h("div", { class: "opt" + (opt.type === "textarea" || opt.type === "list" ? " opt-wide" : "") }, labelEl, control, meta(opt));
}

// ---------------------------------------------------------- SSH key pair ---
// Keys are generated in the browser with Web Crypto. The private key is never
// sent to the server or saved anywhere; it is only offered as a download.
let generatedKey = null; // { name, privateKey, publicKey } for the "download again" button

function sshString(bytes) {
  const b = typeof bytes === "string" ? new TextEncoder().encode(bytes) : bytes;
  const out = new Uint8Array(4 + b.length);
  new DataView(out.buffer).setUint32(0, b.length);
  out.set(b, 4);
  return out;
}
const concat = (...parts) => {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let i = 0;
  for (const p of parts) { out.set(p, i); i += p.length; }
  return out;
};
const u32 = (n) => { const b = new Uint8Array(4); new DataView(b.buffer).setUint32(0, n); return b; };
const b64 = (bytes) => btoa(String.fromCharCode(...bytes));

async function generateEd25519(comment) {
  const kp = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const pub = new Uint8Array(await crypto.subtle.exportKey("raw", kp.publicKey));
  const seed = new Uint8Array(await crypto.subtle.exportKey("pkcs8", kp.privateKey)).slice(-32);
  const pubBlob = concat(sshString("ssh-ed25519"), sshString(pub));

  // OpenSSH private key format (openssh-key-v1), unencrypted.
  const check = crypto.getRandomValues(new Uint8Array(4));
  let priv = concat(check, check, sshString("ssh-ed25519"), sshString(pub), sshString(concat(seed, pub)), sshString(comment));
  const pad = (8 - (priv.length % 8)) % 8;
  priv = concat(priv, Uint8Array.from({ length: pad }, (_, i) => i + 1));
  const blob = concat(
    new TextEncoder().encode("openssh-key-v1\0"),
    sshString("none"), sshString("none"), sshString(new Uint8Array(0)),
    u32(1), sshString(pubBlob), sshString(priv),
  );
  const body = b64(blob).match(/.{1,70}/g).join("\n");
  return {
    publicKey: `ssh-ed25519 ${b64(pubBlob)} ${comment}`,
    privateKey: `-----BEGIN OPENSSH PRIVATE KEY-----\n${body}\n-----END OPENSSH PRIVATE KEY-----\n`,
  };
}

function keygenControl(textarea, values, set) {
  const note = h("div", { class: "keygen-note", hidden: !generatedKey });
  const showNote = () => {
    note.replaceChildren(
      h("strong", { text: `Private key downloaded as ${generatedKey.name}` }),
      h("p", { text: "It is not stored anywhere else, including this server. Keep it safe; if you lose it, generate a new pair." }),
      h("div", { class: "cmd" }, h("code", { text: `chmod 600 ~/Downloads/${generatedKey.name} && ssh-keygen -p -f ~/Downloads/${generatedKey.name}` })),
      h("p", { class: "small", text: "The second command adds a passphrase (recommended). Then connect with:" }),
      h("div", { class: "cmd" }, h("code", { text: `ssh -i ~/Downloads/${generatedKey.name} ${values.new_admin_user || "<user>"}@<server>` })),
      h("button", { class: "btn tiny", type: "button", onclick: () => download(generatedKey.name, generatedKey.privateKey, "application/octet-stream") }, "Download private key again"),
    );
    note.hidden = false;
  };
  if (generatedKey) showNote();

  const err = h("p", { class: "help error-text", hidden: true });
  const btn = h("button", { class: "btn", type: "button", onclick: async () => {
    err.hidden = true;
    if (!crypto.subtle || !window.isSecureContext) {
      err.textContent = "Key generation needs HTTPS (or localhost). Run ssh-keygen -t ed25519 locally and paste the .pub file instead.";
      err.hidden = false; return;
    }
    const user = values.new_admin_user || "admin";
    try {
      const key = await generateEd25519(`${user}@hardeningchef`);
      generatedKey = { name: `${user}_ed25519`, ...key };
    } catch (e) {
      err.textContent = "This browser cannot generate Ed25519 keys. Run ssh-keygen -t ed25519 locally and paste the .pub file instead.";
      err.hidden = false; return;
    }
    const keys = [...values.new_admin_keys, generatedKey.publicKey];
    textarea.value = keys.join("\n");
    set(keys);
    download(generatedKey.name, generatedKey.privateKey, "application/octet-stream");
    showNote();
  } }, "Generate key pair");

  return h("div", { class: "keygen" }, textarea,
    h("div", { class: "keygen-row" }, btn, h("span", { class: "help", text: "Creates an Ed25519 key in your browser, adds the public key here, and downloads the private key." })),
    err, note);
}

function meta(opt) {
  const bits = [];
  if (opt.help) bits.push(h("p", { class: "help" }, richText(opt.help)));
  if (opt.why) bits.push(h("p", { class: "why" }, richText(opt.why)));
  return bits;
}

function portTable(opt, v, set) {
  return h("table", { class: "ports" },
    h("thead", {}, h("tr", {}, h("th", { text: "Service" }), h("th", { text: "Port" }), h("th", { text: "Action" }))),
    h("tbody", {}, opt.ports.map((p) => h("tr", { class: v[p.id] ? "active" : "" },
      h("td", { text: p.label }),
      h("td", {}, h("code", { text: p.spec.join(", ") })),
      h("td", {}, h("select", { "aria-label": `${p.label} action`, onchange: (e) => {
        set({ ...v, [p.id]: Number(e.target.value) }, true);
      } }, opt.actions.map((a) => h("option", { value: a.value, selected: a.value === v[p.id], text: `${a.value} - ${a.label}` })))),
    ))),
  );
}

function renderAll() {
  renderOsList();
  renderSections();
  $("#result").hidden = true;
  $("#errors").hidden = true;
}

// ------------------------------------------------------------------ build ---
async function build() {
  const btn = $("#build-btn");
  btn.disabled = true; btn.textContent = "Building...";
  $("#errors").hidden = true;
  try {
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ os: state.os, config: cfg(), share: $("#share-chk").checked }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showErrors(data.errors || [data.detail || `Request failed (${res.status})`]);
      return;
    }
    showResult(data);
  } catch (e) {
    showErrors([`Could not reach the server: ${e.message}`]);
  } finally {
    btn.disabled = false; btn.textContent = "Build script";
  }
}

function showErrors(list) {
  const box = $("#errors");
  box.replaceChildren(h("strong", { text: "Please fix the following:" }), h("ul", {}, list.map((e) => h("li", { text: e }))));
  box.hidden = false;
  $("#result").hidden = true;
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function showResult(data) {
  lastResult = data;
  $("#res-filename").textContent = data.filename;
  $("#res-sha").textContent = data.sha256;
  $("#res-script").textContent = data.script;
  $("#warnings").replaceChildren(...(data.warnings || []).map((w) => h("div", { class: "warn", text: w })));

  clearInterval(countdownTimer);
  const share = data.share;
  $("#share-card").hidden = !share;
  if (share) {
    $("#cmd-audit").textContent = `curl -fsSL ${share.url} | sudo bash`;
    $("#cmd-apply").textContent = `curl -fsSL ${share.url} | sudo bash -s -- --apply`;
    $("#cmd-verify").textContent =
      `curl -fsSLo hc.sh ${share.url} && echo "${data.sha256}  hc.sh" | sha256sum -c && sudo bash hc.sh --apply`;
    const tick = () => {
      const left = Math.max(0, Math.round(share.expires_at - Date.now() / 1000));
      const pill = $("#countdown");
      pill.textContent = left ? `expires in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : "expired";
      pill.classList.toggle("danger", left === 0);
      if (!left) clearInterval(countdownTimer);
    };
    tick();
    countdownTimer = setInterval(tick, 1000);
  }
  $("#result").hidden = false;
  $("#result").scrollIntoView({ behavior: "smooth", block: "start" });
}

function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = h("a", { href: url, download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ----------------------------------------------------------------- import ---
async function importProfile(file) {
  const text = await file.text();
  let profile = null;
  try {
    if (file.name.endsWith(".sh") || text.startsWith("#!")) {
      const m = text.match(/^# HC-PROFILE: ([A-Za-z0-9+/=]+)$/m);
      if (!m) throw new Error("no HC-PROFILE line in that script");
      profile = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(m[1]), (c) => c.charCodeAt(0))));
    } else {
      profile = JSON.parse(text);
    }
    if (!profile || !CATALOG.sections[profile.os]) throw new Error("unknown or missing OS in profile");
  } catch (e) {
    showErrors([`Could not import ${file.name}: ${e.message}`]);
    return;
  }
  state.os = profile.os;
  state.configs[profile.os] = mergeConfig(profile.os, profile.config);
  save();
  renderAll();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ------------------------------------------------------------------- init ---
async function init() {
  const res = await fetch("/api/catalog");
  CATALOG = await res.json();
  load();
  $("#foot-version").textContent = `HardeningChef catalog v${CATALOG.version}`;
  if (!CATALOG.share.enabled) $("#share-wrap").hidden = true;
  else $("#share-wrap span").append(` (valid ${Math.round(CATALOG.share.ttl / 60)} min)`);

  $("#build-btn").addEventListener("click", build);
  $("#dl-script").addEventListener("click", () => lastResult && download(lastResult.filename, lastResult.script, "text/x-shellscript"));
  $("#dl-profile").addEventListener("click", () => lastResult &&
    download(lastResult.filename.replace(/\.sh$/, ".json"), JSON.stringify(lastResult.profile, null, 2), "application/json"));
  $("#import-file").addEventListener("change", (e) => { if (e.target.files[0]) importProfile(e.target.files[0]); e.target.value = ""; });
  $("#reset-btn").addEventListener("click", () => { state.configs[state.os] = defaultsFor(state.os); save(); renderAll(); });
  document.addEventListener("click", async (e) => {
    const id = e.target.dataset && e.target.dataset.copy;
    if (!id) return;
    try {
      await navigator.clipboard.writeText($(`#${id}`).textContent);
      e.target.textContent = "Copied";
      setTimeout(() => { e.target.textContent = "Copy"; }, 1200);
    } catch (_) { e.target.textContent = "Select & copy"; }
  });
  renderAll();
}

document.addEventListener("DOMContentLoaded", init);
