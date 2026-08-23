// Shell: course selection, role switching, and the student picker.
// Each role gets one landing view answering one question. Everything else is a
// drill-in — resisting the dashboard-of-everything is the whole design.

import { api, closeDrawer, esc } from "./util.js";
import * as studentView from "./views/student.js";
import * as facultyView from "./views/faculty.js";
import * as adminView from "./views/admin.js";

const state = {
  role: "student",
  courseId: null,
  students: [],
  studentId: null,
  facultyId: null,
};

const view = document.getElementById("view");

async function boot() {
  const courses = await api.get("/api/courses");
  const select = document.getElementById("course-select");
  select.innerHTML = courses
    .map((c) => `<option value="${esc(c.id)}">${esc(c.code)} — ${esc(c.title)}</option>`)
    .join("");
  state.courseId = courses[0]?.id ?? null;
  select.addEventListener("change", async () => {
    state.courseId = select.value;
    await loadPeople();
    await renderView();
  });

  document.querySelectorAll(".role-btn").forEach((button) =>
    button.addEventListener("click", () => {
      document.querySelectorAll(".role-btn").forEach((b) => b.classList.remove("active"));
      button.classList.add("active");
      state.role = button.dataset.role;
      renderView();
    }),
  );

  document.querySelectorAll("[data-close]").forEach((node) => node.addEventListener("click", closeDrawer));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDrawer();
  });
  document.addEventListener("evalpro:refresh", () => renderView());

  await loadPeople();
  await renderView();
  loadHealth();
}

async function loadPeople() {
  if (!state.courseId) return;
  state.students = await api.get(`/api/courses/${state.courseId}/students`);
  state.studentId = state.students[0]?.id ?? null;
  // The acting staff identity comes from the roster. A real deployment gets it
  // from the LTI launch; either way an override must be attributable to a real
  // person, because the reason attached to it is training data.
  const staff = await api.get(`/api/courses/${state.courseId}/staff`);
  state.facultyId = (staff.find((s) => s.role === "faculty") ?? staff[0])?.id ?? null;
  state.facultyName = (staff.find((s) => s.role === "faculty") ?? staff[0])?.name ?? "";
}

function studentPicker() {
  return `<label class="field"><span>Viewing as</span>
    <select id="student-select">
      ${state.students
        .map(
          (s) =>
            `<option value="${esc(s.id)}" ${s.id === state.studentId ? "selected" : ""}>${esc(s.name)}</option>`,
        )
        .join("")}
    </select></label>`;
}

async function renderView() {
  if (!state.courseId) {
    view.innerHTML = '<div class="empty">No course is configured.</div>';
    return;
  }

  // The student picker belongs to the student role only; showing it everywhere
  // would imply the other roles are scoped to one student, which they are not.
  const existing = document.getElementById("student-picker-slot");
  if (existing) existing.remove();
  if (state.role === "student") {
    const slot = document.createElement("span");
    slot.id = "student-picker-slot";
    slot.innerHTML = studentPicker();
    document.querySelector(".topbar-right").prepend(slot);
    slot.querySelector("#student-select").addEventListener("change", (event) => {
      state.studentId = event.target.value;
      renderView();
    });
  }

  try {
    if (state.role === "student") {
      await studentView.render(view, { courseId: state.courseId, studentId: state.studentId });
    } else if (state.role === "faculty") {
      await facultyView.render(view, { courseId: state.courseId, facultyId: state.facultyId });
    } else {
      await adminView.render(view, { courseId: state.courseId });
    }
  } catch (error) {
    view.innerHTML = `<div class="callout bad"><strong>Could not load this view.</strong>
      <div class="mono small" style="margin-top:6px">${esc(error.message)}</div></div>`;
  }
}

async function loadHealth() {
  try {
    const health = await api.get("/api/admin/system-health");
    document.getElementById("footer-health").textContent =
      `${health.total_runs} runs · p95 ${(health.p95_latency_ms / 1000).toFixed(1)}s · ` +
      `sandbox ${health.isolation.applied_count}/${health.isolation.total_layers} layers on this host`;
  } catch {
    /* the footer is informational; a failure here must not break the page */
  }
}

boot();
