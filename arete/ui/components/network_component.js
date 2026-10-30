// Network: a web of the people the user knows, as a tree with cross-links.
//
// Python passes { people: [{id, name, description, worksFor, groupId}],
//                 links: [{a, b, reason}], groups: [{id, name, colour, parent_id}] }.
//
// The tree: "You" in the middle -> a circle per group -> circles for its
// sub-groups -> the people in each (ungrouped people hang off "You").
// Only groups with someone in them (or below them) are drawn. People who know
// each other are joined by solid lines across the tree.
//
// The layout is a small force simulation (no libraries, so it works offline):
// everything pushes everything else apart, tree and link lines pull together.
// Drag the background to pan, scroll to zoom, drag a circle to move it (it
// stays where it's dropped), click one for its card. Cards have buttons that
// send an "edit" trigger: {kind: "person" | "group", id}, {kind: "new_person",
// group}, or {kind: "new_group", parent}; the page opens the matching dialog.

const ME = "__me";
const HUB = "g:";                     // hub node ids are "g:<group id>"

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2);

// sessionStorage can be unavailable; the web works without it.
function remember(key, value) {
  try { sessionStorage.setItem(key, JSON.stringify(value)); } catch (e) {}
}
function recall(key) {
  try { return JSON.parse(sessionStorage.getItem(key)); } catch (e) { return null; }
}

const instances = new WeakMap();

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const root = parentElement.querySelector(".nw-root");
  let web = instances.get(root);
  if (!web) {
    web = new Web(root);
    instances.set(root, web);
  }
  web.edit = (value) => setTriggerValue("edit", { ...value, nonce: uid() });
  web.update(data);
  return () => web.detach();
}

class Web {
  constructor(root) {
    this.root = root;
    this.pos = new Map(Object.entries(recall("nw-pos2") || {}));   // node id -> {x, y, vx, vy, fixed}
    this.view = recall("nw-view") || { x: 0, y: 0, k: 1 };          // pan (centre offset) and zoom
    this.hidden = new Set(recall("nw-hidden") || []);               // top-level group ids switched off
    this.selected = null;                                           // a person id or a hub id
    this.query = "";
    this.alpha = 0;
    this.build();
  }

  // ---------- Skeleton (built once) ----------

  build() {
    this.root.innerHTML = `
      <div class="nw-bar">
        <input class="nw-search" type="search" placeholder="Find someone…" aria-label="Find someone">
        <div class="nw-legend"></div>
      </div>
      <div class="nw-stage">
        <svg class="nw-svg" role="group" aria-label="Your network: Tab to each person or group, Enter for details"><g class="nw-world"><g class="nw-edges"></g><g class="nw-nodes"></g></g></svg>
        <div class="nw-empty" hidden>
          <b>No one here yet.</b><br>Tell Arete about someone, e.g.
          <i>"Add Sam, a friend from uni who works at a bank, and Alex, who knows Sam from football."</i>
        </div>
        <div class="nw-card" hidden role="region" aria-live="polite" aria-label="Details"></div>
        <div class="nw-zoom">
          <button data-zoom="1.25" title="Zoom in">+</button>
          <button data-zoom="0.8" title="Zoom out">−</button>
          <button data-zoom="fit" title="Fit everyone in">⤢</button>
        </div>
      </div>`;
    const $ = (s) => this.root.querySelector(s);
    this.svg = $(".nw-svg");
    this.world = $(".nw-world");
    this.edgesEl = $(".nw-edges");
    this.nodesEl = $(".nw-nodes");
    this.card = $(".nw-card");
    this.legend = $(".nw-legend");
    this.empty = $(".nw-empty");
    this.search = $(".nw-search");
    this.bindEvents();
  }

  // ---------- Data -> nodes and edges ----------

