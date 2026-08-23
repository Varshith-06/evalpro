"""Demo course content: concept graph, outcomes, assignments, and the cohort of
student solution archetypes.

Separated from ``seed.py`` so the content reads as content. Everything here is
synthetic. The student names are invented, and the protected attributes exist
only so the §7.3 bias audit has something to audit; they are never model
features.
"""
from __future__ import annotations

# ==========================================================================
# The concept graph - authored once per course, reused every semester
# ==========================================================================
CONCEPTS: list[dict] = [
    # Week 1-2: foundations
    {"key": "c_var_binding", "name": "Variables and binding", "week": 1, "bloom": "understand",
     "prereq": [], "co": ["CO1"],
     "desc": "Names, assignment, and the difference between a value and a reference to it.",
     "misconceptions": ["assignment_copies_object"]},
    {"key": "c_control_flow", "name": "Conditionals and control flow", "week": 1, "bloom": "apply",
     "prereq": ["c_var_binding"], "co": ["CO1"],
     "desc": "Branching, boolean expressions, and the paths a program can take.",
     "misconceptions": ["assignment_in_condition", "dangling_else"]},
    {"key": "c_loops", "name": "Iteration", "week": 2, "bloom": "apply",
     "prereq": ["c_control_flow"], "co": ["CO1"],
     "desc": "for and while loops, loop variables, and termination.",
     "misconceptions": ["off_by_one_bound", "mutating_while_iterating"]},
    {"key": "c_functions", "name": "Functions and scope", "week": 2, "bloom": "apply",
     "prereq": ["c_var_binding"], "co": ["CO1"],
     "desc": "Parameters, return values, local scope, and why globals hurt.",
     "misconceptions": ["missing_return", "global_mutation"]},

    # Week 3-4: sequences
    {"key": "c_list_indexing", "name": "List indexing", "week": 3, "bloom": "apply",
     "prereq": ["c_loops"], "co": ["CO1", "CO2"],
     "desc": "Zero-based indexing, slicing, and index bounds.",
     "misconceptions": ["off_by_one_bound", "negative_index_confusion"]},
    {"key": "c_bounds_check", "name": "Bounds checking", "week": 3, "bloom": "apply",
     "prereq": ["c_list_indexing"], "co": ["CO2", "CO4"],
     "desc": "Verifying an index or length before using it.",
     "misconceptions": ["off_by_one_bound", "assumes_non_empty"]},
    {"key": "c_defensive_prog", "name": "Defensive programming", "week": 4, "bloom": "apply",
     "prereq": ["c_bounds_check", "c_functions"], "co": ["CO4"],
     "desc": "Handling the inputs a caller should not send but will.",
     "misconceptions": ["assumes_non_empty", "silent_failure"]},
    {"key": "c_swap_inplace", "name": "In-place mutation", "week": 4, "bloom": "apply",
     "prereq": ["c_list_indexing", "c_var_binding"], "co": ["CO2"],
     "desc": "Modifying a sequence without allocating a new one.",
     "misconceptions": ["lost_temp_swap", "aliasing_surprise"]},

    # Week 5-6: complexity and sorting
    {"key": "c_complexity", "name": "Asymptotic complexity", "week": 5, "bloom": "analyse",
     "prereq": ["c_loops"], "co": ["CO3"],
     "desc": "Big-O reasoning about time and space as input grows.",
     "misconceptions": ["constant_factors_dominate", "nested_loop_is_always_n2"]},
    {"key": "c_comparison_sort", "name": "Comparison sorting", "week": 5, "bloom": "apply",
     "prereq": ["c_swap_inplace", "c_bounds_check"], "co": ["CO2", "CO3"],
     "desc": "Selection, insertion, and bubble sort: quadratic exchange sorts.",
     "misconceptions": ["unstable_assumed_stable", "inner_loop_bound_wrong"]},
    {"key": "c_sort_invariants", "name": "Sorting invariants", "week": 6, "bloom": "analyse",
     "prereq": ["c_comparison_sort", "c_complexity"], "co": ["CO3"],
     "desc": "Reasoning about what is true after each pass of a sort.",
     "misconceptions": ["invariant_confused_with_postcondition"]},

    # Week 7-8: search
    {"key": "c_linear_search", "name": "Linear search", "week": 7, "bloom": "apply",
     "prereq": ["c_loops", "c_list_indexing"], "co": ["CO2"],
     "desc": "Scanning a sequence for a value.",
     "misconceptions": ["returns_value_not_index"]},
    {"key": "c_binary_search", "name": "Binary search", "week": 7, "bloom": "apply",
     "prereq": ["c_linear_search", "c_sort_invariants", "c_bounds_check"], "co": ["CO2", "CO3"],
     "desc": "Halving search over a sorted sequence.",
     "misconceptions": ["off_by_one_bound", "infinite_loop_on_equal_bounds", "midpoint_overflow"]},
    {"key": "c_search_correctness", "name": "Search correctness", "week": 8, "bloom": "analyse",
     "prereq": ["c_binary_search"], "co": ["CO3", "CO4"],
     "desc": "Proving a search terminates and reports absence correctly.",
     "misconceptions": ["absent_value_returns_zero"]},

    # Week 9-10: hashing
    {"key": "c_hash_concept", "name": "Hashing", "week": 9, "bloom": "understand",
     "prereq": ["c_functions"], "co": ["CO2"],
     "desc": "Mapping keys to buckets, and what makes a good hash.",
     "misconceptions": ["hash_is_ordered", "hash_is_always_o1"]},
    {"key": "c_dict_usage", "name": "Dictionaries in practice", "week": 9, "bloom": "apply",
     "prereq": ["c_hash_concept", "c_loops"], "co": ["CO2"],
     "desc": "Building and querying key-value maps.",
     "misconceptions": ["keyerror_not_handled", "mutable_key"]},
    {"key": "c_frequency_counting", "name": "Frequency counting", "week": 10, "bloom": "apply",
     "prereq": ["c_dict_usage", "c_bounds_check"], "co": ["CO2", "CO3"],
     "desc": "Accumulating counts over a stream in one pass.",
     "misconceptions": ["missing_key_default", "counts_reset_in_loop"]},

    # Week 11-13: recursion and trees
    {"key": "c_recursion_basics", "name": "Recursion", "week": 11, "bloom": "apply",
     "prereq": ["c_functions", "c_control_flow"], "co": ["CO1", "CO5"],
     "desc": "Self-reference, base cases, and the call stack.",
     "misconceptions": ["missing_base_case", "no_progress_toward_base"]},
    {"key": "c_recursion_depth", "name": "Recursive decomposition", "week": 11, "bloom": "analyse",
     "prereq": ["c_recursion_basics"], "co": ["CO5"],
     "desc": "Splitting a problem into strictly smaller subproblems.",
     "misconceptions": ["subproblem_not_smaller"]},
    {"key": "c_tree_structure", "name": "Tree structure", "week": 12, "bloom": "understand",
     "prereq": ["c_recursion_basics", "c_var_binding"], "co": ["CO5"],
     "desc": "Nodes, children, depth, and height.",
     "misconceptions": ["depth_vs_height", "empty_tree_depth"]},
    {"key": "c_tree_traversal", "name": "Tree traversal", "week": 12, "bloom": "apply",
     "prereq": ["c_tree_structure", "c_recursion_depth"], "co": ["CO5"],
     "desc": "Visiting every node of a tree in a defined order.",
     "misconceptions": ["depth_vs_height", "missing_base_case"]},
    {"key": "c_divide_conquer", "name": "Divide and conquer", "week": 13, "bloom": "analyse",
     "prereq": ["c_recursion_depth", "c_complexity", "c_comparison_sort"], "co": ["CO3", "CO5"],
     "desc": "Split, solve, combine - and the recurrences it produces.",
     "misconceptions": ["merge_step_forgotten"]},

    # Cross-cutting
    {"key": "c_testing", "name": "Testing your own code", "week": 6, "bloom": "apply",
     "prereq": ["c_functions", "c_bounds_check"], "co": ["CO4"],
     "desc": "Choosing inputs that would reveal a bug rather than confirm a hope.",
     "misconceptions": ["only_happy_path"]},
    {"key": "c_code_documentation", "name": "Documenting code", "week": 4, "bloom": "understand",
     "prereq": ["c_functions"], "co": ["CO4"],
     "desc": "Explaining intent, not restating syntax.",
     "misconceptions": ["comments_restate_code"]},
]

