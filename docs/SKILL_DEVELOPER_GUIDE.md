# Skill developer guide

A **skill** teaches the DeepSeek Agent something new. It can do two things, alone or together:

- give the AI **instructions** ("when you write Python here, follow these rules"), and
- give the AI **tools** (new things it can *do*: query a database, call an API, run your linter).

A skill is a small package (a folder or a `.zip`) that the user imports in the extension's
**Skills** page. This guide covers everything you need to build, test and ship one.

**Contents**
1. [Quick start (10 minutes)](#1-quick-start)
2. [How it works](#2-how-it-works)
3. [Package layout](#3-package-layout)
4. [`skill.json` reference](#4-skilljson-reference)
5. [Writing instructions for an AI](#5-writing-instructions-for-an-ai)
6. [The tool contract](#6-the-tool-contract)
7. [What the AI writes](#7-what-the-ai-writes)
8. [Risk levels, approval and trust](#8-risk-levels-approval-and-trust)
9. [Security rules for tool authors](#9-security-rules-for-tool-authors)
10. [Testing and debugging](#10-testing-and-debugging)
11. [Packaging and updating](#11-packaging-and-updating)
12. [Cross-platform notes](#12-cross-platform-notes)
13. [Cookbook](#13-cookbook)
14. [Limits](#14-limits)
15. [Troubleshooting](#15-troubleshooting)
16. [FAQ](#16-faq)

---

## 1. Quick start

We will build `word-count`: a tool that counts the words in a project file.

**Step 1. Make a folder.**

```
word-count/
  skill.json
  instructions.md
  tools/
    wc.py
```

**Step 2. `skill.json`**

```json
{
  "$schema": "../docs/skill.schema.json",
  "schema": 1,
  "id": "word-count",
  "name": "Word count",
  "version": "1.0.0",
  "description": "Counts the words in a text file of the project.",
  "instructions": "instructions.md",
  "tools": [
    {
      "name": "count_words",
      "description": "Count words and lines in one file.",
      "params": {
        "path": { "type": "string", "required": true, "description": "File path, relative to the project" }
      },
      "exec": ["{python}", "{skill_dir}/tools/wc.py"],
      "risk": "read",
      "timeout": 10
    }
  ]
}
```

**Step 3. `instructions.md`** (this text is sent to the AI)

```markdown
Use `count_words` when the user asks how long a document is. Do not read a whole
file just to count its words.
```

**Step 4. `tools/wc.py`**: your tool is an ordinary program. It receives a JSON request on
standard input and prints its answer on standard output.

```python
import json, sys
from pathlib import Path

req = json.load(sys.stdin)                       # {"tool": ..., "params": {...}, "body": ..., "project_dir": ..., ...}
project = Path(req["project_dir"]).resolve()
target = (project / req["params"]["path"]).resolve()

if project not in target.parents:                # ALWAYS keep paths inside the project
    print(json.dumps({"ok": False, "error": "path is outside the project"}))
    sys.exit(0)
if not target.is_file():
    print(json.dumps({"ok": False, "error": f"no such file: {req['params']['path']}"}))
    sys.exit(0)

text = target.read_text(encoding="utf-8", errors="replace")
print(f"{len(text.split())} words, {text.count(chr(10))} lines")
```

**Step 5. Check it, run it, pack it** with the bundled helper (it lives next to `bridge.py`):

```
cd bridge
python skilltool.py validate ../word-count
python skilltool.py run      ../word-count count_words path=README.md --project ..
python skilltool.py pack     ../word-count            # writes word-count-1.0.0.dsskill.zip
```

**Step 6. Import it.** Make sure the bridge is running, click the extension icon, press
**Manage skills...**, then drop the `.zip` (or the folder) on the page. Review it, press
**Install**. In DeepSeek, press **Start session** (or **Sync** if a session is already open).

---

## 2. How it works

```
 you ask DeepSeek for something
        |
        v
 DeepSeek writes a tool block:          ```tool
                                        name: count_words
                                        path: README.md
                                        ```
        |
        v
 browser extension  - parses the block
                    - asks you to approve it (depending on risk/trust, see section 8)
        |
        v
 bridge.py (local)  - validates the arguments against your manifest
                    - starts YOUR program (no shell), sends the request on stdin
        |
        v
 your program       - does the work, prints the result on stdout
        |
        v
 extension          - sends the result back to DeepSeek as the next message
```

Key facts:

- Skills are **installed on the user's computer**, in `~/.dsagent/skills/<id>/`
  (on Windows `C:\Users\<name>\.dsagent\skills\<id>\`). Nothing is uploaded anywhere.
- Your tool runs as a **new process for every call**. It cannot keep state in memory between
  calls. Store state in files if you need it.
- Your tool runs with **the user's permissions**. There is no sandbox. See section 9.
- The extension tells the AI about your skill by adding the **instructions** and an
  **automatic description of every tool** (name, parameters, risk, descriptions) to the first
  message of a session. You do not have to document the call syntax yourself.

---

## 3. Package layout

```
my-skill/                 <- the zip may contain these files at its top level,
  skill.json              <- or inside exactly ONE folder (zipping the folder works)
  instructions.md         <- optional, any name; referenced from skill.json
  tools/
    my_tool.py            <- any programs / scripts / data your tools need
  data/
    ...
```

Rules:

- `skill.json` must be at the top level of the package.
- Files are stored under `~/.dsagent/skills/<id>/`. Refer to them from `exec` with `{skill_dir}`.
- Not allowed: symbolic links, absolute paths, `..` in paths. These make the import fail.
- Ignored when importing: `.git/`, `__pycache__/`, `*.pyc`, `.DS_Store`, `__MACOSX/`.
- Limits: 500 files, 20 MB unpacked, 12 MB for the file you import.

---

## 4. `skill.json` reference

### Top level

| Field | Required | Type | Notes |
| --- | --- | --- | --- |
| `schema` | yes | `1` | Manifest format version. |
| `id` | yes | string | `^[a-z0-9][a-z0-9-]{1,40}$`. Unique. Becomes the folder name. Importing the same id replaces the installed skill. |
| `name` | yes | string | Up to 80 characters. Shown in the manager and given to the AI. |
| `version` | yes | string | Semantic version `MAJOR.MINOR.PATCH`, e.g. `1.4.0`. |
| `description` | yes | string | Up to 300 characters. Shown in the manager. |
| `author`, `homepage`, `license` | no | string | Shown on the review screen. |
| `instructions` | one of | string | Path to a UTF-8 text/markdown file **inside the package**. |
| `instructions_text` | one of | string | Instructions written inline (good for short ones). |
| `tools` | one of | array | Up to 20 tools. |
| `$schema` | no | string | Lets editors offer completion. Point it at `docs/skill.schema.json`. |

A skill needs **at least one** of `instructions` / `instructions_text` / `tools`.
Use only one of `instructions` and `instructions_text`. Unknown top-level fields produce a
warning and are ignored.

### A tool

| Field | Required | Default | Notes |
| --- | --- | --- | --- |
| `name` | yes | | `^[a-z][a-z0-9_]{1,40}$`. This is what the AI writes after `name:`. Must not equal a built-in tool (`list_dir`, `read_file`, `write_file`, `replace_in_file`, `delete_file`, `search`, `run_command`) or `generate_image`, which the extension handles itself. Must be unique among all *enabled* skills, so give it a distinctive prefix (`git_status`, `jira_search`). |
| `description` | yes | | Up to 300 characters. Tell the AI **what it does and when to use it**. |
| `params` | no | `{}` | Object: parameter name -> parameter definition. Up to 20. |
| `body` | no | none | Declare this if the tool takes multi-line text. See below. |
| `exec` | yes | | Array of strings: the program, then its arguments. **No shell is used.** |
| `input` | no | `"json"` | `"json"` or `"args"`. See section 6. |
| `timeout` | no | `60` | Seconds, 1 to 900. The process (and its children) is killed after this. |
| `cwd` | no | `"project"` | Working directory: `"project"` (the user's project folder) or `"skill"` (your skill's folder). |
| `risk` | no | `"exec"` | `"read"`, `"write"` or `"exec"`. Controls approval. Be honest. See section 8. |

### A parameter

```json
"count": { "type": "integer", "default": 10, "description": "How many commits to show" }
```

| Field | Default | Notes |
| --- | --- | --- |
| `type` | `"string"` | `string`, `integer`, `number` or `boolean`. The bridge converts the AI's text to this type and rejects values that do not fit. |
| `required` | `false` | If true and the AI leaves it out, the call fails with a clear message. Cannot be combined with `default`. |
| `default` | none | Used when the AI omits the parameter (or leaves it empty). |
| `enum` | none | Allowed values, e.g. `["fast", "thorough"]`. Anything else is rejected. |
| `description` | none | Up to 300 characters, shown to the AI next to the parameter. |

Parameter names: lowercase letters, digits, `_`, starting with a letter. `name` and `body` are reserved.

Booleans are accepted as `true/false/yes/no/1/0`. Parameter values are always **single-line**.
For longer text use a body.

### The body

```json
"body": { "description": "The note text (can span several lines)", "required": true }
```

A body is for multi-line text such as file contents, a commit message, a SQL query or a
prompt. The AI writes it between `=====BODY=====` and `=====END=====`, so it needs no quoting
or escaping. Your tool receives it as `body` (a string that ends with exactly one newline,
or an empty string). Tools without a `body` declaration reject calls that include one.

### `exec` and placeholders

`exec` is a list, for example:

```json
"exec": ["{python}", "{skill_dir}/tools/search.py", "--limit", "{limit}", "{query}"]
```

The first item is the program. If it contains no `/` it is looked up on `PATH`
(`git`, `node`, `docker`, ...). Placeholders are replaced **inside each list item**, which
is why shell injection is not possible: a value can never become a new argument or a new command.

| Placeholder | Becomes |
| --- | --- |
| `{python}` | The Python interpreter that runs the bridge. **Use this instead of `python` / `python3`**; it works on every OS. |
| `{skill_dir}` | Absolute path of your installed skill folder. |
| `{project_dir}` | Absolute path of the user's project folder (the bridge's `--root`). |
| `{name_of_a_parameter}` | The parameter's value as text (`true`/`false` for booleans). |

An item that consists **only** of an optional parameter placeholder (`"{path}"`) is dropped
completely when the parameter was not given. That lets one command serve both
`git diff` and `git diff -- file.txt`.

Using an undefined placeholder, or pointing `{skill_dir}/...` at a file that is not in the
package, is rejected at import time.

---

## 5. Writing instructions for an AI

The instructions are text that DeepSeek reads at the start of every session. The model has no
memory of your skill apart from this text, so write it like a briefing for a capable colleague.

**Do**

- Say **when** to use each tool, not just what it does ("use `db_schema` before writing any SQL").
- Give **rules** the AI must follow, in short imperative sentences.
- Show **one example call** if the format is unusual (a body, a tricky parameter).
- State **limits** and what *not* to do ("never put secrets in notes", "read-only: you cannot commit").
- Keep it short. Over 6 000 characters triggers a warning; 30 000 is the hard limit. Every character is sent at every session start.

**Don't**

- Don't repeat parameter lists. The extension already adds each tool's signature, risk and
  parameter descriptions automatically. Put detail in the `description` fields instead.
- Don't write instructions that conflict with the extension's own rules (read before editing,
  verify after changing, one tool block per call).
- Don't include secrets, personal data, or instructions to hide actions from the user.

**What the AI is shown** (generated for you):

```
### Skill: Project notes (v1.0.0)
<your instructions text>

Tools from this skill (risk level in brackets):
- note_add(title: string, BODY) [write]: Append a note to .agent-notes.md in the project root.
    title: Short title for the note
    BODY: The note text (can span several lines)
- note_list(last?: integer = 10) [read]: Show the notes written so far.
```

`?` after a name means optional; `= value` is the default.

**Good** (instruction-only skill):

```markdown
# Database rules
1. Before writing SQL, call `db_schema` and use only tables and columns it returns.
2. Never run DELETE or UPDATE without a WHERE clause.
3. Queries must have LIMIT 100 unless the user asks for more.
```

**Weak:** "You have access to a database tool. Use it well."

---

## 6. The tool contract

### How your program is started

- The program in `exec[0]` is started **directly, without a shell**, with the remaining items as arguments.
- Working directory: the project folder (`cwd: "project"`) or your skill folder (`cwd: "skill"`).
- Standard input is a pipe holding the request (see below). It is closed after writing, so
  never wait for interactive input. There is no terminal.
- Environment: the **user's environment** plus these variables:

  | Variable | Value |
  | --- | --- |
  | `DSAGENT_PROJECT_DIR` | The project folder |
  | `DSAGENT_SKILL_DIR` | Your skill folder |
  | `DSAGENT_TOOL` | The tool name being called |
  | `NO_COLOR=1`, `PYTHONUNBUFFERED=1`, `PYTHONIOENCODING=utf-8` | Convenience |

  Do not assume a clean environment: the user's own variables (including any secrets in
  them) are visible to your program. Never print the environment.
- Every call is a **new process**.

### `input: "json"` (default)

Standard input contains one JSON object:

```json
{
  "tool": "note_add",
  "params": { "title": "Chose SQLite" },
  "body": "Single user, offline.\n",
  "project_dir": "/home/me/myproject",
  "skill_dir": "/home/me/.dsagent/skills/project-notes"
}
```

- `params` holds **typed** values (integers are integers, booleans are booleans) with
  defaults already filled in. Missing optional parameters without a default are absent.
- `body` is `null` when the tool has no body.
- One script can serve several tools: switch on `tool`.

Python:

```python
import json, sys
req = json.load(sys.stdin)
```

Node.js:

```js
let raw = '';
process.stdin.on('data', d => raw += d);
process.stdin.on('end', () => { const req = JSON.parse(raw); /* ... */ });
```

### `input: "args"`

Use this to wrap a program that already takes command-line arguments. Only the `exec` list
is used: the JSON request is **not** sent. If the tool has a body, the body text is sent on
standard input. See `examples/skills/code-stats`.

### Answering

What your program prints on **standard output** is what the AI reads.

| You do | The AI sees |
| --- | --- |
| Print text, exit 0 | The text (as a successful result). |
| Print nothing, exit 0 | `(no output)` |
| Print `{"ok": true, "output": "text"}` | `text` |
| Print `{"ok": false, "error": "why"}` (exit code does not matter) | An **error** with that message. |
| Exit non-zero | An error showing the exit code, stdout and stderr. |
| Write to stderr, exit 0 | The result, followed by the stderr text. |
| Run longer than `timeout` | An error `timed out after Ns`; the process tree is killed. |

Guidelines:

- Return **errors the AI can act on**: "no such file: src/a.py", not "failed".
- Keep output **small and useful**. Anything over 20 000 characters is cut (the first 4 000 and
  the last 16 000 are kept). Summarise, paginate, or accept a `limit` parameter.
- Use UTF-8. ANSI colour codes are stripped.
- Output is plain text. The extension shows it inside a collapsible card; the AI reads it as is.

### Showing a picture to the user: `IMAGE:` lines

The AI reads text only, but **the user can see images**. If your tool creates or finds an image,
add a line to its output:

```
Saved drawings/logo.svg
IMAGE: drawings/logo.svg
```

The extension then fetches the file and shows it **inside the tool's card in the chat**, with an
**Open** button that opens a full-size viewer (with *Download PNG* and *Download original*).

Rules:

- The path is **relative to the project folder**, with `/` separators. Absolute paths and `..` are ignored.
- Types: `png`, `jpg`, `gif`, `webp`, `svg`. The file's content must really be that type.
- Up to 4 `IMAGE:` lines per result. Files up to 12 MB can be opened; pictures over 3 MB are not
  shown inline (the card offers **Open** instead).
- The card shows the picture **as it was when the tool finished**. If the file is changed later,
  older cards keep showing the older version, and **Open** shows the current file.
- Write SVG files **without scripts, styles, images or external links** (the `canvas-draw`
  example enforces this with an allowlist). SVGs are only ever displayed through `<img>`, and the
  bridge serves them with a sandboxing `Content-Security-Policy`, but users may also open the file
  directly.
- Tell the AI plainly that **it cannot see the image**, otherwise it may invent a description.
  See `examples/skills/canvas-draw`.

---

## 7. What the AI writes

The AI calls a skill tool exactly like a built-in one: a fenced block with the language `tool`.

````
```tool
name: note_add
title: Database choice
=====BODY=====
Chose SQLite. The app is single-user and offline.
Revisit if sync is needed.
=====END=====
```
````

- `name:` then one `key: value` line per parameter. The value is everything after the first colon,
  trimmed. URLs with colons are fine.
- A body goes between `=====BODY=====` and `=====END=====`. A block that has a body marker but no
  `=====END=====` is treated as **cut off** and is rejected, so a half-written body is never run.
- The bridge checks the call before running your program:
  - unknown parameter -> error listing the valid ones,
  - missing required parameter -> error,
  - value not convertible to the declared type, or not in `enum` -> error,
  - body given but not declared, or required but empty -> error.

  So your program only ever sees valid, typed input. You still must treat the *content* as untrusted (section 9).
- Up to 6 tool blocks per reply are executed, in order.

---

## 8. Risk levels, approval and trust

Every tool declares a `risk`. The extension uses it to decide whether to ask the user.

| `risk` | Means | Examples |
| --- | --- | --- |
| `read` | Only reads or queries. No changes anywhere, no network. | list tables, count lines, `git log` |
| `write` | Changes files or data on this computer. | append a note, format a file |
| `exec` | Anything else: runs other programs, uses the network, changes remote systems, or you are not sure. | HTTP calls, deploys, running tests |

**Declare the highest risk that applies.** If unsure, use `exec` (the default).

| Situation | Approval prompt? |
| --- | --- |
| Tool is `write` or `exec` | **Yes**, every time, showing the skill, the tool and its arguments. |
| Tool is `read`, skill **not** trusted | Yes. |
| Tool is `read`, skill **trusted** by the user | No. |
| User ticked "Run commands and tools without asking" | No, for everything. |

"Trusted" is a switch the **user** sets per skill in the manager. It is reset to off whenever
the skill is replaced by an import (the code may have changed). You cannot set it from the manifest.

Mislabelling a tool as `read` to avoid prompts is a breach of trust and may get your skill
removed by users. The import screen shows each tool's risk next to its exact command.

---

## 9. Security rules for tool authors

Installing a skill means running someone's program with the user's full permissions.
The bridge validates the *shape* of calls; it cannot sandbox your code. Be careful.

1. **Treat every parameter and the body as untrusted.** They come from an AI that may have been
   steered by text in files, web pages or tool output (prompt injection).
2. **Never build shell commands from input.** That is why `exec` has no shell. If you must spawn
   a process, pass an argument list (`subprocess.run([...])`, `spawn(cmd, args)`), never
   `shell=True` / `exec("...")`. The importer warns when a tool runs `sh`, `bash`, `cmd` or `powershell`.
3. **Keep file access inside the project.** Resolve the path and check it:

   ```python
   project = Path(req["project_dir"]).resolve()
   target = (project / req["params"]["path"]).resolve()      # resolve() follows symlinks
   if project != target and project not in target.parents:
       ...refuse...
   ```

   ```js
   const project = fs.realpathSync(req.project_dir);
   const target = path.resolve(project, req.params.path);
   if (target !== project && !target.startsWith(project + path.sep)) { /* refuse */ }
   ```
4. **Stop option injection** when you pass a value to another program: put `--` before it
   (`["git", "diff", "--", "{path}"]`) or validate it. A value such as `--output=/etc/x` must
   not become a flag. Integer and boolean parameters are safe because they are converted first.
5. **Don't read or print secrets.** Do not dump environment variables, config files, SSH keys,
   browser data. If your tool needs a credential, read it from a file the user chose, not from
   the AI's parameters, and never echo it back.
6. **No surprises.** A tool does what its `description` says and nothing else. No telemetry,
   no downloading and running code, no writing outside the project (or your own skill folder).
7. **Be conservative by default.** Prefer dry-run behaviour, ask for explicit parameters for
   anything destructive, never delete without a `confirm`-style parameter.
8. **Bound your work.** Respect `timeout`, limit output size, limit file sizes you read.
9. **Declare risk honestly** (section 8).
10. **Dependencies:** prefer the standard library. If you need packages, vendor them inside the
    skill or document the install step. Do not install anything at runtime.
11. **Make it reviewable.** Users see your `exec` commands before installing. Keep scripts small
    and readable, and avoid minified or obfuscated code.

What the bridge does for you: validates the manifest, rejects path tricks, symlinks and
oversized archives at import, shows the user a review screen, asks for approval according to
risk, runs your program without a shell, applies a timeout, and caps output. It does **not**
restrict what your program does once running.

---

## 10. Testing and debugging

### The helper: `bridge/skilltool.py`

```
python skilltool.py validate <folder or .zip>
python skilltool.py run <folder> <tool> [key=value ...] [--body TEXT | --body-file FILE] [--project DIR]
python skilltool.py pack <folder> [-o OUTPUT.zip]
```

- `validate` checks the manifest exactly like the importer and prints your tools, their
  commands, and warnings.
- `run` executes a tool **the same way the bridge does** (same argument checks, same
  environment, same output rules). Parameters are written as plain strings, like the AI writes them.
  If the tool fails you see the exact error text the AI would receive. `--project` is the folder
  the tool sees as the project (default `./skilltool-project`).
- `pack` validates, then writes `<id>-<version>.dsskill.zip` with a fixed timestamp, so identical
  sources give identical archives.

Examples:

```
python skilltool.py run ../examples/skills/project-notes note_add title="Hello" --body "First note"
python skilltool.py run ../examples/skills/code-stats count_lines ext=.py --project ../myproject
python skilltool.py run ../examples/skills/project-notes note_add             # shows the "missing required parameter" error
```

### In the browser

1. Start the bridge. Every call is printed in its console window: the tool name and its first argument.
2. Import your skill and press **Start session** (or **Sync**). The panel shows
   `Skills: N active, M tools`.
3. Press **Diagnose** in the panel to see the skills and custom tool names the extension knows.
4. Each call appears in the chat as a card. Open it to see the exact call and the result or error.
5. If the AI misuses your tool, improve the `description` fields and instructions rather than adding code.
   Test with a few different prompts.

### Test checklist

- [ ] `validate` passes with no warnings you do not understand.
- [ ] Each tool works with only its required parameters, and with all parameters.
- [ ] Each tool returns a helpful error for: missing file, empty input, huge input, a path like `../../etc/passwd`.
- [ ] Output stays well under 20 000 characters on large projects.
- [ ] Works with the project path containing spaces (and, on Windows, backslashes).
- [ ] Runs from a different working directory (use `{skill_dir}` and `project_dir`, not relative paths).
- [ ] The risk you declared is the real risk.

---

## 11. Packaging and updating

**Pack:** `python skilltool.py pack my-skill` -> `my-skill-1.0.0.dsskill.zip`.
You can also zip the folder yourself; either layout in section 3 works.

**Distribute:** give people the `.zip`. They import it on the Skills page. Include a checksum or
sign your releases if you publish them; the review screen shows each tool's commands, but
users should still trust where the file came from.

**Executable files:** if a tool is a script run directly (`"exec": ["{skill_dir}/run.sh"]`),
it needs the executable bit and a shebang line. `pack` keeps permission bits on macOS/Linux.
Scripts started through an interpreter (`{python}`, `node`) need neither.

**Updating:**

- Raise `version`, keep the same `id`, and import again. The review screen says
  "Replaces vX.Y.Z".
- Replacing a skill **resets "Trusted"** to off, because the code may have changed. Users must
  re-trust it.
- Whether the new version is enabled follows the "Enable after installing" box.
- An open DeepSeek session is not told about changes automatically: users press **Sync**
  in the panel to send the updated instructions and tool list to DeepSeek.

**Removing:** deleting a skill in the manager removes `~/.dsagent/skills/<id>/` completely.
Files your tools created inside the user's *project* stay there.

**Name clashes:** if two enabled skills define the same tool name, only the one whose `id`
sorts first is active; the manager marks the other as "not active". Use distinctive tool names.

---

## 12. Cross-platform notes

- Use `{python}`, never the literal `python` or `python3` (Windows has `python`, many Linux
  systems only `python3`).
- Other interpreters (`node`, `ruby`, `php`, `pwsh`) must be on the user's `PATH`. The importer
  warns when a program is not found on this computer; mention requirements in your `description`.
- Avoid bash-only scripts if you want Windows support. A Python or Node script runs everywhere.
- After `{skill_dir}` and `{project_dir}`, forward slashes work in Python and Node on Windows too:
  `{skill_dir}/tools/x.py`.
- Use LF line endings in scripts that have a shebang.
- Read and write files as UTF-8 explicitly.
- Never assume a particular current directory; use `project_dir` / `skill_dir` from the request.
- Windows paths and quoting are the OS's business, not yours: there is no shell, so no quoting is needed.

---

## 13. Cookbook

### A. Instruction-only skill

```json
{ "schema": 1, "id": "team-style", "name": "Team style", "version": "1.0.0",
  "description": "Our code conventions.", "instructions": "instructions.md" }
```

### B. Wrap an existing command, with no code at all

```json
{
  "schema": 1, "id": "git-readonly", "name": "Git (read-only)", "version": "1.0.0",
  "description": "Look at git history.",
  "tools": [
    { "name": "git_status", "description": "Short status with branch.",
      "exec": ["git", "--no-pager", "status", "--short", "--branch"], "risk": "read" },
    { "name": "git_log", "description": "Latest commits.",
      "params": { "count": { "type": "integer", "default": 10 } },
      "exec": ["git", "--no-pager", "log", "--oneline", "-n", "{count}"], "risk": "read" },
    { "name": "git_diff", "description": "Uncommitted changes, optionally for one path.",
      "params": { "path": { "type": "string" } },
      "exec": ["git", "--no-pager", "diff", "--", "{path}"], "risk": "read" }
  ]
}
```

`{count}` is an integer, so `1; rm -rf /` is rejected before anything runs. `--` stops a path
that looks like a flag from being read as one. When `path` is omitted its argument disappears.
(`examples/skills/git-readonly`.)

### C. Python tool that takes a body

See `examples/skills/project-notes`: `note_add` (title + multi-line body) and `note_list` share one script.

### C2. A larger skill that makes pictures

`examples/skills/canvas-draw` is the most complete example. DeepSeek cannot generate pixels, so the
skill lets it **draw**: a compact script (`rect`, `circle`, `polygon`, `path`, `text`, gradients,
groups) is validated and compiled to SVG, shown in the chat, and editable by shape id. It shows:
a custom mini-language in a `body`, all-or-nothing validation with line-numbered errors, a layout
checker that tells a "blind" AI when shapes fall off the canvas, an allowlist SVG sanitiser,
atomic file writes, `IMAGE:` previews, and a test suite (`tests/test_canvas_draw.py`).

### D. A tool that calls a web API (Node.js)

See `examples/skills/fetch-url`. It validates the URL, applies a timeout, truncates the response,
and reports errors as `{"ok": false, "error": ...}`. It is declared `risk: "exec"` because it uses the network.

### E. Passing a secret

Do not make the AI pass credentials. Read them yourself from a file the user controls:

```python
token = (Path.home() / ".config" / "myservice" / "token").read_text().strip()
```

Say in your `description` where the user must put it, and never include it in output or error messages.

### F. A tool with a limited choice

```json
"params": { "level": { "type": "string", "enum": ["quick", "full"], "default": "quick" } }
```

### G. Several tools, one script

```python
req = json.load(sys.stdin)
handlers = {"db_tables": tables, "db_query": query}
print(handlers[req["tool"]](req["params"], req["body"]))
```

### H. Returning structured data

Return compact text the AI can read. Tables as aligned columns or TSV, lists one item per line.
Avoid huge JSON blobs: summarise or provide a `limit` parameter.

---

## 14. Limits

| Thing | Limit |
| --- | --- |
| Tools per skill | 20 |
| Parameters per tool | 20 |
| `id` | 2 to 41 characters |
| Tool name | 2 to 41 characters |
| `name` / `description` fields | 80 / 300 characters |
| Instructions | 30 000 characters (warning above 6 000) |
| Files in a package | 500 |
| Package size unpacked / imported | 20 MB / 12 MB |
| Tool timeout | 1 to 900 seconds (default 60) |
| Tool output kept | 20 000 characters |
| Tool calls executed per AI reply | 6 |
| Staged import validity | 15 minutes |

---

## 15. Troubleshooting

| Message / symptom | Cause and fix |
| --- | --- |
| `skill.json not found at the top level of the package` | Your zip nests files more than one folder deep. Zip the contents, or a single folder holding them. |
| `skill.json is not valid JSON` | Trailing comma or comment. JSON has neither. |
| `'schema' must be 1` | Add `"schema": 1`. |
| `tool 'x': collides with a built-in tool` | Rename it. |
| `"name:" appears more than once` | The AI wrote a second `name:` line. `name:` selects the tool, which is why no parameter may be called `name`. |
| `exec refers to 'tools/x.py', which is not in the package` | Wrong path after `{skill_dir}/`, or the file is missing from the zip. |
| `unknown placeholder {x} in exec` | Placeholders are `{python}`, `{skill_dir}`, `{project_dir}` and your own parameter names. |
| `program not found on this computer: X` | The program is not on `PATH`. Install it, or use `{python}`. |
| `missing required parameter 'x'` | The AI omitted it. Improve its `description`, or give it a `default`. |
| `unknown parameter(s): ...` | The AI invented a parameter. Describe the real ones better. |
| `this tool needs a =====BODY===== section` | `body.required` is true and the AI sent none. |
| `block is incomplete ... =====END=====` | The AI's reply was cut off or it forgot `=====END=====`. It is asked to resend; nothing ran. |
| `timed out after Ns (process killed)` | Raise `timeout` (max 900) or make the tool faster. |
| `exit code N` plus output | Your program crashed; read the stdout/stderr shown. Run it with `skilltool.py run`. |
| AI says "unknown tool" | The skill is disabled, or the session predates the install. Enable it, then press **Sync** or start a new session. |
| Tool never runs | The approval prompt is waiting in the panel (bottom-right of the page). |
| Import shows a warning "needs 'node'" | The program is not installed on this computer. |
| Skill tool shows "not active" | Another enabled skill uses the same tool name. Rename yours or disable the other. |
| Garbled characters | Read and write files as UTF-8. |

---

## 16. FAQ

**Can a skill change the AI's behaviour without tools?** Yes: an instruction-only skill is just text.

**Can tools be written in any language?** Yes. Anything that can read stdin and write stdout:
Python, Node, Go, Rust, shell, a compiled binary. For compiled binaries ship one per platform
or document how to build it.

**Can a tool call another tool?** No. Each call is independent. The AI decides what to call next.

**Can a tool show an image to the user?** Yes: print `IMAGE: path` (section 6). The AI itself
still cannot see it.

**Can a tool show progress or stream output?** No. The AI receives one result when the process ends.

**Can a tool ask the user a question?** Not directly. Return a message telling the AI what is
missing; the AI asks the user in the chat.

**Can I override a built-in tool such as `read_file`?** No. Built-in names are reserved.

**Where do tools keep state?** In files. Use the project folder for project data
(prefix hidden files with `.agent-`) or your skill folder for caches. Never rely on process memory.

**Can I depend on another skill?** Not formally. Mention it in your `description`.

**How do users trust a skill?** By reading the review screen, which shows every command a tool
will run, and by approving calls. Publish source code and a license so it can be audited.

**Why are my instructions not followed?** Shorten them, put the most important rule first,
use numbered imperative sentences, and add one concrete example call.

**Is there a schema for editors?** Yes: `docs/skill.schema.json`. Add `"$schema": "<path>"`
to your manifest (editors such as VS Code then validate and complete it).
