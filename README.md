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
python -m pytest              # 128 tests: engine, analytics, authoring, and end-to-end API
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
| **Deterministic first, learned second, LLM last** | `b1`–`b5` are parsers and test runners. The LLM has exactly one seat — the `Drafter` protocol in `services/authoring_service.py`, called once per *assignment*, never per submission. This build ships `HeuristicDrafter`, so it currently makes **no model calls at all**: nothing in `backend/app` opens a network connection |
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
off-by-one earns algorithm-comprehension marks at a 0% test pass rate. It is
consulted on every rubric item that behaviour is supposed to answer, including
items whose tests all failed, and it can only ever *raise* a mark - a correct
submission is never taxed by a similarity heuristic. When the code will not even
parse, the error-tolerant reader recovers the control-flow shape and marks that
instead. Measured on the seeded sorting lab:

| Submission | Tests passing | Mark |
| --- | --- | --- |
| Correct | all | 100% |
| Right algorithm, comparison flipped | **none** | 60% |
| Same idea, three syntax errors, will not compile | none | 34% |
| Returns an empty list | none | 26% |

Where an assignment has no reference solution and therefore no tests, this is
not a fallback - it is the whole mark.

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

## The interface

One screen per task, with a sidebar that changes by role.

**The app opens as a student.** Use the **VIEW AS · Student / Faculty / Admin**
switch at the top of the sidebar to change role — that is where *New assignment*
and the review queue live. The picker below it chooses which person you are
viewing as; `?as=faculty` in the URL does the same thing for a bookmark.

| Role | Screens |
|---|---|
| **Student** | *My labs* — status and score per lab · *Lab* — read the brief, write or upload code, submit, see previous attempts · *Feedback* — per-criterion marks with the specific reason for each, and a button to question any of them · *My progress* — topic map, what to revise next |
| **Faculty** | *Assignments* — the list, **New assignment**, and per assignment: everyone's submissions including who has not handed in, edit the criteria, publish/unpublish, re-mark, delete · *To review* — the queue, most useful first, then their code beside what each criterion found · *Marks* — the whole gradebook, exportable · *Class progress* — topics to go back over, who to check in with, which criteria are not working · *Common mistakes* — groups of students who got the same thing wrong |
| **Administrator** | *Outcomes* — CO/PO attainment, exportable · *Students at risk* · *Integrity* · *System* — marking quality, fairness audit, sandbox status |

The app shell is served `no-store`. It is a few kilobytes of unhashed files, and
a cached `app.js` running last week's UI is indistinguishable from a missing
feature.

Student feedback is never "72/100". It is *"Empty-input handling: 0/8. Test 11
crashed with IndexError at solution.py:14. No length guard found on the input
path."*

---

## Creating an assignment

An instructor can stop at any level of effort, and the platform fills in the
rest — or works around it.

| What you give it | What happens |
|---|---|
| Title + instructions | It reads the instructions for requirements it can actually check, writes a rubric, and marks the **approach**: does the code run, does it use what you asked for, how is it built |
| … + a model solution | It generates test cases, **validates every one against your solution**, discards any that fail, and runs the rest on every submission |
| … + your own rubric | Yours is used verbatim |

The **New assignment** screen previews the rubric it read out of your brief
before anything is created, and every generated item traces back to a phrase
you wrote. Afterwards the criteria are editable from the assignment page.

**Editing an approved rubric creates a new version rather than changing the old
one.** Every run pins the version it was marked against, so a student who
already has a mark keeps the criteria they were marked against until you
deliberately re-mark them — which is the only way "regrade this next year and
get the same answer" stays true.

Two rules the authoring flow will not bend on:

- **No executable grading without a model solution.** A generated test that
  nothing has verified is how a hallucinated expected output silently penalises
  a whole cohort, so the mode is derived from your inputs rather than chosen.
- **No rubric item that cannot earn evidence.** An item with nothing behind it
  is a mark no submission can ever get, so approval is refused rather than
  quietly capping everyone's grade.

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
  layers it is missing rather than implying it has them. Measured on Windows 11:
  **5 of 13 layers applied** — even the POSIX rlimits at layer 6 are unavailable.
  Probing it with deliberately hostile code confirms what that means: sandboxed
  code **can open network sockets, read the repository including its own source,
  and write anywhere the account can write**. What holds on every host is the
  part that protects *grades* rather than the machine — the expected output never
  enters the guest, every job gets a one-shot directory, the wall-clock kill
  comes from a supervisor outside the guest, and the environment is scrubbed to
  15 variables. Run it on untrusted code only under the Linux backend.
- **Auto-release coverage in the demo is about 60%**, not the 70% the spec
  targets for semester 2. That is the honest cold-start number: the demo cohort
  is deliberately full of broken code, and the confidence estimator has three
  faculty overrides to learn from rather than three thousand. What is left is
  not noise - every escalation in the seeded run is a copied submission, a
  self-report the code contradicts, or a mark sitting on the pass/fail line.
- **The entailment model is a lexical stand-in.** The production design is a
  fine-tuned DeBERTa-v3 encoder; the interface is identical, so swapping it in
  changes one module.
- **Python is the only fully supported language today.** The code graph is
  deliberately language-agnostic so a tree-sitter backend drops in behind
  `build_code_graph` without touching anything downstream.
- **The demo has no authentication.** Identity comes from the LTI launch in a
  real deployment; the demo picks a user from the roster via the VIEW AS switch.
- **The rubric editor sets a check's kind and target, not its parameters.**
  Thresholds like `min_ratio` or `max_depth` keep whatever value they were
  created with; changing one needs the API today.
- **A student submits one file at a time through the editor.** Uploading a
  `.zip` handles multi-file work, but the in-page editor is single-file.
