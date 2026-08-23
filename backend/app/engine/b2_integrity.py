"""B2 - Integrity screen.

Cheap, so it runs before anything expensive. Two independent signals:

* **Winnowing fingerprints** (the MOSS algorithm) over an AST-normalised token
  stream. Normalisation strips comments and whitespace, renames identifiers to
  positional placeholders, and canonicalises literals, which defeats renaming
  and reformatting.
* **Structural clone detection** over the code graph, which catches
  control-flow-equivalent rewrites that token fingerprints miss.

Dead code is eliminated before either comparison, because padding a file with
unreachable helpers is the first thing anyone tries against AST similarity.

The output is a **ranked report with aligned code regions, never a verdict.**
Automated integrity accusations are a legal and ethical liability, and
machine-generated-code detectors are not reliable enough to carry consequences.
Surface the signal; faculty decide.
"""
from __future__ import annotations

import ast
import hashlib
import io
import re
import tokenize
from dataclasses import dataclass, field

from .b1_structure import CodeGraph

K_GRAM = 5          # noise threshold: shorter matches are coincidence
WINDOW = 4          # winnowing window; guarantees any match of length k+w-1
_HASH_MASK = (1 << 32) - 1

_KEYWORDS = frozenset(
    """False None True and as assert async await break class continue def del elif else
    except finally for from global if import in is lambda nonlocal not or pass raise
    return try while with yield match case""".split()
)
# Builtins are kept verbatim: renaming ``len`` to a placeholder would erase real
# structural signal, and no student renames them anyway.
_BUILTINS = frozenset(
    """len range print int str float list dict set tuple sorted sum min max abs enumerate
    zip map filter any all reversed round pow divmod isinstance type open""".split()
)


@dataclass
class Fingerprint:
    hashes: set[int] = field(default_factory=set)
    positions: dict[int, tuple[int, int]] = field(default_factory=dict)  # hash -> (start_line, end_line)
    token_count: int = 0

    def containment(self, other: Fingerprint) -> float:
        if not self.hashes:
            return 0.0
        return len(self.hashes & other.hashes) / len(self.hashes)

    def jaccard(self, other: Fingerprint) -> float:
        union = self.hashes | other.hashes
        if not union:
            return 0.0
        return len(self.hashes & other.hashes) / len(union)


