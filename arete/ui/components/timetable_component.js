// Weekly Timetable: 7 days side by side, each with Plan/Actual columns in
// 30-minute slots, like a calendar week view.
//
// Python passes { days, today, groups, categories, entries, labelMap, acked },
// with only that week's entries. Edits are applied locally straight away, then
// sent to Python as "ops": {opId, kind: "paint", date, track, start, end, block, newId}
// for drags, {opId, kind: "upsert", entry} / {opId, kind: "delete", id} for right-click edits.
// Ops stay pending until Python lists their opId in `acked`, so a slow rerun
// never makes the grid flicker back to old data.
// Swiping sideways on the grid sends a "nav" trigger (-1 / +1 week).

const SLOTS = 36;                 // 07:00 -> 01:00 in 30-minute slots
const FIRST_MIN = 7 * 60;
const TRACKS = ["plan", "actual"];
const SWIPE_THRESHOLD = 120;      // px of sideways scroll that flips a week

// ---------- Small helpers ----------

const pad = (n) => String(n).padStart(2, "0");

// Slot index (0..36) -> "HH:mm". Slot 36 is "01:00" (end of the last slot).
function slotTime(i) {
  const m = (FIRST_MIN + 30 * i) % 1440;
  return `${pad(Math.floor(m / 60))}:${pad(m % 60)}`;
}

// "HH:mm" -> slot index. Times before 07:00 are after midnight.
function slotOf(t) {
  const [h, m] = t.split(":").map(Number);
  let mins = h * 60 + m;
  if (mins < FIRST_MIN) mins += 1440;
  return Math.round((mins - FIRST_MIN) / 30);
}

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const CAL_ICON = '<svg class="cal-ic" viewBox="0 0 24 24"><rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg>';

const PIN_ICON = '<svg class="cal-ic" viewBox="0 0 24 24"><path d="M12 21s-6-5.6-6-11a6 6 0 0 1 12 0c0 5.4-6 11-6 11z"/><circle cx="12" cy="10" r="2"/></svg>';

// "26 – 31 Oct 2026" or "26 Oct – 1 Nov 2026" from two ISO dates.
function weekTitle(first, last) {
  const a = new Date(first + "T00:00"), b = new Date(last + "T00:00");
  const mon = (d) => d.toLocaleDateString("en-GB", { month: "short" });
  const start = a.getMonth() === b.getMonth() ? `${a.getDate()}` : `${a.getDate()} ${mon(a)}`;
  return `${start} – ${b.getDate()} ${mon(b)} ${b.getFullYear()}`;
}

// Block text is black, except on genuinely dark fills (deep purple, pure red...),
// where it's white. Deliberately lenient: mid colours like Uni blue stay black.
function contrastText(hex) {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [n >> 16, (n >> 8) & 255, n & 255];
  return 0.299 * r + 0.587 * g + 0.114 * b > 90 ? "#000000" : "#ffffff";
}

const uid = () =>
  (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));

// sessionStorage can be unavailable; the grid works without it.
function remember(key, value) {
  try { sessionStorage.setItem(key, JSON.stringify(value)); } catch (e) {}
}
function recall(key) {
  try { return JSON.parse(sessionStorage.getItem(key)); } catch (e) { return null; }
}

// One instance per mounted root, so brush/selection survive data updates.
const instances = new WeakMap();

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const root = parentElement.querySelector(".tt-root");
  let tt = instances.get(root);
  if (!tt) {
    tt = new Timetable(root);
    instances.set(root, tt);
  }
  tt.send = (ops) => setTriggerValue("ops", ops);
  tt.trigger = (name, value) => setTriggerValue(name, { ...value, nonce: uid() });
  // Week navigation: {dir: -1 | 1}, {dir: 0} for this week, or {date: "YYYY-MM-DD"}.
  tt.nav = (nav) => setTriggerValue("nav", { ...nav, nonce: uid() });
  tt.update(data);
  return () => tt.detach();
}

class Timetable {
  constructor(root) {
    this.root = root;
    this.entries = new Map();   // id -> entry (server data + pending ops)
    this.pending = [];          // ops not yet acknowledged by Python
    this.brush = recall("tt-brush");   // category id, "eraser", or null
    this.label = recall("tt-label") || "";
    this.drag = null;           // {di, track, a, b} while painting
    this.selected = [];         // cells currently highlighted
    this.builtFor = null;       // week + categories the view was built for
    this.swipe = 0;             // accumulated sideways scroll
    this.swipeLocked = false;
    this.onMouseUp = () => this.endDrag();
    this.onWindowResize = () => this.fitToWindow();
    this.onKey = (e) => {
      if (e.key !== "Escape") return;
      this.cancelDrag();
      this.closePopover();
      if (this.returnFocus) { this.returnFocus.focus(); this.returnFocus = null; }
    };
    this.onOutside = (e) => {
      if (this.popover && !e.composedPath().includes(this.popover) && !e.composedPath().includes(this.wrapEl)) this.closePopover();
    };
  }

