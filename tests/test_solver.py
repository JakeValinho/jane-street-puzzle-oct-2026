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
