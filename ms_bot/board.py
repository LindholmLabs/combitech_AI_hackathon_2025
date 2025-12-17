from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Iterator


_OPEN_RE = re.compile(r"\bopen(\d)\b")


@dataclass(frozen=True)
class Cell:
    x: int
    y: int


@dataclass(frozen=True)
class BoardSnapshot:
    origin_x: int
    origin_y: int
    width: int
    height: int
    cells: dict[Cell, object]

    def in_bounds(self, cell: Cell) -> bool:
        return (
            self.origin_x <= cell.x < self.origin_x + self.width
            and self.origin_y <= cell.y < self.origin_y + self.height
        )

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

    def neighbors(self, cell: Cell) -> Iterator[Cell]:
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                n = Cell(cell.x + dx, cell.y + dy)
                if self.in_bounds(n):
                    yield n

    def get(self, cell: Cell) -> object:
        return self.cells.get(cell, "unknown")

    def iter_cells(self) -> Iterable[Cell]:
        for y in range(self.origin_y, self.origin_y + self.height):
            for x in range(self.origin_x, self.origin_x + self.width):
                yield Cell(x, y)

    def covered_cells(self) -> set[Cell]:
        return {c for c, v in self.cells.items() if v == "covered"}

    def flagged_cells(self) -> set[Cell]:
        return {c for c, v in self.cells.items() if v == "flagged"}

    def number_cells(self) -> dict[Cell, int]:
        out: dict[Cell, int] = {}
        for c, v in self.cells.items():
            if isinstance(v, int) and 0 <= v <= 8:
                out[c] = v
        return out


def parse_square_class(class_name: str) -> object:
    class_name = class_name or ""
    if "bombflagged" in class_name or "flag" in class_name:
        return "flagged"
    m = _OPEN_RE.search(class_name)
    if m:
        return int(m.group(1))
    if "blank" in class_name:
        return "covered"
    if "square" in class_name:
        # Default tile state on minesweeperonline is usually "square blank", but
        # treat any unrecognized square as covered to stay robust to class changes.
        return "covered"
    if "open" in class_name:
        return 0
    if "bomb" in class_name:
        return "mine"
    return "unknown"


def render_board_for_llm(board: BoardSnapshot) -> str:
    """
    Render a board snapshot as a compact ASCII grid for debugging.

    Legend:
      # = covered, F = flagged, M = mine, ? = unknown, 0-8 = open numbers
    """

    def cell_char(v: object) -> str:
        if v == "covered":
            return "#"
        if v == "flagged":
            return "F"
        if v == "mine":
            return "M"
        if v == "unknown":
            return "?"
        if isinstance(v, int) and 0 <= v <= 8:
            return str(v)
        return "?"

    xs = list(range(board.min_x, board.max_x + 1))
    ys = list(range(board.min_y, board.max_y + 1))
    header = "    " + " ".join(f"{x:>2}" for x in xs)
    lines = [
        f"origin=({board.min_x},{board.min_y}) size={board.width}x{board.height}",
        header,
    ]
    for y in ys:
        row = [cell_char(board.get(Cell(x, y))) for x in xs]
        lines.append(f"{y:>3} " + " ".join(f"{c:>2}" for c in row))
    return "\n".join(lines)
