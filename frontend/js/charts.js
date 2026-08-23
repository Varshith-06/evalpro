// Hand-rolled SVG. No chart library, because the two visualisations that carry
// this product — a prerequisite DAG shaded by mastery, and a concept-by-student
// heatmap — are not what an off-the-shelf chart library is good at anyway.

import { esc, masteryColour, pct } from "./util.js";

/**
 * The mastery map: the concept DAG laid out in syllabus order, shaded by
 * mastery, with prerequisite edges drawn.
 *
 * This is the student's landing view rather than a grade list, and it is laid
 * out by teaching week so a student reads it as "the course so far" instead of
 * an abstract graph.
 */
export function masteryMap(nodes, edges, { width = 900, onSelect = null } = {}) {
  const weeks = [...new Set(nodes.map((n) => n.week ?? 99))].sort((a, b) => a - b);
  const columns = new Map(weeks.map((w, i) => [w, i]));
  const byColumn = new Map();
  for (const node of nodes) {
    const key = columns.get(node.week ?? 99);
    if (!byColumn.has(key)) byColumn.set(key, []);
    byColumn.get(key).push(node);
  }

  const colWidth = Math.max(110, Math.min(160, (width - 40) / Math.max(1, weeks.length)));
  const rowHeight = 52;
  const maxRows = Math.max(...[...byColumn.values()].map((c) => c.length));
  const height = maxRows * rowHeight + 62;
  const totalWidth = Math.max(width, weeks.length * colWidth + 40);

  const position = new Map();
  for (const [column, group] of byColumn) {
    group.forEach((node, index) => {
      position.set(node.id, {
        x: 24 + column * colWidth + colWidth / 2,
        y: 46 + index * rowHeight + rowHeight / 2,
        node,
      });
    });
  }

  const edgeMarkup = edges
    .map((edge) => {
      const from = position.get(edge.from);
      const to = position.get(edge.to);
      if (!from || !to) return "";
      const midX = (from.x + to.x) / 2;
      const gap = to.node.prerequisite_gap || from.node.prerequisite_gap;
      return `<path d="M${from.x + 34} ${from.y} C ${midX} ${from.y}, ${midX} ${to.y}, ${to.x - 34} ${to.y}"
        fill="none" stroke="${gap ? "rgba(180,35,42,.5)" : "rgba(200,207,216,.9)"}" stroke-width="${gap ? 1.6 : 1}" />`;
    })
    .join("");

  const nodeMarkup = [...position.values()]
    .map(({ x, y, node }) => {
      const fill = masteryColour(node.mastery, node.uncertainty);
      const stroke = node.prerequisite_gap ? "var(--bad)" : "var(--border)";
      const label = node.name.length > 20 ? `${node.name.slice(0, 19)}…` : node.name;
      const value = node.mastery === null || node.mastery === undefined ? "?" : pct(node.mastery);
      return `<g class="concept-node" data-concept="${esc(node.id)}" style="cursor:pointer">
        <title>${esc(node.name)} — ${node.mastery === null ? "no evidence yet" : `mastery ${value}, uncertainty ${pct(node.uncertainty)}`}</title>
        <rect x="${x - 36}" y="${y - 15}" width="72" height="30" rx="7"
              fill="${fill}" stroke="${stroke}" stroke-width="${node.prerequisite_gap ? 1.6 : 1}" />
        <text x="${x}" y="${y + 4}" text-anchor="middle" font-size="11" font-weight="600"
              fill="var(--text)">${value}</text>
        <text x="${x}" y="${y + 26}" text-anchor="middle" class="node-label">${esc(label)}</text>
      </g>`;
    })
    .join("");

  const weekMarkup = weeks
    .map((week, index) => {
      const x = 24 + index * colWidth + colWidth / 2;
      return `<text x="${x}" y="22" text-anchor="middle" class="axis-label">${
        week === 99 ? "unscheduled" : `week ${week}`
      }</text>`;
    })
    .join("");

  const svg = `<svg viewBox="0 0 ${totalWidth} ${height}" width="${totalWidth}" height="${height}" role="img">
    ${weekMarkup}${edgeMarkup}${nodeMarkup}
  </svg>`;

  const wrap = document.createElement("div");
  wrap.className = "svg-wrap";
  wrap.innerHTML = svg;
  if (onSelect) {
    wrap.querySelectorAll(".concept-node").forEach((group) =>
      group.addEventListener("click", () => onSelect(group.dataset.concept)),
    );
  }
  return wrap;
}

