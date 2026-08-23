// One assignment: who has submitted, how it is marked, and editing the rubric.

import { api, chip, esc, meter, toast, when } from "../util.js";
import { setCrumbs, refreshCounts } from "../app.js";

const CHECKS = [
  ["", "No automatic check"],
  ["function_defined", "A named function is defined"],
  ["class_defined", "A named class is defined"],
  ["recursion_present", "Uses recursion"],
  ["uses_iteration", "Uses a loop"],
  ["guard_present", "Guards the empty / boundary case"],
  ["api_called", "Uses a required function"],
  ["api_absent", "Avoids a forbidden function"],
  ["error_handling", "Handles errors"],
  ["loop_nesting", "Stays within a complexity class"],
  ["algorithm_class", "Implements a named approach"],
  ["min_functions", "Splits the work into functions"],
  ["no_global_state", "Avoids global state"],
  ["documented", "Is commented"],
];

export async function render(root, ctx, assignmentId) {
  const [assignment, submissions] = await Promise.all([
    api.get(`/api/assignments/${assignmentId}`),
    api.get(`/api/faculty/assignments/${assignmentId}/submissions`),
  ]);
  const version = assignment.active_version;
  setCrumbs([{ label: "Assignments", path: "f/assignments" }, { label: assignment.code }]);

  const draft = version && !version.approved;
  const s = submissions.summary;
  const isStatic = version?.grading_mode === "static";

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1>${esc(assignment.title)}</h1>
        <div class="sub">${esc(assignment.code)} · due ${when(assignment.due_at)}
          ${draft ? ' · <span class="chip warn">draft — students cannot submit</span>' : ""}</div>
      </div>
      <div class="actions">
        <button class="btn" id="publish">${draft ? "Publish" : "Unpublish"}</button>
        <button class="btn" id="regrade">Re-mark everyone</button>
        <button class="btn danger" id="delete">Delete</button>
      </div>
    </div>

    <div class="tiles">
      <div class="tile"><div class="k">Submitted</div>
        <div class="v">${s.submitted}<span class="faint" style="font-size:15px">/${s.roster}</span></div></div>
      <div class="tile ${s.not_submitted ? "warn" : ""}"><div class="k">Not submitted</div>
        <div class="v">${s.not_submitted}</div></div>
      <div class="tile ${s.needs_review ? "bad" : "good"}"><div class="k">Need review</div>
        <div class="v">${s.needs_review}</div></div>
      <div class="tile"><div class="k">Class average</div>
        <div class="v">${s.average === null ? "—" : `${Math.round(s.average * 100)}%`}</div></div>
      <div class="tile"><div class="k">Marked by</div>
        <div class="v" style="font-size:15px;padding-top:7px">
          ${isStatic ? "approach" : `${version?.admitted_tests || 0} tests`}</div></div>
    </div>

    <div class="grid g-main">
      <div class="stack">
        <div class="card pad0">
          <header><h2>Submissions</h2>
            <span class="faint small">click a row to open it</span></header>
          <div class="scroll-x"><table>
            <thead><tr><th>Student</th><th>Submitted</th><th>Status</th>
              <th class="num">Score</th><th></th></tr></thead>
            <tbody>${submissions.students.map(studentRow).join("")}</tbody>
          </table></div>
        </div>
      </div>

      <div class="stack">
        <div class="card pad0">
          <header><h2>Marking criteria</h2>
            <button class="btn sm" id="edit-rubric">Edit</button></header>
          <div class="scroll-x"><table>
            <thead><tr><th>Criterion</th><th>Check</th><th class="num">Marks</th></tr></thead>
            <tbody>${(version?.rubric || []).map(rubricRow).join("")}</tbody>
          </table></div>
        </div>

        ${
          version?.generated_parts?.length
            ? `<div class="note">The ${version.generated_parts.join(" and ")} came from your
                 instructions. Worth a read before the deadline.</div>`
            : ""
        }

        <div class="card">
          <header><h2>Instructions given</h2></header>
          <pre class="code" style="white-space:pre-wrap">${esc(version?.spec_text || "")}</pre>
        </div>
      </div>
    </div>`;

  root.querySelectorAll("tr.link").forEach((tr) =>
    tr.addEventListener("click", () => {
      window.location.hash = `#/f/submission/${tr.dataset.run}`;
    }),
  );

  const faculty = ctx.role === "faculty" ? ctx.userId : ctx.staff.find((x) => x.role === "faculty")?.id;

  root.querySelector("#publish").addEventListener("click", async (event) => {
    event.target.disabled = true;
    try {
      const body = await api.post(`/api/faculty/versions/${version.id}/publish`, {
        faculty_id: faculty,
        published: draft,
      });
      toast(body.published ? "Published." : "Pulled back to draft.");
      render(root, ctx, assignmentId);
    } catch (error) {
      toast(error.message, "bad");
      event.target.disabled = false;
    }
  });

  root.querySelector("#regrade").addEventListener("click", async (event) => {
    if (!confirm("Re-mark every submission for this assignment?")) return;
    event.target.disabled = true;
    event.target.textContent = "Re-marking…";
    try {
      const body = await api.post(`/api/faculty/assignments/${assignmentId}/regrade`);
      toast(`Re-marked ${body.regraded} submissions.`);
      await refreshCounts();
      render(root, ctx, assignmentId);
    } catch (error) {
      toast(error.message, "bad");
      event.target.disabled = false;
      event.target.textContent = "Re-mark everyone";
    }
  });

  root.querySelector("#edit-rubric").addEventListener("click", () =>
    editor(root, ctx, assignment, version, faculty),
  );

  root.querySelector("#delete").addEventListener("click", async (event) => {
    if (!confirm(`Delete "${assignment.title}"? This cannot be undone.`)) return;
    event.target.disabled = true;
    try {
      // Without ?confirm the server refuses once anything has been submitted,
      // and says how many marks would be thrown away. Ask again with that number.
      let response = await fetch(`/api/faculty/assignments/${assignmentId}`, { method: "DELETE" });
      if (response.status === 409) {
        const body = await response.json();
        if (!confirm(`${body.detail}

Delete anyway?`)) {
          event.target.disabled = false;
          return;
        }
        response = await fetch(`/api/faculty/assignments/${assignmentId}?confirm=true`, {
          method: "DELETE",
        });
      }
      if (!response.ok) throw new Error((await response.json()).detail || "Delete failed");
      toast("Deleted.");
      await refreshCounts();
      window.location.hash = "#/f/assignments";
    } catch (error) {
      toast(error.message, "bad");
      event.target.disabled = false;
    }
  });
}

