import os
import re
import json
import uuid
import time
import zipfile
import subprocess
import threading
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file, Response

app = Flask(__name__)

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# {session_id: {url: {status, filename, error}}}
sessions = {}
# Auto-cleanup tracking: {filepath: expiry_timestamp}
file_expiry = {}
EXPIRY_SECONDS = 3600


def cleanup_expired():
    while True:
        time.sleep(300)
        now = time.time()
        expired = [p for p, exp in file_expiry.items() if now > exp]
        for p in expired:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass
            file_expiry.pop(p, None)
        # Clean empty session dirs
        for d in DOWNLOAD_DIR.iterdir():
            if d.is_dir() and not any(d.iterdir()):
                try:
                    d.rmdir()
                except Exception:
                    pass


threading.Thread(target=cleanup_expired, daemon=True).start()


def extract_reel_id(url):
    m = re.search(r"/(?:reel|reels|p)/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else uuid.uuid4().hex[:11]


def download_reel(url, session_id, idx, also_mp3=False):
    session = sessions[session_id]
    reel_id = extract_reel_id(url)
    session_dir = DOWNLOAD_DIR / session_id
    session_dir.mkdir(exist_ok=True)

    out_path = session_dir / f"{reel_id}.mp4"
    session[idx]["status"] = "downloading"
    session[idx]["reel_id"] = reel_id

    try:
        cmd = [
            "yt-dlp",
            "--no-warnings",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "-o", str(out_path),
            "--no-playlist",
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or "yt-dlp failed")

        session[idx]["status"] = "done"
        session[idx]["filename"] = f"{reel_id}.mp4"
        file_expiry[str(out_path)] = time.time() + EXPIRY_SECONDS

        if also_mp3:
            mp3_path = session_dir / f"{reel_id}.mp3"
            cmd_mp3 = [
                "yt-dlp",
                "--no-warnings",
                "-x", "--audio-format", "mp3",
                "--audio-quality", "0",
                "-o", str(mp3_path).replace(".mp3", ".%(ext)s"),
                "--no-playlist",
                url,
            ]
            subprocess.run(cmd_mp3, capture_output=True, text=True, timeout=120)
            if mp3_path.exists():
                session[idx]["mp3"] = f"{reel_id}.mp3"
                file_expiry[str(mp3_path)] = time.time() + EXPIRY_SECONDS

    except Exception as e:
        session[idx]["status"] = "failed"
        session[idx]["error"] = str(e)[:200]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.json
    urls = [u.strip() for u in data.get("urls", []) if u.strip()]
    also_mp3 = data.get("mp3", False)
    if not urls:
        return jsonify({"error": "No URLs"}), 400

    session_id = uuid.uuid4().hex[:12]
    sessions[session_id] = {}
    for i, url in enumerate(urls):
        sessions[session_id][i] = {"url": url, "status": "pending"}

    def run():
        for i, url in enumerate(urls):
            download_reel(url, session_id, i, also_mp3)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"session_id": session_id, "count": len(urls)})


@app.route("/api/status/<session_id>")
def status(session_id):
    s = sessions.get(session_id)
    if not s:
        return jsonify({"error": "Not found"}), 404
    return jsonify({str(k): v for k, v in s.items()})


@app.route("/api/stream/<session_id>")
def stream(session_id):
    def generate():
        while True:
            s = sessions.get(session_id)
            if not s:
                break
            yield f"data: {json.dumps({str(k): v for k, v in s.items()})}\n\n"
            if all(v["status"] in ("done", "failed") for v in s.values()):
                break
            time.sleep(1)
    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/file/<session_id>/<filename>")
def download_file(session_id, filename):
    # Sanitize
    filename = Path(filename).name
    filepath = DOWNLOAD_DIR / session_id / filename
    if not filepath.exists():
        return "Not found", 404
    return send_file(filepath, as_attachment=True)


@app.route("/api/zip/<session_id>")
def download_zip(session_id):
    session_dir = DOWNLOAD_DIR / session_id
    if not session_dir.exists():
        return "Not found", 404
    zip_path = DOWNLOAD_DIR / f"{session_id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in session_dir.iterdir():
            if f.is_file():
                zf.write(f, f.name)
    file_expiry[str(zip_path)] = time.time() + EXPIRY_SECONDS
    return send_file(zip_path, as_attachment=True, download_name="reels.zip")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
