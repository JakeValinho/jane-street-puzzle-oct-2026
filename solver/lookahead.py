from __future__ import annotations

from dataclasses import dataclass
import time

from .candidates import analyze_shape, analyze_state_domain
from .constraints import is_locked_singleton_capitol, singleton_capitol_candidates
from .knowledge import propagate_knowledge
from .model import BoardState
from .puzzle import CLUES, N, from_index, index, label


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


def _lock_singleton(board: BoardState, cell):
    owner = board.state_at(cell)
    if owner is None:
        owner = board.next_state_id()
        board.assign(owner, cell)
    elif board.current_size(owner) != 1:
        raise SearchContradiction(
            f"{label(cell)} would have to be a singleton capitol, but it already belongs to State {owner} with multiple certain cells."
        )

    marked = board.marked_capitol_cell(owner)
    if marked is not None and marked != cell:
        raise SearchContradiction(
            f"State {owner} would have to be the singleton at {label(cell)}, but its capitol is fixed elsewhere."
        )

    board.manual_capitols[owner] = index(cell)
    for i in range(N * N):
        other = from_index(i)
        if other != cell:
            board.forbid(owner, other)
    return owner


def _lock_exact_shape(board: BoardState, state_id, shape):
    shape = set(shape)

    geometry = analyze_shape(shape)
    if not geometry["valid"]:
        raise SearchContradiction(
            f"The proposed complete shape for State {state_id} is not symmetric and connected."
        )
    inferred_capitol = geometry["capitol"]
    existing_capitol = board.marked_capitol_cell(state_id)
    if existing_capitol is not None and existing_capitol != inferred_capitol:
        raise SearchContradiction(
            f"The proposed complete shape for State {state_id} conflicts with its known capitol."
        )
    if inferred_capitol is not None:
        board.manual_capitols[state_id] = index(inferred_capitol)

    for cell in shape:
        owner = board.state_at(cell)
        if owner is not None and owner != state_id:
            raise SearchContradiction(
                f"{label(cell)} already belongs to State {owner}, so this exact State {state_id} shape is impossible."
            )
        try:
            board.assign(state_id, cell)
        except ValueError as exc:
            raise SearchContradiction(str(exc))

    for i in range(N * N):
        cell = from_index(i)
        if cell in shape:
            continue
        if board.state_at(cell) == state_id:
            raise SearchContradiction(
                f"The proposed exact State {state_id} shape omits already-certain square {label(cell)}."
            )
        board.forbid(state_id, cell)


def _singleton_branches(board: BoardState):
    branches = []
    for clue_cell, value in CLUES.items():
        if value != 1:
            continue
        candidates = singleton_capitol_candidates(board, clue_cell)

        # Once any adjacent singleton is already proved, this clue is
        # satisfied. Other adjacent cells may also become singleton capitols,
        # but the clue no longer creates an exhaustive unresolved branch.
        if any(is_locked_singleton_capitol(board, cell) for cell in candidates):
            continue

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


def _shape_branches(knowledge):
    branches = []
    for state_id, state in knowledge.states.items():
        if not state.candidate_count_exact:
            continue
        shapes = state.candidate_shapes
        if not (2 <= len(shapes) <= 3):
            continue

        options = []
        cells = set(state.certain_cells)
        for number, shape in enumerate(shapes, start=1):
            cells |= set(shape)
            options.append({
                "type": "state_exact_shape",
                "state": state_id,
                "shape0": set(shape),
                "shape": [_cell_json(cell) for cell in sorted(shape)],
                "description": f"State {state_id} uses complete shape {number} of {len(shapes)}",
            })

        branches.append({
            "kind": "state-shape",
            "title": f"Which of the {len(shapes)} remaining complete shapes is State {state_id}?",
            "cells0": sorted(cells),
            "options": options,
            "priority": 1,
        })
    return branches


