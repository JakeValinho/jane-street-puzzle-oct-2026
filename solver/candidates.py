from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable
import heapq
import time
from collections import defaultdict

from ortools.sat.python import cp_model

from .constraints import CELL_MIN_SIZES, forced_singleton_capitols, singleton_capitol_candidates
from .model import BoardState
from .puzzle import CLUES, DIRS, N, from_index, in_bounds, index


@dataclass(frozen=True)
class Symmetry:
    name: str
    mapping: tuple[int, ...]  # -1 means the image is outside the board

    @property
    def fixed(self):
        return tuple(i for i, j in enumerate(self.mapping) if i == j)


@dataclass
class DomainResult:
    state_id: int
    exact: bool
    feasible: bool
    reason: str
    witness: set[tuple[int, int]] | None
    forced_cells: set[tuple[int, int]]
    min_size: int | None
    max_size: int | None
    solver_calls: int
    excluded_cells: set[tuple[int, int]] = field(default_factory=set)
    branch_cell: tuple[int, int] | None = None
    candidate_shapes: list[set[tuple[int, int]]] = field(default_factory=list)
    candidate_count_exact: bool = False
    possible_symmetry_count: int | None = None

    def to_dict(self):
        def cells(xs):
            return [[r + 1, c + 1] for r, c in sorted(xs)]

        return {
            "state_id": self.state_id,
            "exact": self.exact,
            "feasible": self.feasible,
            "reason": self.reason,
            "witness": None if self.witness is None else cells(self.witness),
            "forced_cells": cells(self.forced_cells),
            "excluded_cells": cells(self.excluded_cells),
            "min_size": self.min_size,
            "max_size": self.max_size,
            "solver_calls": self.solver_calls,
            "branch_cell": None if self.branch_cell is None else [self.branch_cell[0] + 1, self.branch_cell[1] + 1],
            "candidate_shapes": [cells(shape) for shape in self.candidate_shapes],
            "candidate_count_exact": self.candidate_count_exact,
            "possible_symmetry_count": self.possible_symmetry_count,
        }


def _mapping_from_transform(fn: Callable[[int, int], tuple[int, int]]):
    mapping = []
    for r in range(N):
        for c in range(N):
            rr, cc = fn(r, c)
            mapping.append(index((rr, cc)) if in_bounds((rr, cc)) else -1)
    return tuple(mapping)


