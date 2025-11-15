import psutil
from flask import Flask, jsonify
app = Flask(__name__)
import json
import os

# configuration
if os.path.exists("config.procstat.json"):
    with open("config.procstat.json", "r", encoding="utf-8") as f:
        config = json.load(f)
else:
    config = {}
# {"id": "cwd path"}

@app.route('/<id>')
def get_status(id):
    if id in config:
        path = config[id]
        try:
            for p in psutil.process_iter(["pid", "name", "cwd"]):
                try:
                    if p.info["cwd"] == path:
                        return jsonify({"status": "running", "pid": p.info["pid"], "name": p.info["name"]}), 200
                except Exception:
                    continue
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    else:
        return jsonify({"status": "error", "message": "ID not found"}), 404
    return jsonify({"status": "process not found"}), 404