  // ---------- Data ----------

  update(data) {
    this.data = data;
    this.cats = new Map(data.categories.map((c) => [c.id, c]));
    this.dayIndex = new Map(data.days.map((d, i) => [d, i]));

    // Server entries, then re-apply anything Python hasn't saved yet.
    const acked = new Set(data.acked);
    this.pending = this.pending.filter((op) => !acked.has(op.opId));
    this.entries = new Map(data.entries.map((e) => [e.id, { ...e }]));
    this.outlook = (data.outlook || []).slice();   // Python already leaves out removed ones
    this.hidden = new Set();                       // removed / detached here, not yet saved
    this.pending.forEach((op) => this.applyLocal(op));

    const key = JSON.stringify([data.categories, data.days, data.today]);
    if (key !== this.builtFor) {
      this.build();
      this.builtFor = key;
    }
    this.refreshDatalist();
    this.showOutlookStatus();
    this.fitToWindow();
    this.paint();
    // Re-measure once the page around the grid (agenda, fonts) has settled.
    requestAnimationFrame(() => this.fitToWindow());
    clearTimeout(this.fitTimer);
    this.fitTimer = setTimeout(() => this.fitToWindow(), 400);
    this.attach();
  }

  applyLocal(op) {
    if (op.kind === "paint") {
      // Outlook events in the way become the user's own blocks first (like store.paint).
      if (op.track === "plan") {
        const s = slotOf(op.start), e = slotOf(op.end);
        for (const ev of this.visibleOutlook()) {
          if (ev.date === op.date && slotOf(ev.start) < e && slotOf(ev.end) > s) {
            this.applyLocal({ kind: "detach_outlook", uid: ev.id });
          }
        }
      }
      this.planPaint(op).forEach((o) => this.applyLocal(o));
    } else if (op.kind === "upsert") this.entries.set(op.entry.id, { ...op.entry });
    else if (op.kind === "hide_outlook") this.hidden.add(op.uid);
    else if (op.kind === "detach_outlook") {
      const ev = this.outlook.find((x) => x.id === op.uid);
      if (ev && !this.hidden.has(ev.id)) {
        this.hidden.add(ev.id);
        this.entries.set(`ol:${ev.id}`, {
          id: `ol:${ev.id}`, date: ev.date, track: "plan", start: ev.start, end: ev.end,
          label: ev.title, categoryId: ev.categoryId, details: ev.details,
        });
      }
    } else this.entries.delete(op.id);
  }

  visibleOutlook() {
    return this.outlook.filter((ev) => !this.hidden.has(ev.id));
  }

  // Apply ops locally, queue them, and send everything still pending.
  commit(ops) {
    if (!ops.length) return;
    ops.forEach((op) => this.applyLocal(op));
    this.pending.push(...ops);
    this.paint();
    this.send(this.pending.slice());
    // If a trigger ever gets lost, resend whatever is still unacknowledged.
    clearTimeout(this.resendTimer);
    this.resendTimer = setTimeout(() => {
      if (this.pending.length) this.send(this.pending.slice());
    }, 3000);
  }

  entriesFor(date, track) {
    return [...this.entries.values()].filter((e) => e.date === date && e.track === track);
  }

  // Fill slots a..b (inclusive) of one day/track with a new block, or clear
  // them when `block` is null. Python saves it with the same rules
  // (store.plan_paint); newId keeps the new block's id the same on both sides.
  paintRange(di, track, a, b, block) {
    const s = Math.min(a, b), e = Math.max(a, b) + 1;
    this.commit([{
      opId: uid(), kind: "paint", date: this.data.days[di], track,
      start: slotTime(s), end: slotTime(e), block, newId: uid(),
    }]);
  }

