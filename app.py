"""P-Bandai stock tracker - dashboard + background checker in one process.

Usage:
    python app.py                 start the dashboard and begin watching
    python app.py once            run a single pass and exit (used by CI)
    python app.py test-alert      send a test Telegram message
    python app.py add <url>       add a listing from the command line
    python app.py remove <id>     remove a listing
    python app.py list            print the watchlist
"""

from __future__ import annotations

import argparse
import glob
import os
import random
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import checker
import engine
import notify
import report
import store
from config import BASE_DIR, load_config, telegram_configured

# The local loop keeps its own history so it never fights the cloud over the
# committed one. This file is inside debug/, which is git-ignored.
LOCAL_HISTORY = BASE_DIR / "debug" / "local-history.log"

LOG_LINES: list[str] = []
LOG_LOCK = threading.Lock()
WAKE = threading.Event()
STATUS = {"running": False, "last_pass": None, "next_pass": None, "passes": 0,
          "interval": None, "backoff": 0}


def log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line, flush=True)
    with LOG_LOCK:
        LOG_LINES.append(line)
        del LOG_LINES[:-300]


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

def worker(cfg, rules):
    STATUS["running"] = True
    fetcher = None
    consecutive_failures = 0
    backoff_steps = 0            # doubles the wait each time the site pushes back

    base = cfg["check_interval_seconds"]
    ceiling = cfg.get("max_interval_seconds", 900)

    while True:
        try:
            if fetcher is None:
                fetcher = checker.PageFetcher(cfg)
                fetcher.start()
                log("Headless browser started.")

            summary = engine.run_pass(cfg, rules, source="PC", fetcher=fetcher, log=log)
            STATUS["last_pass"] = store.now_iso()
            STATUS["passes"] += 1
            consecutive_failures = 0
            try:
                report.append_history(summary, "PC", path=LOCAL_HISTORY)
            except OSError:
                pass

            # If P-Bandai stops rendering for us, backing off is the only thing
            # that helps. Retrying harder is what got us throttled to begin with.
            if cfg.get("backoff_enabled", True):
                unreadable = summary.get("unreadable", 0)
                errors = summary.get("errors", 0)
                if unreadable or errors:
                    parts = []
                    if unreadable:
                        parts.append(f"{unreadable} unreadable")
                    if errors:
                        parts.append(f"{errors} failed to load")
                    backoff_steps = min(backoff_steps + 1, 4)
                    log(f"{' and '.join(parts)} - backing off to "
                        f"{int(min(base * 2 ** backoff_steps, ceiling))}s between passes. "
                        f"This usually clears itself.")
                    if fetcher:
                        fetcher.recycle_context()
                elif backoff_steps:
                    backoff_steps = 0
                    log(f"Readable again - back to {base}s between passes.")
        except Exception as exc:                          # noqa: BLE001
            consecutive_failures += 1
            log(f"Pass failed ({type(exc).__name__}: {exc}). Restarting browser.")
            if fetcher:
                fetcher.stop()
            fetcher = None
            time.sleep(min(60, 5 * consecutive_failures))

        interval = min(base * (2 ** backoff_steps), ceiling)
        interval += random.uniform(0, cfg["jitter_seconds"])
        STATUS["interval"] = int(interval)
        STATUS["backoff"] = backoff_steps
        STATUS["next_pass"] = datetime.now(timezone.utc).timestamp() + interval
        WAKE.wait(timeout=interval)
        WAKE.clear()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>P-Bandai Tracker</title>
