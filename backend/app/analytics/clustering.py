"""§6.4 Misconception clustering.

Each failed submission is embedded as ``(failed test signature, error types,
AST diff to the nearest correct variant, concept context)`` and clustered.
Unsupervised, so it needs no labels and ships on day one.

Clusters are named by the instructor once and then **persist across
semesters**, becoming a reusable misconception library per course. "Nineteen
students failed the same edge case and their ASTs share a common shape" is
worth more to a lecturer than nineteen individual grades.

HDBSCAN is the algorithm the design calls for. This is a dependency-free
implementation of its essential behaviour -- mutual-reachability distances, a
minimum spanning tree, and a cluster-stability selection over the condensed
hierarchy -- which keeps the two properties that actually matter here: it finds
clusters of varying density, and it is allowed to label a point as noise. A
k-means that must assign every struggling student to a cluster invents
misconceptions that do not exist.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class FailureSignature:
    run_id: str
    student_id: str
    failed_tests: list[str] = field(default_factory=list)
    error_types: list[str] = field(default_factory=list)
    concept_keys: list[str] = field(default_factory=list)
    ast_shape: dict[str, float] = field(default_factory=dict)
    algorithm: str = ""
    excerpt: str = ""

    def feature_vector(self) -> dict[str, float]:
        vector: dict[str, float] = {}
        for test in self.failed_tests:
            vector[f"fail:{test}"] = 1.0
        for error in self.error_types:
            vector[f"err:{error}"] = 1.0
        for concept in self.concept_keys:
            vector[f"concept:{concept}"] = 0.6
        if self.algorithm:
            vector[f"algo:{self.algorithm}"] = 0.8
        for key, value in self.ast_shape.items():
            vector[f"ast:{key}"] = value
        return vector


def _cosine_distance(a: dict[str, float], b: dict[str, float]) -> float:
    keys = set(a) | set(b)
    if not keys:
        return 1.0
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - dot / (na * nb)


@dataclass
class Cluster:
    label: int
    members: list[FailureSignature]
    stability: float
    signature: str
    concept_keys: list[str]
    common_failures: list[str]
    representative: FailureSignature

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "size": len(self.members),
            "stability": round(self.stability, 4),
            "auto_signature": self.signature,
            "concept_keys": self.concept_keys,
            "common_failures": self.common_failures,
            "representative_run_id": self.representative.run_id,
            "member_run_ids": [m.run_id for m in self.members],
            "excerpt": self.representative.excerpt[:600],
        }


def _core_distances(matrix: list[list[float]], min_points: int) -> list[float]:
    """Distance to the k-th nearest neighbour: HDBSCAN's density estimate."""
    core: list[float] = []
    for row in matrix:
        neighbours = sorted(row)[1:]      # drop self-distance
        if not neighbours:
            core.append(0.0)
            continue
        index = min(len(neighbours) - 1, max(0, min_points - 1))
        core.append(neighbours[index])
    return core


def _mutual_reachability(matrix: list[list[float]], core: list[float]) -> list[list[float]]:
    n = len(matrix)
    return [
        [max(core[i], core[j], matrix[i][j]) for j in range(n)]
        for i in range(n)
    ]


def _minimum_spanning_tree(matrix: list[list[float]]) -> list[tuple[float, int, int]]:
    """Prim's algorithm over the mutual-reachability graph."""
    n = len(matrix)
    if n == 0:
        return []
    in_tree = [False] * n
    best = [math.inf] * n
    parent = [-1] * n
    best[0] = 0.0
    edges: list[tuple[float, int, int]] = []
    for _ in range(n):
        u = min((i for i in range(n) if not in_tree[i]), key=lambda i: best[i], default=None)
        if u is None:
            break
        in_tree[u] = True
        if parent[u] >= 0:
            edges.append((best[u], parent[u], u))
        for v in range(n):
            if not in_tree[v] and matrix[u][v] < best[v]:
                best[v] = matrix[u][v]
                parent[v] = u
    edges.sort()
    return edges


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.size = [1] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> int:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return ra
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]
        return ra


