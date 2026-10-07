from __future__ import annotations

from dataclasses import dataclass
import time

from .candidates import validate_complete_board
from .knowledge import propagate_knowledge
from .lookahead import (
    SearchContradiction,
    apply_option,
    candidate_branches,
)
from .model import BoardState
from .puzzle import CLUES, N, from_index, label


@dataclass
class SearchStats:
    deadline: float
    max_nodes: int
    nodes: int = 0
    contradictions: int = 0
    complete_failures: int = 0

    def available(self):
        return self.nodes < self.max_nodes and time.monotonic() < self.deadline


class BranchBudgetAdapter:
    """
    candidate_branches only needs available() and remaining_seconds().
    Keep search.py independent of the look-ahead node accounting.
    """

    def __init__(self, stats: SearchStats):
        self.stats = stats

    def available(self):
        return self.stats.available()

    def remaining_seconds(self):
        return max(0.0, self.stats.deadline - time.monotonic())


def _cell_json(cell):
    return [cell[0] + 1, cell[1] + 1]


def _public_option(option):
    out = {
        "type": option["type"],
        "description": option.get("description", option["type"]),
    }
    if "state" in option:
        out["state"] = option["state"]
    if "cell" in option:
        out["cell"] = option["cell"]
    if "value" in option:
        out["value"] = option["value"]
    if "shape" in option:
        out["shape"] = option["shape"]
    return out


def _fallback_owner_branch(board: BoardState, knowledge):
    unassigned = [
        info
        for info in knowledge.cells.values()
        if info.forced_state is None
    ]
    if not unassigned:
        return None

    assigned_set = {
        from_index(i)
        for i, owner in enumerate(board.assignments)
        if owner is not None
    }

    def adjacent_assigned(cell):
        r, c = cell
        return any(
            (r + dr, c + dc) in assigned_set
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1))
        )

    unassigned.sort(
        key=lambda info: (
            len(info.possible_known_states) + 1,
            0 if adjacent_assigned(info.cell) else 1,
            -info.min_state_size,
            0 if info.cell in CLUES else 1,
            info.cell,
        )
    )
    info = unassigned[0]

    options = []
    for state_id in sorted(info.possible_known_states):
        options.append({
            "type": "cell_owner",
            "state": state_id,
            "cell0": info.cell,
            "cell": _cell_json(info.cell),
            "description": f"{label(info.cell)} belongs to State {state_id}",
        })
    options.append({
        "type": "cell_owner_new",
        "cell0": info.cell,
        "cell": _cell_json(info.cell),
        "description": f"{label(info.cell)} belongs to a new state",
    })

    return {
        "kind": "owner-fallback",
        "title": f"Which state contains {label(info.cell)}?",
        "cells0": [info.cell],
        "options": options,
        "priority": 9,
        "information": info.min_state_size,
    }


def _order_options(board: BoardState, branch):
    """
    Search heuristic only. It never changes logical validity.

    Prefer choices that reuse an adjacent existing state, then other existing
    states, then create a new state. For clue-1 branches prefer a singleton
    that satisfies more than one clue-1 simultaneously.
    """
    options = list(branch["options"])

    if branch["kind"] == "one-clue":
        def singleton_score(option):
            cell = option.get("cell0")
            if cell is None:
                return (0, option.get("description", ""))
            count = 0
            r, c = cell
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if CLUES.get((r + dr, c + dc)) == 1:
                    count += 1
            return (-count, option.get("description", ""))

        return sorted(options, key=singleton_score)

    def owner_score(option):
        kind = option["type"]
        if kind not in {"cell_owner", "cell_owner_new"}:
            return (0, 0, option.get("description", ""))

        if kind == "cell_owner_new":
            return (2, 0, option.get("description", ""))

        cell = option["cell0"]
        state_id = option["state"]
        r, c = cell
        adjacent = any(
            0 <= r + dr < N
            and 0 <= c + dc < N
            and board.state_at((r + dr, c + dc)) == state_id
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1))
        )
        return (0 if adjacent else 1, state_id, option.get("description", ""))

    return sorted(options, key=owner_score)