function studentRow(row) {
  if (row.state === "not_submitted") {
    return `<tr>
      <td><div class="t-main">${esc(row.student_name)}</div>
        <div class="t-sub">${esc(row.external_id || "")}</div></td>
      <td colspan="4"><span class="chip">Not submitted</span></td>
    </tr>`;
  }
  return `<tr class="link" data-run="${esc(row.run_id)}">
    <td><div class="t-main">${esc(row.student_name)}</div>
      <div class="t-sub">${esc(row.external_id || "")} · attempt ${row.attempts}</div></td>
    <td class="nowrap">${when(row.submitted_at)}${row.late ? ' <span class="chip warn">late</span>' : ""}</td>
    <td>${chip(row.state)}${row.integrity_flag ? ' <span class="chip bad">similar</span>' : ""}</td>
    <td class="num" style="width:140px">${meter(row.score)}</td>
    <td><a class="btn sm" href="#/f/submission/${esc(row.run_id)}">Open</a></td>
  </tr>`;
}

function rubricRow(item) {
  const byTests = (item.checkable_by || []).includes("test");
  const check = CHECKS.find(([kind]) => kind === (item.static_check?.kind || ""));
  const label = item.static_check
    ? check?.[1] || item.static_check.kind
    : byTests
      ? "Test cases"
      : (item.checkable_by || []).includes("report")
        ? "The written report"
        : "How the code is structured";
  return `<tr>
    <td>${esc(item.text)}
      <div class="t-sub">${(item.concept_ids || []).join(", ") || "no topic tagged"}</div></td>
    <td class="small">${esc(label)}
      ${byTests && item.static_check ? '<div><span class="chip accent">and tests</span></div>' : ""}</td>
    <td class="num">${item.weight}</td>
  </tr>`;
}

