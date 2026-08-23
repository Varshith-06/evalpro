"""B5 - Partial credit. The stage that determines whether students trust the
platform.

Three deterministic mechanisms, no LLM anywhere:

* **B5a repair distance.** On build failure, search for the minimum token-level
  edit that makes the program compile. If the repaired program compiles and
  passes tests, award the test score with a syntax penalty proportional to the
  edit distance. A missing colon should cost two marks, not a hundred percent
  of them. This single mechanism resolves most "I got a zero and I'd basically
  solved it" cases.
* **B5b structural credit.** Compare the AST/CFG against the reference and a
  bank of known-correct variants. Code that is structurally a correct binary
  search with an off-by-one earns algorithm-comprehension marks at a 0% test
  pass rate.
* **B5c static rubric checks.** Deterministic queries against the code graph.
  Cheap, exact, fully explainable. Push as many rubric items here as possible.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from .b1_structure import CodeGraph, build_code_graph

MAX_REPAIR_EDITS = 3
MAX_CANDIDATES = 400


# ==========================================================================
# B5a - repair distance
# ==========================================================================
@dataclass
class RepairResult:
    repaired: bool
    edit_distance: int = 0
    edits: list[dict] = field(default_factory=list)
    repaired_files: dict[str, str] = field(default_factory=dict)
    note: str = ""

    def penalty_fraction(self, per_edit: float = 0.05, cap: float = 0.25) -> float:
        """Proportional, capped, and explicitly small.

        The point of repair distance is that a trivial syntax slip stops being
        catastrophic; a penalty that grows without bound would reintroduce the
        problem it was built to remove.
        """
        return min(cap, per_edit * self.edit_distance)


_BLOCK_KEYWORDS = ("if", "elif", "else", "for", "while", "def", "class", "try", "except", "finally", "with")
_OPENERS = {"(": ")", "[": "]", "{": "}"}
_CLOSERS = {v: k for k, v in _OPENERS.items()}


def _compiles(source: str, filename: str = "<repair>") -> tuple[bool, SyntaxError | None]:
    try:
        compile(source, filename, "exec")
        return True, None
    except SyntaxError as exc:
        return False, exc
    except ValueError as exc:  # e.g. source with null bytes
        return False, SyntaxError(str(exc))


def _levenshtein(a: str, b: str, cutoff: int = 2) -> int:
    if abs(len(a) - len(b)) > cutoff:
        return cutoff + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            )
        previous = current
        if min(previous) > cutoff:
            return cutoff + 1
    return previous[-1]


def _candidate_edits(lines: list[str], error_line: int, symbols: set[str]) -> list[tuple[list[str], dict]]:
    """Localised repair candidates around the reported error.

    Tree-sitter's error-recovery tree is what localises candidates in the
    production design; here the compiler's own ``(lineno, offset)`` plays that
    role, widened by one line in each direction because Python frequently
    reports a delimiter error one line after the omission.
    """
    candidates: list[tuple[list[str], dict]] = []
    window = range(max(0, error_line - 2), min(len(lines), error_line + 1))

    for idx in window:
        line = lines[idx]
        stripped = line.rstrip()
        if not stripped.strip():
            continue
        indent = line[: len(line) - len(line.lstrip())]

        # 1. Missing colon after a block header.
        first_word = stripped.strip().split()[0].rstrip("(:") if stripped.strip() else ""
        if first_word in _BLOCK_KEYWORDS and not stripped.endswith(":"):
            patched = list(lines)
            patched[idx] = stripped + ":"
            candidates.append((patched, {"kind": "insert_colon", "line": idx + 1, "detail": "missing ':' after block header"}))

        # 2. Unbalanced delimiters on this line.
        balance: list[str] = []
        for ch in stripped:
            if ch in _OPENERS:
                balance.append(ch)
            elif ch in _CLOSERS and balance and balance[-1] == _CLOSERS[ch]:
                balance.pop()
        if balance:
            closing = "".join(_OPENERS[ch] for ch in reversed(balance))
            patched = list(lines)
            patched[idx] = stripped + closing
            candidates.append((patched, {"kind": "close_delimiter", "line": idx + 1, "detail": f"inserted '{closing}'"}))

        # 3. A stray trailing delimiter.
        if stripped and stripped[-1] in _CLOSERS:
            patched = list(lines)
            patched[idx] = stripped[:-1]
            candidates.append((patched, {"kind": "delete_delimiter", "line": idx + 1, "detail": f"removed trailing '{stripped[-1]}'"}))

        # 4. Odd number of quotes.
        for quote in ('"', "'"):
            if stripped.count(quote) % 2 == 1:
                patched = list(lines)
                patched[idx] = stripped + quote
                candidates.append((patched, {"kind": "close_string", "line": idx + 1, "detail": f"closed {quote} literal"}))

        # 5. Assignment where a comparison was meant (``if x = 1:``).
        if re.match(r"^\s*(if|while|elif)\b.*[^=!<>]=[^=]", line):
            patched = list(lines)
            patched[idx] = re.sub(r"([^=!<>])=([^=])", r"\1==\2", line, count=1)
            candidates.append((patched, {"kind": "fix_comparison", "line": idx + 1, "detail": "'=' -> '==' in a condition"}))

        # 6. Identifier typo against the symbol table (edit distance 1).
        for word in set(re.findall(r"\b[A-Za-z_]\w*\b", stripped)):
            if word in symbols:
                continue
            near = [s for s in symbols if _levenshtein(word, s, 1) == 1]
            if len(near) == 1:
                patched = list(lines)
                patched[idx] = re.sub(rf"\b{re.escape(word)}\b", near[0], line, count=1)
                candidates.append(
                    (patched, {"kind": "fix_typo", "line": idx + 1, "detail": f"'{word}' -> '{near[0]}'"})
                )

        # 7. Indentation slip.
        if idx > 0:
            previous = lines[idx - 1].rstrip()
            if previous.endswith(":"):
                expected = previous[: len(previous) - len(previous.lstrip())] + "    "
                if not line.startswith(expected):
                    patched = list(lines)
                    patched[idx] = expected + line.strip()
                    candidates.append((patched, {"kind": "fix_indent", "line": idx + 1, "detail": "re-indented block body"}))
            if indent and len(indent) % 4 != 0:
                patched = list(lines)
                patched[idx] = " " * (4 * round(len(indent) / 4)) + line.strip()
                candidates.append((patched, {"kind": "fix_indent", "line": idx + 1, "detail": "normalised indentation"}))

    return candidates


def repair_source(source: str, symbols: set[str] | None = None, max_edits: int = MAX_REPAIR_EDITS) -> RepairResult:
    """Breadth-first search for the smallest edit set that compiles."""
    ok, _ = _compiles(source)
    if ok:
        return RepairResult(repaired=True, edit_distance=0, note="compiles as submitted")

    symbols = symbols or set()
    symbols |= set(re.findall(r"\bdef\s+(\w+)", source))
    symbols |= {"len", "range", "print", "int", "str", "list", "dict", "set", "sorted", "sum", "min", "max", "abs", "enumerate", "return", "self"}

    frontier: list[tuple[str, list[dict]]] = [(source, [])]
    seen: set[str] = {source}
    explored = 0

    for depth in range(1, max_edits + 1):
        next_frontier: list[tuple[str, list[dict]]] = []
        for text, edits in frontier:
            ok, exc = _compiles(text)
            if ok:
                return RepairResult(True, len(edits), edits, note=f"repaired with {len(edits)} edit(s)")
            if exc is None:
                continue
            lines = text.splitlines()
            error_line = max(0, (exc.lineno or 1) - 1)
            for patched_lines, edit in _candidate_edits(lines, error_line, symbols):
                explored += 1
                if explored > MAX_CANDIDATES:
                    return RepairResult(False, 0, [], note="repair search budget exhausted")
                patched = "\n".join(patched_lines)
                if patched in seen:
                    continue
                seen.add(patched)
                compiled, _ = _compiles(patched)
                if compiled:
                    trail = edits + [edit]
                    return RepairResult(True, len(trail), trail, note=f"repaired with {len(trail)} edit(s)")
                next_frontier.append((patched, edits + [edit]))
        frontier = next_frontier[:24]   # keep the search bounded and fast
        if not frontier:
            break

    return RepairResult(False, 0, [], note="no repair found within the edit budget")


def repair_bundle(files: dict[str, str], symbols: set[str] | None = None) -> RepairResult:
    """Repair every non-compiling file in a bundle, accumulating distance."""
    repaired_files = dict(files)
    total_edits = 0
    all_edits: list[dict] = []
    any_failed = False

    for path, source in files.items():
        if not path.endswith(".py"):
            continue
        ok, _ = _compiles(source, path)
        if ok:
            continue
        result = repair_source(source, symbols)
        if not result.repaired:
            any_failed = True
            continue
        total_edits += result.edit_distance
        for edit in result.edits:
            all_edits.append({**edit, "file": path})
        lines = source.splitlines()
        # Re-apply by re-running the search on the file we actually keep.
        patched = source
        for _ in range(result.edit_distance + 1):
            compiled, exc = _compiles(patched, path)
            if compiled:
                break
            single = repair_source(patched, symbols, max_edits=1)
            if not single.repaired or not single.edits:
                break
            patched = _apply_edit(patched, single.edits[0])
        repaired_files[path] = patched
        del lines

    if any_failed:
        return RepairResult(False, total_edits, all_edits, repaired_files, "one or more files could not be repaired")
    return RepairResult(
        repaired=bool(all_edits),
        edit_distance=total_edits,
        edits=all_edits,
        repaired_files=repaired_files,
        note=f"repaired {len(all_edits)} syntax defect(s) across the bundle",
    )


def _apply_edit(source: str, edit: dict) -> str:
    """Re-derive the patched text for a single recorded edit."""
    lines = source.splitlines()
    idx = edit["line"] - 1
    if idx < 0 or idx >= len(lines):
        return source
    line = lines[idx]
    kind = edit["kind"]
    if kind == "insert_colon":
        lines[idx] = line.rstrip() + ":"
    elif kind == "close_delimiter":
        detail = edit.get("detail", "")
        match = re.search(r"'(.+)'", detail)
        lines[idx] = line.rstrip() + (match.group(1) if match else "")
    elif kind == "delete_delimiter":
        lines[idx] = line.rstrip()[:-1]
    elif kind == "close_string":
        detail = edit.get("detail", "")
        quote = '"' if '"' in detail else "'"
        lines[idx] = line.rstrip() + quote
    elif kind == "fix_comparison":
        lines[idx] = re.sub(r"([^=!<>])=([^=])", r"\1==\2", line, count=1)
    elif kind == "fix_typo":
        match = re.search(r"'(\w+)' -> '(\w+)'", edit.get("detail", ""))
        if match:
            lines[idx] = re.sub(rf"\b{re.escape(match.group(1))}\b", match.group(2), line, count=1)
    elif kind == "fix_indent":
        if idx > 0:
            previous = lines[idx - 1].rstrip()
            base = previous[: len(previous) - len(previous.lstrip())]
            lines[idx] = base + ("    " if previous.endswith(":") else "") + line.strip()
    return "\n".join(lines)


# ==========================================================================
# B5b - structural credit
# ==========================================================================
def pq_grams(tree: ast.AST, p: int = 2, q: int = 3) -> set[tuple]:
    """pq-gram profile: a linear-time approximation of tree edit distance.

    Full APTED is the right algorithm for a production deployment; pq-grams
    give a very close ordering at a fraction of the cost, which matters when
    every submission is compared against a bank of known-correct variants.
    """
    grams: set[tuple] = set()

    def label(node: ast.AST) -> str:
        return type(node).__name__

    def walk(node: ast.AST, stem: tuple) -> None:
        stem = (stem + (label(node),))[-p:]
        children = [c for c in ast.iter_child_nodes(node)]
        if not children:
            grams.add(stem + ("*",) * q)
            return
        window = ["*"] * (q - 1)
        for child in children:
            window.append(label(child))
            grams.add(stem + tuple(window[-q:]))
            walk(child, stem)
        for _ in range(q - 1):
            window.append("*")
            grams.add(stem + tuple(window[-q:]))

    walk(tree, ())
    return grams


def tree_similarity(source_a: str, source_b: str) -> float:
    try:
        tree_a, tree_b = ast.parse(source_a), ast.parse(source_b)
    except SyntaxError:
        return 0.0
    a, b = pq_grams(tree_a), pq_grams(tree_b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


ALGORITHM_SIGNATURES: dict[str, dict] = {
    "binary_search": {
        "requires_loop_or_recursion": True,
        "tokens": ("//", "mid", "lo", "hi", "left", "right"),
        "min_branches": 2,
        "description": "halving search over a sorted sequence",
    },
    "divide_and_conquer_sort": {
        "requires_recursion": True,
        "tokens": ("merge", "sort", "mid", "half"),
        "min_branches": 1,
        "description": "recursive split-and-merge sort",
    },
    "quadratic_sort": {
        "min_loop_depth": 2,
        "tokens": ("swap", "temp", "range"),
        "description": "nested-loop exchange sort",
    },
    "linear_scan": {
        "min_loop_depth": 1,
        "max_loop_depth": 1,
        "description": "single pass over the input",
    },
    "hash_lookup": {
        "tokens": ("dict", "{}", "set(", "in "),
        "description": "constant-time membership or counting via a hash structure",
    },
    "dynamic_programming": {
        "min_loop_depth": 1,
        "tokens": ("dp", "memo", "cache", "table"),
        "description": "tabulated or memoised subproblem reuse",
    },
    "recursive_traversal": {
        "requires_recursion": True,
        "tokens": ("left", "right", "node", "child"),
        "description": "recursive descent over a tree structure",
    },
}


def _token_hits(tokens: tuple[str, ...], source: str) -> int:
    """Count signature tokens present as *words*, not substrings.

    Substring matching here was actively misleading: ``lo`` and ``hi`` occur
    inside dozens of ordinary identifiers, so a linked-list reversal would be
    reported to a student as a binary search. Evidence that a student can read
    has to be evidence that is true.
    """
    lowered = source.lower()
    hits = 0
    for token in tokens:
        if token.isalpha():
            if re.search(rf"\b{re.escape(token)}\b", lowered):
                hits += 1
        elif token in lowered:
            hits += 1
    return hits


def identify_algorithm(graph: CodeGraph, source: str) -> list[dict]:
    """Graph classification over the CFG, expressed as auditable rules.

    The production model is a GNN over CFG + AST node types trained on
    reference solutions and faculty tags. Until it has labels, these rules
    occupy the same interface and produce the same evidence shape, so swapping
    the model in changes nothing downstream.

    Structure alone is not enough to name an algorithm -- almost every
    algorithm here is "a loop with a branch in it" -- so the naming tokens are
    required rather than merely contributory. Without them the classifier
    returns nothing, which is the correct answer far more often than a guess.
    """
    max_loop_depth = max((fn.loop_depth for fn in graph.functions.values()), default=0)
    any_recursive = any(fn.is_recursive for fn in graph.functions.values())
    total_branches = sum(fn.branch_count for fn in graph.functions.values())

    matches: list[dict] = []
    for name, signature in ALGORITHM_SIGNATURES.items():
        tokens = signature.get("tokens", ())
        token_score = 1.0
        if tokens:
            hits = _token_hits(tokens, source)
            if hits == 0:
                continue        # no naming evidence at all: do not guess
            token_score = min(1.0, hits / max(1.0, len(tokens) / 2))

        score = 0.0
        possible = 0.0
        if signature.get("requires_recursion"):
            possible += 1
            score += 1 if any_recursive else 0
        if signature.get("requires_loop_or_recursion"):
            possible += 1
            score += 1 if (any_recursive or max_loop_depth >= 1) else 0
        if "min_loop_depth" in signature:
            possible += 1
            score += 1 if max_loop_depth >= signature["min_loop_depth"] else 0
        if "max_loop_depth" in signature:
            possible += 1
            score += 1 if max_loop_depth <= signature["max_loop_depth"] else 0
        if "min_branches" in signature:
            possible += 1
            score += 1 if total_branches >= signature["min_branches"] else 0
        if tokens:
            possible += 1
            score += token_score

        confidence = score / possible if possible else 0.0
        if confidence >= 0.6:
            matches.append(
                {
                    "algorithm": name,
                    "confidence": round(confidence, 3),
                    "description": signature["description"],
                }
            )
    matches.sort(key=lambda m: m["confidence"], reverse=True)
    return matches[:3]


@dataclass
class StructuralCredit:
    similarity_to_reference: float
    algorithm_matches: list[dict]
    matched_expected_algorithm: bool
    fraction: float
    evidence: list[str] = field(default_factory=list)


def structural_credit(
    student_source: str,
    graph: CodeGraph,
    reference_source: str,
    expected_algorithm: str | None = None,
    variant_bank: list[str] | None = None,
) -> StructuralCredit:
    """Algorithm-comprehension credit from structure rather than output.

    With a reference solution the strongest signal is how close the submission's
    tree is to it. **Without one there is nothing to be similar to**, and
    reporting 0% similarity would be a statement about the assignment rather
    than about the student -- it would silently mark down every submission to an
    approach-graded assignment. So in that case the credit comes from what can
    still be established: whether a coherent algorithm class is identifiable at
    all, and whether it is the one the brief asked for.
    """
    has_reference = bool((reference_source or "").strip())
    similarity = 0.0
    if has_reference:
        similarity = tree_similarity(student_source, reference_source)
        for variant in variant_bank or []:
            similarity = max(similarity, tree_similarity(student_source, variant))

    matches = identify_algorithm(graph, student_source)
    matched = bool(expected_algorithm) and any(m["algorithm"] == expected_algorithm for m in matches)
    top = matches[0] if matches else None

    evidence: list[str] = []
    if has_reference:
        evidence.append(
            f"Structural similarity to the reference solution: {similarity:.0%} "
            "(pq-gram profile over the AST)."
        )
    else:
        evidence.append(
            "No reference solution exists for this assignment, so structure is judged on its own "
            "terms rather than against a model answer."
        )
    if top:
        evidence.append(
            f"Algorithm identified as {top['algorithm'].replace('_', ' ')} ({top['description']}) "
            f"with confidence {top['confidence']:.0%}."
        )
    else:
        evidence.append(
            "No algorithm class could be identified from the control-flow graph with enough "
            "confidence to name one."
        )
    if expected_algorithm and matched:
        evidence.append(
            f"This is the required approach ({expected_algorithm.replace('_', ' ')}), so "
            "algorithm-comprehension credit is awarded independently of the test outcome."
        )
    elif expected_algorithm:
        evidence.append(
            f"The required approach ({expected_algorithm.replace('_', ' ')}) was not identified."
        )

    if matched:
        fraction = 0.85
    elif has_reference:
        fraction = min(0.7, similarity * 1.1)
    elif top:
        # Approach-graded: a confidently-identified, coherent algorithm is the
        # evidence available, and it is real evidence.
        fraction = min(0.8, 0.45 + 0.45 * top["confidence"])
    else:
        # Structure that resists classification is not a failure by itself; the
        # named checks in the rubric carry the weight, so this abstains near the
        # middle rather than voting the item down.
        fraction = 0.4 if graph.parsed and graph.functions else 0.15

    return StructuralCredit(similarity, matches, matched, round(fraction, 4), evidence)


# ==========================================================================
# B5c - static rubric checks
# ==========================================================================
def _resolve_scope(graph: CodeGraph, scope: str | None):
    if scope and scope in graph.functions:
        return [graph.functions[scope]]
    return list(graph.functions.values())


def check_guard_present(graph: CodeGraph, spec: dict) -> tuple[bool, str]:
    """Def-use query: is there a guard on the named input path?"""
    target = (spec.get("target") or "").lower()
    for fn in _resolve_scope(graph, spec.get("scope")):
        for guard in fn.guards:
            blob = " ".join(str(v).lower() for v in guard.values())
            if target in blob or (target in ("input_length", "length") and "len(" in blob):
                return True, (
                    f"Guard found at {fn.file}:{guard.get('lineno')} in {fn.name}() - "
                    f"{guard.get('kind')} on {guard.get('target')}."
                )
        if target in ("input_length", "length"):
            for name in fn.params:
                if any(name in str(g.get("target", "")) for g in fn.guards):
                    return True, f"Guard on parameter {name} found in {fn.name}()."
    return False, (
        f"No guard on the {spec.get('target')} path was found on any control-flow path "
        "reaching the computation."
    )


def check_recursion_present(graph: CodeGraph, spec: dict) -> tuple[bool, str]:
    """Call-graph cycle detection, so mutual recursion counts too."""
    for fn in _resolve_scope(graph, spec.get("scope")):
        if fn.is_recursive:
            return True, f"{fn.name}() participates in a call-graph cycle (recursion detected)."
    return False, "No recursive call path found in the call graph."


def check_api_called(graph: CodeGraph, spec: dict) -> tuple[bool, str]:
    """Symbol resolution: was the required API actually invoked?"""
    required = spec.get("target") or spec.get("symbol") or ""
    for fn in graph.functions.values():
        for call in fn.calls:
            if call == required or call.endswith(f".{required}"):
                return True, f"{required} is called from {fn.name}() at {fn.file}:{fn.lineno}."
    for modules in graph.imports.values():
        if required in modules:
            return True, f"{required} is imported and available."
    return False, f"{required} does not appear anywhere in the resolved call graph."


def check_api_absent(graph: CodeGraph, spec: dict) -> tuple[bool, str]:
    """The inverse: a rubric item that forbids a shortcut (``sorted`` on a
    sorting exercise, for example)."""
    forbidden = spec.get("target") or ""
    present, detail = check_api_called(graph, {"target": forbidden})
    if present:
        return False, f"Forbidden API used: {detail}"
    return True, f"{forbidden} is not used, as required."


def check_loop_nesting(graph: CodeGraph, spec: dict) -> tuple[bool, str]:
    """Complexity in the expected class. Advisory by construction: loop nesting
    is a heuristic for asymptotic behaviour, not a proof of it."""
    maximum = spec.get("max_depth", 1)
    worst = max((fn.loop_depth for fn in _resolve_scope(graph, spec.get("scope"))), default=0)
    if worst <= maximum:
        return True, f"Maximum loop nesting is {worst}, within the expected depth of {maximum} (advisory)."
    return False, (
        f"Maximum loop nesting is {worst}, above the expected depth of {maximum}. "
        "Advisory only - nesting is a heuristic for complexity, not a proof."
    )


def check_error_handling(graph: CodeGraph, spec: dict) -> tuple[bool, str]:
    for fn in _resolve_scope(graph, spec.get("scope")):
        if fn.has_try:
            return True, f"Exception handling present in {fn.name}()."
    return False, "No exception handling found on any function in the code graph."


def check_function_defined(graph: CodeGraph, spec: dict) -> tuple[bool, str]:
    name = spec.get("target") or ""
    fn = graph.functions.get(name)
    if fn is None:
        return False, f"Required function {name}() is not defined."
    arity = spec.get("arity")
    if arity is not None and len(fn.params) != arity:
        return False, f"{name}() is defined with {len(fn.params)} parameter(s); {arity} required."
    return True, f"{name}() is defined at {fn.file}:{fn.lineno} with parameters {fn.params}."


def check_no_global_state(graph: CodeGraph, spec: dict) -> tuple[bool, str]:
    offenders = [fn.name for fn in graph.functions.values() if "global" in fn.node_type_histogram]
    if offenders:
        return False, f"Global state mutated in: {', '.join(offenders)}."
    return True, "No global-state mutation found."


def check_class_defined(graph: CodeGraph, spec: dict) -> tuple[bool, str]:
    name = spec.get("target") or ""
    if name and name in graph.classes:
        methods = graph.classes[name]
        return True, f"class {name} is defined with method(s) {methods or '(none)'}."
    if not name and graph.classes:
        return True, f"Class(es) defined: {', '.join(sorted(graph.classes))}."
    return False, f"Required class {name or '(any)'} is not defined."


def check_algorithm_class(graph: CodeGraph, spec: dict) -> tuple[bool, str]:
    """Does the submission implement the algorithm class the brief asked for?

    This is the check that carries most of the weight when there is no reference
    solution to test against: the question stops being "does it produce the
    right output" and becomes "is this the approach the exercise was about".
    """
    expected = spec.get("target") or spec.get("algorithm") or ""
    source = spec.get("_source", "")
    matches = identify_algorithm(graph, source)
    if not matches:
        return False, (
            "No algorithm class could be identified with sufficient confidence from the control-flow "
            "graph."
        )
    names = ", ".join(f"{m['algorithm']} ({m['confidence']:.0%})" for m in matches)
    if not expected:
        return True, f"Algorithm class identified: {names}."
    if any(m["algorithm"] == expected for m in matches):
        return True, f"The required approach ({expected}) was identified. Also matched: {names}."
    return False, f"The required approach ({expected}) was not identified. Identified instead: {names}."


def check_uses_iteration(graph: CodeGraph, spec: dict) -> tuple[bool, str]:
    for fn in _resolve_scope(graph, spec.get("scope")):
        if fn.loop_depth > 0:
            return True, f"{fn.name}() iterates (maximum nesting depth {fn.loop_depth})."
    return False, "No loop appears in any function in the code graph."


def check_min_functions(graph: CodeGraph, spec: dict) -> tuple[bool, str]:
    """Decomposition: did the student break the problem up at all?"""
    minimum = int(spec.get("min", 2))
    count = len(graph.functions)
    if count >= minimum:
        return True, f"The submission defines {count} function(s), meeting the minimum of {minimum}."
    return False, (
        f"The submission defines {count} function(s); the brief expects the problem to be "
        f"decomposed into at least {minimum}."
    )


def check_documented(graph: CodeGraph, spec: dict) -> tuple[bool, str]:
    ratio = graph.comment_lines / graph.loc if graph.loc else 0.0
    minimum = spec.get("min_ratio", 0.05)
    if ratio >= minimum:
        return True, f"Comment density {ratio:.1%} meets the {minimum:.0%} minimum."
    return False, f"Comment density {ratio:.1%} is below the {minimum:.0%} minimum."


STATIC_CHECKS = {
    "guard_present": check_guard_present,
    "recursion_present": check_recursion_present,
    "api_called": check_api_called,
    "api_absent": check_api_absent,
    "loop_nesting": check_loop_nesting,
    "complexity_class": check_loop_nesting,
    "error_handling": check_error_handling,
    "function_defined": check_function_defined,
    "class_defined": check_class_defined,
    "algorithm_class": check_algorithm_class,
    "uses_iteration": check_uses_iteration,
    "min_functions": check_min_functions,
    "no_global_state": check_no_global_state,
    "documented": check_documented,
}

#: Checks that are heuristics rather than proofs. They participate as evidence
#: at reduced reliability; they never decide an item on their own.
ADVISORY_CHECKS = frozenset({"loop_nesting", "complexity_class", "algorithm_class"})


@dataclass
class StaticCheckResult:
    kind: str
    passed: bool
    detail: str
    advisory: bool = False


def run_static_check(graph: CodeGraph, spec: dict, source: str = "") -> StaticCheckResult:
    kind = spec.get("kind", "")
    checker = STATIC_CHECKS.get(kind)
    if checker is None:
        return StaticCheckResult(kind, False, f"Unknown static check kind {kind!r} (authoring error).", advisory=True)
    try:
        # A couple of checks need the raw source as well as the graph; passing
        # it through the spec keeps the checker signature uniform.
        passed, detail = checker(graph, {**spec, "_source": source})
    except Exception as exc:  # noqa: BLE001 - a broken check must not fail the run
        return StaticCheckResult(kind, False, f"Static check errored: {type(exc).__name__}: {exc}", advisory=True)
    return StaticCheckResult(kind, passed, detail, advisory=kind in ADVISORY_CHECKS)


def rebuild_graph(files: dict[str, str], entry: str | None) -> CodeGraph:
    return build_code_graph(files, entry)
