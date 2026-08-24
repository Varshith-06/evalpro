// The one page where operational numbers belong. Nobody else has to see them.

import { api, esc, num, pct, toast } from "../util.js";
import { setCrumbs } from "../app.js";

// The deadline view. Depth is the number waiting to be marked right now, which
// is the one number that moves when a class submits at once - and the one an
// administrator asks about when students say "it is stuck".
function queueTiles(q) {
  if (!q) return "";
  const busy = q.depth > 0;
  return `
    <div class="card pad0" style="margin-bottom:14px">
      <header><h2>Marking queue</h2></header>
      <div style="padding:12px 14px 4px"><div class="tiles">
      <div class="tile ${busy ? "warn" : "good"}">
        <div class="k">Waiting to be marked</div><div class="v">${q.depth}</div>
        <div class="n">${busy ? `longest wait ${(q.oldest_wait_ms / 1000).toFixed(0)}s` : "nothing queued"}</div></div>
      <div class="tile"><div class="k">Marking now</div>
        <div class="v">${q.running} <span class="faint" style="font-size:15px">/ ${q.workers}</span></div>
        <div class="n">at most ${q.max_per_assignment} per lab</div></div>
      <div class="tile"><div class="k">Typical wait</div>
        <div class="v">${(q.mean_wait_ms / 1000).toFixed(1)}s</div>
        <div class="n">before marking starts</div></div>
      <div class="tile ${q.failed ? "bad" : "good"}">
        <div class="k">Marked / failed</div>
        <div class="v" style="font-size:19px;padding-top:4px">${q.completed} / ${q.failed}</div>
        <div class="n">busiest backlog ${q.peak_depth}</div></div>
      </div></div>
    </div>`;
}


export async function render(root, ctx) {
  setCrumbs([{ label: "System" }]);
  const [health, metrics, audit] = await Promise.all([
    api.get("/api/admin/system-health"),
    api.get(`/api/admin/courses/${ctx.courseId}/metrics`),
    api.get(`/api/admin/courses/${ctx.courseId}/bias-audit`),
  ]);
  const isolation = health.isolation;

  root.innerHTML = `
    <div class="page-head">
      <div><h1>System</h1><div class="sub">How the marking is behaving.</div></div>
      <div class="actions"><button class="btn" id="refresh">Recalculate</button></div>
    </div>

    <div class="tiles">
      <div class="tile"><div class="k">Submissions marked</div><div class="v">${health.total_runs}</div></div>
      <div class="tile ${health.review_queue_depth ? "warn" : "good"}">
        <div class="k">Waiting for review</div><div class="v">${health.review_queue_depth}</div></div>
      <div class="tile"><div class="k">Typical marking time</div>
        <div class="v">${(health.median_latency_ms / 1000).toFixed(1)}s</div>
        <div class="n">per submission</div></div>
      <div class="tile ${audit.passed ? "good" : "bad"}"><div class="k">Fairness check</div>
        <div class="v" style="font-size:17px;padding-top:6px">${audit.passed ? "Passed" : "Failed"}</div>
        <div class="n">largest gap ${pct(audit.max_flag_rate_delta, 1)}</div></div>
    </div>

    ${queueTiles(health.grading_queue)}

    ${
      audit.deployment_blocked
        ? `<div class="note bad" style="margin-bottom:14px"><strong>Risk flagging is switched off.</strong>
             ${esc(audit.note)}</div>`
        : ""
    }

    <div class="grid g2">
      <div class="card pad0">
        <header><h2>Marking quality</h2></header>
        <div class="scroll-x"><table>
          <thead><tr><th>Measure</th><th class="num">Now</th><th>Target</th><th></th></tr></thead>
          <tbody>${metrics.metrics.map(metricRow).join("")}</tbody>
        </table></div>
      </div>

      <div class="card pad0">
        <header><h2>Fairness by group</h2></header>
        ${
          audit.rows.length
            ? `<div class="scroll-x"><table>
                 <thead><tr><th>Attribute</th><th>Group</th><th class="num">n</th>
                   <th class="num">Flagged</th><th></th></tr></thead>
                 <tbody>${audit.rows
                   .map(
                     (r) => `<tr>
                       <td>${esc(r.attribute.replace(/_/g, " "))}</td>
                       <td>${esc(r.group)}</td>
                       <td class="num">${r.n}</td>
                       <td class="num">${pct(r.flag_rate)}</td>
                       <td><span class="chip ${r.status === "ok" ? "good" : "bad"}">${esc(r.status)}</span></td>
                     </tr>`,
                   )
                   .join("")}</tbody>
               </table></div>`
            : `<div class="empty">Not enough students per group to test this yet.</div>`
        }
      </div>
    </div>

    <div class="card pad0" style="margin-top:14px">
      <header><h2>Sandbox</h2>
        <span class="faint small">${isolation.applied_count} of ${isolation.total_layers} protections
          active on this machine</span></header>
      <div class="scroll-x"><table>
        <thead><tr><th class="num">#</th><th>Protection</th><th></th><th>Note</th></tr></thead>
        <tbody>${isolation.layers
          .map(
            (l) => `<tr>
              <td class="num faint">${l.layer}</td>
              <td class="small">${esc(l.control)}</td>
              <td>${l.applied ? `<span class="chip good">on</span>` : `<span class="chip">needs a server</span>`}</td>
              <td class="small faint">${esc(l.note)}</td>
            </tr>`,
          )
          .join("")}</tbody>
      </table></div>
    </div>`;

  root.querySelector("#refresh").addEventListener("click", async (event) => {
    event.target.disabled = true;
    event.target.textContent = "Recalculating…";
    try {
      await api.post(`/api/admin/courses/${ctx.courseId}/refresh`);
      toast("Done.");
      render(root, ctx);
    } catch (error) {
      toast(error.message, "bad");
      event.target.disabled = false;
      event.target.textContent = "Recalculate";
    }
  });
}

const FRIENDLY = {
  auto_release_coverage: "Marked without a human",
  override_rate: "Marks changed after release",
  qwk: "Agreement with human marking",
  appeal_rate: "Students who appealed",
  appeals_upheld: "Appeals upheld",
  integrity_false_flag_rate: "Similarity flags later cleared",
  mastery_predictive_validity: "Progress model predicts next lab",
  p95_latency_s: "Slowest marking time",
  faculty_minutes_per_assignment: "Staff time per assignment",
  confidence_model_examples: "Examples learned from",
};

function metricRow(metric) {
  const value =
    metric.value === null || metric.value === undefined
      ? "—"
      : metric.unit === "%"
        ? pct(metric.value, 1)
        : metric.unit === "s"
          ? `${num(metric.value, 1)}s`
          : metric.unit === "min"
            ? `${num(metric.value, 0)} min`
            : metric.unit === "rows"
              ? num(metric.value, 0)
              : num(metric.value, 2);
  return `<tr>
    <td class="small">${esc(FRIENDLY[metric.key] || metric.label)}</td>
    <td class="num">${value}</td>
    <td class="small faint">${esc(metric.target)}</td>
    <td>${
      metric.meets_target === null || metric.meets_target === undefined
        ? `<span class="chip">tracked</span>`
        : metric.meets_target
          ? `<span class="chip good">on target</span>`
          : `<span class="chip warn">below</span>`
    }</td>
  </tr>`;
}
