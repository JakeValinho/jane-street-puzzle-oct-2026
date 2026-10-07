import pytest

from solver.model import BoardState
from solver.puzzle import CLUES, N


def empty_snapshot():
    return {
        "assignments": [None] * (N * N),
        "manualCapitols": {},
    }


def test_puzzle_clue_transcription_sanity():
    assert len(CLUES) == 29
    assert CLUES[(5, 7)] == 0   # r6c8
    assert CLUES[(10, 5)] == 0  # r11c6
    assert CLUES[(2, 5)] == 1   # r3c6
    assert CLUES[(3, 6)] == 1   # r4c7
    assert CLUES[(4, 10)] == 1  # r5c11
    assert CLUES[(10, 8)] == 77
    assert CLUES[(10, 9)] == 61


def test_snapshot_rejects_nonpositive_state_ids():
    snapshot = empty_snapshot()
    snapshot["assignments"][0] = 0
    with pytest.raises(ValueError, match="positive"):
        BoardState.from_snapshot(snapshot)


def test_snapshot_rejects_out_of_range_capitol():
    snapshot = empty_snapshot()
    snapshot["manualCapitols"] = {"1": N * N}
    with pytest.raises(ValueError, match="capitol cell index"):
        BoardState.from_snapshot(snapshot)


def test_snapshot_rejects_out_of_range_forbidden_coordinate():
    snapshot = empty_snapshot()
    snapshot["forbiddenByState"] = {"1": [[12, 1]]}
    with pytest.raises(ValueError, match="11x11"):
        BoardState.from_snapshot(snapshot)