<style>
  :root{
    --bg:#0f1115; --panel:#171a21; --panel2:#1e222b; --line:#2a2f3a;
    --text:#e6e8ee; --muted:#9aa3b2; --accent:#e8443a; --ok:#2ecc71;
    --warn:#f5a623; --dim:#5c6473;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
    font:15px/1.5 "Segoe UI",system-ui,-apple-system,sans-serif}
  .wrap{max-width:960px;margin:0 auto;padding:28px 20px 60px}
  header{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:6px}
  h1{font-size:22px;margin:0;letter-spacing:.3px}
  .sub{color:var(--muted);font-size:13px}
  .bar{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0 22px}
  button{background:var(--panel2);color:var(--text);border:1px solid var(--line);
    padding:8px 14px;border-radius:8px;cursor:pointer;font-size:13px;font-family:inherit}
  button:hover{border-color:#3c4453;background:#242936}
  button.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
  button.primary:hover{background:#f2544a}
  button:disabled{opacity:.5;cursor:not-allowed}
  .addbox{display:flex;gap:8px;flex-wrap:wrap;background:var(--panel);
    border:1px solid var(--line);border-radius:12px;padding:14px}
  input[type=text]{flex:1;min-width:240px;background:#0c0e13;border:1px solid var(--line);
    color:var(--text);padding:9px 12px;border-radius:8px;font-size:13px;font-family:inherit}
  input[type=text]:focus{outline:none;border-color:#4a5568}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
    padding:14px 16px;margin-top:12px;display:flex;gap:14px;align-items:flex-start}
  .thumb{width:64px;height:64px;border-radius:8px;object-fit:cover;background:var(--panel2);flex:none}
  .card .body{flex:1;min-width:0}
  .name{font-weight:600;margin-bottom:3px;word-break:break-word}
  .meta{color:var(--muted);font-size:12px;word-break:break-all}
  .pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;
    font-weight:700;letter-spacing:.4px;white-space:nowrap}
  .s-in_stock{background:rgba(46,204,113,.15);color:var(--ok);border:1px solid rgba(46,204,113,.4)}
  .s-sold_out{background:rgba(232,68,58,.12);color:#ff7a70;border:1px solid rgba(232,68,58,.35)}
  .s-coming_soon{background:rgba(245,166,35,.12);color:var(--warn);border:1px solid rgba(245,166,35,.35)}
  .s-ended{background:#22262f;color:var(--dim);border:1px solid var(--line)}
  .s-unknown,.s-error,.s-never{background:#22262f;color:var(--muted);border:1px solid var(--line)}
  .right{display:flex;flex-direction:column;align-items:flex-end;gap:8px;flex:none}
  .x{background:none;border:none;color:var(--dim);font-size:18px;padding:0 4px;line-height:1}
  .x:hover{color:var(--accent);background:none}
  .empty{color:var(--muted);text-align:center;padding:44px;border:1px dashed var(--line);
    border-radius:12px;margin-top:12px}
  pre{background:#0a0c10;border:1px solid var(--line);border-radius:10px;padding:12px;
    max-height:220px;overflow:auto;font-size:12px;color:var(--muted);margin-top:22px}
  .toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);
    background:var(--panel2);border:1px solid var(--line);padding:11px 18px;
    border-radius:9px;font-size:13px;opacity:0;transition:opacity .25s;pointer-events:none}
  .toast.show{opacity:1}
  .warn{background:rgba(245,166,35,.1);border:1px solid rgba(245,166,35,.4);
    color:#ffd08a;padding:11px 14px;border-radius:9px;font-size:13px;margin-bottom:16px}
  a{color:#7fb2ff}
</style></head><body>
<div class="wrap">
  <header>
    <h1>P-Bandai Stock Tracker</h1>
    <span class="sub" id="sub">loading…</span>
  </header>

  <div id="tgwarn"></div>

  <div class="addbox">
    <input type="text" id="url" placeholder="Paste a p-bandai.com listing URL…"
           onkeydown="if(event.key==='Enter')addItem()">
    <input type="text" id="label" placeholder="Nickname (optional)" style="max-width:190px;flex:0 1 190px">
    <button class="primary" onclick="addItem()">Add</button>
  </div>

  <div class="bar">
    <button onclick="post('/api/check-now',{},'Checking now…')">Check now</button>
    <button onclick="post('/api/test-alert',{},'Sending test…')">Send test alert</button>
    <button onclick="post('/api/sync',{},'Syncing to cloud…')">Sync to cloud backup</button>
  </div>

  <div id="items"></div>
  <pre id="log"></pre>
</div>
<div class="toast" id="toast"></div>

<script>
const LABEL={in_stock:"IN STOCK",sold_out:"SOLD OUT",coming_soon:"COMING SOON",
  ended:"ENDED",unknown:"UNREADABLE",error:"CHECK FAILED",never:"NOT YET CHECKED"};

function ago(iso){
  if(!iso) return "never";
  const s=Math.max(0,(Date.now()-new Date(iso).getTime())/1000);
  if(s<60) return Math.round(s)+"s ago";
  if(s<3600) return Math.round(s/60)+"m ago";
  if(s<86400) return Math.round(s/3600)+"h ago";
  return Math.round(s/86400)+"d ago";
}
function esc(s){const d=document.createElement("div");d.textContent=s==null?"":s;return d.innerHTML;}
function toast(msg){const t=document.getElementById("toast");t.textContent=msg;
  t.classList.add("show");clearTimeout(t._t);t._t=setTimeout(()=>t.classList.remove("show"),2600);}

async function post(url,body,pending){
  if(pending) toast(pending);
  try{
    const r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify(body||{})});
    const d=await r.json();
    toast(d.message||(d.ok?"Done":"Failed"));
  }catch(e){ toast("Request failed"); }
  refresh();
}
function addItem(){
  const u=document.getElementById("url");
  const l=document.getElementById("label");
  if(!u.value.trim()){toast("Paste a URL first");return;}
  post("/api/add",{url:u.value,label:l.value},"Adding…").then(()=>{u.value="";l.value="";});
}
function removeItem(id,name){
  if(!confirm("Stop tracking "+name+"?")) return;
  post("/api/remove",{id:id},"Removing…");
}

async function refresh(){
  let d;
  try{ d=await (await fetch("/api/data")).json(); }catch(e){ return; }

  const secs = d.status.interval || d.config.interval;
  const every = secs < 90 ? secs+"s" : Math.round(secs/60*10)/10+" min";
  document.getElementById("sub").textContent =
    d.items.length+" tracked · checks every "+every+
    (d.status.backoff ? " (backed off)" : "")+
    " · "+d.status.passes+" passes this session";

  document.getElementById("tgwarn").innerHTML = d.config.telegram_ok ? "" :
    '<div class="warn">Telegram is not configured yet — status is tracked but ' +
    'no alerts will be sent. Add your bot token and chat id to <code>config.json</code>, ' +
    'then restart.</div>';

  const box=document.getElementById("items");
  if(!d.items.length){
    box.innerHTML='<div class="empty">Nothing tracked yet.<br>Paste a P-Bandai listing URL above to start.</div>';
  }else{
    box.innerHTML=d.items.map(it=>{
      const st=it.status||"never";
      const name=it.label||it.title||it.id;
      return '<div class="card">'+
        (it.image?'<img class="thumb" src="'+esc(it.image)+'" onerror="this.style.visibility=\'hidden\'">':'<div class="thumb"></div>')+
        '<div class="body">'+
          '<div class="name">'+esc(name)+'</div>'+
          '<div class="meta">'+esc(it.id)+(it.price?' · '+esc(it.price):'')+'</div>'+
          '<div class="meta">checked '+ago(it.last_checked)+
            (it.signal?' · '+esc(it.signal):'')+'</div>'+
          '<div class="meta"><a href="'+esc(it.url)+'" target="_blank" rel="noopener">open listing ↗</a></div>'+
        '</div>'+
        '<div class="right">'+
          '<span class="pill s-'+esc(st)+'">'+(LABEL[st]||st)+'</span>'+
          '<button class="x" title="Remove" onclick="removeItem(\''+esc(it.id)+'\',\''+esc(name).replace(/'/g,"")+'\')">✕</button>'+
        '</div>'+
      '</div>';
    }).join("");
  }
  document.getElementById("log").textContent=d.log.join("\n");
}
refresh(); setInterval(refresh,5000);
</script></body></html>
"""


def build_app(cfg, rules):
    from flask import Flask, jsonify, request

    flask_app = Flask(__name__)

    @flask_app.get("/")
    def index():
        return PAGE

    @flask_app.get("/api/data")
    def api_data():
        state = store.load_state()
        items = []
        for entry in store.load_watchlist():
            merged = dict(entry)
            merged.update(state.get(entry["id"], {}))
            merged["url"] = entry["url"]          # watchlist wins on url
            items.append(merged)
        with LOG_LOCK:
            recent = LOG_LINES[-60:]
        return jsonify({
            "items": items,
            "log": recent,
            "status": STATUS,
            "config": {
                "interval": cfg["check_interval_seconds"],
                "telegram_ok": telegram_configured(cfg),
            },
        })

    @flask_app.post("/api/add")
    def api_add():
        data = request.get_json(silent=True) or {}
        try:
            entry = store.add_item(data.get("url", ""), (data.get("label") or "").strip())
        except ValueError as exc:
            return jsonify({"ok": False, "message": str(exc)}), 400
        log(f"Added {entry['id']}")
        WAKE.set()
        return jsonify({"ok": True, "message": f"Added {entry['id']} — checking now"})

    @flask_app.post("/api/remove")
    def api_remove():
        data = request.get_json(silent=True) or {}
        item_id = data.get("id", "")
        if store.remove_item(item_id):
            log(f"Removed {item_id}")
            return jsonify({"ok": True, "message": f"Removed {item_id}"})
        return jsonify({"ok": False, "message": "Not found"}), 404

    @flask_app.post("/api/check-now")
    def api_check_now():
        WAKE.set()
        return jsonify({"ok": True, "message": "Check triggered"})

    @flask_app.post("/api/test-alert")
    def api_test():
        if not telegram_configured(cfg):
            return jsonify({"ok": False,
                            "message": "Telegram not configured in config.json"}), 400
        try:
            notify.send_test(cfg)
        except notify.NotifyError as exc:
            return jsonify({"ok": False, "message": f"Failed: {exc}"}), 502
        return jsonify({"ok": True, "message": "Test sent — check Telegram"})

    @flask_app.post("/api/sync")
    def api_sync():
        ok, message = git_sync()
        log(f"Cloud sync: {message}")
        return jsonify({"ok": ok, "message": message})

    return flask_app


def find_git() -> str | None:
    """Locate git, including the copy bundled inside GitHub Desktop.

    GitHub Desktop ships its own git but doesn't put it on PATH, so a plain
    subprocess call to "git" fails on machines where Desktop is the only install.
    """
    found = shutil.which("git")
    if found:
        return found

    candidates = []
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        candidates += glob.glob(os.path.join(
            local, "GitHubDesktop", "app-*", "resources", "app", "git", "cmd", "git.exe"))
        candidates += glob.glob(os.path.join(
            local, "GitHubDesktop", "app-*", "resources", "app", "git", "mingw64",
            "bin", "git.exe"))
    candidates += [r"C:\Program Files\Git\cmd\git.exe",
                   r"C:\Program Files (x86)\Git\cmd\git.exe"]

    for path in sorted(candidates):
        if os.path.exists(path):
            return path
    return None


def git_sync() -> tuple[bool, str]:
    """Commit and push watchlist.json so the cloud backup watches the same items."""
    git = find_git()
    if not git:
        return False, "Git not found — commit and push from GitHub Desktop instead"

    def run(*args):
        return subprocess.run((git,) + args, cwd=str(BASE_DIR), capture_output=True,
                              text=True, timeout=90)

    try:
        if not (BASE_DIR / ".git").exists():
            return False, "No git repo here — see README for cloud backup setup"
        run("add", "watchlist.json", "state.json")
        committed = run("commit", "-m", "Update watchlist")
        if committed.returncode != 0 and "nothing to commit" not in committed.stdout.lower():
            return False, f"Commit failed: {committed.stderr.strip()[:150]}"
        pushed = run("push")
        if pushed.returncode != 0:
            return False, f"Push failed: {pushed.stderr.strip()[:150]}"
        return True, "Synced to cloud backup"
    except FileNotFoundError:
        return False, "Git is not installed"
    except subprocess.TimeoutExpired:
        return False, "Git timed out"
    except Exception as exc:                              # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def cmd_run(cfg, rules):
    threading.Thread(target=worker, args=(cfg, rules), daemon=True).start()

    flask_app = build_app(cfg, rules)
    host, port = cfg["dashboard_host"], cfg["dashboard_port"]
    url = f"http://{host}:{port}"

    log(f"Dashboard: {url}")
    if not telegram_configured(cfg):
        log("NOTE: Telegram not configured — tracking only, no alerts will be sent.")
    if cfg.get("open_browser_on_start", True):
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    import logging
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    flask_app.run(host=host, port=port, threaded=True, use_reloader=False)


def cmd_once(cfg, rules, source):
    summary = engine.run_pass(cfg, rules, source=source, log=log)
    log(f"Pass complete: {summary['checked']} checked, {summary['alerts']} alert(s), "
        f"{summary.get('unreadable', 0)} unreadable, {summary.get('errors', 0)} failed.")
    log(report.publish(summary, source))


def cmd_digest(cfg, source):
    """Periodic 'still alive' message, so silence is never ambiguous."""
    items = store.load_watchlist()
    state = store.load_state()
    lines, healthy = [], True

    for item in items:
        record = state.get(item["id"], {})
        status = record.get("status", "never")
        name = item.get("label") or record.get("title") or item["id"]
        if len(name) > 40:
            name = name[:37] + "..."
        age = report._age(record.get("last_checked"))
        lines.append(f"{name}: {checker.STATUS_LABEL.get(status, 'not yet checked')} "
                     f"({age})")
        if status in (checker.UNKNOWN, checker.ERROR):
            healthy = False

    # A stale timestamp means the schedule stopped, which no per-item status
    # would reveal on its own.
    newest = max((store.parse_iso(state.get(i["id"], {}).get("last_checked"))
                  for i in items), default=None, key=lambda d: d or datetime.min.replace(
                      tzinfo=timezone.utc))
    if items and newest:
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=timezone.utc)
        # Measured on a real free public repo, GitHub honours roughly one
        # scheduled run an hour regardless of what the cron asks for, and gaps
        # of 90+ minutes are normal. Flagging anything tighter than 3 hours
        # would cry wolf, which is worse than not warning at all.
        if (datetime.now(timezone.utc) - newest).total_seconds() > 10800:
            healthy = False
            lines.append("No check has completed in over 3 hours — "
                         "the schedule may have stopped.")
    elif items:
        healthy = False
        lines.append("No listing has ever been checked.")

    if not telegram_configured(cfg):
        print("Telegram not configured; nothing sent.")
        return
    notify.send_digest(cfg, lines, healthy, source)
    print(f"Digest sent ({'healthy' if healthy else 'needs a look'}).")


def main():
    parser = argparse.ArgumentParser(description="P-Bandai stock tracker")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="dashboard + continuous watching (default)")
    once = sub.add_parser("once", help="single pass, then exit")
    once.add_argument("--source", default="PC", help="label shown in the alert")
    sub.add_parser("test-alert", help="send a test Telegram message")
    digest = sub.add_parser("digest", help="send a 'still alive' summary to Telegram")
    digest.add_argument("--source", default="cloud backup")
    add = sub.add_parser("add", help="add a listing")
    add.add_argument("url")
    add.add_argument("--label", default="")
    rem = sub.add_parser("remove", help="remove a listing")
    rem.add_argument("item_id")
    sub.add_parser("list", help="print the watchlist")

    args = parser.parse_args()
    cfg = load_config()
    rules = checker.load_rules()
    command = args.command or "run"

    if command == "run":
        cmd_run(cfg, rules)
    elif command == "once":
        cmd_once(cfg, rules, args.source)
    elif command == "digest":
        cmd_digest(cfg, args.source)
    elif command == "test-alert":
        try:
            notify.send_test(cfg)
            print("Test message sent.")
        except notify.NotifyError as exc:
            print(f"Failed: {exc}")
            sys.exit(1)
    elif command == "add":
        try:
            entry = store.add_item(args.url, args.label)
            print(f"Added {entry['id']}")
        except ValueError as exc:
            print(f"Error: {exc}")
            sys.exit(1)
    elif command == "remove":
        print("Removed." if store.remove_item(args.item_id) else "Not found.")
    elif command == "list":
        state = store.load_state()
        for entry in store.load_watchlist():
            status = state.get(entry["id"], {}).get("status", "never checked")
            flag = "" if entry.get("enabled", True) else "  (paused)"
            print(f"{entry['id']:<16} {status:<14} {entry['url']}{flag}")


if __name__ == "__main__":
    main()
