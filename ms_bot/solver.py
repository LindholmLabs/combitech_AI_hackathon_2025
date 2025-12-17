from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from .board import BoardSnapshot, Cell


@dataclass
class SolveResult:
    to_click: set[Cell]
    to_flag: set[Cell]
    guess: Cell | None
    guess_prob: float | None


def _adjacent_sets(board: BoardSnapshot, cell: Cell) -> tuple[set[Cell], set[Cell], int]:
    covered: set[Cell] = set()
    flagged: set[Cell] = set()
    for n in board.neighbors(cell):
        v = board.get(n)
        if v == "covered":
            covered.add(n)
        elif v == "flagged":
            flagged.add(n)
    return covered, flagged, len(flagged)


def _basic_deductions(board: BoardSnapshot) -> tuple[set[Cell], set[Cell]]:
    safe: set[Cell] = set()
    mines: set[Cell] = set()

    for cell, number in board.number_cells().items():
        # Include 0: if a cell shows 0, all adjacent covered cells are safe.
        covered, flagged, flagged_count = _adjacent_sets(board, cell)
        if not covered:
            continue
        if number == flagged_count:
            safe |= covered
        if number == flagged_count + len(covered):
            mines |= covered
    safe -= board.flagged_cells()
    mines -= board.flagged_cells()
    return safe, mines


def _subset_deductions(board: BoardSnapshot) -> tuple[set[Cell], set[Cell]]:
    safe: set[Cell] = set()
    mines: set[Cell] = set()

    numbered = [(c, n) for c, n in board.number_cells().items() if n > 0]
    per_cell: dict[Cell, tuple[set[Cell], int]] = {}
    for c, n in numbered:
        covered, _flagged, flagged_count = _adjacent_sets(board, c)
        need = n - flagged_count
        if need < 0 or not covered:
            continue
        per_cell[c] = (covered, need)

    items = list(per_cell.items())
    for i in range(len(items)):
        a_cell, (a_set, a_need) = items[i]
        for j in range(i + 1, len(items)):
            b_cell, (b_set, b_need) = items[j]
            if a_set == b_set or not a_set or not b_set:
                continue
            if a_set.issubset(b_set):
                diff = b_set - a_set
                mines_in_diff = b_need - a_need
            elif b_set.issubset(a_set):
                diff = a_set - b_set
                mines_in_diff = a_need - b_need
            else:
                continue

            if mines_in_diff == 0:
                safe |= diff
            elif mines_in_diff == len(diff):
                mines |= diff

    safe -= board.flagged_cells()
    mines -= board.flagged_cells()
    return safe, mines


