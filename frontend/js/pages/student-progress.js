import { api, chip, esc, pct } from "../util.js";
import { masteryMap, trajectory } from "../charts.js";
import { setCrumbs } from "../app.js";

export async function render(root, ctx) {
  setCrumbs([{ label: "My progress" }]);
  const data = await api.get(`/api/student/${ctx.userId}/courses/${ctx.courseId}`);
  const s = data.summary;

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1>My progress</h1>
        <div class="sub">Topic by topic, from the labs you've submitted.</div>
      </div>
    </div>

    <div class="tiles">
      <div class="tile good"><div class="k">Solid</div><div class="v">${s.mastered}</div></div>
      <div class="tile warn"><div class="k">Getting there</div><div class="v">${s.developing}</div></div>
      <div class="tile bad"><div class="k">Needs work</div><div class="v">${s.gaps}</div></div>
      <div class="tile"><div class="k">Not covered yet</div>
        <div class="v">${s.concepts_total - s.concepts_with_evidence + s.uncertain}</div></div>
    </div>

    <div class="grid g-main">
      <div class="card">
        <header><h2>Topic map</h2>
          <span class="faint small">left to right follows the course</span></header>
        <div id="map"></div>
        <div class="legend">
          <span><i class="swatch" style="background:hsl(6 58% 84%)"></i>needs work</span>
          <span><i class="swatch" style="background:hsl(70 40% 74%)"></i>getting there</span>
          <span><i class="swatch" style="background:hsl(134 58% 62%)"></i>solid</span>
          <span><i class="swatch" style="background:#eef1f4;border:1px solid var(--border)"></i>not covered</span>
        </div>
      </div>

      <div class="card">
        <header><h2>Work on these next</h2></header>
        <div id="next"></div>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <header><h2>How your topics have moved</h2>
        <span class="faint small">shaded band = how sure we are</span></header>
      <div id="traj"></div>
    </div>`;

  root.querySelector("#map").appendChild(
    masteryMap(data.mastery_map.nodes, data.mastery_map.edges, {
      onSelect: (id) => {
        const node = data.mastery_map.nodes.find((n) => n.id === id);
        if (node) alert(`${node.name}\n\n${node.mastery === null ? "No evidence yet." : `Mastery ${pct(node.mastery)}`}`);
      },
    }),
  );

  const next = root.querySelector("#next");
  next.innerHTML = data.next_actions.length
    ? data.next_actions.map(action).join("")
    : `<div class="empty">Nothing flagged — you're on top of every topic we've measured.</div>`;

  const traj = root.querySelector("#traj");
  if (data.trajectories.length) traj.appendChild(trajectory(data.trajectories.slice(0, 5)));
  else traj.innerHTML = `<div class="empty">Not enough submissions yet to plot a trend.</div>`;
}

function action(entry) {
  return `<div class="card" style="border-left:3px solid var(--${
    entry.action_kind === "diagnose" ? "info" : "warn"
  });margin-bottom:10px">
    <header>
      <h3>${esc(entry.concept_name)}</h3>
      ${chip(entry.action_kind === "diagnose" ? "uncertain" : "gap",
             entry.action_kind === "diagnose" ? "Check first" : "Practise")}
    </header>
    <div class="small dim">${esc(entry.why_flagged)}</div>
    <div class="small" style="margin-top:7px">${esc(entry.recommended_action)}</div>
    <div class="faint small" style="margin-top:6px">About ${esc(entry.estimated_effort)}</div>
  </div>`;
}
