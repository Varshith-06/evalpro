// Faculty view. One question: what should I teach next?
// Course health is the landing surface; the review queue is a drill-in, sorted
// by the expected value of attention rather than by arrival time.

import { api, esc, humanReason, num, openDrawer, pct, stateChip, when } from "../util.js";
import { heatmap, histogram } from "../charts.js";
import { rubricBreakdown, sourceView, stageTrail, testTable, verdictHeader } from "../evidence.js";

let state = { courseId: null, facultyId: null, health: null };

export async function render(container, { courseId, facultyId }) {
  state.courseId = courseId;
  state.facultyId = facultyId;
  container.innerHTML = '<div class="loading">Computing course health…</div>';

  const [health, queue, appeals] = await Promise.all([
    api.get(`/api/faculty/courses/${courseId}/health`),
    api.get(`/api/faculty/courses/${courseId}/queue`),
    api.get(`/api/faculty/courses/${courseId}/appeals`),
  ]);
  state.health = health;

  const distribution = health.cohort_distribution;

  container.innerHTML = `
    <div class="headline">
      <h1>${esc(health.question)}</h1>
      <p>Cohort mastery by concept, ranked re-teach signals, and the rubric items that are not measuring anything.</p>
    </div>

    <div class="stats">
      <div class="stat ${health.reteach_signals.length ? "warn" : "good"}">
        <div class="label">Re-teach signals</div><div class="value">${health.reteach_signals.length}</div>
        <div class="sub">concepts below cohort threshold</div></div>
      <div class="stat ${health.broken_items.length ? "bad" : "good"}">
        <div class="label">Broken rubric items</div><div class="value">${health.broken_items.length}</div>
        <div class="sub">measuring the wrong thing</div></div>
      <div class="stat"><div class="label">Review queue</div><div class="value">${health.queue_depth}</div>
        <div class="sub">escalations awaiting a human</div></div>
      <div class="stat"><div class="label">Misconception clusters</div><div class="value">${health.misconceptions.length}</div>
        <div class="sub">shared failure shapes</div></div>
      <div class="stat"><div class="label">Cohort shape</div><div class="value" style="font-size:15px">${esc(distribution.shape)}</div>
        <div class="sub">mean ${pct(distribution.mean)}, sd ${num(distribution.std)}</div></div>
    </div>

    <div class="grid two">
      <div class="card">
        <header><h2>Re-teach next</h2><span class="hint">ranked by downstream prerequisite impact, not by score</span></header>
        <div id="reteach"></div>
      </div>
      <div class="card">
        <header><h2>Score distribution</h2><span class="hint">shape, not just the mean</span></header>
        <div id="dist"></div>
        <div class="note">${esc(distribution.interpretation || "")}</div>
      </div>
    </div>

    <div class="card" style="margin-top:16px">
      <header><h2>Cohort mastery heatmap</h2>
        <span class="hint">students down, concepts across · blank means no evidence, not zero</span></header>
      <div id="heat"></div>
    </div>

    <div class="grid two" style="margin-top:16px">
      <div class="card">
        <header><h2>Review queue</h2><span class="hint">sorted by where a human minute is worth the most</span></header>
        <div class="table-scroll"><table>
          <thead><tr><th>Lab</th><th>Why this first</th><th class="num">Score</th><th class="num">Conf</th><th class="num">Priority</th></tr></thead>
          <tbody id="queue-rows"></tbody></table></div>
      </div>
      <div class="card">
        <header><h2>Assessment quality</h2><span class="hint">this grades the assessment, not the student</span></header>
        <div id="broken"></div>
      </div>
    </div>

    <div class="grid two" style="margin-top:16px">
      <div class="card">
        <header><h2>Misconception briefings</h2><span class="hint">named once, reused every semester</span></header>
        <div id="clusters"></div>
      </div>
      <div class="card">
        <header><h2>Students to talk to</h2><span class="hint">who, why, and what to raise</span></header>
        <div class="table-scroll"><table>
          <thead><tr><th>Student</th><th>Raise with them</th><th class="num">Mean mastery</th></tr></thead>
          <tbody id="interventions"></tbody></table></div>
        ${
          appeals.length
            ? `<div class="note">${appeals.length} open appeal(s): ${appeals
                .filter((a) => a.state === "open")
                .map((a) => esc(a.student))
                .join(", ")}</div>`
            : ""
        }
      </div>
    </div>

    ${
      health.pacing.length
        ? `<div class="card" style="margin-top:16px">
            <header><h2>Pacing</h2><span class="hint">the syllabus has moved on; the cohort has not</span></header>
            ${health.pacing
              .map(
                (p) => `<div class="evidence-line">${esc(p.note)}</div>`,
              )
              .join("")}</div>`
        : ""
    }`;

  container.querySelector("#reteach").innerHTML = health.reteach_signals.length
    ? health.reteach_signals
        .map(
          (signal) => `<div class="action remediate">
            <div class="action-head">
              <span class="action-title">${esc(signal.concept_name)}</span>
              <span class="chip ${signal.downstream_dependents > 3 ? "bad" : "warn"}">${signal.downstream_dependents} dependent concept(s)</span>
            </div>
            <div class="action-why">${esc(signal.rationale)}</div>
            <div class="action-meta">
              <span>cohort mastery ${pct(signal.cohort_mastery)}</span>
              <span>${signal.students_below} student(s) below threshold</span>
              <span title="The rest is inferred from failures on concepts that depend on this one.">
                ${pct(signal.direct_evidence_share)} directly assessed</span>
              ${signal.syllabus_week ? `<span>taught week ${signal.syllabus_week}</span>` : ""}
            </div>
          </div>`,
        )
        .join("")
    : '<div class="empty">No concept is below the cohort re-teach threshold.</div>';

  container.querySelector("#dist").appendChild(histogram(distribution));

  container
    .querySelector("#heat")
    .appendChild(heatmap(health.heatmap.concepts, health.heatmap.rows));

  const queueRows = container.querySelector("#queue-rows");
  queueRows.innerHTML = queue.length
    ? queue
        .map(
          (entry) => `<tr class="clickable" data-run="${esc(entry.run_id)}">
            <td><strong>${esc(entry.assignment.slice(0, 26))}</strong>
              <div class="chips" style="margin-top:3px">${(entry.reasons || [])
                .map((r) => `<span class="chip warn">${esc(humanReason(r))}</span>`)
                .join(" ")}</div></td>
            <td class="small dim">${esc(entry.why_this_first)}</td>
            <td class="num">${pct(entry.score)}</td>
            <td class="num">${num(entry.confidence)}</td>
            <td class="num">${num(entry.priority)}</td>
          </tr>`,
        )
        .join("")
    : '<tr><td colspan="5" class="empty">Queue is empty.</td></tr>';
  queueRows.querySelectorAll("tr[data-run]").forEach((row) =>
    row.addEventListener("click", () => showReview(row.dataset.run)),
  );

  container.querySelector("#broken").innerHTML = health.broken_items.length
    ? `<div class="table-scroll"><table>
        <thead><tr><th>Item</th><th class="num">Difficulty</th><th class="num">Discrim.</th><th class="num">Alignment</th><th>Flag</th></tr></thead>
        <tbody>${health.broken_items
          .map(
            (item) => `<tr>
              <td><span class="mono">${esc(item.item_key)}</span> <span class="faint small">${esc(item.assignment)}</span>
                <div class="small dim">${esc(item.item_text)}</div></td>
              <td class="num">${pct(item.difficulty)}</td>
              <td class="num" style="color:${item.discrimination < 0 ? "var(--bad)" : "inherit"}">${num(item.discrimination)}</td>
              <td class="num">${num(item.concept_alignment)}</td>
              <td><span class="chip ${item.flag === "anticorrelated" ? "bad" : "warn"}">${esc(item.flag.replace(/_/g, " "))}</span></td>
            </tr>`,
          )
          .join("")}</tbody></table></div>
      <div class="note">Negative discrimination means strong students are failing the item — almost always an ambiguous
        specification or an incorrect test, not a cohort that suddenly forgot the topic.</div>`
    : '<div class="empty">No item is flagged. Difficulty, discrimination, and concept alignment all look healthy.</div>';

  container.querySelector("#clusters").innerHTML = health.misconceptions.length
    ? health.misconceptions
        .map(
          (cluster) => `<div class="action" data-cluster="${esc(cluster.id)}" style="cursor:pointer">
            <div class="action-head">
              <span class="action-title">${esc(cluster.label || "Unnamed cluster")}</span>
              <span class="chip ${cluster.size >= 5 ? "bad" : "warn"}">${cluster.size} student(s)</span>
            </div>
            <div class="action-why">${esc(cluster.auto_signature)}</div>
            <div class="chips">${(cluster.concepts || []).map((c) => `<span class="chip info">${esc(c)}</span>`).join(" ")}</div>
          </div>`,
        )
        .join("")
    : '<div class="empty">No shared failure shape has enough members to be a cluster yet.</div>';
  container.querySelectorAll("[data-cluster]").forEach((node) =>
    node.addEventListener("click", () => showCluster(node.dataset.cluster)),
  );

  container.querySelector("#interventions").innerHTML = health.interventions.length
    ? health.interventions
        .map(
          (row) => `<tr>
            <td><strong>${esc(row.student_name)}</strong></td>
            <td><div>${esc(row.raise_with_them)}</div><div class="small dim">${esc(row.suggested_action)}</div></td>
            <td class="num">${pct(row.mean_mastery)}</td>
          </tr>`,
        )
        .join("")
    : '<tr><td colspan="3" class="empty">Nobody is flagged for a conversation.</td></tr>';
}

