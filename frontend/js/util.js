// Shared helpers. No framework, no build step.

export const api = {
  async get(path) {
    const response = await fetch(path);
    if (!response.ok) throw new Error(await describe(response));
    return response.json();
  },
  async post(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body ?? {}),
    });
    if (!response.ok) throw new Error(await describe(response));
    return response.json();
  },
};

async function describe(response) {
  try {
    const body = await response.json();
    return body.detail || `${response.status} ${response.statusText}`;
  } catch {
    return `${response.status} ${response.statusText}`;
  }
}

export const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]),
  );

export const pct = (value, digits = 0) =>
  value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;

export const num = (value, digits = 2) =>
  value === null || value === undefined ? "—" : Number(value).toFixed(digits);

export function when(iso, withYear = false) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    ...(withYear ? { year: "numeric" } : {}),
  });
}

export function relative(iso) {
  if (!iso) return "";
  const days = Math.round((new Date(iso) - Date.now()) / 86400000);
  if (Number.isNaN(days)) return "";
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  return days > 0 ? `in ${days} days` : `${-days} days ago`;
}

/** Mastery colour. The washed-out end means "not enough evidence", which has to
 *  look different from "weak" or the map tells students the wrong thing. */
export function masteryColour(mastery, uncertainty = 0) {
  if (mastery === null || mastery === undefined) return "#eef1f4";
  const hue = 6 + mastery * 128;
  const sat = Math.max(14, 58 - uncertainty * 40);
  const light = 86 - mastery * 24;
  return `hsl(${hue.toFixed(0)} ${sat.toFixed(0)}% ${light.toFixed(0)}%)`;
}

export function scoreColour(fraction) {
  if (fraction === null || fraction === undefined) return "var(--border-strong)";
  if (fraction >= 0.7) return "var(--good)";
  if (fraction >= 0.4) return "var(--warn)";
  return "var(--bad)";
}

const CHIPS = {
  released: ["good", "Graded"],
  overridden: ["info", "Reviewed"],
  escalated: ["warn", "Being reviewed"],
  pending: ["", "Pending"],
  pass: ["good", "pass"],
  fail: ["bad", "fail"],
  timeout: ["bad", "timed out"],
  crash: ["bad", "crashed"],
  oom: ["bad", "out of memory"],
  skipped: ["", "not run"],
  ok: ["good", "ok"],
  warn: ["warn", "check"],
  error: ["bad", "error"],
  mastered: ["good", "Solid"],
  developing: ["warn", "Getting there"],
  gap: ["bad", "Needs work"],
  uncertain: ["info", "Not enough evidence"],
  no_evidence: ["", "Not covered yet"],
};

export function chip(state, override) {
  const [kind, label] = CHIPS[state] || ["", state];
  return `<span class="chip ${kind}">${esc(override || label)}</span>`;
}

export function bar(value, colour) {
  const width = Math.max(0, Math.min(1, value ?? 0)) * 100;
  return `<span class="bar"><i style="width:${width.toFixed(1)}%;background:${colour}"></i></span>`;
}

export function meter(value) {
  return `<span class="meter">${bar(value, scoreColour(value))}<span class="v">${pct(value)}</span></span>`;
}

export function toast(message, kind = "") {
  document.querySelectorAll(".toast").forEach((t) => t.remove());
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  document.body.appendChild(node);
  setTimeout(() => node.remove(), kind === "bad" ? 6000 : 3200);
}

export function on(root, selector, event, handler) {
  root.querySelectorAll(selector).forEach((node) => node.addEventListener(event, handler));
}

export const REASONS = {
  signal_conflict: "Checks disagree",
  low_confidence: "Not confident enough",
  integrity_flag: "Similar to another submission",
  report_contradiction: "Report doesn't match the code",
  grade_boundary: "Sits on a grade boundary",
  repair_material: "Fixed a syntax error to run it",
  stage_error: "Something went wrong",
  appeal: "Student appealed",
};

export const reason = (key) => REASONS[key] || key;
