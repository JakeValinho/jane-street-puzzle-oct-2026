from __future__ import annotations

from dataclasses import dataclass, asdict
from math import ceil

from .model import BoardState
from .puzzle import CLUES, N, index, label, neighbors
from .constraints import forced_singleton_capitols
from .candidates import analyze_state_domain


@dataclass
class Deduction:
    rule: str
    title: str
    explanation: str
    cells: list[tuple[int, int]]
    choices: list[dict]
    category: str = "deduction"
    rank: int = 50

    @property
    def option_count(self):
        return max(1, len(self.choices))

    def to_dict(self):
        d = asdict(self)
        d["cells"] = [[r + 1, c + 1] for r, c in self.cells]
        d["option_count"] = self.option_count
        return d


def _candidate_singleton_capitol(board: BoardState, cell):
    # A capitol square itself must have clue 0 if it is a numbered square.
    if cell in CLUES and CLUES[cell] != 0:
        return False

    state_id = board.state_at(cell)
    if state_id is not None and board.current_size(state_id) > 1:
        return False

    # If this were a singleton capitol, every adjacent numbered square could
    # reach a capitol in cost 1, so none may have a clue greater than 1.
    for adj in neighbors(cell):
        if CLUES.get(adj, 0) > 1:
            return False

    return True


def rule_contradictions(board: BoardState):
    out = []

    # A currently drawn state must at least still be connectable through its
    # own cells and unassigned cells. Other states are permanent barriers.
    for state_id, cells in board.state_cells.items():
        if not board.state_can_still_connect(state_id):
            out.append(Deduction(
                rule="connectability",
                title=f"State {state_id} cannot be made connected",
                explanation=(
                    f"The currently selected cells of State {state_id} are separated by cells "
                    "already committed to other states. No future filling can connect them."
                ),
                cells=cells,
                choices=[],
                category="contradiction",
                rank=0,
            ))

    # Every 0-clue square is itself a capitol. One state cannot contain two
    # different 0-clue squares, and a manual capitol elsewhere conflicts.
    zero_cells = [cell for cell, value in CLUES.items() if value == 0]
    by_state = {}
    for cell in zero_cells:
        state_id = board.state_at(cell)
        if state_id is None:
            continue
        if state_id in by_state:
            other = by_state[state_id]
            out.append(Deduction(
                rule="zero-clue",
                title=f"State {state_id} contains two forced capitols",
                explanation=f"Both {label(other)} and {label(cell)} have clue 0, so both must be capitols. They cannot belong to the same state.",
                cells=[other, cell],
                choices=[],
                category="contradiction",
                rank=0,
            ))
        by_state[state_id] = cell

        marked = board.marked_capitol_cell(state_id)
        if marked is not None and marked != cell:
            out.append(Deduction(
                rule="zero-clue",
                title=f"State {state_id}'s marked capitol conflicts with a 0 clue",
                explanation=f"{label(cell)} has clue 0, so that exact square must be the capitol of its state.",
                cells=[marked, cell],
                choices=[],
                category="contradiction",
                rank=0,
            ))

    # A positive clue cannot sit in a state that is already a singleton,
    # because a one-square state necessarily has its only square as capitol.
    for cell, value in CLUES.items():
        if value <= 0:
            continue
        state_id = board.state_at(cell)
        if state_id is not None and board.current_size(state_id) == 1:
            out.append(Deduction(
                rule="positive-clue-singleton",
                title=f"{label(cell)} cannot be a singleton state",
                explanation=f"Its clue is {value}, but a one-square state has its sole square as a capitol and would therefore have distance 0.",
                cells=[cell],
                choices=[],
                category="contradiction",
                rank=0,
            ))

    return out


