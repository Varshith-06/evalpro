import { api, esc, pct } from "../util.js";
import { setCrumbs } from "../app.js";

export async function render(root, ctx) {
  setCrumbs([{ label: "Integrity" }]);
  const data = await api.get(`/api/admin/courses/${ctx.courseId}/integrity`);

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1>Integrity</h1>
        <div class="sub">Pairs of submissions that stand out against the rest of the class.</div>
      </div>
    </div>

    <div class="note" style="margin-bottom:14px">
      These are overlaps, not accusations. Nothing here is a finding until a person makes one.
    </div>

    <div class="card pad0">
      <div class="scroll-x"><table>
        <thead><tr><th>Assignment</th><th>Pair</th><th class="num">Overlap</th><th></th></tr></thead>
        <tbody>${
          data.pairs.map(row).join("") ||
          `<tr><td colspan="4" class="empty">Nothing stands out.</td></tr>`
        }</tbody>
      </table></div>
    </div>
    <div id="compare" style="margin-top:14px"></div>`;

  root.querySelectorAll("tr.link").forEach((tr) =>
    tr.addEventListener("click", () => {
      root.querySelector("#compare").innerHTML = compare(data.pairs[Number(tr.dataset.i)]);
    }),
  );
}

function row(pair, index) {
  return `<tr class="link" data-i="${index}">
    <td>${esc(pair.assignment)}</td>
    <td class="t-main">${esc(pair.student_a)} &amp; ${esc(pair.student_b)}</td>
    <td class="num">${pct(pair.combined)}</td>
    <td><button class="btn sm">Compare</button></td>
  </tr>`;
}

function compare(pair) {
  if (!pair) return "";
  const regions = pair.aligned_regions || [];
  return `<div class="card">
    <header>
      <h2>${esc(pair.student_a)} &amp; ${esc(pair.student_b)}</h2>
      <span class="faint small">${pct(pair.combined)} overlap · ${esc(pair.assignment)}</span>
    </header>
    ${
      regions
        .map(
          (region) => `<div class="grid g2" style="margin-bottom:10px">
            <pre class="code">${esc(region.a.excerpt)}</pre>
            <pre class="code">${esc(region.b.excerpt)}</pre>
          </div>`,
        )
        .join("") || `<div class="empty">No aligned region was recorded for this pair.</div>`
    }
  </div>`;
}
