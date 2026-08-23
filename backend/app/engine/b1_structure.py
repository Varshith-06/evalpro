"""B1 - Structural analysis. Builds the code graph.

The spec calls for tree-sitter, and for a multi-language deployment that is the
right dependency: one grammar collection, 40+ languages, and crucially
*error-tolerant* parsing, which is what makes partial credit on non-compiling
code possible at all.

This implementation uses Python's own ``ast`` for the exact tree and adds a
**lexical recovery parser** for the case ``ast`` refuses -- so the same
property holds: a file that will not compile still yields a partial tree,
symbols, imports, and enough structure for B5 to award credit. The
``CodeGraph`` shape is deliberately language-agnostic so a tree-sitter backend
can be dropped in behind ``build_code_graph`` without touching anything
downstream.

Everything after this stage reads the graph, not the directory tree. File
layout stops mattering here.
"""
from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass, field


@dataclass
class FunctionNode:
    name: str
    file: str
    lineno: int
    end_lineno: int
    params: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    is_recursive: bool = False
    loop_depth: int = 0
    branch_count: int = 0
    returns: int = 0
    has_try: bool = False
    cfg_nodes: int = 0
    cfg_edges: int = 0
    cyclomatic: int = 1
    defs: list[str] = field(default_factory=list)
    uses: list[str] = field(default_factory=list)
    guards: list[dict] = field(default_factory=list)
    node_type_histogram: dict[str, int] = field(default_factory=dict)


@dataclass
class CodeGraph:
    language: str = "python"
    parsed: bool = False
    partial: bool = False
    syntax_errors: list[dict] = field(default_factory=list)
    entry_point: str | None = None
    files: list[str] = field(default_factory=list)
    imports: dict[str, list[str]] = field(default_factory=dict)
    symbols: dict[str, dict] = field(default_factory=dict)
    functions: dict[str, FunctionNode] = field(default_factory=dict)
    call_graph: dict[str, list[str]] = field(default_factory=dict)
    classes: dict[str, list[str]] = field(default_factory=dict)
    data_structures: list[str] = field(default_factory=list)
    loc: int = 0
    comment_lines: int = 0

    def to_dict(self) -> dict:
        return {
            "language": self.language,
            "parsed": self.parsed,
            "partial": self.partial,
            "syntax_errors": self.syntax_errors,
            "entry_point": self.entry_point,
            "files": self.files,
            "imports": self.imports,
            "symbols": self.symbols,
            "call_graph": self.call_graph,
            "classes": self.classes,
            "data_structures": self.data_structures,
            "loc": self.loc,
            "comment_lines": self.comment_lines,
            "functions": {
                name: {
                    "name": fn.name,
                    "file": fn.file,
                    "lineno": fn.lineno,
                    "params": fn.params,
                    "calls": fn.calls,
                    "is_recursive": fn.is_recursive,
                    "loop_depth": fn.loop_depth,
                    "branch_count": fn.branch_count,
                    "returns": fn.returns,
                    "has_try": fn.has_try,
                    "cyclomatic": fn.cyclomatic,
                    "cfg_nodes": fn.cfg_nodes,
                    "cfg_edges": fn.cfg_edges,
                    "defs": fn.defs,
                    "uses": fn.uses,
                    "guards": fn.guards,
                    "node_types": fn.node_type_histogram,
                }
                for name, fn in self.functions.items()
            },
        }


# --------------------------------------------------------------------------
# Language detection (model 1 in the ML stack; heuristic until trained)
# --------------------------------------------------------------------------
_EXT_LANGUAGE = {
    ".py": "python", ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp",
    ".hpp": "cpp", ".java": "java", ".js": "javascript", ".ts": "typescript",
    ".go": "go", ".rs": "rust",
}

_CONTENT_HINTS = (
    (re.compile(r"^\s*def\s+\w+\s*\(", re.M), "python", 2.0),
    (re.compile(r"^\s*import\s+\w+", re.M), "python", 1.0),
    (re.compile(r"#include\s*<", re.M), "c", 2.0),
    (re.compile(r"\bpublic\s+class\b", re.M), "java", 3.0),
    (re.compile(r"\bfunc\s+\w+\s*\(", re.M), "go", 2.0),
    (re.compile(r"\bfn\s+\w+\s*\(", re.M), "rust", 2.0),
)


