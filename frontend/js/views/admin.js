// Administrator view. One question: where is this programme weak?
//
// Two things on this surface are deliberately uncomfortable and deliberately
// prominent: the bias audit, which can say the risk model must not be deployed,
// and the platform trust metrics, which include the numbers that would show the
// grader drifting away from human judgement.

import { api, esc, num, openDrawer, pct } from "../util.js";
import { histogram } from "../charts.js";

export async function render(container, { courseId }) {
  container.innerHTML = '<div class="loading">Computing attainment and risk…</div>';

  const [attainment, risk, metrics, integrity, trends, health, audit] = await Promise.all([
    api.get(`/api/admin/courses/${courseId}/attainment`),
    api.get(`/api/admin/courses/${courseId}/risk`),
    api.get(`/api/admin/courses/${courseId}/metrics`),
    api.get(`/api/admin/courses/${courseId}/integrity`),
    api.get(`/api/admin/courses/${courseId}/trends`),
    api.get("/api/admin/system-health"),
    api.get(`/api/admin/courses/${courseId}/bias-audit`),
  ]);

  const summary = attainment.summary;
  const isolation = health.isolation;

  container.innerHTML = `
    <div class="headline">
      <h1>${esc(risk.question)}</h1>
      <p>Direct CO–PO attainment computed from performance evidence, with per-student traceability down to
         individual submissions — not reconstructed from a spreadsheet at the end of semester.</p>
    </div>

    <div class="stats">
      <div class="stat ${summary.cos_attained === summary.cos_total ? "good" : "warn"}">
        <div class="label">COs attained</div><div class="value">${summary.cos_attained}/${summary.cos_total}</div>
        <div class="sub">at level 2 or above</div></div>
      <div class="stat"><div class="label">Mean attainment</div><div class="value">${pct(summary.mean_attainment)}</div>
        <div class="sub">weakest: ${esc(summary.weakest_co || "—")}</div></div>
      <div class="stat ${risk.flagged ? "warn" : "good"}"><div class="label">Students flagged</div>
        <div class="value">${risk.flagged}</div><div class="sub">of ${risk.cohort_size}, routed to support</div></div>
      <div class="stat ${audit.passed ? "good" : "bad"}"><div class="label">Bias audit</div>
        <div class="value" style="font-size:16px">${audit.passed ? "within tolerance" : "OVER THRESHOLD"}</div>
        <div class="sub">max flag-rate delta ${pct(audit.max_flag_rate_delta, 1)}</div></div>
      <div class="stat"><div class="label">Sandbox layers</div>
        <div class="value">${isolation.applied_count}/${isolation.total_layers}</div>
        <div class="sub">applied on this host</div></div>
    </div>

    ${
      audit.deployment_blocked
        ? `<div class="callout bad"><strong>Risk model deployment blocked.</strong> ${esc(audit.note)}</div>`
        : `<div class="callout"><strong>Bias audit.</strong> ${esc(audit.note)}</div>`
    }

    <div class="grid two">
      <div class="card">
        <header><h2>Course outcome attainment</h2><span class="hint">${esc(attainment.method)} · threshold ${pct(attainment.attainment_threshold)}</span></header>
        <div class="table-scroll"><table>
          <thead><tr><th>CO</th><th>Outcome</th><th class="num">Mean mastery</th><th class="num">Attaining</th><th>Level</th></tr></thead>
          <tbody>${attainment.course_outcomes
            .map(
              (co) => `<tr class="clickable" data-co="${esc(co.code)}">
                <td><strong>${esc(co.code)}</strong></td>
                <td class="small">${esc(co.text)}</td>
                <td class="num">${pct(co.mean_mastery)}</td>
                <td class="num">${co.students_attaining}/${co.cohort_size}</td>
                <td><span class="chip ${co.level >= 3 ? "good" : co.level === 2 ? "warn" : "bad"}">L${co.level}</span></td>
              </tr>`,
            )
            .join("")}</tbody></table></div>
        <div class="note">${esc(attainment.method_note)}</div>
      </div>

      <div class="card">
        <header><h2>Programme outcome attainment</h2><span class="hint">rolled up through the CO–PO matrix</span></header>
        <div class="table-scroll"><table>
          <thead><tr><th>PO</th><th class="num">Weighted attainment</th><th>Level</th><th>Contributing COs</th></tr></thead>
          <tbody>${attainment.programme_outcomes
            .map(
              (po) => `<tr>
                <td><strong>${esc(po.code)}</strong></td>
                <td class="num">${pct(po.weighted_attainment)}</td>
                <td><span class="chip ${po.level >= 3 ? "good" : po.level === 2 ? "warn" : "bad"}">L${po.level}</span></td>
                <td class="small dim">${po.contributing_cos
                  .map((c) => `${esc(c.co)} (w${c.correlation_weight})`)
                  .join(", ")}</td>
              </tr>`,
            )
            .join("")}</tbody></table></div>
      </div>
    </div>

    <div class="card" style="margin-top:16px">
      <header><h2>Platform trust metrics</h2>
        <span class="hint">is the grader still agreeing with humans this semester?</span></header>
      <div class="table-scroll"><table>
        <thead><tr><th>Metric</th><th class="num">Value</th><th>Target</th><th></th><th>Why it is tracked</th></tr></thead>
        <tbody>${metrics.metrics
          .map(
            (m) => `<tr>
              <td><strong>${esc(m.label)}</strong></td>
              <td class="num">${_formatMetric(m)}</td>
              <td class="small dim">${esc(m.target)}</td>
              <td>${
                m.meets_target === null || m.meets_target === undefined
                  ? '<span class="chip mute">tracked</span>'
                  : m.meets_target
                    ? '<span class="chip good">meets</span>'
                    : '<span class="chip warn">below</span>'
              }</td>
              <td class="small dim">${esc(m.why)}</td>
            </tr>`,
          )
          .join("")}</tbody></table></div>
      <div class="note">${esc(metrics.note)}</div>
    </div>

    <div class="grid two" style="margin-top:16px">
      <div class="card">
        <header><h2>At-risk students</h2><span class="hint">ranked factors, routed to support</span></header>
        <div class="callout small">${esc(risk.policy)}</div>
        <div class="table-scroll"><table>
          <thead><tr><th>Student</th><th class="num">Risk</th><th>Routed to</th><th>Top factor</th></tr></thead>
          <tbody id="risk-rows"></tbody></table></div>
      </div>
      <div class="card">
        <header><h2>Bias audit detail</h2><span class="hint">protected attributes are audited, never used as features</span></header>
        ${
          audit.rows.length
            ? `<div class="table-scroll"><table>
                <thead><tr><th>Attribute</th><th>Group</th><th class="num">n</th><th class="num">Flag rate</th><th class="num">Δ</th><th></th></tr></thead>
                <tbody>${audit.rows
                  .map(
                    (row) => `<tr>
                      <td>${esc(row.attribute.replace(/_/g, " "))}</td>
                      <td>${esc(row.group)}</td>
                      <td class="num">${row.n}</td>
                      <td class="num">${pct(row.flag_rate)}</td>
                      <td class="num">${row.delta_vs_baseline >= 0 ? "+" : ""}${pct(row.delta_vs_baseline, 1)}</td>
                      <td><span class="chip ${row.status === "ok" ? "good" : "bad"}">${esc(row.status)}</span></td>
                    </tr>`,
                  )
                  .join("")}</tbody></table></div>`
            : '<div class="empty">Not enough students per group to test parity. That is an inconclusive audit, not a passing one.</div>'
        }
      </div>
    </div>

    <div class="grid two" style="margin-top:16px">
      <div class="card">
        <header><h2>Assignment trends</h2><span class="hint">distribution shape per lab</span></header>
        <div id="trend-charts"></div>
      </div>
      <div class="card">
        <header><h2>Integrity dashboard</h2><span class="hint">ranked evidence, never a determination</span></header>
        <div class="callout small">${esc(integrity.policy)}</div>
        ${
          integrity.pairs.length
            ? `<div class="table-scroll"><table>
                <thead><tr><th>Lab</th><th>Pair</th><th class="num">Combined</th><th class="num">Token</th><th class="num">Structural</th></tr></thead>
                <tbody>${integrity.pairs
                  .slice(0, 12)
                  .map(
                    (pair) => `<tr>
                      <td>${esc(pair.assignment)}</td>
                      <td class="small">${esc(pair.student_a)} ↔ ${esc(pair.student_b)}</td>
                      <td class="num">${pct(pair.combined)}</td>
                      <td class="num">${pct(pair.token_similarity)}</td>
                      <td class="num">${pct(pair.structural_similarity)}</td>
                    </tr>`,
                  )
                  .join("")}</tbody></table></div>`
            : '<div class="empty">No pair stands out against the cohort distribution.</div>'
        }
      </div>
    </div>

    <div class="card" style="margin-top:16px">
      <header><h2>System health and sandbox isolation</h2>
        <span class="hint">what is actually enforced on this host, layer by layer</span></header>
      <div class="stats" style="margin-bottom:14px">
        <div class="stat"><div class="label">Runs</div><div class="value">${health.total_runs}</div></div>
        <div class="stat"><div class="label">Queue depth</div><div class="value">${health.review_queue_depth}</div></div>
        <div class="stat"><div class="label">p95 latency</div><div class="value">${(health.p95_latency_ms / 1000).toFixed(1)}s</div></div>
        <div class="stat"><div class="label">Observations</div><div class="value">${health.observations}</div>
          <div class="sub">concept-level evidence</div></div>
      </div>
      <div class="callout small">
        Backend <strong>${esc(isolation.backend)}</strong> on ${esc(isolation.host)}.
        The oracle is held outside the guest and instances are one-shot on every backend.
        Layers reported as not applied are honestly not applied on a developer machine — the production
        backend supplies them.
      </div>
      <div class="table-scroll"><table>
        <thead><tr><th class="num">Layer</th><th>Control</th><th></th><th>Note</th></tr></thead>
        <tbody>${isolation.layers
          .map(
            (layer) => `<tr>
              <td class="num">${layer.layer}</td>
              <td class="small">${esc(layer.control)}</td>
              <td>${layer.applied ? '<span class="chip good">applied</span>' : '<span class="chip mute">not on this host</span>'}</td>
              <td class="small dim">${esc(layer.note)}</td>
            </tr>`,
          )
          .join("")}</tbody></table></div>
    </div>`;

  const riskRows = container.querySelector("#risk-rows");
  riskRows.innerHTML = risk.students
    .slice(0, 14)
    .map((student) => {
      const top = (student.contributing_factors || [])[0];
      return `<tr class="clickable" data-student="${esc(student.student_id)}">
        <td><strong>${esc(student.student_name)}</strong></td>
        <td class="num" style="color:${student.flagged ? "var(--bad)" : "inherit"}">${num(student.risk_score)}</td>
        <td>${student.flagged ? `<span class="chip warn">${esc(student.routed_to.replace(/_/g, " "))}</span>` : '<span class="chip mute">—</span>'}</td>
        <td class="small dim">${esc(top ? top.factor : "no factor above threshold")}</td>
      </tr>`;
    })
    .join("");
  riskRows.querySelectorAll("tr[data-student]").forEach((row) =>
    row.addEventListener("click", () => {
      const student = risk.students.find((s) => s.student_id === row.dataset.student);
      showRisk(student);
    }),
  );

  const trendHost = container.querySelector("#trend-charts");
  trends.assignments.forEach((assignment) => {
    const block = document.createElement("div");
    block.style.marginBottom = "14px";
    block.innerHTML = `<div class="small"><strong>${esc(assignment.assignment)}</strong>
      <span class="faint">${esc(assignment.title)} · ${assignment.n} submissions ·
      ${assignment.released} auto-released, ${assignment.escalated} escalated · ${esc(assignment.shape)}</span></div>`;
    block.appendChild(histogram(assignment, { width: 380, height: 110 }));
    trendHost.appendChild(block);
  });

  container.querySelectorAll("tr[data-co]").forEach((row) =>
    row.addEventListener("click", () => {
      const co = attainment.course_outcomes.find((c) => c.code === row.dataset.co);
      showOutcome(co);
    }),
  );
}

