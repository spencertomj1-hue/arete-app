// To-do lists in the style of Apple Reminders: the Home page's three compact
// cards (today, this week, this month).
//
// Python passes { today, sections: [{period, start, title, goals, limit}], acked },
// where goals are the to-do rows of that section's period.
// Edits show straight away and go to Python as ops:
//   {opId, kind: "add", goal: {id, title, period, period_start}}
//   {opId, kind: "toggle", id, done}         {opId, kind: "edit", id, title | notes}
//   {opId, kind: "repeat", id, repeat}       {opId, kind: "move", id, period}
//   {opId, kind: "delete", id}               {opId, kind: "reorder", ids}
// Like the timetable grid, ops stay pending until Python lists their opId in
// `acked`, so a slow rerun never shows old data.

const PERIODS = ["daily", "weekly", "monthly"];
const NAMES = { daily: "Daily", weekly: "Weekly", monthly: "Monthly" };
const UNITS = { daily: "day", weekly: "week", monthly: "month" };
const COMPLETE_MS = 650;          // how long a ticked goal stays in place before moving down

const ICONS = {
  check: '<svg viewBox="0 0 24 24"><path d="M6.5 12.5l3.5 3.5 7.5-8" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>',
  chevron: '<svg viewBox="0 0 24 24"><path d="M9 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  repeat: '<svg viewBox="0 0 24 24"><path d="M17 2l3 3-3 3M4 11V9a4 4 0 0 1 4-4h12M7 22l-3-3 3-3M20 13v2a4 4 0 0 1-4 4H4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  notes: '<svg viewBox="0 0 24 24"><path d="M5 6h14M5 12h14M5 18h9" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  move: '<svg viewBox="0 0 24 24"><rect x="3.5" y="5" width="17" height="15" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M3.5 10h17M8 3v4M16 3v4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  edit: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16v4z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
};

// ---------- Small helpers ----------

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const uid = () =>
  (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));

function remember(key, value) {
  try { sessionStorage.setItem(key, JSON.stringify(value)); } catch (e) {}
}
function recall(key) {
  try { return JSON.parse(sessionStorage.getItem(key)); } catch (e) { return null; }
}

// Period maths on ISO dates (UTC, so there are no daylight-saving surprises).
// Mirrors store.start_of_period / shift_period / move_target.
const toDate = (iso) => new Date(iso + "T00:00:00Z");
const toIso = (d) => d.toISOString().slice(0, 10);

function periodStart(period, iso) {
  const d = toDate(iso);
  if (period === "weekly") d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
  else if (period === "monthly") d.setUTCDate(1);
  return toIso(d);
}

function shiftPeriod(period, start, n) {
  const d = toDate(periodStart(period, start));
  if (period === "daily") d.setUTCDate(d.getUTCDate() + n);
  else if (period === "weekly") d.setUTCDate(d.getUTCDate() + 7 * n);
  else if (period === "monthly") d.setUTCMonth(d.getUTCMonth() + n);
  return toIso(d);
}

function periodEnd(period, start) {
  const d = toDate(shiftPeriod(period, start, 1));
  d.setUTCDate(d.getUTCDate() - 1);
  return toIso(d);
}

function moveTarget(goal, period, today) {
  const inside = goal.period_start <= today && today <= periodEnd(goal.period, goal.period_start);
  return periodStart(period, inside ? today : goal.period_start);
}

function ring(done, total) {
  const r = 7, c = 2 * Math.PI * r, frac = total ? done / total : 0;
  return `<svg class="gl-ring${total && done === total ? " full" : ""}" viewBox="0 0 18 18">
    <circle cx="9" cy="9" r="${r}" class="track"/>
    ${done ? `<circle cx="9" cy="9" r="${r}" class="fill" stroke-dasharray="${(frac * c).toFixed(2)} ${c.toFixed(2)}"/>` : ""}</svg>`;
}

// ---------- Entry point ----------

const instances = new WeakMap();

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const root = parentElement.querySelector(".gl-root");
  let gl = instances.get(root);
  if (!gl) {
    gl = new GoalLists(root);
    instances.set(root, gl);
  }
  gl.send = (ops) => setTriggerValue("ops", ops);
  gl.update(data);
  return () => gl.detach();
}

