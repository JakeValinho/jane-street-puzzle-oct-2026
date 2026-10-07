from __future__ import annotations

from dataclasses import dataclass, field
import time

from .candidates import (
    CandidateModel,
    DomainResult,
    analyze_shape,
    analyze_state_domain,
    probe_fresh_state,
    probe_state_membership,
)
from .constraints import CELL_MIN_SIZES, is_locked_singleton_capitol, singleton_capitol_candidates
from .model import BoardState
from .puzzle import CLUES, N, from_index, index, label


@dataclass
class StateKnowledge:
    state_id: int
    certain_cells: set[tuple[int, int]] = field(default_factory=set)
    excluded_cells: set[tuple[int, int]] = field(default_factory=set)
    capitol: tuple[int, int] | None = None
    min_size: int = 1
    max_size: int = N * N
    parity: str | None = None
    possible_symmetry_count: int | None = None
    candidate_shapes: list[set[tuple[int, int]]] = field(default_factory=list)
    candidate_count_exact: bool = False

    def to_dict(self):
        def cells(values):
            return [[r + 1, c + 1] for r, c in sorted(values)]

        return {
            "state_id": self.state_id,
            "certain_cells": cells(self.certain_cells),
            "excluded_cells": cells(self.excluded_cells),
            "capitol": None if self.capitol is None else [self.capitol[0] + 1, self.capitol[1] + 1],
            "min_size": self.min_size,
            "max_size": self.max_size,
            "parity": self.parity,
            "possible_symmetry_count": self.possible_symmetry_count,
            "candidate_shapes": [cells(shape) for shape in self.candidate_shapes],
            "candidate_count_exact": self.candidate_count_exact,
        }


@dataclass
class CellKnowledge:
    cell: tuple[int, int]
    forced_state: int | None
    must_be_capitol: bool
    min_state_size: int
    cannot_be_in: set[int] = field(default_factory=set)
    possible_known_states: set[int] = field(default_factory=set)
    new_state_possible: bool = True

    def to_dict(self):
        return {
            "cell": [self.cell[0] + 1, self.cell[1] + 1],
            "forced_state": self.forced_state,
            "must_be_capitol": self.must_be_capitol,
            "min_state_size": self.min_state_size,
            "cannot_be_in": sorted(self.cannot_be_in),
            "possible_known_states": sorted(self.possible_known_states),
            "new_state_possible": self.new_state_possible,
        }


@dataclass
class KnowledgeResult:
    board: BoardState
    states: dict[int, StateKnowledge]
    cells: dict[tuple[int, int], CellKnowledge]
    actions: list[dict]
    facts: list[dict]
    contradiction: str | None
    rounds: int
    fixed_point: bool
    timed_out: bool

    def to_dict(self):
        return {
            "states": {
                str(state_id): knowledge.to_dict()
                for state_id, knowledge in sorted(self.states.items())
            },
            "cells": {
                f"{r + 1},{c + 1}": knowledge.to_dict()
                for (r, c), knowledge in sorted(self.cells.items())
            },
            "actions": self.actions,
            "facts": self.facts,
            "contradiction": self.contradiction,
            "rounds": self.rounds,
            "fixed_point": self.fixed_point,
            "timed_out": self.timed_out,
            "internal_board": {
                "assignments": self.board.assignments,
                "manualCapitols": {
                    str(state_id): cell_index
                    for state_id, cell_index in self.board.manual_capitols.items()
                },
                "forbiddenByState": {
                    str(state_id): [[r + 1, c + 1] for r, c in sorted(cells)]
                    for state_id, cells in self.board.forbidden_by_state.items()
                },
            },
        }


def _cell_json(cell):
    return [cell[0] + 1, cell[1] + 1]


def _deadline_available(deadline):
    return deadline is None or time.monotonic() < deadline


def _remaining(deadline, fallback=0.3):
    if deadline is None:
        return fallback
    return max(0.0, deadline - time.monotonic())


