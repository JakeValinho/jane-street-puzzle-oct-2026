import time
from solver.engine import analyze_snapshot
from solver.puzzle import N, index


def empty_snapshot():
    return {
        "assignments": [None] * (N * N),
        "manualCapitols": {},
    }


def test_empty_board_finds_zero_clue_capitols():
    result = analyze_snapshot(empty_snapshot())
    titles = [d["title"] for d in result["deductions"]]
    assert "r6c8 is definitely a capitol" in titles
    assert "r11c6 is definitely a capitol" in titles


def test_r6c11_is_forced_by_the_one_at_r5c11():
    result = analyze_snapshot(empty_snapshot())
    matches = [d for d in result["deductions"] if d["rule"] == "one-clue" and "r6c11" in d["title"]]
    assert matches
    assert matches[0]["option_count"] == 1


def test_two_zero_clues_cannot_share_a_state():
    s = empty_snapshot()
    s["assignments"][index((5, 7))] = 1
    s["assignments"][index((10, 5))] = 1
    result = analyze_snapshot(s)
    assert any(d["category"] == "contradiction" and "two forced capitols" in d["title"] for d in result["deductions"])


from solver.candidates import analyze_shape, analyze_state_domain
from solver.model import BoardState


def test_shape_capitol_examples_from_puzzle_statement():
    assert analyze_shape({(5, 5)})["capitol"] == (5, 5)

    one_by_three = {(5, 4), (5, 5), (5, 6)}
    assert analyze_shape(one_by_three)["valid"]
    assert analyze_shape(one_by_three)["capitol"] is None

    x_pentomino = {(5, 5), (4, 5), (6, 5), (5, 4), (5, 6)}
    assert analyze_shape(x_pentomino)["valid"]
    assert analyze_shape(x_pentomino)["capitol"] is None


def test_forced_singleton_domain_stays_singleton():
    s = empty_snapshot()
    forced = (5, 10)  # r6c11
    s["assignments"][index(forced)] = 1
    s["manualCapitols"] = {"1": index(forced)}

    board = BoardState.from_snapshot(s)
    domain = analyze_state_domain(board, 1, time_limit=1.0)

    assert domain.feasible
    assert domain.min_size == 1
    assert domain.max_size == 1
    assert domain.forced_cells == {forced}


from solver.lookahead import apply_option, analyze_lookahead


def test_partial_one_cell_state_is_not_assumed_complete():
    s = empty_snapshot()
    s["assignments"][index((0, 0))] = 1
    result = analyze_snapshot(s)
    assert not any(
        d["rule"] == "singleton-state"
        for d in result["deductions"]
    )


def test_membership_hypothesis_can_forbid_a_cell_without_assigning_it():
    board = BoardState.from_snapshot(empty_snapshot())
    board.assign(1, (0, 0))

    option = {
        "type": "state_cell_membership",
        "state": 1,
        "cell0": (0, 1),
        "cell": [1, 2],
        "value": False,
        "description": "r1c2 is not in State 1",
    }
    child = apply_option(board, option)

    assert child.state_at((0, 1)) is None
    assert child.is_forbidden(1, (0, 1))
    assert not board.is_forbidden(1, (0, 1))


def test_blank_board_lookahead_starts_with_direct_propagation():
    board = BoardState.from_snapshot(empty_snapshot())
    result = analyze_lookahead(board, depth=2, time_budget=3.0, max_nodes=12)

    assert result["status"] == "propagation"
    action_types = {action["type"] for action in result["actions"]}
    assert "zero_capitol" in action_types
    assert "singleton_capitol" in action_types


from solver.knowledge import propagate_knowledge


def test_blank_board_knowledge_creates_capitol_anchors_without_mutating_input():
    board = BoardState.from_snapshot(empty_snapshot())
    original = board.assignments[:]

    result = propagate_knowledge(
        board,
        deadline=None,
        max_rounds=3,
        deep=False,
        collect_actions=True,
    )

    # Knowledge propagation works on a clone. The user's real board remains
    # untouched until they choose to apply a proved deduction.
    assert board.assignments == original
    assert all(owner is None for owner in board.assignments)

    by_capitol = {
        state.capitol: state
        for state in result.states.values()
        if state.capitol is not None
    }

    # The two 0 clues become anchored states with a certain capitol but an
    # otherwise incomplete shape.
    assert (5, 7) in by_capitol   # r6c8
    assert (10, 5) in by_capitol  # r11c6
    assert (5, 7) in by_capitol[(5, 7)].certain_cells
    assert (10, 5) in by_capitol[(10, 5)].certain_cells
    assert by_capitol[(5, 7)].max_size > 1
    assert by_capitol[(10, 5)].max_size > 1

    # r6c11 is the forced singleton capitol from the clue 1 at r5c11.
    singleton = by_capitol[(5, 10)]
    assert singleton.certain_cells == {(5, 10)}
    assert singleton.min_size == 1
    assert singleton.max_size == 1


def test_locked_singleton_has_exactly_one_enumerated_candidate_shape():
    s = empty_snapshot()
    cell = (5, 10)
    s["assignments"][index(cell)] = 1
    s["manualCapitols"] = {"1": index(cell)}
    board = BoardState.from_snapshot(s)

    for i in range(N * N):
        other = divmod(i, N)
        if other != cell:
            board.forbid(1, other)

    domain = analyze_state_domain(
        board,
        1,
        time_limit=1.0,
        max_forced_checks=3,
        max_exclusion_checks=3,
        compute_bounds=True,
        enumerate_limit=3,
    )

    assert domain.feasible
    assert domain.candidate_count_exact
    assert domain.candidate_shapes == [{cell}]



def test_positive_clue_can_be_the_first_cell_of_a_growing_state():
    s = empty_snapshot()
    clue_cell = (0, 1)  # r1c2 has clue 8
    s["assignments"][index(clue_cell)] = 1

    result = analyze_snapshot(s)

    assert not any(
        d["category"] == "contradiction"
        and d["rule"] == "positive-clue-singleton"
        for d in result["deductions"]
    )


def test_positive_clue_cannot_be_a_proved_singleton():
    s = empty_snapshot()
    clue_cell = (0, 1)  # r1c2 has clue 8
    s["assignments"][index(clue_cell)] = 1
    s["manualCapitols"] = {"1": index(clue_cell)}
    s["forbiddenByState"] = {
        "1": [
            [r + 1, c + 1]
            for r in range(N)
            for c in range(N)
            if (r, c) != clue_cell
        ]
    }

    result = analyze_snapshot(s)

    assert any(
        d["category"] == "contradiction"
        and d["rule"] == "positive-clue-singleton"
        for d in result["deductions"]
    )



def test_deep_blank_board_propagates_beyond_initial_capitol_facts():
    board = BoardState.from_snapshot(empty_snapshot())
    result = propagate_knowledge(
        board,
        deadline=time.monotonic() + 10.0,
        max_rounds=8,
        deep=True,
        collect_actions=True,
    )

    assigned = sum(owner is not None for owner in result.board.assignments)
    assert result.contradiction is None
    assert assigned > 3, (
        f"expected ownership/state-shape reasoning to move beyond the two zero "
        f"anchors plus r6c11 singleton, but only {assigned} cells were assigned. "
        f"actions={result.actions!r}; facts={result.facts!r}"
    )