def rule_zero_clues(board: BoardState):
    out = []
    for cell, value in CLUES.items():
        if value != 0:
            continue
        state_id = board.state_at(cell)
        if state_id is None:
            out.append(Deduction(
                rule="zero-clue",
                title=f"{label(cell)} is definitely a capitol",
                explanation="A distance clue of 0 means this square itself is a state capitol. Its state shape is not yet determined.",
                cells=[cell],
                choices=[{"type": "capitol_at", "cell": [cell[0] + 1, cell[1] + 1]}],
                rank=5,
            ))
        elif board.marked_capitol_cell(state_id) != cell:
            out.append(Deduction(
                rule="zero-clue",
                title=f"Mark {label(cell)} as State {state_id}'s capitol",
                explanation="Its clue is 0, so this is forced regardless of the rest of the state's shape.",
                cells=[cell],
                choices=[{"type": "mark_capitol", "state": state_id, "cell": [cell[0] + 1, cell[1] + 1]}],
                rank=4,
            ))
    return out


def rule_existing_singletons(board: BoardState):
    out = []
    for state_id, cells in board.state_cells.items():
        if len(cells) != 1:
            continue
        cell = cells[0]
        if board.marked_capitol_cell(state_id) == cell:
            continue
        out.append(Deduction(
            rule="singleton-state",
            title=f"State {state_id}'s only square is its capitol",
            explanation="A one-square state is symmetric under every rotation and reflection, and every such symmetry fixes exactly that one square.",
            cells=[cell],
            choices=[{"type": "mark_capitol", "state": state_id, "cell": [cell[0] + 1, cell[1] + 1]}],
            rank=6,
        ))
    return out


def rule_one_clues(board: BoardState):
    out = []
    for clue_cell, value in CLUES.items():
        if value != 1:
            continue

        candidates = [cell for cell in neighbors(clue_cell) if _candidate_singleton_capitol(board, cell)]
        choices = [
            {"type": "singleton_capitol", "cell": [r + 1, c + 1]}
            for r, c in candidates
        ]

        if not candidates:
            out.append(Deduction(
                rule="one-clue",
                title=f"The 1 at {label(clue_cell)} has no possible adjacent singleton capitol",
                explanation="A clue of 1 must be one move from a size-1 state whose sole square is a capitol. Every adjacent candidate is currently ruled out.",
                cells=[clue_cell],
                choices=[],
                category="contradiction",
                rank=0,
            ))
        elif len(candidates) == 1:
            only = candidates[0]
            out.append(Deduction(
                rule="one-clue",
                title=f"{label(only)} is forced to be a singleton capitol",
                explanation=f"The 1 at {label(clue_cell)} needs an adjacent size-1 capitol, and all other adjacent squares are impossible.",
                cells=[clue_cell, only],
                choices=choices,
                rank=7,
            ))
        else:
            out.append(Deduction(
                rule="one-clue",
                title=f"The 1 at {label(clue_cell)} has only {len(candidates)} possible singleton capitols",
                explanation="A distance of 1 is possible only by crossing an edge whose cost is 1, so the destination capitol must be a one-square state.",
                cells=[clue_cell] + candidates,
                choices=choices,
                rank=18 + len(candidates),
            ))
    return out


def _add_bound(bounds, cell, minimum, reason, source_cells):
    if minimum <= 1:
        return
    current = bounds.get(cell)
    if current is None or minimum > current["minimum"]:
        bounds[cell] = {
            "minimum": minimum,
            "reason": reason,
            "source_cells": source_cells,
        }


