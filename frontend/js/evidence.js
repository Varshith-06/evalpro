// The evidence trail. Shared by the student drill-in and the faculty review,
// because a grade a student can't interrogate is one you'll spend more time
// defending than you saved generating — so both roles see the same trail.

import { esc, meter, num, pct, stateChip, humanReason } from "./util.js";

export function verdictHeader(detail, { role = "student" } = {}) {
  const verdict = detail.verdict;
  const reasons = (verdict.escalation_reasons || [])
    .map((r) => `<span class="chip warn">${esc(humanReason(r))}</span>`)
    .join(" ");
  return `
    <div class="headline">
      <h1>${esc(detail.assignment.code)} · ${esc(detail.assignment.title)}</h1>
      <p>
        Attempt ${detail.attempt_no} · ${stateChip(verdict.state)} ${reasons}
        ${verdict.integrity_flag ? '<span class="chip bad">similarity outlier</span>' : ""}
      </p>
    </div>
    <div class="stats">
      <div class="stat"><div class="label">Score</div>
        <div class="value">${pct(verdict.total_fraction)}</div>
        <div class="sub">${num(verdict.total_points, 1)} / ${num(verdict.max_points, 1)} points</div></div>
      <div class="stat"><div class="label">Confidence</div>
        <div class="value">${num(verdict.confidence)}</div>
        <div class="sub">${verdict.state === "released" ? "above the release threshold" : "below the release threshold"}</div></div>
      ${
        verdict.syntax_penalty > 0
          ? `<div class="stat warn"><div class="label">Syntax penalty</div>
              <div class="value">${pct(verdict.syntax_penalty)}</div>
              <div class="sub">from repair distance, not a zero</div></div>`
          : ""
      }
      <div class="stat"><div class="label">Latency</div>
        <div class="value">${(detail.duration_ms / 1000).toFixed(1)}s</div>
        <div class="sub">whole cascade</div></div>
    </div>
    ${detail.student_note ? `<div class="callout warn">${esc(detail.student_note)}</div>` : ""}
    ${verdict.override_reason ? `<div class="callout"><strong>Faculty override:</strong> ${esc(verdict.override_reason)}</div>` : ""}
    <div class="callout small">
      <strong>Reproducible.</strong> Pipeline ${esc(detail.reproducibility.pipeline_version)},
      rubric v${esc(detail.reproducibility.rubric_version)},
      content hash <span class="mono">${esc((detail.reproducibility.content_hash || "").slice(0, 16))}</span>.
      ${esc(detail.reproducibility.note)}
    </div>`;
}

export function rubricBreakdown(detail, { onAppeal = null, onOverride = null } = {}) {
  const rows = detail.items
    .map((item) => {
      const effective = item.faculty_score_fraction ?? item.score_fraction;
      const signals = (item.signals || [])
        .map(
          (s) =>
            `<span class="chip mute" title="reliability ${s.reliability}">${esc(s.source)} ${pct(s.score)}</span>`,
        )
        .join(" ");
      const evidence = (item.evidence || [])
        .map((line) => `<div class="evidence-line">${esc(line)}</div>`)
        .join("");
      const actions = [];
      if (onAppeal) actions.push(`<button class="btn" data-appeal="${esc(item.item_key)}">Appeal</button>`);
      if (onOverride) actions.push(`<button class="btn" data-override="${esc(item.item_key)}">Override</button>`);
      return `<details class="stage">
        <summary>
          <span class="stage-name">${esc(item.item_key)}</span>
          <span class="stage-summary">${esc(_itemText(detail, item))}</span>
          <span class="nowrap">${meter(effective, effective >= 0.7 ? "var(--good)" : effective >= 0.35 ? "var(--warn)" : "var(--bad)")}</span>
          <span class="chip ${item.confidence >= 0.75 ? "good" : "warn"}">conf ${num(item.confidence)}</span>
        </summary>
        <div class="stage-body">
          <div class="chips" style="margin-bottom:8px">
            ${signals}
            <span class="chip mute">agreement ${num(item.signal_agreement)}</span>
            <span class="chip mute">weight ${num(item.weight, 0)}</span>
            ${(item.concepts || []).map((c) => `<span class="chip info">${esc(c)}</span>`).join(" ")}
          </div>
          ${evidence || '<div class="faint small">No evidence lines recorded.</div>'}
          ${
            item.faculty_reason
              ? `<div class="callout small" style="margin-top:10px"><strong>Faculty:</strong> ${esc(item.faculty_reason)}</div>`
              : ""
          }
          ${actions.length ? `<div class="row-actions" style="margin-top:10px">${actions.join("")}</div>` : ""}
        </div>
      </details>`;
    })
    .join("");

  return `<div class="card"><header><h2>Rubric breakdown</h2>
      <span class="hint">every item traces to the evidence that produced it</span></header>
      ${rows}</div>`;
}