/** Cohort heatmap: students down, concepts across. Blank means no evidence,
 *  which is a different and equally important thing from "scored zero". */
export function heatmap(concepts, rows, { onCell = null } = {}) {
  const cell = 17;
  const gap = 2;
  const labelWidth = 128;
  const headerHeight = 92;
  const width = labelWidth + concepts.length * (cell + gap) + 20;
  const height = headerHeight + rows.length * (cell + gap) + 30;

  const header = concepts
    .map((concept, index) => {
      const x = labelWidth + index * (cell + gap) + cell / 2;
      const name = concept.name.length > 22 ? `${concept.name.slice(0, 21)}…` : concept.name;
      return `<text transform="translate(${x} ${headerHeight - 6}) rotate(-58)"
        class="heat-name" text-anchor="start">${esc(name)}</text>`;
    })
    .join("");

  const meanRow = concepts
    .map((concept, index) => {
      const x = labelWidth + index * (cell + gap);
      return `<rect x="${x}" y="${headerHeight}" width="${cell}" height="${cell}" rx="3"
        fill="${masteryColour(concept.mean, 0)}" stroke="var(--border)" stroke-width=".5">
        <title>${esc(concept.name)} — cohort mean ${pct(concept.mean)} over ${concept.n} student(s)</title></rect>`;
    })
    .join("");

  const body = rows
    .map((row, rowIndex) => {
      const y = headerHeight + (rowIndex + 1) * (cell + gap) + 6;
      const name = row.student_name.length > 18 ? `${row.student_name.slice(0, 17)}…` : row.student_name;
      const cells = row.cells
        .map((c, index) => {
          const x = labelWidth + index * (cell + gap);
          const fill = c.mastery === null ? "#f1f3f6" : masteryColour(c.mastery, c.uncertainty);
          return `<rect class="heat-cell" data-student="${esc(row.student_id)}" data-concept="${esc(c.concept)}"
            x="${x}" y="${y}" width="${cell}" height="${cell}" rx="3" fill="${fill}"
            stroke="var(--border)" stroke-width=".5">
            <title>${esc(row.student_name)} — ${esc(c.concept)}: ${
              c.mastery === null ? "no evidence" : `${pct(c.mastery)} (uncertainty ${pct(c.uncertainty)})`
            }</title></rect>`;
        })
        .join("");
      return `<text x="0" y="${y + 12}" class="heat-name">${esc(name)}</text>${cells}`;
    })
    .join("");

  const wrap = document.createElement("div");
  wrap.className = "svg-wrap";
  wrap.innerHTML = `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img">
    ${header}
    <text x="0" y="${headerHeight + 13}" class="heat-name" font-weight="700">COHORT MEAN</text>
    ${meanRow}${body}
  </svg>`;
  if (onCell) {
    wrap.querySelectorAll(".heat-cell").forEach((rect) =>
      rect.addEventListener("click", () => onCell(rect.dataset.student, rect.dataset.concept)),
    );
  }
  return wrap;
}

/** Mastery trajectory. Uncertainty is drawn as a band, not hidden: the width of
 *  the band is often the most honest thing on the chart. */
