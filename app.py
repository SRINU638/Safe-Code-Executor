from flask import Flask, request, jsonify
import subprocess
import os
import uuid
import shlex

app = Flask(__name__)

@app.route('/')
def home():
    return "Flask server is running!"

@app.route('/run', methods=['POST'])
def run_code():
    data = request.get_json(silent=True) or {}
    code = data.get("code", "")

    # Basic validations
    if not isinstance(code, str):
        return jsonify({"error": "code must be a string"}), 400
    if len(code) > 5000:
        return jsonify({"error": "Code too long (max 5000 chars)"}), 400

    # Ensure tmp folder exists (no spaces in path recommended)
    tmp_folder = "tmp"
    os.makedirs(tmp_folder, exist_ok=True)

    # Write code to a unique file
    file_id = uuid.uuid4().hex[:12]
    file_path = os.path.join(tmp_folder, f"{file_id}.py")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(code)

    # Container name so we can forcibly remove it on timeout
    container_name = f"runpy-{file_id}"

    # Docker command (mount tmp read-only)
    # Note: use :ro to avoid container writing host files.
    cmd = [
        "docker", "run", "--rm",
        "--name", container_name,
        "--network", "none",           # block internet
        "--memory=128m",               # memory limit
        "--memory-swap=128m",          # prevent swap bypass
        "--pids-limit=64",             # prevent forkbombs
        "--cpus=1",                    # CPU limit
        "-v", f"{os.getcwd()}/tmp:/scripts:ro",
        "python:3.11-slim",
        "python", f"/scripts/{file_id}.py"
    ]

    # Run docker with a host-enforced timeout so Flask never blocks indefinitely
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        # If docker run hangs, forcibly remove the container (best-effort)
        try:
            subprocess.run(["docker", "rm", "-f", container_name],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except Exception:
            pass
        return jsonify({"output": "", "error": "Execution timed out after 10 seconds"}), 200

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    rc = proc.returncode

    # Map some common failure modes to friendly messages
    if rc == 0:
        return jsonify({"output": stdout.rstrip("\n"), "error": ""}), 200

    # OOM / killed by signal is often exit code 137 (128 + 9)
    if rc == 137 or "Killed" in stderr or "Out of memory" in stderr or "MemoryError" in stderr:
        return jsonify({"output": stdout.rstrip("\n"), "error": "Memory limit exceeded (container killed)"}), 200

    # Generic non-zero exit
    return jsonify({
        "output": stdout.rstrip("\n"),
        "error": stderr.strip(),
        "exit_code": rc
    }), 200

if __name__ == "__main__":
    # run on all interfaces (WSL/Windows friendly)
    app.run(host="0.0.0.0", port=5000)
