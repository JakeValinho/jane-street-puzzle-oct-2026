from __future__ import annotations

from dataclasses import dataclass
import time

from .candidates import analyze_state_domain
from .constraints import singleton_capitol_candidates
from .model import BoardState
from .puzzle import CLUES, N, index, label


@dataclass
class SearchBudget:
    deadline: float
    max_nodes: int
    nodes: int = 0

    def available(self):
        return self.nodes < self.max_nodes and time.monotonic() < self.deadline

    def use_node(self):
        if not self.available():
            return False
        self.nodes += 1
        return True

    def remaining_seconds(self):
        return max(0.0, self.deadline - time.monotonic())


class SearchContradiction(Exception):
    pass


def _cell_json(cell):
    return [cell[0] + 1, cell[1] + 1]


def _copy_forbidden(board):
    return {
        state_id: set(cells)
        for state_id, cells in board.forbidden_by_state.items()
    }


def _lock_singleton(board: BoardState, cell):
    owner = board.state_at(cell)
    if owner is None:
        owner = board.next_state_id()
        board.assign(owner, cell)
    elif board.current_size(owner) != 1:
        raise SearchContradiction(
            f"{label(cell)} would have to be a singleton capitol, but it already belongs to State {owner} with more than one selected square."
        )

    marked = board.marked_capitol_cell(owner)
    if marked is not None and marked != cell:
        raise SearchContradiction(
            f"State {owner} is required to be the singleton at {label(cell)}, but its marked capitol is elsewhere."
        )

    board.manual_capitols[owner] = index(cell)
    for r in range(N):
        for c in range(N):
            other = (r, c)
            if other != cell:
                board.forbid(owner, other)

    return owner


def _ensure_zero_capitol(board: BoardState, cell):
    owner = board.state_at(cell)
    created = False
    if owner is None:
        owner = board.next_state_id()
        board.assign(owner, cell)
        created = True

    marked = board.marked_capitol_cell(owner)
    if marked is not None and marked != cell:
        raise SearchContradiction(
            f"{label(cell)} has clue 0, but State {owner}'s marked capitol is {label(marked)}."
        )
    board.manual_capitols[owner] = index(cell)
    return owner, created


def _basic_contradiction(board: BoardState):
    for state_id, cells in board.state_cells.items():
        if any(board.is_forbidden(state_id, cell) for cell in cells):
            return f"State {state_id} contains a square that the hypothetical branch forbids."
        if not board.state_can_still_connect(state_id):
            return f"State {state_id} can no longer be connected through its own or unassigned squares."

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
        cap = divmod(cap_index, N)
        owner = board.state_at(cap)
        if owner is not None and owner != state_id:
            return f"State {state_id}'s marked capitol is owned by State {owner}."
        if CLUES.get(cap, 0) > 0:
            return f"{label(cap)} has a positive clue and therefore cannot itself be a capitol."

    for clue_cell, value in CLUES.items():
        if value == 1 and not singleton_capitol_candidates(board, clue_cell):
            return f"The 1 at {label(clue_cell)} has no possible adjacent singleton capitol."

    return None


def _structurally_constrained(board: BoardState, state_id, cells):
    return (
        len(cells) >= 2
        or board.marked_capitol_cell(state_id) is not None
        or bool(board.forbidden_by_state.get(state_id))
        or any(CLUES.get(cell) == 0 for cell in cells)
    )