def _pattern_121_1221(board: BoardSnapshot) -> tuple[set[Cell], set[Cell]]:
    safe: set[Cell] = set()
    mines: set[Cell] = set()
    nums = board.number_cells()

    def is_open_or_oob(c: Cell) -> bool:
        if not board.in_bounds(c):
            return True
        v = board.get(c)
        return v != "covered" and v != "unknown"

    def is_covered(c: Cell) -> bool:
        return board.in_bounds(c) and board.get(c) == "covered"

    # Horizontal and vertical orientations; we only apply when the pattern borders
    # a single strip of covered cells (edge-like situation).
    for y in range(board.min_y, board.max_y + 1):
        for x in range(board.min_x, board.max_x + 1):
            c0 = Cell(x, y)
            if nums.get(c0) != 1:
                continue

            # 1-2-1 horizontal
            c1 = Cell(x + 1, y)
            c2 = Cell(x + 2, y)
            if board.in_bounds(c2) and nums.get(c1) == 2 and nums.get(c2) == 1:
                for dy in (-1, 1):
                    strip = [Cell(x + k, y + dy) for k in range(3)]
                    other = [Cell(x + k, y - dy) for k in range(3)]
                    if all(is_covered(s) for s in strip) and all(is_open_or_oob(o) for o in other):
                        mines.add(strip[0])
                        mines.add(strip[2])
                        safe.add(strip[1])

            # 1-2-2-1 horizontal
            c3 = Cell(x + 3, y)
            if board.in_bounds(c3) and nums.get(c1) == 2 and nums.get(c2) == 2 and nums.get(c3) == 1:
                for dy in (-1, 1):
                    strip = [Cell(x + k, y + dy) for k in range(4)]
                    other = [Cell(x + k, y - dy) for k in range(4)]
                    if all(is_covered(s) for s in strip) and all(is_open_or_oob(o) for o in other):
                        safe.add(strip[0])
                        safe.add(strip[3])
                        mines.add(strip[1])
                        mines.add(strip[2])

    for x in range(board.min_x, board.max_x + 1):
        for y in range(board.min_y, board.max_y + 1):
            c0 = Cell(x, y)
            if nums.get(c0) != 1:
                continue

            # 1-2-1 vertical
            c1 = Cell(x, y + 1)
            c2 = Cell(x, y + 2)
            if board.in_bounds(c2) and nums.get(c1) == 2 and nums.get(c2) == 1:
                for dx in (-1, 1):
                    strip = [Cell(x + dx, y + k) for k in range(3)]
                    other = [Cell(x - dx, y + k) for k in range(3)]
                    if all(is_covered(s) for s in strip) and all(is_open_or_oob(o) for o in other):
                        mines.add(strip[0])
                        mines.add(strip[2])
                        safe.add(strip[1])

            # 1-2-2-1 vertical
            c3 = Cell(x, y + 3)
            if board.in_bounds(c3) and nums.get(c1) == 2 and nums.get(c2) == 2 and nums.get(c3) == 1:
                for dx in (-1, 1):
                    strip = [Cell(x + dx, y + k) for k in range(4)]
                    other = [Cell(x - dx, y + k) for k in range(4)]
                    if all(is_covered(s) for s in strip) and all(is_open_or_oob(o) for o in other):
                        safe.add(strip[0])
                        safe.add(strip[3])
                        mines.add(strip[1])
                        mines.add(strip[2])

    safe -= board.flagged_cells()
    mines -= board.flagged_cells()
    return safe, mines


@dataclass(frozen=True)
class Constraint:
    cells: frozenset[Cell]
    mines: int


def _frontier_constraints(board: BoardSnapshot) -> tuple[set[Cell], list[Constraint]]:
    frontier: set[Cell] = set()
    constraints: list[Constraint] = []
    for cell, number in board.number_cells().items():
        if number <= 0:
            continue
        covered, _flagged, flagged_count = _adjacent_sets(board, cell)
        if not covered:
            continue
        needed = number - flagged_count
        if needed < 0:
            continue
        frontier |= covered
        constraints.append(Constraint(frozenset(covered), needed))
    return frontier, constraints


def _components(frontier: set[Cell], constraints: list[Constraint]) -> list[tuple[list[Cell], list[Constraint]]]:
    parent: dict[Cell, Cell] = {c: c for c in frontier}

    def find(c: Cell) -> Cell:
        while parent[c] != c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c

    def union(a: Cell, b: Cell) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for con in constraints:
        cells = list(con.cells)
        for i in range(1, len(cells)):
            union(cells[0], cells[i])

    groups: dict[Cell, set[Cell]] = {}
    for c in frontier:
        groups.setdefault(find(c), set()).add(c)

    out: list[tuple[list[Cell], list[Constraint]]] = []
    for root, cells in groups.items():
        cell_set = set(cells)
        cons = [con for con in constraints if con.cells & cell_set]
        out.append((sorted(cells, key=lambda z: (z.y, z.x)), cons))
    return out


