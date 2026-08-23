import { api, esc, meter, pct } from "../util.js";
import { setCrumbs } from "../app.js";

export async function render(root, ctx) {
  setCrumbs([{ label: "Outcomes" }]);
  const data = await api.get(`/api/admin/courses/${ctx.courseId}/attainment`);
  const s = data.summary;

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1>Course outcomes</h1>
        <div class="sub">${esc(data.course)} · ${esc(data.term)} · calculated from submitted work.</div>
      </div>
      <div class="actions"><button class="btn" id="export">Export CSV</button></div>
    </div>

    <div class="tiles">
      <div class="tile ${s.cos_attained === s.cos_total ? "good" : "warn"}">
        <div class="k">Outcomes met</div><div class="v">${s.cos_attained}/${s.cos_total}</div></div>
      <div class="tile"><div class="k">Average attainment</div><div class="v">${pct(s.mean_attainment)}</div></div>
      <div class="tile"><div class="k">Weakest</div>
        <div class="v" style="font-size:20px">${esc(s.weakest_co || "—")}</div></div>
      <div class="tile"><div class="k">Pass mark</div><div class="v">${pct(data.attainment_threshold)}</div></div>
    </div>

    <div class="card pad0">
      <header><h2>Course outcomes</h2></header>
      <div class="scroll-x"><table>
        <thead><tr>
          <th>CO</th><th>Outcome</th><th class="num">Students meeting it</th>
          <th class="num">Attainment</th><th>Level</th>
        </tr></thead>
        <tbody>${data.course_outcomes.map(coRow).join("")}</tbody>
      </table></div>
    </div>

    <div class="card pad0" style="margin-top:14px">
      <header><h2>Programme outcomes</h2></header>
      <div class="scroll-x"><table>
        <thead><tr><th>PO</th><th class="num">Attainment</th><th>Level</th><th>From</th></tr></thead>
        <tbody>${data.programme_outcomes.map(poRow).join("")}</tbody>
      </table></div>
    </div>`;

  root.querySelector("#export").addEventListener("click", () => exportCsv(data));
}

function level(value) {
  return `<span class="chip ${value >= 3 ? "good" : value === 2 ? "warn" : "bad"}">L${value}</span>`;
}

function coRow(co) {
  return `<tr>
    <td class="t-main">${esc(co.code)}</td>
    <td class="small">${esc(co.text)}</td>
    <td class="num">${co.students_attaining}/${co.cohort_size}</td>
    <td class="num" style="width:150px">${meter(co.attainment_fraction)}</td>
    <td>${level(co.level)}</td>
  </tr>`;
}

function poRow(po) {
  return `<tr>
    <td class="t-main">${esc(po.code)}</td>
    <td class="num" style="width:150px">${meter(po.weighted_attainment)}</td>
    <td>${level(po.level)}</td>
    <td class="small faint">${po.contributing_cos.map((c) => esc(c.co)).join(", ")}</td>
  </tr>`;
}

function exportCsv(data) {
  const rows = [["Code", "Outcome", "Mean mastery", "Attaining", "Cohort", "Attainment", "Level"]];
  data.course_outcomes.forEach((co) =>
    rows.push([
      co.code, co.text, co.mean_mastery, co.students_attaining,
      co.cohort_size, co.attainment_fraction, co.level,
    ]),
  );
  const quote = (value) => `"${String(value).split('"').join('""')}"`;
  const csv = rows.map((row) => row.map(quote).join(",")).join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `${data.course}-attainment.csv`;
  link.click();
  URL.revokeObjectURL(url);
}