def propagate(board: BoardState, budget: SearchBudget, collect_actions=False):
    """
    Repeatedly apply only deductions that are logically forced.

    Returns (board, actions, contradiction_reason). Candidate-domain deductions
    are used only when their infeasibility/forced membership is actually proved.
    """
    work = board.clone()
    actions = []

    for _round in range(12):
        changed = False

        contradiction = _basic_contradiction(work)
        if contradiction:
            return work, actions, contradiction

        # Every clue 0 is a capitol. Starting a partial state at that square is
        # bookkeeping only; the state may still grow.
        for cell, value in CLUES.items():
            if value != 0:
                continue
            before_owner = work.state_at(cell)
            before_marked = (
                None if before_owner is None
                else work.marked_capitol_cell(before_owner)
            )
            try:
                owner, created = _ensure_zero_capitol(work, cell)
            except SearchContradiction as exc:
                return work, actions, str(exc)

            if created or before_marked != cell:
                changed = True
                if collect_actions:
                    actions.append({
                        "type": "zero_capitol",
                        "state": owner,
                        "cell": _cell_json(cell),
                        "text": f"{label(cell)} is forced to be a capitol.",
                    })

        # A 1-clue with one surviving candidate forces an exact singleton.
        for clue_cell, value in CLUES.items():
            if value != 1:
                continue
            candidates = singleton_capitol_candidates(work, clue_cell)
            if not candidates:
                return work, actions, f"The 1 at {label(clue_cell)} has no possible singleton capitol."
            if len(candidates) == 1:
                cell = candidates[0]
                owner_before = work.state_at(cell)
                was_locked = False
                if owner_before is not None:
                    forbidden = work.forbidden_by_state.get(owner_before, set())
                    was_locked = len(forbidden) >= N * N - 1
                try:
                    owner = _lock_singleton(work, cell)
                except SearchContradiction as exc:
                    return work, actions, str(exc)
                if not was_locked:
                    changed = True
                    if collect_actions:
                        actions.append({
                            "type": "singleton_capitol",
                            "state": owner,
                            "cell": _cell_json(cell),
                            "text": f"{label(cell)} is forced to be a singleton capitol by the 1 at {label(clue_cell)}.",
                        })

        contradiction = _basic_contradiction(work)
        if contradiction:
            return work, actions, contradiction

        # Use the exact candidate-domain engine as propagation. Keep this
        # intentionally shallow per pass so recursive look-ahead stays fast.
        if budget.available():
            for state_id, cells in list(work.state_cells.items()):
                if not budget.available():
                    break
                if not _structurally_constrained(work, state_id, cells):
                    continue

                per_call = min(0.28, max(0.05, budget.remaining_seconds()))
                domain = analyze_state_domain(
                    work,
                    state_id,
                    time_limit=per_call,
                    max_forced_checks=5,
                    compute_bounds=False,
                )

                if not domain.feasible and domain.exact:
                    return (
                        work,
                        actions,
                        f"State {state_id} has no legal connected symmetric completion under this hypothesis.",
                    )

                for forced_cell in sorted(domain.forced_cells - set(cells)):
                    try:
                        work.assign(state_id, forced_cell)
                    except ValueError as exc:
                        return work, actions, str(exc)
                    changed = True
                    if collect_actions:
                        actions.append({
                            "type": "add_cell",
                            "state": state_id,
                            "cell": _cell_json(forced_cell),
                            "text": f"{label(forced_cell)} is in every legal completion of State {state_id}.",
                        })

        if not changed:
            break

    contradiction = _basic_contradiction(work)
    return work, actions, contradiction


def _singleton_branches(board: BoardState):
    branches = []
    for clue_cell, value in CLUES.items():
        if value != 1:
            continue
        candidates = singleton_capitol_candidates(board, clue_cell)
        if len(candidates) < 2:
            continue

        options = [
            {
                "type": "singleton_capitol",
                "cell0": cell,
                "cell": _cell_json(cell),
                "description": f"make {label(cell)} the singleton capitol",
            }
            for cell in candidates
        ]
        branches.append({
            "kind": "one-clue",
            "title": f"Which singleton satisfies the 1 at {label(clue_cell)}?",
            "cells0": [clue_cell] + candidates,
            "options": options,
            "priority": 0,
        })
    return branches


