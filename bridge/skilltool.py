"""
skilltool - helper for people who write skills for the DeepSeek Agent.

    python skilltool.py validate  <skill folder or .zip>
    python skilltool.py run       <skill folder> <tool> [key=value ...] [--body TEXT | --body-file FILE] [--project DIR]
    python skilltool.py pack      <skill folder> [-o OUTPUT.zip]

`run` executes a tool exactly the way the bridge would, so what works here works in the
browser. Parameters are written the way the AI writes them: plain `key=value` strings.
"""
import argparse
import io
import sys
import zipfile
from pathlib import Path

from skills import (RESERVED_TOOLS, SkillError, ToolError, files_from_dir, files_from_zip,
                    package_digest, parse_manifest, run_tool)


def load(path):
    p = Path(path)
    if p.is_dir():
        return files_from_dir(p), p.resolve()
    if p.is_file() and p.suffix.lower() in (".zip", ".dsskill"):
        return files_from_zip(p.read_bytes()), None
    raise SkillError(f"not a folder or .zip file: {path}")


def cmd_validate(a):
    files, _ = load(a.path)
    manifest, warnings = parse_manifest(files, RESERVED_TOOLS)
    print(f"OK  {manifest['name']} ({manifest['id']}) v{manifest['version']}")
    print(f"    {manifest['description']}")
    print(f"    files: {len(files)}   sha256: {package_digest(files)[:16]}...")
    n = len(manifest["instructions_text"])
    print(f"    instructions: {n} characters" + (f" from {manifest['instructions_source']}" if n else " (none)"))
    if manifest["tools"]:
        print("    tools:")
        for t in manifest["tools"]:
            params = ", ".join(f"{k}{'*' if v['required'] else ''}:{v['type']}" for k, v in t["params"].items())
            body = " +body" if t["body"] else ""
            print(f"      - {t['name']}({params}){body}  [risk={t['risk']}, input={t['input']}]")
            print(f"          runs: {' '.join(t['exec'])}")
    for w in warnings:
        print(f"WARNING: {w}")
    return 0


def cmd_run(a):
    folder = Path(a.path)
    if not folder.is_dir():
        raise SkillError("`run` needs the skill folder, not a zip")
    files, skill_dir = load(folder)
    manifest, warnings = parse_manifest(files, RESERVED_TOOLS)
    tool = next((t for t in manifest["tools"] if t["name"] == a.tool), None)
    if not tool:
        raise SkillError(f"no tool '{a.tool}'. Available: {', '.join(t['name'] for t in manifest['tools']) or '(none)'}")
    args = {}
    for item in a.params:
        if "=" not in item:
            raise SkillError(f"expected key=value, got {item!r}")
        k, v = item.split("=", 1)
        args[k] = v
    body = Path(a.body_file).read_text(encoding="utf-8") if a.body_file else a.body
    if body is not None:
        body = body.replace("\r\n", "\n")
        args["body"] = body if (body.endswith("\n") or not body) else body + "\n"   # same as the extension
    project = Path(a.project).resolve()
    project.mkdir(parents=True, exist_ok=True)
    print(f"[running {tool['name']} in project {project}]", file=sys.stderr)
    try:
        print(run_tool(skill_dir, tool, args, project))
        return 0
    except ToolError as e:
        print("TOOL ERROR (this is what the AI would see):\n" + str(e))
        return 2


def cmd_pack(a):
    files, _ = load(a.path)
    manifest, warnings = parse_manifest(files, RESERVED_TOOLS)
    out = Path(a.output or f"{manifest['id']}-{manifest['version']}.dsskill.zip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(files):
            data, mode = files[path]
            info = zipfile.ZipInfo(path, date_time=(2020, 1, 1, 0, 0, 0))   # fixed date: reproducible zips
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = ((mode or 0o644) & 0o777) << 16
            zf.writestr(info, data)
    out.write_bytes(buf.getvalue())
    print(f"Packed {len(files)} files -> {out} ({out.stat().st_size} bytes)")
    for w in warnings:
        print(f"WARNING: {w}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate"); v.add_argument("path"); v.set_defaults(fn=cmd_validate)
    r = sub.add_parser("run"); r.add_argument("path"); r.add_argument("tool"); r.add_argument("params", nargs="*")
    r.add_argument("--body"); r.add_argument("--body-file"); r.add_argument("--project", default="./skilltool-project")
    r.set_defaults(fn=cmd_run)
    k = sub.add_parser("pack"); k.add_argument("path"); k.add_argument("-o", "--output"); k.set_defaults(fn=cmd_pack)
    a = ap.parse_args()
    try:
        sys.exit(a.fn(a))
    except BrokenPipeError:      # e.g. output piped into `head`
        sys.exit(0)
    except SkillError as e:
        print(f"INVALID: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