def detect_language(files: dict[str, str]) -> str:
    scores: dict[str, float] = {}
    for path, source in files.items():
        dot = path.lower().rfind(".")
        ext = path.lower()[dot:] if dot >= 0 else ""
        if ext in _EXT_LANGUAGE:
            scores[_EXT_LANGUAGE[ext]] = scores.get(_EXT_LANGUAGE[ext], 0.0) + 3.0
        if source.startswith("#!"):
            shebang = source.splitlines()[0]
            for lang in ("python", "node", "bash"):
                if lang in shebang:
                    key = "javascript" if lang == "node" else lang
                    scores[key] = scores.get(key, 0.0) + 2.0
        for pattern, lang, weight in _CONTENT_HINTS:
            if pattern.search(source):
                scores[lang] = scores.get(lang, 0.0) + weight
    if not scores:
        return "unknown"
    return max(scores.items(), key=lambda kv: kv[1])[0]


def infer_entry_point(files: dict[str, str], declared: str | None = None) -> str | None:
    """Project layout inference: build descriptors first, then entry search."""
    if declared and declared in files:
        return declared
    for name in ("solution.py", "main.py", "app.py", "__main__.py"):
        if name in files:
            return name
    # Fall back to the file defining the most top-level functions.
    best, best_count = None, -1
    for path, source in files.items():
        if not path.endswith(".py"):
            continue
        count = len(re.findall(r"^\s*def\s+\w+", source, re.M))
        if count > best_count:
            best, best_count = path, count
    return best or (next(iter(files)) if files else None)


# --------------------------------------------------------------------------
# Exact parse
# --------------------------------------------------------------------------
_LOOP_NODES = (ast.For, ast.While, ast.AsyncFor)
_BRANCH_NODES = (ast.If, ast.IfExp, ast.Match)
_DATA_STRUCTURE_HINTS = {
    "dict": "hash map", "set": "hash set", "list": "list", "deque": "deque",
    "heapq": "heap", "defaultdict": "hash map", "Counter": "hash map",
    "OrderedDict": "hash map", "tuple": "tuple", "array": "array",
}


class _FunctionVisitor(ast.NodeVisitor):
    """Extracts one function's CFG shape, def-use chains, and guards."""

    def __init__(self, fn: FunctionNode) -> None:
        self.fn = fn
        self._loop_depth = 0

    def generic_visit(self, node: ast.AST) -> None:
        name = type(node).__name__
        self.fn.node_type_histogram[name] = self.fn.node_type_histogram.get(name, 0) + 1
        self.fn.cfg_nodes += 1
        super().generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        target = _call_name(node.func)
        if target:
            self.fn.calls.append(target)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.fn.defs.append(node.id)
        else:
            self.fn.uses.append(node.id)
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        self.fn.returns += 1
        self.fn.cfg_edges += 1
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        self.fn.has_try = True
        self.fn.cfg_edges += len(node.handlers) + 1
        self.generic_visit(node)

    def _visit_loop(self, node: ast.AST) -> None:
        self._loop_depth += 1
        self.fn.loop_depth = max(self.fn.loop_depth, self._loop_depth)
        self.fn.cyclomatic += 1
        self.fn.cfg_edges += 2
        self.generic_visit(node)
        self._loop_depth -= 1

    visit_For = _visit_loop
    visit_While = _visit_loop
    visit_AsyncFor = _visit_loop

    def visit_If(self, node: ast.If) -> None:
        self.fn.branch_count += 1
        self.fn.cyclomatic += 1
        self.fn.cfg_edges += 2
        guard = _describe_guard(node.test)
        if guard:
            guard["lineno"] = node.lineno
            self.fn.guards.append(guard)
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.fn.cyclomatic += len(node.values) - 1
        self.generic_visit(node)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _describe_guard(test: ast.AST) -> dict | None:
    """Recognise the shapes a ``guard_present`` static check looks for.

    Deliberately narrow and deliberately explainable: a rubric item that says
    "handles empty input" should be satisfiable by a check a student can read.
    """
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _call_name(test.operand) or _target_name(test.operand)
        if inner:
            return {"kind": "falsy_check", "target": inner}
    if isinstance(test, ast.Compare):
        left = _target_name(test.left)
        if isinstance(test.left, ast.Call) and _call_name(test.left.func) == "len":
            left = f"len({_target_name(test.left.args[0]) if test.left.args else '?'})"
        op = type(test.ops[0]).__name__ if test.ops else "?"
        right = _target_name(test.comparators[0]) if test.comparators else "?"
        if left:
            return {"kind": "comparison", "target": left, "op": op, "against": right}
    if isinstance(test, ast.Name):
        return {"kind": "truthy_check", "target": test.id}
    if isinstance(test, ast.BoolOp):
        for value in test.values:
            described = _describe_guard(value)
            if described:
                return described
    return None