RESOURCES: dict[str, list[dict]] = {
    "c_bounds_check": [
        {"kind": "practice", "title": "Bounds and empty-input drills", "url": "course://drills/bounds"},
        {"kind": "reading", "title": "Why the empty case is the first case", "url": "course://notes/empty-case"},
    ],
    "c_off_by_one": [],
    "c_binary_search": [
        {"kind": "practice", "title": "Binary search: eight variants, one invariant", "url": "course://drills/bsearch"},
        {"kind": "worked_example", "title": "Deriving the loop invariant", "url": "course://notes/bsearch-invariant"},
    ],
    "c_list_indexing": [
        {"kind": "practice", "title": "Indexing and slicing drills", "url": "course://drills/indexing"},
    ],
    "c_loops": [
        {"kind": "practice", "title": "Loop bound exercises", "url": "course://drills/loops"},
    ],
    "c_recursion_basics": [
        {"kind": "practice", "title": "Base case first: ten small recursions", "url": "course://drills/recursion"},
        {"kind": "reading", "title": "Reading the call stack", "url": "course://notes/call-stack"},
    ],
    "c_recursion_depth": [
        {"kind": "practice", "title": "Strictly smaller subproblems", "url": "course://drills/decomposition"},
    ],
    "c_dict_usage": [
        {"kind": "practice", "title": "Dictionary patterns", "url": "course://drills/dicts"},
    ],
    "c_frequency_counting": [
        {"kind": "practice", "title": "One-pass counting", "url": "course://drills/counting"},
    ],
    "c_hash_concept": [
        {"kind": "reading", "title": "What a hash table actually does", "url": "course://notes/hashing"},
    ],
    "c_comparison_sort": [
        {"kind": "practice", "title": "Exchange sorts by hand", "url": "course://drills/sorting"},
    ],
    "c_complexity": [
        {"kind": "reading", "title": "Counting operations, not seconds", "url": "course://notes/complexity"},
    ],
    "c_tree_structure": [
        {"kind": "reading", "title": "Depth, height, and the empty tree", "url": "course://notes/trees"},
    ],
    "c_defensive_prog": [
        {"kind": "practice", "title": "Inputs your caller should not send", "url": "course://drills/defensive"},
    ],
}