@lru_cache(maxsize=1)
def all_symmetries():
    """All non-identity square-grid isometries that could stabilize a state in the board."""
    out = []
    seen = set()

    def add(name, fn):
        mapping = _mapping_from_transform(fn)
        if mapping in seen:
            return
        seen.add(mapping)
        out.append(Symmetry(name, mapping))

    # Reflections. Axis positions may be integer or half-integer.
    for s in range(2 * N - 1):
        add(f"vertical c={s}/2", lambda r, c, s=s: (r, s - c))
        add(f"horizontal r={s}/2", lambda r, c, s=s: (s - r, c))
        add(f"anti-diagonal r+c={s}", lambda r, c, s=s: (s - c, s - r))

    for k in range(-(N - 1), N):
        add(f"diagonal c-r={k}", lambda r, c, k=k: (c - k, r + k))

    # 180-degree rotations around every integer/half-integer center.
    for sr in range(2 * N - 1):
        for sc in range(2 * N - 1):
            add(f"rotate180 center=({sr}/2,{sc}/2)", lambda r, c, sr=sr, sc=sc: (sr - r, sc - c))

    # 90-degree rotations. A lattice-preserving quarter turn requires the
    # doubled row/column coordinates of the center to have the same parity.
    for sr in range(2 * N - 1):
        for sc in range(2 * N - 1):
            if (sr - sc) % 2:
                continue
            add(
                f"rotate90 center=({sr}/2,{sc}/2)",
                lambda r, c, sr=sr, sc=sc: ((sr - sc) // 2 + c, (sr + sc) // 2 - r),
            )

    return tuple(out)


def _connected(cells):
    if not cells:
        return False
    cells = set(cells)
    start = next(iter(cells))
    seen = {start}
    stack = [start]
    while stack:
        r, c = stack.pop()
        for dr, dc in DIRS:
            nxt = (r + dr, c + dc)
            if nxt in cells and nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return len(seen) == len(cells)


def _normalize(cells):
    min_r = min(r for r, _ in cells)
    min_c = min(c for _, c in cells)
    return {(r - min_r, c - min_c) for r, c in cells}, min_r, min_c


_SHAPE_TRANSFORMS = (
    ("rotate90", lambda r, c: (-c, r)),
    ("rotate180", lambda r, c: (-r, -c)),
    ("rotate270", lambda r, c: (c, -r)),
    ("reflect-horizontal", lambda r, c: (-r, c)),
    ("reflect-vertical", lambda r, c: (r, -c)),
    ("reflect-diagonal", lambda r, c: (c, r)),
    ("reflect-antidiagonal", lambda r, c: (-c, -r)),
)


def analyze_shape(cells):
    """Authoritative geometry check, including the puzzle's capitol definition."""
    cells = set(cells)
    if not cells or not _connected(cells):
        return {"valid": False, "capitol": None, "symmetries": []}

    norm, min_r, min_c = _normalize(cells)
    symmetries = []

    for name, fn in _SHAPE_TRANSFORMS:
        raw = {fn(r, c) for r, c in norm}
        raw_min_r = min(r for r, _ in raw)
        raw_min_c = min(c for _, c in raw)
        mapped = {(r - raw_min_r, c - raw_min_c) for r, c in raw}
        if mapped != norm:
            continue

        fixed = []
        for r, c in norm:
            rr, cc = fn(r, c)
            image = (rr - raw_min_r, cc - raw_min_c)
            if image == (r, c):
                fixed.append((r, c))
        symmetries.append((name, fixed))

    if not symmetries:
        return {"valid": False, "capitol": None, "symmetries": []}

    capitol = None
    if all(len(fixed) == 1 for _, fixed in symmetries):
        first = symmetries[0][1][0]
        if all(fixed[0] == first for _, fixed in symmetries):
            capitol = (first[0] + min_r, first[1] + min_c)

    return {
        "valid": True,
        "capitol": capitol,
        "symmetries": [name for name, _ in symmetries],
    }


class CandidateModel:
    def __init__(self, board: BoardState, state_id: int, time_limit=2.0):
        self.board = board
        self.state_id = state_id
        self.time_limit = max(0.01, float(time_limit))
        self.deadline = time.monotonic() + self.time_limit
        self.required = set(board.state_cells.get(state_id, []))
        self.reason = ""
        self.other_assigned = {
            from_index(i)
            for i, owner in enumerate(board.assignments)
            if owner is not None and owner != state_id
        }
        self.forbidden = set(board.forbidden_by_state.get(state_id, set()))
        self.forced_singletons = forced_singleton_capitols(board)
        self.forced_capitol = self._derive_forced_capitol()
        self.exact_singleton = None
        self.solver_calls = 0

        overlap = self.required & self.forced_singletons
        if overlap:
            if len(self.required) != 1 or len(overlap) != 1:
                self.reason = "This state contains a square already forced to be a singleton capitol."
            else:
                self.exact_singleton = next(iter(overlap))
                self.forced_capitol = self.exact_singleton

        if not self.required:
            self.reason = "The state has no selected cells."

        self.plausible_symmetries = []
        if not self.reason:
            self.plausible_symmetries = [
                sym for sym in all_symmetries()
                if self._symmetry_precheck(sym)
            ]
            if not self.plausible_symmetries:
                self.reason = "No rotation or reflection can extend the currently selected cells."

    def _remaining_time(self):
        return max(0.0, self.deadline - time.monotonic())

    def _new_solver(self):
        remaining = self._remaining_time()
        if remaining <= 0:
            return None
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max(0.01, remaining)
        solver.parameters.num_search_workers = 8
        return solver

    def _derive_forced_capitol(self):
        marked = self.board.marked_capitol_cell(self.state_id)
        zeros = [cell for cell in self.required if CLUES.get(cell) == 0]

        if len(zeros) > 1:
            self.reason = "The state already contains two clue-0 squares."
            return None

        if marked is not None and self.board.state_at(marked) != self.state_id:
            self.reason = "The state's marked capitol is not inside the state."
            return None

        if zeros:
            zero = zeros[0]
            if marked is not None and marked != zero:
                self.reason = "A clue-0 square conflicts with the manually marked capitol."
                return None
            return zero

        return marked

    def _symmetry_precheck(self, sym: Symmetry):
        if self.forced_capitol is not None:
            cap_i = index(self.forced_capitol)
            if sym.mapping[cap_i] != cap_i:
                return False

        # The orbit closure of every required cell must stay in the board
        # and cannot hit a square already committed to another state.
        forbidden_i = {index(cell) for cell in self.other_assigned | self.forbidden}
        for cell in self.required:
            cur = index(cell)
            seen = set()
            while cur not in seen:
                seen.add(cur)
                nxt = sym.mapping[cur]
                if nxt < 0 or nxt in forbidden_i:
                    return False
                cur = nxt

        if self.forced_capitol is not None:
            # The chosen symmetry must fix exactly one selected square if a
            # capitol is already known.
            cap_i = index(self.forced_capitol)
            for cell in self.required:
                i = index(cell)
                if i != cap_i and sym.mapping[i] == i:
                    return False

        return True

    def _build_model(self, force_include=None, force_exclude=None, objective=None):
        if self.reason:
            return None
        if self.exact_singleton is not None:
            # Still use the generic model so queries remain consistent.
            pass

        model = cp_model.CpModel()
        x = [model.NewBoolVar(f"x_{i}") for i in range(N * N)]
        size = model.NewIntVar(1, N * N, "size")
        model.Add(size == sum(x))

        for cell in self.required:
            model.Add(x[index(cell)] == 1)

        for cell in self.other_assigned:
            model.Add(x[index(cell)] == 0)
        for cell in self.forbidden:
            model.Add(x[index(cell)] == 0)

        # A square forced by a clue-1 deduction to be a singleton capitol can
        # never be absorbed into another state.
        for cell in self.forced_singletons:
            if cell not in self.required:
                model.Add(x[index(cell)] == 0)

        if self.exact_singleton is not None:
            model.Add(size == 1)

        if force_include is not None:
            model.Add(x[index(force_include)] == 1)
        if force_exclude is not None:
            model.Add(x[index(force_exclude)] == 0)

        # Every selected cell carries any proven lower bound on the size of
        # its state.
        for cell, minimum in CELL_MIN_SIZES.items():
            model.Add(size >= minimum * x[index(cell)])

        # At most one clue-0 square can belong to a state.
        zero_indices = [index(cell) for cell, value in CLUES.items() if value == 0]
        model.Add(sum(x[i] for i in zero_indices) <= 1)

        if self.forced_capitol is not None:
            cap = self.forced_capitol
            if CLUES.get(cap, 0) > 0:
                self.reason = "A positive clue cannot be the capitol of its state."
                return None
            model.Add(x[index(cap)] == 1)
            # A capitol-bearing state must have odd cardinality.
            rem = model.NewIntVar(0, 1, "size_mod_2")
            model.AddModuloEquality(rem, size, 2)
            model.Add(rem == 1)

        selectors = []
        for s_idx, sym in enumerate(self.plausible_symmetries):
            y = model.NewBoolVar(f"sym_{s_idx}")
            selectors.append(y)

            for i, j in enumerate(sym.mapping):
                if j < 0:
                    model.Add(x[i] == 0).OnlyEnforceIf(y)
                elif j != i:
                    model.Add(x[i] == x[j]).OnlyEnforceIf(y)

            fixed = set(sym.fixed)
            if self.forced_capitol is not None:
                cap_i = index(self.forced_capitol)
                if cap_i not in fixed:
                    model.Add(y == 0)
                else:
                    for j in fixed:
                        if j != cap_i:
                            model.Add(x[j] == 0).OnlyEnforceIf(y)
                    for zi in zero_indices:
                        if zi != cap_i:
                            model.Add(x[zi] == 0).OnlyEnforceIf(y)
            else:
                # If a clue-0 square is selected, it must be the unique fixed
                # square of every active symmetry.
                for zi in zero_indices:
                    if zi not in fixed:
                        model.Add(x[zi] == 0).OnlyEnforceIf(y)
                    else:
                        for j in fixed:
                            if j != zi:
                                model.Add(x[zi] + x[j] <= 1).OnlyEnforceIf(y)

        if not selectors:
            return None
        model.Add(sum(selectors) >= 1)

        # Single-commodity flow forces the selected cells to be orthogonally connected.
        root = index(next(iter(self.required)))
        M = N * N
        incoming = [[] for _ in range(N * N)]
        outgoing = [[] for _ in range(N * N)]

        for r in range(N):
            for c in range(N):
                u = index((r, c))
                for dr, dc in ((1, 0), (0, 1)):
                    rr, cc = r + dr, c + dc
                    if not in_bounds((rr, cc)):
                        continue
                    v = index((rr, cc))
                    for a, b in ((u, v), (v, u)):
                        f = model.NewIntVar(0, M, f"flow_{a}_{b}")
                        model.Add(f <= M * x[a])
                        model.Add(f <= M * x[b])
                        outgoing[a].append(f)
                        incoming[b].append(f)

        for i in range(N * N):
            in_expr = sum(incoming[i])
            out_expr = sum(outgoing[i])
            if i == root:
                model.Add(out_expr - in_expr == size - 1)
            else:
                model.Add(in_expr - out_expr == x[i])

        if objective == "min":
            model.Minimize(size)
        elif objective == "max":
            model.Maximize(size)

        return model, x, size

    def _complete_partition_matches_clues(self, assignments):
        if any(owner is None for owner in assignments):
            return True

        state_cells = defaultdict(set)
        for i, owner in enumerate(assignments):
            state_cells[owner].add(from_index(i))

        sizes = {}
        capitols = []
        for owner, cells in state_cells.items():
            geom = analyze_shape(cells)
            if not geom["valid"]:
                return False

            # A user-marked capitol is a hard constraint. On a complete
            # partition it must agree exactly with the capitol implied by
            # every nontrivial symmetry of that state's final shape.
            marked = self.board.marked_capitol_cell(owner)
            if marked is not None and geom["capitol"] != marked:
                return False

            sizes[owner] = len(cells)
            if geom["capitol"] is not None:
                capitols.append(geom["capitol"])

        # A marked capitol for a state that does not exist in the completed
        # partition is malformed/inconsistent input.
        if any(owner not in state_cells for owner in self.board.manual_capitols):
            return False

        if not capitols:
            return False

        inf = 10**18
        dist = [inf] * (N * N)
        heap = []
        for cell in capitols:
            i = index(cell)
            if dist[i] != 0:
                dist[i] = 0
                heapq.heappush(heap, (0, i))

        while heap:
            d, u = heapq.heappop(heap)
            if d != dist[u]:
                continue
            ur, uc = from_index(u)
            owner_u = assignments[u]
            for dr, dc in DIRS:
                vr, vc = ur + dr, uc + dc
                if not in_bounds((vr, vc)):
                    continue
                v = index((vr, vc))
                owner_v = assignments[v]
                weight = min(sizes[owner_u], sizes[owner_v])
                nd = d + weight
                if nd < dist[v]:
                    dist[v] = nd
                    heapq.heappush(heap, (nd, v))

        return all(dist[index(cell)] == target for cell, target in CLUES.items())

    def _candidate_is_globally_legal(self, cells):
        cells = set(cells)
        geom = analyze_shape(cells)
        if not geom["valid"]:
            return False

        capitol = geom["capitol"]
        if self.forced_capitol is not None and capitol != self.forced_capitol:
            return False

        zeros = [cell for cell in cells if CLUES.get(cell) == 0]
        if len(zeros) > 1:
            return False
        if zeros and capitol != zeros[0]:
            return False
        if capitol is not None and CLUES.get(capitol, 0) > 0:
            return False

        # If this candidate has a capitol, paths that stay inside the state
        # give hard upper bounds on clue distances because every internal
        # edge costs exactly the state's size. The same applies to a clue
        # immediately outside the state: entering the state costs at most
        # its size, then the internal route reaches the capitol.
        if capitol is not None:
            size = len(cells)
            dist = {capitol: 0}
            queue = [capitol]
            for cur in queue:
                r, c = cur
                for dr, dc in DIRS:
                    nxt = (r + dr, c + dc)
                    if nxt in cells and nxt not in dist:
                        dist[nxt] = dist[cur] + 1
                        queue.append(nxt)

            for clue_cell, target in CLUES.items():
                if target == 0:
                    continue
                if clue_cell in cells:
                    if target > dist[clue_cell] * size:
                        return False
                else:
                    entry_lengths = [
                        dist[nbr] + 1
                        for nbr in ((clue_cell[0] + dr, clue_cell[1] + dc) for dr, dc in DIRS)
                        if nbr in dist
                    ]
                    if entry_lengths and target > min(entry_lengths) * size:
                        return False

        # Update only this state's completion and ensure each clue-1 still has
        # at least one possible adjacent singleton capitol.
        temp_assignments = self.board.assignments[:]
        for cell in cells:
            temp_assignments[index(cell)] = self.state_id
        temp = BoardState(
            temp_assignments,
            dict(self.board.manual_capitols),
            {
                state_id: set(cells)
                for state_id, cells in self.board.forbidden_by_state.items()
            },
        )

        # A completion of this state may not wall off another already-started
        # state so that its selected cells can no longer be joined.
        for other_state_id in temp.state_cells:
            if other_state_id == self.state_id:
                continue
            if not temp.state_can_still_connect(other_state_id):
                return False

        for clue_cell, value in CLUES.items():
            if value == 1 and not singleton_capitol_candidates(temp, clue_cell):
                return False

        # If this candidate completes the whole board, enforce the full puzzle:
        # every state's exact capitol status plus multi-source shortest-path
        # travel costs must match every clue.
        if not self._complete_partition_matches_clues(temp_assignments):
            return False

        return True

    def find(self, force_include=None, force_exclude=None, objective=None):
        built = self._build_model(force_include, force_exclude, objective)
        if built is None:
            return None, bool(self.reason)

        model, x, size = built
        solver = self._new_solver()
        if solver is None:
            return None, False

        exact = True
        rejected = 0
        while rejected < 250:
            if self._remaining_time() <= 0:
                return None, False
            solver = self._new_solver()
            if solver is None:
                return None, False
            self.solver_calls += 1
            status = solver.Solve(model)
            if status == cp_model.INFEASIBLE:
                return None, True
            if status in (cp_model.UNKNOWN, cp_model.MODEL_INVALID):
                # UNKNOWN is a timeout/resource limit; MODEL_INVALID is a
                # modelling/runtime error. Neither may be used as a proof of
                # impossibility.
                return None, False
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                return None, False

            if objective is not None and status != cp_model.OPTIMAL:
                exact = False

            selected_i = [i for i in range(N * N) if solver.Value(x[i])]
            cells = {from_index(i) for i in selected_i}
            if self._candidate_is_globally_legal(cells):
                return cells, exact

            # Exclude this exact shape, not merely this flow assignment.
            diff_terms = []
            selected_set = set(selected_i)
            for i in range(N * N):
                diff_terms.append(1 - x[i] if i in selected_set else x[i])
            model.Add(sum(diff_terms) >= 1)
            rejected += 1

        return None, False



    def enumerate_legal(self, limit=4):
        """
        Enumerate a small exact prefix of legal complete shapes for this state.

        Returns (shapes, complete). complete=True means the solver proved there
        are no additional legal shapes beyond those returned.
        """
        built = self._build_model()
        if built is None:
            return [], bool(self.reason)

        model, x, _size = built
        found = []
        rejected = 0
        complete = False

        while rejected < 600 and len(found) <= limit:
            solver = self._new_solver()
            if solver is None:
                break
            self.solver_calls += 1
            status = solver.Solve(model)

            if status == cp_model.INFEASIBLE:
                complete = True
                break
            if status == cp_model.MODEL_INVALID:
                # A model error is never evidence that the candidate domain
                # has been exhausted.
                return found[:limit], False
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                break

            selected_i = [i for i in range(N * N) if solver.Value(x[i])]
            selected_set = set(selected_i)
            cells = {from_index(i) for i in selected_i}

            # Always block the raw shape before testing it so rejected shapes
            # cannot recur because of a different flow/symmetry assignment.
            diff_terms = [
                1 - x[i] if i in selected_set else x[i]
                for i in range(N * N)
            ]
            model.Add(sum(diff_terms) >= 1)

            if self._candidate_is_globally_legal(cells):
                found.append(cells)
                if len(found) > limit:
                    return found[:limit], False
            else:
                rejected += 1

        return found[:limit], complete and len(found) <= limit


def analyze_state_domain(
    board: BoardState,
    state_id: int,
    time_limit=1.0,
    max_forced_checks=None,
    max_exclusion_checks=0,
    compute_bounds=True,
    enumerate_limit=0,
):
    """
    Analyze the legal completion domain of a partially known state.

    The CP-SAT model is implicit. A square becomes forced only after the
    solver proves there is no legal completion excluding it. A square becomes
    excluded only after the solver proves there is no legal completion
    including it. Optional small-domain enumeration is exact only when the
    solver reaches infeasibility after listing all candidates.
    """
    cm = CandidateModel(board, state_id, time_limit=time_limit)
    symmetry_count = len(cm.plausible_symmetries)

    if cm.reason:
        return DomainResult(
            state_id=state_id,
            exact=True,
            feasible=False,
            reason=cm.reason,
            witness=None,
            forced_cells=set(),
            min_size=None,
            max_size=None,
            solver_calls=cm.solver_calls,
            possible_symmetry_count=symmetry_count,
        )

    witness, exact = cm.find()
    if witness is None:
        reason = cm.reason or "No connected symmetric completion satisfies the currently encoded puzzle rules."
        return DomainResult(
            state_id=state_id,
            exact=exact,
            feasible=False,
            reason=reason,
            witness=None,
            forced_cells=set(),
            min_size=None,
            max_size=None,
            solver_calls=cm.solver_calls,
            possible_symmetry_count=symmetry_count,
        )

    required = set(cm.required)
    forced = set(required)
    excluded = set(cm.forbidden) | set(cm.other_assigned)
    all_exact = exact

    # Only witness cells can possibly be forced. Prioritize cells adjacent to
    # already-certain cells, then cells carrying strong clue-derived bounds.
    branch_candidates = []
    extras = sorted(
        witness - required,
        key=lambda cell: (
            0 if any(
                n in required
                for n in (
                    (cell[0] + 1, cell[1]),
                    (cell[0] - 1, cell[1]),
                    (cell[0], cell[1] + 1),
                    (cell[0], cell[1] - 1),
                )
            ) else 1,
            -CELL_MIN_SIZES.get(cell, 1),
            cell,
        ),
    )
    if max_forced_checks is not None:
        extras = extras[:max_forced_checks]

    for cell in extras:
        alternate, proof_exact = cm.find(force_exclude=cell)
        all_exact = all_exact and proof_exact
        if alternate is None and proof_exact:
            forced.add(cell)
        elif alternate is not None:
            branch_candidates.append(cell)

    # Prove exclusions by asking whether a legal completion can include a
    # currently unassigned square. We prioritize the frontier around the
    # certain core because those exclusions propagate connectivity fastest.
    if max_exclusion_checks:
        candidate_cells = []
        seen = set()
        frontier = set()
        for r, col in required | forced:
            for dr, dc in DIRS:
                cell = (r + dr, col + dc)
                if in_bounds(cell):
                    frontier.add(cell)

        ordered = list(sorted(frontier))
        ordered += [
            cell for cell in sorted(CELL_MIN_SIZES, key=lambda x: -CELL_MIN_SIZES[x])
            if cell not in frontier
        ]
        ordered += [
            from_index(i) for i in range(N * N)
            if from_index(i) not in frontier and from_index(i) not in CELL_MIN_SIZES
        ]

        for cell in ordered:
            if len(candidate_cells) >= max_exclusion_checks:
                break
            if cell in seen or cell in required or cell in excluded:
                continue
            seen.add(cell)
            candidate_cells.append(cell)

        for cell in candidate_cells:
            possible, proof_exact = cm.find(force_include=cell)
            all_exact = all_exact and proof_exact
            if possible is None and proof_exact:
                excluded.add(cell)

    min_candidate = max_candidate = None
    min_exact = max_exact = True
    if compute_bounds:
        min_candidate, min_exact = cm.find(objective="min")
        max_candidate, max_exact = cm.find(objective="max")
        all_exact = all_exact and min_exact and max_exact

    candidate_shapes = []
    candidate_count_exact = False
    if enumerate_limit:
        candidate_shapes, candidate_count_exact = cm.enumerate_legal(limit=enumerate_limit)

    return DomainResult(
        state_id=state_id,
        exact=all_exact,
        feasible=True,
        reason="Legal connected symmetric completions exist.",
        witness=witness,
        forced_cells=forced,
        excluded_cells=excluded,
        min_size=None if min_candidate is None or not min_exact else len(min_candidate),
        max_size=None if max_candidate is None or not max_exact else len(max_candidate),
        solver_calls=cm.solver_calls,
        branch_cell=branch_candidates[0] if branch_candidates else None,
        candidate_shapes=candidate_shapes,
        candidate_count_exact=candidate_count_exact,
        possible_symmetry_count=symmetry_count,
    )




def probe_state_membership(board: BoardState, state_id: int, cell, time_limit=0.25):
    """
    Ask whether an existing known state can legally contain an unassigned cell.

    Returns (possible, exact, witness). possible=False with exact=True is a
    proof that the cell cannot belong to that state. A timeout or model error
    returns exact=False and must never be used as an exclusion proof.
    """
    if board.state_at(cell) == state_id:
        return True, True, set(board.state_cells.get(state_id, []))
    if board.state_at(cell) is not None and board.state_at(cell) != state_id:
        return False, True, None
    if board.is_forbidden(state_id, cell):
        return False, True, None

    cm = CandidateModel(board, state_id, time_limit=time_limit)
    if cm.reason:
        return False, True, None

    witness, exact = cm.find(force_include=cell)
    return witness is not None, exact, witness


def probe_fresh_state(board: BoardState, cell, time_limit=0.25):
    """
    Ask whether cell can be the first certain cell of some not-yet-named
    state, distinct from every state already present on the board.

    This bridges clue-derived facts about an unassigned cell into the state
    shape solver. If all existing states are impossible but this probe
    succeeds, naming a new state at this cell is a deduction, not a guess.
    """
    if board.state_at(cell) is not None:
        return False, True, None

    temp = board.clone()
    state_id = temp.next_state_id()
    try:
        temp.assign(state_id, cell)
    except ValueError:
        return False, True, None

    cm = CandidateModel(temp, state_id, time_limit=time_limit)
    if cm.reason:
        return False, True, None

    witness, exact = cm.find()
    return witness is not None, exact, witness
