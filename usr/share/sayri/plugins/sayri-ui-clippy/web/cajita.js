/**
 * Sayri Cajita — the full assistant card, rendered inside the companion
 * speech balloon.
 *
 * The webview has no database, no config file and no subprocess of its own, so
 * every tab here talks to the Sayri daemon over the same IPC the GTK Cajita
 * uses. That keeps one source of truth for the whole desktop: change a setting
 * in this panel and the orb, the CLI and the daemon all agree.
 *
 * The few things that belong to the companion itself (which character is
 * loaded, whether the classic sound effects play) are not core config, so they
 * go back to the plugin host through `postAction` instead.
 *
 * Shared state with the balloon lives on `window.sayriBalloon`, which
 * index.html defines before this module loads.
 */

const TABS = [
  { id: "chat", label: "💬 Chat" },
  { id: "history", label: "🕘 History" },
  { id: "agents", label: "🤖 Agents" },
  { id: "skills", label: "🧠 Skills" },
  { id: "plugins", label: "🧩 Plugins" },
  { id: "gateways", label: "🌐 Gateways" },
  { id: "routines", label: "⏰ Routines" },
  { id: "vault", label: "🔐 Vault" },
  { id: "settings", label: "⚙️ Settings" },
];

const TRIGGERS = [
  { value: "daily_at", label: "Every day at a time" },
  { value: "hourly", label: "Every N hours" },
  { value: "on_login", label: "When I log in" },
  { value: "cron", label: "Cron expression" },
];

const STATE_POLL_MS = 3000;
// Long enough for a model download, short enough that a wedged daemon shows up
// as an error in the panel instead of a permanent spinner.
const CALL_TIMEOUT_MS = 120000;

let root = null;
let tabsEl = null;
let bodyEl = null;
let stateEl = null;
let agentEl = null;
let versionEl = null;

let open = false;
let activeTab = "chat";
// Section to scroll to once the next tab finishes rendering, so a "Settings"
// button on a plugin row lands on that plugin's form.
let focusSection = null;
let reqSeq = 0;
// Which render is the current one, and the chain they all take their turn on.
let renderToken = 0;
let renderQueue = Promise.resolve();
let statusTimer = null;
let pluginInfo = { character: "Clippy", characters: [], sound_effects: true, version: "" };

// ── transport ────────────────────────────────────────────────────

/**
 * Ask the daemon to do something. Resolves with the handler's return value.
 *
 * The pending map is keyed by request id and lives on `window` because the
 * reply comes back through a `window.onCallResult` callback installed by
 * index.html, outside this module's scope.
 */
function call(cmd, params) {
  return new Promise((resolve, reject) => {
    const reqId = "c" + (++reqSeq);
    const timer = setTimeout(() => {
      delete window.__cajitaPending[reqId];
      reject(new Error(cmd + " timed out"));
    }, CALL_TIMEOUT_MS);
    window.__cajitaPending[reqId] = {
      resolve: (v) => { clearTimeout(timer); resolve(v); },
      reject: (e) => { clearTimeout(timer); reject(e); },
    };
    if (!window.cajitaCall(reqId, cmd, params)) {
      clearTimeout(timer);
      delete window.__cajitaPending[reqId];
      reject(new Error("no bridge to the Sayri daemon"));
    }
  });
}

function postAction(action) {
  if (window.postAction) window.postAction(action);
}

// ── tiny DOM helpers ──────────────────────────────────────────────

function appendKids(node, kids) {
  if (kids === null || kids === undefined || kids === false) return;
  if (Array.isArray(kids)) {
    for (const k of kids) appendKids(node, k);
    return;
  }
  node.appendChild(kids instanceof Node ? kids : document.createTextNode(String(kids)));
}

function el(tag, opts) {
  const n = document.createElement(tag);
  if (opts) {
    for (const key of Object.keys(opts)) {
      const v = opts[key];
      if (v === null || v === undefined || v === false) continue;
      if (key === "class") n.className = v;
      else if (key === "text") n.textContent = v;
      // Set as a property, not an attribute: `value` is what the control's
      // current value is, and reading it back later has to see what was given.
      else if (key === "value") n.value = v;
      else if (key === "onclick") n.addEventListener("click", v);
      else if (key === "onchange") n.addEventListener("change", v);
      else if (key === "oninput") n.addEventListener("input", v);
      else if (v === true) n.setAttribute(key, "");
      else n.setAttribute(key, v);
    }
  }
  for (let i = 2; i < arguments.length; i++) appendKids(n, arguments[i]);
  return n;
}

function notice(text) {
  return el("div", { class: "cajita-error", text: text });
}

function hint(text) {
  return el("div", { class: "cajita-hint", text: text });
}

function button(label, onclick, cls) {
  return el("button", { class: "cajita-btn" + (cls ? " " + cls : ""), text: label, onclick: onclick });
}

function item(title, sub, actions, active) {
  return el("div", { class: "cajita-item" + (active ? " active" : "") },
    el("div", { class: "grow" },
      el("div", { class: "title", text: title }),
      sub ? el("div", { class: "sub", text: sub }) : null),
    actions ? el("div", { class: "actions" }, actions) : null);
}

// Extensions are not built into Sayri: the panel has no installer, so getting
// a new plugin or gateway means picking it up from the Pulsar Store. This
// hands the URL to the desktop to open, since the panel is a web view with no
// browser of its own.
const STORE_URL = "https://store-os.inled.es";

function storeButton() {
  return button("Pulsar Store ↗", () => postAction("open_url:" + STORE_URL));
}

function field(label, input, note) {
  return el("div", { class: "cajita-field" },
    el("label", { text: label }),
    input,
    note ? el("div", { class: "note", text: note }) : null);
}

function select(options, value, onchange) {
  const s = el("select", { onchange: (e) => onchange(e.target.value) });
  for (const opt of options) {
    const o = el("option", { value: opt.value, text: opt.label });
    if (String(opt.value) === String(value)) o.selected = true;
    s.appendChild(o);
  }
  return s;
}

function badge(label, isOn) {
  return el("span", { class: "cajita-badge " + (isOn ? "on" : "off"), text: label });
}

function checkbox(checked, onchange) {
  const cb = el("input", { type: "checkbox" });
  cb.checked = !!checked;
  cb.addEventListener("change", () => onchange(cb.checked));
  return cb;
}

function shortTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay
    ? "Today " + d.toTimeString().slice(0, 5)
    : d.toISOString().slice(5, 16).replace("T", " ");
}

/** Show a failure at the top of the current tab without losing the form. */
function fail(box, err) {
  box.insertBefore(notice(String((err && err.message) || err)), box.firstChild);
}

function replaceBox(box, err) {
  box.innerHTML = "";
  box.appendChild(notice(String((err && err.message) || err)));
}

// ── permissions ───────────────────────────────────────────────────
//
// A question from an agent is the one thing this panel cannot afford to hide.
// It expires, and an unanswered question is a refusal — so a request opens the
// panel and puts itself at the top of Chat rather than waiting behind whatever
// the user happened to be reading. That interruption is the price of turning
// asking on, and asking is off unless the agent says otherwise.
//
// Every button on the card is one round trip to the daemon and no optimism
// about the outcome: `delivered: false` means the question had already gone, so
// the card says so rather than pretending it was answered.

let permState = { pending: [], approvals: {}, loaded: false };
let permTimer = null;
// One entry per card that has a deadline, pruned by the tick.
let permCountdowns = [];
// The block currently on screen, so a repaint replaces the right node.
let permHost = null;

/** Last one wins per id, so a re-broadcast cannot render the card twice. */
function dedupeById(list) {
  const byId = new Map();
  for (const req of list) byId.set(String((req && req.id) || ""), req);
  return Array.from(byId.values());
}