def _lock_singleton(board: BoardState, cell):
    owner = board.state_at(cell)
    created = False
    if owner is None:
        owner = board.next_state_id()
        board.assign(owner, cell)
        created = True
    elif board.current_size(owner) != 1:
        raise ValueError(
            f"{label(cell)} must be a singleton capitol, but it is already part of State {owner} with multiple selected cells."
        )

    marked = board.marked_capitol_cell(owner)
    if marked is not None and marked != cell:
        raise ValueError(
            f"State {owner} must be the singleton at {label(cell)}, but its marked capitol is elsewhere."
        )

    board.manual_capitols[owner] = index(cell)
    newly_forbidden = []
    for i in range(N * N):
        other = from_index(i)
        if other == cell or board.is_forbidden(owner, other):
            continue
        board.forbid(owner, other)
        newly_forbidden.append(other)

    return owner, created, newly_forbidden


def _ensure_zero_anchor(board: BoardState, cell):
    owner = board.state_at(cell)
    created = False
    if owner is None:
        owner = board.next_state_id()
        board.assign(owner, cell)
        created = True

    marked = board.marked_capitol_cell(owner)
    if marked is not None and marked != cell:
        raise ValueError(
            f"{label(cell)} has clue 0, but State {owner}'s marked capitol is elsewhere."
        )

    board.manual_capitols[owner] = index(cell)
    return owner, created


def find_basic_contradiction(board: BoardState):
    for state_id, cells in board.state_cells.items():
        if any(board.is_forbidden(state_id, cell) for cell in cells):
            return f"State {state_id} contains a square that is proven/assumed not to belong to it."
        if not board.state_can_still_connect(state_id):
            return f"State {state_id} can no longer be connected through its own or still-available squares."

        available = 0
        for i, owner in enumerate(board.assignments):
            cell = from_index(i)
            if owner == state_id:
                available += 1
            elif owner is None and not board.is_forbidden(state_id, cell):
                available += 1

        minimum = _state_min_size(board, state_id)
        if available < minimum:
            return (
                f"State {state_id} needs at least {minimum} squares, but only "
                f"{available} cells remain available to it."
            )

        # Cheap geometric pruning: before invoking CP-SAT, make sure at least
        # one board-preserving nontrivial symmetry can still extend the
        # state's certain cells without crossing a known exclusion/other state.
        if len(cells) >= 2 or board.marked_capitol_cell(state_id) is not None:
            candidate_model = CandidateModel(board, state_id, time_limit=0.01)
            if candidate_model.reason:
                return (
                    f"State {state_id} has no possible symmetry extension: "
                    f"{candidate_model.reason}"
                )

    minimum_total_area = sum(
        _state_min_size(board, state_id)
        for state_id in board.state_cells
    )
    if minimum_total_area > N * N:
        return (
            f"Known distinct states require at least {minimum_total_area} cells "
            f"in total, but the board has only {N * N}."
        )

    zero_owner = {}
    for cell, value in CLUES.items():
        if value != 0:
            continue
        owner = board.state_at(cell)
        if owner is None:
            continue
        if owner in zero_owner and zero_owner[owner] != cell:
            return (
                f"State {owner} contains both {label(zero_owner[owner])} and {label(cell)}, "
                "but both clue-0 squares must be capitols."
            )
        zero_owner[owner] = cell

    for state_id, cap_index in board.manual_capitols.items():
        cap = from_index(cap_index)
        owner = board.state_at(cap)
        if owner is not None and owner != state_id:
            return f"State {state_id}'s marked capitol is owned by State {owner}."
        if CLUES.get(cap, 0) > 0:
            return f"{label(cap)} has a positive clue and cannot itself be a capitol."

    for clue_cell, value in CLUES.items():
        if value == 1 and not singleton_capitol_candidates(board, clue_cell):
            return f"The 1 at {label(clue_cell)} has no possible adjacent singleton capitol."

    return None


def _state_min_size(board: BoardState, state_id):
    cells = board.state_cells.get(state_id, [])
    bound = max([len(cells)] + [CELL_MIN_SIZES.get(cell, 1) for cell in cells])
    cap = board.marked_capitol_cell(state_id)
    if cap is not None and bound % 2 == 0:
        bound += 1
    return bound


def _is_structurally_constrained(board: BoardState, state_id, cells):
    return (
        len(cells) >= 2
        or board.marked_capitol_cell(state_id) is not None
        or bool(board.forbidden_by_state.get(state_id))
        or any(CLUES.get(cell) == 0 for cell in cells)
        or _state_min_size(board, state_id) > len(cells)
    )