def rule_distance_size_bounds(board: BoardState):
    bounds = {}
    clue_items = list(CLUES.items())

    for i, (a, da) in enumerate(clue_items):
        for b, db in clue_items[i + 1:]:
            manhattan = abs(a[0] - b[0]) + abs(a[1] - b[1])
            diff = abs(da - db)
            if diff == 0:
                continue

            if manhattan == 1:
                # |D(a)-D(b)| <= edge_cost = min(size(a), size(b)).
                _add_bound(bounds, a, diff, f"Adjacent clues {da} and {db} differ by {diff}.", [a, b])
                _add_bound(bounds, b, diff, f"Adjacent clues {da} and {db} differ by {diff}.", [a, b])

            elif manhattan == 2:
                # For every square z adjacent to both clue cells:
                # diff <= w(a,z)+w(z,b) <= 2*size(z).
                common = set(neighbors(a)).intersection(neighbors(b))
                minimum = ceil(diff / 2)
                for middle in common:
                    _add_bound(
                        bounds,
                        middle,
                        minimum,
                        f"Clues {da} at {label(a)} and {db} at {label(b)} differ by {diff} across two moves.",
                        [a, b, middle],
                    )

    out = []
    # Consolidate multiple cell bounds that already refer to the same drawn state.
    state_best = {}
    for cell, info in bounds.items():
        state_id = board.state_at(cell)
        if state_id is None:
            if info["minimum"] > 1:
                out.append(Deduction(
                    rule="distance-lower-bound",
                    title=f"The state containing {label(cell)} must have size at least {info['minimum']}",
                    explanation=info["reason"] + " Each edge touching this square costs no more than the size of its state.",
                    cells=info["source_cells"],
                    choices=[{"type": "state_size_at_least", "cell": [cell[0] + 1, cell[1] + 1], "minimum": info["minimum"]}],
                    rank=30 - min(info["minimum"], 20),
                ))
        else:
            prev = state_best.get(state_id)
            if prev is None or info["minimum"] > prev[1]["minimum"]:
                state_best[state_id] = (cell, info)

    for state_id, (cell, info) in state_best.items():
        current = board.current_size(state_id)
        if current >= info["minimum"]:
            continue
        out.append(Deduction(
            rule="distance-lower-bound",
            title=f"State {state_id} must eventually contain at least {info['minimum']} squares",
            explanation=info["reason"] + f" It currently has {current}, so it still needs at least {info['minimum'] - current} more.",
            cells=info["source_cells"],
            choices=[{"type": "state_size_at_least", "state": state_id, "minimum": info["minimum"]}],
            rank=28 - min(info["minimum"], 20),
        ))

    return out


def rule_marked_capitol_parity(board: BoardState):
    out = []
    for state_id, cap_index in board.manual_capitols.items():
        current = board.current_size(state_id)
        if current == 0 or current % 2 == 1:
            continue
        cap_cell = divmod(cap_index, N)
        out.append(Deduction(
            rule="capitol-parity",
            title=f"State {state_id} cannot finish at its current even size {current}",
            explanation="Any state with a capitol has odd size: all non-capitol squares are paired (or grouped) by the state's nontrivial symmetry.",
            cells=[cap_cell],
            choices=[{"type": "state_size_parity", "state": state_id, "parity": "odd", "minimum": current + 1}],
            rank=25,
        ))
    return out


BASE_RULES = [
    rule_contradictions,
    rule_zero_clues,
    rule_one_clues,
    rule_distance_size_bounds,
    rule_marked_capitol_parity,
]