async function refreshPermissions() {
  try {
    const r = await call("permission_list");
    permState = {
      pending: (r && r.pending) || [],
      approvals: (r && r.approvals) || {},
      loaded: true,
    };
  } catch (err) {
    // A daemon that is not answering is the same as no questions open, which
    // is the safe thing to draw: no card, no countdown, no lie.
    permState = { pending: [], approvals: {}, loaded: false };
  }
  paintPermissions();
}

/**
 * Repaint the block in place, or do nothing if no tab is showing it.
 *
 * The node is held rather than looked up: this module built it, so it knows
 * which one it is, and a node that has since been replaced by a tab rebuild is
 * exactly the case where a search by id would find the wrong thing.
 */
function paintPermissions() {
  const old = permHost;
  if (!old || !old.parentNode) return;
  // Built first, then swapped in. The order matters: approvalBlock() records
  // the new block as permHost, so reading permHost after calling it would hand
  // replaceChild the node it is meant to be replacing.
  const next = approvalBlock();
  old.parentNode.replaceChild(next, old);
}

/**
 * The open questions, pinned above the conversation.
 *
 * Empty when there is nothing to ask, so the caller can append it either way
 * and a quiet panel carries no leftover heading.
 */
function approvalBlock() {
  const wrap = el("div", { class: "sayri-approvals" });
  permHost = wrap;
  if (!permState.pending.length) return wrap;

  wrap.appendChild(el("h3", {
    text: permState.pending.length === 1
      ? "Sayri needs your approval"
      : "Sayri needs your approval · " + permState.pending.length + " waiting",
  }));
  for (const req of permState.pending) wrap.appendChild(approvalCard(req));
  return wrap;
}

function approvalCard(req) {
  const id = String((req && req.id) || "");
  const card = el("div", { class: "cajita-approval", id: "sayri-approval-" + id });

  card.appendChild(el("div", { class: "cajita-approval-head" },
    el("span", { class: "cajita-approval-who", text: req.agent_name || req.agent_id || "Sayri" }),
    countdown(req)));

  // The command is the thing being approved, so it is shown verbatim and in a
  // monospace face. Never summarised: "runs a system command" is not something
  // anybody can decide on.
  card.appendChild(el("pre", { class: "cajita-approval-cmd", text: req.resource || "" }));
  if (req.reason) {
    card.appendChild(el("div", { class: "cajita-approval-why", text: req.reason }));
  }

  const settle = async (allow, remember, label) => {
    card.innerHTML = "";
    card.classList.add(allow ? "granted" : "refused");
    card.appendChild(el("div", { class: "cajita-approval-wait", text: label }));
    try {
      const r = await call("permission_answer", {
        request_id: id, allow: !!allow, remember: !!remember,
      });
      if (r && r.delivered === false) {
        card.classList.remove("granted", "refused");
        card.classList.add("stale");
        card.innerHTML = "";
        card.appendChild(el("div", {
          class: "cajita-approval-wait",
          text: "That question had already timed out. Nothing was run.",
        }));
        return;
      }
      // A remembered answer is a rule that outlives this card, so the saved
      // list has to be re-read rather than guessed at.
      if (remember) refreshPermissions();
    } catch (err) {
      card.classList.remove("granted", "refused");
      card.classList.add("stale");
      card.innerHTML = "";
      card.appendChild(el("div", {
        class: "cajita-approval-wait",
        text: "Could not reach Sayri: " + String((err && err.message) || err),
      }));
    }
  };

  card.appendChild(el("div", { class: "cajita-approval-btns" },
    button("Allow", () => settle(true, false, "Allowed. Running it now…"), "primary"),
    button("Always allow", () => settle(true, true, "Allowed, and remembered for next time…")),
    button("Deny", () => settle(false, false, "Denied. Sayri will be told why."))));
  return card;
}

/** The time left on a question, or nothing at all when it does not expire. */
function countdown(req) {
  const label = el("span", { class: "cajita-approval-timer" });
  const total = Number(req && req.expires_in) || 0;
  if (total <= 0) {
    label.textContent = "waiting for you";
    return label;
  }
  label.textContent = Math.ceil(total) + "s to answer, then it is refused";
  // The deadlines live here rather than in the nodes, so the tick is a walk
  // over a short list instead of a query of the whole document. A question that
  // times out on the daemon side takes its card away with it, so an entry whose
  // node is no longer in the document is dropped rather than ticked at.
  const entry = { node: label, deadline: Date.now() + total * 1000 };
  permCountdowns.push(entry);
  if (!permTimer) {
    permTimer = setInterval(() => {
      const now = Date.now();
      permCountdowns = permCountdowns.filter((e) => {
        if (!e.node.parentNode) return false;
        const left = (e.deadline - now) / 1000;
        e.node.textContent = left > 0
          ? Math.ceil(left) + "s to answer, then it is refused"
          : "timed out";
        return true;
      });
      if (!permCountdowns.length) {
        clearInterval(permTimer);
        permTimer = null;
      }
    }, 1000);
  }
  return label;
}

/**
 * The saved "always allow" rules, and the button to take one back.
 *
 * A remembered approval is a standing permission, so it outlives the panel and
 * the process. Listing it somewhere the user can see is the only reason it is
 * safe to offer "always" in the first place.
 */
function savedApprovalsSection() {
  const wrap = el("div", { class: "cajita-section", "data-section": "approvals" });
  wrap.appendChild(el("h3", { text: "Always allow" }));

  const actions = Object.keys(permState.approvals).sort();
  const rows = [];
  for (const action of actions) {
    for (const entry of permState.approvals[action] || []) {
      const row = el("div", { class: "cajita-item" },
        el("div", { class: "grow" },
          el("div", { class: "title", text: entry.resource || "(everything)" }),
          el("div", {
            class: "sub",
            text: action + (entry.agent_id ? " · only " + entry.agent_id : " · every agent"),
          })),
        el("div", { class: "actions" },
          button("Forget", async () => {
            try {
              await call("permission_forget", { action: action, agent_id: entry.agent_id || "" });
            } catch (err) {
              return;
            }
            refreshPermissions();
          })));
      rows.push(row);
    }
  }

  if (!rows.length) {
    wrap.appendChild(hint(
      "Nothing is remembered. Commands marked “ask” run without a question "
      + "while asking is switched off, and a “always allow” you have never "
      + "used would be a permission nobody asked for."));
    return wrap;
  }
  for (const row of rows) wrap.appendChild(row);
  wrap.appendChild(hint("These run without asking from now on, until you forget them."));
  return wrap;
}

// ── tab scaffolding ───────────────────────────────────────────────

function buildTabs() {
  tabsEl.innerHTML = "";
  for (const tab of TABS) {
    tabsEl.appendChild(el("div", {
      class: "cajita-tab" + (tab.id === activeTab ? " active" : ""),
      text: tab.label,
      onclick: () => selectTab(tab.id),
    }));
  }
}

function selectTab(id) {
  activeTab = id;
  buildTabs();
  render();
}

async function render() {
  // Renders are chained rather than run at once. A tab that has to ask the
  // daemon something before it can draw takes a moment, and two of them
  // appending to the same body is how the panel ends up showing the same
  // conversation twice. Whatever was asked for while one was in flight
  // supersedes it rather than queueing up behind it, so a burst of clicks
  // costs one render and not five.
  const token = ++renderToken;
  renderQueue = renderQueue.then(async () => {
    if (token !== renderToken || !open) return;
    const renderer = TAB_RENDERERS[activeTab];
    if (!renderer) return;
    bodyEl.innerHTML = "";
    bodyEl.appendChild(hint("Loading…"));
    try {
      await renderer(bodyEl);
    } catch (err) {
      if (token === renderToken) replaceBox(bodyEl, err);
    }
  });
  return renderQueue;
}