class GoalLists {
  constructor(root) {
    this.root = root;
    this.pending = [];           // ops not yet acknowledged by Python
    this.goals = new Map();      // id -> goal (server data + pending ops)
    this.completing = new Map(); // id -> "ticked" | "leaving": just ticked, still shown in place
    this.editing = null;         // {kind: "add", period} | {kind: "title" | "notes", id}, plus value
    this.openDone = new Set(recall("gl-open-done") || []);
    this.expanded = new Set();   // sections showing all their to-dos, not just the first few
    this.drag = null;
    this.onOutside = (e) => { if (this.menu && !e.composedPath().includes(this.menu)) this.closeMenu(); };
    this.onKey = (e) => { if (e.key === "Escape") this.closeMenu(); };
    this.onPress = () => { this.pressed = true; };
    this.onRelease = () => { setTimeout(() => { this.pressed = false; }, 0); };
    root.addEventListener("click", (e) => this.onClick(e));
    root.addEventListener("contextmenu", (e) => this.onContextMenu(e));
    root.addEventListener("pointerdown", (e) => this.onPointerDown(e));
  }

  // ---------- Data ----------

  update(data) {
    this.data = data;
    const acked = new Set(data.acked);
    this.pending = this.pending.filter((op) => !acked.has(op.opId));
    this.goals = new Map();
    data.sections.forEach((s) => s.goals.forEach((g) => this.goals.set(g.id, { ...g })));
    this.pending.forEach((op) => this.applyLocal(op));
    if (!(this.drag && this.drag.active)) this.render();   // a drag redraws when it ends
    this.attach();
  }

  attach() {
    if (this.attached) return;
    this.attached = true;
    document.addEventListener("pointerdown", this.onOutside, true);
    document.addEventListener("keydown", this.onKey);
    window.addEventListener("pointerdown", this.onPress, true);
    window.addEventListener("pointerup", this.onRelease, true);
  }

  detach() {
    this.attached = false;
    document.removeEventListener("pointerdown", this.onOutside, true);
    document.removeEventListener("keydown", this.onKey);
    window.removeEventListener("pointerdown", this.onPress, true);
    window.removeEventListener("pointerup", this.onRelease, true);
  }

  applyLocal(op) {
    const g = this.goals.get(op.id);
    if (op.kind === "add") this.goals.set(op.goal.id, { notes: null, done: false, repeat: false, ...op.goal });
    else if (op.kind === "reorder") op.ids.forEach((id, i) => { const x = this.goals.get(id); if (x) x.ord = i; });
    else if (op.kind === "delete") this.goals.delete(op.id);
    else if (!g) return;
    else if (op.kind === "toggle") g.done = op.done;
    else if (op.kind === "edit") Object.assign(g, "title" in op ? { title: op.title } : { notes: op.notes || null });
    else if (op.kind === "repeat") g.repeat = op.repeat;
    else if (op.kind === "move") {
      g.period_start = moveTarget(g, op.period, this.data.today);
      g.period = op.period;
      g.ord = this.nextOrd(op.period, g.period_start);
    }
  }

  // Apply ops locally, queue them, and send everything still pending.
  commit(ops) {
    ops.forEach((op) => this.applyLocal(op));
    this.pending.push(...ops);
    this.render();
    this.send(this.pending.slice());
    // If a trigger ever gets lost, resend whatever is still unacknowledged.
    clearTimeout(this.resendTimer);
    this.resendTimer = setTimeout(() => {
      if (this.pending.length) this.send(this.pending.slice());
    }, 3000);
  }

  goalsOf(period, start) {
    return [...this.goals.values()]
      .filter((g) => g.period === period && g.period_start === start)
      .sort((a, b) => a.ord - b.ord || String(a.created_at).localeCompare(String(b.created_at)));
  }

  nextOrd(period, start) {
    return Math.max(-1, ...this.goalsOf(period, start).map((g) => g.ord)) + 1;
  }

  section(period) {
    return this.data.sections.find((s) => s.period === period);
  }

