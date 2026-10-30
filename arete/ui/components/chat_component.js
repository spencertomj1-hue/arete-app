// Arete chat bar: a Claude.ai-style input pinned to the bottom of the main
// area, with a collapsible history panel above it.
//
// Python passes { messages: [{role, text}], pending: [{id, summary}],
//                 busy: null | "thinking" | "transcribing", recording, error }.
// Triggers back to Python:
//   send    {text, nonce}             a typed message
//   mic     {action: "start"|"stop", nonce}
//   confirm {id, ok, nonce}           Confirm / Cancel on a preview

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2);

// sessionStorage can be unavailable; the bar works without it.
function remember(key, value) {
  try { sessionStorage.setItem(key, JSON.stringify(value)); } catch (e) {}
}
function recall(key) {
  try { return JSON.parse(sessionStorage.getItem(key)); } catch (e) { return null; }
}

const ICONS = {
  send: '<svg viewBox="0 0 24 24" width="16" height="16"><path d="M12 19V5M5 12l7-7 7 7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  mic: '<svg viewBox="0 0 24 24" width="17" height="17"><rect x="9" y="3" width="6" height="11" rx="3" fill="none" stroke="currentColor" stroke-width="2"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  stop: '<svg viewBox="0 0 24 24" width="14" height="14"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/></svg>',
  close: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M6 6l12 12M18 6 6 18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  history: '<svg viewBox="0 0 24 24" width="16" height="16"><path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" width="16" height="16"><path d="M6 15l6-6 6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

const instances = new WeakMap();

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const root = parentElement.querySelector(".cb-root");
  let bar = instances.get(root);
  if (!bar) {
    bar = new ChatBar(root);
    instances.set(root, bar);
  }
  bar.trigger = (name, value) => setTriggerValue(name, { ...value, nonce: uid() });
  bar.update(data);
  return () => bar.detach();
}

class ChatBar {
  constructor(root) {
    this.root = root;
    this.open = !!recall("cb-open");                 // collapsed by default
    this.seen = recall("cb-seen") || 0;              // messages already shown
    this.optimistic = null;                          // text sent but not yet echoed back
    this.build();
    this.onResize = () => this.place();
    this.onKey = (e) => { if (e.key === "Escape" && this.historyOpen) this.setOpen(false); };
  }

