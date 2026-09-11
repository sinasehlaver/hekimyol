/* hekimyol — akış (pathway) editörü.
 *
 * Graph editor = Drawflow (MIT, CDN, no build step). This file owns the mapping
 * between the clinical YAML and the node graph:
 *
 *   question node -> 1 input, one output per option; output i wires to option[i].next
 *   outcome  node -> 1 input, 0 outputs
 *
 * Source of truth is split on purpose:
 *   - topology + canvas positions  -> the Drawflow editor (pulled via syncFromEditor)
 *   - node *content* (text/urgency/…) -> `model` (pushed into the editor on render)
 * Canvas positions are NEVER written into pathways/*.yaml; they go to the
 * pathway_layouts/<slug>.json sidecar so the clinical file stays clean.
 */
(() => {
"use strict";

const $ = s => document.querySelector(s);
const URGENCY = ["acil", "24_saat", "planli"];
const URGENCY_TR = {acil: "ACİL", "24_saat": "24 SAAT", planli: "PLANLI"};

let slug = null;
let model = null;        // the pathway dict
let layout = {};         // nid -> {x, y}
let editor = null;
let nidOf = {};          // drawflow numeric id -> nid
let dfOf = {};           // nid -> drawflow numeric id
let selected = null;     // nid
let quiet = false;       // suppress editor events while (re)rendering
let dirty = false;

const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

function setDirty(v) { dirty = v; $("#dirty").hidden = !v; }
function status(msg, kind) {
  const el = $("#status");
  el.textContent = msg || "";
  el.className = "adm-status" + (kind ? " " + kind : "");
}

// ---------- API ----------
// Auth rides on the signed HttpOnly `hk_admin` cookie the login page set — no
// header to attach here. A 401 means the session expired: back to the login page.
async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    headers: {"content-type": "application/json", ...(opts.headers || {})},
  });
  if (r.status === 401) { location.href = "/admin/login"; throw new Error("oturum sona erdi"); }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || ("HTTP " + r.status));
  return body;
}

// ---------- session ----------
$("#logout").addEventListener("click", async () => {
  try { await fetch("/api/admin/logout", {method: "POST"}); } catch (e) { /* ignore */ }
  location.href = "/admin/login";
});
$("#homeBtn").addEventListener("click", () => { location.href = "/"; });

async function start() {
  try {
    const list = await api("/api/admin/pathways");
    $("#pwSel").innerHTML = list.map(p =>
      `<option value="${esc(p.slug)}">${esc(p.title)} — ${esc(p.slug)} (${p.node_count} düğüm)</option>`
    ).join("");
    boot();
    await open($("#pwSel").value);
  } catch (e) {
    status(e.message, "warn");
  }
}

// ---------- editor bootstrap ----------
function boot() {
  editor = new Drawflow($("#canvas"));
  editor.reroute = true;
  editor.reroute_fix_curvature = true;
  editor.start();

  editor.on("nodeSelected", id => { syncFromEditor(); select(nidOf[id]); });
  editor.on("nodeUnselected", () => select(null));
  editor.on("nodeMoved", () => { syncFromEditor(); setDirty(true); });
  editor.on("nodeRemoved", () => { syncFromEditor(); select(null); render(); setDirty(true); });
  editor.on("connectionRemoved", () => { if (!quiet) { syncFromEditor(); setDirty(true); } });
  editor.on("connectionCreated", d => {
    if (quiet) return;
    // one `next` per option: drop any older connection hanging off the same output
    const n = editor.drawflow.drawflow.Home.data[d.output_id];
    const cons = ((n.outputs || {})[d.output_class] || {}).connections || [];
    cons.slice(0, -1).forEach(c =>
      editor.removeSingleConnection(d.output_id, c.node, d.output_class, c.output));
    syncFromEditor();
    setDirty(true);
    if (selected) select(selected);
  });
}

$("#pwSel").addEventListener("change", async e => {
  if (dirty && !confirm("Kaydedilmemiş değişiklikler var. Yine de değiştirilsin mi?")) {
    e.target.value = slug;
    return;
  }
  await open(e.target.value);
});

async function open(s) {
  status("yükleniyor…");
  const d = await api("/api/admin/pathway/" + encodeURIComponent(s));
  slug = s;
  model = d.parsed;
  layout = d.layout || {};
  if (!Object.keys(layout).length) layout = autoLayout();
  selected = null;
  setDirty(false);
  render();
  select(null);
  status(`${slug} yüklendi — ${Object.keys(model.nodes).length} düğüm, sürüm ${model.version}`);
}

