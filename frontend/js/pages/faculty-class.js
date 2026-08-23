import { api, esc, pct } from "../util.js";
import { heatmap } from "../charts.js";
import { setCrumbs } from "../app.js";

export async function render(root, ctx) {
  setCrumbs([{ label: "Class progress" }]);
  const health = await api.get(`/api/faculty/courses/${ctx.courseId}/health`);

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1>Class progress</h1>
        <div class="sub">Where the class stands topic by topic.</div>
      </div>
    </div>

    <div class="grid g-main">
      <div class="card">
        <header><h2>Topics to go back over</h2>
          <span class="faint small">most blocking first</span></header>
        <div id="reteach"></div>
      </div>

      <div class="card pad0">
        <header><h2>Students to check in with</h2></header>
        <div class="scroll-x"><table>
          <thead><tr><th>Student</th><th>Talk about</th><th class="num">Overall</th></tr></thead>
          <tbody>${
            health.interventions
              .map(
                (row) => `<tr>
                  <td class="t-main">${esc(row.student_name)}</td>
                  <td>${esc(row.raise_with_them)}<div class="t-sub">${esc(row.suggested_action)}</div></td>
                  <td class="num">${pct(row.mean_mastery)}</td>
                </tr>`,
              )
              .join("") || `<tr><td colspan="3" class="empty">Nobody flagged.</td></tr>`
          }</tbody>
        </table></div>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <header><h2>Everyone, every topic</h2>
        <span class="faint small">blank = not covered by any submission yet</span></header>
      <div id="heat"></div>
    </div>

    ${
      health.broken_items.length
        ? `<div class="card pad0" style="margin-top:14px">
             <header><h2>Criteria that aren't working</h2></header>
             <div class="scroll-x"><table>
               <thead><tr><th>Criterion</th><th>Assignment</th><th>Problem</th></tr></thead>
               <tbody>${health.broken_items
                 .map(
                   (b) => `<tr>
                     <td>${esc(b.item_text || b.item_key)}</td>
                     <td>${esc(b.assignment)}</td>
                     <td>${explain(b)}</td>
                   </tr>`,
                 )
                 .join("")}</tbody>
             </table></div>
           </div>`
        : ""
    }`;

  const reteach = root.querySelector("#reteach");
  reteach.innerHTML = health.reteach_signals.length
    ? health.reteach_signals
        .slice(0, 6)
        .map(
          (s) => `<div style="padding:10px 0;border-bottom:1px solid var(--border)">
            <div style="display:flex;justify-content:space-between;gap:10px;align-items:baseline">
              <strong>${esc(s.concept_name)}</strong>
              <span class="chip ${s.downstream_dependents > 3 ? "bad" : "warn"}">
                blocks ${s.downstream_dependents} later topic${s.downstream_dependents === 1 ? "" : "s"}</span>
            </div>
            <div class="small dim" style="margin-top:4px">
              ${s.students_below} of ${s.cohort_size} students below the bar · class average ${pct(s.cohort_mastery)}
              ${s.direct_evidence_share < 0.5 ? " · mostly inferred, worth checking directly" : ""}
            </div>
          </div>`,
        )
        .join("")
    : `<div class="empty">Every topic is where you'd want it.</div>`;

  root.querySelector("#heat").appendChild(heatmap(health.heatmap.concepts, health.heatmap.rows));
}

function explain(item) {
  const map = {
    anticorrelated: ["bad", "Strong students are failing it — likely an unclear question"],
    misaligned_concepts: ["warn", "Tagged to the wrong topics"],
    non_discriminating: ["warn", "Everyone scores the same — tells you nothing"],
    too_hard: ["warn", "Almost nobody gets it"],
    too_easy: ["", "Everybody gets it"],
  };
  const [kind, text] = map[item.flag] || ["", item.flag];
  return `<span class="chip ${kind}">${esc(text)}</span>`;
}