function _itemText(detail, item) {
  // The rubric text lives on the version; the run stores keys. Fall back to the
  // key so a breakdown never renders blank.
  return item.item_text || item.item_key;
}

export function testTable(detail) {
  if (!detail.tests.length) {
    return `<div class="card"><header><h2>Tests</h2></header>
      <div class="empty">No tests were executed for this run.</div></div>`;
  }
  const rows = detail.tests
    .map(
      (test) => `<tr>
        <td class="mono">${esc(test.test_key)}</td>
        <td><span class="chip mute">${esc(test.category)}</span></td>
        <td>${stateChip(test.outcome)}${test.on_repaired_source ? ' <span class="chip info">on repaired source</span>' : ""}</td>
        <td class="mono small">${esc((test.expected || "").slice(0, 60))}</td>
        <td class="mono small">${esc((test.actual || "").slice(0, 60))}</td>
        <td class="num">${test.cpu_ms} ms</td>
      </tr>
      ${
        test.diff
          ? `<tr><td colspan="6"><pre class="code">${_colourDiff(test.diff)}</pre></td></tr>`
          : ""
      }`,
    )
    .join("");
  return `<div class="card"><header><h2>Tests</h2>
    <span class="hint">expected outputs never entered the sandbox — comparison happens on the host</span></header>
    <div class="table-scroll"><table>
      <thead><tr><th>Test</th><th>Category</th><th>Outcome</th><th>Expected</th><th>Actual</th><th class="num">CPU</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    ${detail.hidden_test_note ? `<div class="note">${esc(detail.hidden_test_note)}</div>` : ""}
    </div>`;
}

function _colourDiff(diff) {
  return esc(diff)
    .split("\n")
    .map((line) => {
      if (line.startsWith("+")) return `<span class="add">${line}</span>`;
      if (line.startsWith("-")) return `<span class="del">${line}</span>`;
      if (line.startsWith("@") || line.startsWith("---") || line.startsWith("+++))"))
        return `<span class="meta">${line}</span>`;
      return line;
    })
    .join("\n");
}

export function stageTrail(detail) {
  const stages = detail.stages
    .map((stage) => {
      return `<details class="stage">
        <summary>
          <span class="stage-name">${esc(stage.stage)}</span>
          <span class="stage-summary">${esc(stage.summary)}</span>
          ${stateChip(stage.status)}
          <span class="faint small nowrap">${stage.duration_ms} ms</span>
        </summary>
        <div class="stage-body">${_stageEvidence(stage)}</div>
      </details>`;
    })
    .join("");
  return `<div class="card"><header><h2>Evaluation cascade</h2>
    <span class="hint">each stage writes evidence; none writes a score</span></header>${stages}</div>`;
}