  build() {
    this.root.innerHTML = `
      <div class="cb-dock">
        <div class="cb-backdrop"></div>
        <div class="cb-panel">
          <div class="cb-head">
            <span>Arete</span>
            <button class="cb-close" title="Hide chat" aria-label="Hide chat">${ICONS.chevron}</button>
          </div>
          <div class="cb-log" role="log" aria-live="polite" aria-label="Chat history"></div>
        </div>
        <div class="cb-error" role="alert"></div>
        <div class="cb-bar">
          <button class="cb-toggle" title="Show chat history" aria-label="Show chat history">${ICONS.chevron}</button>
          <textarea rows="1" placeholder="Ask Arete..." aria-label="Message to Arete (Enter sends, Shift+Enter for a new line)"></textarea>
          <button class="cb-mic" title="Speak" aria-label="Speak (record a message)">${ICONS.mic}</button>
          <button class="cb-send" title="Send" aria-label="Send">${ICONS.send}</button>
        </div>
        <div class="cb-latest" aria-live="polite" aria-label="Latest reply"></div>
      </div>`;
    const $ = (s) => this.root.querySelector(s);
    this.dock = $(".cb-dock");
    this.panel = $(".cb-panel");
    this.log = $(".cb-log");
    this.input = $("textarea");
    this.sendBtn = $(".cb-send");
    this.micBtn = $(".cb-mic");
    this.errorEl = $(".cb-error");
    this.latest = $(".cb-latest");
    this.toggleBtn = $(".cb-toggle");

    this.input.value = recall("cb-draft") || "";
    this.input.addEventListener("input", () => {
      this.autosize();
      remember("cb-draft", this.input.value);
    });
    this.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        this.send();
      }
    });
    this.sendBtn.addEventListener("click", () => this.send());
    this.micBtn.addEventListener("click", () => this.toggleMic());
    $(".cb-toggle").addEventListener("click", () => this.setOpen(!this.open));
    $(".cb-close").addEventListener("click", () => this.setOpen(false));
    $(".cb-backdrop").addEventListener("click", () => this.setOpen(false));
    this.dock.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-confirm]");
      if (!btn) return;
      btn.closest(".cb-pending").classList.add("done");     // instant feedback
      this.trigger("confirm", { id: btn.dataset.confirm, ok: btn.dataset.ok === "1" });
    });
  }

  update(data) {
    this.data = data;
    // Inline (Home page): just the box, with the latest reply under it and the
    // full history in a pop-up behind the history button.
    this.dock.classList.toggle("inline", !!data.inline);
    const n = data.messages.length;
    // Bottom bar: a new reply, or a preview waiting for Confirm -> open the panel.
    if (!data.inline && n > this.seen && data.messages[n - 1].role !== "user") this.setOpen(true);
    if (!data.inline && data.pending.length) this.setOpen(true);
    this.seen = n;
    remember("cb-seen", n);
    if (this.optimistic && data.messages.some((m) => m.role === "user" && m.text === this.optimistic)) {
      this.optimistic = null;
    }
    if (!data.busy) this.optimistic = null;
    this.render();
    this.attach();
    this.dock.classList.toggle("open", data.inline ? !!this.historyOpen : this.open);
    this.place();
  }

  // ---------- Rendering ----------

  render() {
    const { messages, pending, busy, recording, error, inline } = this.data;
    const msg = (m) => `<div class="cb-msg ${m.role}">${esc(m.text).replace(/\n/g, "<br>")}</div>`;
    const items = messages.map(msg);
    if (this.optimistic) items.push(`<div class="cb-msg user">${esc(this.optimistic)}</div>`);
    const extras = [];   // previews and the thinking line (in the log, or under the box inline)
    for (const p of pending) {
      extras.push(`
        <div class="cb-pending">
          <div class="cb-pending-text">${esc(p.summary)}</div>
          <div class="cb-pending-actions">
            <button data-confirm="${esc(p.id)}" data-ok="0">Cancel</button>
            <button data-confirm="${esc(p.id)}" data-ok="1" class="primary">Confirm</button>
          </div>
        </div>`);
    }
    const label = busy === "transcribing" ? "Transcribing" : "Thinking";
    const working = !!busy || !!this.optimistic;
    if (working) extras.push(`<div class="cb-thinking">${label}<i></i><i></i><i></i></div>`);

    if (inline) {
      // Under the box: only the latest reply (until you send again), plus any previews.
      const last = messages[messages.length - 1];
      const reply = !working && last && last.role !== "user" ? msg(last) : "";
      this.latest.innerHTML = reply + extras.join("");
      if (!items.length) items.push('<div class="cb-empty">No messages yet.</div>');
      this.log.innerHTML = items.join("");
    } else {
      this.latest.innerHTML = "";
      items.push(...extras);
      if (!items.length) items.push('<div class="cb-empty">Ask Arete to log your day, add timetable blocks, tick habits…</div>');
      this.log.innerHTML = items.join("");
    }
    this.log.scrollTop = this.log.scrollHeight;
    this.toggleBtn.innerHTML = inline ? ICONS.history : ICONS.chevron;
    this.root.querySelector(".cb-close").innerHTML = inline ? ICONS.close : ICONS.chevron;
    this.root.querySelector(".cb-head span").textContent = inline ? "Chat history" : "Arete";
    this.toggleBtn.title = inline ? "Chat history" : "Show chat history";

    this.errorEl.textContent = error || "";
    this.errorEl.classList.toggle("show", !!error);
    this.sendBtn.disabled = working;
    this.micBtn.disabled = working && !recording;
    this.micBtn.classList.toggle("rec", !!recording);
    this.micBtn.innerHTML = recording ? ICONS.stop : ICONS.mic;
    this.micBtn.title = recording ? "Stop and send" : "Speak";
    this.input.placeholder = recording ? "Listening… click the red button to send" : "Ask Arete...";
    this.autosize();
  }

  setOpen(open) {
    if (this.data && this.data.inline) {      // the history pop-up; not remembered
      this.historyOpen = open;
      this.dock.classList.toggle("open", open);
      if (open) this.log.scrollTop = this.log.scrollHeight;
      return;
    }
    this.open = open;
    remember("cb-open", open);
    this.dock.classList.toggle("open", open);
  }

  autosize() {
    this.input.style.height = "auto";
    this.input.style.height = Math.min(this.input.scrollHeight, 160) + "px";
  }

  // ---------- Actions ----------

  send() {
    const text = this.input.value.trim();
    if (!text || this.sendBtn.disabled) return;
    this.optimistic = text;
    this.input.value = "";
    remember("cb-draft", "");
    if (!this.data.inline) this.setOpen(true);
    this.render();
    this.trigger("send", { text });
  }

  toggleMic() {
    const recording = this.micBtn.classList.contains("rec");
    // Flip the button straight away; Python confirms (or reports an error).
    this.micBtn.classList.toggle("rec", !recording);
    this.micBtn.innerHTML = recording ? ICONS.mic : ICONS.stop;
    if (recording && !this.data.inline) this.setOpen(true);
    this.trigger("mic", { action: recording ? "stop" : "start" });
  }

  // ---------- Placement ----------

  // Pin the bar to the bottom of the main content column (not over the sidebar).
  place() {
    if (this.data && this.data.inline) {
      this.dock.style.left = this.dock.style.width = "";
      return;
    }
    const main = document.querySelector('[data-testid="stMainBlockContainer"]') ||
                 document.querySelector(".stMainBlockContainer");
    if (!main) return;
    const r = main.getBoundingClientRect();
    const cs = getComputedStyle(main);
    const left = r.left + parseFloat(cs.paddingLeft);
    const width = r.width - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    this.dock.style.left = `${left}px`;
    this.dock.style.width = `${width}px`;
    if (!this.observer) {
      this.observer = new ResizeObserver(this.onResize);
      this.observer.observe(main);
    }
  }

  attach() {
    if (this.attached) return;
    window.addEventListener("resize", this.onResize);
    window.addEventListener("keydown", this.onKey);
    if (this.observer) {
      const main = document.querySelector('[data-testid="stMainBlockContainer"]');
      if (main) this.observer.observe(main);
    }
    this.dock.classList.toggle("open", this.open);
    this.attached = true;
  }

  detach() {
    window.removeEventListener("resize", this.onResize);
    window.removeEventListener("keydown", this.onKey);
    if (this.observer) this.observer.disconnect();
    this.attached = false;
  }
}
