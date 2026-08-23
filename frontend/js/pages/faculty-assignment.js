import { api, chip, esc, meter, toast, when } from "../util.js";
import { setCrumbs, refreshCounts } from "../app.js";

export async function render(root, ctx, assignmentId) {
  const [assignment, queue] = await Promise.all([
    api.get(`/api/assignments/${assignmentId}`),
    api.get(`/api/faculty/courses/${ctx.courseId}/queue`),
  ]);
  const version = assignment.active_version;
  setCrumbs([{ label: "Assignments", path: "f/assignments" }, { label: assignment.code }]);

  const mine = queue.filter((q) => q.assignment_id === assignmentId);
  const nameOf = Object.fromEntries(ctx.students.map((s) => [s.id, s.name]));
  const isStatic = version?.grading_mode === "static" || version?.tests?.length === 0;

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1>${esc(assignment.title)}</h1>
        <div class="sub">${esc(assignment.code)} · due ${when(assignment.due_at)}
          ${version?.approved ? "" : ' · <span class="chip warn">draft</span>'}</div>
      </div>
      <div class="actions">
        ${mine.length ? `<a class="btn primary" href="#/f/review">Review ${mine.length}</a>` : ""}
        <button class="btn" id="regrade">Re-mark everyone</button>
      </div>
    </div>

    <div class="grid g-main">
      <div class="stack">
        <div class="card pad0">
          <header><h2>Marking criteria</h2>
            <span class="faint small">${version ? version.rubric.reduce((s, i) => s + i.weight, 0) : 0} marks</span></header>
          <div class="scroll-x"><table>
            <thead><tr><th>Criterion</th><th>Checked by</th><th>Topics</th><th class="num">Marks</th></tr></thead>
            <tbody>${(version?.rubric || []).map(rubricRow).join("")}</tbody>
          </table></div>
        </div>

        ${
          mine.length
            ? `<div class="card pad0">
                 <header><h2>Waiting for you</h2></header>
                 <div class="scroll-x"><table>
                   <thead><tr><th>Student</th><th>Why</th><th class="num">Score</th><th></th></tr></thead>
                   <tbody>${mine
                     .map(
                       (q) => `<tr>
                         <td class="t-main">${esc(nameOf[q.student_id] || q.student_id)}</td>
                         <td class="small dim">${esc(q.why_this_first)}</td>
                         <td class="num" style="width:130px">${meter(q.score)}</td>
                         <td><a class="btn sm" href="#/f/submission/${esc(q.run_id)}">Review</a></td>
                       </tr>`,
                     )
                     .join("")}</tbody>
                 </table></div>
               </div>`
            : ""
        }
      </div>

      <div class="stack">
        <div class="card">
          <header><h2>Marking method</h2></header>
          <div class="note ${isStatic ? "warn" : "good"}">
            ${
              isStatic
                ? "Approach-marked. There's no model solution, so no tests run — marks come from what the code does and how it's built."
                : `${version.admitted_tests} test cases run against every submission.`
            }
          </div>
          ${
            version?.generated_parts?.length
              ? `<div class="note" style="margin-top:8px">The ${version.generated_parts.join(" and ")}
                   ${version.generated_parts.length > 1 ? "were" : "was"} written from your instructions.</div>`
              : ""
          }
        </div>

        ${
          version?.tests?.length
            ? `<div class="card pad0">
                 <header><h2>Test cases</h2></header>
                 <div class="scroll-x"><table>
                   <tbody>${version.tests
                     .map(
                       (t) => `<tr>
                         <td><span class="mono">${esc(t.test_key)}</span>
                           <span class="chip">${esc(t.category)}</span>
                           ${t.hidden ? '<span class="chip info">hidden</span>' : ""}</td>
                         <td>${t.admitted ? chip("ok", "ok") : chip("bad", "discarded")}</td>
                       </tr>`,
                     )
                     .join("")}</tbody>
                 </table></div>
               </div>`
            : ""
        }

        <div class="card">
          <header><h2>Instructions given</h2></header>
          <pre class="code" style="white-space:pre-wrap">${esc(version?.spec_text || "")}</pre>
        </div>
      </div>
    </div>`;

  root.querySelector("#regrade").addEventListener("click", async (event) => {
    if (!confirm("Re-mark every submission for this assignment?")) return;
    event.target.disabled = true;
    event.target.textContent = "Re-marking…";
    try {
      const body = await api.post(`/api/faculty/assignments/${assignmentId}/regrade`);
      toast(`Re-marked ${body.regraded} submissions.`);
      await refreshCounts();
      render(root, ctx, assignmentId);
    } catch (error) {
      toast(error.message, "bad");
      event.target.disabled = false;
      event.target.textContent = "Re-mark everyone";
    }
  });
}

function rubricRow(item) {
  return `<tr>
    <td>${esc(item.text)}</td>
    <td class="small">
      ${(item.checkable_by || []).map((c) => `<span class="chip">${esc(c)}</span>`).join(" ")}
      ${item.static_check ? `<div class="t-sub mono">${esc(item.static_check.kind)}</div>` : ""}
    </td>
    <td class="small">${(item.concept_ids || []).map((c) => `<span class="chip info">${esc(c)}</span>`).join(" ") || '<span class="faint">—</span>'}</td>
    <td class="num">${item.weight}</td>
  </tr>`;
}
