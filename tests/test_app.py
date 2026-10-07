from pathlib import Path

import pytest

from app import app
from solver.puzzle import N


def empty_snapshot():
    return {
        "assignments": [None] * (N * N),
        "manualCapitols": {},
    }


def test_home_and_static_assets_load():
    client = app.test_client()

    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="board"' in html
    assert 'id="solverStep"' in html
    assert 'id="propagateKnowledge"' in html
    assert 'id="lookAhead"' in html

    assert client.get("/js/puzzle.js").status_code == 200
    assert client.get("/js/game.js").status_code == 200
    assert client.get("/styles.css").status_code == 200


def test_analyze_api_smoke():
    client = app.test_client()
    response = client.post("/api/analyze", json=empty_snapshot())

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["summary"]["assigned_cells"] == 0
    assert payload["summary"]["unassigned_cells"] == N * N
    assert payload["deductions"]


def test_knowledge_api_smoke():
    client = app.test_client()
    payload = empty_snapshot()
    payload.update({"time_budget": 2.0, "max_rounds": 3, "deep": False})

    response = client.post("/api/knowledge", json=payload)

    assert response.status_code == 200
    result = response.get_json()
    assert result["contradiction"] is None
    assert result["states"]
    capitols = {
        tuple(state["capitol"])
        for state in result["states"].values()
        if state["capitol"] is not None
    }
    assert (6, 8) in capitols
    assert (11, 6) in capitols


def test_lookahead_api_smoke():
    client = app.test_client()
    payload = empty_snapshot()
    payload.update({"depth": 1, "time_budget": 2.0, "max_nodes": 8})

    response = client.post("/api/lookahead", json=payload)

    assert response.status_code == 200
    result = response.get_json()
    assert result["status"] in {
        "propagation",
        "forced-by-contradiction",
        "narrowed",
        "unresolved",
        "no-branch",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"assignments": [None], "manualCapitols": {}},
        {"assignments": [0] + [None] * (N * N - 1), "manualCapitols": {}},
        {
            "assignments": [None] * (N * N),
            "manualCapitols": {"1": N * N},
        },
    ],
)
def test_api_rejects_malformed_snapshots(payload):
    client = app.test_client()
    response = client.post("/api/analyze", json=payload)
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_javascript_referenced_controls_exist_in_html():
    root = Path(__file__).resolve().parents[1]
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "js" / "game.js").read_text(encoding="utf-8")

    required_ids = {
        "board",
        "stateList",
        "summary",
        "resultPanel",
        "rowResults",
        "finalAnswer",
        "activeInfo",
        "showIds",
        "showDistances",
        "capitolMode",
        "solverStep",
        "propagateKnowledge",
        "applyForced",
        "pruneState",
        "lookAhead",
        "solverOutput",
        "newState",
        "check",
        "undo",
        "redo",
        "clear",
        "exportBtn",
        "importBtn",
    }

    for element_id in required_ids:
        assert f'id="{element_id}"' in html
        assert element_id in js



def test_domain_api_smoke_for_known_singleton():
    client = app.test_client()
    payload = empty_snapshot()
    cell_index = 5 * N + 10  # r6c11
    payload["assignments"][cell_index] = 1
    payload["manualCapitols"] = {"1": cell_index}
    payload["state_id"] = 1

    response = client.post("/api/domain", json=payload)

    assert response.status_code == 200
    result = response.get_json()
    assert result["state_id"] == 1
    assert result["feasible"] is True
    assert result["min_size"] is not None
    assert result["max_size"] is not None



def test_knowledge_api_returns_board_that_can_be_applied():
    client = app.test_client()
    payload = empty_snapshot()
    payload.update({"time_budget": 3.0, "max_rounds": 5, "deep": False})

    response = client.post("/api/knowledge", json=payload)

    assert response.status_code == 200
    result = response.get_json()
    board = result["internal_board"]

    assert len(board["assignments"]) == N * N
    # The two clue-0 anchors and the forced singleton at r6c11 should be
    # materialized as real state assignments in the returned board.
    assert board["assignments"][5 * N + 7] is not None   # r6c8
    assert board["assignments"][10 * N + 5] is not None # r11c6
    assert board["assignments"][5 * N + 10] is not None # r6c11

    singleton_state = board["assignments"][5 * N + 10]
    assert str(singleton_state) in board["manualCapitols"]
    assert board["manualCapitols"][str(singleton_state)] == 5 * N + 10
    assert len(board["forbiddenByState"][str(singleton_state)]) == N * N - 1
