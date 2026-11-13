"""
Skill support for the DeepSeek Agent bridge.

A skill is a package (folder or .zip) containing a `skill.json` manifest, optional
instructions for the AI, and optional tools. A tool is an ordinary program that the
bridge runs with the arguments the AI chose (no shell involved). See
docs/SKILL_DEVELOPER_GUIDE.md.

Standard library only.
"""
import base64
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

BUILTIN_TOOLS = ("list_dir", "read_file", "write_file", "replace_in_file",
                 "delete_file", "search", "run_command")
# names a skill may not use: the built-ins, plus tools the browser extension handles itself
RESERVED_TOOLS = BUILTIN_TOOLS + ("generate_image",)

SCHEMA = 1
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
PARAM_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")
PLACEHOLDER_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
TYPES = ("string", "integer", "number", "boolean")
RISKS = ("read", "write", "exec")
INPUT_MODES = ("json", "args")
SHELLS = {"sh", "bash", "zsh", "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe"}
RESERVED_PLACEHOLDERS = ("skill_dir", "project_dir", "python")
KNOWN_TOP = {"$schema", "schema", "id", "name", "version", "description", "author", "homepage",
             "license", "instructions", "instructions_text", "tools"}

MAX_FILES = 500
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_INSTRUCTIONS = 30_000
WARN_INSTRUCTIONS = 6_000
MAX_TOOLS = 20
MAX_OUTPUT_CHARS = 20_000
JUNK = ("__MACOSX/", ".DS_Store", "__pycache__/", ".git/")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class ToolError(Exception):
    """A problem the model should be told about (bad argument, tool failed, ...)."""


class SkillError(Exception):
    """The skill package is invalid or could not be installed."""


# ---------------------------------------------------------------------------
# Reading packages
# ---------------------------------------------------------------------------
def _clean_path(p):
    p = str(p).replace("\\", "/")
    if not p or p.startswith("/") or re.match(r"^[A-Za-z]:", p) or "\0" in p:
        raise SkillError(f"unsafe path in package: {p!r}")
    parts = [x for x in p.split("/") if x not in ("", ".")]
    if not parts or any(x == ".." for x in parts):
        raise SkillError(f"unsafe path in package: {p!r}")
    return "/".join(parts)


def _is_junk(path):
    return any(path.startswith(j) or ("/" + j) in ("/" + path) for j in JUNK) or path.endswith(".pyc")


def _strip_top_folder(files):
    """Allow a zip whose contents sit inside one folder (the usual 'zip the folder' result)."""
    if "skill.json" in files:
        return files
    tops = {p.split("/", 1)[0] for p in files if "/" in p}
    if len(tops) == 1:
        top = next(iter(tops))
        if f"{top}/skill.json" in files and all(p.startswith(top + "/") for p in files):
            return {p[len(top) + 1:]: v for p, v in files.items()}
    return files


def files_from_zip(data):
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SkillError("that file is not a valid .zip")
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_FILES:
        raise SkillError(f"too many files ({len(infos)}; limit {MAX_FILES})")
    if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
        raise SkillError("package is too large when unpacked (limit 20 MB)")
    files = {}
    for info in infos:
        mode_bits = (info.external_attr >> 16) & 0o170000
        if mode_bits == stat.S_IFLNK:
            raise SkillError(f"symbolic links are not allowed: {info.filename}")
        path = _clean_path(info.filename)
        if _is_junk(path):
            continue
        mode = ((info.external_attr >> 16) & 0o777) or None
        files[path] = (zf.read(info), mode)
    return _strip_top_folder(files)


def files_from_list(items):
    if not isinstance(items, list) or not items:
        raise SkillError("no files were provided")
    if len(items) > MAX_FILES:
        raise SkillError(f"too many files ({len(items)}; limit {MAX_FILES})")
    files, total = {}, 0
    for it in items:
        path = _clean_path(it.get("path", ""))
        if _is_junk(path):
            continue
        try:
            data = base64.b64decode(it.get("data", ""), validate=True)
        except Exception:
            raise SkillError(f"file {path} is not valid base64")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise SkillError("package is too large (limit 20 MB)")
        files[path] = (data, None)
    return _strip_top_folder(files)


def files_from_dir(folder):
    root = Path(folder)
    files = {}
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            raise SkillError(f"symbolic links are not allowed: {p}")
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if _is_junk(rel):
            continue
        mode = stat.S_IMODE(p.stat().st_mode) if os.name == "posix" else None
        files[rel] = (p.read_bytes(), mode)
        if len(files) > MAX_FILES:
            raise SkillError(f"too many files (limit {MAX_FILES})")
    return files


