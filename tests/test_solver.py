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

    x_pentomino = {(5, 5), (4, 4), (4, 6), (6, 4), (6, 6)}
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