COURSE_OUTCOMES: list[dict] = [
    {"code": "CO1", "text": "Write correct, readable procedural programs using variables, control flow, and functions.",
     "po": ["PO1", "PO3"], "weights": {"PO1": 3, "PO3": 2}},
    {"code": "CO2", "text": "Select and implement appropriate linear data structures and search techniques.",
     "po": ["PO1", "PO2", "PO3"], "weights": {"PO1": 3, "PO2": 3, "PO3": 2}},
    {"code": "CO3", "text": "Analyse the time and space complexity of an implementation and justify the choice made.",
     "po": ["PO2", "PO4"], "weights": {"PO2": 3, "PO4": 2}},
    {"code": "CO4", "text": "Design and defend test cases, including boundary and failure conditions.",
     "po": ["PO4", "PO5"], "weights": {"PO4": 3, "PO5": 2}},
    {"code": "CO5", "text": "Apply recursion and hierarchical structures to decompose non-trivial problems.",
     "po": ["PO2", "PO3", "PO5"], "weights": {"PO2": 2, "PO3": 3, "PO5": 2}},
]


# ==========================================================================
# Assignments
# ==========================================================================
A1_REFERENCE = '''"""Reference solution: ascending sort without the built-in sort."""


def solve(nums):
    """Return a new list containing nums in ascending order."""
    if not nums:
        return []
    items = list(nums)
    for i in range(len(items)):
        smallest = i
        for j in range(i + 1, len(items)):
            if items[j] < items[smallest]:
                smallest = j
        if smallest != i:
            items[i], items[smallest] = items[smallest], items[i]
    return items
'''

A2_REFERENCE = '''"""Reference solution: iterative binary search."""


def solve(haystack, target):
    """Return the index of target in the sorted list haystack, or -1."""
    lo, hi = 0, len(haystack) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if haystack[mid] == target:
            return mid
        if haystack[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1
'''

A3_REFERENCE = '''"""Reference solution: one-pass frequency counting with a dictionary."""


def solve(items):
    """Return a dict mapping each value in items to its number of occurrences."""
    counts = {}
    if not items:
        return counts
    for item in items:
        key = str(item)
        counts[key] = counts.get(key, 0) + 1
    return counts
'''

A4_REFERENCE = '''"""Reference solution: recursive nesting depth."""


def solve(nested):
    """Return the maximum nesting depth of a list of lists."""
    if not isinstance(nested, list):
        return 0
    if not nested:
        return 1
    deepest = 0
    for element in nested:
        depth = solve(element)
        if depth > deepest:
            deepest = depth
    return 1 + deepest
'''