def _enumerate_component(
    variables: list[Cell],
    constraints: list[Constraint],
    max_solutions: int = 200_000,
) -> tuple[int, dict[Cell, int]]:
    idx = {c: i for i, c in enumerate(variables)}
    cons_vars: list[list[int]] = [[idx[c] for c in con.cells if c in idx] for con in constraints]
    cons_need: list[int] = [con.mines for con in constraints]

    # Track current assigned mines count per constraint and remaining unassigned.
    cons_assigned = [0] * len(constraints)
    cons_remaining = [len(vs) for vs in cons_vars]
    mines_per_cell = {c: 0 for c in variables}
    total = 0

    # Precompute which constraints each variable affects.
    var_to_cons: list[list[int]] = [[] for _ in variables]
    for ci, vs in enumerate(cons_vars):
        for vi in vs:
            var_to_cons[vi].append(ci)

    assignment = [0] * len(variables)
    # Heuristic ordering: assign most constrained variables first for better pruning.
    order = sorted(range(len(variables)), key=lambda vi: len(var_to_cons[vi]), reverse=True)

    def feasible() -> bool:
        for ci in range(len(constraints)):
            need = cons_need[ci]
            assigned = cons_assigned[ci]
            remaining = cons_remaining[ci]
            if assigned > need:
                return False
            if assigned + remaining < need:
                return False
        return True

    def backtrack(i: int) -> None:
        nonlocal total
        if total >= max_solutions:
            return
        if i == len(variables):
            if all(cons_assigned[ci] == cons_need[ci] for ci in range(len(constraints))):
                total += 1
                for vi, val in enumerate(assignment):
                    if val:
                        mines_per_cell[variables[vi]] += 1
            return

        vi = order[i]
        # Try safe then mine to bias towards fewer mines for early pruning.
        for val in (0, 1):
            assignment[vi] = val
            touched = var_to_cons[vi]
            for ci in touched:
                cons_remaining[ci] -= 1
                cons_assigned[ci] += val
            if feasible():
                backtrack(i + 1)
            for ci in touched:
                cons_assigned[ci] -= val
                cons_remaining[ci] += 1
            assignment[vi] = 0

    backtrack(0)
    return total, mines_per_cell


def _enumerate_component_hist(
    variables: list[Cell],
    constraints: list[Constraint],
    max_solutions: int = 200_000,
) -> tuple[dict[int, int], dict[Cell, dict[int, int]]]:
    """
    Enumerate satisfying assignments for a component and return:
      - counts[m] = number of solutions with exactly m mines in this component
      - per_cell_counts[cell][m] = number of solutions where cell is a mine AND total mines is m
    """
    idx = {c: i for i, c in enumerate(variables)}
    cons_vars: list[list[int]] = [[idx[c] for c in con.cells if c in idx] for con in constraints]
    cons_need: list[int] = [con.mines for con in constraints]

    cons_assigned = [0] * len(constraints)
    cons_remaining = [len(vs) for vs in cons_vars]
    counts: dict[int, int] = {}
    per_cell: dict[Cell, dict[int, int]] = {c: {} for c in variables}

    var_to_cons: list[list[int]] = [[] for _ in variables]
    for ci, vs in enumerate(cons_vars):
        for vi in vs:
            var_to_cons[vi].append(ci)

    order = sorted(range(len(variables)), key=lambda vi: len(var_to_cons[vi]), reverse=True)
    assignment = [0] * len(variables)
    total = 0

    def feasible() -> bool:
        for ci in range(len(constraints)):
            need = cons_need[ci]
            assigned = cons_assigned[ci]
            remaining = cons_remaining[ci]
            if assigned > need:
                return False
            if assigned + remaining < need:
                return False
        return True

    def backtrack(i: int, mine_count: int) -> None:
        nonlocal total
        if total >= max_solutions:
            return
        if i == len(variables):
            if all(cons_assigned[ci] == cons_need[ci] for ci in range(len(constraints))):
                total += 1
                counts[mine_count] = counts.get(mine_count, 0) + 1
                for vi, val in enumerate(assignment):
                    if val:
                        cell = variables[vi]
                        d = per_cell[cell]
                        d[mine_count] = d.get(mine_count, 0) + 1
            return

        vi = order[i]
        for val in (0, 1):
            assignment[vi] = val
            touched = var_to_cons[vi]
            for ci in touched:
                cons_remaining[ci] -= 1
                cons_assigned[ci] += val
            if feasible():
                backtrack(i + 1, mine_count + val)
            for ci in touched:
                cons_assigned[ci] -= val
                cons_remaining[ci] += 1
            assignment[vi] = 0

    backtrack(0, 0)
    return counts, per_cell