// ── the live conversation ─────────────────────────────────────────

// Markdown, rendered without a library. The panel is a local file:// page with
// no network, so a CDN is not an option and shipping a bundler for this would
// be out of proportion.
//
// The order below is the whole safety story: every character is escaped BEFORE
// any markdown rule runs, and nothing is ever matched as markup. Sayri's
// replies are model output, which can contain anything — if a rule ran on raw
// text, a reply containing markup would either break the layout or inject
// nodes into the panel. Only the handful of tags built below are ever emitted.
// The panel and the companion's speech bubble are two surfaces showing the same
// reply, so they share one renderer. Written twice it would be two renderers to
// keep in step, and the day they disagreed nobody would notice until a reply
// looked broken in one place and fine in the other.
export function mdToHtml(src) {
  const text = String(src == null ? "" : src).replace(/\r\n?/g, "\n");
  if (!text.trim()) return "";

  // Fenced code is lifted out first so that markdown inside a code block stays
  // literal, which is the whole point of a code block.
  const blocks = [];
  let body = text.replace(/```([a-zA-Z0-9_+-]*)[ \t]*\n?([\s\S]*?)```/g, (_m, lang, code) => {
    blocks.push(
      '<pre class="md-code"><code>'
      + escapeHtml(code.replace(/\n$/, ""))
      + "</code></pre>"
    );
    return "\u0000BLOCK" + (blocks.length - 1) + "\u0000";
  });

  // Unterminated fence: the model was cut off mid-block, so close it rather
  // than dropping the rest of the reply.
  const openFence = body.match(/```([a-zA-Z0-9_+-]*)[ \t]*\n?([\s\S]*)$/);
  if (openFence) {
    blocks.push('<pre class="md-code"><code>' + escapeHtml(openFence[2]) + "</code></pre>");
    body = body.slice(0, openFence.index) + "\u0000BLOCK" + (blocks.length - 1) + "\u0000";
  }

  body = escapeHtml(body);

  // Inline code is protected the same way, so `*` inside a span is never a
  // style marker.
  const spans = [];
  body = body.replace(/`([^`\n]+)`/g, (_m, code) => {
    spans.push("<code>" + code + "</code>");
    return "\u0000SPAN" + (spans.length - 1) + "\u0000";
  });

  const lines = body.split("\n");
  const out = [];
  let list = null; // "ul" | "ol", so a stray blank line does not end a list
  const closeList = () => { if (list) { out.push("</" + list + ">"); list = null; } };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const quote = line.match(/^&gt;\s?(.*)$/);
    const fenceLine = line.match(/^\s*(?:---+|\*\*\*+|___+)\s*$/);

    if (h) { closeList(); out.push("<h" + h[1].length + ">" + inline(h[2]) + "</h" + h[1].length + ">"); continue; }
    if (fenceLine) { closeList(); out.push("<hr>"); continue; }
    if (quote) { closeList(); out.push("<blockquote>" + inline(quote[1]) + "</blockquote>"); continue; }
    if (ul || ol) {
      const want = ul ? "ul" : "ol";
      if (list !== want) { closeList(); out.push("<" + want + ">"); list = want; }
      out.push("<li>" + inline((ul || ol)[1]) + "</li>");
      continue;
    }
    closeList();
    // A blank line is a paragraph break; anything else keeps the flow.
    if (!line.trim()) { out.push(""); continue; }
    out.push("<p>" + inline(line) + "</p>");
  }
  closeList();

  let html = out.join("\n")
    // Drop the empty strings the blank lines left behind.
    .replace(/<p><\/p>/g, "")
    .replace(/\n{2,}/g, "\n");

  // Restore the literal pieces, innermost first so a code block that held a
  // code span does not have the span marker stolen out of it.
  html = html.replace(/\u0000SPAN(\d+)\u0000/g, (_m, n) => spans[+n]);
  html = html.replace(/\u0000BLOCK(\d+)\u0000/g, (_m, n) => blocks[+n]);
  return html;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// Inline rules. Only ever run on text that is already escaped.
function inline(s) {
  return String(s)
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
    // Anything that is not http, https or mailto is left as plain text: a
    // `javascript:` or `file:` link is not worth the risk of rendering.
    .replace(/\[([^\]\n]+)\]\((?![a-z]+:|\/|#)([^\s)]+)\)/g, "$1 ($2)")
    .replace(/\*\*\*([^*\n]+)\*\*\*/g, "<strong><em>$1</em></strong>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*(?=$|[\s).,;:!?])/g, "$1<em>$2</em>")
    .replace(/(^|[\s(])_([^_\n]+)_(?=$|[\s).,;:!?])/g, "$1<em>$2</em>")
    .replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
}


// A command Sayri ran, with how it ended. Kept visually apart from speech:
// it is machine output, not something the user said or Sayri said out loud.
function toolRow(turn) {
  const t = turn.tool || {};
  const state = t.state || "ok";
  const mark = state === "running" ? "⏳" : (state === "failed" ? "✗" : "✓");
  const label = state === "running" ? "Running"
    : (state === "failed" ? "Failed" + (t.exit_code ? " (code " + t.exit_code + ")" : "")
                          : "Done");
  const row = el("div", { class: "cajita-tool " + state },
    el("div", { class: "tool-head" },
      el("span", { class: "mark", text: mark }),
      t.step ? el("span", { class: "tool-step", text: "Step " + t.step }) : null,
      el("span", { class: "tool-label", text: label }),
      el("span", { class: "grow" })),
    el("code", { class: "tool-cmd", text: turn.text || "(no command)" }));

  // What the command actually printed. This is the part that explains the
  // answer: without it you see a list of commands and have to take the result
  // on trust, which is exactly the "it did something, what?" feeling.
  if (t.output) {
    const out = el("pre", { class: "tool-out", text: t.output });
    row.appendChild(el("div", { class: "tool-out-wrap" },
      el("button", {
        class: "tool-out-toggle",
        text: "Output",
        onclick: (e) => {
          e.stopPropagation();
          const shown = out.style.display !== "none";
          out.style.display = shown ? "none" : "block";
          e.target.textContent = shown ? "Output" : "Hide output";
        },
      }),
      out));
    out.style.display = "none";
  }
  return row;
}

function paintTranscript(log) {  log.innerHTML = "";
  const turns = window.sayriBalloon ? window.sayriBalloon.transcript : [];
  if (!turns.length) {
    log.appendChild(hint("No messages yet. Ask Sayri something below."));
    return;
  }
  for (const turn of turns) {
    if (turn.role === "tool") {
      log.appendChild(toolRow(turn));
      continue;
    }
    // Only Sayri's own replies are markdown. What the user typed is shown as
    // typed: rendering it would turn a literal "*" in their question into
    // italics and make the panel disagree with what they actually wrote.
    const body = el("div", { class: "cajita-body" });
    if (turn.role === "user") {
      body.textContent = turn.text;
    } else {
      body.innerHTML = mdToHtml(turn.text);
    }
    log.appendChild(el("div", { class: "cajita-msg " + turn.role },
      el("span", { class: "who", text: turn.role === "user" ? "You" : "Sayri" }),
      body));
  }
  // Keep the newest turn in view as the answer streams in.
  log.scrollTop = log.scrollHeight;
}

function refreshChat() {
  if (!open || activeTab !== "chat") return;
  const log = bodyEl.querySelector(".cajita-transcript");
  if (log) paintTranscript(log);
}

// ── tabs ──────────────────────────────────────────────────────────

const TAB_RENDERERS = {
  /** Live conversation, plus a box to type into. */
  async chat(box) {
    box.innerHTML = "";
    // The questions come first, above the toolbar, because the thing on screen
    // has to be the decision rather than the way to reach the decision.
    await refreshPermissions();
    box.appendChild(approvalBlock());
    box.appendChild(el("div", { class: "cajita-row" },
      button("+ New chat", async () => {
        await call("new_conversation");
        window.sayriBalloon.resetTranscript();
        render();
      }, "primary"),
      button("⏹ Stop speaking", async () => { await call("interrupt"); })));

    const log = el("div", { class: "cajita-transcript" });
    box.appendChild(log);
    paintTranscript(log);

    const input = el("input", { type: "text", placeholder: "Ask Sayri anything…", autocomplete: "off" });
    const send = async () => {
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      try {
        await call("talk", { text: text });
      } catch (err) {
        fail(box, err);
      }
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); send(); }
    });
    box.appendChild(el("div", { class: "cajita-row" }, input, button("➤", send, "primary")));
  },

  /** Past conversations: open, rename, delete. */
  async history(box) {
    box.innerHTML = "";
    const sessions = await call("sessions_list", { limit: 30 });
    const status = await call("status");
    const activeId = status.session_id || "";

    box.appendChild(el("div", { class: "cajita-row" },
      button("+ New chat", async () => {
        await call("new_conversation");
        window.sayriBalloon.resetTranscript();
        selectTab("chat");
      }, "primary")));

    if (!sessions.length) {
      box.appendChild(hint("No saved conversations yet."));
      return;
    }
    for (const s of sessions) {
      box.appendChild(item(
        (s.title || "Conversation") + "  ·  " + s.messages + " msgs",
        shortTime(s.updated_at) + "  ·  " + (s.agent_id || "default"),
        [
          button("Open", async () => {
            try {
              await call("switch_session", { session_id: s.id });
              const full = await call("session_get", { session_id: s.id });
              window.sayriBalloon.setTranscript((full.messages || []).map((m) => ({
                role: m.role === "user" ? "user" : "assistant",
                text: m.content,
              })));
              selectTab("chat");
            } catch (err) {
              fail(box, err);
            }
          }, "primary"),
          button("Rename", async () => {
            const title = window.prompt("New title for this conversation:", s.title || "");
            if (title === null || !title.trim()) return;
            try {
              await call("session_rename", { session_id: s.id, title: title.trim() });
              render();
            } catch (err) {
              fail(box, err);
            }
          }),
          button("Delete", async () => {
            if (!window.confirm("Delete \"" + (s.title || "this conversation") + "\"?")) return;
            try {
              await call("session_delete", { session_id: s.id });
              render();
            } catch (err) {
              fail(box, err);
            }
          }, "danger"),
        ],
        s.id === activeId));
    }
  },

  /** Who Sayri is being, and what it is allowed to do. */
  async agents(box) {
    box.innerHTML = "";
    const agents = await call("agents_list");
    const status = await call("status");
    const activeId = status.agent_id || status.active_agent || "default";

    box.appendChild(el("div", { class: "cajita-row" },
      button("+ New agent", () => agentForm(box, null), "primary")));

    for (const a of agents) {
      const actions = [];
      if (a.id !== activeId) {
        actions.push(button("Use", async () => {
          try {
            await call("agent_switch", { agent_id: a.id });
            render();
          } catch (err) {
            fail(box, err);
          }
        }, "primary"));
      }
      actions.push(button("Edit", () => agentForm(box, a)));
      if (!a.is_builtin) {
        actions.push(button("Delete", async () => {
          if (!window.confirm("Delete the agent \"" + a.name + "\"?")) return;
          try {
            await call("agent_delete", { agent_id: a.id });
            render();
          } catch (err) {
            fail(box, err);
          }
        }, "danger"));
      }
      box.appendChild(item(
        a.name + (a.is_builtin ? "  (built-in)" : ""),
        (a.description || "No description") + "  ·  Model: " +
          ((a.model && a.model.model_name) || "—") +
          "  ·  Tools: " + ((a.allowed_tools || []).join(", ") || "none"),
        actions,
        a.id === activeId));
    }
  },

  /** Installable capabilities, from the online catalogue. */
  async skills(box) {
    box.innerHTML = "";
    const search = el("input", { class: "cajita-search", type: "text", placeholder: "Search the skill catalogue…" });
    const results = el("div", {});
    box.appendChild(search);
    box.appendChild(results);

    const showInstalled = async () => {
      let installed = [];
      try {
        installed = await call("skills_list");
      } catch (err) {
        results.innerHTML = "";
        results.appendChild(notice(String(err.message || err)));
        return;
      }
      results.innerHTML = "";
      results.appendChild(el("div", { class: "cajita-item" },
        el("div", { class: "grow" },
          el("div", { class: "title", text: "Installed skills" }),
          el("div", { class: "sub", text: installed.length + " installed" }))));
      if (!installed.length) {
        results.appendChild(hint("Nothing installed yet. Search above to add some."));
        return;
      }
      for (const s of installed) {
        results.appendChild(item(s.name || s.slug, s.description || "",
          [button("Remove", async () => {
            try {
              await call("skills_uninstall", { slug: s.slug });
              showInstalled();
            } catch (err) {
              fail(results, err);
            }
          }, "danger")]));
      }
    };

    let searchTimer = null;
    search.addEventListener("input", () => {
      clearTimeout(searchTimer);
      const q = search.value.trim();
      if (!q) { showInstalled(); return; }
      // The catalogue is a network call: debounce so every keystroke does not
      // become a request.
      searchTimer = setTimeout(async () => {
        results.innerHTML = "";
        results.appendChild(hint("Searching…"));
        try {
          const found = await call("skills_search", { query: q });
          const rows = Array.isArray(found) ? found : (found && found.results) || [];
          results.innerHTML = "";
          if (!rows.length) {
            results.appendChild(hint("No skill matches \"" + q + "\"."));
            return;
          }
          for (const s of rows) {
            const install = button("Install", async () => {
              install.disabled = true;
              install.textContent = "Installing…";
              try {
                await call("skills_install", { slug: s.slug || s.id });
                install.textContent = "Installed";
              } catch (err) {
                install.disabled = false;
                install.textContent = "Install";
                results.insertBefore(notice(String(err.message || err)), results.firstChild);
              }
            }, "primary");
            results.appendChild(item(
              s.name || s.slug || s.title || "",
              s.description || "",
              [install]));
          }
        } catch (err) {
          replaceBox(results, err);
        }
      }, 350);
    });

    await showInstalled();
  },

  /** Which interfaces are running, and which services are enabled. */
  async plugins(box) {
    box.innerHTML = "";
    const uis = await call("ui_list");
    const plugins = await call("plugins_list");
    // Which plugins have a settings form at all, so a row can offer a way into
    // it instead of a button that would go nowhere.
    const configurable = new Set();
    try {
      const schema = await call("settings_schema");
      for (const s of schema.plugin_sections || []) configurable.add(s.plugin_id);
    } catch (err) {
      // Settings being unavailable must not hide the plugin list.
    }

    box.appendChild(el("div", { class: "cajita-item" },
      el("div", { class: "grow" },
        el("div", { class: "title", text: "Desktop interfaces" }),
        el("div", { class: "sub", text: "Start, stop or change which UI the launcher opens." }))));

    box.appendChild(el("div", { class: "cajita-row" },
      el("div", { class: "grow" },
        el("div", { class: "title", text: "Add more companions and extensions" }),
        el("div", { class: "note", text: "Get new interfaces, plugins and gateways from the Pulsar Store." })),
      storeButton()));

    for (const u of uis) {
      const toggleUi = async (running) => {
        try {
          await call(running ? "ui_stop" : "ui_start", { ui_id: u.id });
        } catch (err) {
          fail(box, err);
        }
        render();
      };
      const actions = [button(u.running ? "Stop" : "Start", () => toggleUi(u.running),
                             u.running ? "danger" : "primary")];
      if (configurable.has(u.id)) {
        actions.push(button("Settings", () => openPluginSettings(u.id)));
      }
      if (!u.is_default) {
        actions.push(button("Make default", async () => {
          try {
            await call("settings_save", { values: { "ui.default_ui": u.id } });
            render();
          } catch (err) {
            fail(box, err);
          }
        }));
      }
      box.appendChild(item(
        u.name + (u.is_default ? "  (default)" : ""),
        u.description,
        actions));
    }

    const rest = plugins.filter((p) => p.has_service || configurable.has(p.id));
    if (!rest.length) return;

    box.appendChild(el("div", { class: "cajita-item" },
      el("div", { class: "grow" },
        el("div", { class: "title", text: "Plugins" }),
        el("div", { class: "sub", text: "Channel gateways and other installed plugins." }))));
    for (const p of rest) {
      const actions = [];
      if (configurable.has(p.id)) {
        actions.push(button("Settings", () => openPluginSettings(p.id)));
      }
      if (p.has_service) {
        actions.push(badge(p.service_running ? "running" : "stopped", p.service_running));
        actions.push(checkbox(p.service_enabled, async (on) => {
          try {
            await call("plugin_set_enabled", { plugin_id: p.id, enabled: on });
          } catch (err) {
            fail(box, err);
          }
          render();
        }));
      }
      box.appendChild(item(p.name || p.id, p.description, actions));
    }
  },

  /** Chat channels: Telegram, Discord, and friends. */
  async gateways(box) {
    box.innerHTML = "";
    const rows = await call("gateway_list");
    const list = Array.isArray(rows) ? rows : (rows && rows.instances) || [];

    box.appendChild(el("div", { class: "cajita-row" },
      button("+ Add gateway", () => gatewayForm(box), "primary"),
      storeButton()));

    if (!list.length) {
      box.appendChild(hint("No chat gateway configured yet."));
      return;
    }
    for (const g of list) {
      const id = g.instance_id || g.id;
      const running = g.enabled !== undefined ? g.enabled
        : (g.status === "running" || g.running === true);
      const toggle = async () => {
        try {
          await call(running ? "gateway_stop" : "gateway_start", { instance_id: id });
        } catch (err) {
          fail(box, err);
        }
        render();
      };
      box.appendChild(item(
        (g.name || id) + (running ? "" : "  (stopped)"),
        g.description || g.channel || g.plugin_id || "",
        [
          button(running ? "Stop" : "Start", toggle, running ? "danger" : "primary"),
          button("Delete", async () => {
            if (!window.confirm("Delete this gateway configuration?")) return;
            try {
              await call("gateway_delete", { instance_id: id });
              render();
            } catch (err) {
              fail(box, err);
            }
          }, "danger"),
        ]));
    }
  },

  /** Things Sayri does on a schedule. */
  async routines(box) {
    box.innerHTML = "";
    const rows = await call("routines_list");

    box.appendChild(el("div", { class: "cajita-row" },
      button("+ New routine", () => routineForm(box, null), "primary")));

    if (!rows.length) {
      box.appendChild(hint("No routines yet."));
      return;
    }
    for (const r of rows) {
      box.appendChild(item(
        r.name,
        (r.description || "") + "  ·  " + r.trigger + " @ " + (r.time_spec || "—"),
        [
          badge(r.enabled ? "on" : "off", r.enabled),
          checkbox(r.enabled, async (on) => {
            try {
              await call("routine_set_enabled", { routine_id: r.id, enabled: on });
            } catch (err) {
              fail(box, err);
            }
            render();
          }),
          button("Run now", async () => {
            try {
              await call("routines_run", { routine_id: r.id });
              window.sayriBalloon.show("Routine started: " + r.name, "⏰ Sayri", "status");
            } catch (err) {
              fail(box, err);
            }
          }, "primary"),
          button("Edit", () => routineForm(box, r)),
          button("Delete", async () => {
            if (!window.confirm("Delete the routine \"" + r.name + "\"?")) return;
            try {
              await call("routine_delete", { routine_id: r.id });
              render();
            } catch (err) {
              fail(box, err);
            }
          }, "danger"),
        ]));
    }
  },

  /** Credentials: stored obfuscated, never shown in full, never in a prompt. */
  async vault(box) {
    box.innerHTML = "";
    const secrets = await call("vault_list");

    box.appendChild(el("div", { class: "cajita-item" },
      el("div", { class: "grow" },
        el("div", { class: "title", text: "Secret credentials" }),
        el("div", { class: "sub", text: "Stored obfuscated on this machine and injected into sandboxed commands as environment variables. Reference one in a prompt as $SECRET:NAME." }))));

    box.appendChild(el("div", { class: "cajita-row" },
      button("+ Add secret", () => secretForm(box), "primary")));

    if (!secrets.length) {
      box.appendChild(hint("No secrets stored yet."));
      return;
    }
    for (const s of secrets) {
      box.appendChild(item(
        s.key,
        (s.description || "Injected into the sandbox environment at runtime") +
          "  ·  Value: " + (s.masked || "***"),
        [
          button("Copy handle", async () => {
            try {
              const r = await call("clipboard_copy", { text: "$SECRET:" + s.key });
              if (!r || !r.copied) {
                fail(box, (r && r.reason) || "could not reach the clipboard");
              }
            } catch (err) {
              fail(box, err);
            }
          }),
          button("Delete", async () => {
            if (!window.confirm("Delete the secret \"" + s.key + "\"?")) return;
            try {
              await call("vault_delete", { key: s.key });
              render();
            } catch (err) {
              fail(box, err);
            }
          }, "danger"),
        ]));
    }
  },

  /**
   * Settings, the way the GTK Cajita presents them: labelled sections, the
   * right widget per value, and one Save per section.
   *
   * The entire description arrives from the daemon (`settings_schema`), so this
   * tab does not know a single config key name and the two settings screens
   * cannot drift apart.
   */
  async settings(box) {
    box.innerHTML = "";
    let schema;
    try {
      schema = await call("settings_schema");
    } catch (err) {
      replaceBox(box, err);
      return;
    }

    box.appendChild(el("div", { class: "cajita-row" },
      button("Open the full settings window", () => postAction("open_settings"), "primary"),
      button("Reload", () => render())));

    // Each plugin owns its settings (declared in its manifest, stored in its own
    // file), so a plugin gets its own section instead of being folded into the
    // core form. This is how you reach the Clippy UI's own settings.
    for (const section of schema.plugin_sections || []) {
      box.appendChild(settingsSection(section, "plugin"));
    }
    for (const section of schema.sections || []) {
      box.appendChild(settingsSection(section, "core"));
    }

    // What Sayri is allowed to do without asking, and what it is allowed to do
    // only if somebody says so. Both halves belong in one place: a switch with
    // nothing to show for what it has allowed is a switch nobody can audit.
    await refreshPermissions();
    box.appendChild(savedApprovalsSection());
    box.appendChild(processSection());
  },
};

/**
 * What Sayri is running, and the two buttons that act on it.
 *
 * A companion that is stuck or in the way is the usual reason someone goes
 * looking for a way to shut Sayri down, so the way out is here rather than
 * buried in a menu. The state line is filled in after the tab is on screen, so
 * a slow daemon does not hold up the rest of Settings.
 */
function processSection() {
  const wrap = el("div", { class: "cajita-section", "data-section": "processes" });
  wrap.appendChild(el("h3", { text: "Sayri processes" }));

  const status = el("div", { class: "cajita-note", text: "Checking what is running\u2026" });
  wrap.appendChild(status);

  // Two-step confirmation instead of a modal. A native confirm() in a
  // transparent always-on-top companion window is jarring, and one click on
  // "Terminate everything" should not be enough to throw away the session.
  let armed = false;
  let armTimer = null;
  const kill = button("Terminate all Sayri processes", () => {
    if (!armed) {
      armed = true;
      kill.textContent = "Click again to confirm";
      kill.classList.add("armed");
      // The button forgets on its own, so a stray earlier click cannot
      // authorise a destructive action half a minute later.
      if (armTimer) clearTimeout(armTimer);
      armTimer = setTimeout(() => {
        armed = false;
        kill.textContent = "Terminate all Sayri processes";
        kill.classList.remove("armed");
      }, 6000);
      return;
    }
    if (armTimer) clearTimeout(armTimer);
    status.textContent = "Terminating. Sayri is closing now.";
    postAction("kill_all");
  }, "danger");

  wrap.appendChild(el("div", { class: "cajita-row" },
    button("Restart this companion", () => {
      // The companion restarts itself, so this window goes away mid-action.
      // Saying so beats a click that looks like it did nothing.
      status.textContent = "Restarting. The companion will be back in a moment\u2026";
      postAction("restart_companion");
    }),
    kill));

  Promise.resolve()
    .then(() => call("ui_status", { ui_id: "sayri-ui-clippy" }))
    .then((s) => {
      status.textContent = s && s.running
        ? "The companion is running"
          + (s.pid ? " (pid " + s.pid + ")" : "") + "."
        : "The companion is not running.";
    })
    .catch(() => {
      status.textContent = "The daemon did not answer, so the state is unknown. "
        + "The buttons still work.";
    });

  return wrap;
}

// ── settings ───────────────────────────────────────────────────────

/**
 * Open the Settings tab scrolled to one plugin's own section.
 *
 * A plugin's settings live in the Settings tab (it has one form, not two), so
 * "Settings" on a plugin row is a jump, not a new screen. Falls back to the top
 * of the tab if the plugin's section is not there, so a stale row can never
 * leave the user on a blank tab.
 */
async function openPluginSettings(pluginId) {
  activeTab = "settings";
  focusSection = "plugin:" + pluginId;
  buildTabs();
  await render();
  const wanted = focusSection;
  focusSection = null;
  // Matched by attribute rather than a CSS selector: section ids carry a colon
  // ("plugin:sayri-ui-clippy") and there is no need for selector escaping.
  const kids = bodyEl.children ? Array.from(bodyEl.children) : [];
  const target = kids.find((n) => n.getAttribute && n.getAttribute("data-section") === wanted);
  if (target && target.scrollIntoView) target.scrollIntoView({ block: "start" });
}

/**
 * One settings section: a titled card of labelled fields and a single Save.
 *
 * Values that only need picking (switches, dropdowns) apply the moment they
 * change, so nobody has to hunt for a Save button after flipping a toggle.
 * Typed values wait for the section button, which is what keeps "one button
 * per section" from turning into "one button per field".
 */
function settingsSection(section, mode) {
  const isPlugin = mode === "plugin";
  const card = el("div", { class: "cajita-form", "data-section": section.id },
    el("h4", { text: section.title }),
    section.subtitle ? hint(section.subtitle) : null);

  const pending = {};
  const saveBtn = button(section.save_label || "Save", saveAll, "primary");
  const ctx = {
    // A section is "dirty" once something is typed but not yet saved; the
    // button says so, because an unsaved API key is a silent failure later.
    markPending(key, value) {
      pending[key] = value;
      saveBtn.classList.add("dirty");
    },
    saveNow,
    saveAll,
  };

  function clearPending() {
    for (const key of Object.keys(pending)) delete pending[key];
    saveBtn.classList.remove("dirty");
  }

  async function saveNow(key, value) {
    try {
      const res = await send([key], value);
      reportSave(card, res, [[key, value]]);
    } catch (err) {
      fail(card, err);
    }
  }

  async function saveAll() {
    const values = Object.assign({}, pending);
    if (!Object.keys(values).length) return;
    saveBtn.disabled = true;
    try {
      const res = await send(Object.keys(values), null, values);
      if (res.ok) clearPending();
      reportSave(card, res, Object.keys(values).map((k) => [k, values[k]]));
    } catch (err) {
      fail(card, err);
    } finally {
      saveBtn.disabled = false;
    }
  }

  /** One call for a core section; plugin settings are one key per call. */
  async function send(keys, firstValue, values) {
    if (!isPlugin) {
      return await call("settings_save", { values: values || pick(keys, firstValue) });
    }
    const saved = [];
    const applied = {};
    const errors = {};
    for (const key of keys) {
      const value = values ? values[key] : firstValue;
      try {
        const r = await call("plugin_settings_set", {
          plugin_id: section.plugin_id, key: key, value: value,
        });
        applied[key] = r.value;
        saved.push(key);
      } catch (err) {
        errors[key] = String((err && err.message) || err);
      }
    }
    return { ok: !Object.keys(errors).length, saved: saved, values: applied, errors: errors };
  }

  for (const f of section.fields || []) {
    card.appendChild(settingsField(f, ctx));
  }
  card.appendChild(el("div", { class: "cajita-row" },
    saveBtn,
    button("Revert", () => render())));
  return card;
}

function pick(keys, value) {
  const out = {};
  for (const key of keys) out[key] = value;
  return out;
}

/**
 * Show what a save did: a tick, any per-key error, and the cases where the
 * daemon stored something other than what was sent (a number clamped to its
 * range) or where a restart is needed for the change to bite.
 */
function reportSave(card, res, sent) {
  const errors = (res && res.errors) || {};
  const errorKeys = Object.keys(errors);
  for (const key of errorKeys) card.appendChild(notice(key + ": " + errors[key]));

  const stored = (res && res.values) || {};
  const adjusted = [];
  for (const [key, value] of sent || []) {
    if (errorKeys.indexOf(key) !== -1) continue;
    // Composite fields (the Piper voice triple) store several keys, so only
    // report a change when the answer maps back onto the field that was sent.
    const back = stored[key] !== undefined
      ? stored[key]
      : Object.keys(stored).filter((k) => k.indexOf(key.split(".")[0] + ".") === 0)
          .map((k) => stored[k]).join("|");
    if (back !== undefined && String(back) !== String(value)) {
      adjusted.push(key + " is now " + back);
    }
  }

  if (errorKeys.length) return;
  const ok = el("div", { class: "cajita-hint saved-tick", text: "Saved ✓" });
  card.appendChild(ok);
  setTimeout(() => ok.remove(), 2500);
  for (const note of adjusted) card.appendChild(hint(note));
  const restart = (res && res.restart_required) || [];
  if (restart.length) {
    card.appendChild(hint("Restart Sayri for " + restart.join(", ") + " to take effect."));
  }
}

/** One schema field: the right widget, the right label, the right save timing. */
function settingsField(f, ctx) {
  const immediate = f.save === "now";
  const hintText = f.hint || "";

  if (f.kind === "toggle") {
    // A switch reads as a labelled row; a form field with a checkbox in it
    // looks like a mistake.
    return el("div", { class: "cajita-row", "data-field": f.key },
      checkbox(f.value, (on) => (immediate ? ctx.saveNow(f.key, on) : ctx.markPending(f.key, on))),
      el("span", { class: "grow", text: f.label }),
      hintText ? el("span", { class: "note", text: hintText }) : null);
  }

  let input;
  if (f.kind === "choice") {
    input = select(f.options || [], f.value, (v) =>
      (immediate ? ctx.saveNow(f.key, v) : ctx.markPending(f.key, v)));
  } else if (f.kind === "number") {
    input = el("input", {
      type: "number",
      step: f.step === undefined ? "any" : String(f.step),
      min: f.min === undefined ? null : String(f.min),
      max: f.max === undefined ? null : String(f.max),
      value: text(f.value),
    });
  } else if (f.kind === "textarea") {
    input = el("textarea", { rows: "4" });
    input.value = text(f.value);
  } else if (f.kind === "password") {
    // The daemon never ships a stored credential, so the box starts empty and
    // leaving it empty keeps what is already saved.
    input = el("input", {
      type: "password",
      value: "",
      autocomplete: "off",
      placeholder: f.value ? "unchanged (" + f.value + ")" : "not set",
    });
  } else {
    input = el("input", { type: "text", value: text(f.value), placeholder: f.placeholder || "" });
  }

  input.addEventListener("input", () => {
    if (!immediate) ctx.markPending(f.key, input.value);
  });

  const row = field(f.label, input, hintText || null);
  row.setAttribute("data-field", f.key);
  if (f.asset) row.appendChild(assetRow(f));
  return row;
}

function text(value) {
  return value === null || value === undefined ? "" : String(value);
}

/**
 * The file a choice depends on: "already downloaded", or a button to fetch it.
 *
 * Choosing a Whisper model or a Piper voice only does something if the file is
 * on disk, and the failure is otherwise silent. Saying so here saves a trip to
 * a terminal.
 */
function assetRow(f) {
  const asset = f.asset;
  if (!asset) return null;
  const row = el("div", { class: "cajita-row" });
  if (asset.downloaded) {
    row.appendChild(badge("✓ " + asset.label + " ready", true));
    return row;
  }
  const status = el("span", { class: "note", text: (asset.size ? asset.size + ", " : "") + "not downloaded" });
  row.appendChild(status);
  row.appendChild(button("⬇ Download", async () => {
    status.textContent = "starting…";
    watchAsset(asset, status, row);
    try {
      const r = await call("asset_download", { kind: asset.kind, params: asset.params });
      if (r && r.already_running) status.textContent = "already downloading…";
    } catch (err) {
      status.textContent = "download failed: " + String((err && err.message) || err);
    }
  }));
  return row;
}

/** Follow an in-flight download through the daemon's progress broadcasts. */
function watchAsset(asset, status, row) {
  const token = asset.kind + ":";
  const listener = (ev) => {
    const d = (ev && ev.detail) || {};
    if (!d.token || d.token.indexOf(token) !== 0) return;
    if (d.done === undefined) {
      status.textContent = "downloading… " + (d.percent || 0) + "%";
      return;
    }
    window.removeEventListener("sayri-daemon-event", listener);
    row.innerHTML = "";
    row.appendChild(d.error
      ? notice("Download failed: " + d.error)
      : badge("✓ " + asset.label + " ready", true));
  };
  window.addEventListener("sayri-daemon-event", listener);
}

// ── inline forms ──────────────────────────────────────────────────

// Isolation levels, from the model's SandboxLevel enum, in the order a user
// thinks about them: how much Sayri is allowed to touch, from nothing to
// everything.
const SANDBOX_LEVELS = [
  ["LEVEL_0_NO_EXEC", "Level 0 — Pure chat", "No commands at all. It can only talk."],
  ["LEVEL_1_READONLY", "Level 1 — Read-only", "Reads files in a bubblewrap sandbox. Writes nothing."],
  ["LEVEL_2_ISOLATED_DEV", "Level 2 — Isolated workspace", "A private workspace, no system access, no display."],
  ["LEVEL_3_HOST_USER", "Level 3 — Your user account", "Full terminal access as you. Can open apps and manage files."],
  ["LEVEL_4_HOST_ROOT", "Level 4 — Administrator", "Everything above plus root, asking for your password."],
];

function agentForm(box, agent) {
  const a = agent || {};
  const name = el("input", { type: "text", value: a.name || "", placeholder: "Agent name" });
  const desc = el("input", { type: "text", value: a.description || "", placeholder: "What is it for?" });
  const prompt = el("textarea", { placeholder: "System prompt…" });
  prompt.value = a.system_prompt || "";
  const skills = el("input", { type: "text", value: (a.allowed_skills || []).join(", "), placeholder: "Leave blank to allow every skill" });
  const tools = el("input", { type: "text", value: (a.allowed_tools || []).join(", "), placeholder: "Leave blank to allow every tool" });
  const level = (a.sandbox && a.sandbox.level) || "LEVEL_3_HOST_USER";
  const levelInfo = el("div", { class: "note", text: sandboxNote(level) });
  const levelSel = select(
    SANDBOX_LEVELS.map(([value, label]) => ({ value, label })),
    level,
    (v) => { levelInfo.textContent = sandboxNote(v); }
  );
  const levelField = el("div", { class: "cajita-field" },
    el("label", { text: "Isolation level" }),
    levelSel,
    levelInfo);
  const loopBox = checkbox(a.investigation_loop, () => {});
  const learnBox = checkbox(a.reinforcement_learning, () => {});
  const askBox = checkbox(a.sandbox && a.sandbox.ask_before_run, () => {});

  const form = el("div", { class: "cajita-form" },
    el("h4", { text: agent ? "Edit agent" : "New agent" }),
    field("Name", name),
    field("Description", desc),
    field("System prompt", prompt),
    levelField,
    field("Allowed skills", skills,
      "Blank means every installed skill is available. Name skills to limit it to those."),
    field("Allowed tools", tools,
      "Blank means it can use any tool, within its isolation level. Name tools to restrict it."),
    // These two decide how hard the agent works before answering. Both are off
    // by default: a capable model asked a straight question should answer it,
    // not go off searching the web first and then build a reply out of whatever
    // it found. Turning the loop on opts into that behaviour deliberately.
    field("Autonomous loop", loopBox,
      "Searches for how to do something before doing it, and retries when a "
      + "command fails. Off means it answers directly, and does not search the "
      + "web unless you ask it to."),
    field("Learning from past answers", learnBox,
      "Remembers what it learned about you and consults it next time. Off "
      + "means each answer stands on its own."),
    // Asking is opt-in per agent, and only does anything at level 3 or 4. The
    // note says all three things, because a switch that appears to do nothing
    // is worse than one that is not there at all.
    field("Ask before running commands", askBox,
      "Off by default: commands marked “ask” just run. Turn this on and the "
      + "agent stops at each one and waits for you in the Chat tab — but only "
      + "at level 3 and 4, since below that your answer could not change what "
      + "it is allowed to do. Refusals are not affected either way."),
    el("div", { class: "cajita-row" },
      button("Save", async () => {
        if (!name.value.trim()) { fail(box, "The agent needs a name."); return; }
        try {
          await call("agent_save", {
            id: a.id || "",
            name: name.value.trim(),
            description: desc.value.trim(),
            system_prompt: prompt.value,
            allowed_skills: skills.value,
            allowed_tools: tools.value,
            sandbox_level: levelSel.value,
            investigation_loop: loopBox.checked,
            reinforcement_learning: learnBox.checked,
            ask_before_run: askBox.checked,
          });
        } catch (err) {
          fail(box, err);
          return;
        }
        render();
      }, "primary"),
      button("Cancel", () => render())));
  box.insertBefore(form, box.firstChild);
  name.focus();
}

function sandboxNote(level) {
  const found = SANDBOX_LEVELS.find(([value]) => value === level);
  return found ? found[2] : "";
}

function routineForm(box, routine) {
  const r = routine || {};
  const name = el("input", { type: "text", value: r.name || "", placeholder: "Routine name" });
  const desc = el("input", { type: "text", value: r.description || "", placeholder: "Short description" });
  const trigger = select(TRIGGERS, r.trigger || "daily_at", () => {});
  const timeSpec = el("input", { type: "text", value: r.time_spec || "09:00", placeholder: "09:00, or an interval in hours" });
  const prompt = el("textarea", { placeholder: "What should Sayri do?" });
  prompt.value = r.prompt || "";
  const speak = checkbox(r.speak_tts !== false, () => {});
  const notify = checkbox(r.notify_desktop !== false, () => {});

  const form = el("div", { class: "cajita-form" },
    el("h4", { text: routine ? "Edit routine" : "New routine" }),
    field("Name", name),
    field("Description", desc),
    field("Trigger", trigger),
    field("Time", timeSpec, "09:00 for daily_at, an interval in hours for hourly."),
    field("Prompt", prompt),
    el("div", { class: "cajita-row" }, speak, el("span", { text: "Speak the answer out loud" })),
    el("div", { class: "cajita-row" }, notify, el("span", { text: "Show a desktop notification" })),
    el("div", { class: "cajita-row" },
      button("Save", async () => {
        if (!name.value.trim()) { fail(box, "The routine needs a name."); return; }
        try {
          await call("routine_save", {
            id: r.id || "",
            name: name.value.trim(),
            description: desc.value.trim(),
            trigger: trigger.value,
            time_spec: timeSpec.value.trim(),
            prompt: prompt.value,
            speak_tts: speak.checked,
            notify_desktop: notify.checked,
            enabled: r.enabled !== false,
          });
        } catch (err) {
          fail(box, err);
          return;
        }
        render();
      }, "primary"),
      button("Cancel", () => render())));
  box.insertBefore(form, box.firstChild);
  name.focus();
}

function secretForm(box) {
  const key = el("input", { type: "text", placeholder: "NAME, e.g. GITHUB_TOKEN" });
  const value = el("input", { type: "password", placeholder: "Secret value" });
  const desc = el("input", { type: "text", placeholder: "What is it for? (optional)" });

  const form = el("div", { class: "cajita-form" },
    el("h4", { text: "Add secret" }),
    field("Key", key),
    field("Value", value),
    field("Description", desc),
    el("div", { class: "cajita-row" },
      button("Save", async () => {
        if (!key.value.trim() || !value.value) {
          fail(box, "A secret needs both a key and a value.");
          return;
        }
        try {
          await call("vault_set", {
            key: key.value.trim(),
            value: value.value,
            description: desc.value.trim(),
          });
        } catch (err) {
          fail(box, err);
          return;
        }
        render();
      }, "primary"),
      button("Cancel", () => render())));
  box.insertBefore(form, box.firstChild);
  key.focus();
}

function gatewayForm(box) {
  const name = el("input", { type: "text", placeholder: "Gateway name" });
  const plugin = el("input", { type: "text", placeholder: "Plugin id, e.g. sayri-gateway-telegram" });
  const token = el("input", { type: "password", placeholder: "Bot token / API key" });

  const form = el("div", { class: "cajita-form" },
    el("h4", { text: "Add gateway" }),
    field("Name", name),
    field("Plugin id", plugin),
    field("Credentials", token, "Stored in the vault and read at startup."),
    el("div", { class: "cajita-row" },
      button("Save", async () => {
        if (!name.value.trim() || !plugin.value.trim()) {
          fail(box, "A gateway needs a name and a plugin id.");
          return;
        }
        try {
          if (token.value) {
            await call("vault_set", {
              key: plugin.value.trim().toUpperCase() + "_TOKEN",
              value: token.value,
              description: "Token for " + name.value.trim(),
            });
          }
          await call("gateway_save", {
            name: name.value.trim(),
            plugin_id: plugin.value.trim(),
            enabled: true,
          });
        } catch (err) {
          fail(box, err);
          return;
        }
        render();
      }, "primary"),
      button("Cancel", () => render())));
  box.insertBefore(form, box.firstChild);
  name.focus();
}

// ── status bar ────────────────────────────────────────────────────

async function pollStatus() {
  try {
    const st = await call("status");
    stateEl.textContent = st.state || "idle";
    stateEl.className = "cajita-badge " + (st.state === "idle" ? "off" : "on");
    const who = st.agent_name || st.agent_id || st.agent || pluginInfo.character;
    agentEl.textContent = who + (st.mic_on ? "  ·  🎙️ listening" : "");
    if (st.version && !pluginInfo.version) {
      pluginInfo.version = st.version;
      if (versionEl) versionEl.textContent = "v" + st.version;
    }
  } catch (err) {
    stateEl.textContent = "offline";
    stateEl.className = "cajita-badge off";
  }
}

function startStatusPolling() {
  stopStatusPolling();
  pollStatus();
  statusTimer = setInterval(pollStatus, STATE_POLL_MS);
}

function stopStatusPolling() {
  if (statusTimer) { clearInterval(statusTimer); statusTimer = null; }
}

// ── open / close ──────────────────────────────────────────────────

export function setOpen(next) {
  open = !!next;
  if (open) {
    postAction("cajita_open");
    window.sayriBalloon.hide();
    root.style.display = "flex";
    buildTabs();
    render();
    startStatusPolling();
  } else {
    postAction("cajita_close");
    root.style.display = "none";
    stopStatusPolling();
    // Closing Settings must not leave a bare companion on screen with no way to
    // talk to it: hand the window back to the speech balloon, which is the
    // compact view the gear was opened from.
    window.sayriBalloon.showChatBox();
  }
}

export function isOpen() {
  return open;
}

export function applyInfo(info) {
  pluginInfo = Object.assign(pluginInfo, info || {});
  // The host can hand us the plugin info before the document is parsed, so
  // paint the version only once the header exists; init() picks it up then.
  if (versionEl && pluginInfo.version) versionEl.textContent = "v" + pluginInfo.version;
}

export function init() {
  root = document.getElementById("cajita");
  tabsEl = document.getElementById("cajita-tabs");
  bodyEl = document.getElementById("cajita-body");
  stateEl = document.getElementById("cajita-state");
  agentEl = document.getElementById("cajita-agent");
  versionEl = document.getElementById("cajita-version");
  if (!root) return;
  if (versionEl && pluginInfo.version) versionEl.textContent = "v" + pluginInfo.version;

  document.getElementById("cajita-close").addEventListener("click", () => setOpen(false));
  document.getElementById("cajita-refresh").addEventListener("click", () => render());

  // Follow the conversation. The balloon owns the transcript and pushes a turn
  // per event; without this the panel only ever redrew when its tab was
  // rebuilt, so anything happening behind an open Chat tab was invisible until
  // you closed and reopened it.
  if (window.sayriBalloon && typeof window.sayriBalloon.onChange === "function") {
    window.sayriBalloon.onChange(() => refreshChat());
  }

  // Questions and answers from the daemon. A request opens the panel and lands
  // on Chat even when the panel was closed, because the alternative is a
  // question that sits behind a hidden window until it expires, and an expired
  // question is a refused command.
  window.addEventListener("sayri-daemon-event", (ev) => {
    const d = (ev && ev.detail) || {};
    if (d.event === "permission_request") {
      permState.pending = dedupeById(permState.pending.concat([d.request || {}]));
      permState.loaded = true;
      // Chat, not whatever was open. setOpen() renders the active tab, so the
      // tab has to be chosen first or the panel would open on a page with no
      // card on it.
      if (!open) {
        activeTab = "chat";
        setOpen(true);
      } else if (activeTab !== "chat") {
        selectTab("chat");
      } else {
        paintPermissions();
      }
      return;
    }
    if (d.event === "permission_resolved") {
      const id = String(d.request_id || "");
      permState.pending = permState.pending.filter((r) => String(r.id) !== id);
      // The answer may have been remembered, which is a rule the panel has to
      // show, so this re-reads rather than assumes.
      refreshPermissions();
    }
  });

  // The panel is the only thing on screen while it is open, so the status
  // poll is the only thing that needs to notice the daemon going away.
  window.addEventListener("beforeunload", stopStatusPolling);
}