  // Local copy of store.plan_paint, used only to show a paint instantly.
  // Overlapped blocks are trimmed or split, and the new block merges with
  // touching blocks that have the same label/category. Keep the two in sync.
  planPaint({ date, track, start, end, block, newId }) {
    const s = slotOf(start), e = slotOf(end);
    const ops = [];
    const up = (entry) => ({ kind: "upsert", entry });
    const del = (id) => ({ kind: "delete", id });

    for (const en of this.entriesFor(date, track)) {
      const es = slotOf(en.start), ee = slotOf(en.end);
      if (ee <= s || es >= e) continue;
      const left = es < s, right = ee > e;
      if (left) ops.push(up({ ...en, end: slotTime(s) }));
      if (right) ops.push(up({ ...en, id: left ? uid() : en.id, start: slotTime(e) }));
      if (!left && !right) ops.push(del(en.id));
    }

    if (block) {
      // Look at the state after trimming to find blocks to merge with.
      const after = new Map(this.entries);
      ops.forEach((op) => (op.kind === "upsert" ? after.set(op.entry.id, op.entry) : after.delete(op.id)));
      let ns = s, ne = e;
      for (const en of after.values()) {
        if (en.date !== date || en.track !== track) continue;
        if (en.label !== block.label || en.categoryId !== block.categoryId) continue;
        if (slotOf(en.end) === ns) { ns = slotOf(en.start); ops.push(del(en.id)); }
        else if (slotOf(en.start) === ne) { ne = slotOf(en.end); ops.push(del(en.id)); }
      }
      ops.push(up({ id: newId, date, track, start: slotTime(ns), end: slotTime(ne), ...block }));
    }
    return ops;
  }

  // ---------- Building the view ----------

  build() {
    const { categories, groups, days, today } = this.data;

    // Key: one line of colour dots (groups separated by a thin divider).
    // Clicking an item picks the brush.
    const item = (c) =>
      `<button class="sw" data-cat="${esc(c.id)}" title="${esc(c.group)}"><i style="background:${esc(c.colour)}"></i>${esc(c.name)}</button>`;
    const key = groups
      .map((g) => categories.filter((c) => c.group === g).sort((x, y) => x.order - y.order).map(item).join(""))
      .join('<span class="sep"></span>');

    // Two header rows: weekday + dd/mm (spanning Plan + Actual), then Plan / Actual.
    const dayHead = days
      .map((d) => {
        const dt = new Date(d + "T00:00");
        const name = dt.toLocaleDateString("en-GB", { weekday: "long" });
        const [, m, day] = d.split("-");
        return `<th colspan="2" class="day ${d === today ? "today" : ""}"><div class="dn">${name}</div><div class="dd">${day}/${m}</div><div class="chips" data-d="${days.indexOf(d)}"></div></th>`;
      })
      .join("");
    const subHead = days.map(() => `<th class="sub first">Plan</th><th class="sub">Actual</th>`).join("");

    const rows = [];
    for (let s = 0; s < SLOTS; s++) {
      const cells = days
        .map((d, di) => TRACKS.map((t) =>
          `<td class="s ${t === "plan" ? "first" : ""} ${d === today ? "today" : ""}" data-d="${di}" data-t="${t}" data-s="${s}"></td>`).join(""))
        .join("");
      // Label each hour on its grid line, like a calendar.
      const time = s % 2 === 0 ? `<span>${slotTime(s)}</span>` : "";
      rows.push(`<tr class="${s % 2 ? "half" : "hour"}"><td class="t">${time}</td>${cells}</tr>`);
    }

    const cols = days.map(() => "<col><col>").join("");

    this.root.classList.toggle("single", !!this.data.single);   // one day: no week navigation
    this.root.classList.toggle("readonly", !!this.data.readonly); // view only: no editing tools
    this.root.innerHTML = `
      <div class="tt-head">
        <div class="tt-title">${this.data.single
          ? new Date(days[0] + "T00:00").toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" })
          : weekTitle(days[0], days[days.length - 1])}</div>
        <div class="tt-nav">
          <button data-nav="-1" title="Previous week">‹</button>
          <button data-nav="0">Today</button>
          <button data-nav="1" title="Next week">›</button>
        </div>
        <input type="date" class="tt-goto" title="Go to date" value="${days[0]}">
        <div class="tt-outlook">
          <button class="tt-sync" title="Sync Outlook now"><span class="msr">sync</span></button>
          <span class="tt-synced"></span>
          <button class="tt-ol-toggle" title="Show or hide Outlook events">${CAL_ICON}Outlook</button>
        </div>
        <div class="tt-tools">
          <input class="tt-label" list="tt-labels" placeholder="Label, e.g. Lecture" title="Label for new blocks">
          <datalist id="tt-labels"></datalist>
          <button class="sw eraser" data-cat="eraser">Eraser</button>
        </div>
      </div>
      <div class="tt-legend">${key}</div>
      <div class="tt-card">
        <div class="tt-wrap">
          <table class="tt" aria-hidden="true">
            <colgroup><col class="c-time">${cols}</colgroup>
            <thead>
              <tr><th rowspan="2" class="h-time"></th>${dayHead}</tr>
              <tr>${subHead}</tr>
            </thead>
            <tbody>${rows.join("")}</tbody>
          </table>
          <div class="tt-blocks" aria-hidden="true"></div>
        </div>
      </div>
      <div class="tt-hint">Drag down a column to add · click a block for details${this.data.single ? "" : " · swipe sideways to change week"}</div>
      <div class="tt-a11y" role="region" aria-label="Timetable blocks (press Enter on one for details; to add a block with the keyboard, use the Add block button above the timetable)"></div>`;

    // Blocks are positioned from the cells, so redo them when the size changes.
    this.blocksEl = this.root.querySelector(".tt-blocks");
    this.wrapEl = this.root.querySelector(".tt-wrap");
    if (this.resizeObserver) this.resizeObserver.disconnect();
    this.resizeObserver = new ResizeObserver(() => this.layoutBlocks());
    if (this.attached) this.resizeObserver.observe(this.wrapEl);

    // Index the slot cells: cells[day][track][slot].
    this.cells = days.map(() => ({ plan: [], actual: [] }));
    this.root.querySelectorAll("td.s").forEach((td) => {
      this.cells[+td.dataset.d][td.dataset.t][+td.dataset.s] = td;
    });

    this.labelInput = this.root.querySelector(".tt-label");
    this.labelInput.value = this.label;
    this.bindEvents();
    this.showBrush();
  }