// ---------- layout ----------
function autoLayout() {
  const nodes = model.nodes, seen = {}, out = {};
  const rows = [];
  let layer = [model.start].filter(n => nodes[n]);
  layer.forEach(n => seen[n] = true);
  while (layer.length) {
    rows.push(layer);
    const next = [];
    for (const nid of layer) {
      for (const o of (nodes[nid].options || [])) {
        if (o.next && nodes[o.next] && !seen[o.next]) { seen[o.next] = true; next.push(o.next); }
      }
    }
    layer = next;
  }
  const orphans = Object.keys(nodes).filter(n => !seen[n]);
  if (orphans.length) rows.push(orphans);
  rows.forEach((row, x) => row.forEach((nid, y) => {
    out[nid] = {x: 60 + x * 340, y: 40 + y * 250};
  }));
  return out;
}

// ---------- model -> editor ----------
function nodeBody(nid) {
  const n = model.nodes[nid];
  const isStart = model.start === nid;
  const head = `<div class="nd-head">
      <span class="nd-id">${esc(nid)}</span>
      ${isStart ? '<span class="nd-start">BAŞLANGIÇ</span>' : ""}
    </div>`;
  if (n.type === "question") {
    const opts = (n.options || []).map((o, i) =>
      `<li${o.next ? "" : ' class="unwired"'}><span class="oi">${i + 1}</span>${esc(o.label || "(boş)")}
        ${o.risk_flag ? `<em class="rf">${esc(o.risk_flag)}</em>` : ""}</li>`).join("");
    return `<div class="nd nd-q">${head}
      <p class="nd-text">${esc(n.text || "(soru metni yok)")}</p>
      <ol class="nd-opts">${opts}</ol></div>`;
  }
  const u = URGENCY.includes(n.urgency) ? n.urgency : "planli";
  return `<div class="nd nd-o">${head}
    <span class="nd-urg u-${esc(u)}">${esc(URGENCY_TR[n.urgency] || n.urgency || "?")}</span>
    <p class="nd-dep">${esc(n.department || "(bölüm yok)")}</p>
    <p class="nd-adv">${esc((n.patient_advice || "").slice(0, 110))}</p></div>`;
}

function render() {
  const cx = editor.canvas_x, cy = editor.canvas_y, z = editor.zoom;
  quiet = true;
  editor.clear();
  nidOf = {}; dfOf = {};
  for (const nid of Object.keys(model.nodes)) {
    const n = model.nodes[nid];
    const outs = n.type === "question" ? (n.options || []).length : 0;
    const pos = layout[nid] || {x: 80, y: 80};
    const cls = "hy-node " + (n.type === "question" ? "t-q" : "t-o") +
                (model.start === nid ? " is-start" : "");
    const id = editor.addNode(nid, 1, outs, pos.x, pos.y, cls, {nid}, nodeBody(nid));
    nidOf[id] = nid; dfOf[nid] = id;
  }
  for (const nid of Object.keys(model.nodes)) {
    const n = model.nodes[nid];
    if (n.type !== "question") continue;
    (n.options || []).forEach((o, i) => {
      if (o.next && dfOf[o.next] !== undefined) {
        editor.addConnection(dfOf[nid], dfOf[o.next], "output_" + (i + 1), "input_1");
      }
    });
  }
  editor.canvas_x = cx; editor.canvas_y = cy; editor.zoom = z;
  editor.precanvas.style.transform = `translate(${cx}px, ${cy}px) scale(${z})`;
  quiet = false;
}

function refreshBody(nid) {
  const el = document.querySelector(`#node-${dfOf[nid]} .drawflow_content_node`);
  if (el) el.innerHTML = nodeBody(nid);
  const box = document.getElementById("node-" + dfOf[nid]);
  if (box) box.classList.toggle("is-start", model.start === nid);
}