def _domain_for_state(board, state_id, deadline, deep):
    cells = board.state_cells.get(state_id, [])
    if not _is_structurally_constrained(board, state_id, cells):
        return None

    seconds = min(0.45 if deep else 0.22, max(0.04, _remaining(deadline, 0.22)))
    enumerate_limit = 3 if deep and len(cells) >= 2 else 0

    return analyze_state_domain(
        board,
        state_id,
        time_limit=seconds,
        max_forced_checks=8 if deep else 4,
        max_exclusion_checks=8 if deep else 3,
        compute_bounds=True,
        enumerate_limit=enumerate_limit,
    )


def _shape_key(shape):
    return tuple(sorted(shape))


def _ownership_probe_cells(board: BoardState, deep=True):
    """
    Pick unassigned cells whose ownership carries the most information.

    Strong clue-derived size bounds come first, followed by cells on the
    frontier of already-known states. This is deliberately selective: owner
    probing is expensive, but unlike the old engine it lets deductions about
    currently unassigned cells feed back into the partition.
    """
    frontier = set()
    for cells in board.state_cells.values():
        for r, c in cells:
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                rr, cc = r + dr, c + dc
                if 0 <= rr < N and 0 <= cc < N and board.state_at((rr, cc)) is None:
                    frontier.add((rr, cc))

    unassigned = [
        from_index(i)
        for i, owner in enumerate(board.assignments)
        if owner is None
    ]

    def score(cell):
        return (
            -CELL_MIN_SIZES.get(cell, 1),
            0 if cell in frontier else 1,
            0 if cell in CLUES else 1,
            cell,
        )

    ranked = sorted(unassigned, key=score)

    # Always include the strongest lower-bound cells. Frontier-only cells are
    # useful later, but probing every blank square would destroy interactivity.
    informative = [
        cell for cell in ranked
        if CELL_MIN_SIZES.get(cell, 1) >= 3 or cell in frontier
    ]
    limit = 10 if deep else 4
    return informative[:limit]


def _apply_exact_shape(board, state_id, shape):
    shape = set(shape)
    newly_added = []
    newly_excluded = []

    geometry = analyze_shape(shape)
    if not geometry["valid"]:
        raise ValueError(f"Exact shape for State {state_id} is not a legal symmetric state.")
    inferred_capitol = geometry["capitol"]
    existing_capitol = board.marked_capitol_cell(state_id)
    if existing_capitol is not None and existing_capitol != inferred_capitol:
        raise ValueError(f"Exact shape for State {state_id} conflicts with its known capitol.")
    if inferred_capitol is not None:
        board.manual_capitols[state_id] = index(inferred_capitol)

    for cell in shape:
        if board.state_at(cell) != state_id:
            board.assign(state_id, cell)
            newly_added.append(cell)

    for i in range(N * N):
        cell = from_index(i)
        if cell in shape or board.is_forbidden(state_id, cell):
            continue
        if board.state_at(cell) == state_id:
            raise ValueError(
                f"Exact shape for State {state_id} excludes already-certain square {label(cell)}."
            )
        board.forbid(state_id, cell)
        newly_excluded.append(cell)

    return newly_added, newly_excluded