  showOutlookStatus() {
    const { sync, showOutlook } = this.data;
    const note = this.root.querySelector(".tt-synced");
    note.textContent = sync ? sync.text : "";
    note.classList.toggle("warn", !!(sync && sync.error));
    note.title = (sync && sync.detail) || "";
    this.root.querySelector(".tt-ol-toggle").classList.toggle("on", !!showOutlook);
    this.root.querySelector(".tt-sync").classList.remove("spin");
  }

  refreshDatalist() {
    const labels = new Set([
      ...Object.keys(this.data.labelMap),
      ...this.data.categories.map((c) => c.name),
      ...[...this.entries.values()].map((e) => e.label),
    ]);
    this.root.querySelector("#tt-labels").innerHTML =
      [...labels].sort().map((l) => `<option value="${esc(l)}">`).join("");
  }

  // Work out which entry fills each slot, then draw the blocks.
  paint() {
    this.occupant = this.data.days.map(() => ({ plan: [], actual: [] }));
    this.blocks = [];
    for (const day of this.cells) {
      for (const td of [...day.plan, ...day.actual]) {
        td.title = "";
        td.classList.remove("filled");
      }
    }
    for (const en of this.entries.values()) {
      const di = this.dayIndex.get(en.date);
      if (di === undefined) continue;
      const s = slotOf(en.start), e = Math.min(slotOf(en.end), SLOTS);
      if (e <= s) continue;
      for (let i = s; i < e; i++) {
        const td = this.cells[di][en.track][i];
        td.title = `${en.label} · ${en.start}–${en.end}`;
        td.classList.add("filled");
        this.occupant[di][en.track][i] = en.id;
      }
      this.blocks.push({ en, di, s, e });
    }

    // Outlook events: drawn on top of the Plan column (click for details, edit or remove).
    this.outlookAt = this.data.days.map(() => []);
    for (const ev of this.visibleOutlook()) {
      const di = this.dayIndex.get(ev.date);
      if (di === undefined) continue;
      const s = slotOf(ev.start), e = Math.min(slotOf(ev.end), SLOTS);
      if (e <= s) continue;
      for (let i = s; i < e; i++) {
        const td = this.cells[di].plan[i];
        td.title = `${ev.title} · ${ev.exact} (Outlook)`;
        td.classList.add("filled");
        this.outlookAt[di][i] = ev;
      }
      this.blocks.push({ en: { ...ev, label: ev.title, track: "plan" }, di, s, e, outlook: true });
    }

    // All-day Outlook events: small chips in the day header.
    this.root.querySelectorAll("th.day .chips").forEach((box) => {
      const date = this.data.days[+box.dataset.d];
      box.innerHTML = (this.data.outlookAllDay || []).filter((c) => c.date === date)
        .map((c) => `<span class="chip${c.deadline ? " due" : ""}" title="${esc(c.title)} (${c.deadline ? "deadline" : "Outlook, all day"})">${esc(c.title)}</span>`).join("");
    });
    this.layoutBlocks();
    this.renderBlockList();
  }

