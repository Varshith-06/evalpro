import { api, esc, toast } from "../util.js";
import { setCrumbs } from "../app.js";

export async function render(root, ctx) {
  setCrumbs([{ label: "Common mistakes" }]);
  const health = await api.get(`/api/faculty/courses/${ctx.courseId}/health`);
  const clusters = health.misconceptions;

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1>Common mistakes</h1>
        <div class="sub">Groups of students who got something wrong the same way.</div>
      </div>
    </div>
    <div class="grid g2" id="list"></div>`;

  const list = root.querySelector("#list");
  if (!clusters.length) {
    list.innerHTML = `<div class="empty">No shared pattern yet — needs a few more submissions.</div>`;
    return;
  }
  list.innerHTML = clusters
    .map(
      (c) => `<div class="card" data-id="${esc(c.id)}" style="cursor:pointer">
        <header>
          <h2>${esc(c.label || "Unnamed pattern")}</h2>
          <span class="chip ${c.size >= 5 ? "bad" : "warn"}">${c.size} students</span>
        </header>
        <div class="small dim">${esc(c.auto_signature)}</div>
        <div class="chips" style="margin-top:8px">
          ${(c.concepts || []).map((k) => `<span class="chip info">${esc(k)}</span>`).join("")}
        </div>
      </div>`,
    )
    .join("");

  list.querySelectorAll("[data-id]").forEach((card) =>
    card.addEventListener("click", () => open(root, ctx, card.dataset.id)),
  );
}

async function open(root, ctx, clusterId) {
  const b = await api.get(`/api/faculty/clusters/${clusterId}/briefing`);
  setCrumbs([{ label: "Common mistakes", path: "f/mistakes" }, { label: b.label || "Pattern" }]);
  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1>${esc(b.label || "Unnamed pattern")}</h1>
        <div class="sub">${esc(b.auto_signature)}</div>
      </div>
      <div class="actions">
        <a class="btn" href="#/f/mistakes">Back</a>
        <button class="btn primary" id="name">${b.label ? "Rename" : "Name this"}</button>
      </div>
    </div>
    <div class="note" style="margin-bottom:14px">${esc(b.suggested_lecture_note)}</div>
    ${b.representatives
      .map(
        (rep) => `<div class="card" style="margin-bottom:12px">
          <header><h2>${rep.is_medoid ? "Most typical example" : "Another example"}</h2></header>
          ${rep.failed_tests
            .map((t) => `<div class="ev">${esc(t.test_key)} — ${esc(t.outcome)}</div>`)
            .join("")}
          ${Object.values(rep.files || {})
            .map((body) => `<pre class="code" style="margin-top:8px">${esc(body)}</pre>`)
            .join("")}
        </div>`,
      )
      .join("")}`;

  root.querySelector("#name").addEventListener("click", async () => {
    const label = prompt("Name this mistake so it's recognisable next semester:", b.label || "");
    if (!label) return;
    const faculty = ctx.role === "faculty" ? ctx.userId : ctx.staff.find((s) => s.role === "faculty")?.id;
    await api.post(`/api/faculty/clusters/${clusterId}/label`, { faculty_id: faculty, label });
    toast("Saved.");
    open(root, ctx, clusterId);
  });
}
