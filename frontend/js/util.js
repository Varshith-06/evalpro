// Small shared helpers. No framework, no build step: the demo is one command.

export const api = {
  async get(path) {
    const response = await fetch(path);
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
    return response.json();
  },
  async post(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body ?? {}),
    });
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
    return response.json();
  },
};

export const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]),
  );

export const pct = (value, digits = 0) =>
  value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;

export const num = (value, digits = 2) =>
  value === null || value === undefined ? "—" : Number(value).toFixed(digits);

export const when = (iso) => {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
};

/** Mastery colour ramp. Deliberately not a red-green gradient alone: the
 *  desaturated end means "we don't know yet", which is a different message
 *  from "you are weak here" and must not look the same. */
export function masteryColour(mastery, uncertainty = 0) {
  if (mastery === null || mastery === undefined) return "#1d2530";
  const hue = 8 + mastery * 122; // red → green
  const sat = Math.max(18, 62 - uncertainty * 42);
  const light = 26 + mastery * 20;
  return `hsl(${hue.toFixed(0)} ${sat.toFixed(0)}% ${light.toFixed(0)}%)`;
}

export function stateChip(state) {
  const map = {
    mastered: ["good", "mastered"],
    developing: ["warn", "developing"],
    gap: ["bad", "gap"],
    uncertain: ["info", "uncertain"],
    no_evidence: ["mute", "no evidence"],
    released: ["good", "released"],
    escalated: ["warn", "escalated"],
    overridden: ["info", "faculty reviewed"],
    pending: ["mute", "pending"],
    pass: ["good", "pass"],
    fail: ["bad", "fail"],
    timeout: ["bad", "timeout"],
    crash: ["bad", "crash"],
    oom: ["bad", "oom"],
    skipped: ["mute", "not run"],
    ok: ["good", "ok"],
    warn: ["warn", "warn"],
    error: ["bad", "error"],
  };
  const [kind, label] = map[state] || ["mute", state];
  return `<span class="chip ${kind}">${esc(label)}</span>`;
}

export function bar(value, colour) {
  const width = Math.max(0, Math.min(1, value ?? 0)) * 100;
  return `<span class="bar"><i style="width:${width.toFixed(1)}%;background:${colour}"></i></span>`;
}

export function meter(value, colour = "var(--accent)") {
  return `<span class="meter">${bar(value, colour)}<span class="val">${pct(value)}</span></span>`;
}

export function el(html) {
  const template = document.createElement("template");
  template.innerHTML = html.trim();
  return template.content.firstElementChild;
}

export function openDrawer(html) {
  const drawer = document.getElementById("drawer");
  document.getElementById("drawer-body").innerHTML = html;
  drawer.hidden = false;
  drawer.querySelector(".drawer-panel").scrollTop = 0;
}

export function closeDrawer() {
  document.getElementById("drawer").hidden = true;
}

export function humanReason(reason) {
  return (
    {
      signal_conflict: "signals conflict",
      low_confidence: "low confidence",
      integrity_flag: "similarity outlier",
      report_contradiction: "report contradicts code",
      grade_boundary: "on a grade boundary",
      repair_material: "syntax penalty is material",
      stage_error: "a stage errored",
      appeal: "student appeal",
    }[reason] || reason
  );
}
