// New assignment. Fill in as much or as little as you want.
//
// The preview panel is the point of the screen: it shows what the platform read
// out of the brief before anything is created, so an instructor can see the
// rubric they are about to publish and fix the brief if it is wrong.

import { api, esc, toast } from "../util.js";
import { setCrumbs, refreshCounts } from "../app.js";

let debounce = null;

export async function render(root, ctx) {
  setCrumbs([{ label: "Assignments", path: "f/assignments" }, { label: "New" }]);

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1>New assignment</h1>
        <div class="sub">Only the title and the instructions are required.</div>
      </div>
      <div class="actions">
        <a class="btn" href="#/f/assignments">Cancel</a>
        <button class="btn primary" id="create">Create</button>
      </div>
    </div>

    <div class="grid g-main">
      <div class="card">
        <div class="field">
          <label for="title">Title</label>
          <input type="text" id="title" placeholder="e.g. Reverse a linked list" />
        </div>

        <div class="field">
          <label for="brief">Instructions for students</label>
          <div class="hint">Write it the way you'd write it on a handout. Anything you state as a
            requirement becomes something we can check — "must use recursion", "handle the empty
            list", "don't use sorted".</div>
          <textarea id="brief" rows="9" placeholder="Write a function that…&#10;- …"></textarea>
        </div>

        <div class="row">
          <div class="field">
            <label for="entry_call">Function students must write <span class="optional">(optional)</span></label>
            <input type="text" id="entry_call" value="solve" />
          </div>
          <div class="field">
            <label for="due">Due date <span class="optional">(optional)</span></label>
            <input type="datetime-local" id="due" />
          </div>
        </div>

        <div class="field">
          <label for="reference">Model solution <span class="optional">(optional)</span></label>
          <div class="hint">Add one and we'll generate test cases, check them against it, and run
            them on every submission. Leave it blank and we'll mark the approach instead.</div>
          <textarea class="code" id="reference" rows="8" spellcheck="false"
            placeholder="def solve(nums):&#10;    ..."></textarea>
        </div>

        <div class="field">
          <label class="checkbox"><input type="checkbox" id="report" /> Ask students for a short written report</label>
        </div>
      </div>

      <div class="stack">
        <div class="card" id="preview">
          <header><h2>What we'll mark</h2></header>
          <div class="empty">Start typing the instructions and this fills in.</div>
        </div>
      </div>
    </div>`;

  const fields = ["brief", "entry_call", "reference", "report"].map((id) => root.querySelector(`#${id}`));
  fields.forEach((el) => el.addEventListener("input", () => schedulePreview(root, ctx)));
  root.querySelector("#report").addEventListener("change", () => schedulePreview(root, ctx));

  root.querySelector("#create").addEventListener("click", () => create(root, ctx));
}

function schedulePreview(root, ctx) {
  clearTimeout(debounce);
  debounce = setTimeout(() => preview(root, ctx), 350);
}

async function preview(root, ctx) {
  const brief = root.querySelector("#brief").value.trim();
  const panel = root.querySelector("#preview");
  if (brief.length < 10) {
    panel.innerHTML = `<header><h2>What we'll mark</h2></header>
      <div class="empty">Start typing the instructions and this fills in.</div>`;
    return;
  }
  try {
    const body = await api.post(`/api/faculty/courses/${ctx.courseId}/assignments/preview`, {
      brief,
      entry_call: root.querySelector("#entry_call").value || "solve",
      reference_solution: root.querySelector("#reference").value,
      requires_report: root.querySelector("#report").checked,
    });
    const total = body.rubric.reduce((sum, item) => sum + item.weight, 0);
    panel.innerHTML = `
      <header><h2>What we'll mark</h2><span class="faint small">${total} marks</span></header>
      <div class="note ${body.grading_mode === "static" ? "warn" : "good"}" style="margin-bottom:12px">
        ${
          body.grading_mode === "static"
            ? "No model solution, so we'll mark the approach: whether the code runs, whether it does what you asked for, and how it's built."
            : "Model solution given, so we'll generate test cases, check them against it, and run them on every submission."
        }
      </div>
      ${body.rubric
        .map(
          (item) => `<div style="padding:8px 0;border-bottom:1px solid var(--border)">
            <div style="display:flex;gap:10px;align-items:baseline">
              <span class="grow" style="flex:1">${esc(item.text)}</span>
              <span class="faint small nowrap">${item.weight} marks</span>
            </div>
            <div class="chips" style="margin-top:5px">
              ${item.concept_names.map((c) => `<span class="chip info">${esc(c)}</span>`).join("")}
              ${item.static_check ? `<span class="chip">${esc(item.static_check.kind.replace(/_/g, " "))}</span>` : ""}
              ${item.checkable_by.includes("test") ? `<span class="chip accent">test</span>` : ""}
            </div>
          </div>`,
        )
        .join("")}
      <div class="faint small" style="margin-top:10px">You can edit any of this after creating it.</div>`;
  } catch (error) {
    panel.innerHTML = `<header><h2>What we'll mark</h2></header>
      <div class="note bad">${esc(error.message)}</div>`;
  }
}

async function create(root, ctx) {
  const title = root.querySelector("#title").value.trim();
  const brief = root.querySelector("#brief").value.trim();
  if (title.length < 3) return toast("Give it a title.", "bad");
  if (brief.length < 10) return toast("Add some instructions for students.", "bad");

  const faculty = ctx.role === "faculty" ? ctx.userId : ctx.staff.find((s) => s.role === "faculty")?.id;
  const due = root.querySelector("#due").value;
  const button = root.querySelector("#create");
  button.disabled = true;
  button.textContent = "Creating…";

  try {
    const body = await api.post(`/api/faculty/courses/${ctx.courseId}/assignments`, {
      faculty_id: faculty,
      title,
      brief,
      entry_call: root.querySelector("#entry_call").value || "solve",
      reference_solution: root.querySelector("#reference").value,
      requires_report: root.querySelector("#report").checked,
      due_at: due ? new Date(due).toISOString() : null,
    });
    await refreshCounts();
    if (!body.published) {
      toast("Saved as a draft — see the notes.", "bad");
      root.querySelector("#preview").innerHTML =
        `<header><h2>Saved as a draft</h2></header>` +
        body.notes.map((n) => `<div class="note warn">${esc(n)}</div>`).join("");
      button.disabled = false;
      button.textContent = "Create";
      return;
    }
    toast(`Published with ${body.rubric_items} marking criteria.`);
    window.location.hash = `#/f/assignment/${body.assignment_id}`;
  } catch (error) {
    toast(error.message, "bad");
    button.disabled = false;
    button.textContent = "Create";
  }
}
