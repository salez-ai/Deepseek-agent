# DeepSeek Agent

A Chrome/Edge extension plus a small local bridge that turns the free
DeepSeek chat (chat.deepseek.com) into a coding agent: it can explore a
project, edit files, run your tests, read the output and keep fixing until
they pass. No API key needed.

```
DeepSeek chat page  ->  extension (reads replies, runs the loop)
                           |  via the extension's background worker
                           v
                    bridge.py on 127.0.0.1  ->  your project folder
```

## Setup

You need Python 3.9+ and Chrome or Edge.

1. **Load the extension.** Open `chrome://extensions` (or `edge://extensions`),
   turn on Developer mode, click **Load unpacked**, pick the `extension` folder.
2. **Start the bridge** for the project you want the agent to work on.
   On Windows, drag the project folder onto `start.bat`, or run:

   ```
   python bridge\bridge.py --root C:\path\to\your\project
   ```

   Always start it with `python ...`. Typing just `bridge.py` on Windows
   opens it through the file association in a separate window and drops the
   `--root` argument. Check that the banner shows the folder you meant.
   `--root` is required, and the bridge refuses drive roots, system folders
   and your whole home folder.

   It prints a token. The token is saved in `~/.dsagent/token`, so you only
   copy it once.
3. **Paste the token.** Click the extension icon, paste the token, press
   *Save and test*. You should see "Connected" and your project path.
4. **Open https://chat.deepseek.com**, start a new chat, and click
   **Start session** in the panel at the bottom right. DeepSeek replies "Ready."
5. Type a task, for example:
   *"The tests in this project fail. Find out why and fix it."*

## What the agent can do

| Tool | Purpose |
| --- | --- |
| `list_dir` | Show the folder tree |
| `read_file` | Read a file, optionally a line range |
| `search` | Regex search across files |
| `write_file` | Create or overwrite a file |
| `replace_in_file` | Exact SEARCH/REPLACE edits, must match one place |
| `delete_file` | Delete a file (never folders) |
| `run_command` | Run a shell command with a timeout (build, test, lint) |

DeepSeek writes tool calls as fenced `tool` blocks. The format is plain text,
not JSON, so source code does not need escaping:

````
```tool
name: replace_in_file
path: src/calc.py
=====SEARCH=====
    return a - b
=====REPLACE=====
    return a + b
=====END=====
```
````

`write_file` and `replace_in_file` blocks must finish with `=====END=====`.
A block without it is rejected as cut off, and nothing is changed.

If DeepSeek stops mid-reply and shows its **Continue** button, the extension
**cannot press it for you**: the site ignores clicks made by scripts. Instead it
highlights the button with an orange outline, rings and sends a notification
(see *Alerts* below). It never acts on a cut-off reply: once you click Continue
it waits until the reply has grown and settled.

The extension runs the blocks, then sends the results back as a new message,
and repeats until DeepSeek answers without a tool block. After 40 automatic
steps it pauses and shows **Resume**. Typing your own message resets the count.

## Alerts: it tells you when it needs you

The agent often waits for you while you are in another window. The extension rings and notifies
you whenever it is blocked on you:

| Event | Rings | Desktop notification | Repeats |
| --- | --- | --- | --- |
| A command or skill tool needs your **Allow** | yes | when you are away from the tab | up to 3 times |
| DeepSeek stopped mid-reply: **click Continue** (the button is outlined in orange) | yes | when you are away | up to 3 times |
| The agent **paused** after 40 automatic steps (press **Resume**) | yes | when you are away | up to 3 times |
| The agent stopped because of an **error** | yes | when you are away | up to 3 times |
| The agent **finished** and waits for your next message | softer, only when you are away | only when you are away | no |

"Away" means the tab is hidden or the window is not focused. While you are looking at the page you
get the sound and the highlight but no pop-up. Clicking a notification takes you back to the right
tab. The tab title also shows a bell while something is waiting. Everything clears by itself as soon
as you act.

Settings are in the extension popup, section **Alerts**: sound on/off, desktop notification
on/off, ringtone (Chime, Bell, Digital), volume, and how soon to remind you again (never, 30 s,
1 min, 2 min), with Test buttons. The bell in the panel header mutes the sound quickly.
The ringtones are generated in the browser, so there is no audio file to install.

## Skills (add instructions and tools)

A **skill** is a small package that gives the AI extra instructions, extra tools, or both.
For example: house coding rules, a tool that queries your database, a wrapper around `git`.

