from dataclasses import dataclass, field
from collections import Counter, defaultdict, deque
from typing import Any

from .puzzle import N, from_index, index, neighbors


@dataclass
class BoardState:
    assignments: list[Any]
    manual_capitols: dict[int, int]
    # Internal search-only negative information:
    # state_id -> cells that this state is proven/assumed not to contain.
    forbidden_by_state: dict[int, set[tuple[int, int]]] = field(default_factory=dict)

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

        forbidden = {}
        for state_id, raw_cells in (data.get("forbiddenByState") or {}).items():
            sid = int(state_id)
            cells = set()
            for raw in raw_cells:
                if isinstance(raw, int):
                    cells.add(from_index(raw))
                elif isinstance(raw, (list, tuple)) and len(raw) == 2:
                    # JSON form is 1-indexed for readability.
                    cells.add((int(raw[0]) - 1, int(raw[1]) - 1))
            forbidden[sid] = cells

        return cls(
            assignments=normalized,
            manual_capitols=caps,
            forbidden_by_state=forbidden,
        )

    def clone(self):
        return BoardState(
            assignments=self.assignments[:],
            manual_capitols=dict(self.manual_capitols),
            forbidden_by_state={
                state_id: set(cells)
                for state_id, cells in self.forbidden_by_state.items()
            },
        )

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

    def is_forbidden(self, state_id, cell):
        return cell in self.forbidden_by_state.get(state_id, set())

    def forbid(self, state_id, cell):
        self.forbidden_by_state.setdefault(state_id, set()).add(cell)

    def assign(self, state_id, cell):
        if self.is_forbidden(state_id, cell):
            raise ValueError(f"{cell} is forbidden from State {state_id}")
        owner = self.state_at(cell)
        if owner is not None and owner != state_id:
            raise ValueError(f"{cell} already belongs to State {owner}")
        self.assignments[index(cell)] = state_id

    def next_state_id(self):
        ids = [x for x in self.assignments if x is not None]
        ids += list(self.manual_capitols)
        ids += list(self.forbidden_by_state)
        return max(ids, default=0) + 1

    def marked_capitol_cell(self, state_id):
        i = self.manual_capitols.get(state_id)
        return None if i is None else from_index(i)

    def state_can_still_connect(self, state_id):
        cells = self.state_cells.get(state_id, [])
        if len(cells) <= 1:
            return True

        allowed = set()
        for i, owner in enumerate(self.assignments):
            cell = from_index(i)
            if owner == state_id:
                allowed.add(cell)
            elif owner is None and not self.is_forbidden(state_id, cell):
                allowed.add(cell)

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