def rule_candidate_domains(board: BoardState):
    """
    Deep state-completion pruning.

    CP-SAT implicitly represents every completion compatible with a chosen
    state's already-painted cells. It enforces board boundaries, ownership,
    orthogonal connectivity, at least one genuine rotation/reflection,
    clue-derived size bounds, forced singleton capitols, clue-0 capitols,
    manually marked capitols, and the puzzle's full capitol definition.
    """
    out = []
    forced_singletons = forced_singleton_capitols(board)

    for state_id, cells in board.state_cells.items():
        # A single unconstrained painted square can have hundreds of symmetry
        # axes/centres and is rarely the best human next step. Analyze it once
        # it has structural information.
        structurally_constrained = (
            len(cells) >= 2
            or board.marked_capitol_cell(state_id) is not None
            or any(CLUES.get(cell) == 0 for cell in cells)
            or any(cell in forced_singletons for cell in cells)
        )
        if not structurally_constrained:
            continue

        domain = analyze_state_domain(board, state_id, time_limit=0.8)

        if not domain.feasible:
            if domain.exact:
                out.append(Deduction(
                    rule="candidate-domain",
                    title=f"State {state_id} has no legal completion",
                    explanation=domain.reason + " Every remaining connected symmetric state shape has been eliminated by the encoded rules.",
                    cells=cells,
                    choices=[],
                    category="contradiction",
                    rank=0,
                ))
            continue

        newly_forced = sorted(domain.forced_cells - set(cells))
        if newly_forced:
            out.append(Deduction(
                rule="candidate-domain",
                title=f"State {state_id} forces {len(newly_forced)} additional square" + ("" if len(newly_forced) == 1 else "s"),
                explanation=(
                    "The candidate solver tested the complete legal domain represented by the constraint model. "
                    "No valid connected symmetric completion exists without these square(s)."
                    + ("" if domain.exact else " The time-limited search did not prove every secondary bound, so treat only the listed forced cells as actionable.")
                ),
                cells=cells + newly_forced,
                choices=[{
                    "type": "add_cells",
                    "state": state_id,
                    "cells": [[r + 1, c + 1] for r, c in newly_forced],
                }],
                rank=8,
            ))

        if domain.branch_cell is not None:
            br, bc = domain.branch_cell
            out.append(Deduction(
                rule="candidate-membership",
                title=f"Does {label(domain.branch_cell)} belong to State {state_id}?",
                explanation=(
                    "Both possibilities still have at least one legal connected symmetric completion. "
                    "This is a natural two-way branch for hypothetical look-ahead."
                ),
                cells=cells + [domain.branch_cell],
                choices=[
                    {
                        "type": "state_cell_membership",
                        "state": state_id,
                        "cell": [br + 1, bc + 1],
                        "value": True,
                    },
                    {
                        "type": "state_cell_membership",
                        "state": state_id,
                        "cell": [br + 1, bc + 1],
                        "value": False,
                    },
                ],
                rank=34,
            ))

        if domain.min_size is not None and domain.max_size is not None:
            current = len(cells)
            if domain.exact and domain.min_size == domain.max_size and domain.min_size != current:
                out.append(Deduction(
                    rule="candidate-domain",
                    title=f"State {state_id}'s final size is forced to {domain.min_size}",
                    explanation="After discarding every illegal completion, the minimum and maximum feasible sizes are identical.",
                    cells=cells,
                    choices=[{
                        "type": "state_size_exact",
                        "state": state_id,
                        "size": domain.min_size,
                    }],
                    rank=12,
                ))
            elif domain.max_size - domain.min_size <= 3:
                out.append(Deduction(
                    rule="candidate-domain",
                    title=f"State {state_id} is tightly constrained to size {domain.min_size}–{domain.max_size}",
                    explanation=(
                        "All legal completions outside this range have been eliminated by connectivity, symmetry, capitol, ownership, and clue-derived constraints."
                        + ("" if domain.exact else " The range is based on the best proven bounds before the solver time limit.")
                    ),
                    cells=cells,
                    choices=[{
                        "type": "state_size_range",
                        "state": state_id,
                        "minimum": domain.min_size,
                        "maximum": domain.max_size,
                    }],
                    rank=22,
                ))

    return out


def analyze(board: BoardState):
    deductions = []
    seen = set()

    for rule in BASE_RULES:
        for d in rule(board):
            signature = (d.rule, d.title)
            if signature not in seen:
                seen.add(signature)
                deductions.append(d)

    # Do not spend time on deep candidate search when the current board
    # already contains a direct contradiction.
    if not any(d.category == "contradiction" for d in deductions):
        for d in rule_candidate_domains(board):
            signature = (d.rule, d.title)
            if signature not in seen:
                seen.add(signature)
                deductions.append(d)

    deductions.sort(key=lambda d: (
        0 if d.category == "contradiction" else 1,
        d.option_count,
        d.rank,
        d.title,
    ))
    return deductions


def analyze_snapshot(snapshot: dict):
    board = BoardState.from_snapshot(snapshot)
    deductions = analyze(board)
    return {
        "deductions": [d.to_dict() for d in deductions],
        "summary": {
            "assigned_cells": sum(x is not None for x in board.assignments),
            "unassigned_cells": sum(x is None for x in board.assignments),
            "drawn_states": len(board.state_cells),
            "deduction_count": len(deductions),
        },
    }