  // For keyboard and screen-reader users: every block as a button, grouped by day,
  // in a list that's invisible until something in it has focus. Enter opens the
  // block's details card (with Edit / Delete), the same as clicking it.
  renderBlockList() {
    const list = this.root.querySelector(".tt-a11y");
    if (!list) return;
    this.listed = [...this.blocks].sort((x, y) => x.di - y.di || x.s - y.s || (x.en.track < y.en.track ? -1 : 1));
    const groups = this.data.days.map((d, di) => {
      const day = new Date(d + "T00:00").toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
      const items = this.listed.map((b, i) => [b, i]).filter(([b]) => b.di === di).map(([b, i]) => {
        const cat = this.cats.get(b.en.categoryId);
        const what = `${b.outlook ? "Plan (Outlook)" : b.en.track === "plan" ? "Plan" : "Actual"}: ${b.en.label}, ` +
          `${b.outlook ? b.en.exact : `${b.en.start} to ${b.en.end}`}${cat ? `, ${cat.name}` : ""}`;
        return `<button data-i="${i}">${esc(what)}</button>`;
      });
      return items.length ? `<div role="group" aria-label="${esc(day)}"><span class="a11y-day">${esc(day)}</span>${items.join("")}</div>` : "";
    }).join("");
    list.innerHTML = groups || `<button disabled>Nothing in the timetable this ${this.data.single ? "day" : "week"}</button>`;
  }

  // Draw each entry as one rounded block laid over its slot cells.
  // Home (view only): shrink the rows so the whole day fits in the window, no scrolling.
  fitToWindow() {
    if (!this.data.readonly || !this.wrapEl) return;
    const card = this.root.querySelector(".tt-card").getBoundingClientRect();
    const head = this.root.querySelector("thead").getBoundingClientRect().height;
    const legend = this.root.querySelector(".tt-legend").getBoundingClientRect().height;
    const main = document.querySelector(".stMainBlockContainer");
    const bottomPad = main ? parseFloat(getComputedStyle(main).paddingBottom) : 24;
    // window height minus everything above the rows and everything below them
    const room = window.innerHeight - card.top - head - legend - 10 - bottomPad - 6;
    let row = Math.max(9, Math.min(18, Math.floor(room / SLOTS)));
    this.root.style.setProperty("--row-h", `${row}px`);
    // Borders and margins make the estimate a little off: check where the key really
    // ends and take any overshoot back out of the rows.
    const over = this.root.querySelector(".tt-legend").getBoundingClientRect().bottom + bottomPad - window.innerHeight;
    if (over > 0 && row > 9) {
      row = Math.max(9, row - Math.ceil(over / SLOTS));
      this.root.style.setProperty("--row-h", `${row}px`);
    }
  }

  layoutBlocks() {
    if (!this.blocks) return;
    const origin = this.wrapEl.getBoundingClientRect();
    this.blocksEl.innerHTML = this.blocks
      .map(({ en, di, s, e, outlook }) => {
        const first = this.cells[di][en.track][s].getBoundingClientRect();
        const last = this.cells[di][en.track][e - 1].getBoundingClientRect();
        const cat = this.cats.get(en.categoryId);
        const bg = cat ? cat.colour : "#9e9e9e";
        const fg = (cat && cat.textColour) || contrastText(bg);
        const tall = last.bottom - first.top >= 30;   // room for the time line under the title
        if (outlook) {
          return `<div class="blk outlook ${tall ? "tall" : ""} ${this.focusId === "outlook:" + en.id ? "focus" : ""}" data-id="outlook:${esc(en.id)}" style="left:${first.left - origin.left}px;top:${first.top - origin.top}px;` +
            `width:${first.width}px;height:${last.bottom - first.top}px;background:${esc(bg)};color:${fg}">` +
            `<b>${CAL_ICON}${esc(en.title)}</b>${tall ? `<span>${esc(en.exact)}</span>` : ""}</div>`;
        }
        return `<div class="blk ${tall ? "tall" : ""} ${this.focusId === en.id ? "focus" : ""}" data-id="${esc(en.id)}" style="left:${first.left - origin.left}px;top:${first.top - origin.top}px;` +
          `width:${first.width}px;height:${last.bottom - first.top}px;background:${esc(bg)};color:${fg}">` +
          `<b>${esc(en.label)}</b>${tall ? `<span>${esc(en.start)}–${esc(en.end)}</span>` : ""}</div>`;
      })
      .join("");
  }