@dataclass
class _Node:
    """One node of the single-linkage tree."""

    members: list[int]
    distance: float                 # merge distance; 0.0 for a leaf
    children: tuple[int, int] | None = None


def _single_linkage_tree(edges: list[tuple[float, int, int]], n: int) -> list[_Node]:
    """Bottom-up single-linkage tree from the MST, leaves first."""
    nodes: list[_Node] = [_Node([i], 0.0) for i in range(n)]
    union = _UnionFind(n)
    component_node = {i: i for i in range(n)}

    for weight, a, b in edges:
        ra, rb = union.find(a), union.find(b)
        if ra == rb:
            continue
        left, right = component_node[ra], component_node[rb]
        nodes.append(_Node(nodes[left].members + nodes[right].members, weight, (left, right)))
        root = union.union(a, b)
        component_node[root] = len(nodes) - 1
    return nodes


def _condense(nodes: list[_Node], min_cluster_size: int) -> dict[int, dict]:
    """Condense the single-linkage tree into the HDBSCAN cluster hierarchy.

    Walking down from the root, a split where one side is smaller than
    ``min_cluster_size`` is not a split at all — those points simply *fall out*
    of the parent cluster at that density. Only a split where both sides are
    large enough creates two new clusters. This is the step that stops a
    hierarchy of n-1 merges from being read as n-1 clusters.
    """
    root = len(nodes) - 1
    clusters: dict[int, dict] = {}
    # (tree node, parent cluster id, birth distance of the parent cluster)
    stack: list[tuple[int, int | None, float]] = [(root, None, nodes[root].distance)]
    clusters[root] = {
        "node": root, "parent": None, "birth": nodes[root].distance,
        "children": [], "fallouts": [], "members": nodes[root].members,
    }

    while stack:
        node_index, cluster_id, _birth = stack.pop()
        node = nodes[node_index]
        cluster_id = cluster_id if cluster_id is not None else root
        if node.children is None:
            clusters[cluster_id]["fallouts"].append((node_index, node.distance))
            continue

        left, right = node.children
        left_big = len(nodes[left].members) >= min_cluster_size
        right_big = len(nodes[right].members) >= min_cluster_size

        if left_big and right_big:
            # A genuine split: two new clusters are born at this distance.
            for child in (left, right):
                clusters[child] = {
                    "node": child, "parent": cluster_id, "birth": node.distance,
                    "children": [], "fallouts": [], "members": nodes[child].members,
                }
                clusters[cluster_id]["children"].append(child)
                stack.append((child, child, node.distance))
        else:
            # One (or both) sides are too small: those points leave the cluster
            # here, and the larger side continues as the same cluster.
            for child, big in ((left, left_big), (right, right_big)):
                if big:
                    stack.append((child, cluster_id, node.distance))
                else:
                    for point in nodes[child].members:
                        clusters[cluster_id]["fallouts"].append((point, node.distance))
    return clusters


def _stability(cluster: dict, nodes: list[_Node]) -> float:
    """Sum over the cluster's points of (lambda_death - lambda_birth).

    lambda is 1/distance, so a cluster that survives over a wide range of
    densities scores highly and a cluster that immediately shatters does not.
    """
    birth = cluster["birth"]
    lambda_birth = 1.0 / birth if birth > 0 else float("inf")
    if lambda_birth == float("inf"):
        return 0.0
    total = 0.0
    for _point, distance in cluster["fallouts"]:
        lambda_death = 1.0 / distance if distance > 0 else lambda_birth * 4
        total += max(0.0, lambda_death - lambda_birth)
    return total


