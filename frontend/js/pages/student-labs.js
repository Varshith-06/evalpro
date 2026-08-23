import { api, chip, esc, meter, relative, when } from "../util.js";
import { setCrumbs } from "../app.js";

export async function render(root, ctx) {
  setCrumbs([{ label: "My labs" }]);
  const data = await api.get(`/api/student/${ctx.userId}/courses/${ctx.courseId}`);
  const labs = data.assignments;

  const done = labs.filter((l) => l.released).length;
  const waiting = labs.filter((l) => l.latest && !l.released).length;
  const todo = labs.filter((l) => !l.latest).length;

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1>My labs</h1>
        <div class="sub">${esc(ctx.course?.title || "")}</div>
      </div>
    </div>

    <div class="tiles">
      <div class="tile good"><div class="k">Graded</div><div class="v">${done}</div></div>
      <div class="tile warn"><div class="k">With your instructor</div><div class="v">${waiting}</div></div>
      <div class="tile"><div class="k">Not submitted</div><div class="v">${todo}</div></div>
    </div>

    <div class="card pad0">
      <div class="scroll-x"><table>
        <thead><tr>
          <th>Lab</th><th>Due</th><th>Attempts</th><th>Status</th>
          <th class="num">Score</th><th></th>
        </tr></thead>
        <tbody>${labs.map(row).join("") || `<tr><td colspan="6" class="empty">No labs yet.</td></tr>`}</tbody>
      </table></div>
    </div>`;

  root.querySelectorAll("tr.link").forEach((tr) =>
    tr.addEventListener("click", (event) => {
      if (event.target.closest("a")) return;
      window.location.hash = `#/s/lab/${tr.dataset.id}`;
    }),
  );
}

function row(lab) {
  const latest = lab.latest;
  const delta = lab.attempt_deltas?.length ? lab.attempt_deltas.at(-1) : null;
  return `<tr class="link" data-id="${esc(lab.assignment_id)}">
    <td>
      <div class="t-main">${esc(lab.title)}</div>
      <div class="t-sub">${esc(lab.code)}</div>
    </td>
    <td class="nowrap">${when(lab.due_at)}<div class="t-sub">${esc(relative(lab.due_at))}</div></td>
    <td class="num">${lab.attempts || "—"}</td>
    <td>${latest ? chip(latest.state) : `<span class="chip">Not started</span>`}</td>
    <td class="num" style="width:150px">
      ${latest ? meter(latest.score) : "—"}
      ${delta ? `<div class="t-sub">${delta > 0 ? "+" : ""}${(delta * 100).toFixed(0)} pts vs last attempt</div>` : ""}
    </td>
    <td class="nowrap">
      <a class="btn sm ${latest ? "" : "primary"}" href="#/s/lab/${esc(lab.assignment_id)}">
        ${latest ? "Open" : "Start"}
      </a>
      ${
        latest && latest.state !== "escalated"
          ? `<a class="btn sm" href="#/s/feedback/${esc(latest.run_id)}">Feedback</a>`
          : ""
      }
    </td>
  </tr>`;
}