ASSIGNMENTS: list[dict] = [
    {
        "code": "LAB01",
        "title": "Sorting without the built-in",
        "week": 5,
        "entry_point": "solution.py",
        "entry_call": "solve",
        "requires_report": False,
        "reference": A1_REFERENCE,
        "expected_algorithm": "quadratic_sort",
        "brief": (
            "Implement solve(nums) returning a new list with the elements of nums in ascending order.\n"
            "- The result must be in ascending order for every input\n"
            "- The result must contain exactly the same elements as the input, with the same multiplicities\n"
            "- The empty list and a single-element list must be handled without crashing\n"
            "- You may not call the built-in sorted() or list.sort(); implement a comparison sort yourself\n"
            "- The implementation should be a nested-loop exchange sort, so quadratic time is expected\n"
        ),
        "rubric": [
            {"key": "rb_01", "text": "Returns the input elements in ascending order", "category": "correctness",
             "weight": 10, "concepts": ["c_comparison_sort", "c_sort_invariants"],
             "checks": ["test"], "tests": ["tc_01", "tc_02", "tc_05"]},
            {"key": "rb_02", "text": "Preserves the input multiset - no elements lost or invented", "category": "correctness",
             "weight": 8, "concepts": ["c_swap_inplace", "c_list_indexing"],
             "checks": ["test"], "tests": ["tc_02", "tc_05"]},
            {"key": "rb_03", "text": "Handles the empty-input case without crashing", "category": "robustness",
             "weight": 6, "concepts": ["c_bounds_check", "c_defensive_prog"],
             "checks": ["test", "static"], "tests": ["tc_03"],
             "static": {"kind": "guard_present", "target": "input_length"}},
            # Two separate claims, so two separate items. Checking only "did not
            # call sorted()" gave full marks to a program that returned the
            # input untouched - it had not delegated to the built-in, but it had
            # not sorted anything either.
            {"key": "rb_04", "text": "Implements a comparison sort rather than returning the input unchanged",
             "category": "correctness", "weight": 6, "concepts": ["c_comparison_sort"],
             "checks": ["static", "structural"], "tests": [],
             "static": {"kind": "algorithm_class", "target": "quadratic_sort"}},
            {"key": "rb_06", "text": "Does not delegate to the built-in sorted()", "category": "correctness",
             "weight": 4, "concepts": ["c_comparison_sort"],
             "checks": ["static"], "tests": [],
             "static": {"kind": "api_absent", "target": "sorted"}},
            {"key": "rb_05", "text": "Loop bounds are correct at both ends of the range", "category": "correctness",
             "weight": 5, "concepts": ["c_loops", "c_bounds_check"],
             "checks": ["test"], "tests": ["tc_04", "tc_05"]},
        ],
        "tests": [
            {"key": "tc_01", "category": "smoke", "weight": 1, "args": [[3, 1, 2]], "expected": "[1, 2, 3]"},
            {"key": "tc_02", "category": "property", "weight": 3,
             "property": {"generator": {"kind": "int_list", "n": [0, 40], "lo": -100, "hi": 100},
                          "predicates": ["is_ascending", "multiset_equal_to_input"]}},
            {"key": "tc_03", "category": "edge", "weight": 2, "args": [[]], "expected": "[]"},
            {"key": "tc_04", "category": "edge", "weight": 2, "args": [[7]], "expected": "[7]"},
            {"key": "tc_05", "category": "stress", "weight": 3, "hidden": True,
             "property": {"generator": {"kind": "int_list", "n": [150, 300], "lo": -5000, "hi": 5000},
                          "predicates": ["is_ascending", "multiset_equal_to_input", "same_length_as_input"]}},
        ],
    },
    {
        "code": "LAB02",
        "title": "Binary search and the absent value",
        "week": 8,
        "entry_point": "solution.py",
        "entry_call": "solve",
        "requires_report": True,
        "reference": A2_REFERENCE,
        "expected_algorithm": "binary_search",
        "brief": (
            "Implement solve(haystack, target) where haystack is a sorted list. Return the index of "
            "target, or -1 if it is not present.\n"
            "- Must return a valid index whenever the target is present\n"
            "- Must return -1 when the target is absent, including for the empty list\n"
            "- Must run in logarithmic time: use binary search, not a linear scan\n"
            "- The loop must terminate for every input, including when the bounds meet\n"
            "- Submit a short report describing your approach and its complexity\n"
        ),
        "rubric": [
            {"key": "rb_01", "text": "Returns a correct index when the target is present", "category": "correctness",
             "weight": 10, "concepts": ["c_binary_search", "c_list_indexing"],
             "checks": ["test"], "tests": ["tc_01", "tc_02", "tc_05"]},
            {"key": "rb_02", "text": "Returns -1 when the target is absent", "category": "correctness",
             "weight": 8, "concepts": ["c_search_correctness", "c_bounds_check"],
             "checks": ["test"], "tests": ["tc_02", "tc_03", "tc_05"]},
            {"key": "rb_03", "text": "Handles the empty haystack without crashing", "category": "robustness",
             "weight": 5, "concepts": ["c_bounds_check", "c_defensive_prog"],
             "checks": ["test", "static"], "tests": ["tc_03"],
             "static": {"kind": "guard_present", "target": "input_length"}},
            # Checked by algorithm class rather than loop nesting: "at most one
            # loop" is also true of a function with no loops at all, so a
            # submission that did nothing passed it.
            {"key": "rb_04", "text": "Uses a halving search rather than a linear scan", "category": "efficiency",
             "weight": 8, "concepts": ["c_binary_search", "c_complexity"],
             "checks": ["static", "structural"], "tests": [],
             "static": {"kind": "algorithm_class", "target": "binary_search"}},
            {"key": "rb_05", "text": "The report describes the submitted implementation and its complexity",
             "category": "communication", "weight": 5, "concepts": ["c_complexity", "c_code_documentation"],
             "checks": ["report"], "tests": []},
        ],
        "tests": [
            {"key": "tc_01", "category": "smoke", "weight": 1, "args": [[1, 3, 5, 7], 5], "expected": "2"},
            {"key": "tc_02", "category": "property", "weight": 3,
             "property": {"generator": {"kind": "sorted_list_and_target", "n": [0, 40], "lo": -60, "hi": 60},
                          "predicates": ["search_index_correct"], "spread_args": True}},
            {"key": "tc_03", "category": "edge", "weight": 2, "args": [[], 4], "expected": "-1"},
            {"key": "tc_04", "category": "edge", "weight": 2, "args": [[2], 2], "expected": "0"},
            {"key": "tc_05", "category": "stress", "weight": 3, "hidden": True,
             "property": {"generator": {"kind": "sorted_list_and_target", "n": [200, 400], "lo": -2000, "hi": 2000},
                          "predicates": ["search_index_correct"], "spread_args": True}},
        ],
    },
    {
        "code": "LAB03",
        "title": "Frequency counting in one pass",
        "week": 10,
        "entry_point": "solution.py",
        "entry_call": "solve",
        "requires_report": True,
        "reference": A3_REFERENCE,
        "expected_algorithm": "hash_lookup",
        "brief": (
            "Implement solve(items) returning a dictionary mapping each distinct value in items "
            "(as a string key) to the number of times it occurs.\n"
            "- Every key in the result must come from the input, and the counts must total len(items)\n"
            "- The empty input must return an empty dictionary, not None and not a crash\n"
            "- Use a hash map so the whole thing is a single linear pass\n"
            "- Do not use a nested loop to count occurrences\n"
            "- Submit a short report stating the data structure you used and the resulting complexity\n"
        ),
        "rubric": [
            {"key": "rb_01", "text": "Counts are correct for every distinct value", "category": "correctness",
             "weight": 10, "concepts": ["c_frequency_counting", "c_dict_usage"],
             "checks": ["test"], "tests": ["tc_01", "tc_02", "tc_05"]},
            {"key": "rb_02", "text": "Every key in the result comes from the input", "category": "correctness",
             "weight": 6, "concepts": ["c_dict_usage"],
             "checks": ["test"], "tests": ["tc_02"]},
            {"key": "rb_03", "text": "Empty input returns an empty mapping", "category": "robustness",
             "weight": 5, "concepts": ["c_bounds_check", "c_defensive_prog"],
             "checks": ["test", "static"], "tests": ["tc_03"],
             "static": {"kind": "guard_present", "target": "input_length"}},
            {"key": "rb_04", "text": "Counting is a single linear pass, not a nested scan", "category": "efficiency",
             "weight": 8, "concepts": ["c_complexity", "c_hash_concept"],
             "checks": ["static", "structural"], "tests": [],
             "static": {"kind": "loop_nesting", "max_depth": 1}},
            {"key": "rb_05", "text": "The report names the data structure actually used and its complexity",
             "category": "communication", "weight": 6, "concepts": ["c_hash_concept", "c_complexity"],
             "checks": ["report"], "tests": []},
        ],
        "tests": [
            {"key": "tc_01", "category": "smoke", "weight": 1, "args": [[1, 1, 2]],
             "expected": '{"1": 2, "2": 1}'},
            {"key": "tc_02", "category": "property", "weight": 3,
             "property": {"generator": {"kind": "int_list", "n": [0, 50], "lo": -8, "hi": 8},
                          "predicates": ["counts_match_input", "keys_are_input_values"]}},
            {"key": "tc_03", "category": "edge", "weight": 2, "args": [[]], "expected": "{}"},
            {"key": "tc_04", "category": "basic", "weight": 2, "args": [[5, 5, 5, 5]],
             "expected": '{"5": 4}'},
            {"key": "tc_05", "category": "stress", "weight": 3, "hidden": True,
             "property": {"generator": {"kind": "int_list", "n": [400, 900], "lo": -30, "hi": 30},
                          "predicates": ["counts_match_input"]}},
        ],
    },
    {
        "code": "LAB04",
        "title": "Recursive nesting depth",
        "week": 12,
        "entry_point": "solution.py",
        "entry_call": "solve",
        "requires_report": False,
        "reference": A4_REFERENCE,
        "expected_algorithm": "recursive_traversal",
        "brief": (
            "Implement solve(nested) returning the maximum nesting depth of a list of lists. "
            "A non-list value has depth 0; the empty list has depth 1.\n"
            "- The implementation must be recursive; an explicit stack or queue is not accepted\n"
            "- The base case must be handled before any recursive call\n"
            "- Deeply nested inputs must not exhaust the stack for the sizes tested\n"
            "- Every element of every level must be considered, not just the first\n"
        ),
        "rubric": [
            {"key": "rb_01", "text": "Computes the maximum nesting depth correctly", "category": "correctness",
             "weight": 10, "concepts": ["c_tree_structure", "c_recursion_depth"],
             "checks": ["test"], "tests": ["tc_01", "tc_02", "tc_05"]},
            {"key": "rb_02", "text": "Uses recursion rather than an explicit stack", "category": "correctness",
             "weight": 8, "concepts": ["c_recursion_basics"],
             "checks": ["static", "structural"], "tests": [],
             "static": {"kind": "recursion_present"}},
            {"key": "rb_03", "text": "Handles the empty list and non-list values as specified", "category": "robustness",
             "weight": 6, "concepts": ["c_bounds_check", "c_defensive_prog"],
             "checks": ["test", "static"], "tests": ["tc_03", "tc_04"],
             "static": {"kind": "guard_present", "target": "nested"}},
            {"key": "rb_04", "text": "Considers every branch, not only the first element", "category": "correctness",
             "weight": 6, "concepts": ["c_tree_traversal", "c_loops"],
             "checks": ["test"], "tests": ["tc_02", "tc_05"]},
        ],
        "tests": [
            {"key": "tc_01", "category": "smoke", "weight": 1, "args": [[1, [2, [3]]]], "expected": "3"},
            {"key": "tc_02", "category": "property", "weight": 3,
             "property": {"generator": {"kind": "nested_list", "max_depth": 5, "max_width": 4},
                          "predicates": ["depth_matches_input"]}},
            {"key": "tc_03", "category": "edge", "weight": 2, "args": [[]], "expected": "1"},
            {"key": "tc_04", "category": "edge", "weight": 2, "args": [7], "expected": "0"},
            {"key": "tc_05", "category": "stress", "weight": 3, "hidden": True,
             "property": {"generator": {"kind": "nested_list", "max_depth": 8, "max_width": 3},
                          "predicates": ["depth_matches_input"]}},
        ],
    },
]


