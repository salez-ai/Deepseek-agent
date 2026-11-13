# DeepSeek Agent

A Chrome/Edge extension plus a small local bridge that turns the free
DeepSeek chat (chat.deepseek.com) into a coding agent: it can explore a
project, edit files, run your tests, read the output and keep fixing until
they pass. No API key needed.

The idea (extension in the chat page + bridge on your PC) follows
[ZeroScript-Free](https://github.com/sebattfg/ZeroScript-Free), which does
the same for Roblox Studio. This project is written from scratch for general
projects and shares no code with it.

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
steps it pauses and shows **Keep going**. Typing your own message resets the count.

## Continuing an earlier chat: Resume session

Say the agent built a game a few days ago and you want to improve it. Open that DeepSeek chat, make
sure the bridge is running for the same project folder, and press **Resume session** (not Start).

What it does:

1. **Recognises the chat.** If the chat holds an earlier agent session, the panel says so (with the
   project folder and how long ago) and highlights **Resume session**. New chats highlight
   **Start session** instead.
2. **Never re-runs old work.** Tool calls already in the chat are history. They are not executed
   again and their cards read **Earlier**. This matters: re-running an old "write file" would wipe
   out the edits you made since.
3. **Refreshes the agent.** It sends one message with the current rules, your enabled skills and the
   **files that exist now**, and tells the agent that anything it remembers from earlier may be out of
   date and must be read again before editing. The agent answers "Ready to continue."
4. **Protects the wrong-folder case.** If the bridge now points at a different project than the one
   the chat worked on, the panel names both folders and asks before continuing, and the agent is
   told the folder changed. Press Cancel to stop and restart the bridge with the right `--root`.

Then just say what you want: *"Add a score display and a pause menu to the game."*

Which chats are remembered is stored in your browser (the chat address, the project folder and the
time); nothing is sent anywhere. **Start session** also works in an old chat, but it treats the chat
as new and does not show the agent the current files.

## Alerts: it tells you when it needs you

The agent often waits for you while you are in another window. The extension rings and notifies
you whenever it is blocked on you:

| Event | Rings | Desktop notification | Repeats |
| --- | --- | --- | --- |
| A command or skill tool needs your **Allow** | yes | when you are away from the tab | up to 3 times |
| DeepSeek stopped mid-reply: **click Continue** (the button is outlined in orange) | yes | when you are away | up to 3 times |
| The agent **paused** after 40 automatic steps (press **Keep going**) | yes | when you are away | up to 3 times |
| The agent stopped because of an **error** | yes | when you are away | up to 3 times |
| The agent **finished** and waits for your next message | softer, only when you are away | only when you are away | no |

"Away" means the tab is hidden or the window is not focused. While you are looking at the page you
get the sound and the highlight but no pop-up. Clicking a notification takes you back to the right
tab. The tab title also shows a bell while something is waiting. Everything clears by itself as soon
as you act.

Settings are in the extension popup, section **Alerts**: sound on/off, desktop notification
on/off, **a checkbox for each event** (approval needed, Continue needed, paused, errors, finished),
ringtone (Chime, Bell, Digital), volume, and how soon to remind you again (never, 30 s, 1 min,
2 min), with Test buttons. An event you untick makes no sound, no notification and no title mark
(the highlight and the status line still show). The bell in the panel header mutes the sound quickly.
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

## Real image generation (your Perchance generator)

DeepSeek cannot make pictures itself, so the extension connects it to an **image generator web page**
(by default <https://perchance.org/5jsm6cis4r>). The agent gets one more tool, `generate_image`:

```
name: generate_image
prompt: a cozy fox in a snowy forest, golden light
size: portrait            (square | landscape | portrait | large; default square)
negative: blurry, text    (optional)
seed: 777                 (optional)
filename: fox hero        (optional; used to name the saved file)
```

1. The agent writes that call. You are asked to **Allow** it (the prompt, size, negative prompt and seed
   are shown). The prompt text is sent to the generator site, so only allow what you are happy to send.
2. The extension asks the generator page for the picture, then saves it in your project:
   `generated-images/<name>-<id>.jpg`, plus a line in `generated-images/index.jsonl` (prompt, seed, size).
3. The picture appears in the tool's card in the chat. **Open** shows it full size, with PNG download.
   The agent itself cannot see the picture; it is told so, and is given the saved path to use in code.

**How it reaches the page.** The generator needs a real browser, and the extension *is* your browser:

| Method | What happens | Notes |
| --- | --- | --- |
| Hidden frame | The page is loaded in a hidden frame and driven with `postMessage` | Silent and fast; reused between pictures |
| Background tab | A tab opens with the page's direct link, the extension reads the finished picture and closes the tab | A normal browser visit: use it if the page needs browser verification or refuses to be framed |
| Automatic (default) | Hidden frame first; if the page cannot be reached, a background tab | |

Settings are in the extension popup, section **Image generation**: on/off, the generator address
(https, or http on your own computer), the method, how long to wait, and a **Test generator**
button that shows the picture it got. If you turn it off or on during a session, press **Sync**.

**Privacy and safety.** Pictures and prompts go only to the address you configured. Each call needs your approval.
Messages from the generator page are accepted only from its own frame and origin, and only if the picture
is a real png/jpg/gif/webp data URL of at most 14 MB. The bridge stores it under a name it chooses itself,
inside `generated-images/`, after checking the file's real type.
This feature adds the permissions *offscreen* (the hidden frame), *scripting* (to read the finished picture
from the background tab) and access to perchance.org.

**If you edit the generator page.** The extension depends on these parts of it:

- On load it posts `{action: 'ready'}` to its parent, and answers `{action: 'ping'}` with `{action: 'ready'}`.
- `{action: 'generate', prompt, resolution, negativePrompt?, seed?}` -> `{action: 'result', dataUrl, seed, resolution}`
  or `{action: 'error', message}`. `resolution` is one of `512x512 | 768x512 | 512x768 | 768x768`.
- For the background tab it opens `<address>?prompt=...&resolution=...&negative=...&seed=...&style=&autogen=1&embed=1`
  and reads `#resultImg` (`src` = the data URL, not `hidden`), `#metaEl` (`seed: N`) and `#statusEl`
  (text containing "failed" or "error" means failure). It passes an **empty `style=`** on purpose: your page
  otherwise adds its Style dropdown (default "Photo") to every prompt.
- If the prompt has no negative prompt, the page's own default negative prompt applies.

To check a changed page without internet access: put it in `tests/fixtures/perchance_page.html`, run
`python tests/perchance_mock_server.py 8789`, set the generator address to `http://127.0.0.1:8789/gen`
and press **Test generator**. The mock serves your page's real code and only replaces the parts that exist
on perchance.org itself (`generateImage`, `randomIdea`, `generatorName`).

**Not tested:** the live perchance.org (this was built without internet access to it). If the real page
behaves differently, the popup's **Test generator** button shows the exact error, and the method setting
lets you switch between the hidden frame and a background tab.

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
- Resume session and per-event alerts: 43 checks with the real extension (an old chat is recognised;
  the resume message carries the rules, skills and current files; old tool calls never re-run and read
  "Earlier"; the person's edits survive; the agent edits the current file; the wrong-folder question
  names both folders and Cancel sends nothing; a new chat is remembered once it has an address; each
  event's switch silences exactly that event and cancels a pending one; the popup saves them).
- Alerts: ringtones rendered offline and measured (32 checks: audible, no clipping, click-free
  starts, the right notes, equal loudness across tones, volume scaling); a real-extension run (32
  checks: popup settings, approval alert, reminders, "away" handling, mute, the Continue flow,
  clearing, clicking a notification to return to the tab); the paused alert and Keep going.
  **Not testable here:** the actual sound coming out of speakers, and how your operating system
  shows the notification (the headless test browser has no notification centre).
- Image generation: 36 bridge checks (real png/jpg/webp/gif, hostile names, a symlinked folder, size
  and type limits, auth) and 57 real-extension checks against **the generator page's actual code**
  with only Perchance's own functions stubbed: popup settings and address validation, the full agent flow
  (approval, prompt/size/negative/seed passed exactly, saved JPEG, preview in the chat), validation before
  any approval prompt, on/off with Sync, a failing generator, a hang, a page that forbids framing (falls
  back to a tab), a broken page, two pictures queued, and forged or oversized messages.
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
