import { api, esc, num } from "../util.js";
import { setCrumbs } from "../app.js";

export async function render(root, ctx) {
  setCrumbs([{ label: "Students at risk" }]);
  const data = await api.get(`/api/admin/courses/${ctx.courseId}/risk`);

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1>Students at risk</h1>
        <div class="sub">${data.flagged} of ${data.cohort_size} flagged, each with its reason.</div>
      </div>
    </div>

    <div class="note" style="margin-bottom:14px">
      This list refers students to support. It is not a basis for any penalty.
    </div>

    <div class="grid g-main">
      <div class="card pad0">
        <div class="scroll-x"><table>
          <thead><tr>
            <th>Student</th><th class="num">Risk</th><th>Refer to</th><th>Main reason</th>
          </tr></thead>
          <tbody>${data.students.map(row).join("")}</tbody>
        </table></div>
      </div>
      <div id="detail">
        <div class="card"><div class="empty">Pick a student to see why they're flagged.</div></div>
      </div>
    </div>`;

  root.querySelectorAll("tr.link").forEach((tr) =>
    tr.addEventListener("click", () => {
      const student = data.students.find((s) => s.student_id === tr.dataset.id);
      root.querySelector("#detail").innerHTML = detail(student);
    }),
  );
}

function row(student) {
  const top = (student.contributing_factors || [])[0];
  return `<tr class="link" data-id="${esc(student.student_id)}">
    <td class="t-main">${esc(student.student_name)}</td>
    <td class="num" style="color:${student.flagged ? "var(--bad)" : "inherit"}">${num(student.risk_score)}</td>
    <td>${
      student.flagged
        ? `<span class="chip warn">${esc(student.routed_to.replace(/_/g, " "))}</span>`
        : `<span class="faint">—</span>`
    }</td>
    <td class="small dim">${esc(top ? top.factor : "nothing above threshold")}</td>
  </tr>`;
}

function detail(student) {
  if (!student) return "";
  const factors = student.contributing_factors || [];
  return `<div class="card">
    <header>
      <h2>${esc(student.student_name)}</h2>
      <span class="faint small">risk ${num(student.risk_score)}</span>
    </header>
    ${
      factors
        .map(
          (factor) => `<div style="padding:9px 0;border-bottom:1px solid var(--border)">
            <strong class="small">${esc(factor.factor)}</strong>
            <div class="small dim" style="margin-top:2px">${esc(factor.detail)}</div>
          </div>`,
        )
        .join("") || `<div class="empty">No factor above threshold.</div>`
    }
  </div>`;
}