  // Active goals stay above; ticked ones (except those still animating) go to Completed.
  split(sec) {
    const all = this.goalsOf(sec.period, sec.start);
    return {
      all,
      active: all.filter((g) => !g.done || this.completing.has(g.id)),
      done: all.filter((g) => g.done && !this.completing.has(g.id)),
    };
  }

  // ---------- Drawing ----------

  render() {
    const { sections } = this.data;
    this.rendering = true;
    this.root.innerHTML = `<div class="gl-grid compact">
      ${sections.map((s) => this.sectionHtml(s)).join("")}</div>`;
    this.rendering = false;
    this.restoreEditor();
  }

  sectionHtml(sec) {
    const { all, active, done } = this.split(sec);
    const finished = all.filter((g) => g.done).length;
    const limit = this.expanded.has(sec.period) ? Infinity : sec.limit || Infinity;
    const shown = active.slice(0, limit);
    const hidden = active.length - shown.length;
    const open = this.openDone.has(sec.period);
    const adding = this.editing && this.editing.kind === "add" && this.editing.period === sec.period;
    return `<section class="gl-card" data-period="${sec.period}">
      <header class="gl-head">
        ${ring(finished, all.length)}
        <span class="gl-name">${NAMES[sec.period]}</span>
        <span class="gl-count">${finished}/${all.length}</span>
        <span class="gl-sub">${esc(sec.title)}</span>
      </header>
      <ul class="gl-list" data-list="${sec.period}">${shown.map((g) => this.rowHtml(g)).join("")}</ul>
      ${hidden > 0 ? `<button class="gl-link gl-more" data-more>${hidden} more</button>` : ""}
      ${adding ? `<div class="gl-add editing"><span class="gl-tick ghost"></span>
          <input class="gl-input" data-editor placeholder="New goal" aria-label="New goal"></div>`
               : `<button class="gl-add" data-add>${ICONS.plus}<span>New goal</span></button>`}
      ${done.length ? `
        <button class="gl-done-head${open ? " open" : ""}" data-toggle-done>${ICONS.chevron}Completed (${done.length})</button>
        ${open ? `<ul class="gl-list gl-done-list">${done.map((g) => this.rowHtml(g)).join("")}</ul>` : ""}` : ""}
    </section>`;
  }

  rowHtml(g) {
    const ed = this.editing && this.editing.id === g.id ? this.editing.kind : null;
    const state = this.completing.get(g.id);
    const title = ed === "title"
      ? `<input class="gl-input" data-editor aria-label="Goal title">`
      : `<span class="gl-title" data-title>${esc(g.title)}</span>`;
    const notes = ed === "notes"
      ? `<input class="gl-input gl-notes-input" data-editor placeholder="Notes" aria-label="Notes">`
      : g.notes ? `<span class="gl-notes">${esc(g.notes)}</span>` : "";
    return `<li class="gl-row${g.done ? " done" : ""}${state ? ` ${state}` : ""}" data-id="${esc(g.id)}">
      <button class="gl-tick" data-tick role="checkbox" aria-checked="${g.done}"
        aria-label="${g.done ? "Mark as not done" : "Mark as done"}">${ICONS.check}</button>
      <div class="gl-text">${title}${notes}</div>
      ${g.repeat ? `<span class="gl-repeat" title="Repeats every ${UNITS[g.period]}">${ICONS.repeat}</span>` : ""}
    </li>`;
  }

  // ---------- Inline editors (title, notes, new goal) ----------

  startEdit(editing) {
    this.closeMenu();
    if (this.editing) this.finishEdit(true);
    const g = editing.id && this.goals.get(editing.id);
    this.editing = { ...editing, value: editing.kind === "add" ? "" : (g && g[editing.kind]) || "", fresh: true };
    this.render();
  }

