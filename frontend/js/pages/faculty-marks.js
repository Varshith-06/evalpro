// Marks for everyone, every assignment. The screen faculty ask for first.

import { api, esc, pct, scoreColour, when } from "../util.js";
import { setCrumbs } from "../app.js";

export async function render(root, ctx) {
  setCrumbs([{ label: "Marks" }]);
  const data = await api.get(`/api/faculty/courses/${ctx.courseId}/gradebook`);

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1>Marks</h1>
        <div class="sub">${data.students.length} students · ${data.assignments.length} assignments</div>
      </div>
      <div class="actions"><button class="btn" id="export">Export CSV</button></div>
    </div>

    <div class="card pad0">
      <div class="scroll-x"><table>
        <thead><tr>
          <th>Student</th>
          ${data.assignments
            .map(
              (a) => `<th class="num nowrap">${esc(a.code)}
                <div class="t-sub" style="font-weight:400">${esc(when(a.due_at))}</div></th>`,
            )
            .join("")}
          <th class="num">Overall</th>
        </tr></thead>
        <tbody>${data.students.map((s) => row(s, data.assignments)).join("")}</tbody>
      </table></div>
    </div>`;

  root.querySelectorAll("td.cell[data-run]").forEach((td) =>
    td.addEventListener("click", () => {
      window.location.hash = `#/f/submission/${td.dataset.run}`;
    }),
  );

  root.querySelector("#export").addEventListener("click", () => exportCsv(data));
}

function row(student, assignments) {
  const cells = student.marks
    .map((mark, index) => {
      if (!mark) return `<td class="num faint">—</td>`;
      const late = mark.state === "escalated";
      return `<td class="num cell" data-run="${esc(mark.run_id)}"
        style="cursor:pointer;color:${scoreColour(mark.score)};font-weight:560"
        title="${esc(assignments[index].title)} — ${mark.points} of ${mark.max_points} marks">
        ${pct(mark.score)}${late ? ' <span class="chip warn">review</span>' : ""}
      </td>`;
    })
    .join("");
  return `<tr>
    <td><div class="t-main">${esc(student.student_name)}</div>
      <div class="t-sub">${esc(student.external_id || "")}</div></td>
    ${cells}
    <td class="num" style="font-weight:600">${student.overall === null ? "—" : pct(student.overall)}</td>
  </tr>`;
}

function exportCsv(data) {
  const header = ["Student", "Roll number", ...data.assignments.map((a) => a.code), "Overall %"];
  const rows = [header];
  data.students.forEach((student) => {
    rows.push([
      student.student_name,
      student.external_id || "",
      ...student.marks.map((m) => (m ? m.points : "")),
      student.overall === null ? "" : (student.overall * 100).toFixed(1),
    ]);
  });
  const quote = (value) => `"${String(value).split('"').join('""')}"`;
  const csv = rows.map((r) => r.map(quote).join(",")).join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "marks.csv";
  link.click();
  URL.revokeObjectURL(url);
}