def cluster_failures(
    signatures: list[FailureSignature],
    min_cluster_size: int = 3,
    min_points: int = 2,
) -> tuple[list[Cluster], list[FailureSignature]]:
    """Return ``(clusters, noise)``.

    Points that belong to no cluster are returned as noise rather than forced
    into the nearest one. A misconception library full of clusters of size one
    is worse than an empty one, because a lecturer will try to teach to them.

    The root cluster is never selected. "Every failing submission is one
    misconception" is always available as an explanation and is never a useful
    one, so it is excluded the way HDBSCAN excludes it by default.
    """
    n = len(signatures)
    if n < min_cluster_size * 2:
        return [], list(signatures)

    vectors = [s.feature_vector() for s in signatures]
    matrix = [[_cosine_distance(vectors[i], vectors[j]) for j in range(n)] for i in range(n)]
    core = _core_distances(matrix, min_points)
    reachability = _mutual_reachability(matrix, core)
    edges = _minimum_spanning_tree(reachability)
    if not edges:
        return [], list(signatures)

    nodes = _single_linkage_tree(edges, n)
    hierarchy = _condense(nodes, min_cluster_size)
    root = len(nodes) - 1
    stabilities = {cid: _stability(cluster, nodes) for cid, cluster in hierarchy.items()}

    # Standard bottom-up selection: keep a node only if it is more stable than
    # its descendants combined. Deepest clusters are considered first.
    selected: set[int] = set()
    order = sorted(hierarchy, key=lambda cid: len(hierarchy[cid]["members"]))
    adjusted = dict(stabilities)
    for cid in order:
        children = hierarchy[cid]["children"]
        if not children:
            selected.add(cid)
            continue
        child_total = sum(adjusted[c] for c in children)
        if child_total > stabilities[cid]:
            adjusted[cid] = child_total
        else:
            adjusted[cid] = stabilities[cid]
            for child in children:
                selected.discard(child)
                for descendant in _descendants(hierarchy, child):
                    selected.discard(descendant)
            selected.add(cid)

    selected.discard(root)
    if not selected:
        return [], list(signatures)

    clusters: list[Cluster] = []
    claimed: set[int] = set()
    for label, cid in enumerate(sorted(selected, key=lambda c: -len(hierarchy[c]["members"]))):
        component = hierarchy[cid]["members"]
        if len(component) < min_cluster_size or any(i in claimed for i in component):
            continue
        claimed.update(component)
        member_sigs = [signatures[i] for i in component]
        clusters.append(_describe(label, member_sigs, adjusted[cid], matrix, component))

    noise = [signatures[i] for i in range(n) if i not in claimed]
    clusters.sort(key=lambda c: len(c.members), reverse=True)
    return clusters, noise


def _descendants(hierarchy: dict[int, dict], cid: int) -> list[int]:
    out: list[int] = []
    stack = list(hierarchy[cid]["children"])
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(hierarchy[current]["children"])
    return out


def _describe(
    label: int,
    members: list[FailureSignature],
    stability: float,
    matrix: list[list[float]],
    indices: list[int],
) -> Cluster:
    failure_counts: dict[str, int] = {}
    error_counts: dict[str, int] = {}
    concept_counts: dict[str, int] = {}
    for signature in members:
        for test in signature.failed_tests:
            failure_counts[test] = failure_counts.get(test, 0) + 1
        for error in signature.error_types:
            error_counts[error] = error_counts.get(error, 0) + 1
        for concept in signature.concept_keys:
            concept_counts[concept] = concept_counts.get(concept, 0) + 1

    threshold = max(1, len(members) // 2)
    common_failures = sorted([t for t, c in failure_counts.items() if c >= threshold])
    common_errors = sorted([e for e, c in error_counts.items() if c >= threshold])
    concepts = sorted(concept_counts, key=lambda c: -concept_counts[c])[:4]

    parts: list[str] = []
    if common_failures:
        parts.append(f"fails {', '.join(common_failures[:4])}")
    if common_errors:
        parts.append(f"raises {', '.join(common_errors[:3])}")
    if concepts:
        parts.append(f"on {', '.join(concepts[:2])}")
    auto_signature = (
        f"{len(members)} student(s) " + "; ".join(parts)
        if parts
        else f"{len(members)} student(s) with a shared failure shape"
    )

    # The representative is the medoid: the member closest to all the others,
    # which is what a lecturer should be shown in a briefing.
    medoid_index = min(indices, key=lambda i: sum(matrix[i][j] for j in indices))
    representative = members[indices.index(medoid_index)]

    return Cluster(
        label=label,
        members=members,
        stability=stability,
        signature=auto_signature,
        concept_keys=concepts,
        common_failures=common_failures,
        representative=representative,
    )