def search_for_solution(
    board: BoardState,
    time_budget=30.0,
    max_nodes=5000,
):
    """
    Depth-first branch-and-backtrack search.

    Unlike solve_logically(), this function IS allowed to explore non-forced
    hypotheses internally. A hypothesis is never presented as a proved fact.
    Contradictory branches are abandoned and the search backtracks.

    Returns either an exact validated solution or the deepest consistent
    speculative position reached before the budget expires.
    """
    time_budget = max(1.0, min(float(time_budget), 120.0))
    max_nodes = max(10, min(int(max_nodes), 100000))
    stats = SearchStats(
        deadline=time.monotonic() + time_budget,
        max_nodes=max_nodes,
    )
    branch_budget = BranchBudgetAdapter(stats)

    best_board = board.clone()
    best_path = []
    best_assigned = sum(owner is not None for owner in board.assignments)

    def update_best(current, path):
        nonlocal best_board, best_path, best_assigned
        assigned = sum(owner is not None for owner in current.assignments)
        if assigned > best_assigned:
            best_assigned = assigned
            best_board = current.clone()
            best_path = list(path)

    def dfs(current: BoardState, path):
        nonlocal best_board, best_path, best_assigned

        if not stats.available():
            return None

        stats.nodes += 1

        # Fast logical closure. Expensive CP-SAT domain sweeps are omitted at
        # every node; cheap symmetry/connectivity/area contradictions remain.
        knowledge = propagate_knowledge(
            current,
            deadline=stats.deadline,
            max_rounds=8,
            deep=False,
            collect_actions=False,
            probe_ownership=False,
            analyze_domains=False,
        )

        if knowledge.contradiction:
            stats.contradictions += 1
            return None

        current = knowledge.board
        update_best(current, path)

        if all(owner is not None for owner in current.assignments):
            valid, _reason = validate_complete_board(current)
            if valid:
                return current
            stats.complete_failures += 1
            return None

        if not stats.available():
            return None

        branches = candidate_branches(current, knowledge, branch_budget)
        branch = branches[0] if branches else _fallback_owner_branch(current, knowledge)
        if branch is None:
            return None

        for option in _order_options(current, branch):
            if not stats.available():
                break
            try:
                child = apply_option(current, option)
            except SearchContradiction:
                stats.contradictions += 1
                continue

            public = _public_option(option)
            solution = dfs(
                child,
                path + [{
                    "branch": branch["title"],
                    "choice": public,
                }],
            )
            if solution is not None:
                return solution

        return None

    solution = dfs(board.clone(), [])

    if solution is not None:
        valid, reason = validate_complete_board(solution)
        return {
            "status": "solution",
            "title": "Exact solution found",
            "explanation": reason,
            "nodes": stats.nodes,
            "contradictions": stats.contradictions,
            "complete_failures": stats.complete_failures,
            "assigned_cells": N * N,
            "path": best_path,
            "internal_board": {
                "assignments": solution.assignments,
                "manualCapitols": {
                    str(state_id): cell_index
                    for state_id, cell_index in solution.manual_capitols.items()
                },
                "forbiddenByState": {
                    str(state_id): [
                        [r + 1, c + 1]
                        for r, c in sorted(cells)
                    ]
                    for state_id, cells in solution.forbidden_by_state.items()
                },
            },
        }

    return {
        "status": "partial",
        "title": "Search budget ended before a full solution",
        "explanation": (
            "The returned board is a speculative search position, not a set of "
            "proved deductions. The solver explored hypotheses and backtracked "
            "from contradictions, but did not finish the entire puzzle."
        ),
        "nodes": stats.nodes,
        "contradictions": stats.contradictions,
        "complete_failures": stats.complete_failures,
        "assigned_cells": best_assigned,
        "path": best_path,
        "internal_board": {
            "assignments": best_board.assignments,
            "manualCapitols": {
                str(state_id): cell_index
                for state_id, cell_index in best_board.manual_capitols.items()
            },
            "forbiddenByState": {
                str(state_id): [
                    [r + 1, c + 1]
                    for r, c in sorted(cells)
                ]
                for state_id, cells in best_board.forbidden_by_state.items()
            },
        },
    }
