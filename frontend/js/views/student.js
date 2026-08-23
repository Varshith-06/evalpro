// Student view. One question: what should I work on next?
// The landing surface is the mastery map, not a grade list — scores are a
// drill-in, and uncertainty is shown rather than rounded away.

import { api, esc, num, openDrawer, pct, stateChip, when } from "../util.js";
import { masteryMap, trajectory } from "../charts.js";
import { rubricBreakdown, sourceView, stageTrail, testTable, verdictHeader } from "../evidence.js";

let state = { courseId: null, studentId: null, data: null };

export async function render(container, { courseId, studentId }) {
  state.courseId = courseId;
  state.studentId = studentId;
  container.innerHTML = '<div class="loading">Loading mastery map…</div>';

  const data = await api.get(`/api/student/${studentId}/courses/${courseId}`);
  state.data = data;
  const summary = data.summary;

  container.innerHTML = `
    <div class="headline">
      <h1>${esc(data.question)}</h1>
      <p><span class="who">${esc(data.student.name)}</span> · ${esc(data.student.external_id)} —
         your progress is tracked per concept, not per assignment, so a gap shows up wherever it actually is.</p>
    </div>

    <div class="stats">
      <div class="stat good"><div class="label">Mastered</div><div class="value">${summary.mastered}</div>
        <div class="sub">of ${summary.concepts_with_evidence} with evidence</div></div>
      <div class="stat warn"><div class="label">Developing</div><div class="value">${summary.developing}</div>
        <div class="sub">below the 70% threshold</div></div>
      <div class="stat bad"><div class="label">Gaps</div><div class="value">${summary.gaps}</div>
        <div class="sub">worth acting on now</div></div>
      <div class="stat"><div class="label">Not yet measured</div>
        <div class="value">${summary.uncertain + (summary.concepts_total - summary.concepts_with_evidence)}</div>
        <div class="sub">too little evidence to judge</div></div>
      <div class="stat"><div class="label">Mean mastery</div><div class="value">${pct(summary.mean_mastery)}</div>
        <div class="sub">across tracked concepts</div></div>
    </div>

    <div class="grid two">
      <div class="card">
        <header><h2>Your mastery map</h2>
          <span class="hint">laid out by teaching week · red outline marks a gap that blocks later concepts</span></header>
        <div id="map"></div>
        <div class="legend">
          <span><i class="swatch" style="background:hsl(8 62% 26%)"></i>low</span>
          <span><i class="swatch" style="background:hsl(69 40% 36%)"></i>partial</span>
          <span><i class="swatch" style="background:hsl(130 62% 46%)"></i>mastered</span>
          <span><i class="swatch" style="background:var(--bg-inset);border:1px solid var(--border)"></i>no evidence</span>
          <span>Faded colour means high uncertainty — we are not confident either way.</span>
        </div>
      </div>

      <div class="card">
        <header><h2>What to work on next</h2><span class="hint">ranked, with the reason</span></header>
        <div id="actions"></div>
      </div>
    </div>

    <div class="grid two" style="margin-top:16px">
      <div class="card">
        <header><h2>Assignments</h2><span class="hint">click a row for the full evidence trail</span></header>
        <div class="table-scroll"><table>
          <thead><tr><th>Lab</th><th>Due</th><th class="num">Attempts</th><th>Latest</th><th>Status</th><th class="num">Progress</th></tr></thead>
          <tbody id="assignment-rows"></tbody>
        </table></div>
      </div>
      <div class="card">
        <header><h2>Mastery over the semester</h2>
          <span class="hint">shaded band is uncertainty</span></header>
        <div id="trajectories"></div>
      </div>
    </div>

    <div class="callout small" style="margin-top:16px">${esc(data.disclosure)}</div>`;

  container.querySelector("#map").appendChild(
    masteryMap(data.mastery_map.nodes, data.mastery_map.edges, {
      onSelect: (conceptId) => showConcept(conceptId),
    }),
  );

  const actions = container.querySelector("#actions");
  if (!data.next_actions.length) {
    actions.innerHTML = '<div class="empty">Nothing is flagged. Every tracked concept is above the mastery threshold.</div>';
  } else {
    actions.innerHTML = data.next_actions
      .map(
        (action) => `<div class="action ${esc(action.action_kind)}">
          <div class="action-head">
            <span class="action-title">${esc(action.concept_name)}</span>
            <span class="chip ${action.action_kind === "diagnose" ? "info" : "warn"}">${
              action.action_kind === "diagnose" ? "diagnose first" : "practise"
            }</span>
          </div>
          <div class="action-why">${esc(action.why_flagged)}</div>
          <div class="action-do">${esc(action.recommended_action)}</div>
          <div class="action-meta">
            <span>mastery ${pct(action.mastery)}</span>
            <span>uncertainty ${pct(action.uncertainty)}</span>
            <span>${esc(action.estimated_effort)}</span>
            <span>${action.evidence_refs.length} piece(s) of evidence</span>
          </div>
          ${
            action.prerequisite_path.length > 1
              ? `<div class="path">traced: ${action.prerequisite_path.map(esc).join(" → ")}</div>`
              : ""
          }
        </div>`,
      )
      .join("");
  }

  const rows = container.querySelector("#assignment-rows");
  rows.innerHTML = data.assignments
    .map((a) => {
      const latest = a.latest;
      const delta = a.attempt_deltas.length ? a.attempt_deltas[a.attempt_deltas.length - 1] : null;
      return `<tr class="${latest ? "clickable" : ""}" data-run="${latest ? esc(latest.run_id) : ""}">
        <td><strong>${esc(a.code)}</strong><div class="faint small">${esc(a.title)}</div></td>
        <td class="small dim">${when(a.due_at)}</td>
        <td class="num">${a.attempts}</td>
        <td class="num">${latest ? pct(latest.score) : "—"}
          ${delta !== null && delta !== 0 ? `<div class="small ${delta > 0 ? "chip good" : "chip bad"}">${delta > 0 ? "+" : ""}${pct(delta)}</div>` : ""}</td>
        <td>${latest ? stateChip(latest.state) : '<span class="chip mute">not submitted</span>'}</td>
        <td class="num">${latest ? num(latest.confidence) : "—"}</td>
      </tr>`;
    })
    .join("");
  rows.querySelectorAll("tr[data-run]").forEach((row) => {
    if (!row.dataset.run) return;
    row.addEventListener("click", () => showRun(row.dataset.run));
  });

  const traj = container.querySelector("#trajectories");
  if (data.trajectories.length) {
    traj.appendChild(trajectory(data.trajectories.slice(0, 5)));
  } else {
    traj.innerHTML = '<div class="empty">Not enough observations yet to plot a trajectory.</div>';
  }
}

