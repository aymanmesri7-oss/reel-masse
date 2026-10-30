import os
import re
import json
import uuid
import time
import zipfile
import subprocess
import threading
from pathlib import Path
from flask import Flask, request, jsonify, send_file, Response

app = Flask(__name__)

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

sessions = {}
file_expiry = {}
EXPIRY_SECONDS = 3600

HTML = '''<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ReelGrab - Instagram Reels Downloader</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#1a1a2e;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:16px}
h1{font-size:2rem;margin:32px 0 8px;background:linear-gradient(135deg,#7c3aed,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.sub{color:#94a3b8;margin-bottom:24px;font-size:.9rem}
.container{width:100%;max-width:700px}
textarea{width:100%;min-height:200px;background:#16213e;border:1px solid #334155;border-radius:12px;color:#e2e8f0;padding:16px;font-size:14px;resize:vertical;outline:none;transition:border .2s}
textarea:focus{border-color:#7c3aed}
textarea::placeholder{color:#64748b}
.controls{display:flex;gap:12px;margin:16px 0;align-items:center;flex-wrap:wrap}
.btn{background:#7c3aed;color:#fff;border:none;padding:12px 28px;border-radius:10px;font-size:15px;cursor:pointer;font-weight:600;transition:background .2s}
.btn:hover{background:#6d28d9}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-outline{background:transparent;border:2px solid #7c3aed;color:#7c3aed}
.btn-outline:hover{background:#7c3aed22}
label.check{display:flex;align-items:center;gap:8px;color:#94a3b8;font-size:14px;cursor:pointer}
label.check input{accent-color:#7c3aed;width:18px;height:18px}
.progress-bar{width:100%;height:6px;background:#16213e;border-radius:3px;margin:16px 0;overflow:hidden}
.progress-bar .fill{height:100%;background:linear-gradient(90deg,#7c3aed,#a78bfa);border-radius:3px;transition:width .3s}
.progress-text{color:#94a3b8;font-size:13px;margin-bottom:8px}
.list{display:flex;flex-direction:column;gap:8px}
.item{background:#16213e;border-radius:10px;padding:14px 16px;display:flex;align-items:center;gap:12px;font-size:13px;word-break:break-all}
.item .url{flex:1;color:#94a3b8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.badge{padding:4px 10px;border-radius:6px;font-size:11px;font-weight:600;text-transform:uppercase;white-space:nowrap}
.badge.pending{background:#334155;color:#94a3b8}
.badge.downloading{background:#7c3aed33;color:#a78bfa}
.badge.done{background:#05966933;color:#34d399}
.badge.failed{background:#dc262633;color:#f87171}
.dl-links{display:flex;gap:6px}
.dl-links a{color:#7c3aed;text-decoration:none;font-weight:600;font-size:12px;padding:4px 8px;border:1px solid #7c3aed;border-radius:6px}
.dl-links a:hover{background:#7c3aed22}
.zip-wrap{text-align:center;margin:20px 0}
footer{margin-top:auto;padding:24px 0 8px;color:#475569;font-size:12px}
</style>
</head>
<body>
<h1>ReelGrab</h1>
<p class="sub">Colle tes liens de reels Instagram, un par ligne</p>
<div class="container">
  <textarea id="urls" placeholder="https://www.instagram.com/reel/ABC123...&#10;https://www.instagram.com/reel/DEF456...&#10;..."></textarea>
  <div class="controls">
    <button class="btn" id="startBtn" onclick="startDownload()">Telecharger tout</button>
    <label class="check"><input type="checkbox" id="mp3Check"> Aussi en MP3</label>
  </div>
  <div id="progressArea" style="display:none">
    <div class="progress-text" id="progressText">0 / 0</div>
    <div class="progress-bar"><div class="fill" id="progressFill" style="width:0%"></div></div>
  </div>
  <div class="list" id="list"></div>
  <div class="zip-wrap" id="zipWrap" style="display:none">
    <button class="btn btn-outline" id="zipBtn" onclick="downloadZip()">Telecharger tout en .zip</button>
  </div>
</div>
<footer>ReelGrab &mdash; fichiers supprimes apres 1h</footer>
<script>
let sessionId=null,totalCount=0;
function startDownload(){
  const raw=document.getElementById('urls').value.trim();
  if(!raw)return;
  const urls=raw.split('\\n').map(u=>u.trim()).filter(u=>u);
  if(!urls.length)return;
  totalCount=urls.length;
  document.getElementById('startBtn').disabled=true;
  document.getElementById('progressArea').style.display='block';
  document.getElementById('zipWrap').style.display='none';
  document.getElementById('list').innerHTML='';
  urls.forEach((url,i)=>{
    const div=document.createElement('div');div.className='item';div.id='item-'+i;
    div.innerHTML=`<span class="url">${esc(url)}</span><span class="badge pending">En attente</span>`;
    document.getElementById('list').appendChild(div);
  });
  fetch('/api/download',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({urls,mp3:document.getElementById('mp3Check').checked})})
  .then(r=>r.json()).then(data=>{
    if(data.error){alert(data.error);return;}
    sessionId=data.session_id;pollSSE();
  });
}
function pollSSE(){
  const es=new EventSource('/api/stream/'+sessionId);
  es.onmessage=(e)=>{
    const items=JSON.parse(e.data);let done=0,failed=0;
    for(const[idx,info]of Object.entries(items)){
      const el=document.getElementById('item-'+idx);if(!el)continue;
      const sm={pending:'En attente',downloading:'En cours',done:'Termine',failed:'Echoue'};
      let h=`<span class="url">${esc(info.url)}</span><span class="badge ${info.status}">${sm[info.status]||info.status}</span>`;
      if(info.status==='done'){h+=`<span class="dl-links"><a href="/api/file/${sessionId}/${info.filename}" download>MP4</a>`;
        if(info.mp3)h+=`<a href="/api/file/${sessionId}/${info.mp3}" download>MP3</a>`;h+=`</span>`;done++;}
      if(info.status==='failed')failed++;
      el.innerHTML=h;
    }
    const finished=done+failed;
    document.getElementById('progressText').textContent=`${finished} / ${totalCount}`;
    document.getElementById('progressFill').style.width=`${(finished/totalCount)*100}%`;
    if(finished===totalCount){es.close();document.getElementById('startBtn').disabled=false;
      if(done>0)document.getElementById('zipWrap').style.display='block';}
  };
  es.onerror=()=>{es.close();document.getElementById('startBtn').disabled=false;};
}
function downloadZip(){if(sessionId)window.location='/api/zip/'+sessionId;}
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML;}
</script>
</body>
</html>'''