1. Click the extension icon, press **Manage skills...** (the bridge must be running).
2. Drop a `.zip` or a skill folder on the page. Try one from `examples/packaged/`.
3. **Review it.** The screen shows every tool, how risky it says it is, and the exact command
   it will run. Press **Install**.
4. In DeepSeek press **Start session**. If a session is already open, press **Sync** in the
   panel to tell DeepSeek about the change.

Skill tools show up as dropdown cards with their result, like the built-in ones.

**Safety.** A skill's tools are programs that run on your computer with your permissions, so
only install skills from people you trust. Tools that write, or run programs, always ask for
approval. Tools declared read-only ask too, unless you mark that skill **Trusted**; replacing a
skill resets Trusted. Details in `docs/SKILL_DEVELOPER_GUIDE.md`, section 8 and 9.

**Writing your own:** read `docs/SKILL_DEVELOPER_GUIDE.md`. The helper
`bridge/skilltool.py` validates, runs and packs a skill (`validate`, `run`, `pack`), and
`docs/skill.schema.json` gives editors completion for `skill.json`.

Included examples (`examples/skills/` has the sources, `examples/packaged/` the importable zips):

| Skill | Shows |
| --- | --- |
| `project-notes` | Python tool, multi-line body, read and write tools |
| `fetch-url` | Node.js tool calling a web API |
| `code-stats` | `input: "args"` mode and an optional argument |
| `git-readonly` | A tool with no code at all, wrapping `git` |
| `python-style` | Instructions only |
| `canvas-draw` | Drawing toolkit: a mini-language compiled to SVG, previews in the chat |

## Drawing images (the `canvas-draw` skill)

DeepSeek cannot produce pixels, so this skill lets it **draw**. It writes a short script of shapes
(`rect`, `circle`, `polygon`, `path`, `text`, gradients, groups); the bridge validates it and turns it
into an **SVG** that appears in the chat. No API key, no internet, no cost.

1. Import `examples/packaged/canvas-draw-1.0.0.dsskill.zip` on the Skills page, then press
   **Start session** (or **Sync**).
2. Ask: *"Draw a flat-style logo of a fox in orange and navy, 512x512"*. Approve the `draw` call.
3. The picture shows inside the tool card. Ask for changes ("make the sun bigger"): DeepSeek
   redraws just that shape by its id. **Open** shows it full size; **Download PNG** exports 1x to 4x.

Be realistic about quality: DeepSeek draws **blind** (it cannot see the result), so expect clean
logos, icons, diagrams, charts and simple scenes. Complex or photographic images are out of reach.
To compensate, the tool warns it about shapes that fall off the canvas or are invisible, and
`draw_list` shows where every shape sits.

## Appearance

The extension also tidies the DeepSeek page. Each part can be switched off in
the popup (click the extension icon, section *Appearance*):

- **Collapse the thinking block.** It clicks DeepSeek's own "Thought for..."
  header once, so the reasoning starts closed. If it cannot find that header it
  hides the reasoning itself and adds a *Show reasoning* button. Open it
  yourself any time; it will not be collapsed again.
- **Tool calls as dropdowns.** Each call becomes a closed card:
  `replace_in_file  src/calc.py  [Done]`. Open it to see the exact call and the
  result or error that came back. Chips: Queued, Running, Done, Failed.
- **Fold agent messages.** The long instruction message and the
  `<<<RESULT ...>>>` replies the extension sends become one-line cards too.
- **Cleaner fonts, code blocks and tables.** Better spacing and headings,
  monospace code, bordered tables. Colours are derived from the page's own text
  colour, so it works in both DeepSeek's light and dark themes. The agent panel
  follows the page theme as well.

The original elements are only hidden, never removed, so the agent loop still
reads them. Fonts are the system stack (Inter if installed, else Segoe UI,
SF, Roboto) because web fonts can be blocked by the site's security policy.

## Safety

- The bridge listens on `127.0.0.1` only and requires the token on every call.
  It also rejects requests that carry a web-page `Origin` and sends no CORS
  headers, so a random website cannot talk to it.
- Every path is resolved and must stay inside `--root` (symlinks included).
- Files are copied to `.dsagent_backups/<timestamp>/` inside the project
  before they are overwritten or deleted. Add that folder to `.gitignore`.
  Use `--no-backups` to turn it off.