  // ---------- Brush ----------

  setBrush(id, { fromLabel = false } = {}) {
    this.brush = id;
    remember("tt-brush", id);
    const cat = this.cats.get(id);
    // Picking a category fills in its name, unless the typed label already maps to it.
    if (cat && !fromLabel && this.mapLabel(this.labelInput.value) !== id) {
      this.labelInput.value = cat.name;
      this.label = cat.name;
      remember("tt-label", cat.name);
    }
    this.showBrush();
  }

  showBrush() {
    this.root.querySelectorAll("[data-cat]").forEach((el) => el.classList.toggle("on", el.dataset.cat === this.brush));
  }

  // Default category for a typed label (case-insensitive), if any.
  mapLabel(label) {
    const want = label.trim().toLowerCase();
    if (!want) return null;
    for (const [l, id] of Object.entries(this.data.labelMap)) if (l.toLowerCase() === want) return id;
    for (const c of this.data.categories) if (c.name.toLowerCase() === want) return c.id;
    return null;
  }

  // ---------- Events ----------

  bindEvents() {
    const table = this.root.querySelector("table.tt");

    this.root.querySelectorAll("[data-cat]").forEach((el) =>
      el.addEventListener("click", () => this.setBrush(el.dataset.cat)));

    this.root.querySelector(".tt-sync").addEventListener("click", (e) => {
      e.currentTarget.classList.add("spin");
      this.trigger("sync", {});
    });
    this.root.querySelector(".tt-ol-toggle").addEventListener("click", () =>
      this.trigger("outlook", { show: !this.data.showOutlook }));

    this.root.querySelectorAll("[data-nav]").forEach((el) =>
      el.addEventListener("click", () => this.nav({ dir: +el.dataset.nav })));
    this.root.querySelector(".tt-goto").addEventListener("change", (e) => {
      if (e.target.value) this.nav({ date: e.target.value });
    });

    this.labelInput.addEventListener("input", () => {
      this.label = this.labelInput.value;
      remember("tt-label", this.label);
      const id = this.mapLabel(this.label);
      if (id && this.cats.has(id)) this.setBrush(id, { fromLabel: true });
    });

    table.addEventListener("mousedown", (e) => {
      const td = e.target.closest("td.s");
      if (!td || e.button !== 0) return;
      e.preventDefault();
      if (this.data.readonly) {                     // Home: no editing, just the details card
        const hit = this.blockAt(+td.dataset.d, td.dataset.t, +td.dataset.s);
        if (hit) this.openCard(hit); else this.closePopover();
        return;
      }
      this.closePopover();
      this.drag = { di: +td.dataset.d, track: td.dataset.t, a: +td.dataset.s, b: +td.dataset.s };
      this.highlight();
    });

    this.root.querySelector(".tt-a11y").addEventListener("click", (e) => {
      const b = e.target.closest("[data-i]");
      if (!b) return;
      const block = this.listed[+b.dataset.i];
      const hit = block.outlook ? { outlook: true, ev: { ...block.en, title: block.en.label } } : { outlook: false, entry: block.en };
      this.openCard(hit);
      // Move focus into the card so its buttons are next; Esc closes it and returns here.
      this.returnFocus = b;
      if (this.popover) { this.popover.tabIndex = -1; this.popover.focus(); }
    });

    table.addEventListener("mouseover", (e) => {
      const td = e.target.closest("td.s");
      if (!this.drag || !td) return;
      if (+td.dataset.d !== this.drag.di || td.dataset.t !== this.drag.track) return;
      this.drag.b = +td.dataset.s;
      this.highlight();
    });

    table.addEventListener("contextmenu", (e) => {
      const td = e.target.closest("td.s");
      if (!td) return;
      const hit = this.blockAt(+td.dataset.d, td.dataset.t, +td.dataset.s);
      if (!hit) return;
      e.preventDefault();
      if (this.data.readonly || hit.outlook) this.openCard(hit);       // details first for Outlook events
      else this.openPopover(hit.entry, e.clientX, e.clientY);           // straight to the editor
    });

    // Trackpad swipe sideways = previous / next week (one flip per gesture).
    this.root.querySelector(".tt-card").addEventListener("wheel", (e) => {
      if (this.data.single || Math.abs(e.deltaX) <= Math.abs(e.deltaY)) return;
      e.preventDefault();
      if (this.swipeLocked) return;
      this.swipe += e.deltaX;
      if (Math.abs(this.swipe) >= SWIPE_THRESHOLD) {
        this.nav({ dir: this.swipe > 0 ? 1 : -1 });
        this.swipe = 0;
        this.swipeLocked = true;
        setTimeout(() => { this.swipeLocked = false; }, 700);
      }
    }, { passive: false });
  }