def _nCk(n: int, k: int) -> int:
    if k < 0 or k > n:
        return 0
    return math.comb(n, k)


def _probability_guess(
    board: BoardSnapshot,
    total_mines: int,
    component_limit: int = 24,
) -> tuple[Cell | None, float | None, set[Cell], set[Cell]]:
    covered_all = {c for c in board.iter_cells() if board.get(c) == "covered"}
    flagged = board.flagged_cells()
    mines_left = max(0, total_mines - len(flagged))

    frontier, constraints = _frontier_constraints(board)
    safe: set[Cell] = set()
    mines: set[Cell] = set()

    probs: dict[Cell, float] = {}
    unconstrained = covered_all - frontier - flagged

    def heuristic_prob_for_frontier_cell(cell: Cell) -> float | None:
        # Cheap local estimate for cells in large frontier components that we
        # don't enumerate: look at each adjacent revealed number constraint and
        # estimate mine chance as (mines_needed / covered_neighbors_count).
        estimates: list[float] = []
        for n in board.neighbors(cell):
            v = board.get(n)
            if not isinstance(v, int) or v < 0:
                continue
            covered, _flagged, flagged_count = _adjacent_sets(board, n)
            if cell not in covered:
                continue
            need = v - flagged_count
            if need < 0 or len(covered) == 0:
                continue
            estimates.append(need / len(covered))
        if not estimates:
            return None
        # Conservative-ish: use the maximum local density among adjacent constraints.
        return max(estimates)

    # Enumerate each connected frontier component (when small enough).
    enum_components: list[tuple[list[Cell], dict[int, int], dict[Cell, dict[int, int]]]] = []
    for variables, cons in _components(frontier, constraints):
        if len(variables) == 0:
            continue
        if len(variables) > component_limit:
            continue
        counts, per_cell = _enumerate_component_hist(variables, cons)
        if sum(counts.values()) == 0:
            continue
        enum_components.append((variables, counts, per_cell))

    # Combine enumerated components with the global mine total to improve guessing.
    unconstrained_count = len(unconstrained)
    if enum_components:
        # dp_all[sum_mines] = number of ways across components to realize that sum.
        dp_all: dict[int, int] = {0: 1}
        for _vars, counts, _per_cell in enum_components:
            new: dict[int, int] = {}
            for s, ways_s in dp_all.items():
                for m, ways_m in counts.items():
                    new[s + m] = new.get(s + m, 0) + ways_s * ways_m
            dp_all = new

        total_ways = 0
        for s, ways_s in dp_all.items():
            total_ways += ways_s * _nCk(unconstrained_count, mines_left - s)

        if total_ways > 0:
            # Build prefix/suffix DPs so we can compute "other components" efficiently.
            prefix: list[dict[int, int]] = [{0: 1}]
            for _vars, counts, _per_cell in enum_components:
                prev = prefix[-1]
                cur: dict[int, int] = {}
                for s, ways_s in prev.items():
                    for m, ways_m in counts.items():
                        cur[s + m] = cur.get(s + m, 0) + ways_s * ways_m
                prefix.append(cur)

            suffix: list[dict[int, int]] = [{0: 1} for _ in range(len(enum_components) + 1)]
            suffix[-1] = {0: 1}
            for i in range(len(enum_components) - 1, -1, -1):
                nxt = suffix[i + 1]
                counts = enum_components[i][1]
                cur: dict[int, int] = {}
                for s, ways_s in nxt.items():
                    for m, ways_m in counts.items():
                        cur[s + m] = cur.get(s + m, 0) + ways_s * ways_m
                suffix[i] = cur

            for i, (_vars, counts_i, per_cell_i) in enumerate(enum_components):
                # other_dp = convolution(prefix[i], suffix[i+1])
                other_dp: dict[int, int] = {}
                for s1, w1 in prefix[i].items():
                    for s2, w2 in suffix[i + 1].items():
                        other_dp[s1 + s2] = other_dp.get(s1 + s2, 0) + w1 * w2

                # Precompute weight for each m in this component (ways for the rest + unconstrained).
                weight_for_m: dict[int, int] = {}
                for m in counts_i.keys():
                    w = 0
                    for s_other, ways_other in other_dp.items():
                        w += ways_other * _nCk(unconstrained_count, mines_left - (m + s_other))
                    weight_for_m[m] = w

                for cell, hist in per_cell_i.items():
                    mine_weight = 0
                    for m, cnt in hist.items():
                        mine_weight += cnt * weight_for_m.get(m, 0)
                    if mine_weight == 0:
                        safe.add(cell)
                        probs[cell] = 0.0
                    elif mine_weight == total_ways:
                        mines.add(cell)
                        probs[cell] = 1.0
                    else:
                        probs[cell] = mine_weight / total_ways

    # Unconstrained cells: simple global estimate.
    remaining_covered = len(covered_all - flagged)
    base_prob = (mines_left / remaining_covered) if remaining_covered else 1.0
    for c in unconstrained:
        probs[c] = base_prob
    # Frontier cells in skipped components: fill with local heuristic estimate.
    for c in frontier:
        if c in flagged or c in probs:
            continue
        hp = heuristic_prob_for_frontier_cell(c)
        if hp is not None:
            probs[c] = hp

    # If we have any frontier, prefer guessing *on* the frontier instead of
    # clicking in an unrelated "sea" of covered tiles.
    if frontier:
        candidates = [c for c in frontier if c not in flagged and board.get(c) == "covered"]
    else:
        candidates = [c for c in covered_all if c not in flagged]
    if not candidates:
        return None, None, safe, mines

    def edge_corner_bonus(c: Cell) -> float:
        is_edge = c.x in (board.min_x, board.max_x) or c.y in (board.min_y, board.max_y)
        is_corner = (c.x, c.y) in (
            (board.min_x, board.min_y),
            (board.min_x, board.max_y),
            (board.max_x, board.min_y),
            (board.max_x, board.max_y),
        )
        if is_corner:
            return -0.02
        if is_edge:
            return -0.01
        return 0.0

    def score(c: Cell) -> tuple[float, float, int, int]:
        p = probs.get(c, base_prob)
        return (p, p + edge_corner_bonus(c), c.y, c.x)

    best = min(candidates, key=score)
    return best, probs.get(best, base_prob), safe, mines