  // Re-create the open editor after a redraw, with its text, caret and focus.
  restoreEditor() {
    const input = this.root.querySelector("[data-editor]");
    if (!input || !this.editing) return;
    input.value = this.editing.value;
    const end = input.value.length;
    input.focus();
    if (this.editing.fresh && this.editing.kind !== "add") input.select();
    else input.setSelectionRange(this.editing.caret ?? end, this.editing.caret ?? end);
    this.editing.fresh = false;
    input.addEventListener("input", () => { this.editing.value = input.value; this.editing.caret = input.selectionStart; });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); this.finishEdit(true, true); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); this.finishEdit(false); }
    });
    // Clicking elsewhere saves. While the button is still down, wait for the click to
    // land first, so redrawing here doesn't swallow it (it may open another editor).
    input.addEventListener("blur", () => {
      const ed = this.editing;
      if (this.rendering || !ed) return;
      const finish = () => { if (this.editing === ed) this.finishEdit(true); };
      if (this.pressed) window.addEventListener("pointerup", () => setTimeout(finish, 30), { once: true, capture: true });
      else finish();
    });
  }

  // save: keep the text; again: (Enter on "New goal") stay open for the next one.
  finishEdit(save, again = false) {
    const ed = this.editing;
    if (!ed) return;
    this.editing = null;
    const text = ed.value.trim();
    const ops = [];
    if (save && ed.kind === "add" && text) {
      const sec = this.section(ed.period);
      ops.push({ opId: uid(), kind: "add", goal: {
        id: uid(), title: text, period: sec.period, period_start: sec.start,
        ord: this.nextOrd(sec.period, sec.start), created_at: new Date().toISOString(),
      } });
    } else if (save && ed.kind !== "add") {
      const g = this.goals.get(ed.id);
      if (g && ed.kind === "title" && text && text !== g.title) ops.push({ opId: uid(), kind: "edit", id: ed.id, title: text });
      if (g && ed.kind === "notes" && text !== (g.notes || "")) ops.push({ opId: uid(), kind: "edit", id: ed.id, notes: text });
    }
    if (again && ed.kind === "add" && text) this.editing = { kind: "add", period: ed.period, value: "" };
    if (ops.length) this.commit(ops);
    else this.render();
  }

  // ---------- Clicks ----------

  onClick(e) {
    const t = e.target.closest("button, [data-title]");
    if (!t || this.dragMoved) return;
    const card = t.closest(".gl-card");
    const period = card && card.dataset.period;
    const row = t.closest(".gl-row");
    if (t.dataset.tick !== undefined) this.toggle(row.dataset.id);
    else if (t.dataset.title !== undefined) this.startEdit({ kind: "title", id: row.dataset.id });
    else if (t.dataset.add !== undefined) this.startEdit({ kind: "add", period });
    else if (t.dataset.more !== undefined) {
      this.expanded.add(period);
      this.render();
    } else if (t.dataset.toggleDone !== undefined) {
      this.openDone.has(period) ? this.openDone.delete(period) : this.openDone.add(period);
      remember("gl-open-done", [...this.openDone]);
      this.render();
    }
  }

  // Tick: fill the circle, fade and strike the title, then slide down into Completed.
  // Untick: straight back into the list.
  toggle(id) {
    const g = this.goals.get(id);
    if (!g) return;
    const done = !g.done;
    clearTimeout((this.timers ||= {})[id]);
    if (done) {
      this.completing.set(id, "ticking");     // animates on this draw only; redraws show it settled
      this.timers[id] = setTimeout(() => {
        this.completing.set(id, "leaving");
        this.render();
        this.timers[id] = setTimeout(() => { this.completing.delete(id); this.render(); }, 260);
      }, COMPLETE_MS);
    } else this.completing.delete(id);
    this.commit([{ opId: uid(), kind: "toggle", id, done }]);
    if (this.completing.get(id) === "ticking") this.completing.set(id, "ticked");
  }

  // ---------- Right-click menu ----------

  onContextMenu(e) {
    const row = e.target.closest(".gl-row");
    if (!row) return;
    e.preventDefault();
    const g = this.goals.get(row.dataset.id);
    if (!g) return;
    if (this.editing) this.finishEdit(true);
    this.closeMenu();
    const others = PERIODS.filter((p) => p !== g.period);
    const menu = document.createElement("div");
    menu.className = "gl-menu";
    menu.innerHTML = `
      <button data-act="title">${ICONS.edit}Edit title</button>
      <button data-act="notes">${ICONS.notes}${g.notes ? "Edit notes" : "Add notes"}</button>
      <button data-act="repeat" class="${g.repeat ? "on" : ""}">${ICONS.repeat}Repeat every ${UNITS[g.period]}
        <span class="gl-menu-check">${g.repeat ? ICONS.check : ""}</span></button>
      <div class="gl-menu-sep"></div>
      <div class="gl-menu-label">Move to</div>
      ${others.map((p) => `<button data-act="move" data-to="${p}">${ICONS.move}${NAMES[p]}</button>`).join("")}
      <div class="gl-menu-sep"></div>
      <button data-act="delete" class="danger">${ICONS.trash}Delete</button>`;
    this.root.appendChild(menu);
    const { innerWidth: w, innerHeight: h } = window;
    const r = menu.getBoundingClientRect();
    menu.style.left = `${Math.min(e.clientX, w - r.width - 8)}px`;
    menu.style.top = `${Math.min(e.clientY, h - r.height - 8)}px`;
    menu.addEventListener("click", (ev) => {
      const b = ev.target.closest("button");
      if (!b) return;
      this.closeMenu();
      const act = b.dataset.act;
      if (act === "title" || act === "notes") this.startEdit({ kind: act, id: g.id });
      else if (act === "repeat") this.commit([{ opId: uid(), kind: "repeat", id: g.id, repeat: !g.repeat }]);
      else if (act === "move") this.commit([{ opId: uid(), kind: "move", id: g.id, period: b.dataset.to }]);
      else if (act === "delete") this.commit([{ opId: uid(), kind: "delete", id: g.id }]);
    });
    this.menu = menu;
  }

  closeMenu() {
    if (this.menu) this.menu.remove();
    this.menu = null;
  }

  // ---------- Drag to reorder (active goals of one list) ----------

  onPointerDown(e) {
    const row = e.target.closest(".gl-list:not(.gl-done-list) .gl-row");
    if (!row || e.button !== 0 || e.target.closest("[data-tick], input")) return;
    this.dragMoved = false;
    this.drag = { row, list: row.parentElement, y0: e.clientY, active: false, pointer: e.pointerId };
    const move = (ev) => this.onDragMove(ev);
    const up = (ev) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      this.onDragEnd(ev);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  }

  onDragMove(e) {
    const d = this.drag;
    if (!d) return;
    const dy = e.clientY - d.y0;
    if (!d.active) {
      if (Math.abs(dy) < 5) return;
      d.active = true;
      this.dragMoved = true;
      d.rows = [...d.list.children];
      d.rects = d.rows.map((r) => r.getBoundingClientRect());
      d.from = d.rows.indexOf(d.row);
      d.to = d.from;
      d.height = d.rects[d.from].height;
      d.list.classList.add("sorting");
      d.row.classList.add("dragging");
    }
    // The row follows the pointer (kept inside the list). Its new slot is the
    // number of other rows whose middle it has passed; this uses the unclamped
    // position, so the first and last slots can be reached.
    const first = d.rects[0].top, last = d.rects[d.rects.length - 1].bottom;
    const top = Math.max(first, Math.min(last - d.height, d.rects[d.from].top + dy));
    d.row.style.transform = `translateY(${top - d.rects[d.from].top}px)`;
    const mid = d.rects[d.from].top + dy + d.height / 2;
    const to = d.rects.filter((r, i) => i !== d.from && r.top + r.height / 2 < mid).length;
    d.to = to;
    d.rows.forEach((r, i) => {
      if (r === d.row) return;
      const shift = d.from < to && i > d.from && i <= to ? -d.height : d.from > to && i >= to && i < d.from ? d.height : 0;
      r.style.transform = shift ? `translateY(${shift}px)` : "";
    });
  }

  onDragEnd() {
    const d = this.drag;
    this.drag = null;
    // The click that follows a drag must not open the title editor.
    setTimeout(() => { this.dragMoved = false; }, 0);
    if (!d || !d.active) return;
    const sec = this.section(d.list.dataset.list);
    const { active, done } = this.split(sec);
    const visible = d.rows.map((r) => r.dataset.id);
    if (d.from === d.to) { this.render(); return; }
    visible.splice(d.to, 0, visible.splice(d.from, 1)[0]);
    const rest = active.map((g) => g.id).filter((id) => !visible.includes(id));
    this.commit([{ opId: uid(), kind: "reorder", ids: [...visible, ...rest, ...done.map((g) => g.id)] }]);
  }
}