def cleanup_expired():
    while True:
        time.sleep(300)
        now = time.time()
        for p in [p for p, exp in file_expiry.items() if now > exp]:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass
            file_expiry.pop(p, None)
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
        cmd = ["yt-dlp", "--no-warnings",
               "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
               "--merge-output-format", "mp4", "-o", str(out_path), "--no-playlist", url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or "yt-dlp failed")
        session[idx]["status"] = "done"
        session[idx]["filename"] = f"{reel_id}.mp4"
        file_expiry[str(out_path)] = time.time() + EXPIRY_SECONDS
        if also_mp3:
            mp3_path = session_dir / f"{reel_id}.mp3"
            subprocess.run(["yt-dlp", "--no-warnings", "-x", "--audio-format", "mp3",
                            "--audio-quality", "0", "-o",
                            str(mp3_path).replace(".mp3", ".%(ext)s"),
                            "--no-playlist", url],
                           capture_output=True, text=True, timeout=120)
            if mp3_path.exists():
                session[idx]["mp3"] = f"{reel_id}.mp3"
                file_expiry[str(mp3_path)] = time.time() + EXPIRY_SECONDS
    except Exception as e:
        session[idx]["status"] = "failed"
        session[idx]["error"] = str(e)[:200]


@app.route("/")
def index():
    return HTML


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.json
    urls = [u.strip() for u in data.get("urls", []) if u.strip()]
    also_mp3 = data.get("mp3", False)
    if not urls:
        return jsonify({"error": "No URLs"}), 400
    session_id = uuid.uuid4().hex[:12]
    sessions[session_id] = {i: {"url": url, "status": "pending"} for i, url in enumerate(urls)}
    threading.Thread(target=lambda: [download_reel(url, session_id, i, also_mp3) for i, url in enumerate(urls)], daemon=True).start()
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
    filepath = DOWNLOAD_DIR / session_id / Path(filename).name
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
    app.run(host="0.0.0.0", port=port, threaded=True)
