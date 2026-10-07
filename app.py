from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from solver.engine import analyze_snapshot

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
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
