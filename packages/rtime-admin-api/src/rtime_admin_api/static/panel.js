// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"use strict";
/*
 * rtime 控制面板客户端逻辑(T7 骨架)。原生 JS fetch,无框架、无构建。
 *
 * 所有 /v1/* 调用经 api() 走 Authorization: Bearer <token>。token 只放
 * sessionStorage(关标签即丢),从不 localStorage。任何要写进 innerHTML 的文本
 * 先过 DOMPurify(不可达时降级 textContent)。
 *
 * 纯函数(schema→表单、diff 组装、字段解析)放在 panel.schema.js,便于单测;
 * 本文件只做 DOM 接线与网络。若该文件未加载(离线打开),回退到内联最小实现。
 */
(function () {
  const $ = (id) => document.getElementById(id);

  // ---- 纯逻辑:优先用可测模块,离线时回退 -------------------------------------
  const S = window.PanelSchema || {};

  // ---- token 状态(仅 sessionStorage)----------------------------------------
  const TOKEN_KEY = "rtime_admin_panel_token";
  let token = sessionStorage.getItem(TOKEN_KEY) || "";
  let schema = null; // {modules: {mod: {properties: {...}}}}
  let currentEtag = null;

  const tokenInput = $("token");
  const whoami = $("whoami");
  tokenInput.value = token;

  // ---- 安全写 HTML(仅用于本页自建、已 esc 的 diff 片段)----------------------
  // 大部分渲染走 textContent(见各 build* 函数)。极少数需要着色的 diff 才走这里,
  // 且内容全部经 esc() 转义后才拼接;DOMPurify 是第二道防线,不可达时降级 <pre>。
  function setHTML(el, html) {
    el.innerHTML = "";
    if (window.DOMPurify) {
      el.innerHTML = window.DOMPurify.sanitize(html);
    } else {
      const pre = document.createElement("pre");
      pre.className = "diff";
      // 去标签,保底纯文本(html 内容已是我们自建且 esc 过的)
      pre.textContent = html.replace(/<[^>]*>/g, "");
      el.appendChild(pre);
    }
  }

  function flash(kind, msg) {
    const el = $("flash");
    el.className = "flash " + kind;
    el.textContent = msg;
    el.classList.remove("hidden");
  }
  function clearFlash() {
    $("flash").classList.add("hidden");
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // ---- 网络:统一鉴权 + 错误映射 --------------------------------------------
  async function api(path, opts) {
    opts = opts || {};
    const headers = Object.assign({}, opts.headers || {});
    if (!token) throw new ApiErr(0, "no_token", "尚未连接:请粘贴 token 并点“连接”。");
    headers["Authorization"] = "Bearer " + token;
    if (opts.body != null && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    let res;
    try {
      res = await fetch(path, { method: opts.method || "GET", headers, body: opts.body });
    } catch (e) {
      throw new ApiErr(0, "network", "网络错误(admin-api 未启动?):" + (e && e.message));
    }
    const etag = res.headers.get("ETag");
    if (etag) currentEtag = etag.replace(/^"|"$/g, "");
    let data = null;
    const text = await res.text();
    if (text) { try { data = JSON.parse(text); } catch (e) { data = { raw: text }; } }
    if (!res.ok) {
      const err = (data && data.error) || {};
      throw new ApiErr(res.status, err.code || "http_" + res.status, err.message || text || ("HTTP " + res.status), err.errors);
    }
    return { data, etag: currentEtag, status: res.status };
  }

  function ApiErr(status, code, message, errors) {
    this.status = status; this.code = code; this.message = message; this.errors = errors;
  }
  function describeErr(e) {
    if (e && e.status === 401) return "401 未授权:token 无效或缺失。";
    if (e && e.status === 403) return "403 权限不足:" + (e.message || ("缺少所需 scope"));
    if (e && e.status === 412) return "412 ETag 冲突:配置在你操作期间被改动。";
    if (e && e.status === 428) return "428:缺少 If-Match(内部错误,请刷新)。";
    return (e && e.message) || String(e);
  }

  // ---- 连接 / whoami --------------------------------------------------------
  async function connect() {
    token = tokenInput.value.trim();
    if (!token) { flash("err", "请先粘贴 token。"); return; }
    sessionStorage.setItem(TOKEN_KEY, token);
    whoami.className = "muted"; whoami.textContent = "连接中…";
    try {
      const { data } = await api("v1/health");
      whoami.className = "ok";
      whoami.textContent = "已连接 · v" + (data.version || "?") +
        (data.needs_restart && data.needs_restart.length ? " · 待重启:" + data.needs_restart.length : "");
      clearFlash();
      // 预取 schema,四视图共用
      schema = (await api("v1/schema")).data;
      loadTree();
    } catch (e) {
      whoami.className = "err";
      whoami.textContent = "连接失败";
      flash("err", describeErr(e));
    }
  }

  function forget() {
    token = ""; tokenInput.value = ""; sessionStorage.removeItem(TOKEN_KEY);
    whoami.className = "muted"; whoami.textContent = "未连接";
    flash("warn", "已从本页清除 token。");
  }

  // ---- 视图切换 -------------------------------------------------------------
  document.querySelectorAll("nav .tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll("nav .tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
      tab.classList.add("active");
      const view = tab.getAttribute("data-view");
      $("view-" + view).classList.add("active");
      if (!token) return;
      if (view === "history") loadHistory();
      else if (view === "audit") { loadAudit(); loadDrift(); }
      else if (view === "modules") loadModules();
      else if (view === "edit" && !$("form-body").dataset.built) loadForm();
    });
  });

  // ============================================================ 配置树 =========
  async function loadTree() {
    const body = $("tree-body");
    const reveal = $("tree-reveal").checked ? 1 : 0;
    try {
      const { data } = await api("v1/config?provenance=1" + (reveal ? "&reveal=1" : ""));
      // J1:同时取 drift(被 profile 遮蔽的 store override),用于标记+unset 按钮。
      let driftPaths = new Set();
      try {
        const dr = await api("v1/config/drift");
        driftPaths = new Set((dr.data.drift || []).map((d) => d.path));
      } catch (e) { /* drift 取不到不阻塞主视图 */ }
      body.innerHTML = "";
      body.appendChild(buildTreeTable(data.values, driftPaths));
    } catch (e) {
      body.textContent = "";
      flash("err", describeErr(e));
    }
  }

  function buildTreeTable(values, driftPaths) {
    driftPaths = driftPaths || new Set();
    // values: {path: {value, provenance}}  (provenance=1)
    const table = document.createElement("table");
    const head = document.createElement("tr");
    head.innerHTML = "<th>路径</th><th>来源</th><th>值</th><th>标记</th><th>操作</th>";
    table.appendChild(head);
    const paths = Object.keys(values).sort();
    for (const path of paths) {
      const cell = values[path] || {};
      const prov = cell.provenance || "default";
      const meta = S.fieldMeta ? S.fieldMeta(schema, path) : fieldMetaLocal(path);
      const shadowed = driftPaths.has(path);
      const tr = document.createElement("tr");
      const tdPath = document.createElement("td"); tdPath.className = "path"; tdPath.textContent = path;
      const tdProv = document.createElement("td");
      const b = document.createElement("span"); b.className = "badge " + prov; b.textContent = prov; tdProv.appendChild(b);
      const tdVal = document.createElement("td"); tdVal.className = "val";
      tdVal.textContent = formatVal(cell.value);
      const tdTag = document.createElement("td");
      if (meta.secret) tdTag.appendChild(badge("secret", "secret"));
      if (meta.reload) tdTag.appendChild(badge(meta.reload, meta.reload));
      // J1:被 profile 遮蔽的 store override 明确标出——消灭"改了 UI 不生效"。
      if (shadowed) tdTag.appendChild(badge("shadowed", "被profile遮蔽"));
      const tdOp = document.createElement("td");
      if (shadowed) {
        const btn = document.createElement("button");
        btn.textContent = "交还上层(unset)";
        btn.className = "unset-btn";
        btn.title = "清掉 store override,值落回 profile/默认层";
        btn.addEventListener("click", () => doUnset(path));
        tdOp.appendChild(btn);
      }
      tr.appendChild(tdPath); tr.appendChild(tdProv); tr.appendChild(tdVal); tr.appendChild(tdTag); tr.appendChild(tdOp);
      table.appendChild(tr);
    }
    return table;
  }

  async function doUnset(path) {
    if (!window.confirm("清掉 " + path + " 的 store override,值将落回 profile/默认层。继续?")) return;
    try {
      if (!currentEtag) await api("v1/config");
      const res = await api("v1/config/" + encodeURIComponent(path), {
        method: "DELETE",
        headers: { "If-Match": '"' + currentEtag + '"' },
      });
      const r = res.data || {};
      let msg = "已交还上层:" + path + "。";
      if (r.restart_required && r.restart_required.length) { msg += " 需重启。"; flash("warn", msg); }
      else flash("ok", msg);
      loadTree();
    } catch (e) {
      if (e.status === 412) { await api("v1/config"); flash("warn", "配置在你操作期间被改动(412),已刷新,请重试。"); }
      else flash("err", describeErr(e));
    }
  }

  function badge(cls, text) {
    const s = document.createElement("span"); s.className = "badge " + cls; s.textContent = text;
    s.style.marginRight = "4px"; return s;
  }
  function formatVal(v) {
    if (v === null || v === undefined) return "(未设置)";
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
  }
  // 离线回退:直接从已取 schema 读 meta
  function fieldMetaLocal(path) {
    const dot = path.indexOf(".");
    const mod = path.slice(0, dot), field = path.slice(dot + 1);
    const prop = ((((schema || {}).modules || {})[mod] || {}).properties || {})[field] || {};
    return { secret: !!prop["x-secret"], reload: prop["x-reload"] || "", scope: prop["x-scope"] || null };
  }

  $("tree-refresh").addEventListener("click", loadTree);
  $("tree-reveal").addEventListener("change", loadTree);

  // ================================================ 编辑 / diff / 提交 =========
  // 表单 100% 由 schema 生成(设计要求):字段描述符来自纯逻辑模块 PanelSchema
  // .fieldDescriptors(),这里只把描述符渲染成控件。绝不手写字段名。
  async function loadForm() {
    const body = $("form-body");
    if (!schema) { flash("err", "schema 未加载,请重新连接。"); return; }
    // 先取当前值(带 provenance),用作各字段的原值/占位。
    let values = {};
    try { values = (await api("v1/config?provenance=1")).data.values; }
    catch (e) { flash("err", describeErr(e)); return; }
    body.innerHTML = "";
    body.appendChild(buildForm(values));
    body.dataset.built = "1";
    $("diff-card").style.display = "none";
  }

  function buildForm(values) {
    const groups = S.fieldDescriptors
      ? S.fieldDescriptors(schema, values)
      : fieldDescriptorsLocal(schema, values);
    const form = document.createElement("div"); form.id = "form-el";
    groups.forEach((g) => {
      const det = document.createElement("details"); det.className = "module"; det.open = true;
      const sum = document.createElement("summary"); sum.textContent = g.module; det.appendChild(sum);
      g.fields.forEach((f) => det.appendChild(buildFieldRow(f)));
      form.appendChild(det);
    });
    return form;
  }

  function buildFieldRow(f) {
    const row = document.createElement("div"); row.className = "field-row";
    const label = document.createElement("label"); label.textContent = f.path; label.title = f.description || "";
    row.appendChild(label);
    const wrap = document.createElement("div"); wrap.className = "ctl";
    const input = makeInput(f);
    input.dataset.path = f.path;
    input.dataset.type = f.type;
    input.dataset.secret = f.secret ? "1" : "";
    input.dataset.orig = JSON.stringify(f.orig == null ? null : f.orig);
    if (f.secret) {
      input.placeholder = "*** 留空=不改";
    } else if (f.enum || f.type === "boolean") {
      if (f.current != null) input.value = String(f.current);
    } else if (f.current != null) {
      input.value = typeof f.current === "object" ? JSON.stringify(f.current) : String(f.current);
    }
    wrap.appendChild(input); row.appendChild(wrap);
    return row;
  }

  function makeInput(f) {
    if (f.enum) {
      const sel = document.createElement("select");
      const blank = document.createElement("option"); blank.value = ""; blank.textContent = "(不改)"; sel.appendChild(blank);
      f.enum.forEach((v) => { const o = document.createElement("option"); o.value = v; o.textContent = v; sel.appendChild(o); });
      return sel;
    }
    if (f.type === "boolean") {
      const sel = document.createElement("select");
      [["", "(不改)"], ["true", "true"], ["false", "false"]].forEach(([v, t]) => {
        const o = document.createElement("option"); o.value = v; o.textContent = t; sel.appendChild(o);
      });
      return sel;
    }
    if (f.type === "array" || f.type === "object") {
      const ta = document.createElement("textarea"); ta.rows = 2; ta.placeholder = "JSON,如 [] 或 {}"; return ta;
    }
    const input = document.createElement("input");
    input.type = (f.type === "integer" || f.type === "number") ? "number" : "text";
    if (f.secret) input.type = "password";
    return input;
  }

  // 读出每个控件为 reading,交纯逻辑 collectChangesFrom 决定哪些真的改了。
  function collectChanges() {
    const readings = [];
    document.querySelectorAll("#form-el [data-path]").forEach((el) => {
      readings.push({
        path: el.dataset.path,
        type: el.dataset.type,
        secret: el.dataset.secret === "1",
        raw: el.value,
        orig: JSON.parse(el.dataset.orig),
      });
    });
    return S.collectChangesFrom ? S.collectChangesFrom(readings) : collectChangesFromLocal(readings);
  }

  // 离线回退(PanelSchema 未加载时):与纯模块语义等价的最小实现。
  function fieldDescriptorsLocal(schema, values) {
    const mods = (schema.modules || {}); const out = [];
    Object.keys(mods).sort().forEach((mod) => {
      const props = mods[mod].properties || {}; const fields = [];
      Object.keys(props).forEach((field) => {
        const path = mod + "." + field; const prop = props[field]; const secret = !!prop["x-secret"];
        const cell = values[path]; const cur = cell && typeof cell === "object" && "value" in cell ? cell.value : cell;
        let type = prop.enum ? "enum" : (prop.type || "string");
        fields.push({ path, type, secret, enum: prop.enum || null, description: prop.description || "",
          current: cur == null ? null : cur, orig: secret ? null : (cur == null ? null : cur) });
      });
      out.push({ module: mod, fields });
    });
    return out;
  }
  function collectChangesFromLocal(readings) {
    const changes = {};
    readings.forEach((r) => {
      if (r.secret) { if (r.raw) changes[r.path] = r.raw; return; }
      let p = r.raw === "" ? null : r.raw;
      if (r.type === "integer") p = p == null ? null : parseInt(p, 10);
      else if (r.type === "number") p = p == null ? null : parseFloat(p);
      else if (r.type === "boolean") p = p == null ? null : (r.raw === "true");
      else if (r.type === "array" || r.type === "object") { try { p = p == null ? null : JSON.parse(p); } catch (e) {} }
      if (JSON.stringify(p) !== JSON.stringify(r.orig)) changes[r.path] = p;
    });
    return changes;
  }

  async function doDiff() {
    const changes = collectChanges();
    if (!Object.keys(changes).length) { flash("warn", "没有检测到改动。"); return; }
    try {
      // J1 dry-run 预览影响:validate 现在一次返回 diff + hot + restart_required
      // (不落盘不审计),作为提交前"预览影响"的单一来源。
      const v = await api("v1/config/validate", { method: "POST", body: JSON.stringify({ changes }) });
      const d = v.data;
      renderDiff(d.diff || {}, false, { hot: d.hot, restart_required: d.restart_required });
      if (!d.ok) {
        flash("err", "校验未通过:" + JSON.stringify(d.errors));
      } else {
        clearFlash();
      }
    } catch (e) { flash("err", describeErr(e)); }
  }

  function renderDiff(diff, applied, impact) {
    const card = $("diff-card"); const body = $("diff-body");
    card.style.display = "block";
    const lines = [];
    for (const path of Object.keys(diff).sort()) {
      const d = diff[path];
      lines.push(esc(path));
      lines.push('  <span class="before">- ' + esc(formatVal(d.before)) + "</span>");
      lines.push('  <span class="after">+ ' + esc(formatVal(d.after)) + "</span>");
    }
    setHTML(body, '<pre class="diff">' + lines.join("\n") + "</pre>");
    // J1 预览影响:提交前告诉运营者这次改动哪些热生效、哪些要重启。
    if (impact && (impact.hot || impact.restart_required)) {
      const hot = impact.hot || [], rr = impact.restart_required || [];
      const p = document.createElement("p"); p.className = "muted";
      const parts = [];
      if (hot.length) parts.push("热生效(" + hot.length + "):" + hot.join(", "));
      if (rr.length) parts.push("⚠️ 需重启(" + rr.length + "):" + rr.join(", "));
      p.textContent = "预览影响 — " + (parts.join(" · ") || "无字段变化");
      body.appendChild(p);
    }
    if (applied) {
      const p = document.createElement("p"); p.className = "muted"; p.textContent = "已应用。";
      body.appendChild(p);
    }
  }

  async function doApply() {
    const changes = collectChanges();
    if (!Object.keys(changes).length) { flash("warn", "没有检测到改动。"); return; }
    try {
      // 先确保有最新 ETag
      if (!currentEtag) await api("v1/config");
      let res;
      try {
        res = await api("v1/config", {
          method: "PATCH",
          headers: { "If-Match": '"' + currentEtag + '"' },
          body: JSON.stringify({ changes }),
        });
      } catch (e) {
        if (e.status === 412) {
          // 重取 ETag 并警告(有人在你操作期间改了)
          await api("v1/config");
          flash("warn", "配置在你操作期间被改动(412)。已刷新 ETag,请重新“预演 diff”确认后再提交。");
          return;
        }
        throw e;
      }
      const r = res.data;
      renderDiff(r.diff, true);
      let msg = "提交成功。";
      if (r.hot && r.hot.length) msg += " 热生效:" + r.hot.join(", ") + "。";
      if (r.restart_required && r.restart_required.length) {
        msg += " 需重启:" + r.restart_required.join(", ") + "。";
        flash("warn", msg);
      } else {
        flash("ok", msg);
      }
      loadForm(); // 重置表单为新值
    } catch (e) {
      if (e.status === 422 && e.errors) {
        flash("err", "校验失败:" + JSON.stringify(e.errors));
      } else {
        flash("err", describeErr(e));
      }
    }
  }

  $("form-load").addEventListener("click", loadForm);
  $("form-diff").addEventListener("click", doDiff);
  $("form-apply").addEventListener("click", doApply);

  // ============================================= 历史 / 快照 / 回滚 =============
  async function loadHistory() {
    const body = $("history-body");
    try {
      const { data } = await api("v1/history");
      const snaps = data.snapshots || [];
      if (!snaps.length) { body.innerHTML = '<p class="placeholder">暂无快照。</p>'; return; }
      body.innerHTML = "";
      const table = document.createElement("table");
      const head = document.createElement("tr");
      head.innerHTML = "<th>快照 id</th><th>时间</th><th>备注</th><th>操作</th>";
      table.appendChild(head);
      snaps.slice().reverse().forEach((s) => {
        const tr = document.createElement("tr");
        const id = s.id || s.snapshot_id || "";
        tr.innerHTML =
          '<td class="path">' + esc(id) + "</td>" +
          "<td>" + esc(s.ts || s.timestamp || "") + "</td>" +
          "<td>" + esc(s.note || "") + "</td>";
        const td = document.createElement("td");
        const btn = document.createElement("button");
        btn.className = "btn danger"; btn.textContent = "回滚到此";
        btn.addEventListener("click", () => confirmRollback(id));
        td.appendChild(btn); tr.appendChild(td);
        table.appendChild(tr);
      });
      body.appendChild(table);
    } catch (e) { flash("err", describeErr(e)); }
  }

  function confirmRollback(snapshotId) {
    confirmDialog("确认回滚到快照 " + snapshotId + " ?\n这会把 store 层恢复到该快照,并记录审计。", async () => {
      try {
        if (!currentEtag) await api("v1/config");
        let res;
        try {
          res = await api("v1/rollback", {
            method: "POST",
            headers: { "If-Match": '"' + currentEtag + '"' },
            body: JSON.stringify({ snapshot_id: snapshotId }),
          });
        } catch (e) {
          if (e.status === 412) {
            await api("v1/config");
            flash("warn", "配置在你操作期间被改动(412)。已刷新 ETag,请重新回滚确认。");
            return;
          }
          throw e;
        }
        const r = res.data;
        let msg = "回滚成功。";
        if (r.restart_required && r.restart_required.length) msg += " 需重启:" + r.restart_required.join(", ") + "。";
        flash(r.restart_required && r.restart_required.length ? "warn" : "ok", msg);
        loadHistory();
      } catch (e) { flash("err", describeErr(e)); }
    });
  }

  $("history-refresh").addEventListener("click", loadHistory);
  $("history-snapshot").addEventListener("click", () => {
    flash("warn", "当前 admin-api 未提供手动打快照端点(POST /v1/snapshots 未实现);快照由每次 apply/rollback 自动生成。");
  });

  // ================================================== 模块总览(K5) ============
  async function loadModules() {
    const body = $("modules-body");
    try {
      const { data } = await api("v1/modules");
      body.innerHTML = "";
      const bar = document.createElement("p");
      bar.appendChild(badge(data.ok ? "hot" : "restart", data.ok ? "doctor: 0 问题" : "doctor: " + data.issues.length + " 问题"));
      bar.appendChild(document.createTextNode(" 共 " + data.total + " 模块  " +
        Object.entries(data.by_kind || {}).map(([k, n]) => k + ":" + n).join("  ")));
      body.appendChild(bar);
      const table = document.createElement("table");
      const head = document.createElement("tr");
      setHTML(head, "<th>模块</th><th>类</th><th>装态</th><th>热插拔</th><th>配置</th><th>文档</th>");
      table.appendChild(head);
      (data.modules || []).forEach((m) => {
        const tr = document.createElement("tr");
        const inst = m.installed === true ? '<span class="badge hot">已装</span>'
          : m.installed === false ? '<span class="badge secret">未装</span>'
          : '<span class="muted">—</span>';
        const cfg = m.config_module
          ? '<button class="btn" data-cfgmod="' + esc(m.config_module) + '">配置 ' + esc(m.config_module) + '</button>'
          : '<span class="muted">无</span>';
        setHTML(tr,
          "<td><b>" + esc(m.id) + "</b><br><span class=\"muted\">" + esc(m.title || "") + "</span></td>" +
          "<td>" + esc(m.kind) + (m.optional ? "" : " <span class=\"muted\">(恒开)</span>") + "</td>" +
          "<td>" + inst + "</td>" +
          "<td>" + esc(m.hot_pluggable || "") + "</td>" +
          "<td>" + cfg + "</td>" +
          "<td>" + (m.docs ? '<code class="inline">' + esc(m.docs) + "</code>" : "") + "</td>");
        table.appendChild(tr);
      });
      body.appendChild(table);
      body.querySelectorAll("button[data-cfgmod]").forEach((b) => {
        b.addEventListener("click", () => {
          document.querySelector('nav .tab[data-view="edit"]').click();
          flash("ok", "已切到编辑表单;在表单中定位模块 “" + b.dataset.cfgmod + "” 的字段段落(字段路径以 " + b.dataset.cfgmod + ". 开头)。");
        });
      });
      if (data.issues && data.issues.length) {
        const card = document.createElement("div");
        card.className = "placeholder";
        setHTML(card, "<b>doctor issues:</b><br>" + data.issues.map((i) =>
          esc(i.module) + " [" + esc(i.code) + "] " + esc(i.detail)).join("<br>"));
        body.appendChild(card);
      }
    } catch (e) {
      if (e.status === 501) body.innerHTML = '<p class="placeholder">本部署未接模块清单(设 RTIME_MODULES_MANIFEST 指向 deploy/modules.json 后重启 admin-api)。</p>';
      else flash("err", describeErr(e));
    }
  }
  $("modules-refresh").addEventListener("click", loadModules);

  // ================================================== 审计 / 漂移 =============
  async function loadAudit() {
    const body = $("audit-body");
    const limit = parseInt($("audit-limit").value, 10) || 50;
    try {
      const { data } = await api("v1/audit?limit=" + limit);
      const entries = data.entries || [];
      if (!entries.length) { body.innerHTML = '<p class="placeholder">暂无审计条目。</p>'; return; }
      body.innerHTML = "";
      entries.slice().reverse().forEach((e) => {
        const div = document.createElement("div");
        div.className = "audit-entry";
        div.style.borderBottom = "1px solid var(--line)"; div.style.padding = "6px 0";
        const line = [e.ts, e.action, e.actor ? "@" + e.actor : "", e.source ? "(" + e.source + ")" : "", e.outcome || ""]
          .filter(Boolean).join("  ");
        div.textContent = line + (e.path ? "  path=" + e.path : "") + (e.snapshot_id ? "  snap=" + e.snapshot_id : "");
        body.appendChild(div);
      });
    } catch (e) { flash("err", describeErr(e)); }
  }

  async function loadDrift() {
    const body = $("drift-body");
    try {
      const { data } = await api("v1/config/drift");
      const items = data.drift || [];
      if (!items.length) { body.innerHTML = '<p class="placeholder">无漂移:store 层没有遮蔽 profile 层的键。</p>'; return; }
      body.innerHTML = "";
      const table = document.createElement("table");
      const head = document.createElement("tr");
      setHTML(head, "<th>path</th><th>store 值(生效)</th><th>profile 值(被遮蔽)</th>");
      table.appendChild(head);
      items.forEach((d) => {
        const tr = document.createElement("tr");
        setHTML(tr, "<td><code class=\"inline\">" + esc(d.path) + "</code></td><td>" +
          esc(formatVal(d.store)) + "</td><td>" + esc(formatVal(d.profile)) + "</td>");
        table.appendChild(tr);
      });
      body.appendChild(table);
    } catch (e) { body.innerHTML = '<p class="placeholder">' + esc(describeErr(e)) + "</p>"; }
  }

  $("audit-refresh").addEventListener("click", loadAudit);

  // ---- 通用确认对话框 -------------------------------------------------------
  function confirmDialog(text, onOk) {
    const dlg = $("confirm-dialog");
    $("confirm-text").textContent = text;
    const ok = $("confirm-ok"), cancel = $("confirm-cancel");
    function cleanup() { ok.removeEventListener("click", okH); cancel.removeEventListener("click", cancelH); }
    function okH() { cleanup(); dlg.close(); onOk(); }
    function cancelH() { cleanup(); dlg.close(); }
    ok.addEventListener("click", okH); cancel.addEventListener("click", cancelH);
    if (dlg.showModal) dlg.showModal(); else if (confirm(text)) onOk();
  }

  // ---- 绑定 -----------------------------------------------------------------
  $("connect").addEventListener("click", connect);
  $("forget").addEventListener("click", forget);
  tokenInput.addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });

  // 自动连接(sessionStorage 里已有 token 时)
  if (token) connect();
})();
