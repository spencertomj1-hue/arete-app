// Month calendar for the Daily log page. Each day shows small icons for mood,
// sleep, energy and water (and a note marker); clicking a day selects it.
//
// Python passes { month: "YYYY-MM", today, selected, days: {iso: {mood, sleep, energy, water, notes}} }.
// Triggers back to Python:  select {date, nonce}   nav {dir: -1 | 0 | 1, nonce}

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2);
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

const ICON = {
  mood: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M8.5 14.5c1 1.2 2.1 1.8 3.5 1.8s2.5-.6 3.5-1.8M9 9.5h.01M15 9.5h.01"/></svg>',
  sleep: '<svg viewBox="0 0 24 24"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg>',
  energy: '<svg viewBox="0 0 24 24"><path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/></svg>',
  water: '<svg viewBox="0 0 24 24"><path d="M12 3s6 6.6 6 11a6 6 0 0 1-12 0c0-4.4 6-11 6-11z"/></svg>',
  note: '<svg viewBox="0 0 24 24"><path d="M6 3h9l4 4v14H6z"/><path d="M9 12h6M9 16h4"/></svg>',
};

// Mood 1 (low) -> 5 (great).
const MOOD_COLOURS = ["", "#e5484d", "#f76b15", "#e2a400", "#7cb342", "#30a46c"];

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const root = parentElement.querySelector(".cal-root");
  const send = (name, value) => setTriggerValue(name, { ...value, nonce: uid() });

  const [y, m] = data.month.split("-").map(Number);
  const first = new Date(y, m - 1, 1);
  const firstDow = data.weekStart === "Sunday" ? 0 : 1;               // weeks start on Monday, or Sunday
  const start = new Date(y, m - 1, 1 - ((first.getDay() - firstDow + 7) % 7));   // that day on/before the 1st
  const waterUnit = data.waterUnit || "L";
  const title = first.toLocaleDateString("en-GB", { month: "long", year: "numeric" });

  const cells = [];
  for (let i = 0; i < 42; i++) {
    const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
    const key = iso(d);
    const e = data.days[key] || {};
    const cls = ["cell"];
    if (d.getMonth() !== m - 1) cls.push("other");
    if (key === data.today) cls.push("today");
    if (key === data.selected) cls.push("selected");
    if (key > data.today) cls.push("future");
    // What a screen reader says for the day, e.g. "Friday 30 October: mood 4, slept 7.5 hours, energy 3, has notes".
    const said = [d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" }),
      [e.mood != null && `mood ${e.mood}`, e.sleep != null && `slept ${+e.sleep} hours`,
       e.energy != null && `energy ${e.energy}`, e.water != null && `drank ${+e.water} ${waterUnit === "L" ? "litres" : waterUnit}`, e.notes && "has notes"].filter(Boolean).join(", ") || "nothing logged"]
      .join(": ");
    const stat = (kind, value, extra = "") =>
      value == null ? "" : `<span class="stat ${kind}" ${extra}>${ICON[kind]}${esc(value)}</span>`;
    cells.push(`
      <button class="${cls.join(" ")}" data-date="${key}" aria-label="${esc(said)}"
        aria-pressed="${key === data.selected}"${key === data.today ? ' aria-current="date"' : ""}>
        <span class="num">${d.getDate()}${e.notes ? `<i class="note" title="Has notes">${ICON.note}</i>` : ""}</span>
        <span class="stats">
          ${stat("mood", e.mood, e.mood ? `style="--c:${MOOD_COLOURS[e.mood]}"` : "")}
          ${stat("sleep", e.sleep == null ? null : `${+e.sleep}h`)}
          ${stat("energy", e.energy)}
          ${stat("water", e.water == null ? null : `${+e.water}${waterUnit === "L" ? "L" : " oz"}`)}
        </span>
      </button>`);
  }

  root.innerHTML = `
    <div class="cal-head">
      <div class="cal-title">${title}</div>
      <div class="cal-nav">
        <button data-nav="-1" title="Previous month" aria-label="Previous month">‹</button>
        <button data-nav="0">Today</button>
        <button data-nav="1" title="Next month" aria-label="Next month">›</button>
      </div>
      <div class="cal-key">
        <span class="stat mood" style="--c:#7cb342">${ICON.mood}mood</span>
        <span class="stat sleep">${ICON.sleep}sleep</span>
        <span class="stat energy">${ICON.energy}energy</span>
        <span class="stat water">${ICON.water}water</span>
      </div>
    </div>
    <div class="cal-grid" role="group" aria-label="${title}">
      ${(data.weekdays || ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]).map((n) => `<div class="dow">${n}</div>`).join("")}
      ${cells.join("")}
    </div>`;

  root.querySelectorAll("[data-nav]").forEach((b) => (b.onclick = () => send("nav", { dir: +b.dataset.nav })));
  root.querySelectorAll(".cell").forEach((b) => (b.onclick = () => {
    root.querySelectorAll(".cell.selected").forEach((c) => c.classList.remove("selected"));
    b.classList.add("selected");                       // instant feedback
    send("select", { date: b.dataset.date });
  }));
}
