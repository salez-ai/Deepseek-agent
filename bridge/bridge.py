"""
DeepSeek Agent - local bridge.

Runs on your machine, bound to 127.0.0.1 only. The browser extension sends it
tool calls (read/write/edit files, search, run commands) and it executes them
inside ONE project folder.

Standard library only. Python 3.9+.

    python bridge.py --root /path/to/your/project   (--root is required)
"""
import argparse
import base64
import fnmatch
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from skills import BUILTIN_TOOLS, RESERVED_TOOLS, SkillError, SkillStore, ToolError

MAX_READ_CHARS = 60_000
MAX_OUTPUT_CHARS = 20_000
MAX_SEARCH_HITS = 100
MAX_LIST_ENTRIES = 500
BRIDGE_VERSION = "0.2.0"
MAX_BODY_BYTES = 16 * 1024 * 1024
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", ".next", ".idea", ".vscode", "dist", "build", "target",
    ".dsagent_backups",
}
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_SAVE_BYTES = 10 * 1024 * 1024        # pictures the extension asks us to store
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _int(value, default, lo, hi):
    if value is None or str(value).strip() == "":
        return default
    try:
        return max(lo, min(hi, int(str(value).strip())))
    except ValueError:
        raise ToolError(f"expected a number, got {value!r}")


def _truthy(value):
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def risky_root_reason(root):
    """Return why this folder is too broad/sensitive to hand to an agent, or ''."""
    norm = lambda p: os.path.normcase(os.path.realpath(p))
    r = norm(root)
    if r == norm(Path(r).anchor):
        return "it is a drive/filesystem root"
    if r == norm(Path.home()):
        return "it is your whole home folder"
    protected = []
    for var in ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        if os.environ.get(var):
            protected.append(norm(os.environ[var]))
    if os.name == "posix":
        protected += ["/etc", "/usr", "/bin", "/sbin", "/lib", "/var", "/boot", "/sys", "/proc"]
    for p in protected:
        if r == p or r.startswith(p + os.sep):
            return "it is inside a system folder"
    return ""


