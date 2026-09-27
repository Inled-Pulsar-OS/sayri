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
  if (!open) return;
  const renderer = TAB_RENDERERS[activeTab];
  if (!renderer) return;
  bodyEl.innerHTML = "";
  bodyEl.appendChild(hint("Loading…"));
  try {
    await renderer(bodyEl);
  } catch (err) {
    replaceBox(bodyEl, err);
  }
}

// ── the live conversation ─────────────────────────────────────────

function paintTranscript(log) {
  log.innerHTML = "";
  const turns = window.sayriBalloon ? window.sayriBalloon.transcript : [];
  if (!turns.length) {
    log.appendChild(hint("No messages yet. Ask Sayri something below."));
    return;
  }
  for (const turn of turns) {
    log.appendChild(el("div", { class: "cajita-msg " + turn.role },
      el("span", { class: "who", text: turn.role === "user" ? "You" : "Sayri" }),
      turn.text));
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
      button("+ Add gateway", () => gatewayForm(box), "primary")));

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
  },
};

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

function agentForm(box, agent) {
  const a = agent || {};
  const name = el("input", { type: "text", value: a.name || "", placeholder: "Agent name" });
  const desc = el("input", { type: "text", value: a.description || "", placeholder: "What is it for?" });
  const prompt = el("textarea", { placeholder: "System prompt…" });
  prompt.value = a.system_prompt || "";
  const skills = el("input", { type: "text", value: (a.allowed_skills || []).join(", "), placeholder: "comma, separated" });
  const tools = el("input", { type: "text", value: (a.allowed_tools || []).join(", "), placeholder: "comma, separated" });

  const form = el("div", { class: "cajita-form" },
    el("h4", { text: agent ? "Edit agent" : "New agent" }),
    field("Name", name),
    field("Description", desc),
    field("System prompt", prompt),
    field("Allowed skills", skills),
    field("Allowed tools", tools),
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

  // The panel is the only thing on screen while it is open, so the status
  // poll is the only thing that needs to notice the daemon going away.
  window.addEventListener("beforeunload", stopStatusPolling);
}
