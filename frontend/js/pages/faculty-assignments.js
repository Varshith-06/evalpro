import { api, esc, meter, when } from "../util.js";
import { setCrumbs } from "../app.js";

export async function render(root, ctx) {
  setCrumbs([{ label: "Assignments" }]);
  const rows = await api.get(`/api/faculty/courses/${ctx.courseId}/assignments`);

  root.innerHTML = `
    <div class="page-head">
      <div><h1>Assignments</h1><div class="sub">${esc(ctx.course?.title || "")}</div></div>
      <div class="actions"><a class="btn primary" href="#/f/new">New assignment</a></div>
    </div>

    <div class="card pad0">
      <div class="scroll-x"><table>
        <thead><tr>
          <th>Assignment</th><th>Due</th><th>Marked by</th>
          <th class="num">Submitted</th><th class="num">To review</th>
          <th class="num">Class average</th><th></th>
        </tr></thead>
        <tbody>${rows.map(row).join("") || `<tr><td colspan="7" class="empty">
          No assignments yet. <a href="#/f/new">Create one</a>.</td></tr>`}</tbody>
      </table></div>
    </div>`;

  root.querySelectorAll("tr.link").forEach((tr) =>
    tr.addEventListener("click", (event) => {
      if (event.target.closest("a")) return;
      window.location.hash = `#/f/assignment/${tr.dataset.id}`;
    }),
  );
}

function row(a) {
  return `<tr class="link" data-id="${esc(a.id)}">
    <td>
      <div class="t-main">${esc(a.title)}</div>
      <div class="t-sub">${esc(a.code)}
        ${a.published ? "" : ' · <span class="chip warn">draft</span>'}
        ${a.generated_parts?.length ? ` · rubric written for you` : ""}</div>
    </td>
    <td class="nowrap">${when(a.due_at)}</td>
    <td>
      ${
        a.grading_mode === "static"
          ? `<span class="chip">approach</span>`
          : `<span class="chip accent">${a.tests} tests</span>`
      }
      <span class="chip">${a.rubric_items} criteria</span>
    </td>
    <td class="num">${a.submissions}</td>
    <td class="num">${a.needs_review ? `<span class="chip bad">${a.needs_review}</span>` : "—"}</td>
    <td class="num" style="width:150px">${a.average === null ? "—" : meter(a.average)}</td>
    <td><a class="btn sm" href="#/f/assignment/${esc(a.id)}">Open</a></td>
  </tr>`;
}
