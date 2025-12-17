from __future__ import annotations

from dataclasses import dataclass
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
        if number <= 0:
            continue
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

    for variables, cons in _components(frontier, constraints):
        if len(variables) == 0:
            continue
        if len(variables) > component_limit:
            continue
        total, mines_per_cell = _enumerate_component(variables, cons)
        if total == 0:
            continue
        for c in variables:
            m = mines_per_cell[c]
            if m == 0:
                safe.add(c)
            elif m == total:
                mines.add(c)
            probs[c] = m / total

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

    safe, mines = _basic_deductions(board)
    to_click |= safe
    to_flag |= mines

    safe, mines = _subset_deductions(board)
    to_click |= safe
    to_flag |= mines

    safe, mines = _pattern_121_1221(board)
    to_click |= safe
    to_flag |= mines

    guess, prob, enum_safe, enum_mines = _probability_guess(board, total_mines=total_mines)
    to_click |= enum_safe
    to_flag |= enum_mines

    # Prefer deterministic actions over guessing.
    if to_click or to_flag:
        guess = None
        prob = None

    to_click -= board.flagged_cells()
    to_flag -= board.flagged_cells()
    to_flag = filter_flag_moves(board, to_flag)
    return SolveResult(to_click=to_click, to_flag=to_flag, guess=guess, guess_prob=prob)


def best_guess(board: BoardSnapshot, total_mines: int) -> tuple[Cell | None, float | None]:
    guess, prob, _safe, _mines = _probability_guess(board, total_mines=total_mines)
    return guess, prob


def filter_flag_moves(board: BoardSnapshot, candidates: Iterable[Cell]) -> set[Cell]:
    """
    Defensive filter to avoid over-flagging around a revealed number.

    This mainly protects against situations where multiple candidate mines touch a
    single "1" (or any number), which would immediately contradict the visible
    constraint and cause a false flag.
    """
    nums = board.number_cells()
    accepted: set[Cell] = set()
    sim_flags: set[Cell] = set(board.flagged_cells())

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