class Workspace:
    def __init__(self, root, allow_commands=True, backups=True, skills=None):
        self.root = Path(os.path.realpath(root))
        if not self.root.is_dir():
            raise SystemExit(f"Project root is not a directory: {root}")
        reason = risky_root_reason(self.root)
        if reason:
            raise SystemExit(
                f"Refusing to use {self.root} as the project root: {reason}.\n"
                f"Point --root at the folder of ONE project, e.g. C:\\Users\\you\\code\\myapp")
        self.allow_commands = allow_commands
        self.backups = backups
        self.skills = skills
        self.stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    # ---- helpers ---------------------------------------------------------
    def resolve(self, rel, must_exist=False):
        rel = (rel or ".").strip().strip("\"'")
        p = Path(rel) if os.path.isabs(rel) else self.root / rel
        p = Path(os.path.realpath(p))
        try:
            p.relative_to(self.root)
        except ValueError:
            raise ToolError(f"path is outside the project root: {rel}")
        if must_exist and not p.exists():
            raise ToolError(f"not found: {rel}")
        return p

    def rel(self, p):
        r = p.relative_to(self.root).as_posix()
        return r or "."

    def backup(self, p):
        if not (self.backups and p.is_file()):
            return
        dest = self.root / ".dsagent_backups" / self.stamp / self.rel(p)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():  # keep the first (oldest) version of this session
            shutil.copy2(p, dest)

    @staticmethod
    def read_text(p):
        data = p.read_bytes()
        if b"\0" in data[:4096]:
            raise ToolError(f"{p.name} looks like a binary file")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise ToolError(f"{p.name} is not valid UTF-8")

    # ---- tools -----------------------------------------------------------
    def list_dir(self, a):
        base = self.resolve(a.get("path", "."), must_exist=True)
        if not base.is_dir():
            raise ToolError("not a directory")
        depth = _int(a.get("depth"), 2, 1, 6)
        lines, truncated = [], False

        def walk(d, level):
            nonlocal truncated
            try:
                entries = sorted(d.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
            except PermissionError:
                return
            for e in entries:
                if len(lines) >= MAX_LIST_ENTRIES:
                    truncated = True
                    return
                indent = "  " * level
                if e.is_dir():
                    if e.name in SKIP_DIRS:
                        lines.append(f"{indent}{e.name}/  (skipped)")
                        continue
                    lines.append(f"{indent}{e.name}/")
                    if level + 1 < depth:
                        walk(e, level + 1)
                else:
                    try:
                        size = e.stat().st_size
                    except OSError:
                        size = 0
                    lines.append(f"{indent}{e.name}  ({size} B)")

        walk(base, 0)
        if truncated:
            lines.append(f"... truncated at {MAX_LIST_ENTRIES} entries; list a subfolder")
        return f"{self.rel(base)}/\n" + ("\n".join(lines) if lines else "(empty)")

    def read_file(self, a):
        p = self.resolve(a.get("path"), must_exist=True)
        if p.is_dir():
            raise ToolError("that is a directory; use list_dir")
        text = self.read_text(p)
        lines = text.splitlines()
        total = len(lines)
        if total == 0:
            return f"{self.rel(p)} (empty file)"
        start = _int(a.get("start_line"), 1, 1, 10**9)
        end = _int(a.get("end_line"), total, 1, 10**9)
        end = min(end, total)
        if start > total:
            raise ToolError(f"start_line {start} is past the end ({total} lines)")
        chunk = "\n".join(lines[start - 1:end])
        note = ""
        if len(chunk) > MAX_READ_CHARS:
            chunk = chunk[:MAX_READ_CHARS]
            note = (f"\n[output cut at {MAX_READ_CHARS} characters - "
                    f"use start_line/end_line to read the rest]")
        return f"{self.rel(p)} (lines {start}-{end} of {total})\n{chunk}{note}"

    def write_file(self, a):
        if a.get("content") is None:
            raise ToolError("write_file needs content")
        p = self.resolve(a.get("path"))
        if p.is_dir():
            raise ToolError("that path is a directory")
        content = a["content"].replace("\r\n", "\n")
        existed = p.exists()
        if existed:
            if b"\r\n" in p.read_bytes()[:200_000]:
                content = content.replace("\n", "\r\n")
            self.backup(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content.encode("utf-8"))
        n_lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
        verb = "Overwrote" if existed else "Created"
        return f"{verb} {self.rel(p)} ({n_lines} lines, {len(content.encode('utf-8'))} bytes)"

    def replace_in_file(self, a):
        p = self.resolve(a.get("path"), must_exist=True)
        if p.is_dir():
            raise ToolError("that is a directory")
        edits = a.get("edits")
        if not isinstance(edits, list) or not edits:
            raise ToolError("replace_in_file needs at least one SEARCH/REPLACE pair")
        raw = self.read_text(p)
        crlf = "\r\n" in raw
        text = raw.replace("\r\n", "\n")
        report = []
        for k, ed in enumerate(edits, 1):
            s = str(ed.get("search", "")).replace("\r\n", "\n")
            r = str(ed.get("replace", "")).replace("\r\n", "\n")
            if s.strip() == "":
                raise ToolError(f"edit {k}: SEARCH is empty")
            count = text.count(s)
            if count > 1:
                raise ToolError(
                    f"edit {k}: SEARCH matches {count} places. Include more surrounding "
                    f"lines so it matches exactly once. Nothing was changed.")
            if count == 1:
                idx = text.index(s)
                line = text.count("\n", 0, idx) + 1
                text = text[:idx] + r + text[idx + len(s):]
                report.append(f"edit {k}: replaced at line {line}")
                continue
            # Fallback: compare line by line ignoring trailing whitespace.
            tl = text.split("\n")
            sl = s.strip("\n").split("\n")
            key = [x.rstrip() for x in sl]
            hits = [i for i in range(len(tl) - len(sl) + 1)
                    if all(tl[i + j].rstrip() == key[j] for j in range(len(sl)))]
            if len(hits) == 1:
                i = hits[0]
                new_lines = r.strip("\n").split("\n") if r.strip("\n") != "" else []
                tl[i:i + len(sl)] = new_lines
                text = "\n".join(tl)
                report.append(f"edit {k}: replaced at line {i + 1} (ignoring trailing whitespace)")
                continue
            if len(hits) > 1:
                raise ToolError(f"edit {k}: SEARCH matches {len(hits)} places. "
                                f"Add context. Nothing was changed.")
            first = next((x.strip() for x in sl if x.strip()), "")
            hint = ""
            if first:
                near = [i + 1 for i, x in enumerate(tl) if first in x]
                if near:
                    hint = (f" The first line of SEARCH does appear at line {near[0]}, but the "
                            f"following lines differ - re-read the file and copy it exactly.")
            raise ToolError(f"edit {k}: SEARCH text not found.{hint} Nothing was changed.")
        self.backup(p)
        out = text.replace("\n", "\r\n") if crlf else text
        p.write_bytes(out.encode("utf-8"))
        return f"{self.rel(p)}: applied {len(edits)} edit(s)\n" + "\n".join(report)

    def delete_file(self, a):
        p = self.resolve(a.get("path"), must_exist=True)
        if p.is_dir():
            raise ToolError("refusing to delete a directory")
        self.backup(p)
        p.unlink()
        return f"Deleted {self.rel(p)}"

    def search(self, a):
        pattern = a.get("pattern")
        if not pattern:
            raise ToolError("search needs a pattern")
        try:
            rx = re.compile(pattern, re.IGNORECASE if _truthy(a.get("ignore_case")) else 0)
        except re.error as e:
            raise ToolError(f"invalid regex: {e}")
        base = self.resolve(a.get("path", "."), must_exist=True)
        glob = a.get("glob")
        hits, files_scanned = [], 0
        paths = [base] if base.is_file() else None

        def iter_files():
            if paths:
                yield from paths
                return
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
                for fn in sorted(filenames):
                    if glob and not fnmatch.fnmatch(fn, glob):
                        continue
                    yield Path(dirpath) / fn

        for fp in iter_files():
            try:
                if fp.stat().st_size > 1_000_000:
                    continue
                data = fp.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:2048]:
                continue
            files_scanned += 1
            for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{self.rel(fp)}:{n}: {line.strip()[:200]}")
                    if len(hits) >= MAX_SEARCH_HITS:
                        return "\n".join(hits) + f"\n... stopped at {MAX_SEARCH_HITS} matches"
        if not hits:
            return f"No matches ({files_scanned} files scanned)"
        return "\n".join(hits)

    def run_command(self, a):
        if not self.allow_commands:
            raise ToolError("commands are disabled (bridge was started with --no-commands)")
        cmd = (a.get("command") or "").strip()
        if not cmd:
            raise ToolError("run_command needs a command")
        cwd = self.resolve(a.get("cwd", "."), must_exist=True)
        if not cwd.is_dir():
            raise ToolError("cwd is not a directory")
        timeout = _int(a.get("timeout"), 120, 1, 900)
        env = dict(os.environ, NO_COLOR="1", CI="1", PYTHONUNBUFFERED="1")
        kwargs = {"start_new_session": True} if os.name == "posix" else {}
        started = time.time()
        proc = subprocess.Popen(
            cmd, shell=True, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", **kwargs)
        timed_out = False
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._kill_tree(proc)
            out, _ = proc.communicate()
        out = ANSI_RE.sub("", out or "").replace("\r\n", "\n")
        if len(out) > MAX_OUTPUT_CHARS:
            head = out[:4000]
            tail = out[-(MAX_OUTPUT_CHARS - 4000):]
            out = f"{head}\n... [{len(out) - MAX_OUTPUT_CHARS} characters omitted] ...\n{tail}"
        took = time.time() - started
        status = (f"TIMED OUT after {timeout}s (process killed)" if timed_out
                  else f"exit code {proc.returncode}")
        return f"$ {cmd}\n{status} in {took:.1f}s\n--- output ---\n{out.rstrip() or '(no output)'}"

    @staticmethod
    def _kill_tree(proc):
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                               capture_output=True)
        except Exception:
            proc.kill()

    @staticmethod
    def sniff_image(data):
        """Return the file extension for real png/jpg/gif/webp content, else None."""
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "png"
        if data[:3] == b"\xff\xd8\xff":
            return "jpg"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return "gif"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "webp"
        return None

    def save_image(self, data, name="", prompt="", meta=None):
        """Store a picture in <project>/generated-images/. The bridge chooses the file name."""
        if not data:
            raise ToolError("no image data")
        if len(data) > MAX_SAVE_BYTES:
            raise ToolError(f"image is {len(data) // 1048576} MB; the limit is {MAX_SAVE_BYTES // 1048576} MB")
        ext = self.sniff_image(data)
        if not ext:
            raise ToolError("the data is not a png, jpg, gif or webp image")
        folder = self.root / "generated-images"
        if folder.is_symlink() or folder.exists():
            real = Path(os.path.realpath(folder))
            if real != self.root and self.root not in real.parents:
                raise ToolError("the project's generated-images folder points outside the project")
            if not real.is_dir():
                raise ToolError("generated-images exists but is not a folder")
        stem = re.sub(r"[^a-z0-9]+", "-", str(name or prompt or "image").lower()).strip("-")[:40].strip("-") or "image"
        tag = __import__("hashlib").sha1(data).hexdigest()[:6]
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / f"{stem}-{tag}.{ext}"
        tmp = folder / f".tmp-{secrets.token_hex(4)}"
        try:
            tmp.write_bytes(data)
            os.replace(tmp, out)
        finally:
            if tmp.exists():
                tmp.unlink()
        entry = {"file": self.rel(out), "bytes": len(data), "prompt": str(prompt)[:300],
                 "created": datetime.now().isoformat(timespec="seconds")}
        for k, v in (meta or {}).items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                entry[str(k)[:30]] = v if not isinstance(v, str) else v[:200]
        try:
            with (folder / "index.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass
        return self.rel(out), len(data), ext

    TOOLS = BUILTIN_TOOLS

    def call(self, name, args):
        if name in self.TOOLS:
            return getattr(self, name)(args or {})
        if self.skills is not None and self.skills.find_tool(name):
            return self.skills.run(name, args or {})
        raise ToolError(f"unknown tool {name!r}. Built-in tools: {', '.join(self.TOOLS)}"
                        + (" (a skill tool may be disabled or not installed)" if self.skills else ""))


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "DSAgentBridge/0.1"

    def log_message(self, *args):  # silence default logging
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self):
        srv = self.server
        if self.headers.get("Host", "") not in srv.allowed_hosts:
            self._send(403, {"ok": False, "error": "bad Host header"})
            return False
        origin = self.headers.get("Origin")
        if origin and not origin.startswith(("chrome-extension://", "moz-extension://")):
            self._send(403, {"ok": False, "error": "requests from web pages are refused"})
            return False
        token = self.headers.get("X-Agent-Token", "")
        if not hmac.compare_digest(token.encode(), srv.token.encode()):
            self._send(401, {"ok": False, "error": "wrong or missing token"})
            return False
        srv.last_request = time.time()
        return True

    def do_OPTIONS(self):  # no CORS: web pages must not be able to call us
        self._send(403, {"ok": False, "error": "CORS is not allowed"})

    # ---- routing -----------------------------------------------------------
    def do_GET(self):
        if not self._authorised():
            return
        srv = self.server
        path = self.path.split("?", 1)[0]
        if path == "/ping":
            ws = srv.ws
            return self._send(200, {"ok": True, "root": str(ws.root), "commands": ws.allow_commands,
                                    "platform": sys.platform, "tools": list(ws.TOOLS), "pid": os.getpid(),
                                    "version": BRIDGE_VERSION, "port": srv.server_address[1],
                                    "started": srv.started})
        if path == "/file":
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            return self._send_file((q.get("path") or [""])[0])
        if path == "/skills":
            return self._send(200, {"ok": True, "skills": srv.skills.list()})
        if path == "/skills/active":
            return self._send(200, {"ok": True, "skills": srv.skills.active()})
        self._send(404, {"ok": False, "error": "not found"})

    def _send_file(self, rel):
        """Serve one image from inside the project (used for previews in the chat)."""
        ws = self.server.ws
        try:
            p = ws.resolve(rel, must_exist=True)
        except ToolError as e:
            return self._send(404, {"ok": False, "error": str(e)})
        mime = IMAGE_TYPES.get(p.suffix.lower())
        if not p.is_file() or not mime:
            return self._send(415, {"ok": False, "error": "only png, jpg, gif, webp and svg images can be previewed"})
        size = p.stat().st_size
        if size > MAX_IMAGE_BYTES:
            return self._send(413, {"ok": False, "error": f"image is {size // 1048576} MB; preview limit is 12 MB"})
        data = p.read_bytes()
        head = data[:2000].lstrip(b"\xef\xbb\xbf \t\r\n")
        sig = (data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff" or data[:4] == b"GIF8"
               or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
               or (mime == "image/svg+xml" and (head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head))))
        if not sig:
            return self._send(415, {"ok": False, "error": "file content is not a recognised image"})
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # an SVG must only ever be shown as an <img>; this stops it running anything if opened directly
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; sandbox")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY_BYTES:
            self._send(413, {"ok": False, "error": "request too large"})
            return None
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (ValueError, TypeError):
            self._send(400, {"ok": False, "error": "invalid JSON"})
            return None

    def do_POST(self):
        if not self._authorised():
            return
        path = self.path.split("?", 1)[0]
        if path == "/shutdown":      # lets the helper (or you) stop a bridge that has no terminal window
            self._send(200, {"ok": True})
            print("  shutdown requested", flush=True)
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if path not in ("/call", "/image", "/skills/stage", "/skills/commit", "/skills/set", "/skills/remove"):
            return self._send(404, {"ok": False, "error": "not found"})
        payload = self._read_json()
        if payload is None:
            return
        try:
            if path == "/call":
                return self._call(payload)
            if path == "/image":
                return self._save_image(payload)
            sk = self.server.skills
            if path == "/skills/stage":
                result = sk.stage(payload)
                print(f"  [skills] staged {result['manifest']['id']} v{result['manifest']['version']}", flush=True)
                return self._send(200, {"ok": True, **result})
            if path == "/skills/commit":
                result = sk.commit(payload.get("stage_id", ""), enable=payload.get("enable", True))
                print(f"  [skills] installed {result['id']} v{result['version']}", flush=True)
                return self._send(200, {"ok": True, **result})
            if path == "/skills/set":
                cur = sk.set_state(payload.get("id", ""), payload.get("enabled"), payload.get("trusted"))
                print(f"  [skills] {payload.get('id')}: {cur}", flush=True)
                return self._send(200, {"ok": True, **cur})
            if path == "/skills/remove":
                sk.remove(payload.get("id", ""))
                print(f"  [skills] removed {payload.get('id')}", flush=True)
                return self._send(200, {"ok": True})
        except SkillError as e:
            return self._send(200, {"ok": False, "error": str(e)})
        except Exception as e:
            print(f"    !! {type(e).__name__}: {e}", flush=True)
            return self._send(200, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def _save_image(self, payload):
        try:
            data = base64.b64decode(payload.get("data", ""), validate=True)
        except Exception:
            return self._send(200, {"ok": False, "error": "image data is not valid base64"})
        meta = {k: payload.get(k) for k in ("seed", "source", "size", "negative")}
        try:
            rel, size, ext = self.server.ws.save_image(data, payload.get("name", ""), payload.get("prompt", ""), meta)
        except ToolError as e:
            print(f"  save_image       ! {e}", flush=True)
            return self._send(200, {"ok": False, "error": str(e)})
        print(f"  save_image       {rel} ({size // 1024} KB)", flush=True)
        self._send(200, {"ok": True, "path": rel, "bytes": size, "ext": ext})

    def _call(self, payload):
        name, args = payload.get("name"), payload.get("args") or {}
        if not isinstance(args, dict):
            return self._send(400, {"ok": False, "error": "args must be an object"})
        label = (args.get("path") or args.get("command") or args.get("pattern")
                 or next((str(v) for k, v in args.items() if k != "body"), ""))
        print(f"  {str(name):<16} {str(label)[:90]}", flush=True)
        try:
            self._send(200, {"ok": True, "output": self.server.ws.call(name, args)})
        except ToolError as e:
            print(f"    ! {str(e).splitlines()[0] if str(e) else e}", flush=True)
            self._send(200, {"ok": False, "error": str(e)})
        except Exception as e:  # never crash the server on a tool bug
            print(f"    !! {type(e).__name__}: {e}", flush=True)
            self._send(200, {"ok": False, "error": f"{type(e).__name__}: {e}"})


def load_token(rotate=False):
    path = Path.home() / ".dsagent" / "token"
    if path.exists() and not rotate:
        tok = path.read_text().strip()
        if tok:
            return tok, path
    path.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(24)
    path.write_text(tok)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return tok, path


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="DeepSeek Agent local bridge")
    ap.add_argument("-r", "--root", required=True, help="the ONE project folder the agent may touch")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-commands", action="store_true", help="disable run_command entirely")
    ap.add_argument("--no-backups", action="store_true", help="do not copy files before changing them")
    ap.add_argument("--rotate-token", action="store_true", help="generate a new token")
    ap.add_argument("--managed", action="store_true", help=argparse.SUPPRESS)   # started by the browser helper
    ap.add_argument("--idle-timeout", type=float, default=0, metavar="MINUTES",
                    help="stop by itself after this many minutes without requests (0 = never)")
    args = ap.parse_args()

    token, token_path = load_token(args.rotate_token)
    ws = Workspace(args.root, allow_commands=not args.no_commands, backups=not args.no_backups)
    skills = SkillStore(token_path.parent, ws.root, RESERVED_TOOLS)
    ws.skills = skills
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as e:
        print(f"Cannot listen on port {args.port}: {e}. Another bridge may already be running; "
              f"stop it or use --port.", file=sys.stderr, flush=True)
        sys.exit(2)
    srv.ws, srv.token, srv.skills = ws, token, skills
    srv.last_request = time.time()
    srv.started = int(time.time())
    srv.allowed_hosts = {f"127.0.0.1:{args.port}", f"localhost:{args.port}"}

    print("=" * 62)
    print(" DeepSeek Agent bridge")
    print(f" Project root : {ws.root}")
    print(f" Listening on : http://127.0.0.1:{args.port}  (this machine only)")
    print(f" Commands     : {'enabled' if ws.allow_commands else 'DISABLED'}")
    print(f" Backups      : {'.dsagent_backups/ in the project' if ws.backups else 'off'}")
    n_on = len(skills.active())
    print(f" Skills       : {n_on} enabled ({skills.dir})")
    print(f" Token        : {token}")
    print(f"   (paste it into the extension popup; stored in {token_path})")
    print("=" * 62)
    print("Tool calls:", flush=True)

    # A small file tells other programs (the browser helper) which bridge is running and where.
    info_file = token_path.parent / "bridge.json"
    try:
        info_file.write_text(json.dumps({"pid": os.getpid(), "port": args.port, "root": str(ws.root),
                                         "started": srv.started, "version": BRIDGE_VERSION,
                                         "managed": bool(args.managed)}), encoding="utf-8")
    except OSError:
        pass

    if args.idle_timeout > 0:
        limit = args.idle_timeout * 60

        def watchdog():
            while True:
                time.sleep(min(30, max(0.5, limit / 4)))
                if time.time() - srv.last_request > limit:
                    print(f"  no requests for {args.idle_timeout:g} minutes: stopping", flush=True)
                    srv.shutdown()
                    return
        threading.Thread(target=watchdog, daemon=True).start()

    try:
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=srv.shutdown, daemon=True).start())
    except (ValueError, OSError):
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nBridge stopped.")
    finally:
        try:
            if json.loads(info_file.read_text(encoding="utf-8")).get("pid") == os.getpid():
                info_file.unlink()
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    main()