// ---------- editor -> model (topology + positions) ----------
function syncFromEditor() {
  if (quiet || !editor) return;
  const data = editor.export().drawflow.Home.data;
  const live = {};
  for (const id of Object.keys(data)) {
    const nid = (data[id].data || {}).nid;
    if (!nid || !model.nodes[nid]) continue;
    live[nid] = data[id];
    layout[nid] = {x: data[id].pos_x, y: data[id].pos_y};
  }
  // nodes deleted in the canvas disappear from the model too
  for (const nid of Object.keys(model.nodes)) if (!(nid in live)) delete model.nodes[nid];
  for (const nid of Object.keys(model.nodes)) {
    const n = model.nodes[nid];
    if (n.type !== "question") continue;
    (n.options || []).forEach((o, i) => {
      const c = ((live[nid].outputs || {})["output_" + (i + 1)] || {}).connections || [];
      const tgt = c.length ? (data[c[0].node].data || {}).nid : null;
      o.next = (tgt && model.nodes[tgt]) ? tgt : null;
    });
  }
  if (!model.nodes[model.start]) model.start = Object.keys(model.nodes)[0] || "";
  nidOf = {}; dfOf = {};
  for (const id of Object.keys(data)) {
    const nid = (data[id].data || {}).nid;
    if (nid && model.nodes[nid]) { nidOf[id] = nid; dfOf[nid] = id; }
  }
}

// ---------- toolbar ----------
function freshId(prefix) {
  let i = 1;
  while (model.nodes[prefix + i]) i++;
  return prefix + i;
}

$("#addQ").addEventListener("click", () => {
  syncFromEditor();
  const nid = freshId("q_yeni_");
  model.nodes[nid] = {type: "question", text: "Yeni soru", options: [
    {label: "Evet", next: null}, {label: "Hayır", next: null}]};
  layout[nid] = {x: 60 - editor.canvas_x / editor.zoom + 40, y: 60 - editor.canvas_y / editor.zoom + 40};
  setDirty(true); render(); select(nid);
});

$("#addO").addEventListener("click", () => {
  syncFromEditor();
  const nid = freshId("o_yeni_");
  model.nodes[nid] = {type: "outcome", urgency: "planli", department: "",
                      patient_advice: "", bring: [], differential: []};
  layout[nid] = {x: 60 - editor.canvas_x / editor.zoom + 40, y: 60 - editor.canvas_y / editor.zoom + 220};
  setDirty(true); render(); select(nid);
});

$("#delN").addEventListener("click", () => {
  if (!selected) return status("Önce bir düğüm seçin.", "warn");
  if (!confirm(`'${selected}' düğümü silinsin mi? Ona giden bağlantılar boşa düşer.`)) return;
  syncFromEditor();
  const gone = selected;
  delete model.nodes[gone];
  delete layout[gone];
  for (const n of Object.values(model.nodes)) {
    (n.options || []).forEach(o => { if (o.next === gone) o.next = null; });
  }
  if (model.start === gone) model.start = Object.keys(model.nodes)[0] || "";
  selected = null;
  setDirty(true); render(); select(null);
});

$("#mkStart").addEventListener("click", () => {
  if (!selected) return status("Önce bir düğüm seçin.", "warn");
  if (model.nodes[selected].type !== "question") {
    return status("Başlangıç bir soru düğümü olmalıdır.", "warn");
  }
  syncFromEditor();
  model.start = selected;
  setDirty(true); render(); select(selected);
  status(`Başlangıç düğümü: ${selected}`);
});

$("#relayout").addEventListener("click", () => {
  syncFromEditor();
  layout = autoLayout();
  setDirty(true); render(); select(selected);
});

// ---------- side panel ----------
function select(nid) {
  selected = nid && model.nodes[nid] ? nid : null;
  $("#sideEmpty").hidden = !!selected;
  $("#sideForm").hidden = !selected;
  if (!selected) { $("#sideForm").innerHTML = ""; return; }
  $("#sideForm").innerHTML = formHtml(selected);
  wireForm(selected);
}

function listHtml(field, items) {
  return `<div class="sl" data-list="${field}">` + items.map((v, i) =>
    `<div class="sl-row"><input type="text" data-list="${field}" data-i="${i}" value="${esc(v)}">
      <button class="tb mini danger" data-rm-list="${field}" data-i="${i}" title="Kaldır">×</button></div>`
  ).join("") + `<button class="tb mini" data-add-list="${field}">+ Ekle</button></div>`;
}