# ---------------------------------------------------------------------------
# Validating manifests
# ---------------------------------------------------------------------------
def _need(cond, msg):
    if not cond:
        raise SkillError(msg)


def _str(d, key, label, required=True, max_len=300):
    v = d.get(key)
    if v is None:
        _need(not required, f"{label}: missing required field '{key}'")
        return ""
    _need(isinstance(v, str), f"{label}: '{key}' must be a string")
    _need(len(v) <= max_len, f"{label}: '{key}' is too long (limit {max_len} characters)")
    _need(not required or v.strip(), f"{label}: '{key}' must not be empty")
    return v.strip()


def _parse_param(tool_label, name, spec):
    label = f"{tool_label} parameter '{name}'"
    _need(PARAM_RE.match(name), f"{label}: invalid name (use lowercase letters, digits, underscore)")
    _need(name not in ("name", "body"), f"{label}: 'name' and 'body' are reserved")
    _need(isinstance(spec, dict), f"{label}: must be an object")
    typ = spec.get("type", "string")
    _need(typ in TYPES, f"{label}: type must be one of {', '.join(TYPES)}")
    out = {"type": typ, "required": bool(spec.get("required", False)),
           "description": _str(spec, "description", label, required=False, max_len=300)}
    if "enum" in spec:
        _need(isinstance(spec["enum"], list) and spec["enum"], f"{label}: 'enum' must be a non-empty list")
        out["enum"] = [str(x) for x in spec["enum"]]
    if "default" in spec:
        out["default"] = spec["default"]
        _need(not out["required"], f"{label}: a required parameter cannot have a default")
        try:
            coerce_value(out, name, out["default"])
        except ToolError as e:
            raise SkillError(f"{label}: default is invalid ({e})")
    return out


