// One lab: the brief, the editor, submit, and the attempt history.
// This is the screen a student is actually on during a session.

import { api, chip, esc, meter, toast, when } from "../util.js";
import { setCrumbs } from "../app.js";

export async function render(root, ctx, assignmentId) {
  const [assignment, dashboard] = await Promise.all([
    api.get(`/api/assignments/${assignmentId}`),
    api.get(`/api/student/${ctx.userId}/courses/${ctx.courseId}`),
  ]);
  const lab = dashboard.assignments.find((a) => a.assignment_id === assignmentId);
  const version = assignment.active_version;
  setCrumbs([{ label: "My labs", path: "s/labs" }, { label: assignment.code }]);

  const history = await attemptHistory(ctx, assignmentId);
  const lastFiles = history.length ? history[history.length - 1].files : null;
  const entry = version?.entry_point || "solution.py";

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1>${esc(assignment.title)}</h1>
        <div class="sub">${esc(assignment.code)} · due ${when(assignment.due_at)}
          ${lab ? `· attempt ${lab.attempts + 1} of ${esc(String(assignment.versions ? 10 : 10))}` : ""}</div>
      </div>
      <div class="actions">
        ${lab?.latest ? `<a class="btn" href="#/s/feedback/${esc(lab.latest.run_id)}">Latest feedback</a>` : ""}
      </div>
    </div>

    <div class="grid g-main">
      <div class="stack">
        <div class="card">
          <header><h2>Your solution</h2>
            <span class="faint small">${esc(entry)}</span></header>
          <textarea class="code" id="code" spellcheck="false"
            placeholder="Paste or write your solution here…">${esc(
              lastFiles ? Object.values(lastFiles)[0] : "",
            )}</textarea>
          ${
            assignment.requires_report
              ? `<div class="field" style="margin-top:14px">
                   <label for="report">Report</label>
                   <div class="hint">Explain your approach and its complexity.</div>
                   <textarea id="report" placeholder="Describe what you built and why…">${esc(
                     history.at(-1)?.report || "",
                   )}</textarea>
                 </div>`
              : ""
          }
          <div class="actions" style="margin-top:14px">
            <button class="btn primary" id="submit">Submit</button>
            <button class="btn" id="pick">Upload a file instead</button>
            <input type="file" id="upload" accept=".py,.txt,.zip,.md" hidden />
            <span class="faint small" id="submit-note" style="align-self:center"></span>
          </div>
        </div>

        ${history.length ? historyCard(history) : ""}
      </div>

      <div class="stack">
        <div class="card">
          <header><h2>What to do</h2></header>
          <pre class="code" style="white-space:pre-wrap">${esc(version?.spec_text || "")}</pre>
        </div>
        ${version ? rubricCard(version) : ""}
      </div>
    </div>`;

  const button = root.querySelector("#submit");
  button.addEventListener("click", async () => {
    const code = root.querySelector("#code").value;
    if (!code.trim()) return toast("Nothing to submit yet.", "bad");
    button.disabled = true;
    button.textContent = "Running…";
    root.querySelector("#submit-note").textContent = "Building and testing your code.";
    try {
      const reportEl = root.querySelector("#report");
      const result = await api.post("/api/submit", {
        assignment_id: assignmentId,
        student_id: ctx.userId,
        files: { [entry]: code },
        report_text: reportEl ? reportEl.value : "",
      });
      toast(result.from_cache ? "Same as your last attempt — showing that result." : "Submitted.");
      window.location.hash = `#/s/feedback/${result.run_id}`;
    } catch (error) {
      toast(error.message, "bad");
      button.disabled = false;
      button.textContent = "Submit";
      root.querySelector("#submit-note").textContent = "";
    }
  });

  // Uploading a .py drops it into the editor so the student can see what they
  // are about to hand in. A .zip goes straight through, since there is nothing
  // meaningful to show in a single text box.
  const picker = root.querySelector("#upload");
  root.querySelector("#pick").addEventListener("click", () => picker.click());
  picker.addEventListener("change", async () => {
    const file = picker.files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".zip")) {
      root.querySelector("#code").value = await file.text();
      root.querySelector("#submit-note").textContent = `Loaded ${file.name}. Check it, then submit.`;
      return;
    }
    const form = new FormData();
    form.append("assignment_id", assignmentId);
    form.append("student_id", ctx.userId);
    form.append("file", file);
    root.querySelector("#submit-note").textContent = "Unpacking and marking…";
    try {
      const response = await fetch("/api/submit/upload", { method: "POST", body: form });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Upload failed");
      toast(`Submitted ${body.files.length} file(s).`);
      window.location.hash = `#/s/feedback/${body.run_id}`;
    } catch (error) {
      toast(error.message, "bad");
      root.querySelector("#submit-note").textContent = "";
    }
  });
}

async function attemptHistory(ctx, assignmentId) {
  const dashboard = await api.get(`/api/student/${ctx.userId}/courses/${ctx.courseId}`);
  const lab = dashboard.assignments.find((a) => a.assignment_id === assignmentId);
  if (!lab?.latest) return [];
  const detail = await api.get(
    `/api/student/${ctx.userId}/courses/${ctx.courseId}/runs/${lab.latest.run_id}`,
  );
  return [
    {
      run_id: lab.latest.run_id,
      attempt_no: detail.attempt_no,
      at: detail.submitted_at,
      score: lab.latest.score,
      state: lab.latest.state,
      files: detail.files,
      report: detail.report_text,
    },
  ];
}

function historyCard(history) {
  return `<div class="card pad0">
    <header><h2>Your attempts</h2></header>
    <div class="scroll-x"><table>
      <thead><tr><th>Attempt</th><th>Submitted</th><th>Status</th><th class="num">Score</th><th></th></tr></thead>
      <tbody>${history
        .map(
          (h) => `<tr>
            <td class="t-main">#${h.attempt_no}</td>
            <td class="nowrap">${when(h.at)}</td>
            <td>${chip(h.state)}</td>
            <td class="num" style="width:140px">${meter(h.score)}</td>
            <td><a class="btn sm" href="#/s/feedback/${esc(h.run_id)}">Feedback</a></td>
          </tr>`,
        )
        .join("")}</tbody>
    </table></div>
  </div>`;
}

function rubricCard(version) {
  const total = version.rubric.reduce((sum, item) => sum + item.weight, 0);
  return `<div class="card pad0">
    <header><h2>How it's marked</h2><span class="faint small">${total} marks</span></header>
    <div class="scroll-x"><table>
      <tbody>${version.rubric
        .map(
          (item) => `<tr>
            <td>${esc(item.text)}</td>
            <td class="num nowrap faint">${item.weight}</td>
          </tr>`,
        )
        .join("")}</tbody>
    </table></div>
  </div>`;
}