def _target_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_target_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Constant):
        return repr(node.value)
    if isinstance(node, ast.Subscript):
        return f"{_target_name(node.value)}[]"
    if isinstance(node, ast.Call):
        return f"{_call_name(node.func)}()"
    return "?"


def _count_comments(source: str) -> int:
    try:
        return sum(
            1
            for tok in tokenize.generate_tokens(io.StringIO(source).readline)
            if tok.type == tokenize.COMMENT
        )
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return sum(1 for line in source.splitlines() if line.strip().startswith("#"))


# --------------------------------------------------------------------------
# Lexical recovery parser (the tree-sitter error-tolerance stand-in)
# --------------------------------------------------------------------------
_DEF_RE = re.compile(r"^(\s*)def\s+(\w+)\s*\(([^)]*)\)?", re.M)
_CLASS_RE = re.compile(r"^\s*class\s+(\w+)", re.M)
_IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w.]+)\s+)?import\s+([\w.,\s*]+)", re.M)


def _recover_partial(graph: CodeGraph, path: str, source: str) -> None:
    """Extract what survives a syntax error.

    A student whose file is one delimiter away from compiling still has
    functions, imports, and loop structure. Refusing to see them is precisely
    how autograders produce the zeros nobody trusts.
    """
    graph.partial = True
    lines = source.splitlines()
    for match in _DEF_RE.finditer(source):
        name = match.group(2)
        params = [p.strip().split(":")[0].split("=")[0].strip() for p in match.group(3).split(",") if p.strip()]
        lineno = source[: match.start()].count("\n") + 1
        indent = len(match.group(1))
        end = len(lines)
        for offset in range(lineno, len(lines)):
            line = lines[offset]
            if line.strip() and (len(line) - len(line.lstrip())) <= indent and not line.lstrip().startswith("def "):
                end = offset
                break
        body = "\n".join(lines[lineno:end])
        fn = FunctionNode(
            name=name,
            file=path,
            lineno=lineno,
            end_lineno=end,
            params=params,
            loop_depth=_lexical_loop_depth(body),
            branch_count=len(re.findall(r"^\s*(if|elif)\b", body, re.M)),
            returns=len(re.findall(r"^\s*return\b", body, re.M)),
            has_try=bool(re.search(r"^\s*try\b", body, re.M)),
        )
        fn.calls = sorted(set(re.findall(r"\b(\w+)\s*\(", body)) - {name})
        fn.is_recursive = bool(re.search(rf"\b{re.escape(name)}\s*\(", body))
        fn.cyclomatic = 1 + fn.branch_count + fn.loop_depth
        fn.uses = sorted(set(re.findall(r"\b([a-z_]\w*)\b", body)))[:60]
        graph.functions[name] = fn
        graph.symbols[name] = {"kind": "function", "file": path, "lineno": lineno, "recovered": True}
        graph.call_graph[name] = fn.calls
    for match in _CLASS_RE.finditer(source):
        graph.classes.setdefault(match.group(1), [])
    for match in _IMPORT_RE.finditer(source):
        module = match.group(1) or match.group(2).split(",")[0].strip()
        graph.imports.setdefault(path, []).append(module.strip())