def _membership_branches(board: BoardState, budget: SearchBudget):
    branches = []
    for state_id, cells in board.state_cells.items():
        if not budget.available():
            break
        if not _structurally_constrained(board, state_id, cells):
            continue

        domain = analyze_state_domain(
            board,
            state_id,
            time_limit=min(0.35, max(0.05, budget.remaining_seconds())),
            max_forced_checks=6,
            compute_bounds=False,
        )
        if not domain.feasible or domain.branch_cell is None:
            continue

        cell = domain.branch_cell
        branches.append({
            "kind": "membership",
            "title": f"Does {label(cell)} belong to State {state_id}?",
            "cells0": list(cells) + [cell],
            "options": [
                {
                    "type": "state_cell_membership",
                    "state": state_id,
                    "cell0": cell,
                    "cell": _cell_json(cell),
                    "value": True,
                    "description": f"{label(cell)} is in State {state_id}",
                },
                {
                    "type": "state_cell_membership",
                    "state": state_id,
                    "cell0": cell,
                    "cell": _cell_json(cell),
                    "value": False,
                    "description": f"{label(cell)} is not in State {state_id}",
                },
            ],
            "priority": 1,
        })
    return branches


def choose_branch(board: BoardState, budget: SearchBudget):
    branches = _singleton_branches(board)
    branches.extend(_membership_branches(board, budget))
    if not branches:
        return None

    # Minimum Remaining Values: fewest options first. Among equal-sized
    # branches, direct clue choices beat generic membership splits.
    branches.sort(key=lambda b: (len(b["options"]), b["priority"], b["title"]))
    return branches[0]


def apply_option(board: BoardState, option):
    work = board.clone()
    kind = option["type"]

    if kind == "singleton_capitol":
        _lock_singleton(work, option["cell0"])
        return work

    if kind == "state_cell_membership":
        state_id = int(option["state"])
        cell = option["cell0"]
        if option["value"]:
            try:
                work.assign(state_id, cell)
            except ValueError as exc:
                raise SearchContradiction(str(exc))
        else:
            if work.state_at(cell) == state_id:
                raise SearchContradiction(
                    f"{label(cell)} is already selected into State {state_id}."
                )
            work.forbid(state_id, cell)
        return work

    raise ValueError(f"Unsupported hypothetical option: {kind}")


def _prove_contradiction(board: BoardState, depth: int, budget: SearchBudget):
    if not budget.use_node():
        return {
            "status": "unknown",
            "reason": "Search budget exhausted before a contradiction could be proved.",
        }

    propagated, _actions, contradiction = propagate(board, budget, collect_actions=False)
    if contradiction:
        return {"status": "contradiction", "reason": contradiction}

    if depth <= 0 or not budget.available():
        return {
            "status": "unknown",
            "reason": "No contradiction was proved within the current look-ahead depth.",
        }

    branch = choose_branch(propagated, budget)
    if branch is None:
        return {
            "status": "unknown",
            "reason": "No small exhaustive branch is currently available.",
        }

    children = []
    for option in branch["options"]:
        try:
            child_board = apply_option(propagated, option)
        except SearchContradiction as exc:
            result = {"status": "contradiction", "reason": str(exc)}
        else:
            result = _prove_contradiction(child_board, depth - 1, budget)
        children.append((option, result))

    if children and all(result["status"] == "contradiction" for _, result in children):
        return {
            "status": "contradiction",
            "reason": (
                f"Every option for '{branch['title']}' leads to a contradiction "
                f"within {depth} remaining look-ahead level(s)."
            ),
            "branch": _public_branch(branch, children),
        }

    return {
        "status": "unknown",
        "reason": "At least one branch survives the current search depth.",
        "branch": _public_branch(branch, children),
    }


def _public_option(option):
    out = {
        "type": option["type"],
        "description": option["description"],
    }
    if "state" in option:
        out["state"] = option["state"]
    if "cell" in option:
        out["cell"] = option["cell"]
    if "value" in option:
        out["value"] = option["value"]
    return out


def _public_branch(branch, children=None):
    out = {
        "kind": branch["kind"],
        "title": branch["title"],
        "cells": [_cell_json(cell) for cell in branch["cells0"]],
        "options": [_public_option(option) for option in branch["options"]],
    }
    if children is not None:
        out["results"] = [
            {
                "option": _public_option(option),
                "status": result["status"],
                "reason": result.get("reason", ""),
            }
            for option, result in children
        ]
    return out


