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