  update(data) {
    this.data = data;
    this.groups = new Map(data.groups.map((g) => [g.id, g]));
    this.people = new Map(data.people.map((p) => [p.id, p]));
    for (const g of data.groups) if (!this.groups.has(g.parent_id)) g.parent_id = null;

    // How many people are in each group, counting its sub-groups too.
    this.count = new Map();
    for (const p of data.people) {
      for (let g = this.groups.get(p.groupId); g; g = this.groups.get(g.parent_id)) {
        this.count.set(g.id, (this.count.get(g.id) || 0) + 1);
      }
    }

    // Tree parents: person -> its group's hub (or You); hub -> its parent group's hub (or You).
    this.parent = new Map();
    for (const g of data.groups) {
      if (this.count.get(g.id)) this.parent.set(HUB + g.id, g.parent_id ? HUB + g.parent_id : ME);
    }
    for (const p of data.people) this.parent.set(p.id, this.groups.has(p.groupId) ? HUB + p.groupId : ME);

    this.neighbours = new Map(data.people.map((p) => [p.id, []]));
    for (const l of data.links) {
      if (!this.people.has(l.a) || !this.people.has(l.b)) continue;
      this.neighbours.get(l.a).push({ id: l.b, reason: l.reason });
      this.neighbours.get(l.b).push({ id: l.a, reason: l.reason });
    }
    if (this.selected && !this.parent.has(this.selected)) this.selected = null;

    // New nodes start next to their tree parent, so the web grows calmly.
    const ids = [...this.parent.keys()];
    const placeNear = (id) => {
      if (this.pos.has(id)) return this.pos.get(id);
      const up = this.parent.get(id);
      const base = up === ME ? { x: 0, y: 0 } : placeNear(up);
      const angle = Math.random() * 2 * Math.PI, r = up === ME ? 160 : 60;
      const p = { x: base.x + r * Math.cos(angle), y: base.y + r * Math.sin(angle), vx: 0, vy: 0 };
      this.pos.set(id, p);
      return p;
    };
    this.pos.set(ME, { x: 0, y: 0, vx: 0, vy: 0, fixed: true });
    ids.forEach(placeNear);
    for (const id of [...this.pos.keys()]) if (id !== ME && !this.parent.has(id)) this.pos.delete(id);

    const key = JSON.stringify([data.people, data.links, data.groups]);
    if (key !== this.drawnFor) {
      const first = !this.drawnFor;
      this.drawnFor = key;
      this.draw();
      // Work the layout out before showing it, then (first time) fit everything in view.
      this.alpha = 1;
      for (let i = 0; i < 400 && this.alpha > 0.02; i++) this.step();
      if (first && !recall("nw-view")) requestAnimationFrame(() => this.fit());
      this.place();
      this.reheat(0.1);
    }
    this.renderLegend();
    this.highlight();
    this.renderCard();
    this.attach();
  }

  isHub(id) {
    return id.startsWith(HUB);
  }

  // The top-level group a node sits under (for colours and the legend), or null.
  topGroup(id) {
    let g = this.isHub(id) ? this.groups.get(id.slice(HUB.length)) : this.groups.get((this.people.get(id) || {}).groupId);
    while (g && g.parent_id) g = this.groups.get(g.parent_id);
    return g || null;
  }

  visible(id) {
    if (id === ME) return true;
    const top = this.topGroup(id);
    return !(top && this.hidden.has(top.id));
  }

  // A node's own group colour (sub-groups can have their own).
  colour(id) {
    const g = this.isHub(id) ? this.groups.get(id.slice(HUB.length)) : this.groups.get((this.people.get(id) || {}).groupId);
    return g ? g.colour : "#9aa0a6";
  }

  radius(id) {
    if (id === ME) return 16;
    if (this.isHub(id)) {
      const g = this.groups.get(id.slice(HUB.length));
      return Math.min(46, Math.max(g.parent_id ? 20 : 26, g.name.length * 3.6 + 10));
    }
    return 9 + 2.2 * Math.sqrt(this.neighbours.get(id).length);
  }

  // ---------- Drawing ----------