# --------------------------------------------------------------------------
# Dead-code elimination
# --------------------------------------------------------------------------
def strip_dead_code(source: str, entry_symbols: set[str] | None = None) -> str:
    """Remove functions unreachable from the entry symbols, and statements
    after an unconditional ``return``.

    Padding with unreachable helpers is the standard attack on AST similarity;
    doing this first means the attacker has to actually rewrite the algorithm.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    defined = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if not defined:
        return source

    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called.add(func.id)
            elif isinstance(func, ast.Attribute):
                called.add(func.attr)

    roots = set(entry_symbols or set()) or {"solve", "main"}
    reachable: set[str] = set()
    frontier = [name for name in defined if name in roots or name in called]
    if not frontier:
        frontier = list(defined)
    while frontier:
        name = frontier.pop()
        if name in reachable or name not in defined:
            continue
        reachable.add(name)
        for sub in ast.walk(defined[name]):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                frontier.append(sub.func.id)

    kept: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name not in reachable:
                continue
            node.body = _truncate_after_return(node.body)
        kept.append(node)
    tree.body = kept
    try:
        return ast.unparse(tree)
    except Exception:  # pragma: no cover - unparse is total in practice
        return source


def _truncate_after_return(body: list[ast.stmt]) -> list[ast.stmt]:
    out: list[ast.stmt] = []
    for stmt in body:
        out.append(stmt)
        if isinstance(stmt, ast.Return):
            break
    return out


# --------------------------------------------------------------------------
# AST-normalised token stream
# --------------------------------------------------------------------------
def normalise_tokens(source: str) -> list[tuple[str, int]]:
    """Return ``(token, line)`` pairs with identity erased.

    Identifiers become positional placeholders in order of first appearance
    (``v0``, ``v1``, ...), numbers become ``NUM``, strings become ``STR``.
    Comments, docstrings, and layout vanish. Two submissions that differ only
    by naming and formatting produce byte-identical streams.
    """
    out: list[tuple[str, int]] = []
    mapping: dict[str, str] = {}
    try:
        stream = tokenize.generate_tokens(io.StringIO(source).readline)
        tokens = list(stream)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # Error tolerance again: fall back to a lexical scan so a broken file is
        # still comparable. An integrity screen that skips non-compiling code is
        # an integrity screen with a documented bypass.
        return _lexical_tokens(source)

    for tok in tokens:
        if tok.type in (
            tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
            tokenize.DEDENT, tokenize.ENCODING, tokenize.ENDMARKER,
        ):
            continue
        line = tok.start[0]
        if tok.type == tokenize.NAME:
            if tok.string in _KEYWORDS or tok.string in _BUILTINS:
                out.append((tok.string, line))
            else:
                placeholder = mapping.setdefault(tok.string, f"v{len(mapping)}")
                out.append((placeholder, line))
        elif tok.type == tokenize.NUMBER:
            out.append(("NUM", line))
        elif tok.type == tokenize.STRING:
            out.append(("STR", line))
        elif tok.type == tokenize.OP:
            out.append((tok.string, line))
    return out


_LEX_RE = re.compile(r"[A-Za-z_]\w*|\d+\.?\d*|[^\s\w]")


def _lexical_tokens(source: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    mapping: dict[str, str] = {}
    for line_no, line in enumerate(source.splitlines(), start=1):
        line = line.split("#", 1)[0]
        for match in _LEX_RE.finditer(line):
            text = match.group()
            if text[0].isdigit():
                out.append(("NUM", line_no))
            elif text[0].isalpha() or text[0] == "_":
                if text in _KEYWORDS or text in _BUILTINS:
                    out.append((text, line_no))
                else:
                    out.append((mapping.setdefault(text, f"v{len(mapping)}"), line_no))
            else:
                out.append((text, line_no))
    return out


# --------------------------------------------------------------------------
# Winnowing
# --------------------------------------------------------------------------
def _hash_gram(gram: tuple[str, ...]) -> int:
    digest = hashlib.blake2b("\x1f".join(gram).encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big") & _HASH_MASK


def fingerprint(source: str, entry_symbols: set[str] | None = None) -> Fingerprint:
    """Winnowed fingerprint of a normalised token stream.

    Winnowing selects, from every window of ``WINDOW`` consecutive k-gram
    hashes, the minimum -- ties broken by taking the rightmost. That guarantees
    any shared substring of length ``K_GRAM + WINDOW - 1`` is detected, while
    storing a small fraction of the hashes.
    """
    cleaned = strip_dead_code(source, entry_symbols)
    tokens = normalise_tokens(cleaned)
    fp = Fingerprint(token_count=len(tokens))
    if len(tokens) < K_GRAM:
        return fp

    grams: list[tuple[int, int, int]] = []
    for i in range(len(tokens) - K_GRAM + 1):
        window_tokens = tokens[i : i + K_GRAM]
        grams.append(
            (
                _hash_gram(tuple(t for t, _ in window_tokens)),
                window_tokens[0][1],
                window_tokens[-1][1],
            )
        )

    if len(grams) < WINDOW:
        for h, start, end in grams:
            fp.hashes.add(h)
            fp.positions.setdefault(h, (start, end))
        return fp

    previous_index = -1
    for i in range(len(grams) - WINDOW + 1):
        window = grams[i : i + WINDOW]
        min_value = min(w[0] for w in window)
        # Rightmost minimum, per the winnowing paper.
        offset = max(j for j, w in enumerate(window) if w[0] == min_value)
        absolute = i + offset
        if absolute == previous_index:
            continue
        previous_index = absolute
        h, start, end = grams[absolute]
        fp.hashes.add(h)
        fp.positions.setdefault(h, (start, end))
    return fp


# --------------------------------------------------------------------------
# Structural clone detection
# --------------------------------------------------------------------------
def structural_vector(graph: CodeGraph) -> dict[str, float]:
    """A small graph embedding: CFG shape aggregated over the code graph.

    Control-flow-equivalent rewrites -- rename everything, reorder functions,
    swap ``for`` for ``while`` at the source level but keep the same branch and
    loop skeleton -- leave this vector nearly unchanged while moving the token
    fingerprint a long way.
    """
    vector: dict[str, float] = {}
    for fn in graph.functions.values():
        vector["loop_depth"] = vector.get("loop_depth", 0.0) + fn.loop_depth
        vector["branches"] = vector.get("branches", 0.0) + fn.branch_count
        vector["returns"] = vector.get("returns", 0.0) + fn.returns
        vector["cyclomatic"] = vector.get("cyclomatic", 0.0) + fn.cyclomatic
        vector["params"] = vector.get("params", 0.0) + len(fn.params)
        vector["recursive"] = vector.get("recursive", 0.0) + (1.0 if fn.is_recursive else 0.0)
        vector["try"] = vector.get("try", 0.0) + (1.0 if fn.has_try else 0.0)
        for node_type, count in fn.node_type_histogram.items():
            vector[f"n:{node_type}"] = vector.get(f"n:{node_type}", 0.0) + count
    vector["functions"] = float(len(graph.functions))
    vector["classes"] = float(len(graph.classes))
    return vector


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    keys = set(a) | set(b)
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
    na = sum(v * v for v in a.values()) ** 0.5
    nb = sum(v * v for v in b.values()) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------
@dataclass
class SimilarityReport:
    token_similarity: float
    structural_similarity: float
    combined: float
    aligned_regions: list[dict]
    shared_hashes: int
    uninformative: bool = False

    def as_dict(self) -> dict:
        return {
            "token_similarity": round(self.token_similarity, 4),
            "structural_similarity": round(self.structural_similarity, 4),
            "combined": round(self.combined, 4),
            "shared_fingerprints": self.shared_hashes,
            "aligned_regions": self.aligned_regions,
            "uninformative": self.uninformative,
            "disclaimer": (
                "Similarity evidence only. This is not a determination of misconduct; "
                "aligned regions are provided so a human can judge."
            ),
        }


#: Below this many *distinctive* fingerprints, Jaccard is noise: two documents
#: sharing one of the two hashes each has left scores 1.0, which is how a
#: similarity screen manufactures an accusation out of boilerplate.
MIN_INFORMATIVE_HASHES = 8


def compare(
    source_a: str,
    graph_a: CodeGraph,
    source_b: str,
    graph_b: CodeGraph,
    fp_a: Fingerprint | None = None,
    fp_b: Fingerprint | None = None,
    excluded_hashes: set[int] | None = None,
) -> SimilarityReport:
    fp_a = fp_a or fingerprint(source_a)
    fp_b = fp_b or fingerprint(source_b)
    excluded = excluded_hashes or set()
    if excluded:
        fp_a = _without(fp_a, excluded)
        fp_b = _without(fp_b, excluded)
        if min(len(fp_a.hashes), len(fp_b.hashes)) < MIN_INFORMATIVE_HASHES:
            # Once base code and common idioms are removed there is not enough
            # distinctive content left to compare. The honest answer is "no
            # signal", not a number. For a short exercise where nearly every
            # line is boilerplate, similarity detection genuinely cannot
            # discriminate, and saying so is the whole point.
            return SimilarityReport(
                token_similarity=0.0,
                structural_similarity=cosine(structural_vector(graph_a), structural_vector(graph_b)),
                combined=0.0,
                aligned_regions=[],
                shared_hashes=0,
                uninformative=True,
            )
    shared = fp_a.hashes & fp_b.hashes
    token_sim = fp_a.jaccard(fp_b)
    struct_sim = cosine(structural_vector(graph_a), structural_vector(graph_b))

    lines_a = source_a.splitlines()
    lines_b = source_b.splitlines()
    regions: list[dict] = []
    for h in sorted(shared)[:40]:
        start_a, end_a = fp_a.positions.get(h, (0, 0))
        start_b, end_b = fp_b.positions.get(h, (0, 0))
        regions.append(
            {
                "a": {
                    "start_line": start_a,
                    "end_line": end_a,
                    "excerpt": "\n".join(lines_a[max(0, start_a - 1) : end_a])[:400],
                },
                "b": {
                    "start_line": start_b,
                    "end_line": end_b,
                    "excerpt": "\n".join(lines_b[max(0, start_b - 1) : end_b])[:400],
                },
            }
        )
    regions = _merge_regions(regions)

    # Token similarity is the calibrated signal and sets the baseline.
    # Structural similarity deliberately **cannot raise a report on its own**:
    # near-identical CFG shape is the norm when forty people implement the same
    # exercise from the same lecture, so averaging the two would flag the whole
    # cohort. Instead it amplifies a report that already has token evidence,
    # which is exactly the control-flow-preserving rewrite it exists to catch.
    combined = token_sim + (1.0 - token_sim) * token_sim * struct_sim
    return SimilarityReport(token_sim, struct_sim, combined, regions, len(shared))


def _merge_regions(regions: list[dict]) -> list[dict]:
    """Adjacent fingerprint hits are one plagiarised block, not fifteen."""
    if not regions:
        return []
    ordered = sorted(regions, key=lambda r: (r["a"]["start_line"], r["b"]["start_line"]))
    merged = [ordered[0]]
    for region in ordered[1:]:
        last = merged[-1]
        if (
            region["a"]["start_line"] - last["a"]["end_line"] <= 2
            and region["b"]["start_line"] - last["b"]["end_line"] <= 2
        ):
            last["a"]["end_line"] = max(last["a"]["end_line"], region["a"]["end_line"])
            last["b"]["end_line"] = max(last["b"]["end_line"], region["b"]["end_line"])
        else:
            merged.append(region)
    return merged[:12]


def _without(fp: Fingerprint, excluded: set[int]) -> Fingerprint:
    kept = fp.hashes - excluded
    return Fingerprint(
        hashes=kept,
        positions={h: pos for h, pos in fp.positions.items() if h in kept},
        token_count=fp.token_count,
    )


COMMON_IDIOM_SHARE = 0.40


def excluded_fingerprints(
    reference_sources: list[str],
    corpus: list[dict],
    common_share: float = COMMON_IDIOM_SHARE,
) -> set[int]:
    """Base code and common idioms, removed before any comparison.

    Two false-positive sources dominate a naive similarity screen, and both are
    the platform's fault rather than the students':

    * **Base code.** Anything the instructor handed out -- a template, a
      signature, the reference solution's boilerplate -- is shared by everyone
      by construction. MOSS calls this the base-code option; it is not optional
      here.
    * **Common idioms.** When forty students implement selection sort, the
      inner comparison loop is genuinely near-identical, because there are only
      so many ways to write it. A fingerprint appearing in more than
      ``common_share`` of the cohort is evidence about the exercise, not about
      any student.

    Getting this wrong is how an integrity screen becomes an accusation
    generator, and the false-flag rate is the highest-consequence error the
    platform can make.
    """
    excluded: set[int] = set()
    for source in reference_sources:
        excluded |= fingerprint(source).hashes

    if not corpus:
        return excluded

    counts: dict[int, int] = {}
    for entry in corpus:
        for h in fingerprint(entry["source"]).hashes:
            counts[h] = counts.get(h, 0) + 1
    threshold = max(3, int(len(corpus) * common_share))
    excluded |= {h for h, count in counts.items() if count >= threshold}
    return excluded


@dataclass
class CorpusScreen:
    """The result of screening one submission against a comparison corpus."""

    ranked: list[dict] = field(default_factory=list)
    top: float = 0.0
    median: float = 0.0
    p90: float = 0.0
    z_score: float = 0.0
    outlier: bool = False
    flag_score: float = 0.0
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "ranked": self.ranked[:5],
            "top_similarity": round(self.top, 4),
            "cohort_median_similarity": round(self.median, 4),
            "cohort_p90_similarity": round(self.p90, 4),
            "z_score": round(self.z_score, 3),
            "outlier": self.outlier,
            "flag_score": round(self.flag_score, 4),
            "note": self.note,
        }


#: An absolute floor below which nothing is worth a human's attention, however
#: unusual it looks against the cohort.
ABSOLUTE_FLOOR = 0.55
#: How far above the cohort's own similarity distribution a pair must sit.
OUTLIER_Z = 2.5


def screen_against_corpus(
    source: str,
    graph: CodeGraph,
    corpus: list[dict],
    excluded_hashes: set[int] | None = None,
) -> CorpusScreen:
    """Rank a submission against a corpus, and flag only genuine outliers.

    ``corpus`` entries are ``{id, student_id, source, graph, corpus}`` covering
    this cohort, prior years, and a scraped public-solution index. All three
    matter: cohort-only screening misses the student who bought a solution.

    The flag is **relative to the cohort's own similarity distribution**, not to
    a fixed number. When an exercise is small and canonical, every pair in the
    class scores highly and an absolute threshold flags everyone -- which is
    precisely how an integrity screen becomes an accusation generator. What is
    actually interesting is a pair that stands out *against that background*, so
    a report needs both an absolute floor and a real outlier z-score.
    """
    fp = fingerprint(source)
    ranked: list[dict] = []
    for entry in corpus:
        report = compare(
            source, graph, entry["source"], entry["graph"], fp_a=fp,
            excluded_hashes=excluded_hashes,
        )
        if report.combined <= 0.0:
            continue
        ranked.append(
            {
                "against_id": entry["id"],
                "against_student_id": entry.get("student_id"),
                "corpus": entry.get("corpus", "cohort"),
                **report.as_dict(),
            }
        )
    ranked.sort(key=lambda r: r["combined"], reverse=True)

    scores = [r["combined"] for r in ranked]
    if not scores:
        return CorpusScreen(
            note="No comparison produced a usable signal: after removing base code and shared "
            "idioms there was not enough distinctive content to compare."
        )

    top = scores[0]
    ordered = sorted(scores)
    median = ordered[len(ordered) // 2]
    p90 = ordered[int(len(ordered) * 0.9)] if len(ordered) > 1 else top
    if len(scores) >= 4:
        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / (len(scores) - 1)
        std = variance ** 0.5
        z = (top - mean) / std if std > 1e-6 else 0.0
    else:
        z = 0.0

    outlier = top >= ABSOLUTE_FLOOR and (z >= OUTLIER_Z or len(scores) < 4)
    flag_score = top if outlier else min(top, ABSOLUTE_FLOOR - 0.05)

    if outlier:
        note = (
            f"Top match {top:.0%} sits {z:.1f} standard deviations above this cohort's own similarity "
            f"distribution (median {median:.0%}). That is what makes it worth a human minute."
        )
    elif top >= ABSOLUTE_FLOOR:
        note = (
            f"Top match {top:.0%}, but the cohort median is already {median:.0%}: this exercise produces "
            "similar solutions by its nature, so this pair does not stand out and is not reported."
        )
    else:
        note = f"Top match {top:.0%}, below the {ABSOLUTE_FLOOR:.0%} floor for human review."

    return CorpusScreen(ranked[:20], top, median, p90, z, outlier, flag_score, note)