function _formatMetric(metric) {
  if (metric.value === null || metric.value === undefined) return "—";
  if (metric.unit === "%") return pct(metric.value, 1);
  if (metric.unit === "s") return `${num(metric.value, 1)} s`;
  if (metric.unit === "min") return `${num(metric.value, 0)} min`;
  if (metric.unit === "rows") return num(metric.value, 0);
  return num(metric.value, 3);
}

function showRisk(student) {
  if (!student) return;
  openDrawer(`
    <div class="headline"><h1>${esc(student.student_name)}</h1>
      <p>Risk score ${num(student.risk_score)} · ${
        student.flagged ? `routed to <strong>${esc(student.routed_to.replace(/_/g, " "))}</strong>` : "not flagged"
      }</p></div>
    <div class="callout">Early warning routes to support, never to sanction. This view exists so that a person
      can start a useful conversation, and it is never a basis for a penalty.</div>
    <div class="card"><header><h2>Contributing factors, ranked</h2></header>
      ${(student.contributing_factors || [])
        .map(
          (factor) => `<div class="action">
            <div class="action-head"><span class="action-title">${esc(factor.factor)}</span>
              <span class="chip mute">weight ${num(factor.contribution, 3)}</span></div>
            <div class="action-why">${esc(factor.detail)}</div></div>`,
        )
        .join("")}
    </div>`);
}

function showOutcome(co) {
  if (!co) return;
  openDrawer(`
    <div class="headline"><h1>${esc(co.code)}</h1><p>${esc(co.text)}</p></div>
    <div class="stats">
      <div class="stat"><div class="label">Mean mastery</div><div class="value">${pct(co.mean_mastery)}</div></div>
      <div class="stat"><div class="label">Attaining</div><div class="value">${co.students_attaining}/${co.cohort_size}</div></div>
      <div class="stat"><div class="label">Level</div><div class="value">L${co.level}</div>
        <div class="sub">${esc(co.level_label)}</div></div>
      <div class="stat"><div class="label">Evidence</div><div class="value">${co.evidence_count}</div>
        <div class="sub">student-concept observations</div></div>
    </div>
    <div class="card"><header><h2>Concepts mapped to this outcome</h2>
      <span class="hint">attainment is a rollup over these, not a separate calculation</span></header>
      <div class="chips">${co.concepts.map((c) => `<span class="chip info">${esc(c)}</span>`).join(" ")}</div>
    </div>`);
}