  attach() {
    if (this.attached) return;
    window.addEventListener("resize", this.onWindowResize);
    window.addEventListener("mousedown", this.onOutside);
    this.resizeObserver.observe(this.wrapEl);
    window.addEventListener("mouseup", this.onMouseUp);
    window.addEventListener("keydown", this.onKey);
    this.attached = true;
  }

  detach() {
    window.removeEventListener("resize", this.onWindowResize);
    window.removeEventListener("mousedown", this.onOutside);
    if (this.resizeObserver) this.resizeObserver.disconnect();
    window.removeEventListener("mouseup", this.onMouseUp);
    window.removeEventListener("keydown", this.onKey);
    this.attached = false;
  }

  highlight() {
    this.selected.forEach((td) => td.classList.remove("sel"));
    this.selected = [];
    // Fade the blocks while dragging so the selection shows through.
    this.wrapEl.classList.toggle("dragging", !!this.drag);
    if (!this.drag) return;
    const { di, track, a, b } = this.drag;
    for (let s = Math.min(a, b); s <= Math.max(a, b); s++) {
      const td = this.cells[di][track][s];
      td.classList.add("sel");
      this.selected.push(td);
    }
  }

  cancelDrag() {
    this.drag = null;
    this.highlight();
  }

  endDrag() {
    const drag = this.drag;
    this.cancelDrag();
    if (!drag) return;
    // A click (no drag) on a block opens its details card; dragging paints.
    if (drag.a === drag.b && this.brush !== "eraser") {
      const hit = this.blockAt(drag.di, drag.track, drag.a);
      if (hit) {
        this.openCard(hit);
        return;
      }
    }
    if (this.brush === "eraser") {
      this.paintRange(drag.di, drag.track, drag.a, drag.b, null);
      return;
    }
    const cat = this.cats.get(this.brush);
    if (!cat) {
      this.flashHint("Pick a category (or type a label) first");
      return;
    }
    const label = this.labelInput.value.trim() || cat.name;
    this.paintRange(drag.di, drag.track, drag.a, drag.b, { label, categoryId: cat.id });
  }

  flashHint(text) {
    const hint = this.root.querySelector(".tt-hint");
    const old = hint.textContent;
    hint.textContent = text;
    hint.classList.add("warn");
    setTimeout(() => { hint.textContent = old; hint.classList.remove("warn"); }, 2000);
  }

  // ---------- Right-click editor ----------

