# EvalPro — Automated Programming Lab Evaluation Platform

**Smart India Hackathon 2026 · Automated Programming Lab Evaluation Platform**

Not a grader that produces analytics as a byproduct. **An academic analytics
platform whose sensor is an automated grader.**

The distinction is structural. A grading engine scopes everything to one
assignment and answers "what mark does this submission deserve." This platform
scopes everything to a *concept taxonomy that spans the course* and answers:

- Which concepts has this student actually mastered, and which are they faking
  with pattern-matching?
- Which prerequisite gap is causing this student's current failures?
- Which of my rubric items are actually measuring anything?
- Which students will fail this course, and how early can we tell?
- What is our attainment against declared course outcomes?

Grading falls out of that as a special case. The reverse does not hold.

---

## Run it

```bash
cd backend
python -m pip install -r requirements.txt
python -m app.main
```

Then open <http://127.0.0.1:8000>.

On first run the demo course builds itself: 24 students, 24 concepts, 5 course
outcomes, 4 labs, ~90 submissions. **Every score, mastery estimate, item
statistic, misconception cluster, risk flag and attainment figure in it is
produced by the real cascade running real student code in the real sandbox.**
Nothing is fixture data — that takes about 80 seconds on a laptop, and it is the
only way the demo means anything.

The frontend needs no build step: no `npm install`, no bundler, no `node_modules`.

```bash
python -m pytest              # from backend/ — engine, analytics, and end-to-end API tests
python scripts/demo.py        # the 90-second narrative walkthrough
python scripts/build_presentation.py   # regenerate the SIH deck from the official template
```

---

## The shape of it

```
                     ┌────────────────────────────────────────┐
   LMS / SIS ───────▶│  L0  Institutional context             │
                     │  roster, course structure, outcomes    │
                     └────────────────┬───────────────────────┘
                                      │
   Faculty ─────────▶┌────────────────▼───────────────────────┐
                     │  L1  SENSING                           │
   Student ──submit─▶│  authoring → cascade → sandbox → gate  │
                     │  output: per-rubric-item evidence      │
                     └────────────────┬───────────────────────┘
                                      │ tagged with concept_ids
                     ┌────────────────▼───────────────────────┐
                     │  L2  ACCUMULATION                      │
                     │  concept graph, knowledge tracing,     │
                     │  item analysis, cohort aggregation     │
                     └────────────────┬───────────────────────┘
                                      │ mastery state
                     ┌────────────────▼───────────────────────┐
                     │  L3  ACTION                            │
                     │  student remediation, faculty re-teach │
                     │  admin early warning, CO–PO attainment │
                     └────────────────────────────────────────┘
```

**The single field that connects L1 to L2 is `RubricItem.concept_ids[]`.**
Without it you have twelve disconnected gradebooks. With it, every submission
becomes evidence about specific, named competencies, and everything in L2 and L3
becomes computable.

---

## Design principles, and where they live in the code

| Principle | Where it is enforced |
|---|---|
| **Evidence, not scores** — the primary output is a defensible evidence trail; the score is derived from it | Every stage writes `StageResult.evidence`; only `b7_gate` produces a number |
| **Deterministic first, learned second, LLM last** | `b1`–`b5` are parsers and test runners; the LLM appears only in `services/authoring_service.py`, once per *assignment* |
| **Abstain rather than guess** | `b7_gate.decide` — coverage is a tunable dial, not a fixed property |
| **Reproducible forever** | `EvaluationRun` pins `pipeline_version` and `model_versions`; seeds derive from `(assignment_id, attempt_id)`; the sandbox freezes `PYTHONHASHSEED` |
| **Adversarial by default** | `engine/sandbox.py` and the catalogue in `docs/SECURITY.md` |
| **Every assignment is a sensor reading, not a verdict** | Nothing in `models.py` is scoped only to one assignment |

---

## The evaluation cascade

Ordered by cost ascending, with early exits. Each stage writes structured
evidence; **none writes a score directly**.

| Stage | What it does | Module |
|---|---|---|
| **B0** Ingest | Hard decompression limits, path-traversal rejection, content hash for free idempotency and submit-spam protection | `engine/b0_ingest.py` |
| **B1** Structure | Language detection, **error-tolerant parse**, code graph: symbols, imports, call graph, per-function CFG, def-use chains | `engine/b1_structure.py` |
| **B2** Integrity | AST-normalised winnowing fingerprints (MOSS) + structural clone detection, with base-code exclusion and **cohort-relative** outlier flagging | `engine/b2_integrity.py` |
| **B3** Build | Compilation is code execution, so it gets the same sandbox with a shorter budget and no network | `engine/b3_build.py` |
| **B4** Execute | Each test in a fresh one-shot instance. **The oracle never enters the sandbox.** Property tests seeded from `(assignment_id, attempt_id)` | `engine/b4_execute.py` |
| **B5** Partial credit | Repair distance, structural credit, static rubric checks | `engine/b5_partial.py` |
| **B6** Report cross-check | Does the report describe *this* code? Does it cover the rubric? | `engine/b6_report.py` |
| **B7** Aggregate and gate | Reliability-weighted score, confidence, routing table | `engine/b7_gate.py` |

### The three things that make students trust it

**Repair distance.** On build failure, search for the minimum token-level edit
that makes it compile. If the repaired program compiles and passes tests, award
the test score with a syntax penalty proportional to the edit distance. *A
missing colon costs two marks, not a hundred percent of them.* This single
mechanism resolves most "I got a zero and I'd basically solved it" cases.

**Structural credit.** Code that is structurally a correct binary search with an
off-by-one earns algorithm-comprehension marks at a 0% test pass rate.