  draw() {
    this.empty.hidden = this.data.people.length > 0;
    const tree = [...this.parent.entries()].map(([child, up]) => ({ a: up, b: child, kind: "tree" }));
    const links = this.data.links
      .filter((l) => this.people.has(l.a) && this.people.has(l.b))
      .map((l) => ({ ...l, kind: "link" }));
    this.edges = tree.concat(links);
    this.edgesEl.innerHTML = this.edges.map((e) => {
      const tint = e.kind === "tree" && e.a !== ME ? ` style="stroke:${esc(this.colour(e.a))}"` : "";
      return `<line class="nw-edge ${e.kind}${e.a === ME ? " me" : ""}"${tint}><title>${esc(e.reason || "")}</title></line>`;
    }).join("");

    const hubs = [...this.parent.keys()].filter((id) => this.isHub(id));
    const hubSvg = (id) => {
      const g = this.groups.get(id.slice(HUB.length));
      const count = this.count.get(g.id) || 0;
      return `<g class="nw-node hub${g.parent_id ? " sub" : ""}" data-id="${esc(id)}" tabindex="0" role="button"
        aria-label="Group ${esc(g.name)}, ${count} ${count === 1 ? "person" : "people"}">
        <circle r="${this.radius(id)}" style="fill:${esc(g.colour)};stroke:${esc(g.colour)}"></circle>
        <text dy="4">${esc(g.name)}</text></g>`;
    };
    const personSvg = (p) => `<g class="nw-node person" data-id="${esc(p.id)}" tabindex="0" role="button"
        aria-label="${esc(p.name)}${p.worksFor ? `, works for ${esc(p.worksFor)}` : ""}">
        <circle r="${this.radius(p.id)}" fill="${esc(this.colour(p.id))}"></circle>
        <text dy="${this.radius(p.id) + 14}">${esc(p.name)}</text></g>`;
    this.nodesEl.innerHTML =
      `<g class="nw-node me" data-id="${ME}"><circle r="${this.radius(ME)}"></circle><text dy="${this.radius(ME) + 15}">You</text></g>`
      + hubs.map(hubSvg).join("") + this.data.people.map(personSvg).join("");
    this.edgeEls = [...this.edgesEl.children];
    this.nodeEls = new Map([...this.nodesEl.children].map((g) => [g.dataset.id, g]));
    this.applyHidden();
    this.place();
  }

  applyHidden() {
    for (const [id, g] of this.nodeEls) g.style.display = this.visible(id) ? "" : "none";
    this.edges.forEach((e, i) => {
      this.edgeEls[i].style.display = this.visible(e.a) && this.visible(e.b) ? "" : "none";
    });
  }

  // Move everything to its current position and apply pan/zoom.
  place() {
    const { width, height } = this.svg.getBoundingClientRect();
    const v = this.view;
    this.world.setAttribute("transform", `translate(${width / 2 + v.x},${height / 2 + v.y}) scale(${v.k})`);
    // Zoomed out, labels grow to stay readable (up to a point).
    this.world.style.setProperty("--label", Math.min(1.8, Math.max(1, 1 / v.k)));
    this.edges.forEach((e, i) => {
      const a = this.pos.get(e.a), b = this.pos.get(e.b), el = this.edgeEls[i];
      el.setAttribute("x1", a.x); el.setAttribute("y1", a.y);
      el.setAttribute("x2", b.x); el.setAttribute("y2", b.y);
    });
    for (const [id, g] of this.nodeEls) {
      const p = this.pos.get(id);
      g.setAttribute("transform", `translate(${p.x},${p.y})`);
    }
  }

  // ---------- The force simulation ----------

  reheat(alpha = 0.4) {
    this.alpha = Math.max(this.alpha, alpha);
    if (!this.running && this.attached) {
      this.running = true;
      requestAnimationFrame(() => this.tick());
    }
  }

  tick() {
    if (!this.attached) { this.running = false; return; }
    this.step();
    this.place();
    if (this.alpha > 0.02) requestAnimationFrame(() => this.tick());
    else { this.running = false; this.savePositions(); }
  }

  // How long each kind of line likes to be, and how firmly it pulls.
  spring(e) {
    if (e.kind === "link") return [100, 0.02];
    const child = e.b;
    if (this.isHub(child)) return e.a === ME ? [140, 0.1] : [95, 0.1];
    return e.a === ME ? [160, 0.06] : [55, 0.12];
  }