def build_knowledge(board: BoardState, domains=None):
    domains = domains or {}
    states = {}
    state_ids = sorted(board.state_cells)

    for state_id in state_ids:
        certain = set(board.state_cells[state_id])
        excluded = set(board.forbidden_by_state.get(state_id, set()))
        for i, owner in enumerate(board.assignments):
            if owner is not None and owner != state_id:
                excluded.add(from_index(i))

        domain = domains.get(state_id)
        minimum = _state_min_size(board, state_id)
        maximum = N * N - len(excluded)
        if domain is not None:
            if domain.min_size is not None:
                minimum = max(minimum, domain.min_size)
            if domain.max_size is not None:
                maximum = min(maximum, domain.max_size)
            excluded |= set(domain.excluded_cells)

        cap = board.marked_capitol_cell(state_id)
        states[state_id] = StateKnowledge(
            state_id=state_id,
            certain_cells=certain,
            excluded_cells=excluded,
            capitol=cap,
            min_size=minimum,
            max_size=maximum,
            parity="odd" if cap is not None else None,
            possible_symmetry_count=None if domain is None else domain.possible_symmetry_count,
            candidate_shapes=[] if domain is None else list(domain.candidate_shapes),
            candidate_count_exact=False if domain is None else domain.candidate_count_exact,
        )

    cells = {}
    known_ids = set(states)
    for i in range(N * N):
        cell = from_index(i)
        owner = board.state_at(cell)
        cannot = set()
        possible = set()

        if owner is not None:
            cannot = known_ids - {owner}
            possible = {owner}
            new_state_possible = False
        else:
            for state_id in known_ids:
                if board.is_forbidden(state_id, cell):
                    cannot.add(state_id)
                else:
                    possible.add(state_id)
            new_state_possible = True

        cells[cell] = CellKnowledge(
            cell=cell,
            forced_state=owner,
            must_be_capitol=CLUES.get(cell) == 0,
            min_state_size=CELL_MIN_SIZES.get(cell, 1),
            cannot_be_in=cannot,
            possible_known_states=possible,
            new_state_possible=new_state_possible,
        )

    return states, cells


