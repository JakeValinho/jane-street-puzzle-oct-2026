from dataclasses import dataclass
from collections import Counter, defaultdict, deque
from typing import Any

from .puzzle import N, from_index, index, neighbors


@dataclass
class BoardState:
    assignments: list[Any]
    manual_capitols: dict[int, int]

    @classmethod
    def from_snapshot(cls, data: dict) -> "BoardState":
        assignments = data.get("assignments")
        if not isinstance(assignments, list) or len(assignments) != N * N:
            raise ValueError(f"assignments must contain exactly {N * N} cells")

        normalized = []
        for value in assignments:
            if value is None:
                normalized.append(None)
            else:
                normalized.append(int(value))

        caps = {}
        for state_id, cell_index in (data.get("manualCapitols") or {}).items():
            caps[int(state_id)] = int(cell_index)

        return cls(assignments=normalized, manual_capitols=caps)

    @property
    def state_sizes(self) -> Counter:
        return Counter(x for x in self.assignments if x is not None)

    @property
    def state_cells(self):
        out = defaultdict(list)
        for i, state_id in enumerate(self.assignments):
            if state_id is not None:
                out[state_id].append(from_index(i))
        return out

    def state_at(self, cell):
        return self.assignments[index(cell)]

    def current_size(self, state_id):
        return self.state_sizes[state_id]

    def is_unassigned(self, cell):
        return self.state_at(cell) is None

    def marked_capitol_cell(self, state_id):
        i = self.manual_capitols.get(state_id)
        return None if i is None else from_index(i)

    def state_can_still_connect(self, state_id):
        cells = self.state_cells.get(state_id, [])
        if len(cells) <= 1:
            return True

        allowed = set()
        for i, owner in enumerate(self.assignments):
            if owner is None or owner == state_id:
                allowed.add(from_index(i))

        start = cells[0]
        seen = {start}
        q = deque([start])
        while q:
            cur = q.popleft()
            for nxt in neighbors(cur):
                if nxt in allowed and nxt not in seen:
                    seen.add(nxt)
                    q.append(nxt)

        return all(cell in seen for cell in cells)
