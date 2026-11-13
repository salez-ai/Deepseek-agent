"""
DeepSeek Agent - native messaging host ("the helper").

Chrome starts this program when the extension asks it to (chrome.runtime.sendNativeMessage) and
talks to it over stdin/stdout using length-prefixed JSON. It exists so that nobody has to open a
terminal: it starts the bridge in the background, returns the token, and lists folders so the
extension can offer a folder picker.

It deliberately does very little. Commands: hello, status, start, stop, list_dir, log.
There is no command to run programs or read files. Only the extension named in the host manifest
(allowed_origins) can talk to it. Standard library only.
"""
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HELPER_VERSION = 1
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MAX_REQUEST = 1024 * 1024            # our requests are tiny; refuse anything bigger
MAX_REPLY = 900 * 1024               # Chrome refuses host messages over 1 MB
PORT_RANGE = range(8765, 8801)
IDLE_MINUTES = 120                   # a bridge started by the helper stops itself after 2 idle hours
START_TIMEOUT = 15
LOG_LIMIT = 1024 * 1024
HIDDEN_DIRS = {"$recycle.bin", "system volume information", "$windows.~bt"}


class HostError(Exception):
    def __init__(self, message, code="error"):
        super().__init__(message)
        self.code = code


def dsagent_dir():
    return Path.home() / ".dsagent"


# ---------------------------------------------------------------------------
# protocol
# ---------------------------------------------------------------------------
def read_message(stream):
    raw = stream.read(4)
    if len(raw) < 4:
        return None
    (n,) = struct.unpack("=I", raw)
    if n > MAX_REQUEST:
        raise HostError("request too large", "too_large")
    data = stream.read(n)
    if len(data) < n:
        return None
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise HostError("request is not valid JSON", "bad_request")
    if not isinstance(obj, dict):
        raise HostError("request must be a JSON object", "bad_request")
    return obj


def send_message(stream, obj):
    data = json.dumps(obj).encode("utf-8")
    if len(data) > MAX_REPLY:
        data = json.dumps({"ok": False, "code": "too_large", "error": "reply too large"}).encode("utf-8")
    stream.write(struct.pack("=I", len(data)))
    stream.write(data)
    stream.flush()


# ---------------------------------------------------------------------------
# talking to the bridge
# ---------------------------------------------------------------------------
def load_token():
    from bridge import load_token as _load
    return _load()[0]