def solve_step(board: BoardSnapshot, total_mines: int) -> SolveResult:
    to_click: set[Cell] = set()
    to_flag: set[Cell] = set()

    virtual_flags: set[Cell] = set(board.flagged_cells())

    class _VirtualBoard:
        def __init__(self, base: BoardSnapshot, extra_flags: set[Cell]):
            self._base = base
            self._extra_flags = extra_flags
            self.origin_x = base.origin_x
            self.origin_y = base.origin_y
            self.width = base.width
            self.height = base.height

        @property
        def min_x(self) -> int:
            return self.origin_x

        @property
        def min_y(self) -> int:
            return self.origin_y

        @property
        def max_x(self) -> int:
            return self.origin_x + self.width - 1

        @property
        def max_y(self) -> int:
            return self.origin_y + self.height - 1

        def in_bounds(self, cell: Cell) -> bool:
            return self._base.in_bounds(cell)

        def neighbors(self, cell: Cell):
            return self._base.neighbors(cell)

        def iter_cells(self):
            return self._base.iter_cells()

        def number_cells(self):
            return self._base.number_cells()

        def flagged_cells(self) -> set[Cell]:
            return set(self._base.flagged_cells()) | set(self._extra_flags)

        def get(self, cell: Cell) -> object:
            if cell in self._extra_flags:
                return "flagged"
            return self._base.get(cell)

    # Propagate deterministic mine flags through the rules, since new flags can
    # unlock new safe clicks (basic rule) without needing to open additional cells.
    changed = True
    while changed:
        changed = False
        eff = _VirtualBoard(board, virtual_flags)

        safe: set[Cell] = set()
        mines: set[Cell] = set()

        s, m = _basic_deductions(eff)  # type: ignore[arg-type]
        safe |= s
        mines |= m
        s, m = _subset_deductions(eff)  # type: ignore[arg-type]
        safe |= s
        mines |= m
        s, m = _pattern_121_1221(eff)  # type: ignore[arg-type]
        safe |= s
        mines |= m

        # Filter/accept mines safely given already planned flags.
        new_mines = mines - virtual_flags
        if new_mines:
            accepted = filter_flag_moves(board, new_mines, extra_flagged=virtual_flags)
            accepted -= virtual_flags
            if accepted:
                to_flag |= accepted
                virtual_flags |= accepted
                changed = True

        to_click |= safe

        # Enumeration can also produce forced mines/safes. If it produces new
        # mines, loop again to unlock more basic deductions.
        _guess, _prob, enum_safe, enum_mines = _probability_guess(eff, total_mines=total_mines)  # type: ignore[arg-type]
        to_click |= enum_safe
        enum_new = enum_mines - virtual_flags
        if enum_new:
            accepted = filter_flag_moves(board, enum_new, extra_flagged=virtual_flags)
            accepted -= virtual_flags
            if accepted:
                to_flag |= accepted
                virtual_flags |= accepted
                changed = True

    # After propagation, if there are no deterministic actions, pick a guess.
    guess: Cell | None = None
    prob: float | None = None
    if not to_click and not to_flag:
        eff = _VirtualBoard(board, virtual_flags)
        guess, prob, _enum_safe, _enum_mines = _probability_guess(eff, total_mines=total_mines)  # type: ignore[arg-type]

    to_click -= board.flagged_cells()
    to_flag -= board.flagged_cells()
    to_click = {c for c in to_click if board.get(c) == "covered"}
    to_flag = {c for c in to_flag if board.get(c) == "covered"}
    return SolveResult(to_click=to_click, to_flag=to_flag, guess=guess, guess_prob=prob)


