// Feedback on one attempt. Per-item, with the specific reason for each mark.

import { api, chip, esc, meter, num, pct, toast, when } from "../util.js";
import { setCrumbs } from "../app.js";

export async function render(root, ctx, runId) {
  const detail = await api.get(`/api/student/${ctx.userId}/courses/${ctx.courseId}/runs/${runId}`);
  setCrumbs([
    { label: "My labs", path: "s/labs" },
    { label: detail.assignment.code, path: `s/lab/${detail.assignment.id}` },
    { label: "Feedback" },
  ]);

  const verdict = detail.verdict;
  const stillWithInstructor = verdict.state === "escalated";
  const failing = detail.tests.filter((t) => t.outcome !== "pass" && t.outcome !== "skipped");
  const syntaxNote = verdict.syntax_penalty > 0;

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1>${esc(detail.assignment.title)}</h1>
        <div class="sub">Attempt ${detail.attempt_no} · submitted ${when(detail.submitted_at)}</div>
      </div>
      <div class="actions">
        <a class="btn" href="#/s/lab/${esc(detail.assignment.id)}">Try again</a>
      </div>
    </div>

    <div class="tiles">
      <div class="tile"><div class="k">Score</div>
        <div class="v">${pct(verdict.total_fraction)}</div>
        <div class="n">${num(verdict.total_points, 1)} of ${num(verdict.max_points, 1)} marks</div></div>
      <div class="tile"><div class="k">Status</div>
        <div class="v" style="font-size:16px;padding-top:6px">${chip(verdict.state)}</div></div>
      ${
        syntaxNote
          ? `<div class="tile warn"><div class="k">Syntax fix</div>
               <div class="v">−${pct(verdict.syntax_penalty)}</div>
               <div class="n">we fixed it and ran your code anyway</div></div>`
          : ""
      }
    </div>

    ${
      stillWithInstructor
        ? `<div class="note warn" style="margin-bottom:14px">
             This one is with your instructor. The automatic marking wasn't confident enough to
             release it on its own${
               verdict.escalation_reasons.length
                 ? `: ${verdict.escalation_reasons.map((r) => esc(reasonForStudent(r))).join(", ")}`
                 : ""
             }.</div>`
        : ""
    }
    ${
      syntaxNote
        ? `<div class="note" style="margin-bottom:14px">
             Your code didn't compile as submitted. We found the smallest fix that made it run,
             marked it on the fixed version, and took off ${pct(verdict.syntax_penalty)} rather than
             giving you zero.</div>`
        : ""
    }
    ${
      verdict.override_reason
        ? `<div class="note good" style="margin-bottom:14px">
             <strong>Your instructor adjusted this:</strong> ${esc(verdict.override_reason)}</div>`
        : ""
    }

    <div class="grid g-main">
      <div class="stack">
        <div class="card">
          <header><h2>Mark breakdown</h2></header>
          ${detail.items.map(item).join("")}
        </div>
      </div>

      <div class="stack">
        ${failing.length ? failedTests(failing) : ""}
        ${detail.hidden_test_note ? `<div class="note">${esc(detail.hidden_test_note)}</div>` : ""}
        <div class="card">
          <header><h2>What you submitted</h2></header>
          ${Object.entries(detail.files)
            .map(([path, body]) => `<div class="faint small">${esc(path)}</div><pre class="code">${esc(body)}</pre>`)
            .join("")}
        </div>
      </div>
    </div>`;

  root.querySelectorAll("[data-appeal]").forEach((button) =>
    button.addEventListener("click", async () => {
      const why = prompt("What do you think the marking got wrong? Your instructor will see this.");
      if (!why) return;
      button.disabled = true;
      try {
        await api.post(`/api/runs/${runId}/appeal`, {
          student_id: ctx.userId,
          item_key: button.dataset.appeal,
          reason: why,
        });
        button.textContent = "Sent";
        toast("Sent to your instructor.");
      } catch (error) {
        button.disabled = false;
        toast(error.message, "bad");
      }
    }),
  );
}

function reasonForStudent(key) {
  return (
    {
      signal_conflict: "the checks disagreed",
      low_confidence: "the result wasn't clear-cut",
      integrity_flag: "it looked similar to another submission",
      report_contradiction: "your report didn't match your code",
      grade_boundary: "it sits right on a grade boundary",
      repair_material: "we had to fix a syntax error to run it",
      stage_error: "something went wrong while marking",
      appeal: "you appealed it",
    }[key] || key
  );
}

function item(entry) {
  const score = entry.faculty_score_fraction ?? entry.score_fraction;
  const earned = (score * entry.weight).toFixed(1).replace(/\.0$/, "");
  return `<details class="item" ${score < 1 ? "open" : ""}>
    <summary>
      <span class="grow">${esc(entry.item_text)}</span>
      <span class="nowrap faint small">${earned} / ${entry.weight}</span>
      <span style="width:120px">${meter(score)}</span>
    </summary>
    <div class="item-body">
      ${entry.evidence.map((line) => `<div class="ev">${esc(line)}</div>`).join("")}
      ${
        entry.faculty_reason
          ? `<div class="note good" style="margin-top:8px">Instructor: ${esc(entry.faculty_reason)}</div>`
          : ""
      }
      <div class="actions" style="margin-top:10px">
        <button class="btn sm" data-appeal="${esc(entry.item_key)}">Disagree with this?</button>
      </div>
    </div>
  </details>`;
}

function failedTests(tests) {
  return `<div class="card pad0">
    <header><h2>Tests that didn't pass</h2></header>
    <div class="scroll-x"><table>
      <tbody>${tests
        .map(
          (test) => `<tr>
            <td>
              <div class="t-main">${esc(test.test_key)} ${chip(test.outcome)}</div>
              ${
                test.expected
                  ? `<div class="t-sub mono">expected ${esc(test.expected.slice(0, 70))} · got ${esc(
                      (test.actual || "nothing").slice(0, 70),
                    )}</div>`
                  : ""
              }
              ${test.stderr_excerpt ? `<div class="t-sub mono">${esc(test.stderr_excerpt.slice(0, 120))}</div>` : ""}
            </td>
          </tr>`,
        )
        .join("")}</tbody>
    </table></div>
  </div>`;
}