def _owner_branches(knowledge):
    """
    Exhaustive ownership branches for informative unassigned cells.

    This is broader than asking whether a cell belongs to one particular
    state: every currently known state plus "some new state" is represented,
    so the branch is logically exhaustive.
    """
    branches = []
    ranked = sorted(
        (
            cell_knowledge
            for cell_knowledge in knowledge.cells.values()
            if cell_knowledge.forced_state is None
            and cell_knowledge.min_state_size >= 3
        ),
        key=lambda info: (
            len(info.possible_known_states) + (1 if info.new_state_possible else 0),
            -info.min_state_size,
            info.cell,
        ),
    )

    for info in ranked[:8]:
        options = []
        for state_id in sorted(info.possible_known_states):
            options.append({
                "type": "cell_owner",
                "state": state_id,
                "cell0": info.cell,
                "cell": _cell_json(info.cell),
                "description": f"{label(info.cell)} belongs to State {state_id}",
            })

        if info.new_state_possible:
            options.append({
                "type": "cell_owner_new",
                "cell0": info.cell,
                "cell": _cell_json(info.cell),
                "description": f"{label(info.cell)} belongs to a not-yet-named state",
            })

        if 2 <= len(options) <= 4:
            branches.append({
                "kind": "owner",
                "title": f"Which state contains {label(info.cell)}?",
                "cells0": [info.cell],
                "options": options,
                "priority": 2,
                "information": info.min_state_size,
            })

    return branches


def _membership_branches(board: BoardState, budget: SearchBudget):
    branches = []
    for state_id, cells in board.state_cells.items():
        if not budget.available():
            break

        domain = analyze_state_domain(
            board,
            state_id,
            time_limit=min(0.30, max(0.04, budget.remaining_seconds())),
            max_forced_checks=6,
            max_exclusion_checks=0,
            compute_bounds=False,
            enumerate_limit=0,
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
            "priority": 3,
        })
    return branches


def choose_branch(board: BoardState, knowledge, budget: SearchBudget):
    branches = []
    branches.extend(_singleton_branches(board))
    branches.extend(_shape_branches(knowledge))
    branches.extend(_owner_branches(knowledge))
    branches.extend(_membership_branches(board, budget))
    if not branches:
        return None

    # Minimum Remaining Values. Direct clue branches win ties, then complete
    # state-shape domains, exhaustive cell-owner domains, and finally generic
    # binary membership hypotheses.
    branches.sort(key=lambda branch: (
        len(branch["options"]),
        branch["priority"],
        branch["title"],
    ))
    return branches[0]


def apply_option(board: BoardState, option):
    work = board.clone()
    kind = option["type"]

    if kind == "singleton_capitol":
        _lock_singleton(work, option["cell0"])
        return work

    if kind == "state_exact_shape":
        _lock_exact_shape(work, int(option["state"]), option["shape0"])
        return work

    if kind == "cell_owner":
        state_id = int(option["state"])
        cell = option["cell0"]
        try:
            work.assign(state_id, cell)
        except ValueError as exc:
            raise SearchContradiction(str(exc))
        return work

    if kind == "cell_owner_new":
        cell = option["cell0"]
        if work.state_at(cell) is not None:
            raise SearchContradiction(
                f"{label(cell)} already has a known owner."
            )
        state_id = work.next_state_id()
        try:
            work.assign(state_id, cell)
        except ValueError as exc:
            raise SearchContradiction(str(exc))
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
                    f"{label(cell)} is already certain to be in State {state_id}."
                )
            work.forbid(state_id, cell)
        return work

    raise ValueError(f"Unsupported hypothetical option: {kind}")


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
    if "shape" in option:
        out["shape"] = option["shape"]
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


def _propagate_for_search(board, budget, collect_actions=False, deep=False):
    result = propagate_knowledge(
        board,
        deadline=budget.deadline,
        max_rounds=10,
        deep=deep,
        collect_actions=collect_actions,
    )
    return result