def propagate_knowledge(
    board: BoardState,
    deadline=None,
    max_rounds=12,
    deep=True,
    collect_actions=True,
    probe_ownership=True,
):
    """
    Reach a fixed point using only proved consequences.

    The input board is never mutated. The returned board is an internal
    hypothetical copy containing every forced fact derived during propagation.
    No unresolved branch is ever committed.
    """
    work = board.clone()
    actions = []
    facts = []
    last_domains = {}
    seen_action_keys = set()
    seen_fact_keys = set()
    fixed_point = False
    timed_out = False

    def add_action(action):
        key = (
            action.get("type"),
            action.get("state"),
            tuple(action.get("cell", [])),
            tuple(tuple(x) for x in action.get("cells", [])),
        )
        if key in seen_action_keys:
            return
        seen_action_keys.add(key)
        if collect_actions:
            actions.append(action)

    def add_fact(fact):
        key = (
            fact.get("type"),
            fact.get("state"),
            tuple(fact.get("cell", [])),
            fact.get("minimum"),
            fact.get("maximum"),
            fact.get("count"),
        )
        if key in seen_fact_keys:
            return
        seen_fact_keys.add(key)
        facts.append(fact)

    rounds = 0
    for round_index in range(max_rounds):
        rounds = round_index + 1
        if not _deadline_available(deadline):
            timed_out = True
            break

        changed = False
        contradiction = find_basic_contradiction(work)
        if contradiction:
            states, cells = build_knowledge(work, last_domains)
            return KnowledgeResult(
                work, states, cells, actions, facts, contradiction,
                rounds, False, timed_out,
            )

        # 1) Clue zero creates an anchored state: capitol certain, shape unknown.
        for cell, value in CLUES.items():
            if value != 0:
                continue
            before_owner = work.state_at(cell)
            before_cap = None if before_owner is None else work.marked_capitol_cell(before_owner)
            try:
                owner, created = _ensure_zero_anchor(work, cell)
            except ValueError as exc:
                states, cells = build_knowledge(work, last_domains)
                return KnowledgeResult(
                    work, states, cells, actions, facts, str(exc),
                    rounds, False, timed_out,
                )

            if created or before_cap != cell:
                changed = True
                add_action({
                    "type": "zero_capitol",
                    "state": owner,
                    "cell": _cell_json(cell),
                    "text": f"{label(cell)} is certainly the capitol of State {owner}; the rest of that state remains unknown.",
                })

        # 2) A 1 clue with one remaining adjacent singleton candidate is forced.
        for clue_cell, value in CLUES.items():
            if value != 1:
                continue
            candidates = singleton_capitol_candidates(work, clue_cell)
            if not candidates:
                states, cells = build_knowledge(work, last_domains)
                return KnowledgeResult(
                    work, states, cells, actions, facts,
                    f"The 1 at {label(clue_cell)} has no possible adjacent singleton capitol.",
                    rounds, False, timed_out,
                )

            locked = [
                cell for cell in candidates
                if is_locked_singleton_capitol(work, cell)
            ]
            if locked:
                add_fact({
                    "type": "one-clue-satisfied",
                    "cell": _cell_json(clue_cell),
                    "count": len(locked),
                    "options": [_cell_json(cell) for cell in locked],
                    "text": f"The 1 at {label(clue_cell)} is already satisfied by a proved adjacent singleton capitol.",
                })
                continue

            add_fact({
                "type": "one-clue-domain",
                "cell": _cell_json(clue_cell),
                "count": len(candidates),
                "options": [_cell_json(cell) for cell in candidates],
                "text": f"The 1 at {label(clue_cell)} currently has {len(candidates)} possible singleton-capitol location(s).",
            })

            if len(candidates) == 1:
                singleton = candidates[0]
                owner_before = work.state_at(singleton)
                locked_before = is_locked_singleton_capitol(work, singleton)
                try:
                    owner, _created, _forbidden = _lock_singleton(work, singleton)
                except ValueError as exc:
                    states, cells = build_knowledge(work, last_domains)
                    return KnowledgeResult(
                        work, states, cells, actions, facts, str(exc),
                        rounds, False, timed_out,
                    )
                if not locked_before:
                    changed = True
                    add_action({
                        "type": "singleton_capitol",
                        "state": owner,
                        "cell": _cell_json(singleton),
                        "text": f"{label(singleton)} is forced to be a one-square state and capitol.",
                    })

        contradiction = find_basic_contradiction(work)
        if contradiction:
            states, cells = build_knowledge(work, last_domains)
            return KnowledgeResult(
                work, states, cells, actions, facts, contradiction,
                rounds, False, timed_out,
            )

        # 3) Propagate static size facts into the knowledge base.
        for state_id, state_cells in list(work.state_cells.items()):
            minimum = _state_min_size(work, state_id)
            if minimum > len(state_cells):
                add_fact({
                    "type": "state-min-size",
                    "state": state_id,
                    "minimum": minimum,
                    "text": f"State {state_id} must eventually have at least {minimum} squares.",
                })

        # 4) Prune the implicit candidate-shape domain of each known state.
        round_domains = {}
        for state_id, state_cells in list(work.state_cells.items()):
            if not _deadline_available(deadline):
                timed_out = True
                break

            domain = _domain_for_state(work, state_id, deadline, deep)
            if domain is None:
                continue
            round_domains[state_id] = domain

            if not domain.feasible:
                if domain.exact:
                    states, cells = build_knowledge(work, round_domains)
                    return KnowledgeResult(
                        work, states, cells, actions, facts,
                        f"State {state_id} has no legal connected symmetric completion.",
                        rounds, False, timed_out,
                    )
                continue

            if domain.min_size is not None or domain.max_size is not None:
                add_fact({
                    "type": "state-size-range",
                    "state": state_id,
                    "minimum": domain.min_size,
                    "maximum": domain.max_size,
                    "text": (
                        f"State {state_id} has proven size bounds "
                        f"{domain.min_size if domain.min_size is not None else '?'} to "
                        f"{domain.max_size if domain.max_size is not None else '?'}."
                    ),
                })

            if domain.possible_symmetry_count is not None:
                add_fact({
                    "type": "symmetry-domain",
                    "state": state_id,
                    "count": domain.possible_symmetry_count,
                    "text": f"State {state_id} has {domain.possible_symmetry_count} symmetry placements still compatible with its certain cells.",
                })

            certain_before = set(work.state_cells.get(state_id, []))
            for cell in sorted(domain.forced_cells - certain_before):
                try:
                    work.assign(state_id, cell)
                except ValueError as exc:
                    states, cells = build_knowledge(work, round_domains)
                    return KnowledgeResult(
                        work, states, cells, actions, facts, str(exc),
                        rounds, False, timed_out,
                    )
                changed = True
                add_action({
                    "type": "add_cell",
                    "state": state_id,
                    "cell": _cell_json(cell),
                    "text": f"{label(cell)} occurs in every legal completion of State {state_id}.",
                })

            for cell in sorted(domain.excluded_cells):
                if work.state_at(cell) is not None:
                    continue
                if work.is_forbidden(state_id, cell):
                    continue
                work.forbid(state_id, cell)
                changed = True
                add_action({
                    "type": "exclude_cell",
                    "state": state_id,
                    "cell": _cell_json(cell),
                    "text": f"{label(cell)} cannot belong to State {state_id}; no legal completion includes it.",
                })

            if domain.candidate_count_exact:
                count = len(domain.candidate_shapes)
                add_fact({
                    "type": "candidate-shape-count",
                    "state": state_id,
                    "count": count,
                    "text": f"State {state_id} has exactly {count} legal complete shape candidate(s).",
                })

                # Candidate intersection/union in explicit small domains.
                if domain.candidate_shapes:
                    intersection = set.intersection(*map(set, domain.candidate_shapes))
                    union = set.union(*map(set, domain.candidate_shapes))
                    for cell in sorted(intersection - set(work.state_cells.get(state_id, []))):
                        work.assign(state_id, cell)
                        changed = True
                        add_action({
                            "type": "candidate-intersection",
                            "state": state_id,
                            "cell": _cell_json(cell),
                            "text": f"{label(cell)} is in the intersection of every remaining complete shape for State {state_id}.",
                        })

                    for i in range(N * N):
                        cell = from_index(i)
                        if cell in union or work.state_at(cell) is not None or work.is_forbidden(state_id, cell):
                            continue
                        work.forbid(state_id, cell)
                        changed = True
                        add_action({
                            "type": "candidate-union-exclusion",
                            "state": state_id,
                            "cell": _cell_json(cell),
                            "text": f"{label(cell)} lies outside the union of every remaining complete shape for State {state_id}.",
                        })

                if count == 1:
                    try:
                        added, excluded = _apply_exact_shape(
                            work, state_id, domain.candidate_shapes[0]
                        )
                    except ValueError as exc:
                        states, cells = build_knowledge(work, round_domains)
                        return KnowledgeResult(
                            work, states, cells, actions, facts, str(exc),
                            rounds, False, timed_out,
                        )
                    if added or excluded:
                        changed = True
                        add_action({
                            "type": "solve_state_shape",
                            "state": state_id,
                            "cells": [_cell_json(cell) for cell in sorted(domain.candidate_shapes[0])],
                            "text": f"State {state_id} has exactly one legal complete shape.",
                        })

        if probe_ownership:
            # 5) Infer ownership of informative unassigned cells.
            #
            # This is the key bridge the earlier solver was missing. A deduction
            # such as "the state containing r3c4 has size >= 22" used to be stored
            # but never acted on because r3c4 did not yet have a state ID. We now
            # ask whether each known state can contain that cell and whether some
            # genuinely new state can contain it. Proven impossibilities become
            # exclusions; if only one owner remains, the cell is assigned.
            for cell in _ownership_probe_cells(work, deep=deep):
                if not _deadline_available(deadline):
                    timed_out = True
                    break
                if work.state_at(cell) is not None:
                    continue

                known_states = sorted(work.state_cells)
                possible_existing = []
                unresolved_existing = []

                for state_id in known_states:
                    if work.is_forbidden(state_id, cell):
                        continue
                    if not _deadline_available(deadline):
                        timed_out = True
                        break

                    seconds = min(
                        0.30 if deep else 0.14,
                        max(0.03, _remaining(deadline, 0.14)),
                    )
                    possible, exact, _witness = probe_state_membership(
                        work,
                        state_id,
                        cell,
                        time_limit=seconds,
                    )

                    if possible:
                        possible_existing.append(state_id)
                    elif exact:
                        work.forbid(state_id, cell)
                        changed = True
                        add_action({
                            "type": "owner-exclusion",
                            "state": state_id,
                            "cell": _cell_json(cell),
                            "text": (
                                f"{label(cell)} cannot belong to State {state_id}; "
                                "no legal connected symmetric completion can contain it."
                            ),
                        })
                    else:
                        unresolved_existing.append(state_id)

                if timed_out or work.state_at(cell) is not None:
                    continue

                # Test the remaining logical possibility: the cell belongs to a
                # state that has not been named yet.
                seconds = min(
                    0.34 if deep else 0.16,
                    max(0.03, _remaining(deadline, 0.16)),
                )
                fresh_possible, fresh_exact, _fresh_witness = probe_fresh_state(
                    work,
                    cell,
                    time_limit=seconds,
                )

                if not fresh_possible and not fresh_exact:
                    # Timeout: no proof either way.
                    add_fact({
                        "type": "owner-domain-incomplete",
                        "cell": _cell_json(cell),
                        "count": len(possible_existing) + len(unresolved_existing),
                        "text": (
                            f"Ownership search for {label(cell)} hit its time budget; "
                            "no speculative conclusion was committed."
                        ),
                    })
                    continue

                if not fresh_possible and fresh_exact:
                    # The cell cannot start a new state. If exactly one existing
                    # state remains possible (and no state timed out), ownership is
                    # genuinely forced.
                    if not unresolved_existing and len(possible_existing) == 1:
                        owner = possible_existing[0]
                        try:
                            work.assign(owner, cell)
                        except ValueError as exc:
                            states, cells = build_knowledge(work, round_domains)
                            return KnowledgeResult(
                                work, states, cells, actions, facts, str(exc),
                                rounds, False, timed_out,
                            )
                        changed = True
                        add_action({
                            "type": "forced-owner",
                            "state": owner,
                            "cell": _cell_json(cell),
                            "text": (
                                f"{label(cell)} is forced into State {owner}: every "
                                "other known state and every possible new state were eliminated."
                            ),
                        })
                        continue

                    if not unresolved_existing and not possible_existing:
                        states, cells = build_knowledge(work, round_domains)
                        return KnowledgeResult(
                            work,
                            states,
                            cells,
                            actions,
                            facts,
                            (
                                f"{label(cell)} has no possible owner: it cannot join "
                                "any known state and cannot form any new legal state."
                            ),
                            rounds,
                            False,
                            timed_out,
                        )

                    add_fact({
                        "type": "known-owner-domain",
                        "cell": _cell_json(cell),
                        "count": len(possible_existing) + len(unresolved_existing),
                        "options": possible_existing + unresolved_existing,
                        "text": (
                            f"{label(cell)} cannot start a new state and must belong "
                            f"to one of {len(possible_existing) + len(unresolved_existing)} known state(s)."
                        ),
                    })
                    continue

                # A fresh state is possible. If every existing state has been
                # PROVED impossible, the unnamed state containing this cell can be
                # safely given a label now. This is only naming an already-existing
                # mathematical state; it does not choose its shape.
                if (
                    fresh_possible
                    and not possible_existing
                    and not unresolved_existing
                ):
                    new_state = work.next_state_id()
                    try:
                        work.assign(new_state, cell)
                    except ValueError as exc:
                        states, cells = build_knowledge(work, round_domains)
                        return KnowledgeResult(
                            work, states, cells, actions, facts, str(exc),
                            rounds, False, timed_out,
                        )
                    changed = True
                    add_action({
                        "type": "new-state-anchor",
                        "state": new_state,
                        "cell": _cell_json(cell),
                        "text": (
                            f"{label(cell)} cannot belong to any existing state, so "
                            f"the solver created State {new_state} as the symbolic name "
                            "for the (still partly unknown) state containing it."
                        ),
                    })
                    continue

                add_fact({
                    "type": "owner-domain",
                    "cell": _cell_json(cell),
                    "count": len(possible_existing) + len(unresolved_existing) + 1,
                    "options": possible_existing + unresolved_existing + ["new"],
                    "text": (
                        f"{label(cell)} can currently belong to "
                        f"{len(possible_existing) + len(unresolved_existing)} known state(s) "
                        "or to a not-yet-named state."
                    ),
                })

        if round_domains:
            last_domains = round_domains

        contradiction = find_basic_contradiction(work)
        if contradiction:
            states, cells = build_knowledge(work, last_domains)
            return KnowledgeResult(
                work, states, cells, actions, facts, contradiction,
                rounds, False, timed_out,
            )

        if not changed:
            fixed_point = True
            break

    states, cells = build_knowledge(work, last_domains)
    return KnowledgeResult(
        board=work,
        states=states,
        cells=cells,
        actions=actions,
        facts=facts,
        contradiction=None,
        rounds=rounds,
        fixed_point=fixed_point,
        timed_out=timed_out,
    )