def parse_manifest(files, builtin=RESERVED_TOOLS):
    """Validate a package. Returns (normalised_manifest, warnings). Raises SkillError."""
    _need("skill.json" in files, "skill.json not found at the top level of the package")
    try:
        raw = json.loads(files["skill.json"][0].decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as e:
        raise SkillError(f"skill.json is not valid JSON: {e}")
    _need(isinstance(raw, dict), "skill.json must contain a JSON object")
    warnings = []

    _need(raw.get("schema") == SCHEMA, f"skill.json: 'schema' must be {SCHEMA}")
    m = {"schema": SCHEMA}
    m["id"] = _str(raw, "id", "skill", max_len=41)
    _need(ID_RE.match(m["id"]), "skill: 'id' must be 2-41 characters: lowercase letters, digits, '-'")
    m["name"] = _str(raw, "name", "skill", max_len=80)
    m["version"] = _str(raw, "version", "skill", max_len=40)
    _need(VERSION_RE.match(m["version"]), "skill: 'version' must look like 1.2.3")
    m["description"] = _str(raw, "description", "skill", max_len=300)
    for opt in ("author", "homepage", "license"):
        m[opt] = _str(raw, opt, "skill", required=False, max_len=200)
    for k in raw:
        if k not in KNOWN_TOP:
            warnings.append(f"unknown field '{k}' in skill.json (ignored)")

    # instructions ----------------------------------------------------------
    text, src = "", ""
    if "instructions" in raw:
        _need("instructions_text" not in raw, "use either 'instructions' (a file) or 'instructions_text', not both")
        rel = _clean_path(raw["instructions"])
        _need(rel in files, f"instructions file not found in package: {rel}")
        try:
            text = files[rel][0].decode("utf-8-sig")
        except UnicodeDecodeError:
            raise SkillError(f"instructions file {rel} is not valid UTF-8")
        src = rel
    elif "instructions_text" in raw:
        _need(isinstance(raw["instructions_text"], str), "'instructions_text' must be a string")
        text, src = raw["instructions_text"], "(inline)"
    text = text.strip()
    _need(len(text) <= MAX_INSTRUCTIONS, f"instructions are too long ({len(text)} characters; limit {MAX_INSTRUCTIONS})")
    if len(text) > WARN_INSTRUCTIONS:
        warnings.append(f"instructions are long ({len(text)} characters); they are sent to the AI at every session start, so shorter is better")
    m["instructions_text"], m["instructions_source"] = text, src

    # tools -----------------------------------------------------------------
    raw_tools = raw.get("tools", [])
    _need(isinstance(raw_tools, list), "'tools' must be a list")
    _need(len(raw_tools) <= MAX_TOOLS, f"too many tools (limit {MAX_TOOLS})")
    _need(text or raw_tools, "a skill needs instructions, tools, or both")
    seen, tools = set(), []
    for t in raw_tools:
        _need(isinstance(t, dict), "each entry in 'tools' must be an object")
        name = _str(t, "name", "tool", max_len=41)
        label = f"tool '{name}'"
        _need(NAME_RE.match(name), f"{label}: invalid name (lowercase letters, digits, underscore; starts with a letter)")
        _need(name not in builtin, f"{label}: collides with a built-in tool")
        _need(name not in seen, f"{label}: defined twice")
        seen.add(name)
        tool = {"name": name, "description": _str(t, "description", label, max_len=300)}
        params = t.get("params", {})
        _need(isinstance(params, dict), f"{label}: 'params' must be an object")
        _need(len(params) <= 20, f"{label}: too many parameters (limit 20)")
        tool["params"] = {k: _parse_param(label, k, v) for k, v in params.items()}
        body = t.get("body")
        if body is not None:
            _need(isinstance(body, dict), f"{label}: 'body' must be an object")
            tool["body"] = {"description": _str(body, "description", f"{label} body", required=False),
                            "required": bool(body.get("required", False))}
        else:
            tool["body"] = None
        ex = t.get("exec")
        _need(isinstance(ex, list) and ex and all(isinstance(x, str) and x for x in ex),
              f"{label}: 'exec' must be a non-empty list of strings (program, then arguments)")
        known_ph = set(RESERVED_PLACEHOLDERS) | set(tool["params"])
        for el in ex:
            for ph in PLACEHOLDER_RE.findall(el):
                _need(ph in known_ph, f"{label}: unknown placeholder {{{ph}}} in exec")
        tool["exec"] = ex
        tool["input"] = t.get("input", "json")
        _need(tool["input"] in INPUT_MODES, f"{label}: 'input' must be 'json' or 'args'")
        tool["timeout"] = t.get("timeout", 60)
        _need(isinstance(tool["timeout"], int) and 1 <= tool["timeout"] <= 900, f"{label}: 'timeout' must be 1-900 seconds")
        tool["cwd"] = t.get("cwd", "project")
        _need(tool["cwd"] in ("project", "skill"), f"{label}: 'cwd' must be 'project' or 'skill'")
        tool["risk"] = t.get("risk", "exec")
        _need(tool["risk"] in RISKS, f"{label}: 'risk' must be one of {', '.join(RISKS)}")

        # files referenced through {skill_dir} must exist in the package
        for el in ex:
            if el.startswith("{skill_dir}/"):
                rel = el[len("{skill_dir}/"):]
                _need(rel and ".." not in rel.split("/") and rel in files,
                      f"{label}: exec refers to '{rel}', which is not in the package")
        prog = os.path.basename(ex[0].replace("\\", "/")).lower()
        if prog in SHELLS:
            warnings.append(f"{label}: runs through a shell ({ex[0]}); make sure no parameter can be abused as shell syntax")
        if tool["risk"] == "read" and prog in SHELLS:
            warnings.append(f"{label}: declared 'read' but runs a shell, which can do anything")
        tools.append(tool)
    m["tools"] = tools
    return m, warnings


def package_digest(files):
    h = hashlib.sha256()
    for path in sorted(files):
        h.update(path.encode() + b"\0" + hashlib.sha256(files[path][0]).digest())
    return h.hexdigest()


def summarise_manifest(m):
    return {
        "id": m["id"], "name": m["name"], "version": m["version"], "description": m["description"],
        "author": m.get("author", ""), "homepage": m.get("homepage", ""), "license": m.get("license", ""),
        "instructions_chars": len(m["instructions_text"]),
        "instructions": m["instructions_text"],
        "tools": [{"name": t["name"], "description": t["description"], "risk": t["risk"],
                   "params": t["params"], "body": t["body"], "exec": t["exec"],
                   "input": t["input"], "timeout": t["timeout"], "cwd": t["cwd"]} for t in m["tools"]],
    }


# ---------------------------------------------------------------------------
# Running tools
# ---------------------------------------------------------------------------
def coerce_value(spec, name, raw):
    typ = spec.get("type", "string")
    if typ == "string":
        v = str(raw)
    elif typ == "integer":
        try:
            v = int(str(raw).strip())
        except ValueError:
            raise ToolError(f"parameter '{name}' must be an integer, got {str(raw)[:40]!r}")
    elif typ == "number":
        try:
            v = float(str(raw).strip())
        except ValueError:
            raise ToolError(f"parameter '{name}' must be a number, got {str(raw)[:40]!r}")
    else:
        s = str(raw).strip().lower()
        if s in ("true", "yes", "1", "y"):
            v = True
        elif s in ("false", "no", "0", "n"):
            v = False
        else:
            raise ToolError(f"parameter '{name}' must be true or false, got {str(raw)[:40]!r}")
    if spec.get("enum") and str(v) not in spec["enum"]:
        raise ToolError(f"parameter '{name}' must be one of: {', '.join(spec['enum'])}")
    return v


def _kill_tree(proc):
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    except Exception:
        proc.kill()


def _trim(out):
    out = ANSI_RE.sub("", out or "").replace("\r\n", "\n")
    if len(out) > MAX_OUTPUT_CHARS:
        head, tail = out[:4000], out[-(MAX_OUTPUT_CHARS - 4000):]
        out = f"{head}\n... [{len(out) - MAX_OUTPUT_CHARS} characters omitted] ...\n{tail}"
    return out


def run_tool(skill_dir, tool, args, project_root):
    """Run one skill tool. `args` are the raw strings the AI wrote. Returns output text."""
    args = dict(args or {})
    body = args.pop("body", None)
    args.pop("name", None)

    unknown = sorted(set(args) - set(tool["params"]))
    if unknown:
        valid = ", ".join(tool["params"]) or "(none)"
        raise ToolError(f"unknown parameter(s): {', '.join(unknown)}. Valid parameters: {valid}")

    params = {}
    for pname, spec in tool["params"].items():
        if pname in args and str(args[pname]).strip() != "":
            params[pname] = coerce_value(spec, pname, args[pname])
        elif "default" in spec:
            params[pname] = spec["default"]
        elif spec["required"]:
            raise ToolError(f"missing required parameter '{pname}'")

    spec_body = tool.get("body")
    if spec_body is None and body is not None:
        raise ToolError("this tool does not take a =====BODY===== section")
    if spec_body is not None and spec_body["required"] and not (body or "").strip():
        raise ToolError("this tool needs a =====BODY===== section")

    subst = {"skill_dir": str(skill_dir), "project_dir": str(project_root), "python": sys.executable}
    argv = []
    for el in tool["exec"]:
        only = PLACEHOLDER_RE.fullmatch(el)
        if only and only.group(1) in tool["params"] and only.group(1) not in params:
            continue  # optional parameter left out: drop the whole argument
        def sub(m):
            k = m.group(1)
            if k in subst:
                return subst[k]
            return "" if k not in params else (
                ("true" if params[k] else "false") if isinstance(params[k], bool) else str(params[k]))
        argv.append(PLACEHOLDER_RE.sub(sub, el))

    prog = argv[0]
    if not (os.path.isabs(prog) or os.sep in prog or "/" in prog):
        found = shutil.which(prog)
        if not found:
            raise ToolError(f"program not found on this computer: {prog}")
        argv[0] = found

    env = dict(os.environ, DSAGENT_PROJECT_DIR=str(project_root), DSAGENT_SKILL_DIR=str(skill_dir),
               DSAGENT_TOOL=tool["name"], NO_COLOR="1", PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    cwd = str(skill_dir) if tool["cwd"] == "skill" else str(project_root)

    if tool["input"] == "json":
        stdin_bytes = json.dumps({"tool": tool["name"], "params": params, "body": body,
                                  "project_dir": str(project_root), "skill_dir": str(skill_dir)}).encode("utf-8")
    else:
        stdin_bytes = (body or "").encode("utf-8")

    kwargs = {"start_new_session": True} if os.name == "posix" else {}
    started = time.time()
    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, **kwargs)
    except OSError as e:
        raise ToolError(f"could not start {argv[0]}: {e}")
    timed_out = False
    try:
        out, err = proc.communicate(stdin_bytes, timeout=tool["timeout"])
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        out, err = proc.communicate()
    out = _trim(out.decode("utf-8", "replace"))
    err = _trim(err.decode("utf-8", "replace")).strip()
    took = time.time() - started

    if timed_out:
        raise ToolError(f"timed out after {tool['timeout']}s (process killed)\n{out}{chr(10) + err if err else ''}".strip())

    # A tool may answer with {"ok": true, "output": "..."} or {"ok": false, "error": "..."}.
    stripped = out.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            obj = json.loads(stripped)
        except ValueError:
            obj = None
        if isinstance(obj, dict) and "ok" in obj:
            if obj.get("ok"):
                return _trim(str(obj.get("output", "")))
            raise ToolError(_trim(str(obj.get("error", "tool reported failure"))))

    if proc.returncode != 0:
        msg = f"exit code {proc.returncode} after {took:.1f}s"
        if out.strip():
            msg += f"\n--- stdout ---\n{out.rstrip()}"
        if err:
            msg += f"\n--- stderr ---\n{err}"
        raise ToolError(msg)
    if err:
        out = f"{out.rstrip()}\n--- stderr ---\n{err}" if out.strip() else f"--- stderr ---\n{err}"
    return out.rstrip() or "(no output)"


# ---------------------------------------------------------------------------
# The store: installed skills on disk + enabled/trusted state
# ---------------------------------------------------------------------------
class SkillStore:
    STAGE_TTL = 15 * 60

    def __init__(self, home, project_root, builtin=RESERVED_TOOLS):
        self.home = Path(home)
        self.dir = self.home / "skills"
        self.state_file = self.home / "skills.json"
        self.project_root = Path(project_root)
        self.builtin = tuple(builtin)
        self.lock = threading.RLock()
        self.staged = {}
        self.dir.mkdir(parents=True, exist_ok=True)

    # -- state ---------------------------------------------------------------
    def _state(self):
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self, state):
        self.home.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(self.state_file)

    # -- reading installed skills --------------------------------------------
    def _load(self):
        """Return {id: skill} for everything installed."""
        state = self._state()
        skills = {}
        for d in sorted(self.dir.iterdir()) if self.dir.exists() else []:
            if not d.is_dir() or d.name.startswith("."):
                continue
            mf = d / ".manifest.json"
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
                info = json.loads((d / ".install.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                skills[d.name] = {"id": d.name, "broken": True, "dir": d, "name": d.name,
                                  "enabled": False, "trusted": False, "manifest": None}
                continue
            st = state.get(m["id"], {})
            skills[m["id"]] = {"id": m["id"], "dir": d, "manifest": m, "broken": False,
                               "enabled": bool(st.get("enabled", False)),
                               "trusted": bool(st.get("trusted", False)),
                               "installed_at": info.get("installed_at"), "sha256": info.get("sha256", "")}
        return skills

    def _index(self, skills):
        """name -> (skill, tool) for enabled skills; also the list of conflicting names."""
        idx, conflicts = {}, []
        for sid in sorted(skills):
            s = skills[sid]
            if s["broken"] or not s["enabled"]:
                continue
            for t in s["manifest"]["tools"]:
                if t["name"] in idx:
                    conflicts.append((t["name"], sid, idx[t["name"]][0]["id"]))
                else:
                    idx[t["name"]] = (s, t)
        return idx, conflicts

    def list(self):
        with self.lock:
            skills = self._load()
            _, conflicts = self._index(skills)
            out = []
            for sid in sorted(skills):
                s = skills[sid]
                if s["broken"]:
                    out.append({"id": sid, "name": sid, "broken": True, "enabled": False, "trusted": False})
                    continue
                item = summarise_manifest(s["manifest"])
                item.update({"enabled": s["enabled"], "trusted": s["trusted"], "broken": False,
                             "installed_at": s["installed_at"], "sha256": s["sha256"],
                             "conflicts": [c[0] for c in conflicts if c[1] == sid]})
                out.append(item)
            return out

    def active(self):
        """Enabled skills in the form the extension needs to build the AI's prompt."""
        with self.lock:
            skills = self._load()
            idx, _ = self._index(skills)
            out = []
            for sid in sorted(skills):
                s = skills[sid]
                if s["broken"] or not s["enabled"]:
                    continue
                m = s["manifest"]
                tools = [t for t in m["tools"] if idx.get(t["name"], (None,))[0] is s]
                out.append({"id": sid, "name": m["name"], "version": m["version"], "trusted": s["trusted"],
                            "instructions": m["instructions_text"],
                            "tools": [{"name": t["name"], "description": t["description"], "risk": t["risk"],
                                       "params": t["params"], "body": t["body"]} for t in tools]})
            return out

    def find_tool(self, name):
        with self.lock:
            idx, _ = self._index(self._load())
            return idx.get(name)

    def run(self, name, args):
        hit = self.find_tool(name)
        if not hit:
            raise ToolError(f"unknown tool {name!r}")
        skill, tool = hit
        return run_tool(skill["dir"], tool, args, self.project_root)

    # -- install flow ---------------------------------------------------------
    def stage(self, payload):
        with self.lock:
            now = time.time()
            self.staged = {k: v for k, v in self.staged.items() if now - v["at"] < self.STAGE_TTL}
            if len(self.staged) >= 5:
                oldest = min(self.staged, key=lambda k: self.staged[k]["at"])
                self.staged.pop(oldest)
            if payload.get("zip"):
                try:
                    data = base64.b64decode(payload["zip"], validate=True)
                except Exception:
                    raise SkillError("upload was not valid base64")
                files = files_from_zip(data)
            else:
                files = files_from_list(payload.get("files"))
            manifest, warnings = parse_manifest(files, self.builtin)
            digest = package_digest(files)

            existing = self._load().get(manifest["id"])
            replaces = None
            if existing and not existing["broken"]:
                replaces = {"version": existing["manifest"]["version"], "enabled": existing["enabled"],
                            "trusted": existing["trusted"], "same_content": existing["sha256"] == digest}
                if existing["trusted"]:
                    warnings.append("this replaces a skill you marked as trusted; trust will be reset because the code may have changed")

            # tool names that clash with OTHER enabled skills
            idx, _ = self._index(self._load())
            for t in manifest["tools"]:
                if t["name"] in idx and idx[t["name"]][0]["id"] != manifest["id"]:
                    warnings.append(f"tool '{t['name']}' is already provided by skill '{idx[t['name']][0]['id']}'; only one of them can be enabled at a time")

            # programs the package does not ship and this computer cannot find
            for t in manifest["tools"]:
                prog = t["exec"][0]
                if prog.startswith("{"):
                    continue
                if not (os.path.isabs(prog) or "/" in prog or os.sep in prog) and not shutil.which(prog):
                    warnings.append(f"tool '{t['name']}' needs '{prog}', which was not found on this computer")

            stage_id = secrets.token_urlsafe(12)
            self.staged[stage_id] = {"files": files, "manifest": manifest, "digest": digest, "at": now}
            return {"stage_id": stage_id, "manifest": summarise_manifest(manifest), "warnings": warnings,
                    "files": [{"path": p, "size": len(files[p][0])} for p in sorted(files)],
                    "sha256": digest, "replaces": replaces}

    def commit(self, stage_id, enable=True):
        with self.lock:
            st = self.staged.pop(stage_id, None)
            if not st:
                raise SkillError("this import expired; choose the file again")
            m, files = st["manifest"], st["files"]
            target = self.dir / m["id"]
            tmp = self.dir / f".tmp-{secrets.token_hex(4)}"
            try:
                for path, (data, mode) in files.items():
                    dest = tmp / path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                    if os.name == "posix":
                        perm = (mode or 0o644) & 0o755
                        dest.chmod(perm | 0o600)
                (tmp / ".manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
                (tmp / ".install.json").write_text(json.dumps(
                    {"installed_at": int(time.time()), "sha256": st["digest"]}), encoding="utf-8")
                if target.exists():
                    shutil.rmtree(target)
                tmp.rename(target)
            except Exception:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
            state = self._state()
            state[m["id"]] = {"enabled": bool(enable), "trusted": False}
            self._save_state(state)
            return {"id": m["id"], "name": m["name"], "version": m["version"], "enabled": bool(enable)}

    def set_state(self, sid, enabled=None, trusted=None):
        with self.lock:
            skills = self._load()
            if sid not in skills or skills[sid]["broken"]:
                raise SkillError(f"no such skill: {sid}")
            state = self._state()
            cur = state.get(sid, {"enabled": False, "trusted": False})
            if enabled is not None:
                cur["enabled"] = bool(enabled)
            if trusted is not None:
                cur["trusted"] = bool(trusted)
            state[sid] = cur
            self._save_state(state)
            return cur

    def remove(self, sid):
        with self.lock:
            if not ID_RE.match(sid or ""):
                raise SkillError("invalid skill id")
            target = self.dir / sid
            if not target.is_dir():
                raise SkillError(f"no such skill: {sid}")
            shutil.rmtree(target)
            state = self._state()
            state.pop(sid, None)
            self._save_state(state)