async function showReview(runId) {
  openDrawer('<div class="loading">Loading the evidence trail…</div>');
  const detail = await api.get(`/api/faculty/runs/${runId}/review`);

  const similarity = (detail.similarity || []).length
    ? `<div class="card" style="margin-top:14px">
        <header><h2>Similarity evidence</h2><span class="hint">evidence, never a verdict</span></header>
        ${detail.similarity
          .map(
            (pair) => `<div class="action">
              <div class="action-head"><span class="action-title">${pct(pair.combined)} combined similarity</span>
                <span class="chip mute">${esc(pair.corpus)}</span></div>
              <div class="action-meta"><span>token ${pct(pair.token_similarity)}</span>
                <span>structural ${pct(pair.structural_similarity)}</span></div>
              ${(pair.aligned_regions || [])
                .slice(0, 2)
                .map(
                  (region) => `<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px">
                    <pre class="code">${esc(region.a.excerpt)}</pre>
                    <pre class="code">${esc(region.b.excerpt)}</pre></div>`,
                )
                .join("")}
            </div>`,
          )
          .join("")}
        <div class="note">${esc(detail.similarity_disclaimer)}</div></div>`
    : "";

  openDrawer(`
    ${verdictHeader(detail, { role: "faculty" })}
    <div class="row-actions" style="margin-bottom:14px">
      <button class="btn primary" id="confirm-run">Confirm as-is</button>
      <span class="faint small" style="align-self:center">Confirming releases the grade and lets its evidence feed the mastery model.</span>
    </div>
    ${rubricBreakdown(detail, { onOverride: true })}
    <div style="height:14px"></div>
    ${testTable(detail)}
    <div style="height:14px"></div>
    ${stageTrail(detail)}
    ${similarity}
    <div style="height:14px"></div>
    ${sourceView(detail, { reference: detail.reference_solution })}`);

  document.getElementById("confirm-run").addEventListener("click", async (event) => {
    event.target.disabled = true;
    event.target.textContent = "Confirming…";
    await api.post(`/api/faculty/runs/${runId}/confirm`, { faculty_id: state.facultyId, note: "" });
    event.target.textContent = "Confirmed";
    document.dispatchEvent(new CustomEvent("evalpro:refresh"));
  });

  document.querySelectorAll("[data-override]").forEach((button) =>
    button.addEventListener("click", async () => {
      const raw = prompt("New score for this item as a fraction between 0 and 1:", "0.5");
      if (raw === null) return;
      const score = Number(raw);
      if (Number.isNaN(score) || score < 0 || score > 1) return alert("Enter a number between 0 and 1.");
      const reason = prompt(
        "Reason for the override. This is mandatory: it is the training signal for the confidence model, and the student sees it on appeal.",
      );
      if (!reason || reason.trim().length < 8) return alert("A reason of at least a few words is required.");
      button.disabled = true;
      await api.post(`/api/faculty/runs/${runId}/override`, {
        faculty_id: state.facultyId,
        item_key: button.dataset.override,
        score_fraction: score,
        reason,
      });
      button.textContent = "Overridden";
      document.dispatchEvent(new CustomEvent("evalpro:refresh"));
    }),
  );
}