def _lexical_loop_depth(body: str) -> int:
    depth = max_depth = 0
    stack: list[int] = []
    for line in body.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        while stack and indent <= stack[-1]:
            stack.pop()
            depth -= 1
        if re.match(r"^\s*(for|while)\b", line):
            stack.append(indent)
            depth += 1
            max_depth = max(max_depth, depth)
    return max_depth


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def build_code_graph(files: dict[str, str], declared_entry: str | None = None) -> CodeGraph:
    graph = CodeGraph(language=detect_language(files), files=sorted(files))
    graph.entry_point = infer_entry_point(files, declared_entry)

    for path, source in sorted(files.items()):
        if not path.endswith(".py"):
            continue
        graph.loc += sum(1 for line in source.splitlines() if line.strip())
        graph.comment_lines += _count_comments(source)
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            graph.syntax_errors.append(
                {
                    "file": path,
                    "line": exc.lineno or 0,
                    "column": exc.offset or 0,
                    "code": type(exc).__name__,
                    "message": exc.msg or str(exc),
                    "text": (exc.text or "").strip()[:200],
                }
            )
            _recover_partial(graph, path, source)
            continue

        graph.parsed = True
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    graph.imports.setdefault(path, []).append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                graph.imports.setdefault(path, []).append(node.module)
            elif isinstance(node, ast.ClassDef):
                methods = [b.name for b in node.body if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))]
                graph.classes[node.name] = methods
                graph.symbols[node.name] = {"kind": "class", "file": path, "lineno": node.lineno}
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = FunctionNode(
                    name=node.name,
                    file=path,
                    lineno=node.lineno,
                    end_lineno=getattr(node, "end_lineno", node.lineno) or node.lineno,
                    params=[a.arg for a in node.args.args],
                )
                visitor = _FunctionVisitor(fn)
                for child in node.body:
                    visitor.visit(child)
                fn.calls = sorted(set(fn.calls))
                fn.defs = sorted(set(fn.defs))
                fn.uses = sorted(set(fn.uses))
                fn.is_recursive = node.name in fn.calls
                fn.cfg_edges = max(fn.cfg_edges, 1)
                graph.functions[node.name] = fn
                graph.symbols[node.name] = {"kind": "function", "file": path, "lineno": node.lineno}
                graph.call_graph[node.name] = fn.calls

    # Data structures actually used - a deterministic code fact for B6.
    seen: set[str] = set()
    for fn in graph.functions.values():
        for call in fn.calls:
            base = call.split(".")[0]
            if base in _DATA_STRUCTURE_HINTS:
                seen.add(_DATA_STRUCTURE_HINTS[base])
            if call.endswith(".get") or call.endswith(".setdefault") or call.endswith(".keys"):
                seen.add("hash map")
    for modules in graph.imports.values():
        for module in modules:
            base = module.split(".")[0]
            if base in _DATA_STRUCTURE_HINTS:
                seen.add(_DATA_STRUCTURE_HINTS[base])
    for source in files.values():
        for token, label in _DATA_STRUCTURE_HINTS.items():
            if re.search(rf"\b{re.escape(token)}\b", source):
                seen.add(label)
    # Literals and comprehensions. Most students write ``counts = {}`` and never
    # type the word "dict" at all, so a name-only scan reports no data structure
    # for the very code the rubric is about.
    seen |= _literal_structures(files)
    graph.data_structures = sorted(seen)

    # Call-graph cycle detection powers the ``recursion_present`` static check.
    for name, callees in graph.call_graph.items():
        if name in callees:
            graph.functions[name].is_recursive = True
    for cycle in _find_cycles(graph.call_graph):
        for name in cycle:
            if name in graph.functions:
                graph.functions[name].is_recursive = True

    return graph


_LITERAL_NODE_LABELS = {
    ast.Dict: "hash map",
    ast.DictComp: "hash map",
    ast.Set: "hash set",
    ast.SetComp: "hash set",
    ast.List: "list",
    ast.ListComp: "list",
    ast.Tuple: "tuple",
}


def _literal_structures(files: dict[str, str]) -> set[str]:
    """Structures introduced by literal syntax rather than by a constructor call."""
    found: set[str] = set()
    for path, source in files.items():
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            # Lexical fallback so a non-compiling file still yields code facts.
            if re.search(r"=\s*\{\s*\}", source) or re.search(r"\[[^\]]*\]\s*=", source):
                found.add("hash map")
            continue
        for node in ast.walk(tree):
            label = _LITERAL_NODE_LABELS.get(type(node))
            if label is None:
                continue
            # An empty ``{}`` is a dict; a non-empty one may be a set literal,
            # which ast already distinguishes for us.
            found.add(label)
    return found


def _find_cycles(call_graph: dict[str, list[str]]) -> list[list[str]]:
    """Tarjan-lite: any strongly connected component of size > 1 is mutual
    recursion, which ``recursion_present`` must also accept."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: list[list[str]] = []
    counter = [0]

    def strongconnect(node: str) -> None:
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for callee in call_graph.get(node, []):
            if callee not in call_graph:
                continue
            if callee not in index:
                strongconnect(callee)
                low[node] = min(low[node], low[callee])
            elif callee in on_stack:
                low[node] = min(low[node], index[callee])
        if low[node] == index[node]:
            component = []
            while True:
                item = stack.pop()
                on_stack.discard(item)
                component.append(item)
                if item == node:
                    break
            if len(component) > 1:
                cycles.append(component)

    for node in list(call_graph):
        if node not in index:
            try:
                strongconnect(node)
            except RecursionError:  # pragma: no cover - pathological graphs
                return cycles
    return cycles