def _prove_contradiction(board: BoardState, depth: int, budget: SearchBudget):
    if not budget.use_node():
        return {
            "status": "unknown",
            "reason": "Search budget exhausted before a contradiction could be proved.",
        }

    knowledge = _propagate_for_search(board, budget, collect_actions=False, deep=False)
    if knowledge.contradiction:
        return {"status": "contradiction", "reason": knowledge.contradiction}

    if depth <= 0 or not budget.available():
        return {
            "status": "unknown",
            "reason": "No contradiction was proved within the current look-ahead depth.",
        }

    branch = choose_branch(knowledge.board, knowledge, budget)
    if branch is None:
        return {
            "status": "unknown",
            "reason": "No small exhaustive branch is currently available.",
        }

    children = []
    for option in branch["options"]:
        try:
            child_board = apply_option(knowledge.board, option)
        except SearchContradiction as exc:
            result = {"status": "contradiction", "reason": str(exc)}
        else:
            result = _prove_contradiction(child_board, depth - 1, budget)
        children.append((option, result))

    if children and all(
        result["status"] == "contradiction"
        for _, result in children
    ):
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

    knowledge = _propagate_for_search(
        board,
        budget,
        collect_actions=True,
        deep=True,
    )

    if knowledge.contradiction:
        return {
            "status": "contradiction",
            "title": "Current board is contradictory",
            "explanation": knowledge.contradiction,
            "cells": [],
            "depth": depth,
            "nodes": budget.nodes,
            "knowledge": knowledge.to_dict(),
        }

    if knowledge.actions:
        cells = []
        for action in knowledge.actions:
            if "cell" in action:
                cells.append(action["cell"])
            cells.extend(action.get("cells", []))

        return {
            "status": "propagation",
            "title": (
                f"{len(knowledge.actions)} forced knowledge update"
                + ("" if len(knowledge.actions) == 1 else "s")
                + " found before branching"
            ),
            "explanation": (
                "These are consequences of direct rules, candidate intersections, "
                "candidate exclusions, or a uniquely determined state shape. "
                "No speculative choice needs to be committed yet."
            ),
            "actions": knowledge.actions,
            "facts": knowledge.facts,
            "cells": cells,
            "depth": depth,
            "nodes": budget.nodes,
            "fixed_point": knowledge.fixed_point,
            "knowledge": knowledge.to_dict(),
        }

    branch = choose_branch(knowledge.board, knowledge, budget)
    if branch is None:
        return {
            "status": "no-branch",
            "title": "Propagation reached a fixed point with no small branch",
            "explanation": (
                "No direct forced move remains and the current engine did not expose "
                "a small exhaustive clue, state-shape, or cell-membership domain."
            ),
            "cells": [],
            "depth": depth,
            "nodes": budget.nodes,
            "fixed_point": knowledge.fixed_point,
            "knowledge": knowledge.to_dict(),
        }

    children = []
    for option in branch["options"]:
        try:
            child = apply_option(knowledge.board, option)
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
            "knowledge": knowledge.to_dict(),
        }

    if len(survivors) == 1 and contradicted:
        forced_option = _public_option(survivors[0][0])
        eliminated = [_public_option(option) for option, _ in contradicted]
        return {
            "status": "forced-by-contradiction",
            "title": "Look-ahead forces one option",
            "explanation": (
                f"All alternatives to '{forced_option['description']}' lead to contradictions "
                f"within depth {depth}. The survivor is logically forced; it was never guessed onto the real board."
            ),
            "forced_option": forced_option,
            "eliminated_options": eliminated,
            "branch": public_branch,
            "cells": cells,
            "depth": depth,
            "nodes": budget.nodes,
            "knowledge": knowledge.to_dict(),
        }

    if contradicted:
        return {
            "status": "narrowed",
            "title": (
                f"Look-ahead eliminates {len(contradicted)} option"
                + ("" if len(contradicted) == 1 else "s")
            ),
            "explanation": (
                f"The branch '{branch['title']}' still has {len(survivors)} surviving option(s), "
                f"but the contradictory alternatives are permanently impossible."
            ),
            "eliminated_options": [_public_option(option) for option, _ in contradicted],
            "surviving_options": [_public_option(option) for option, _ in survivors],
            "branch": public_branch,
            "cells": cells,
            "depth": depth,
            "nodes": budget.nodes,
            "knowledge": knowledge.to_dict(),
        }

    return {
        "status": "unresolved",
        "title": (
            f"No contradiction found for the best {len(children)}-way branch"
        ),
        "explanation": (
            f"The solver selected '{branch['title']}' using minimum remaining values "
            f"and tested every option recursively to depth {depth}. Every option survived "
            "the current proof budget, so nothing is committed."
        ),
        "branch": public_branch,
        "cells": cells,
        "depth": depth,
        "nodes": budget.nodes,
        "knowledge": knowledge.to_dict(),
    }