function showConcept(conceptId) {
  const node = state.data.mastery_map.nodes.find((n) => n.id === conceptId);
  if (!node) return;
  const action = state.data.next_actions.find((a) => a.concept === conceptId);
  openDrawer(`
    <div class="headline"><h1>${esc(node.name)}</h1>
      <p>${node.week ? `taught in week ${node.week}` : "unscheduled"} ·
        ${(node.outcomes || []).map((o) => `<span class="chip info">${esc(o)}</span>`).join(" ")}</p></div>
    <div class="stats">
      <div class="stat"><div class="label">Mastery</div><div class="value">${pct(node.mastery)}</div>
        <div class="sub">${esc(node.state.replace("_", " "))}</div></div>
      <div class="stat"><div class="label">Uncertainty</div><div class="value">${pct(node.uncertainty)}</div>
        <div class="sub">${node.uncertainty > 0.3 ? "we need more evidence" : "reasonably confident"}</div></div>
      <div class="stat"><div class="label">Observations</div><div class="value">${node.evidence_count}</div>
        <div class="sub">released or faculty-confirmed only</div></div>
    </div>
    ${action ? `<div class="callout"><strong>Recommended:</strong> ${esc(action.recommended_action)}<br>
      <span class="small dim">${esc(action.why_flagged)}</span></div>` : ""}
    ${
      action?.evidence_refs?.length
        ? `<div class="card"><header><h2>Evidence behind this estimate</h2></header>
            <div class="table-scroll"><table><thead><tr><th>Rubric item</th><th class="num">Score</th>
              <th class="num">Confidence</th><th>Source</th><th>When</th></tr></thead><tbody>
              ${action.evidence_refs
                .map(
                  (r) => `<tr><td class="mono">${esc(r.item_key)}</td><td class="num">${pct(r.score)}</td>
                    <td class="num">${num(r.confidence)}</td><td><span class="chip mute">${esc(r.source)}</span></td>
                    <td class="small dim">${when(r.at)}</td></tr>`,
                )
                .join("")}
            </tbody></table></div></div>`
        : ""
    }
    ${
      (node.misconceptions || []).length
        ? `<div class="card" style="margin-top:14px"><header><h2>Common errors on this concept</h2></header>
            <div class="chips">${node.misconceptions.map((m) => `<span class="chip warn">${esc(m.replace(/_/g, " "))}</span>`).join(" ")}</div></div>`
        : ""
    }`);
}

async function showRun(runId) {
  openDrawer('<div class="loading">Loading evidence…</div>');
  const detail = await api.get(`/api/student/${state.studentId}/courses/${state.courseId}/runs/${runId}`);
  openDrawer(`
    ${verdictHeader(detail)}
    ${rubricBreakdown(detail, { onAppeal: true })}
    <div style="height:14px"></div>
    ${testTable(detail)}
    <div style="height:14px"></div>
    ${stageTrail(detail)}
    <div style="height:14px"></div>
    ${sourceView(detail)}`);

  document.querySelectorAll("[data-appeal]").forEach((button) =>
    button.addEventListener("click", async () => {
      const reason = prompt(
        "Appeal this rubric item. Say what you think the evidence got wrong — it goes to your instructor with the full trail.",
      );
      if (!reason) return;
      button.disabled = true;
      await api.post(`/api/runs/${runId}/appeal`, {
        student_id: state.studentId,
        item_key: button.dataset.appeal,
        reason,
      });
      button.textContent = "Appeal submitted";
    }),
  );
}