async function showCluster(clusterId) {
  openDrawer('<div class="loading">Loading briefing…</div>');
  const briefing = await api.get(`/api/faculty/clusters/${clusterId}/briefing`);
  openDrawer(`
    <div class="headline">
      <h1>${esc(briefing.label || "Unnamed misconception")}</h1>
      <p>${esc(briefing.auto_signature)}</p>
    </div>
    <div class="callout"><strong>For the next lecture:</strong> ${esc(briefing.suggested_lecture_note)}</div>
    <div class="chips" style="margin-bottom:14px">
      ${(briefing.concepts || []).map((c) => `<span class="chip info">${esc(c)}</span>`).join(" ")}
      <span class="chip warn">${briefing.size} student(s)</span>
    </div>
    <div class="row-actions" style="margin-bottom:16px">
      <button class="btn" id="name-cluster">${briefing.label ? "Rename" : "Name this misconception"}</button>
      <span class="faint small" style="align-self:center">A name persists across semesters and becomes part of the course's misconception library.</span>
    </div>
    ${briefing.representatives
      .map(
        (rep) => `<div class="card" style="margin-bottom:12px">
          <header><h2>${rep.is_medoid ? "Representative submission" : "Another member"}</h2>
            <span class="hint mono">${esc(rep.run_id)}</span></header>
          ${rep.failed_tests
            .map(
              (t) => `<div class="evidence-line">${esc(t.test_key)} — ${esc(t.outcome)}${t.reason ? `: ${esc(String(t.reason).slice(0, 120))}` : ""}</div>`,
            )
            .join("")}
          ${Object.entries(rep.files || {})
            .map(([path, body]) => `<pre class="code" style="margin-top:8px">${esc(body)}</pre>`)
            .join("")}
        </div>`,
      )
      .join("")}`);

  document.getElementById("name-cluster").addEventListener("click", async () => {
    const label = prompt("Name this misconception:", briefing.label || "");
    if (!label) return;
    await api.post(`/api/faculty/clusters/${clusterId}/label`, { faculty_id: state.facultyId, label });
    document.dispatchEvent(new CustomEvent("evalpro:refresh"));
  });
}