export function trajectory(series, { width = 560, height = 150 } = {}) {
  const padding = { top: 12, right: 12, bottom: 22, left: 30 };
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;
  const palette = ["#2563eb", "#6b3fc4", "#157f3d", "#a05e00", "#b4232a", "#0e7490"];

  const maxPoints = Math.max(...series.map((s) => s.points.length), 2);
  const xFor = (i) => padding.left + (i / (maxPoints - 1)) * innerW;
  const yFor = (v) => padding.top + (1 - v) * innerH;

  const gridlines = [0, 0.25, 0.5, 0.7, 1]
    .map(
      (v) =>
        `<line x1="${padding.left}" x2="${width - padding.right}" y1="${yFor(v)}" y2="${yFor(v)}"
          stroke="${v === 0.7 ? "rgba(21,127,61,.35)" : "var(--border)"}"
          stroke-dasharray="${v === 0.7 ? "4 3" : "0"}" stroke-width="1" />
        <text x="2" y="${yFor(v) + 3}" class="axis-label">${(v * 100).toFixed(0)}</text>`,
    )
    .join("");

  const lines = series
    .map((s, index) => {
      const colour = palette[index % palette.length];
      const path = s.points
        .map((p, i) => `${i === 0 ? "M" : "L"}${xFor(i)} ${yFor(p.estimate)}`)
        .join(" ");
      const bandTop = s.points.map((p, i) => `${i === 0 ? "M" : "L"}${xFor(i)} ${yFor(Math.min(1, p.estimate + (p.uncertainty ?? 0) / 2))}`).join(" ");
      const bandBottom = s.points
        .slice()
        .reverse()
        .map((p, i) => {
          const originalIndex = s.points.length - 1 - i;
          return `L${xFor(originalIndex)} ${yFor(Math.max(0, p.estimate - (p.uncertainty ?? 0) / 2))}`;
        })
        .join(" ");
      return `<path d="${bandTop} ${bandBottom} Z" fill="${colour}" opacity=".08" />
        <path d="${path}" fill="none" stroke="${colour}" stroke-width="1.8" stroke-linejoin="round" />
        ${s.points
          .map((p, i) => `<circle cx="${xFor(i)}" cy="${yFor(p.estimate)}" r="2.4" fill="${colour}"><title>${esc(s.concept)}: ${pct(p.estimate)} (±${pct((p.uncertainty ?? 0) / 2)})</title></circle>`)
          .join("")}`;
    })
    .join("");

  const legend = series
    .map(
      (s, index) =>
        `<span><i class="swatch" style="background:${palette[index % palette.length]}"></i>${esc(s.concept)}</span>`,
    )
    .join("");

  const wrap = document.createElement("div");
  wrap.innerHTML = `<div class="svg-wrap"><svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}">
    ${gridlines}${lines}
    <text x="${padding.left}" y="${height - 5}" class="axis-label">earlier</text>
    <text x="${width - padding.right}" y="${height - 5}" class="axis-label" text-anchor="end">latest</text>
  </svg></div><div class="legend">${legend}</div>`;
  return wrap;
}

/** Score distribution. Shape matters more than the mean: a bimodal cohort and a
 *  uniformly mediocre one have identical averages and need different responses. */
export function histogram(distribution, { width = 420, height = 130 } = {}) {
  const bins = distribution.histogram || [];
  if (!bins.length) return document.createElement("div");
  const max = Math.max(...bins, 1);
  const barWidth = (width - 24) / bins.length;
  const bars = bins
    .map((count, index) => {
      const h = (count / max) * (height - 34);
      const x = 12 + index * barWidth;
      const midpoint = (index + 0.5) / bins.length;
      return `<rect x="${x + 1}" y="${height - 22 - h}" width="${barWidth - 2}" height="${h}" rx="2"
        fill="${masteryColour(midpoint, 0.15)}"><title>${(index * 10)}–${(index + 1) * 10}%: ${count}</title></rect>`;
    })
    .join("");
  const wrap = document.createElement("div");
  wrap.innerHTML = `<div class="svg-wrap"><svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}">
    ${bars}
    <text x="12" y="${height - 6}" class="axis-label">0%</text>
    <text x="${width - 12}" y="${height - 6}" class="axis-label" text-anchor="end">100%</text>
    <text x="${width / 2}" y="${height - 6}" class="axis-label" text-anchor="middle">${esc(distribution.shape || "")}</text>
  </svg></div>`;
  return wrap;
}
