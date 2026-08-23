// Reviewing one submission: the student's code, what each criterion found, and
// the two things a lecturer does here — agree, or change a mark and say why.

import { api, chip, esc, meter, num, pct, reason, toast, when } from "../util.js";
import { setCrumbs } from "../app.js";

export async function render(root, ctx, runId) {
  const detail = await api.get(`/api/faculty/runs/${runId}/review`);
  const nameOf = Object.fromEntries(ctx.students.map((s) => [s.id, s.name]));
  const student = nameOf[detail.student_id] || detail.student_id;
  setCrumbs([
    { label: "To review", path: "f/review" },
    { label: `${detail.assignment.code} · ${student}` },
  ]);

  const verdict = detail.verdict;
  const failing = detail.tests.filter((t) => t.outcome !== "pass" && t.outcome !== "skipped");

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1>${esc(student)}</h1>
        <div class="sub">${esc(detail.assignment.title)} · attempt ${detail.attempt_no} ·
          ${when(detail.submitted_at)}</div>
      </div>
      <div class="actions">
        <button class="btn primary" id="approve">Approve ${pct(verdict.total_fraction)}</button>
        <a class="btn" href="#/f/review">Back to queue</a>
      </div>
    </div>

    <div class="chips" style="margin-bottom:14px">
      ${(verdict.escalation_reasons || []).map((r) => `<span class="chip warn">${esc(reason(r))}</span>`).join("")}
      ${verdict.syntax_penalty > 0 ? `<span class="chip warn">syntax fix −${pct(verdict.syntax_penalty)}</span>` : ""}
    </div>

    <div class="grid g-main">
      <div class="stack">
        <div class="card">
          <header><h2>Marking criteria</h2>
            <span class="faint small">${num(verdict.total_points, 1)} / ${num(verdict.max_points, 1)}</span></header>
          ${detail.items.map(item).join("")}
        </div>

        ${
          detail.similarity?.length
            ? `<div class="card">
                 <header><h2>Similar to another submission</h2></header>
                 ${detail.similarity
                   .map(
                     (p) => `<div style="margin-bottom:12px">
                       <div class="small"><strong>${pct(p.combined)}</strong> overlap with
                         ${esc(nameOf[p.other_student_id] || p.other_student_id)}</div>
                       ${(p.aligned_regions || [])
                         .slice(0, 1)
                         .map(
                           (r) => `<div class="grid g2" style="margin-top:6px">
                             <pre class="code">${esc(r.a.excerpt)}</pre>
                             <pre class="code">${esc(r.b.excerpt)}</pre>
                           </div>`,
                         )
                         .join("")}
                     </div>`,
                   )
                   .join("")}
                 <div class="note">Overlap only. Deciding whether this is misconduct is yours.</div>
               </div>`
            : ""
        }
      </div>

      <div class="stack">
        ${
          failing.length
            ? `<div class="card pad0">
                 <header><h2>Failing tests</h2></header>
                 <div class="scroll-x"><table><tbody>${failing
                   .map(
                     (t) => `<tr><td>
                       <div class="t-main">${esc(t.test_key)} ${chip(t.outcome)}</div>
                       ${t.diff ? `<pre class="code" style="margin-top:6px">${esc(t.diff)}</pre>` : ""}
                     </td></tr>`,
                   )
                   .join("")}</tbody></table></div>
               </div>`
            : ""
        }

        <div class="card">
          <header><h2>Their code</h2></header>
          ${Object.entries(detail.files)
            .map(([path, body]) => `<div class="faint small">${esc(path)}</div><pre class="code">${esc(body)}</pre>`)
            .join("")}
        </div>

        ${
          detail.report_text
            ? `<div class="card"><header><h2>Their report</h2></header>
                 <pre class="code" style="white-space:pre-wrap">${esc(detail.report_text)}</pre></div>`
            : ""
        }
        ${
          detail.reference_solution
            ? `<div class="card"><header><h2>Model solution</h2></header>
                 <pre class="code">${esc(detail.reference_solution)}</pre></div>`
            : ""
        }
      </div>
    </div>`;

  const faculty = ctx.role === "faculty" ? ctx.userId : ctx.staff.find((s) => s.role === "faculty")?.id;

  root.querySelector("#approve").addEventListener("click", async (event) => {
    event.target.disabled = true;
    event.target.textContent = "Approving…";
    try {
      await api.post(`/api/faculty/runs/${runId}/confirm`, { faculty_id: faculty, note: "" });
      toast("Released to the student.");
      window.location.hash = "#/f/review";
    } catch (error) {
      toast(error.message, "bad");
      event.target.disabled = false;
      event.target.textContent = "Approve";
    }
  });

  root.querySelectorAll("[data-change]").forEach((button) =>
    button.addEventListener("click", async () => {
      const key = button.dataset.change;
      const weight = Number(button.dataset.weight);
      const raw = prompt(`New mark for this criterion, out of ${weight}:`, button.dataset.current);
      if (raw === null) return;
      const marks = Number(raw);
      if (Number.isNaN(marks) || marks < 0 || marks > weight) {
        return toast(`Enter a number between 0 and ${weight}.`, "bad");
      }
      const why = prompt("Why? The student sees this, and it teaches the marking to do better.");
      if (!why || why.trim().length < 8) return toast("A short reason is required.", "bad");
      button.disabled = true;
      try {
        await api.post(`/api/faculty/runs/${runId}/override`, {
          faculty_id: faculty,
          item_key: key,
          score_fraction: marks / weight,
          reason: why,
        });
        toast("Updated.");
        render(root, ctx, runId);
      } catch (error) {
        button.disabled = false;
        toast(error.message, "bad");
      }
    }),
  );
}

function item(entry) {
  const score = entry.faculty_score_fraction ?? entry.score_fraction;
  const marks = (score * entry.weight).toFixed(1).replace(/\.0$/, "");
  const unsure = entry.confidence < 0.7;
  return `<details class="item" ${unsure ? "open" : ""}>
    <summary>
      <span class="grow">${esc(entry.item_text)}</span>
      ${unsure ? `<span class="chip warn">unsure</span>` : ""}
      <span class="nowrap faint small">${marks} / ${entry.weight}</span>
      <span style="width:110px">${meter(score)}</span>
    </summary>
    <div class="item-body">
      ${entry.evidence.map((line) => `<div class="ev">${esc(line)}</div>`).join("")}
      ${
        entry.faculty_reason
          ? `<div class="note good" style="margin-top:8px">You changed this: ${esc(entry.faculty_reason)}</div>`
          : ""
      }
      <div class="actions" style="margin-top:10px">
        <button class="btn sm" data-change="${esc(entry.item_key)}"
          data-weight="${entry.weight}" data-current="${marks}">Change this mark</button>
      </div>
    </div>
  </details>`;
}
