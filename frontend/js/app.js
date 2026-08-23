// Shell: sidebar, identity, and a hash router.
//
// One screen per task. The menu is what someone would actually click during a
// lab session, and nothing on a page explains the platform to them.

import { api, esc, toast } from "./util.js";

import * as sLabs from "./pages/student-labs.js";
import * as sLab from "./pages/student-lab.js";
import * as sFeedback from "./pages/student-feedback.js";
import * as sProgress from "./pages/student-progress.js";

import * as fAssignments from "./pages/faculty-assignments.js";
import * as fNew from "./pages/faculty-new-assignment.js";
import * as fAssignment from "./pages/faculty-assignment.js";
import * as fMarks from "./pages/faculty-marks.js";
import * as fReview from "./pages/faculty-review.js";
import * as fReviewOne from "./pages/faculty-review-one.js";
import * as fClass from "./pages/faculty-class.js";
import * as fMistakes from "./pages/faculty-mistakes.js";

import * as aOutcomes from "./pages/admin-outcomes.js";
import * as aRisk from "./pages/admin-risk.js";
import * as aIntegrity from "./pages/admin-integrity.js";
import * as aSystem from "./pages/admin-system.js";

export const ctx = {
  courseId: null,
  course: null,
  role: "student",
  userId: null,
  userName: "",
  students: [],
  staff: [],
  counts: {},
};

const ROUTES = [
  { path: "s/labs", role: "student", page: sLabs, label: "My labs" },
  { path: "s/lab", role: "student", page: sLab, label: "Lab", param: true },
  { path: "s/feedback", role: "student", page: sFeedback, label: "Feedback", param: true },
  { path: "s/progress", role: "student", page: sProgress, label: "My progress" },

  { path: "f/assignments", role: "faculty", page: fAssignments, label: "Assignments" },
  { path: "f/new", role: "faculty", page: fNew, label: "New assignment" },
  { path: "f/assignment", role: "faculty", page: fAssignment, label: "Assignment", param: true },
  { path: "f/review", role: "faculty", page: fReview, label: "To review" },
  { path: "f/marks", role: "faculty", page: fMarks, label: "Marks" },
  { path: "f/submission", role: "faculty", page: fReviewOne, label: "Submission", param: true },
  { path: "f/class", role: "faculty", page: fClass, label: "Class progress" },
  { path: "f/mistakes", role: "faculty", page: fMistakes, label: "Common mistakes" },

  { path: "a/outcomes", role: "admin", page: aOutcomes, label: "Outcomes" },
  { path: "a/risk", role: "admin", page: aRisk, label: "Students at risk" },
  { path: "a/integrity", role: "admin", page: aIntegrity, label: "Integrity" },
  { path: "a/system", role: "admin", page: aSystem, label: "System" },
];

const MENUS = {
  student: [
    { group: null, items: ["s/labs", "s/progress"] },
  ],
  faculty: [
    { group: "Teaching", items: ["f/assignments", "f/review", "f/marks"] },
    { group: "Insight", items: ["f/class", "f/mistakes"] },
  ],
  admin: [
    { group: null, items: ["a/outcomes", "a/risk", "a/integrity", "a/system"] },
  ],
};

const DEFAULT_ROUTE = { student: "s/labs", faculty: "f/assignments", admin: "a/outcomes" };

const pageEl = document.getElementById("page");
const menuEl = document.getElementById("menu");
const crumbsEl = document.getElementById("crumbs");
const actionsEl = document.getElementById("topbar-actions");

// --------------------------------------------------------------------------
export function go(path) {
  window.location.hash = `#/${path}`;
}

export function setCrumbs(parts) {
  crumbsEl.innerHTML = parts
    .map((part, index) =>
      index === parts.length - 1
        ? `<strong>${esc(part.label)}</strong>`
        : `<a href="#/${esc(part.path)}">${esc(part.label)}</a><span class="sep">/</span>`,
    )
    .join("");
}

export function setActions(html = "") {
  actionsEl.innerHTML = html;
  return actionsEl;
}

// --------------------------------------------------------------------------
async function boot() {
  const courses = await api.get("/api/courses");
  const select = document.getElementById("course-select");
  select.innerHTML = courses.map((c) => `<option value="${esc(c.id)}">${esc(c.code)}</option>`).join("");
  ctx.courseId = courses[0]?.id ?? null;
  ctx.course = courses[0] ?? null;
  select.addEventListener("change", async () => {
    ctx.courseId = select.value;
    ctx.course = courses.find((c) => c.id === select.value) || null;
    await loadPeople();
    render();
  });

  document.getElementById("user-select").addEventListener("change", (event) => {
    const [role, id] = event.target.value.split("|");
    const changedRole = role !== ctx.role;
    ctx.role = role;
    ctx.userId = id;
    const chosen = [...ctx.students, ...ctx.staff].find((u) => u.id === id);
    ctx.userName = chosen?.name ?? "";
    drawRoles();
    if (changedRole) go(DEFAULT_ROUTE[role]);
    else render();
  });

  document.querySelectorAll("#role-switch button").forEach((button) =>
    button.addEventListener("click", () => switchRole(button.dataset.role)),
  );

  await loadPeople();
  window.addEventListener("hashchange", render);
  if (!window.location.hash) go(DEFAULT_ROUTE[ctx.role]);
  else render();
  refreshCounts();
}

