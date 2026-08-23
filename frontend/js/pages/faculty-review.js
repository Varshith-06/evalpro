import { api, esc, meter, reason, when } from "../util.js";
import { setCrumbs } from "../app.js";

export async function render(root, ctx) {
  setCrumbs([{ label: "To review" }]);
  const queue = await api.get(`/api/faculty/courses/${ctx.courseId}/queue`);
  const nameOf = Object.fromEntries(ctx.students.map((s) => [s.id, s.name]));

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1>To review</h1>
        <div class="sub">${queue.length} submission${queue.length === 1 ? "" : "s"} the marking
          wasn't confident enough to release. Most useful first.</div>
      </div>
    </div>

    <div class="card pad0">
      <div class="scroll-x"><table>
        <thead><tr>
          <th>Student</th><th>Assignment</th><th>Why it's here</th>
          <th class="num">Proposed</th><th></th>
        </tr></thead>
        <tbody>${
          queue
            .map(
              (q) => `<tr class="link" data-run="${esc(q.run_id)}">
                <td>
                  <div class="t-main">${esc(nameOf[q.student_id] || q.student_id)}</div>
                  <div class="t-sub">attempt ${q.attempt_no} · ${esc(when(q.submitted_at))}</div>
                </td>
                <td>${esc(q.assignment)}</td>
                <td>
                  <div class="chips">${(q.reasons || [])
                    .map((r) => `<span class="chip warn">${esc(reason(r))}</span>`)
                    .join("")}</div>
                  <div class="t-sub">${esc(q.why_this_first)}</div>
                </td>
                <td class="num" style="width:140px">${meter(q.score)}</td>
                <td><a class="btn sm primary" href="#/f/submission/${esc(q.run_id)}">Review</a></td>
              </tr>`,
            )
            .join("") || `<tr><td colspan="5" class="empty">Nothing waiting. Everything released automatically.</td></tr>`
        }</tbody>
      </table></div>
    </div>`;

  root.querySelectorAll("tr.link").forEach((tr) =>
    tr.addEventListener("click", (event) => {
      if (event.target.closest("a")) return;
      window.location.hash = `#/f/submission/${tr.dataset.run}`;
    }),
  );
}
