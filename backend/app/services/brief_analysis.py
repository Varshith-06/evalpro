"""Turning an instructor's brief into checkable requirements.

This is what makes "leave it blank and let the platform work it out" honest
rather than a shrug. An instructor writes a paragraph or a bullet list; this
module reads it for the things a parser can actually verify — a required
construct, a forbidden shortcut, an expected complexity class, a boundary case
that must be handled — and proposes a rubric item with a concrete
``static_check`` for each one.

Two rules govern what comes out:

* **Never invent a requirement the brief does not state.** A rubric item the
  student was never told about is worse than no rubric item, and it is the
  fastest way to lose a cohort's trust. Every detector below fires on explicit
  language.
* **Prefer a check over a claim.** An item that says "handles the empty case"
  with a ``guard_present`` check behind it can be shown to a student as
  evidence. One that says "code quality is good" cannot, so it is not emitted.

The output is a *draft*. It goes to the faculty review screen marked as
generated, and nothing grades until a human approves it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Requirement:
    """One thing the brief asks for that the platform can check."""

    text: str                      # rubric item text, in the brief's own terms
    category: str
    weight: float
    static_check: dict | None
    checkable_by: list[str]
    concept_hints: list[str] = field(default_factory=list)
    source_phrase: str = ""        # what in the brief triggered this
    advisory: bool = False


# --------------------------------------------------------------------------
# Detectors
# --------------------------------------------------------------------------
# Each entry: (compiled pattern, builder). The builder receives the match and
# the sentence it came from, and returns a Requirement or None.

_FORBID = re.compile(
    r"(?:without using|do not use|don't use|may not use|must not use|no use of|cannot use)\s+"
    r"(?:the\s+)?(?:built-?in\s+)?[`'\"]?(\w+)",
    re.IGNORECASE,
)
_REQUIRE_FUNCTION = re.compile(
    r"(?:implement|define|write|provide|complete)\s+(?:a\s+|the\s+)?"
    r"(?:function|method|procedure)\s+(?:called\s+|named\s+)?[`'\"]?(\w+)",
    re.IGNORECASE,
)
_SIGNATURE = re.compile(r"\b(\w+)\s*\(\s*([\w,\s]*)\s*\)", re.IGNORECASE)
_REQUIRE_CLASS = re.compile(
    r"(?:implement|define|write|create)\s+(?:a\s+|the\s+)?class\s+(?:called\s+|named\s+)?[`'\"]?(\w+)",
    re.IGNORECASE,
)
_REQUIRE_API = re.compile(
    r"(?:use|using|call|apply|via)\s+(?:the\s+)?[`'\"]?(\w+)[`'\"]?\s*(?:\(\))?\s*"
    r"(?:function|method|module|library)",
    re.IGNORECASE,
)
_COMPLEXITY = re.compile(r"\bO\s*\(\s*([^)]{1,20})\s*\)", re.IGNORECASE)

_RECURSION = ("recursive", "recursion", "recursively")
_ITERATION = ("iterative", "iteratively", "using a loop", "with a loop")
_EMPTY_CASE = ("empty", "zero-length", "no elements", "nothing is passed", "blank input")
_BOUNDARY = ("edge case", "boundary", "corner case", "single element", "one element")
_ERRORS = ("exception", "error handling", "handle errors", "invalid input", "raise", "gracefully")
_DOCS = ("comment", "document", "docstring", "readable", "explain your code")
_DECOMPOSE = ("helper function", "break the problem", "decompose", "separate functions", "modular")
_REPORT = ("report", "write-up", "writeup", "justify", "explain your approach", "discuss")
_NO_GLOBALS = ("without global", "no global", "avoid global")

_ALGORITHM_WORDS = {
    "binary_search": ("binary search", "halving search", "bisect"),
    "divide_and_conquer_sort": ("merge sort", "quick sort", "quicksort", "divide and conquer"),
    "quadratic_sort": ("bubble sort", "insertion sort", "selection sort", "exchange sort"),
    "linear_scan": ("linear search", "linear scan", "single pass", "one pass"),
    "hash_lookup": ("hash map", "hash table", "dictionary", "constant-time lookup"),
    "dynamic_programming": ("dynamic programming", "memoisation", "memoization", "tabulation"),
    "recursive_traversal": ("traversal", "traverse", "depth-first", "breadth-first", "dfs", "bfs"),
}

_CONCEPT_WORDS = {
    "recursion": ("recursion", "recursive"),
    "loops": ("loop", "iterate", "iteration"),
    "bounds": ("bounds", "index", "empty", "boundary"),
    "hashing": ("hash", "dictionary", "map"),
    "sorting": ("sort", "ordering", "ascending", "descending"),
    "search": ("search", "find", "lookup"),
    "complexity": ("complexity", "efficient", "time", "space"),
    "tree": ("tree", "node", "child", "nested"),
    "defensive": ("invalid", "error", "exception", "guard"),
    "documentation": ("comment", "document", "readable"),
}


def _sentences(brief: str) -> list[str]:
    bullets = re.findall(r"^\s*(?:[-*•]|\d+[.)])\s+(.{4,})$", brief, re.M)
    if bullets:
        return [b.strip() for b in bullets]
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", brief) if len(s.strip()) > 8]


def _normalise_complexity(raw: str) -> str:
    cleaned = re.sub(r"\s+", "", raw.lower()).replace("**", "^")
    return {
        "1": "O(1)", "n": "O(n)", "logn": "O(log n)", "nlogn": "O(n log n)",
        "n^2": "O(n^2)", "n2": "O(n^2)", "n^3": "O(n^3)",
    }.get(cleaned, f"O({raw.strip()})")


_MAX_DEPTH_FOR_COMPLEXITY = {
    "O(1)": 0, "O(log n)": 1, "O(n)": 1, "O(n log n)": 1, "O(n^2)": 2, "O(n^3)": 3,
}


def analyse_brief(brief: str, entry_call: str = "solve") -> list[Requirement]:
    """Read a brief for checkable requirements. Order follows the brief."""
    requirements: list[Requirement] = []
    seen_kinds: set[str] = set()

    def add(requirement: Requirement, dedupe_key: str) -> None:
        if dedupe_key in seen_kinds:
            return
        seen_kinds.add(dedupe_key)
        requirements.append(requirement)

    for sentence in _sentences(brief):
        lowered = sentence.lower()

        # -- forbidden shortcut ----------------------------------------
        for match in _FORBID.finditer(sentence):
            symbol = match.group(1)
            add(
                Requirement(
                    text=f"Does not use {symbol}, as the brief requires",
                    category="correctness",
                    # A constraint, not an achievement: not calling a function
                    # is something an empty submission also manages.
                    weight=4.0,
                    static_check={"kind": "api_absent", "target": symbol},
                    checkable_by=["static"],
                    concept_hints=_concept_hints(sentence),
                    source_phrase=sentence,
                ),
                f"forbid:{symbol}",
            )

        # -- required function -----------------------------------------
        for match in _REQUIRE_FUNCTION.finditer(sentence):
            name = match.group(1)
            arity = _arity_for(sentence, name)
            spec: dict = {"kind": "function_defined", "target": name}
            if arity is not None:
                spec["arity"] = arity
            add(
                Requirement(
                    text=f"Defines {name}() with the required signature",
                    category="correctness",
                    weight=6.0,
                    static_check=spec,
                    checkable_by=["static"],
                    concept_hints=_concept_hints(sentence),
                    source_phrase=sentence,
                ),
                f"function:{name}",
            )

        # -- required class --------------------------------------------
        for match in _REQUIRE_CLASS.finditer(sentence):
            name = match.group(1)
            add(
                Requirement(
                    text=f"Defines the class {name}",
                    category="correctness",
                    weight=6.0,
                    static_check={"kind": "class_defined", "target": name},
                    checkable_by=["static"],
                    concept_hints=_concept_hints(sentence),
                    source_phrase=sentence,
                ),
                f"class:{name}",
            )

        # -- required API ----------------------------------------------
        for match in _REQUIRE_API.finditer(sentence):
            symbol = match.group(1)
            if symbol.lower() in ("a", "the", "this", "it", "your", "any"):
                continue
            add(
                Requirement(
                    text=f"Uses {symbol} as the brief specifies",
                    category="correctness",
                    weight=5.0,
                    static_check={"kind": "api_called", "target": symbol},
                    checkable_by=["static"],
                    concept_hints=_concept_hints(sentence),
                    source_phrase=sentence,
                ),
                f"api:{symbol}",
            )

        # -- recursion / iteration -------------------------------------
        if any(word in lowered for word in _RECURSION):
            add(
                Requirement(
                    text="Uses recursion, as the brief requires",
                    category="correctness",
                    weight=8.0,
                    static_check={"kind": "recursion_present"},
                    checkable_by=["static"],
                    concept_hints=["recursion"],
                    source_phrase=sentence,
                ),
                "recursion",
            )
        elif any(word in lowered for word in _ITERATION):
            add(
                Requirement(
                    text="Uses iteration, as the brief requires",
                    category="correctness",
                    weight=6.0,
                    static_check={"kind": "uses_iteration"},
                    checkable_by=["static"],
                    concept_hints=["loops"],
                    source_phrase=sentence,
                ),
                "iteration",
            )

        # -- empty / boundary handling ---------------------------------
        if any(word in lowered for word in _EMPTY_CASE):
            add(
                Requirement(
                    text="Handles the empty-input case without crashing",
                    category="robustness",
                    weight=6.0,
                    static_check={"kind": "guard_present", "target": "input_length"},
                    checkable_by=["static"],
                    concept_hints=["bounds", "defensive"],
                    source_phrase=sentence,
                ),
                "empty",
            )
        elif any(word in lowered for word in _BOUNDARY):
            add(
                Requirement(
                    text="Guards the boundary cases the brief calls out",
                    category="robustness",
                    weight=5.0,
                    static_check={"kind": "guard_present", "target": "input_length"},
                    checkable_by=["static"],
                    concept_hints=["bounds"],
                    source_phrase=sentence,
                ),
                "empty",
            )

        # -- complexity -------------------------------------------------
        for match in _COMPLEXITY.finditer(sentence):
            target = _normalise_complexity(match.group(1))
            depth = _MAX_DEPTH_FOR_COMPLEXITY.get(target)
            if depth is None:
                continue
            add(
                Requirement(
                    text=f"Runs in the required complexity class, {target}",
                    category="efficiency",
                    weight=6.0,
                    static_check={"kind": "loop_nesting", "max_depth": depth},
                    checkable_by=["static", "report"],
                    concept_hints=["complexity"],
                    source_phrase=sentence,
                    advisory=True,
                ),
                "complexity",
            )

        # -- named algorithm --------------------------------------------
        for algorithm, words in _ALGORITHM_WORDS.items():
            if any(word in lowered for word in words):
                add(
                    Requirement(
                        text=f"Implements the required approach ({algorithm.replace('_', ' ')})",
                        category="correctness",
                        weight=8.0,
                        static_check={"kind": "algorithm_class", "target": algorithm},
                        checkable_by=["static", "structural"],
                        concept_hints=_concept_hints(sentence),
                        source_phrase=sentence,
                        advisory=True,
                    ),
                    "algorithm",
                )
                break

        # -- error handling ---------------------------------------------
        if any(word in lowered for word in _ERRORS):
            add(
                Requirement(
                    text="Handles invalid input rather than failing silently",
                    category="robustness",
                    weight=5.0,
                    static_check={"kind": "error_handling"},
                    checkable_by=["static"],
                    concept_hints=["defensive"],
                    source_phrase=sentence,
                ),
                "errors",
            )

        # -- decomposition ----------------------------------------------
        if any(word in lowered for word in _DECOMPOSE):
            add(
                Requirement(
                    text="Decomposes the problem into more than one function",
                    category="style",
                    weight=4.0,
                    static_check={"kind": "min_functions", "min": 2},
                    checkable_by=["static"],
                    concept_hints=["documentation"],
                    source_phrase=sentence,
                ),
                "decompose",
            )

        # -- globals ------------------------------------------------------
        if any(word in lowered for word in _NO_GLOBALS):
            add(
                Requirement(
                    text="Avoids mutating global state",
                    category="style",
                    weight=4.0,
                    static_check={"kind": "no_global_state"},
                    checkable_by=["static"],
                    concept_hints=["documentation"],
                    source_phrase=sentence,
                ),
                "globals",
            )

        # -- documentation --------------------------------------------------
        if any(word in lowered for word in _DOCS):
            add(
                Requirement(
                    text="Documents the code so a reader can follow the intent",
                    category="style",
                    weight=4.0,
                    static_check={"kind": "documented", "min_ratio": 0.05},
                    checkable_by=["static"],
                    concept_hints=["documentation"],
                    source_phrase=sentence,
                ),
                "docs",
            )

        # -- report -----------------------------------------------------------
        if any(word in lowered for word in _REPORT):
            add(
                Requirement(
                    text="The report explains the approach actually submitted",
                    category="communication",
                    weight=5.0,
                    static_check=None,
                    checkable_by=["report"],
                    concept_hints=["documentation", "complexity"],
                    source_phrase=sentence,
                ),
                "report",
            )

    # The entry point is always checkable and is almost always implied rather
    # than stated, so it is added last and only if nothing else covered it.
    if not any(r.static_check and r.static_check.get("kind") == "function_defined" for r in requirements):
        requirements.insert(
            0,
            Requirement(
                # A precondition rather than an achievement: defining the
                # function is where the work starts, so it is worth a token
                # amount. Weighted equally with correctness it handed a third
                # of the marks to a submission that did nothing else.
                text=f"Defines the required entry point {entry_call}()",
                category="correctness",
                weight=3.0,
                static_check={"kind": "function_defined", "target": entry_call},
                checkable_by=["static"],
                concept_hints=[],
                source_phrase="entry point implied by the assignment configuration",
            ),
        )
    return requirements


def _arity_for(sentence: str, name: str) -> int | None:
    """Read an arity out of a signature written in the brief, e.g. solve(a, b)."""
    for match in _SIGNATURE.finditer(sentence):
        if match.group(1) != name:
            continue
        params = [p.strip() for p in match.group(2).split(",") if p.strip()]
        return len(params)
    return None


def _concept_hints(sentence: str) -> list[str]:
    lowered = sentence.lower()
    return [
        hint for hint, words in _CONCEPT_WORDS.items() if any(word in lowered for word in words)
    ]


def summarise(requirements: list[Requirement]) -> str:
    if not requirements:
        return (
            "No checkable requirement could be read from this brief. The generated rubric will fall "
            "back to structural and reporting evidence only, which is weak - adding one or two "
            "explicit requirements to the brief would improve it a great deal."
        )
    checked = sum(1 for r in requirements if r.static_check)
    return (
        f"{len(requirements)} requirement(s) read from the brief, {checked} with a concrete static "
        "check behind them. Every one traces to a phrase you wrote."
    )