**Static rubric checks.** Deterministic queries against the code graph — a guard
on the empty-input path, recursion where required, a required API actually
called. Cheap, exact, and fully explainable to the student.

---

## Security

You are executing untrusted code, written by capable people, on your
infrastructure, at scale, on a deadline. See **[docs/SECURITY.md](docs/SECURITY.md)**
for the full thirteen-layer stack and the adversarial catalogue.

The two decisions that matter more than any flag:

1. **Expected outputs live outside the boundary.** `SandboxJob` has no field for
   an expected output — it is not representable. Comparison happens on the host.
2. **One-shot instances.** Every job gets a fresh directory, destroyed
   afterwards, so no submission can contaminate the next.

`GET /api/admin/system-health` returns an **honest capability report**: which of
the thirteen layers this host actually applies, and which need the production
backend. An operator should never have to guess how contained the grader is.

---

## What the interfaces answer

Each role gets one landing view answering one question. Everything else is a
drill-in. Resisting the dashboard-of-everything is the design.

| Role | Question | Landing view |
|---|---|---|
| **Student** | *What should I work on next?* | The mastery map — the concept DAG shaded by mastery with prerequisite gaps highlighted — plus ranked next actions with the reason and the evidence |
| **Faculty** | *What should I teach next?* | Cohort mastery heatmap, re-teach signals ranked by downstream prerequisite impact, broken-item alerts, intervention list |
| **Administrator** | *Where is this programme weak?* | CO–PO attainment with drill-down to evidence, at-risk cohort with contributing factors, platform trust metrics, integrity dashboard |

Student feedback is never "72/100". It is *"Empty-input handling: 0/8. Test 11
crashed with IndexError at solution.py:14. No length guard found on the input
path."*

---

## The analytics

**Knowledge tracing** (`analytics/bkt.py`) — Bayesian Knowledge Tracing, four
parameters per concept fitted per cohort by EM. Interpretable, works on small
data, and survives an appeal. Two extensions matter more than a fancier model:
observations are weighted by evidence confidence, and repeated failure
propagates *down the prerequisite DAG* so remediation points somewhere useful.
Uncertainty is a first-class output.

**Item analysis** (`analytics/item_analysis.py`) — difficulty, point-biserial
discrimination, and concept alignment per rubric item per cohort. This grades
the *assessment*, not the student. Negative discrimination means the item is
broken: strong students are failing it, which almost always indicates an
ambiguous spec or a wrong test.

**Misconception clustering** (`analytics/clustering.py`) — HDBSCAN over failure
signatures. Unsupervised, so it needs no labels and ships immediately. Clusters
are named by the instructor once and then persist across semesters, becoming a
reusable misconception library per course.

**Remediation** (`analytics/remediation.py`) — traverse the concept DAG from the
student's failures to the lowest-mastery unmastered prerequisite, and recommend
practice *there*. Someone failing tree traversal because they don't understand
pointers is sent to pointers, not to more trees. Where mastery is *uncertain*
rather than low, the recommendation is a diagnostic.

**Early warning** (`analytics/risk.py`) — three non-negotiables, enforced in
code rather than in a policy document: a flagged student cannot be constructed
without ranked contributing factors; the routing field accepts only support
routes; and `bias_audit` blocks deployment when flag rates differ by more than
5% across any protected group. Protected attributes are used **only** by the
audit — never as features.

**CO–PO attainment** (`analytics/attainment.py`) — because
`RubricItem → concept_ids → course_outcomes` already exists, outcome attainment
is a rollup, not a project. Direct attainment from performance evidence, with
per-student traceability down to individual submissions.

---

## Measuring the platform

`GET /api/admin/courses/{id}/metrics` reports the platform against its own
targets, **including the numbers that make it look bad**. Grading drift is
invisible until it isn't.

Two of these matter as much as the accuracy metrics and are usually missing:
**mastery predictive validity** (a knowledge-tracing model that doesn't predict
future performance is an expensive decoration) and **early-warning bias delta**
(a risk model that flags one demographic disproportionately is actively
harmful).

---

## Layout

```
backend/app/
  models.py              the data model — immutable and versioned throughout
  config.py              every threshold and limit, in one place
  engine/                Layer 1: the cascade B0–B7, and the sandbox
  analytics/             Layer 2 and 3: BKT, item analysis, clustering,
                         remediation, risk, attainment, confidence estimation
  services/              authoring, grading, analytics, metrics
  api/                   core + one router per role
  harness/runner.py      runs INSIDE the sandbox; owns the result channel
  seed.py, seed_data.py  the demo course, run through the real pipeline
frontend/                build-free ES modules, hand-authored SVG
docs/                    architecture spec, security model, API reference
scripts/                 demo walkthrough, presentation generator
presentation/            the SIH idea-submission deck
```

`lab-evaluation-platform-spec.md` at the repository root is the architecture and
implementation specification this is built from.

---

## Honest limitations

- **The sandbox on a developer machine is not a production boundary.** Layers 1,
  3, 5, 7 and 8 need a Linux host with KVM. The system reports exactly which
  layers it is missing rather than implying it has them.
- **Auto-release coverage in the demo is about 30%**, not the 70% the spec
  targets for semester 2. That is the honest cold-start number: the demo cohort
  is deliberately full of broken code, and the confidence estimator has three
  faculty overrides to learn from rather than three thousand.
- **The entailment model is a lexical stand-in.** The production design is a
  fine-tuned DeBERTa-v3 encoder; the interface is identical, so swapping it in
  changes one module.
- **Python is the only fully supported language today.** The code graph is
  deliberately language-agnostic so a tree-sitter backend drops in behind
  `build_code_graph` without touching anything downstream.
- **The demo has no authentication.** Identity comes from the LTI launch in a
  real deployment; the demo picks a user from the roster.