// --------------------------------------------------------------------------
// Rubric editor
// --------------------------------------------------------------------------
function editor(root, ctx, assignment, version, faculty) {
  setCrumbs([
    { label: "Assignments", path: "f/assignments" },
    { label: assignment.code, path: `f/assignment/${assignment.id}` },
    { label: "Edit criteria" },
  ]);
  const rows = version.rubric.map((item) => ({ ...item }));

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1>Edit marking criteria</h1>
        <div class="sub">${esc(assignment.code)} · ${esc(assignment.title)}</div>
      </div>
      <div class="actions">
        <a class="btn" href="#/f/assignment/${esc(assignment.id)}">Cancel</a>
        <button class="btn primary" id="save">Save</button>
      </div>
    </div>

    ${
      version.approved
        ? `<div class="note" style="margin-bottom:14px">Saving creates a new version. Work that has
             already been marked keeps the criteria it was marked against, so past marks stay
             valid — tick "re-mark everyone" below to move them onto the new version.</div>`
        : ""
    }

    <div class="card pad0">
      <div class="scroll-x"><table>
        <thead><tr><th style="min-width:280px">Criterion</th><th style="min-width:200px">Automatic check</th>
          <th style="min-width:120px">Topics</th><th class="num">Marks</th><th></th></tr></thead>
        <tbody id="rows"></tbody>
      </table></div>
      <div style="padding:12px 14px;border-top:1px solid var(--border)">
        <button class="btn sm" id="add">Add a criterion</button>
        <span class="faint small" style="margin-left:10px" id="total"></span>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <div class="field" style="margin-bottom:10px">
        <label for="note">Why are you changing this? <span class="optional">(optional)</span></label>
        <input type="text" id="note" placeholder="e.g. the empty-list criterion was unclear" />
      </div>
      <label class="checkbox"><input type="checkbox" id="regrade" />
        Re-mark every submission against the new criteria</label>
    </div>`;

  const tbody = root.querySelector("#rows");

  function draw() {
    tbody.innerHTML = rows
      .map(
        (item, index) => `<tr>
          <td><textarea data-f="text" data-i="${index}" rows="2">${esc(item.text)}</textarea></td>
          <td>
            <select data-f="check" data-i="${index}">
              ${CHECKS.map(
                ([kind, label]) =>
                  `<option value="${esc(kind)}" ${
                    (item.static_check?.kind || "") === kind ? "selected" : ""
                  }>${esc(label)}</option>`,
              ).join("")}
            </select>
            <input type="text" data-f="target" data-i="${index}" style="margin-top:5px"
              placeholder="name, if the check needs one"
              value="${esc(item.static_check?.target || "")}" />
          </td>
          <td><input type="text" data-f="concepts" data-i="${index}"
            value="${esc((item.concept_ids || []).join(", "))}" placeholder="topic keys" /></td>
          <td class="num"><input type="number" data-f="weight" data-i="${index}" min="1" max="100"
            value="${item.weight}" style="width:70px;text-align:right" /></td>
          <td><button class="btn sm danger" data-remove="${index}">Remove</button></td>
        </tr>`,
      )
      .join("");
    root.querySelector("#total").textContent =
      `${rows.reduce((sum, r) => sum + Number(r.weight || 0), 0)} marks in total`;

    tbody.querySelectorAll("[data-f]").forEach((input) =>
      input.addEventListener("input", () => {
        const item = rows[Number(input.dataset.i)];
        const field = input.dataset.f;
        if (field === "text") item.text = input.value;
        else if (field === "weight") item.weight = Number(input.value) || 1;
        else if (field === "concepts")
          item.concept_ids = input.value.split(",").map((c) => c.trim()).filter(Boolean);
        else if (field === "check")
          item.static_check = input.value ? { ...(item.static_check || {}), kind: input.value } : null;
        else if (field === "target" && item.static_check) item.static_check.target = input.value;
        if (field === "weight") {
          root.querySelector("#total").textContent =
            `${rows.reduce((sum, r) => sum + Number(r.weight || 0), 0)} marks in total`;
        }
      }),
    );
    tbody.querySelectorAll("[data-remove]").forEach((button) =>
      button.addEventListener("click", () => {
        rows.splice(Number(button.dataset.remove), 1);
        draw();
      }),
    );
  }
  draw();

  root.querySelector("#add").addEventListener("click", () => {
    rows.push({
      item_key: `rb_${String(rows.length + 1).padStart(2, "0")}`,
      text: "",
      weight: 5,
      category: "correctness",
      checkable_by: ["static"],
      concept_ids: [],
      static_check: null,
    });
    draw();
  });

  root.querySelector("#save").addEventListener("click", async (event) => {
    const items = rows
      .filter((r) => r.text.trim())
      .map((r) => ({
        item_key: r.item_key,
        text: r.text.trim(),
        category: r.category || "correctness",
        weight: Number(r.weight) || 1,
        concept_ids: r.concept_ids || [],
        checkable_by: r.static_check ? ["static"] : r.checkable_by || ["structural"],
        test_ids: r.test_ids || [],
        static_check: r.static_check?.kind ? r.static_check : null,
      }));
    if (!items.length) return toast("Keep at least one criterion.", "bad");

    event.target.disabled = true;
    event.target.textContent = "Saving…";
    try {
      const body = await api.post(`/api/faculty/versions/${version.id}/rubric`, {
        faculty_id: faculty,
        items,
        note: root.querySelector("#note").value,
        regrade: root.querySelector("#regrade").checked,
      });
      toast(body.regraded ? `${body.message} Re-marked ${body.regraded}.` : body.message);
      await refreshCounts();
      window.location.hash = `#/f/assignment/${assignment.id}`;
      render(root, ctx, assignment.id);
    } catch (error) {
      toast(error.message, "bad");
      event.target.disabled = false;
      event.target.textContent = "Save";
    }
  });
}