def http_json(port, path, token, method="GET", timeout=1.5):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=b"{}" if method == "POST" else None,
                                 headers={"X-Agent-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def info_path():
    return dsagent_dir() / "bridge.json"


def running_bridge():
    """The bridge that is actually answering, or None. Cleans up a stale info file."""
    try:
        info = json.loads(info_path().read_text(encoding="utf-8"))
        port = int(info["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    ping = http_json(port, "/ping", load_token())
    if not ping or not ping.get("ok") or ping.get("pid") != info.get("pid"):
        try:
            info_path().unlink()
        except OSError:
            pass
        return None
    return {"port": port, "root": ping["root"], "pid": ping["pid"], "version": ping.get("version"),
            "managed": bool(info.get("managed"))}


def same_dir(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


# ---------------------------------------------------------------------------
# folders
# ---------------------------------------------------------------------------
def check_root(root):
    from bridge import risky_root_reason
    if not isinstance(root, str) or not root.strip():
        raise HostError("No project folder was given.", "bad_root")
    p = Path(os.path.expanduser(root.strip()))
    if not p.is_absolute():
        raise HostError("The project folder must be a full path (for example C:\\Users\\you\\code\\myapp).", "bad_root")
    if not p.exists():
        raise HostError(f"That folder does not exist: {p}", "not_found")
    if not p.is_dir():
        raise HostError(f"That is a file, not a folder: {p}", "not_dir")
    real = Path(os.path.realpath(p))
    why = risky_root_reason(real)
    if why:
        raise HostError(f"Refusing to use {real} as the project folder: {why}. Choose the folder of one project.", "risky_root")
    return real


def drive_roots():
    if os.name != "nt":
        return []
    if hasattr(os, "listdrives"):
        return [d for d in os.listdrives()]
    return [f"{c}:\\" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.isdir(f"{c}:\\")]


def cmd_list_dir(req):
    home = Path.home()
    raw = req.get("path") or ""
    p = Path(os.path.expanduser(raw)) if raw else home
    if not p.is_absolute():
        raise HostError("Give a full path.", "bad_path")
    if not p.exists():
        raise HostError(f"Not found: {p}", "not_found")
    if not p.is_dir():
        raise HostError(f"Not a folder: {p}", "not_dir")
    names, truncated = [], False
    try:
        with os.scandir(p) as it:
            for e in it:
                if e.name.startswith(".") or e.name.lower() in HIDDEN_DIRS:
                    continue
                try:
                    if e.is_dir():
                        names.append(e.name)
                except OSError:
                    continue
    except PermissionError:
        raise HostError(f"No permission to read {p}", "denied")
    names.sort(key=str.lower)
    if len(names) > 500:
        names, truncated = names[:500], True
    parent = str(p.parent) if p.parent != p else None
    quick = []
    for label, sub in (("Home", ""), ("Desktop", "Desktop"), ("Documents", "Documents"), ("Downloads", "Downloads"),
                       ("Projects", "Projects"), ("projects", "projects"), ("code", "code"), ("dev", "dev"),
                       ("src", "src"), ("repos", "repos"), ("workspace", "workspace")):
        q = home / sub if sub else home
        if q.is_dir() and not any(x["path"] == str(q) for x in quick):
            quick.append({"name": label, "path": str(q)})
    return {"ok": True, "path": str(p), "parent": parent, "dirs": names, "truncated": truncated,
            "quick": quick, "roots": drive_roots(), "sep": os.sep}


# ---------------------------------------------------------------------------
# starting and stopping the bridge
# ---------------------------------------------------------------------------
class StartLock:
    """Only one start at a time (several tabs may ask together). A directory is an atomic lock."""

    def __enter__(self):
        self.path = dsagent_dir() / "bridge.lock"
        dsagent_dir().mkdir(parents=True, exist_ok=True)
        deadline = time.time() + 25
        while True:
            try:
                os.mkdir(self.path)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > 60:      # left behind by a crash
                        os.rmdir(self.path)
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise HostError("Another start is taking too long. Try again in a moment.", "busy")
                time.sleep(0.2)

    def __exit__(self, *exc):
        try:
            os.rmdir(self.path)
        except OSError:
            pass


def port_is_free(port):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def choose_port(preferred):
    order = ([preferred] if preferred else []) + [p for p in PORT_RANGE if p != preferred]
    for p in order:
        if port_is_free(p):
            return p
    raise HostError(f"No free port between {PORT_RANGE.start} and {PORT_RANGE.stop - 1}.", "no_port")


def login_path():
    """PATH as the user's terminal sees it. A browser started from the Dock/desktop has a minimal one,
    which would hide node, git, python and friends from the commands the agent runs."""
    current = os.environ.get("PATH", "")
    if os.name == "nt":
        return current
    extra = []
    shell = os.environ.get("SHELL") or "/bin/sh"
    try:
        out = subprocess.run([shell, "-ilc", 'printf "%s" "$PATH"'], stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=6).stdout
        lines = out.strip().splitlines()          # profile scripts may print noise first; PATH is the last line
        if lines:
            extra += [x for x in lines[-1].split(":") if x]
    except (OSError, subprocess.SubprocessError):
        pass
    if sys.platform == "darwin":
        extra += ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin"]
    merged, seen = [], set()
    for d in current.split(os.pathsep) + extra:
        if d and d not in seen:
            seen.add(d)
            merged.append(d)
    return os.pathsep.join(merged)


def spawn_bridge(root, port):
    log_path = dsagent_dir() / "bridge.log"
    try:
        if log_path.exists() and log_path.stat().st_size > LOG_LIMIT:
            log_path.write_text("", encoding="utf-8")
    except OSError:
        pass
    log = open(log_path, "ab")
    log.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} starting bridge for {root} on port {port}\n".encode("utf-8"))
    log.flush()
    cmd = [sys.executable, str(HERE / "bridge.py"), "--root", str(root), "--port", str(port),
           "--idle-timeout", str(IDLE_MINUTES), "--managed"]
    env = dict(os.environ, PATH=login_path(), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True, cwd=str(HERE), env=env)
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200 | 0x08000000     # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
        try:
            return subprocess.Popen(cmd, creationflags=flags | 0x01000000, **kwargs)   # | BREAKAWAY_FROM_JOB
        except OSError:
            return subprocess.Popen(cmd, creationflags=flags, **kwargs)
    return subprocess.Popen(cmd, start_new_session=True, **kwargs)


def log_tail(lines=25):
    p = dsagent_dir() / "bridge.log"
    try:
        data = p.read_bytes()[-20000:].decode("utf-8", "replace")
    except OSError:
        return ""
    return "\n".join(data.strip().splitlines()[-lines:])


def stop_bridge(info, token):
    http_json(info["port"], "/shutdown", token, method="POST")
    deadline = time.time() + 6
    while time.time() < deadline:
        if not http_json(info["port"], "/ping", token, timeout=0.5):
            return True
        time.sleep(0.2)
    ping = http_json(info["port"], "/ping", token)
    if ping and ping.get("pid") == info["pid"]:         # only ever kill the process that answered
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(info["pid"]), "/T", "/F"], capture_output=True)
            else:
                os.kill(info["pid"], signal.SIGTERM)
        except OSError:
            pass
        time.sleep(0.5)
    return not http_json(info["port"], "/ping", token, timeout=0.5)


def cmd_status(_req):
    info = running_bridge()
    if not info:
        return {"ok": True, "running": False}
    return {"ok": True, "running": True, **info}


def cmd_start(req):
    root = check_root(req.get("root"))
    token = load_token()
    with StartLock():
        cur = running_bridge()
        if cur and same_dir(cur["root"], root):
            return {"ok": True, "port": cur["port"], "root": cur["root"], "token": token, "pid": cur["pid"], "reused": True}
        if cur and cur["managed"]:
            if not stop_bridge(cur, token):
                raise HostError("The bridge for the previous project would not stop.", "stop_failed")
        preferred = req.get("port") if isinstance(req.get("port"), int) else 8765
        if cur and not cur["managed"]:
            preferred = None                      # a bridge started from a terminal keeps its port
        port = choose_port(preferred)
        proc = spawn_bridge(root, port)
        deadline = time.time() + START_TIMEOUT
        while time.time() < deadline:
            if proc.poll() is not None:
                tail = log_tail(8)
                raise HostError("The bridge stopped right after starting." + (f"\n{tail}" if tail else ""), "start_failed")
            ping = http_json(port, "/ping", token)
            if ping and ping.get("ok") and ping.get("pid") == proc.pid:
                return {"ok": True, "port": port, "root": ping["root"], "token": token, "pid": proc.pid, "reused": False}
            time.sleep(0.15)
        raise HostError("The bridge did not answer in time." + (f"\n{log_tail(8)}" if log_tail(8) else ""), "start_timeout")


def cmd_stop(_req):
    cur = running_bridge()
    if not cur:
        return {"ok": True, "stopped": False}
    if not cur["managed"]:
        raise HostError("The running bridge was started from a terminal window; close that window to stop it.", "not_managed")
    return {"ok": True, "stopped": stop_bridge(cur, load_token())}


def cmd_hello(_req):
    return {"ok": True, "version": HELPER_VERSION, "python": sys.version.split()[0], "platform": sys.platform,
            "home": str(Path.home()), "app_dir": str(HERE), "bridge_present": (HERE / "bridge.py").exists()}


def cmd_log(_req):
    return {"ok": True, "log": log_tail(40)}


COMMANDS = {"hello": cmd_hello, "status": cmd_status, "start": cmd_start, "stop": cmd_stop,
            "list_dir": cmd_list_dir, "log": cmd_log}


def handle(req):
    name = req.get("cmd")
    fn = COMMANDS.get(name)
    if not fn:
        return {"ok": False, "code": "unknown_command", "error": f"unknown command {name!r}"}
    try:
        return fn(req)
    except HostError as e:
        return {"ok": False, "code": e.code, "error": str(e)}
    except Exception as e:                          # never crash silently: the extension shows this text
        return {"ok": False, "code": "internal", "error": f"{type(e).__name__}: {e}"}


def main():
    out = sys.stdout.buffer
    sys.stdout = sys.stderr                         # a stray print() must never corrupt the message stream
    stdin = sys.stdin.buffer
    if sys.platform == "win32":
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(out.fileno(), os.O_BINARY)
    while True:
        try:
            req = read_message(stdin)
        except HostError as e:
            send_message(out, {"ok": False, "code": e.code, "error": str(e)})
            return
        if req is None:
            return
        send_message(out, handle(req))


if __name__ == "__main__":
    main()