  // One step of the simulation (alpha cools it down, so it settles).
  step() {
    const ids = [ME, ...this.parent.keys()].filter((id) => this.visible(id));
    const nodes = ids.map((id) => this.pos.get(id));
    const charge = ids.map((id) => (this.isHub(id) ? 1.8 : id === ME ? 2.5 : 1));
    const a = this.alpha;

    // Everything pushes everything else away (hubs push harder, to make room for their people).
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const n = nodes[i], m = nodes[j];
        let dx = m.x - n.x, dy = m.y - n.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
        const f = (170 * charge[i] * charge[j] * a) / d2;
        n.vx -= dx * f; n.vy -= dy * f;
        m.vx += dx * f; m.vy += dy * f;
      }
    }
    const shown = new Set(ids);
    for (const e of this.edges) {
      if (!shown.has(e.a) || !shown.has(e.b)) continue;
      const n = this.pos.get(e.a), m = this.pos.get(e.b);
      const dx = m.x - n.x, dy = m.y - n.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const [len, k] = this.spring(e);
      const f = ((d - len) / d) * k * a;
      n.vx += dx * f; n.vy += dy * f;
      m.vx -= dx * f; m.vy -= dy * f;
    }
    for (const n of nodes) {
      if (n.fixed) { n.vx = n.vy = 0; continue; }
      n.vx -= n.x * 0.006 * a;              // a weak pull to the middle
      n.vy -= n.y * 0.006 * a;
      n.vx *= 0.7; n.vy *= 0.7;
      const speed = Math.hypot(n.vx, n.vy);
      if (speed > 25) { n.vx *= 25 / speed; n.vy *= 25 / speed; }   // no flying off
      n.x += n.vx; n.y += n.vy;
    }
    this.alpha *= 0.985;
  }

  savePositions() {
    const out = {};
    for (const [id, p] of this.pos) if (id !== ME) out[id] = { x: p.x, y: p.y, vx: 0, vy: 0, fixed: !!p.fixed };
    remember("nw-pos2", out);
  }

  // ---------- Highlighting, card, legend ----------

  // A node and everything above it in the tree, up to You.
  ancestors(id) {
    const out = [];
    for (let x = id; x; x = this.parent.get(x)) out.push(x);
    return out.concat(ME);
  }

  // Everything below a hub: its sub-group hubs and all their people.
  below(hubId) {
    return [...this.parent.keys()].filter((id) => id !== hubId && this.ancestors(id).includes(hubId));
  }

  highlight() {
    const q = this.query.trim().toLowerCase();
    const focus = this.selected;
    let near = new Set();
    if (focus && this.isHub(focus)) near = new Set([...this.ancestors(focus), ...this.below(focus)]);
    else if (focus) near = new Set([...this.ancestors(focus), ...this.neighbours.get(focus).map((n) => n.id)]);
    for (const [id, g] of this.nodeEls) {
      const p = this.people.get(id);
      const match = q && p && p.name.toLowerCase().includes(q);
      const dim = (focus && !near.has(id)) || (q && !match && id !== ME);
      g.classList.toggle("dim", !!dim);
      g.classList.toggle("sel", id === focus);
      g.classList.toggle("match", !!match);
    }
    this.edges.forEach((e, i) => {
      const lit = focus && near.has(e.a) && near.has(e.b) && (e.kind === "tree" || e.a === focus || e.b === focus);
      this.edgeEls[i].classList.toggle("dim", !!((focus && !lit) || q));
      this.edgeEls[i].classList.toggle("lit", !!(lit && e.kind === "link"));
    });
  }

  chip(groupId) {
    const g = this.groups.get(groupId);
    if (!g) return "";
    const path = [];
    for (let x = g; x; x = this.groups.get(x.parent_id)) path.unshift(x.name);
    return `<button class="nw-chip" data-go="${HUB + esc(g.id)}"><i style="background:${esc(g.colour)}"></i>${esc(path.join(" › "))}</button>`;
  }

  personItem(id, extra = "") {
    const p = this.people.get(id);
    return `<li><button data-go="${esc(id)}"><i style="background:${esc(this.colour(id))}"></i>${esc(p.name)}</button>${extra}</li>`;
  }

  renderCard() {
    const id = this.selected;
    this.card.hidden = !id;
    if (!id) return;
    if (this.isHub(id)) return this.renderGroupCard(id.slice(HUB.length));
    const p = this.people.get(id);
    const links = this.neighbours.get(id)
      .map((n) => ({ ...n, name: this.people.get(n.id).name }))
      .sort((x, y) => x.name.localeCompare(y.name));
    this.card.innerHTML = `
      <button class="nw-close" title="Close">×</button>
      <div class="nw-name">${esc(p.name)}</div>
      ${this.chip(p.groupId)}
      ${p.worksFor ? `<div class="nw-row"><span>Works for</span>${esc(p.worksFor)}</div>` : ""}
      ${p.description ? `<p class="nw-desc">${esc(p.description)}</p>` : ""}
      <div class="nw-sub">Connections (${links.length})</div>
      ${links.length ? `<ul class="nw-links">${links.map((l) => this.personItem(l.id, l.reason ? `<span>${esc(l.reason)}</span>` : "")).join("")}</ul>`
        : `<div class="nw-none">No connections yet.</div>`}
      <div class="nw-actions"><button class="primary" data-edit="person">Edit</button></div>`;
  }

  renderGroupCard(gid) {
    const g = this.groups.get(gid);
    const subs = this.data.groups.filter((x) => x.parent_id === gid);
    const members = this.data.people.filter((p) => p.groupId === gid);
    const total = this.count.get(gid) || 0;
    this.card.innerHTML = `
      <button class="nw-close" title="Close">×</button>
      <div class="nw-name">${esc(g.name)}</div>
      ${g.parent_id ? this.chip(g.parent_id) : ""}
      <div class="nw-row"><span>People</span>${total}${total !== members.length ? ` (${members.length} directly)` : ""}</div>
      ${subs.length ? `<div class="nw-sub">Sub-groups (${subs.length})</div><ul class="nw-links">${subs.map((s) => `
        <li><button data-go="${this.count.get(s.id) ? HUB + esc(s.id) : ""}" ${this.count.get(s.id) ? "" : "disabled"}>
        <i style="background:${esc(s.colour)}"></i>${esc(s.name)}</button><span>${this.count.get(s.id) || 0} people</span></li>`).join("")}</ul>` : ""}
      ${members.length ? `<div class="nw-sub">In ${esc(g.name)}</div><ul class="nw-links">${members.map((p) => this.personItem(p.id)).join("")}</ul>` : ""}
      <div class="nw-actions">
        <button data-edit="new_person">Add person here</button>
        <button data-edit="new_group">Add sub-group</button>
        <button class="primary" data-edit="group">Edit</button>
      </div>`;
  }

  renderLegend() {
    const tops = this.data.groups.filter((g) => !g.parent_id);
    this.legend.innerHTML = tops
      .map((g) => `<button class="nw-key${this.hidden.has(g.id) ? " off" : ""}" data-group="${esc(g.id)}"
        title="Show or hide ${esc(g.name)}"><i style="background:${esc(g.colour)}"></i>${esc(g.name)}
        <small>${this.count.get(g.id) || 0}</small></button>`)
      .join("");
  }

  choose(id, { centre = false } = {}) {
    this.selected = id;
    this.highlight();
    this.renderCard();
    if (centre && id && this.pos.has(id)) {
      const p = this.pos.get(id);
      this.view.x = -p.x * this.view.k;
      this.view.y = -p.y * this.view.k;
      this.place();
      this.saveView();
    }
  }

  fit() {
    const shown = [ME, ...this.parent.keys()].filter((id) => this.visible(id)).map((id) => this.pos.get(id));
    const xs = shown.map((p) => p.x), ys = shown.map((p) => p.y);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const { width, height } = this.svg.getBoundingClientRect();
    const k = Math.min(1.6, Math.max(0.2, Math.min(width / (x1 - x0 + 180), height / (y1 - y0 + 180))));
    this.view = { x: -((x0 + x1) / 2) * k, y: -((y0 + y1) / 2) * k, k };
    this.place();
    this.saveView();
  }

  saveView() {
    remember("nw-view", this.view);
  }

  // ---------- Events ----------

  bindEvents() {
    // Pan, dragging a circle and clicking share one pointer handler.
    this.svg.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      const g = e.target.closest(".nw-node");
      this.press = { id: g && g.dataset.id, x0: e.clientX, y0: e.clientY, moved: false, view: { ...this.view } };
      this.svg.setPointerCapture(e.pointerId);
    });
    this.svg.addEventListener("pointermove", (e) => {
      const pr = this.press;
      if (!pr) return;
      const dx = e.clientX - pr.x0, dy = e.clientY - pr.y0;
      if (!pr.moved && Math.hypot(dx, dy) < 4) return;
      pr.moved = true;
      if (pr.id && pr.id !== ME) {
        // Drag a circle: it follows the pointer and stays where it's dropped.
        const p = this.pos.get(pr.id);
        const r = this.svg.getBoundingClientRect();
        p.x = (e.clientX - r.left - r.width / 2 - this.view.x) / this.view.k;
        p.y = (e.clientY - r.top - r.height / 2 - this.view.y) / this.view.k;
        p.vx = p.vy = 0;
        p.fixed = true;
        this.reheat(0.25);
        this.place();
      } else {
        this.view.x = pr.view.x + dx;
        this.view.y = pr.view.y + dy;
        this.place();
      }
    });
    this.svg.addEventListener("pointerup", () => {
      const pr = this.press;
      this.press = null;
      if (!pr) return;
      if (pr.moved) { this.saveView(); this.savePositions(); return; }
      if (pr.id && pr.id !== ME) this.choose(pr.id === this.selected ? null : pr.id);
      else if (this.selected) this.choose(null);
    });

    // Keyboard: Tab between people and groups, Enter or Space opens their card, Esc closes it.
    this.svg.addEventListener("keydown", (e) => {
      const g = e.target.closest && e.target.closest(".nw-node");
      if (!g || g.dataset.id === ME) return;
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        this.choose(g.dataset.id === this.selected ? null : g.dataset.id);
      } else if (e.key === "Escape" && this.selected) {
        this.choose(null);
      }
    });

    this.svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = this.svg.getBoundingClientRect();
      this.zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left - r.width / 2, e.clientY - r.top - r.height / 2);
    }, { passive: false });

    this.root.querySelector(".nw-zoom").addEventListener("click", (e) => {
      const b = e.target.closest("[data-zoom]");
      if (!b) return;
      if (b.dataset.zoom === "fit") this.fit();
      else this.zoomAt(Number(b.dataset.zoom), 0, 0);
    });

    this.card.addEventListener("click", (e) => {
      if (e.target.closest(".nw-close")) return this.choose(null);
      const go = e.target.closest("[data-go]");
      if (go && go.dataset.go) return this.choose(go.dataset.go, { centre: true });
      const ed = e.target.closest("[data-edit]");
      if (!ed) return;
      const id = this.selected, gid = this.isHub(id) ? id.slice(HUB.length) : null;
      const kind = ed.dataset.edit;
      if (kind === "person") this.edit({ kind, id });
      else if (kind === "group") this.edit({ kind, id: gid });
      else if (kind === "new_person") this.edit({ kind, group: gid });
      else if (kind === "new_group") this.edit({ kind, parent: gid });
    });

    this.legend.addEventListener("click", (e) => {
      const b = e.target.closest("[data-group]");
      if (!b) return;
      const id = b.dataset.group;
      this.hidden.has(id) ? this.hidden.delete(id) : this.hidden.add(id);
      remember("nw-hidden", [...this.hidden]);
      if (this.selected && !this.visible(this.selected)) this.choose(null);
      this.renderLegend();
      this.applyHidden();
      this.reheat(0.3);
    });

    this.search.addEventListener("input", () => {
      this.query = this.search.value;
      this.highlight();
    });
    this.search.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        const q = this.search.value.trim().toLowerCase();
        const hit = q && this.data.people.find((p) => this.visible(p.id) && p.name.toLowerCase().includes(q));
        if (hit) {
          this.search.value = this.query = "";
          this.choose(hit.id, { centre: true });
        }
      } else if (e.key === "Escape") {
        this.search.value = this.query = "";
        this.highlight();
      }
    });

    this.onResize = () => this.place();
  }

  zoomAt(factor, cx, cy) {
    const v = this.view;
    const k = Math.min(3, Math.max(0.2, v.k * factor));
    // Keep the point under the pointer still while zooming.
    v.x = cx - ((cx - v.x) * k) / v.k;
    v.y = cy - ((cy - v.y) * k) / v.k;
    v.k = k;
    this.place();
    this.saveView();
  }

  attach() {
    if (this.attached) return;
    this.attached = true;
    window.addEventListener("resize", this.onResize);
    this.reheat(0.1);
  }

  detach() {
    this.attached = false;
    window.removeEventListener("resize", this.onResize);
    this.savePositions();
  }
}