def best_guess(board: BoardSnapshot, total_mines: int) -> tuple[Cell | None, float | None]:
    guess, prob, _safe, _mines = _probability_guess(board, total_mines=total_mines)
    return guess, prob


def filter_flag_moves(
    board: BoardSnapshot,
    candidates: Iterable[Cell],
    extra_flagged: set[Cell] | None = None,
) -> set[Cell]:
    """
    Defensive filter to avoid over-flagging around a revealed number.

    This mainly protects against situations where multiple candidate mines touch a
    single "1" (or any number), which would immediately contradict the visible
    constraint and cause a false flag.
    """
    nums = board.number_cells()
    accepted: set[Cell] = set()
    sim_flags: set[Cell] = set(board.flagged_cells())
    if extra_flagged:
        sim_flags |= set(extra_flagged)

    for c in iter_in_order(candidates):
        if board.get(c) != "covered":
            continue
        violates = False
        # Only check revealed number cells adjacent to the candidate.
        for n in board.neighbors(c):
            number = nums.get(n)
            if number is None:
                continue
            flagged_after = 0
            for nn in board.neighbors(n):
                if nn == c or nn in sim_flags:
                    flagged_after += 1
                else:
                    v = board.get(nn)
                    if v == "flagged":
                        flagged_after += 1
            if flagged_after > number:
                violates = True
                break
        if violates:
            continue
        accepted.add(c)
        sim_flags.add(c)

    return accepted


def choose_first_click(board: BoardSnapshot) -> Cell:
    return Cell(board.min_x + board.width // 2, board.min_y + board.height // 2)


def iter_in_order(cells: Iterable[Cell]) -> list[Cell]:
    return sorted(cells, key=lambda c: (c.y, c.x))