function _stageEvidence(stage) {
  const evidence = stage.evidence || {};
  const parts = [];

  if (evidence.policy) parts.push(`<div class="callout small">${esc(evidence.policy)}</div>`);
  if (evidence.note) parts.push(`<div class="note">${esc(evidence.note)}</div>`);
  if (evidence.exclusion_note) parts.push(`<div class="note">${esc(evidence.exclusion_note)}</div>`);

  if (evidence.syntax_errors?.length) {
    parts.push(
      `<div class="small dim">Parse diagnostics:</div><pre class="code">${evidence.syntax_errors
        .map((e) => esc(`${e.file}:${e.line}:${e.column} ${e.code}: ${e.message}`))
        .join("\n")}</pre>`,
    );
  }
  if (evidence.diagnostics?.length) {
    parts.push(
      `<pre class="code">${evidence.diagnostics
        .map((d) => esc(`${d.file}:${d.line}:${d.column} ${d.code}: ${d.message}`))
        .join("\n")}</pre>`,
    );
  }
  if (evidence.edits?.length) {
    parts.push(
      `<div class="small dim">Repair edits found (${evidence.edit_distance}):</div>` +
        evidence.edits
          .map((e) => `<div class="evidence-line">${esc(`${e.file ?? ""}:${e.line} ${e.kind} — ${e.detail}`)}</div>`)
          .join(""),
    );
  }
  if (evidence.algorithm_matches?.length) {
    parts.push(
      `<div class="chips">${evidence.algorithm_matches
        .map((m) => `<span class="chip info">${esc(m.algorithm)} ${pct(m.confidence)}</span>`)
        .join(" ")}</div>`,
    );
  }
  if (evidence.evidence?.length) {
    parts.push(evidence.evidence.map((line) => `<div class="evidence-line">${esc(line)}</div>`).join(""));
  }
  if (evidence.entailments?.length) {
    parts.push(
      `<div class="table-scroll"><table><thead><tr><th>Claim</th><th>Label</th><th>Against code fact</th></tr></thead><tbody>` +
        evidence.entailments
          .map(
            (e) => `<tr><td>${esc(e.claim)}</td>
              <td>${
                e.label === "contradicted"
                  ? '<span class="chip bad">contradicted</span>'
                  : e.label === "entailed"
                    ? '<span class="chip good">entailed</span>'
                    : '<span class="chip mute">unsupported</span>'
              }</td>
              <td class="small dim">${esc(e.explanation)}</td></tr>`,
          )
          .join("") +
        `</tbody></table></div>`,
    );
  }
  if (evidence.ranked?.length) {
    parts.push(
      `<div class="small dim">Top matches (${evidence.outlier ? "reported" : "not reported — not a cohort outlier"}):</div>` +
        evidence.ranked
          .slice(0, 3)
          .map(
            (r) =>
              `<div class="evidence-line">${pct(r.combined)} combined · token ${pct(r.token_similarity)} · structural ${pct(
                r.structural_similarity,
              )}${r.uninformative ? " · not enough distinctive content to compare" : ""}</div>`,
          )
          .join(""),
    );
  }
  if (evidence.static_pre_screen?.length) {
    parts.push(
      `<div class="small dim">Layer 0 pre-screen (advisory):</div>` +
        evidence.static_pre_screen
          .slice(0, 6)
          .map((f) => `<div class="evidence-line">${esc(`${f.file}:${f.line} ${f.category} — ${f.excerpt}`)}</div>`)
          .join(""),
    );
  }
  if (evidence.rationale?.length) {
    parts.push(evidence.rationale.map((line) => `<div class="evidence-line">${esc(line)}</div>`).join(""));
  }
  if (!parts.length) {
    parts.push(`<pre class="code">${esc(JSON.stringify(evidence, null, 2).slice(0, 1600))}</pre>`);
  }
  return parts.join("");
}

export function sourceView(detail, { reference = null } = {}) {
  const files = Object.entries(detail.files || {})
    .map(
      ([path, content]) =>
        `<details class="stage" open><summary><span class="stage-name">${esc(path)}</span></summary>
          <div class="stage-body"><pre class="code">${esc(content)}</pre></div></details>`,
    )
    .join("");
  const report = detail.report_text
    ? `<details class="stage"><summary><span class="stage-name">report</span>
        <span class="stage-summary">student's written report</span></summary>
        <div class="stage-body"><pre class="code">${esc(detail.report_text)}</pre></div></details>`
    : "";
  const ref = reference
    ? `<details class="stage"><summary><span class="stage-name">reference solution</span>
        <span class="stage-summary">faculty only</span></summary>
        <div class="stage-body"><pre class="code">${esc(reference)}</pre></div></details>`
    : "";
  return `<div class="card"><header><h2>Submission</h2></header>${files}${report}${ref}</div>`;
}