def analyze_lookahead(
    board: BoardState,
    depth=2,
    time_budget=8.0,
    max_nodes=28,
):
    depth = max(1, min(int(depth), 4))
    time_budget = max(0.5, min(float(time_budget), 30.0))
    max_nodes = max(2, min(int(max_nodes), 200))

    budget = SearchBudget(
        deadline=time.monotonic() + time_budget,
        max_nodes=max_nodes,
    )

    propagated, actions, contradiction = propagate(
        board,
        budget,
        collect_actions=True,
    )

    if contradiction:
        return {
            "status": "contradiction",
            "title": "Current board is contradictory",
            "explanation": contradiction,
            "cells": [],
            "depth": depth,
            "nodes": budget.nodes,
        }

    if actions:
        return {
            "status": "propagation",
            "title": f"{len(actions)} direct forced move" + ("" if len(actions) == 1 else "s") + " found before branching",
            "explanation": "Apply these first; hypothetical branching is unnecessary until ordinary propagation stalls.",
            "actions": actions,
            "cells": [action["cell"] for action in actions if "cell" in action],
            "depth": depth,
            "nodes": budget.nodes,
        }

    branch = choose_branch(propagated, budget)
    if branch is None:
        return {
            "status": "no-branch",
            "title": "No small exhaustive branch found",
            "explanation": "The current rules did not expose a 2- or few-choice hypothesis to test.",
            "cells": [],
            "depth": depth,
            "nodes": budget.nodes,
        }

    children = []
    for option in branch["options"]:
        try:
            child = apply_option(propagated, option)
        except SearchContradiction as exc:
            result = {"status": "contradiction", "reason": str(exc)}
        else:
            result = _prove_contradiction(child, depth - 1, budget)
        children.append((option, result))

    contradicted = [
        (option, result)
        for option, result in children
        if result["status"] == "contradiction"
    ]
    survivors = [
        (option, result)
        for option, result in children
        if result["status"] != "contradiction"
    ]

    public_branch = _public_branch(branch, children)
    cells = public_branch["cells"]

    if children and len(contradicted) == len(children):
        return {
            "status": "contradiction",
            "title": "Every branch leads to contradiction",
            "explanation": (
                f"All {len(children)} possibilities for '{branch['title']}' fail within "
                f"look-ahead depth {depth}. The current board cannot be completed under the encoded rules."
            ),
            "branch": public_branch,
            "cells": cells,
            "depth": depth,
            "nodes": budget.nodes,
        }

    if len(survivors) == 1 and contradicted:
        forced_option = _public_option(survivors[0][0])
        eliminated = [_public_option(option) for option, _ in contradicted]
        return {
            "status": "forced-by-contradiction",
            "title": "Look-ahead forces one option",
            "explanation": (
                f"All alternatives to '{forced_option['description']}' lead to contradictions "
                f"within depth {depth}."
            ),
            "forced_option": forced_option,
            "eliminated_options": eliminated,
            "branch": public_branch,
            "cells": cells,
            "depth": depth,
            "nodes": budget.nodes,
        }

    if contradicted:
        return {
            "status": "narrowed",
            "title": f"Look-ahead eliminates {len(contradicted)} option" + ("" if len(contradicted) == 1 else "s"),
            "explanation": (
                f"The branch '{branch['title']}' still has {len(survivors)} surviving option(s), "
                f"but the listed alternatives are impossible within depth {depth}."
            ),
            "eliminated_options": [_public_option(option) for option, _ in contradicted],
            "surviving_options": [_public_option(option) for option, _ in survivors],
            "branch": public_branch,
            "cells": cells,
            "depth": depth,
            "nodes": budget.nodes,
        }

    return {
        "status": "unresolved",
        "title": f"No contradiction found for the best {len(children)}-way branch",
        "explanation": (
            f"The solver tested '{branch['title']}' recursively to depth {depth}, "
            "but every option survived the current proof budget."
        ),
        "branch": public_branch,
        "cells": cells,
        "depth": depth,
        "nodes": budget.nodes,
    }