function formHtml(nid) {
  const n = model.nodes[nid];
  const isStart = model.start === nid;
  let h = `<div class="sf-head">
      <span class="sf-kind ${n.type === "question" ? "k-q" : "k-o"}">${n.type === "question" ? "Soru" : "Sonuç"}</span>
      ${isStart ? '<span class="nd-start">BAŞLANGIÇ</span>' : ""}
    </div>
    <label class="sf-l">Düğüm kimliği (id)</label>
    <input type="text" id="fId" value="${esc(nid)}" spellcheck="false">
    <div class="err" id="fIdErr"></div>`;

  if (n.type === "question") {
    h += `<label class="sf-l">Soru metni</label>
      <textarea id="fText">${esc(n.text || "")}</textarea>
      <label class="sf-l">Seçenekler <span class="hint">(her seçenek bir çıkış portu)</span></label>`;
    h += (n.options || []).map((o, i) => `
      <div class="opt-row">
        <div class="opt-n">${i + 1}</div>
        <div class="opt-f">
          <input type="text" data-opt="label" data-i="${i}" value="${esc(o.label || "")}" placeholder="Seçenek metni">
          <input type="text" data-opt="risk_flag" data-i="${i}" value="${esc(o.risk_flag || "")}" placeholder="risk_flag (isteğe bağlı)">
          <p class="opt-next">${o.next ? "→ " + esc(o.next) : '<em class="unwired">bağlantı yok</em>'}</p>
        </div>
        <button class="tb mini danger" data-rm-opt="${i}" title="Seçeneği kaldır">×</button>
      </div>`).join("");
    h += `<button class="tb mini" id="addOpt">+ Seçenek</button>`;
  } else {
    h += `<label class="sf-l">Aciliyet</label>
      <select id="fUrg" class="adm-select">${URGENCY.map(u =>
        `<option value="${u}"${n.urgency === u ? " selected" : ""}>${URGENCY_TR[u]} (${u})</option>`).join("")}</select>
      <label class="sf-l">Bölüm</label>
      <input type="text" id="fDep" value="${esc(n.department || "")}">
      <label class="sf-l">Hastaya tavsiye</label>
      <textarea id="fAdv">${esc(n.patient_advice || "")}</textarea>
      <label class="sf-l">Yanında getirilecekler (bring)</label>
      ${listHtml("bring", n.bring || [])}
      <label class="sf-l">Ayırıcı tanı (differential)</label>
      ${listHtml("differential", n.differential || [])}`;
  }
  return h;
}

function wireForm(nid) {
  const n = model.nodes[nid];
  const form = $("#sideForm");
  const touch = () => { setDirty(true); refreshBody(nid); };

  $("#fId").addEventListener("change", e => renameNode(nid, e.target.value.trim()));

  if (n.type === "question") {
    $("#fText").addEventListener("input", e => { n.text = e.target.value; touch(); });
    form.querySelectorAll("[data-opt]").forEach(inp => inp.addEventListener("input", e => {
      const o = n.options[+e.target.dataset.i];
      const f = e.target.dataset.opt;
      if (f === "risk_flag" && !e.target.value.trim()) delete o.risk_flag;
      else o[f] = e.target.value;
      touch();
    }));
    form.querySelectorAll("[data-rm-opt]").forEach(b => b.addEventListener("click", e => {
      syncFromEditor();
      model.nodes[nid].options.splice(+e.currentTarget.dataset.rmOpt, 1);
      setDirty(true); render(); select(nid);   // port count changed -> re-render
    }));
    $("#addOpt").addEventListener("click", () => {
      syncFromEditor();
      model.nodes[nid].options.push({label: "Yeni seçenek", next: null});
      setDirty(true); render(); select(nid);
    });
  } else {
    $("#fUrg").addEventListener("change", e => { n.urgency = e.target.value; touch(); });
    $("#fDep").addEventListener("input", e => { n.department = e.target.value; touch(); });
    $("#fAdv").addEventListener("input", e => { n.patient_advice = e.target.value; touch(); });
    form.querySelectorAll("input[data-list]").forEach(inp => inp.addEventListener("input", e => {
      n[e.target.dataset.list][+e.target.dataset.i] = e.target.value;
      touch();
    }));
    form.querySelectorAll("[data-rm-list]").forEach(b => b.addEventListener("click", e => {
      n[e.currentTarget.dataset.rmList].splice(+e.currentTarget.dataset.i, 1);
      setDirty(true); refreshBody(nid); select(nid);
    }));
    form.querySelectorAll("[data-add-list]").forEach(b => b.addEventListener("click", e => {
      const f = e.currentTarget.dataset.addList;
      (n[f] = n[f] || []).push("");
      setDirty(true); select(nid);
    }));
  }
}

