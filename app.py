from pathlib import Path
import time

from flask import Flask, jsonify, request, send_from_directory

from solver.engine import analyze_snapshot
from solver.model import BoardState
from solver.candidates import analyze_state_domain
from solver.lookahead import analyze_lookahead
from solver.knowledge import propagate_knowledge

ROOT = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=None)


@app.get("/")
def home():
    return send_from_directory(ROOT, "index.html")


@app.get("/<path:path>")
def static_files(path):
    return send_from_directory(ROOT, path)


@app.post("/api/analyze")
def analyze_api():
    try:
        payload = request.get_json(force=True)
        return jsonify(analyze_snapshot(payload))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        app.logger.exception("Unexpected solver error")
        return jsonify({"error": "Internal solver error: " + str(exc)}), 500


@app.post("/api/knowledge")
def knowledge_api():
    try:
        payload = request.get_json(force=True)
        board = BoardState.from_snapshot(payload)
        time_budget = float(payload.get("time_budget", 5.0))
        max_rounds = max(1, min(int(payload.get("max_rounds", 12)), 50))
        deep_value = payload.get("deep", True)
        if not isinstance(deep_value, bool):
            raise ValueError("deep must be true or false")

        result = propagate_knowledge(
            board,
            deadline=time.monotonic() + max(0.5, min(time_budget, 20.0)),
            max_rounds=max_rounds,
            deep=deep_value,
            collect_actions=True,
        )
        return jsonify(result.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        app.logger.exception("Unexpected solver error")
        return jsonify({"error": "Internal solver error: " + str(exc)}), 500


@app.post("/api/lookahead")
def lookahead_api():
    try:
        payload = request.get_json(force=True)
        board = BoardState.from_snapshot(payload)
        depth = int(payload.get("depth", 2))
        time_budget = float(payload.get("time_budget", 8.0))
        max_nodes = int(payload.get("max_nodes", 28))
        return jsonify(
            analyze_lookahead(
                board,
                depth=depth,
                time_budget=time_budget,
                max_nodes=max_nodes,
            )
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        app.logger.exception("Unexpected solver error")
        return jsonify({"error": "Internal solver error: " + str(exc)}), 500


@app.post("/api/domain")
def domain_api():
    try:
        payload = request.get_json(force=True)
        state_id = payload.get("state_id", payload.get("activeRegion"))
        if state_id is None:
            raise ValueError("Select a state first.")
        board = BoardState.from_snapshot(payload)
        domain = analyze_state_domain(board, int(state_id), time_limit=2.5)
        return jsonify(domain.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        app.logger.exception("Unexpected solver error")
        return jsonify({"error": "Internal solver error: " + str(exc)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