- **`run_command` asks you first** in the panel (Allow / Allow all this session
  / Deny). Use `--no-commands` to disable commands completely.
- Commands run with your user's permissions. "Allow all" means whatever the
  model writes will run. Use a git repo and read what you approve.
- **Prompt injection:** the agent reads files and command output. A file in
  your project could contain text that tries to steer the model. The
  confirmation prompt for commands is your main protection, so keep it on when
  working with code you did not write.

## Status of testing

Tested here:

- Bridge: every tool, path-escape attempts, wrong token (401), web-page origin
  (403), timeouts that kill the whole process tree.
- Parser: valid and malformed blocks, including skill tools and bodies.
- Alerts: ringtones rendered offline and measured (32 checks: audible, no clipping, click-free
  starts, the right notes, equal loudness across tones, volume scaling); a real-extension run (32
  checks: popup settings, approval alert, reminders, "away" handling, mute, the Continue flow,
  clearing, clicking a notification to return to the tab); the paused alert and Resume.
  **Not testable here:** the actual sound coming out of speakers, and how your operating system
  shows the notification (the headless test browser has no notification centre).
- canvas-draw: 49 unit tests (`python -m unittest tests/test_canvas_draw.py`: parser, rendering,
  injection attempts, sanitiser, edit-by-id, symlinks), pixel checks of a rendered scene in
  Chromium, and a **real-extension end-to-end run** (the actual extension, bridge and skill loaded
  into Chromium, with a scripted model on a mock DeepSeek page): approvals, previews, per-call
  snapshots, the viewer, PNG export, and refusal of six file-path attacks (31 checks).
- Skills: install flow over HTTP (50 checks: hostile zips with `../`, absolute paths,
  symlinks and a zip bomb, bad manifests, timeouts, trust reset on update), the manager page
  in real Chromium (25 checks, including an XSS attempt in a skill name), and the full AI loop
  with skills (18 checks: prompt contents, approval policy, multi-line bodies, mid-session Sync).
- The full loop (start, explore, edit, approve a command, test passes, finish)
  ran in an automated test with the real `content.js` and the real bridge,
  against a **simulated** DeepSeek page.

**Not tested: the live chat.deepseek.com page.** The appearance features were
checked in real Chromium against a mock of DeepSeek's markup (collapse, cards,
re-renders, light and dark, screenshots) but not on the real site. DeepSeek changes its markup
often, so the selectors may need a tweak. They are at the top of
`extension/content.js`:

```js
const SEL = {
  input: 'textarea',          // the message box
  reply: '.ds-markdown',      // each assistant answer
  ignoreInside: '...',        // reasoning ("DeepThink") text, never parsed
};
```

If the agent seems stuck, press **Retry** in the panel: it runs the tool calls
in DeepSeek's last reply right now. The panel log always says why a reply was
skipped.

If nothing happens, click **Diagnose** in the panel. It tells you whether the
input and replies were found, how many tool blocks the last reply had, and how
many thinking blocks and tool cards it sees. If the thinking block does not
collapse, its selector (`thinkContent` in `extension/style.js`) or the header
wording (`THINK_HEADER`) needs adjusting. Fix
the selector with the browser's Inspect tool.

## Limitations

- If "Continue" starts a new message instead of extending the old one, the cut
  block has no `=====END=====`, so it is rejected and the model is asked to
  resend it. This is safe but costs one extra turn.
- Chrome slows down timers in tabs that have been hidden for a long time
  (more than 5 minutes). The extension also wakes up on page changes, so a Continue
  button or a finished reply is normally noticed at once, but alerts for a tab you
  left hidden for a long time can arrive late. A separate window helps.
- Sound needs one click or keypress on the DeepSeek page after it loads (browsers
  forbid sound before that). Pressing **Start session** counts.
- A reply is treated as finished when it stops changing for 2.5 s
  (`stableMs`). Raise it if long answers get cut off mid-call.
- If a file you write contains a line of exactly three backticks, DeepSeek
  must fence the tool block with four. Markdown files are the usual case.
- Duplicate protection works per turn: a tool call that already ran is skipped
  only if it shows up again in the same reply (re-render or Continue). The same
  call in a later turn runs normally.
- One chat tab at a time. Opening another chat ends the session.
- Quality depends on the model. If it starts answering in plain text instead
  of tool blocks, remind it to use the tool format or start a new session.
- Automating the consumer chat page may go against DeepSeek's terms and can hit
  rate limits. For heavy use, their API is the supported route.