function renameNode(oldId, newId) {
  const err = $("#fIdErr");
  err.textContent = "";
  if (!newId || newId === oldId) { $("#fId").value = oldId; return; }
  if (!/^[a-z0-9_]+$/.test(newId)) {
    err.textContent = "Kimlik yalnızca küçük harf, rakam ve _ içerebilir.";
    return;
  }
  if (model.nodes[newId]) { err.textContent = `'${newId}' zaten kullanılıyor.`; return; }
  syncFromEditor();
  // rebuild in place so key order (and therefore YAML order) is preserved
  const rebuilt = {};
  for (const [k, v] of Object.entries(model.nodes)) rebuilt[k === oldId ? newId : k] = v;
  model.nodes = rebuilt;
  for (const nd of Object.values(model.nodes)) {
    (nd.options || []).forEach(o => { if (o.next === oldId) o.next = newId; });
  }
  if (model.start === oldId) model.start = newId;
  layout[newId] = layout[oldId]; delete layout[oldId];
  setDirty(true); render(); select(newId);
}

// ---------- save (typed-slug safeguard) ----------
function payload() {
  syncFromEditor();
  const nodes = {};
  for (const [nid, n] of Object.entries(model.nodes)) {
    if (n.type === "question") {
      nodes[nid] = {type: "question", text: (n.text || "").trim(),
        options: (n.options || []).map(o => {
          const out = {label: (o.label || "").trim(), next: o.next};
          if (o.risk_flag) out.risk_flag = String(o.risk_flag).trim();
          return out;
        })};
    } else {
      const out = {type: "outcome", urgency: n.urgency,
        department: (n.department || "").trim(),
        patient_advice: (n.patient_advice || "").trim()};
      const bring = (n.bring || []).map(s => s.trim()).filter(Boolean);
      const diff = (n.differential || []).map(s => s.trim()).filter(Boolean);
      if (bring.length) out.bring = bring;
      if (diff.length) out.differential = diff;
      nodes[nid] = out;
    }
  }
  return {slug: model.slug, title: model.title, version: model.version,
          source: model.source, start: model.start, nodes};
}

let pending = null;

$("#save").addEventListener("click", () => {
  pending = payload();
  const unwired = Object.entries(pending.nodes).flatMap(([nid, n]) =>
    (n.options || []).filter(o => !o.next).map(() => nid));
  $("#mSlug").textContent = slug;
  $("#mSummary").textContent =
    `${Object.keys(pending.nodes).length} düğüm · başlangıç: ${pending.start}` +
    (unwired.length ? ` · ${unwired.length} bağlanmamış seçenek (sunucu reddedecek)` : "");
  $("#mConfirm").value = "";
  $("#mErr").textContent = "";
  $("#mGo").disabled = true;
  $("#modal").hidden = false;
  $("#mConfirm").focus();
});

$("#mConfirm").addEventListener("input", e => { $("#mGo").disabled = e.target.value !== slug; });
$("#mConfirm").addEventListener("keydown", e => {
  if (e.key === "Enter" && !$("#mGo").disabled) $("#mGo").click();
});
$("#mCancel").addEventListener("click", () => { $("#modal").hidden = true; });
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") $("#modal").hidden = true; });

$("#mGo").addEventListener("click", async () => {
  const btn = $("#mGo");
  btn.classList.add("loading"); btn.disabled = true;
  $("#mErr").textContent = "";
  try {
    const d = await api("/api/admin/pathway/" + encodeURIComponent(slug), {
      method: "PUT",
      body: JSON.stringify({parsed: pending, layout, confirm: $("#mConfirm").value}),
    });
    $("#modal").hidden = true;
    // reload from what the server actually has on disk
    model = d.parsed;
    layout = d.layout || layout;
    setDirty(false);
    render(); select(null);
    status(`Kaydedildi ✓ yedek: pathway_backups/${d.backup}`, "ok");
  } catch (e) {
    $("#mErr").textContent = e.message;   // server-side validation surfaces here
  } finally {
    btn.classList.remove("loading"); btn.disabled = $("#mConfirm").value !== slug;
  }
});

window.addEventListener("beforeunload", e => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });

start();
})();