# ==========================================================================
# Student solution archetypes
# ==========================================================================
# Each archetype is a realistic thing a student actually submits. Between them
# they exercise every stage of the cascade: clean passes, a syntax slip that
# repair distance should rescue, an off-by-one that structural credit should
# still recognise as the right algorithm, a wrong-complexity solution that only
# the static check catches, a report that contradicts the code, a timeout, and
# a near-copy pair for the integrity screen.

ARCHETYPES: dict[str, dict[str, dict]] = {
    "LAB01": {
        "correct": {"quality": "strong", "source": '''"""Selection sort."""


def solve(nums):
    if not nums:
        return []
    items = list(nums)
    for i in range(len(items)):
        smallest = i
        for j in range(i + 1, len(items)):
            if items[j] < items[smallest]:
                smallest = j
        items[i], items[smallest] = items[smallest], items[i]
    return items
'''},
        "correct_insertion": {"quality": "strong", "source": '''"""Insertion sort - same contract, different shape."""


def solve(nums):
    if len(nums) == 0:
        return []
    items = list(nums)
    for i in range(1, len(items)):
        current = items[i]
        j = i - 1
        while j >= 0 and items[j] > current:
            items[j + 1] = items[j]
            j = j - 1
        items[j + 1] = current
    return items
'''},
        "missing_colon": {"quality": "syntax_slip", "source": '''"""Selection sort with one missing colon."""


def solve(nums)
    if not nums:
        return []
    items = list(nums)
    for i in range(len(items)):
        smallest = i
        for j in range(i + 1, len(items)):
            if items[j] < items[smallest]:
                smallest = j
        items[i], items[smallest] = items[smallest], items[i]
    return items
'''},
        "off_by_one": {"quality": "off_by_one", "source": '''"""Selection sort with an inner-loop bound one short."""


def solve(nums):
    if not nums:
        return []
    items = list(nums)
    for i in range(len(items)):
        smallest = i
        for j in range(i + 1, len(items) - 1):
            if items[j] < items[smallest]:
                smallest = j
        items[i], items[smallest] = items[smallest], items[i]
    return items
'''},
        "no_empty_guard": {"quality": "partial", "source": '''"""Sorts, but assumes the input is non-empty."""


def solve(nums):
    items = list(nums)
    biggest = items[0]
    for i in range(len(items)):
        smallest = i
        for j in range(i + 1, len(items)):
            if items[j] < items[smallest]:
                smallest = j
        items[i], items[smallest] = items[smallest], items[i]
    return items
'''},
        "uses_builtin": {"quality": "shortcut", "source": '''"""Correct output, forbidden method."""


def solve(nums):
    if not nums:
        return []
    return sorted(nums)
'''},
    },

    "LAB02": {
        "correct": {"quality": "strong", "source": '''"""Iterative binary search."""


def solve(haystack, target):
    if len(haystack) == 0:
        return -1
    lo = 0
    hi = len(haystack) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if haystack[mid] == target:
            return mid
        if haystack[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1
''', "report": (
            "I implemented an iterative binary search over the sorted input. The loop maintains the "
            "invariant that the target, if present, lies within haystack[lo:hi+1]. Each iteration halves "
            "that range, so the running time is O(log n) and the space is O(1). The empty list is handled "
            "by the length guard before the loop, and an absent target falls out of the loop and returns -1."
        )},
        "correct_recursive": {"quality": "strong", "source": '''"""Recursive binary search."""


def search(haystack, target, lo, hi):
    if lo > hi:
        return -1
    mid = (lo + hi) // 2
    if haystack[mid] == target:
        return mid
    if haystack[mid] < target:
        return search(haystack, target, mid + 1, hi)
    return search(haystack, target, lo, mid - 1)


def solve(haystack, target):
    if not haystack:
        return -1
    return search(haystack, target, 0, len(haystack) - 1)
''', "report": (
            "The search is recursive: each call inspects the midpoint and recurses into the half that can "
            "still contain the target. The recursion depth is O(log n) and so is the running time. The base "
            "case lo > hi means the target is absent and returns -1."
        )},
        "off_by_one": {"quality": "off_by_one", "source": '''"""Binary search with the high bound set one too far."""


def solve(haystack, target):
    if not haystack:
        return -1
    lo = 0
    hi = len(haystack)
    while lo <= hi:
        mid = (lo + hi) // 2
        if mid >= len(haystack):
            hi = mid - 1
            continue
        if haystack[mid] == target:
            return mid
        if haystack[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1
''', "report": (
            "Binary search over the sorted array, halving the search interval each step, so O(log n)."
        )},
        "linear_scan": {"quality": "wrong_complexity", "source": '''"""A linear scan that produces correct answers slowly."""


def solve(haystack, target):
    if not haystack:
        return -1
    for index in range(len(haystack)):
        if haystack[index] == target:
            return index
    return -1
''', "report": (
            "I used a hash map to get O(1) lookup of the target, so the search is constant time regardless "
            "of the size of the input array. Binary search was not necessary given the hash structure."
        )},
        "absent_returns_zero": {"quality": "partial", "source": '''"""Returns 0 rather than -1 when the target is absent."""


def solve(haystack, target):
    if not haystack:
        return 0
    lo = 0
    hi = len(haystack) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if haystack[mid] == target:
            return mid
        if haystack[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return 0
''', "report": (
            "Standard binary search in O(log n) time. If the value is not there the function returns 0."
        )},
        "infinite_loop": {"quality": "timeout", "source": '''"""The bounds never move when the midpoint misses."""


def solve(haystack, target):
    if not haystack:
        return -1
    lo = 0
    hi = len(haystack) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if haystack[mid] == target:
            return mid
        if haystack[mid] < target:
            lo = mid
        else:
            hi = mid
    return -1
''', "report": "Binary search, O(log n), halving the interval on each comparison."},
    },

    "LAB03": {
        "correct": {"quality": "strong", "source": '''"""One-pass counting with a dictionary."""


def solve(items):
    counts = {}
    if not items:
        return counts
    for item in items:
        key = str(item)
        if key in counts:
            counts[key] = counts[key] + 1
        else:
            counts[key] = 1
    return counts
''', "report": (
            "I used a dictionary as a hash map from the string form of each value to its running count. "
            "The whole computation is a single pass over the input, so it is O(n) time and O(k) space for "
            "k distinct values. An empty input returns an empty dictionary."
        )},
        "correct_get": {"quality": "strong", "source": '''"""Counting with dict.get for the default."""


def solve(items):
    if len(items) == 0:
        return {}
    counts = {}
    for item in items:
        counts[str(item)] = counts.get(str(item), 0) + 1
    return counts
''', "report": (
            "A dictionary accumulates the counts in one linear pass, using get() to supply the zero default "
            "for a key seen for the first time. Time complexity O(n)."
        )},
        "nested_loop": {"quality": "wrong_complexity", "source": '''"""Correct counts, quadratic method."""


def solve(items):
    if not items:
        return {}
    counts = {}
    for outer in items:
        total = 0
        for inner in items:
            if str(inner) == str(outer):
                total = total + 1
        counts[str(outer)] = total
    return counts
''', "report": (
            "I used a hash map for O(1) lookup so the counting is linear in the size of the input."
        )},
        "keyerror": {"quality": "partial", "source": '''"""Missing the default for a first-seen key."""


def solve(items):
    counts = {}
    for item in items:
        counts[str(item)] = counts[str(item)] + 1
    return counts
''', "report": "A dictionary holds the counts. One pass, O(n)."},
        "missing_paren": {"quality": "syntax_slip", "source": '''"""Correct counting, one unclosed call."""


def solve(items):
    if not items:
        return {}
    counts = {}
    for item in items:
        counts[str(item)] = counts.get(str(item), 0 + 1
    return counts
''', "report": "Dictionary-based counting in a single pass, O(n) time."},
    },

    "LAB04": {
        "correct": {"quality": "strong", "source": '''"""Recursive nesting depth."""


def solve(nested):
    if not isinstance(nested, list):
        return 0
    if not nested:
        return 1
    deepest = 0
    for element in nested:
        depth = solve(element)
        if depth > deepest:
            deepest = depth
    return 1 + deepest
'''},
        "correct_max": {"quality": "strong", "source": '''"""Same recursion, expressed with max."""


def solve(nested):
    if not isinstance(nested, list):
        return 0
    if len(nested) == 0:
        return 1
    return 1 + max(solve(element) for element in nested)
'''},
        "first_element_only": {"quality": "partial", "source": '''"""Recurses into the first element only."""


def solve(nested):
    if not isinstance(nested, list):
        return 0
    if not nested:
        return 1
    return 1 + solve(nested[0])
'''},
        "iterative_stack": {"quality": "wrong_method", "source": '''"""Correct answers, but iterative with an explicit stack."""


def solve(nested):
    if not isinstance(nested, list):
        return 0
    if not nested:
        return 1
    deepest = 0
    stack = [(nested, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > deepest:
            deepest = depth
        for element in current:
            if isinstance(element, list):
                stack.append((element, depth + 1))
            elif depth > deepest:
                deepest = depth
    return deepest
'''},
        "no_base_case": {"quality": "crash", "source": '''"""No guard for a non-list value."""


def solve(nested):
    if not nested:
        return 1
    deepest = 0
    for element in nested:
        depth = solve(element)
        if depth > deepest:
            deepest = depth
    return 1 + deepest
'''},
    },
}


# The integrity demonstration: a near-copy of LAB03's correct solution with
# identifiers renamed, comments changed, and statements reordered - exactly the
# transformation AST normalisation is built to see through.
PLAGIARISED_LAB03 = '''"""Counting the occurrences of each element."""


def solve(values):
    result = {}
    if not values:
        return result
    for element in values:
        name = str(element)
        if name in result:
            result[name] = result[name] + 1
        else:
            result[name] = 1
    return result
'''

STUDENT_NAMES: list[str] = [
    "Aarav Menon", "Diya Krishnan", "Rohan Iyer", "Sneha Reddy", "Kabir Nair",
    "Ananya Pillai", "Vikram Rao", "Meera Joshi", "Arjun Varma", "Ishita Bose",
    "Nikhil Shetty", "Priya Sundaram", "Aditya Kulkarni", "Tara Chandran", "Rahul Deshpande",
    "Kavya Ramesh", "Siddharth Bhat", "Neha Agarwal", "Manish Gupta", "Divya Nambiar",
    "Karthik Subramanian", "Riya Mehta", "Aman Chatterjee", "Pooja Hegde",
]
