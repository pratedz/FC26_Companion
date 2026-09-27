/**
 * LE Companion web UI — talks only to /api/* (real Python product backend).
 */
(function () {
  "use strict";

  const state = {
    view: "home",
    status: null,
    targetId: null,
    cardHits: [],
    selectedCard: null,
    selectedCardIndex: -1,
    addHits: [],
    selectedAddCard: null,
    squad: null,
    packs: [],
    presets: [],
    applyPayload: null, // { kind, label, run }
    lastResult: "",
  };

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  async function api(path, opts) {
    const options = opts || {};
    const init = {
      method: options.method || "GET",
      headers: { Accept: "application/json" },
    };
    if (options.body) {
      init.method = options.method || "POST";
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    const res = await fetch(path, init);
    let data = null;
    try {
      data = await res.json();
    } catch (_) {
      data = { ok: false, error: "Invalid JSON", status: res.status };
    }
    if (!res.ok && data && data.error == null) {
      data.error = data.reason || res.statusText;
    }
    return { status: res.status, data };
  }

  function setDock(detail, enableApply) {
    const el = $("#dock-detail");
    if (el) el.textContent = detail || "Idle";
    const btn = $("#btn-apply");
    if (btn) btn.disabled = !enableApply;
  }

  function setToast(el, msg, kind) {
    if (!el) return;
    el.textContent = msg || "";
    el.className = "toast" + (kind ? " " + kind : "");
  }

  function updateChrome(status) {
    state.status = status;
    const arm = (status && status.arm) || {};
    const pill = $("#pill-live");
    if (pill) {
      const live = !!arm.live;
      const busy = !!arm.busy;
      pill.textContent = live ? (busy ? "BUSY" : "LIVE") : arm.state || "OFF";
      pill.className = "pill " + (live ? (busy ? "pill-busy" : "pill-live") : "pill-off");
    }
    const line = $("#status-line");
    if (line) line.textContent = (status && status.status_line) || arm.hint || "";
    if (status && status.target_playerid != null) {
      state.targetId = status.target_playerid;
    }
    const tp = $("#target-pill");
    if (tp) tp.textContent = "Target: " + (state.targetId != null ? state.targetId : "—");
    const q = $("#dock-queue");
    if (q && status && status.health) {
      q.textContent = "Queue: " + (status.health.pending_jobs || 0);
    }
    const dl = $("#dock-live");
    if (dl) dl.textContent = "Worker: " + (arm.state || "—");
  }

  async function refreshStatus() {
    const { data } = await api("/api/status");
    updateChrome(data);
    return data;
  }

  async function refreshQueue() {
    const { data } = await api("/api/queue");
    const q = $("#dock-queue");
    if (q) q.textContent = "Queue: " + (data.pending_jobs || 0);
    const dl = $("#dock-live");
    if (dl) dl.textContent = "Worker: " + (data.live ? "LIVE" : "OFF");
    return data;
  }

  // ── Views ─────────────────────────────────────────────────────────

  function renderHome() {
    return `
      <section class="view" data-surface="home">
        <h2>Home</h2>
        <p class="lead">LIVE-first setup · arm once · apply from any surface</p>
        <div class="grid-2">
          <div class="panel">
            <h3>Setup</h3>
            <ol class="goals" id="home-goals">
              <li>Start FC26 with <strong>LE Launcher</strong></li>
              <li><strong>Install worker</strong> then <strong>Go LIVE</strong> (paste bridge once)</li>
              <li>Export squad · search cards · Apply on the dock</li>
            </ol>
            <div class="row" style="margin-top:12px">
              <button type="button" class="btn secondary" data-action="install">Install worker</button>
              <button type="button" class="btn success" data-action="go-live">Go LIVE</button>
              <button type="button" class="btn ghost" data-action="copy-bridge">Copy bridge</button>
            </div>
            <div class="toast" id="home-toast"></div>
          </div>
          <div class="panel">
            <h3>Status</h3>
            <pre id="home-status" class="muted" style="white-space:pre-wrap;margin:0;font-family:var(--mono);font-size:12px">Loading…</pre>
          </div>
        </div>
        <div class="panel">
          <h3>Quick packs</h3>
          <div class="pack-grid" id="home-packs"></div>
        </div>
      </section>`;
  }

  function renderCards() {
    return `
      <section class="view" data-surface="cards">
        <h2>Cards</h2>
        <p class="lead">Search catalog → select variant → set target → Apply</p>
        <div class="panel">
          <div class="row">
            <label class="field"><span>Query</span>
              <input type="text" id="cards-query" placeholder="Messi, Neymar…" />
            </label>
            <label class="field" style="max-width:100px"><span>Year</span>
              <input type="text" id="cards-year" placeholder="25 / 26 / local" />
            </label>
            <label class="field" style="max-width:90px"><span>OVR</span>
              <input type="number" id="cards-ovr" placeholder="any" />
            </label>
            <label class="field" style="max-width:140px"><span>Target playerid</span>
              <input type="number" id="cards-target" placeholder="158023" />
            </label>
            <button type="button" class="btn secondary" id="cards-search-btn">Search</button>
            <button type="button" class="btn ghost" id="cards-set-target">Lock target</button>
          </div>
          <div class="grid-2">
            <div>
              <h3>Variants</h3>
              <ul class="list" id="cards-list"></ul>
            </div>
            <div>
              <h3>Selected</h3>
              <pre id="cards-selected" class="muted" style="white-space:pre-wrap;font-family:var(--mono);font-size:12px">None</pre>
              <div class="row">
                <button type="button" class="btn success" id="cards-prep-apply">Prepare Apply</button>
                <button type="button" class="btn secondary" id="cards-to-add">Send → Add team</button>
              </div>
              <div class="toast" id="cards-toast"></div>
            </div>
          </div>
        </div>
      </section>`;
  }

  function renderBoost() {
    return `
      <section class="view" data-surface="boost">
        <h2>Boost</h2>
        <p class="lead">Workflow packs and team scripts via the turbo queue</p>
        <div class="panel">
          <div class="pack-grid" id="boost-packs">Loading packs…</div>
          <div class="toast" id="boost-toast"></div>
        </div>
      </section>`;
  }

  function renderAddTeam() {
    return `
      <section class="view" data-surface="add_team">
        <h2>Add team</h2>
        <p class="lead">SAFE free-agent overwrite + transfer (CreatePlayer advanced-only)</p>
        <div class="panel">
          <div class="row">
            <label class="field"><span>Card query</span>
              <input type="text" id="add-query" placeholder="Search card to sign…" />
            </label>
            <label class="field" style="max-width:100px"><span>Year</span>
              <input type="text" id="add-year" placeholder="26" />
            </label>
            <label class="field" style="max-width:120px"><span>Mode</span>
              <select id="add-mode">
                <option value="auto">auto (dummy)</option>
                <option value="dummy">dummy</option>
                <option value="create">create (risk)</option>
              </select>
            </label>
            <button type="button" class="btn secondary" id="add-search-btn">Search</button>
          </div>
          <ul class="list" id="add-list"></ul>
          <div class="row" style="margin-top:12px">
            <button type="button" class="btn success" id="add-run">Queue Add to team</button>
          </div>
          <div class="toast" id="add-toast"></div>
        </div>
      </section>`;
  }

  function renderSquad() {
    return `
      <section class="view" data-surface="squad">
        <h2>Squad</h2>
        <p class="lead">Exported board — click a row to lock Target</p>
        <div class="panel">
          <div class="row">
            <button type="button" class="btn secondary" id="squad-refresh">Refresh</button>
            <button type="button" class="btn ghost" id="squad-export">Queue export squad</button>
            <span class="muted" id="squad-meta"></span>
          </div>
          <div class="table-wrap">
            <table class="squad" id="squad-table">
              <thead>
                <tr><th>ID</th><th>Name</th><th>Pos</th><th>OVR</th><th>POT</th><th>#</th></tr>
              </thead>
              <tbody></tbody>
            </table>
          </div>
          <div class="toast" id="squad-toast"></div>
        </div>
      </section>`;
  }

  function renderEditor() {
    return `
      <section class="view" data-surface="editor">
        <h2>Editor</h2>
        <p class="lead">Presets → field patch → Apply onto locked target</p>
        <div class="panel">
          <div class="row">
            <label class="field"><span>Preset</span>
              <select id="editor-preset"></select>
            </label>
            <label class="field" style="max-width:140px"><span>Target playerid</span>
              <input type="number" id="editor-target" />
            </label>
            <button type="button" class="btn secondary" id="editor-load">Load preset</button>
            <button type="button" class="btn success" id="editor-prep">Prepare Apply</button>
          </div>
          <label class="field"><span>Fields (JSON)</span>
            <textarea id="editor-fields" rows="14" style="font-family:var(--mono)"></textarea>
          </label>
          <div class="toast" id="editor-toast"></div>
        </div>
      </section>`;
  }

  function renderCatalog() {
    return `
      <section class="view" data-surface="catalog">
        <h2>Catalog</h2>
        <p class="lead">Local card_db / SQLite index status</p>
        <div class="panel">
          <pre id="catalog-info" class="muted" style="white-space:pre-wrap;font-family:var(--mono);font-size:12px">Loading…</pre>
        </div>
      </section>`;
  }

  function renderAbout() {
    return `
      <section class="view" data-surface="about">
        <h2>About</h2>
        <p class="lead">Primary UI is this web stack · backend is the existing Python product engine</p>
        <div class="panel">
          <pre id="about-info" class="muted" style="white-space:pre-wrap;font-family:var(--mono);font-size:12px">Loading…</pre>
        </div>
      </section>`;
  }

  const RENDERERS = {
    home: renderHome,
    cards: renderCards,
    boost: renderBoost,
    add_team: renderAddTeam,
    squad: renderSquad,
    editor: renderEditor,
    catalog: renderCatalog,
    about: renderAbout,
  };

  async function showView(name) {
    state.view = name;
    $$(".nav-item").forEach((b) => {
      b.classList.toggle("active", b.getAttribute("data-view") === name);
    });
    const main = $("#main-panel");
    const fn = RENDERERS[name] || renderHome;
    main.innerHTML = fn();
    await mountView(name);
  }

  async function mountView(name) {
    if (name === "home") {
      const { data } = await api("/api/home");
      updateChrome(data);
      const pre = $("#home-status");
      if (pre) {
        pre.textContent = JSON.stringify(
          {
            arm: data.arm,
            health: {
              pending_jobs: data.health && data.health.pending_jobs,
              squad_count: data.health && data.health.squad_count,
              card_db_ok: data.health && data.health.card_db_ok,
              sync_state: data.health && data.health.sync_state,
            },
            target_playerid: data.target_playerid,
          },
          null,
          2
        );
      }
      const grid = $("#home-packs");
      if (grid) {
        const packs = data.quick_packs || [];
        grid.innerHTML = packs
          .map(
            (p) => `
          <div class="pack-card">
            <span class="cat">${escapeHtml(p.category || "")}</span>
            <h4>${escapeHtml(p.label || p.id)}</h4>
            <p>${escapeHtml(p.description || "")}</p>
            <button type="button" class="btn secondary" data-pack="${escapeHtml(p.id)}">Run pack</button>
          </div>`
          )
          .join("");
        grid.querySelectorAll("[data-pack]").forEach((btn) => {
          btn.addEventListener("click", () => runPack(btn.getAttribute("data-pack"), $("#home-toast")));
        });
      }
    }

    if (name === "cards") {
      const t = $("#cards-target");
      if (t && state.targetId != null) t.value = String(state.targetId);
      $("#cards-search-btn").addEventListener("click", doCardSearch);
      $("#cards-query").addEventListener("keydown", (e) => {
        if (e.key === "Enter") doCardSearch();
      });
      $("#cards-set-target").addEventListener("click", async () => {
        const tid = parseInt($("#cards-target").value, 10);
        if (!tid) {
          setToast($("#cards-toast"), "Enter a target playerid", "err");
          return;
        }
        const { data } = await api("/api/target", { body: { playerid: tid } });
        state.targetId = data.target_playerid;
        updateChrome({ ...(state.status || {}), target_playerid: state.targetId, arm: (state.status && state.status.arm) || {} });
        setToast($("#cards-toast"), "Target locked: " + state.targetId, "ok");
        await refreshStatus();
      });
      $("#cards-prep-apply").addEventListener("click", prepCardApply);
      $("#cards-to-add").addEventListener("click", () => {
        if (!state.selectedCard) {
          setToast($("#cards-toast"), "Select a card first", "err");
          return;
        }
        state.selectedAddCard = state.selectedCard;
        showView("add_team").then(() => {
          setToast($("#add-toast"), "Card loaded from Cards: " + (state.selectedAddCard.name || ""), "ok");
          renderAddSelection();
        });
      });
      renderCardList();
    }

    if (name === "boost") {
      const { data } = await api("/api/packs");
      state.packs = data.packs || [];
      const grid = $("#boost-packs");
      grid.innerHTML = state.packs
        .map(
          (p) => `
        <div class="pack-card">
          <span class="cat">${escapeHtml(p.category || "")}</span>
          <h4>${escapeHtml(p.label || p.id)}</h4>
          <p>${escapeHtml(p.description || "")}</p>
          <button type="button" class="btn secondary" data-pack="${escapeHtml(p.id)}">Run</button>
        </div>`
        )
        .join("");
      grid.querySelectorAll("[data-pack]").forEach((btn) => {
        btn.addEventListener("click", () => runPack(btn.getAttribute("data-pack"), $("#boost-toast")));
      });
    }

    if (name === "add_team") {
      $("#add-search-btn").addEventListener("click", doAddSearch);
      $("#add-run").addEventListener("click", doAddTeam);
      if (state.selectedAddCard) renderAddSelection();
    }

    if (name === "squad") {
      $("#squad-refresh").addEventListener("click", loadSquad);
      $("#squad-export").addEventListener("click", async () => {
        const { data } = await api("/api/squad/export", { body: { wait: false } });
        setToast($("#squad-toast"), JSON.stringify(data, null, 2), data.ok ? "ok" : "err");
        setDock("Export squad: " + (data.outcome || data.reason || ""), false);
        await refreshQueue();
      });
      await loadSquad();
    }

    if (name === "editor") {
      const { data } = await api("/api/editor/presets");
      state.presets = data.presets || [];
      const sel = $("#editor-preset");
      sel.innerHTML = state.presets
        .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.label)}</option>`)
        .join("");
      if (state.targetId != null) $("#editor-target").value = String(state.targetId);
      $("#editor-load").addEventListener("click", async () => {
        const id = sel.value;
        const { data: pr } = await api("/api/editor/preset?id=" + encodeURIComponent(id));
        $("#editor-fields").value = JSON.stringify(pr.fields || {}, null, 2);
        setToast($("#editor-toast"), "Loaded " + (pr.label || id), "ok");
      });
      $("#editor-prep").addEventListener("click", () => {
        let fields;
        try {
          fields = JSON.parse($("#editor-fields").value || "{}");
        } catch (e) {
          setToast($("#editor-toast"), "Invalid JSON: " + e.message, "err");
          return;
        }
        const tid = parseInt($("#editor-target").value, 10) || state.targetId;
        if (!tid) {
          setToast($("#editor-toast"), "Set target playerid", "err");
          return;
        }
        state.applyPayload = {
          kind: "editor",
          label: "Editor → " + tid,
          run: async () => {
            const { data: out } = await api("/api/editor/apply", {
              body: { fields, target_playerid: tid, wait: false },
            });
            return out;
          },
        };
        setDock("Ready: editor fields → " + tid, true);
        setToast($("#editor-toast"), "Prepared Apply on dock", "ok");
      });
    }

    if (name === "catalog") {
      const { data } = await api("/api/catalog");
      $("#catalog-info").textContent = JSON.stringify(data, null, 2);
    }

    if (name === "about") {
      const { data } = await api("/api/about");
      $("#about-info").textContent = JSON.stringify(data, null, 2);
    }
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderCardList() {
    const ul = $("#cards-list");
    if (!ul) return;
    if (!state.cardHits.length) {
      ul.innerHTML = '<li class="empty" style="cursor:default"><strong>No results</strong>Search for a player name</li>';
      return;
    }
    ul.innerHTML = state.cardHits
      .map(
        (c, i) =>
          `<li data-i="${i}" class="${i === state.selectedCardIndex ? "selected" : ""}">${escapeHtml(
            c.line || c.name || "?"
          )}</li>`
      )
      .join("");
    ul.querySelectorAll("li[data-i]").forEach((li) => {
      li.addEventListener("click", () => {
        const i = parseInt(li.getAttribute("data-i"), 10);
        state.selectedCardIndex = i;
        state.selectedCard = state.cardHits[i];
        renderCardList();
        const pre = $("#cards-selected");
        if (pre) pre.textContent = JSON.stringify(state.selectedCard, null, 2);
      });
    });
  }

  async function doCardSearch() {
    const query = $("#cards-query").value.trim();
    const year = $("#cards-year").value.trim() || null;
    const ovrRaw = $("#cards-ovr").value;
    const ovr = ovrRaw === "" ? null : parseInt(ovrRaw, 10);
    setToast($("#cards-toast"), "Searching…", "");
    const { data } = await api("/api/cards/search", {
      method: "POST",
      body: { query, year, ovr, limit: 40 },
    });
    state.cardHits = data.hits || [];
    state.selectedCard = null;
    state.selectedCardIndex = -1;
    renderCardList();
    const pre = $("#cards-selected");
    if (pre) pre.textContent = "None";
    setToast(
      $("#cards-toast"),
      data.count + " hit(s) for " + JSON.stringify(query),
      data.count ? "ok" : ""
    );
  }

  function prepCardApply() {
    if (!state.selectedCard) {
      setToast($("#cards-toast"), "Select a variant first", "err");
      return;
    }
    const tid =
      parseInt($("#cards-target").value, 10) || state.targetId;
    if (!tid) {
      setToast($("#cards-toast"), "Set target playerid", "err");
      return;
    }
    const card = Object.assign({}, state.selectedCard);
    delete card.index;
    delete card.line;
    state.applyPayload = {
      kind: "card",
      label: (card.name || "card") + " → " + tid,
      run: async () => {
        const { data } = await api("/api/cards/apply", {
          body: { card, target_playerid: tid, wait: false },
        });
        return data;
      },
    };
    setDock("Ready: " + state.applyPayload.label, true);
    setToast($("#cards-toast"), "Prepared Apply on dock", "ok");
  }

  async function doAddSearch() {
    const query = $("#add-query").value.trim();
    const year = $("#add-year").value.trim() || null;
    const { data } = await api("/api/cards/search", {
      method: "POST",
      body: { query, year, limit: 30 },
    });
    state.addHits = data.hits || [];
    const ul = $("#add-list");
    ul.innerHTML = state.addHits
      .map(
        (c, i) =>
          `<li data-i="${i}">${escapeHtml(c.line || c.name || "?")}</li>`
      )
      .join("") || '<li class="empty">No hits</li>';
    ul.querySelectorAll("li[data-i]").forEach((li) => {
      li.addEventListener("click", () => {
        const i = parseInt(li.getAttribute("data-i"), 10);
        state.selectedAddCard = state.addHits[i];
        ul.querySelectorAll("li").forEach((x) => x.classList.remove("selected"));
        li.classList.add("selected");
      });
    });
  }

  function renderAddSelection() {
    // noop visual if list empty — toast already set
  }

  async function doAddTeam() {
    if (!state.selectedAddCard) {
      setToast($("#add-toast"), "Select a card", "err");
      return;
    }
    const card = Object.assign({}, state.selectedAddCard);
    delete card.index;
    delete card.line;
    const mode = $("#add-mode").value;
    setToast($("#add-toast"), "Queueing…", "");
    const { data } = await api("/api/add-team", {
      body: { card, mode, wait: false },
    });
    setToast($("#add-toast"), JSON.stringify(data, null, 2), data.ok ? "ok" : "err");
    setDock("Add team: " + (data.outcome || data.reason || ""), false);
    state.lastResult = data.reason || data.outcome || "";
    await refreshQueue();
  }

  async function loadSquad() {
    const { data } = await api("/api/squad");
    state.squad = data;
    const meta = $("#squad-meta");
    if (meta) {
      meta.textContent =
        (data.loaded ? data.teamname || "Squad" : "No export") +
        " · " +
        (data.count || 0) +
        " players · FA " +
        (data.free_agent_count || 0);
    }
    const tbody = $("#squad-table tbody");
    const players = data.players || [];
    if (!players.length) {
      tbody.innerHTML =
        '<tr><td colspan="6" class="empty"><strong>No squad loaded</strong>Queue export while LIVE in Career Mode</td></tr>';
      return;
    }
    tbody.innerHTML = players
      .map((p) => {
        const locked = state.targetId != null && Number(p.playerid) === Number(state.targetId);
        return `<tr data-id="${p.playerid}" class="${locked ? "locked" : ""}">
          <td>${p.playerid}</td>
          <td>${escapeHtml(p.name || "")}</td>
          <td>${escapeHtml(p.position || "")}</td>
          <td>${p.overallrating != null ? p.overallrating : ""}</td>
          <td>${p.potential != null ? p.potential : ""}</td>
          <td>${p.jerseynumber != null ? p.jerseynumber : ""}</td>
        </tr>`;
      })
      .join("");
    tbody.querySelectorAll("tr[data-id]").forEach((tr) => {
      tr.addEventListener("click", async () => {
        const id = parseInt(tr.getAttribute("data-id"), 10);
        const { data: out } = await api("/api/target", { body: { playerid: id } });
        state.targetId = out.target_playerid;
        setToast($("#squad-toast"), "Target locked: " + id, "ok");
        await refreshStatus();
        await loadSquad();
      });
    });
  }

  async function runPack(packId, toastEl) {
    setToast(toastEl, "Running " + packId + "…", "");
    setDock("Pack " + packId + "…", false);
    const { data } = await api("/api/packs/run", {
      body: { pack_id: packId, wait: false },
    });
    setToast(toastEl, JSON.stringify(data, null, 2), data.ok ? "ok" : "err");
    setDock(
      "Pack " + packId + ": " + (data.ok ? "ok" : data.reason || "see results"),
      false
    );
    await refreshQueue();
  }

  async function doApply() {
    if (!state.applyPayload || !state.applyPayload.run) return;
    const btn = $("#btn-apply");
    if (btn) btn.disabled = true;
    setDock("Applying: " + state.applyPayload.label + "…", false);
    try {
      const out = await state.applyPayload.run();
      state.lastResult = out.reason || out.outcome || "";
      setDock(
        (out.ok ? "Queued/Applied: " : "Failed: ") +
          (out.outcome || "") +
          " · " +
          (out.reason || ""),
        false
      );
      state.applyPayload = null;
      await refreshStatus();
      await refreshQueue();
    } catch (e) {
      setDock("Error: " + e.message, false);
    }
  }

  function wireChrome() {
    document.body.addEventListener("click", async (e) => {
      const t = e.target;
      if (!(t instanceof Element)) return;
      const action = t.getAttribute("data-action");
      if (action === "install") {
        const { data } = await api("/api/worker/install", { body: {} });
        const toast = $("#home-toast");
        setToast(toast, JSON.stringify(data, null, 2), data.ok !== false ? "ok" : "err");
        await refreshStatus();
      }
      if (action === "go-live") {
        const { data } = await api("/api/worker/go-live", { body: {} });
        const toast = $("#home-toast");
        setToast(
          toast,
          (data.title || data.state || "") + "\n" + (data.body || JSON.stringify(data)),
          data.state === "already_live" ? "ok" : ""
        );
        await refreshStatus();
      }
      if (action === "copy-bridge") {
        const { data } = await api("/api/worker/copy-bridge", { body: {} });
        setToast($("#home-toast"), data.ok ? "Bridge copied to clipboard" : "Clipboard failed", data.ok ? "ok" : "err");
      }
      const view = t.getAttribute("data-view");
      if (view) showView(view);
    });
    $("#btn-apply").addEventListener("click", doApply);
  }

  async function boot() {
    wireChrome();
    try {
      await refreshStatus();
    } catch (e) {
      $("#status-line").textContent = "API offline: " + e.message;
    }
    await showView("home");
    setInterval(() => {
      refreshStatus().catch(() => {});
    }, 4000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