def solve_logically(
    board: BoardState,
    max_depth=3,
    time_budget=20.0,
    max_nodes=120,
    max_iterations=20,
):
    """
    Repeatedly solve without guessing.

    1. Propagate every directly proved fact to a fixed point.
    2. If stalled, choose the smallest exhaustive branch.
    3. Test every option recursively.
    4. Commit an option only when every alternative is proved contradictory.
    5. Propagate again and repeat.

    The returned board therefore contains only logical consequences of the
    input position, including conclusions proved by contradiction.
    """
    max_depth = max(1, min(int(max_depth), 5))
    time_budget = max(1.0, min(float(time_budget), 60.0))
    max_nodes = max(4, min(int(max_nodes), 500))
    max_iterations = max(1, min(int(max_iterations), 100))

    budget = SearchBudget(
        deadline=time.monotonic() + time_budget,
        max_nodes=max_nodes,
    )
    current = board.clone()
    actions = []
    branch_history = []
    stopped_reason = "fixed-point"

    for iteration in range(max_iterations):
        if not budget.available():
            stopped_reason = "budget"
            break

        knowledge = propagate_knowledge(
            current,
            deadline=budget.deadline,
            max_rounds=16,
            deep=True,
            collect_actions=True,
        )
        actions.extend(knowledge.actions)
        current = knowledge.board

        if knowledge.contradiction:
            return {
                "status": "contradiction",
                "title": "Logical solver found a contradiction",
                "explanation": knowledge.contradiction,
                "actions": actions,
                "branches": branch_history,
                "iterations": iteration + 1,
                "nodes": budget.nodes,
                "knowledge": knowledge.to_dict(),
                "internal_board": knowledge.to_dict()["internal_board"],
            }

        if not budget.available():
            stopped_reason = "budget"
            break

        branch = choose_branch(current, knowledge, budget)
        if branch is None:
            stopped_reason = "no-branch"
            break

        forced_option = None
        final_children = None
        used_depth = None

        # Iterative deepening keeps the reasoning human-readable: try the
        # cheapest contradiction proof first, then look farther only if needed.
        for depth in range(1, max_depth + 1):
            if not budget.available():
                break

            children = []
            for option in branch["options"]:
                if not budget.available():
                    break
                try:
                    child = apply_option(current, option)
                except SearchContradiction as exc:
                    result = {"status": "contradiction", "reason": str(exc)}
                else:
                    result = _prove_contradiction(child, depth - 1, budget)
                children.append((option, result))

            if len(children) != len(branch["options"]):
                break

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

            final_children = children
            used_depth = depth

            if len(survivors) == 1 and len(contradicted) == len(children) - 1:
                forced_option = survivors[0][0]
                break

        public_branch = _public_branch(branch, final_children)
        branch_history.append({
            "branch": public_branch,
            "depth": used_depth,
            "forced": None if forced_option is None else _public_option(forced_option),
        })

        if forced_option is None:
            stopped_reason = "unresolved-branch"
            break

        try:
            current = apply_option(current, forced_option)
        except SearchContradiction as exc:
            return {
                "status": "contradiction",
                "title": "Forced branch application contradicted the board",
                "explanation": str(exc),
                "actions": actions,
                "branches": branch_history,
                "iterations": iteration + 1,
                "nodes": budget.nodes,
            }

        public_forced = _public_option(forced_option)
        action = {
            "type": "forced-by-contradiction",
            "text": (
                f"{public_forced['description']} is forced: every alternative "
                f"was proved contradictory at look-ahead depth {used_depth}."
            ),
            "description": public_forced["description"],
            "depth": used_depth,
        }
        if "state" in public_forced:
            action["state"] = public_forced["state"]
        if "cell" in public_forced:
            action["cell"] = public_forced["cell"]
        if "shape" in public_forced:
            action["cells"] = public_forced["shape"]
        actions.append(action)

    # One final propagation makes the returned board include consequences of
    # the last forced-by-contradiction branch.
    final_knowledge = propagate_knowledge(
        current,
        deadline=budget.deadline,
        max_rounds=16,
        deep=True,
        collect_actions=True,
    )
    actions.extend(final_knowledge.actions)
    final_dict = final_knowledge.to_dict()

    changed_cells = sum(
        a != b
        for a, b in zip(board.assignments, final_knowledge.board.assignments)
    )

    return {
        "status": "progress" if changed_cells or actions else "stuck",
        "title": (
            "Logical solver made progress"
            if changed_cells or actions
            else "Logical solver reached a proof fixed point"
        ),
        "explanation": (
            f"Stopped because: {stopped_reason}. "
            "No unresolved option was ever committed as a guess."
        ),
        "actions": actions,
        "branches": branch_history,
        "iterations": min(max_iterations, len(branch_history) + 1),
        "nodes": budget.nodes,
        "changed_cells": changed_cells,
        "stopped_reason": stopped_reason,
        "knowledge": final_dict,
        "internal_board": final_dict["internal_board"],
    }