  openPopover(entry, x, y) {
    this.closePopover();
    const options = this.data.categories
      .map((c) => `<option value="${esc(c.id)}" ${c.id === entry.categoryId ? "selected" : ""}>${esc(c.name)}</option>`)
      .join("");
    const day = new Date(entry.date + "T00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
    const pop = document.createElement("div");
    pop.className = "tt-pop";
    pop.innerHTML = `
      <div><b>${day} · ${esc(entry.start)}–${esc(entry.end)}</b> · ${entry.track === "plan" ? "Plan" : "Actual"}</div>
      <input class="p-label" list="tt-labels" value="${esc(entry.label)}">
      <select class="p-cat">${options}</select>
      <textarea class="p-details" rows="2" placeholder="Description (optional)">${esc(entry.details || "")}</textarea>
      <div class="row">
        <button class="del">Delete</button>
        <button class="cancel">Cancel</button>
        <button class="save"><b>Save</b></button>
      </div>`;
    pop.style.left = Math.min(x, window.innerWidth - 260) + "px";
    pop.style.top = Math.min(y, window.innerHeight - 160) + "px";
    this.root.appendChild(pop);
    this.popover = pop;

    const labelEl = pop.querySelector(".p-label");
    const catEl = pop.querySelector(".p-cat");
    labelEl.focus();
    labelEl.select();
    labelEl.addEventListener("input", () => {
      const id = this.mapLabel(labelEl.value);
      if (id && this.cats.has(id)) catEl.value = id;
    });

    const save = () => {
      const label = labelEl.value.trim() || this.cats.get(catEl.value).name;
      const details = pop.querySelector(".p-details").value.trim() || null;
      this.commit([{ opId: uid(), kind: "upsert", entry: { ...entry, label, categoryId: catEl.value, details } }]);
      this.closePopover();
    };
    labelEl.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
    pop.querySelector(".save").addEventListener("click", save);
    pop.querySelector(".cancel").addEventListener("click", () => this.closePopover());
    pop.querySelector(".del").addEventListener("click", () => {
      this.commit([{ opId: uid(), kind: "delete", id: entry.id }]);
      this.closePopover();
    });
    pop.addEventListener("mousedown", (e) => e.stopPropagation());
  }

  // Which block (own or Outlook) covers a slot. Outlook events are drawn on top.
  blockAt(di, track, slot) {
    const ev = track === "plan" && this.outlookAt[di][slot];
    if (ev) return { outlook: true, ev };
    const id = this.occupant[di][track][slot];
    return id ? { outlook: false, entry: this.entries.get(id) } : null;
  }

  // Click on a block: it grows slightly and a card shows what it is and when,
  // like Outlook's peek. Edit / Delete (Remove for Outlook events) unless view only.
  openCard(hit) {
    this.closePopover();
    const item = hit.outlook ? hit.ev : hit.entry;
    const blockId = hit.outlook ? `outlook:${item.id}` : item.id;
    const cat = this.cats.get(item.categoryId);
    const day = new Date(item.date + "T00:00").toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
    const when = hit.outlook ? item.exact : `${item.start}–${item.end}`;
    const text = hit.outlook ? item.description : item.details;
    const title = hit.outlook ? item.title : item.label;

    const card = document.createElement("div");
    card.className = "tt-pop tt-peek";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-label", `${hit.outlook ? item.title : item.label}, details`);
    card.innerHTML = `
      <div class="pk-title">${hit.outlook ? CAL_ICON : ""}<b>${esc(title)}</b></div>
      <div class="pk-when">${esc(day)} · ${esc(when)}</div>
      <div class="pk-meta"><i style="background:${esc(cat ? cat.colour : "#9e9e9e")}"></i>${esc(cat ? cat.name : "No category")}
        · ${hit.outlook || item.track === "plan" ? "Plan" : "Actual"}${hit.outlook ? " · from Outlook" : ""}</div>
      ${hit.outlook && item.location ? `<div class="pk-loc">${PIN_ICON}${esc(item.location)}</div>` : ""}
      ${text ? `<div class="pk-text">${esc(text).replace(/\n/g, "<br>")}</div>` : ""}
      ${this.data.readonly ? "" : `
      <div class="row">
        <button class="del">${hit.outlook ? "Remove" : "Delete"}</button>
        <button class="edit">Edit</button>
      </div>`}`;
    this.root.appendChild(card);
    this.popover = card;

    // Grow the block a little and put the card beside it.
    this.focusId = blockId;
    const blockEl = this.blocksEl.querySelector(`[data-id="${CSS.escape(blockId)}"]`);
    if (blockEl) blockEl.classList.add("focus");
    const r = blockEl ? blockEl.getBoundingClientRect() : { left: 200, right: 200, top: 200 };
    const w = 280;
    const left = r.right + 10 + w < window.innerWidth ? r.right + 10 : Math.max(8, r.left - 10 - w);
    card.style.left = `${left}px`;
    card.style.top = `${Math.max(8, Math.min(r.top, window.innerHeight - card.offsetHeight - 8))}px`;
    card.addEventListener("mousedown", (e) => e.stopPropagation());

    if (this.data.readonly) return;
    card.querySelector(".del").addEventListener("click", () => {
      this.commit([hit.outlook ? { opId: uid(), kind: "hide_outlook", uid: item.id }
                               : { opId: uid(), kind: "delete", id: item.id }]);
      this.closePopover();
    });
    card.querySelector(".edit").addEventListener("click", () => {
      let entry = item;
      if (hit.outlook) {   // editing an Outlook event makes it the user's own block
        this.commit([{ opId: uid(), kind: "detach_outlook", uid: item.id }]);
        entry = this.entries.get(`ol:${item.id}`);
      }
      const box = card.getBoundingClientRect();
      this.openPopover(entry, box.left, box.top);
    });
  }

  closePopover() {
    if (this.popover) this.popover.remove();
    this.popover = null;
    if (this.focusId) {
      this.focusId = null;
      this.blocksEl.querySelectorAll(".blk.focus").forEach((b) => b.classList.remove("focus"));
    }
  }
}