async function loadPeople() {
  const [students, staff] = await Promise.all([
    api.get(`/api/courses/${ctx.courseId}/students`),
    api.get(`/api/courses/${ctx.courseId}/staff`),
  ]);
  ctx.students = students;
  ctx.staff = staff;

  const select = document.getElementById("user-select");
  select.innerHTML = `
    <optgroup label="Students">
      ${students.map((s) => `<option value="student|${esc(s.id)}">${esc(s.name)}</option>`).join("")}
    </optgroup>
    <optgroup label="Staff">
      ${staff
        .map((s) => `<option value="${esc(s.role)}|${esc(s.id)}">${esc(s.name)}</option>`)
        .join("")}
    </optgroup>`;

  if (!ctx.userId || ![...students, ...staff].some((u) => u.id === ctx.userId)) {
    // ?as=faculty|admin|student picks who you are signed in as, so a view can
    // be linked to or bookmarked directly.
    const wanted = new URLSearchParams(window.location.search).get("as");
    const staffMatch = staff.find((s) => s.role === wanted);
    const first = staffMatch || (wanted === "student" ? students[0] : students[0] || staff[0]);
    ctx.userId = first?.id ?? null;
    ctx.userName = first?.name ?? "";
    ctx.role = staffMatch ? staffMatch.role : students.length ? "student" : staff[0]?.role || "faculty";
  }
  select.value = `${ctx.role}|${ctx.userId}`;
  drawRoles();
}

export async function refreshCounts() {
  try {
    const [queue, labs] = await Promise.all([
      api.get(`/api/faculty/courses/${ctx.courseId}/queue`),
      api.get(`/api/faculty/courses/${ctx.courseId}/assignments`),
    ]);
    ctx.counts = { "f/review": queue.length, "f/assignments": labs.length };
  } catch {
    ctx.counts = {};
  }
  drawMenu(currentRoute()?.path);
}

function drawRoles() {
  document.querySelectorAll("#role-switch button").forEach((button) =>
    button.classList.toggle("active", button.dataset.role === ctx.role),
  );
}

/** Switching role picks a sensible person for it, so one click is enough. */
function switchRole(role) {
  if (role === ctx.role) return;
  const person =
    role === "student" ? ctx.students[0] : ctx.staff.find((s) => s.role === role) || ctx.staff[0];
  if (!person) {
    toast(`Nobody on this course has the ${role} role.`, "bad");
    return;
  }
  ctx.role = role;
  ctx.userId = person.id;
  ctx.userName = person.name;
  document.getElementById("user-select").value = `${role}|${person.id}`;
  drawRoles();
  go(DEFAULT_ROUTE[role]);
}

function currentRoute() {
  const raw = (window.location.hash || "").replace(/^#\/?/, "");
  const [path, param] = [raw.split("/").slice(0, 2).join("/"), raw.split("/")[2]];
  const route = ROUTES.find((r) => r.path === path);
  return route ? { ...route, param } : null;
}

function drawMenu(activePath) {
  const sections = MENUS[ctx.role] || [];
  menuEl.innerHTML = sections
    .map((section) => {
      const links = section.items
        .map((path) => {
          const route = ROUTES.find((r) => r.path === path);
          const count = ctx.counts[path];
          const alert = path === "f/review" && count > 0;
          return `<a href="#/${esc(path)}" class="${path === activePath ? "active" : ""}">
            <span>${esc(route.label)}</span>
            ${count !== undefined ? `<span class="count ${alert ? "alert" : ""}">${count}</span>` : ""}
          </a>`;
        })
        .join("");
      return (section.group ? `<div class="group">${esc(section.group)}</div>` : "") + links;
    })
    .join("");
}

async function render() {
  const route = currentRoute();
  if (!route) {
    go(DEFAULT_ROUTE[ctx.role]);
    return;
  }
  if (route.role !== ctx.role) {
    go(DEFAULT_ROUTE[ctx.role]);
    return;
  }
  drawMenu(route.path);
  setActions("");
  pageEl.innerHTML = '<div class="loading">Loading…</div>';
  try {
    await route.page.render(pageEl, ctx, route.param);
  } catch (error) {
    pageEl.innerHTML = `<div class="note bad">Couldn't load this page. ${esc(error.message)}</div>`;
    toast(error.message, "bad");
  }
}

export { render };

boot();
