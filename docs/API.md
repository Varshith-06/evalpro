# HTTP API

Base URL `http://127.0.0.1:8000`. Interactive documentation is served at
`/docs` (OpenAPI) while the application is running.

There is no authentication in the demo. In a real deployment identity arrives
from the LTI 1.3 launch and every route is role-scoped; the one authorisation
rule already enforced is that a student cannot read another student's run.

---

## Core

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness, plus how many isolation layers this host applies |
| `GET` | `/api/courses` | Courses with roster, concept, and assignment counts |
| `GET` | `/api/courses/{course_id}` | Course detail with outcomes and assignments |
| `GET` | `/api/courses/{course_id}/concepts` | **The concept graph** — nodes and prerequisite edges, ready for a layout |
| `GET` | `/api/courses/{course_id}/students` | Roster |
| `GET` | `/api/courses/{course_id}/staff` | Faculty and administrators |
| `GET` | `/api/assignments/{assignment_id}` | Assignment with all versions and the active rubric and test suite |
| `POST` | `/api/assignments/{assignment_id}/draft` | **A1–A3**: draft a rubric and tests from a brief, then validate every test against the reference solution |
| `POST` | `/api/versions/{version_id}/approve` | **A4**: apply faculty edits and approve. Nothing grades until this runs |
| `POST` | `/api/submit` | Submit an attempt and evaluate it |
| `GET` | `/api/runs/{run_id}` | Full evidence trail for a run |
| `POST` | `/api/runs/{run_id}/appeal` | One-click appeal on a specific rubric item |

### `POST /api/submit`

```json
{
  "assignment_id": "…",
  "student_id": "…",
  "files": { "solution.py": "def solve(nums): ..." },
  "report_text": "optional",
  "force_full_run": false
}
```

`force_full_run` runs the hidden test set too. It is a faculty operation:
pre-deadline student feedback always runs the visible subset only, so the hidden
set never leaks.

Returns `{attempt_id, run_id, from_cache, visible_only, detail}`. An identical
resubmission returns the cached run — that is the content-hash idempotency
guarantee, and it doubles as the deadline submit-spam defence.

Errors: `400` ingest rejected the bundle, `409` no approved version or attempt
limit reached, `429` rate limited. Every message explains itself.

### `GET /api/runs/{run_id}`

The evidence trail. Notable fields:

- `reproducibility` — pinned `pipeline_version`, `model_versions`,
  `rubric_version`, and `content_hash`. The same submission regraded a year
  later against these produces an identical result.
- `stages[]` — one entry per cascade stage with its status, duration, and
  structured evidence.
- `items[]` — per rubric item: score, confidence, `signal_agreement`, the
  individual `signals` with their reliabilities, human-readable `evidence`
  lines, and any faculty override with its reason.
- `tests[]` — outcome, expected vs actual, diff, CPU time, and whether the test
  ran on repaired source.
- `verdict` — state, escalation reasons, integrity flag, syntax penalty.

---

## Student — *what should I work on next?*

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/student/{student_id}/courses/{course_id}` | The mastery map, ranked next actions, assignment history, trajectories |
| `GET` | `/api/student/{student_id}/courses/{course_id}/runs/{run_id}` | Per-assignment drill-in, with hidden tests stripped from pre-deadline runs |

The dashboard leads with `mastery_map`, not a grade list. Each entry in
`next_actions` carries `why_flagged` (human-readable, traceable to specific
submissions), `evidence_refs`, `recommended_action`, `estimated_effort`, and the
`prerequisite_path` the recommendation was derived from.

`action_kind` is `remediate` when mastery is low and `diagnose` when it is
merely *uncertain* — resolve the ambiguity before prescribing practice.

Reading another student's run returns `403`.

---

## Faculty — *what should I teach next?*

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/faculty/courses/{course_id}/health` | Cohort heatmap, re-teach signals, broken items, misconceptions, interventions, pacing |
| `GET` | `/api/faculty/courses/{course_id}/queue` | Review queue, sorted by expected value of attention |
| `GET` | `/api/faculty/runs/{run_id}/review` | Side-by-side evidence, reference solution, similarity report |
| `POST` | `/api/faculty/runs/{run_id}/override` | Override one rubric item — **a reason is mandatory** |
| `POST` | `/api/faculty/runs/{run_id}/confirm` | Confirm an escalated run as-is |
| `GET` | `/api/faculty/clusters/{cluster_id}/briefing` | Misconception briefing with representative submissions |
| `POST` | `/api/faculty/clusters/{cluster_id}/label` | Name a misconception — the label persists across semesters |
| `GET` | `/api/faculty/courses/{course_id}/appeals` | Open and resolved appeals |
| `POST` | `/api/faculty/appeals/{appeal_id}/resolve` | Uphold or reject an appeal |
| `POST` | `/api/faculty/assignments/{assignment_id}/regrade` | Bulk regrade under the current approved rubric |

`queue` entries carry `priority` (escalation severity × contested rubric weight
× confidence deficit) and `why_this_first` in plain language. The first item
should be where a human minute is worth the most.

`override` returns `400` if the reason is shorter than a few words. This is not
bureaucracy: the reason is the training signal for the confidence estimator, and
it is what the student sees on appeal. Overriding retrains model 5 and refreshes
the course analytics.

---

## Administrator — *where is this programme weak?*

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/admin/courses/{course_id}/attainment` | Direct CO and PO attainment, exportable |
| `GET` | `/api/admin/courses/{course_id}/risk` | At-risk students with ranked contributing factors and support routing |
| `GET` | `/api/admin/courses/{course_id}/bias-audit` | Re-run the demographic bias audit for the risk model |
| `GET` | `/api/admin/courses/{course_id}/metrics` | §11 platform trust metrics against their targets |
| `GET` | `/api/admin/courses/{course_id}/integrity` | Ranked similarity evidence with aligned regions |
| `GET` | `/api/admin/courses/{course_id}/trends` | Per-assignment distributions and release/escalation counts |
| `GET` | `/api/admin/courses/{course_id}/gradebook` | LTI Assignment and Grade Services writeback payload |
| `GET` | `/api/admin/system-health` | Queue depth, latency, and the honest isolation report |
| `POST` | `/api/admin/courses/{course_id}/refresh` | Recompute Layers 2 and 3. Idempotent |
| `POST` | `/api/admin/train-confidence` | Retrain the confidence estimator on accumulated overrides |

`bias-audit` can return `deployment_blocked: true`. That is the intended
behaviour, not an error state: a risk model that over-flags one group must not
ship, and an audit with no comparable groups reports itself as *inconclusive*
rather than passing.

`integrity` never states a verdict. Machine-generated-code detectors are not
reliable enough to carry consequences, and automated accusations are a legal and
ethical liability, so the endpoint returns evidence and a policy statement.
