# DeepSeek Agent

**Turn DeepSeek Chat into an autonomous coding agent — without a DeepSeek API key.**

DeepSeek Agent is a Chrome/Edge browser extension paired with a local Python bridge that allows DeepSeek to work directly with your project files.

It can explore your codebase, create and edit files, run commands, execute tests, analyze errors, and iteratively improve your project.

It also supports installable skills, image generation, SVG drawing, session resumption, and desktop notifications.

> [!WARNING]
> DeepSeek Agent can modify or delete files and execute commands on your computer. Review approval prompts carefully. Never approve commands or install skills from sources you do not trust.

## Table of Contents

- [Features](#features)
- [How It Works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Agent Tools](#agent-tools)
- [Continuing Previous Sessions](#continuing-previous-sessions)
- [Alerts and Notifications](#alerts-and-notifications)
- [Skills](#skills)
- [Image Generation](#image-generation)
- [SVG Drawing](#svg-drawing)
- [Appearance](#appearance)
- [Security and Privacy](#security-and-privacy)
- [Testing and Verification](#testing-and-verification)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Disclaimer](#disclaimer)
- [License](#license)

---

## Features

| Feature | Description |
|---|---|
| Autonomous coding | Explore, modify, and improve existing projects. |
| File management | Create, read, search, replace, and delete files. |
| Command execution | Run tests, builds, scripts, and other shell commands with approval. |
| Iterative debugging | Read errors, make corrections, and rerun tests. |
| Session resumption | Continue previous coding sessions without repeating old tool calls. |
| Installable skills | Extend the agent with custom instructions and tools. |
| Image generation | Generate and save images through a configurable external generator. |
| SVG drawing | Create vector illustrations using the `canvas-draw` skill. |
| Notifications | Get alerted when the agent needs your attention. |
| Interface improvements | Organize tool calls, messages, code, and reasoning blocks. |

### No API key required

The extension works through the DeepSeek consumer website rather than directly calling the DeepSeek API.

You do not need a DeepSeek API key. However, an internet connection and access to the DeepSeek website are required.

## How It Works

DeepSeek Agent consists of two main components:

```text
              DeepSeek Chat
             chat.deepseek.com
                    |
                    v
            Chrome / Edge Extension
          Reads replies and runs the loop
                    |
                    | Extension background worker
                    v
             Local Python Bridge
             127.0.0.1 + token
                    |
                    v
              Your Project
          Files, folders, commands,
             tests and outputs
```

**The extension** reads DeepSeek's responses, identifies tool calls, executes them through the bridge, and sends the results back to DeepSeek.

**The bridge** provides controlled access to your project directory and handles file operations, command execution, skills, and generated assets.

The AI decides what to do. Your local bridge performs the operations.

## Requirements

- Python 3.9 or newer.
- Google Chrome or Microsoft Edge.
- Internet access to use DeepSeek Chat.
- A project directory for the agent to work on.

The documented launch instructions below use Windows commands.

## Installation

### 1. Download the project

Clone the repository:

```bash
git clone https://github.com/salez-ai/Deepseek-agent.git
```

Alternatively, download the repository as a ZIP file and extract it.

### 2. Install the browser extension

1. Open `chrome://extensions` in Chrome or `edge://extensions` in Edge.
2. Enable **Developer mode**.
3. Click **Load unpacked**.
4. Select the `extension` folder inside the downloaded project.

The extension should now appear in your browser.

### 3. Start the Python bridge

Open PowerShell or Command Prompt in the repository's root directory.

Run:

```bash
python bridge\bridge.py --root "C:\path\to\your\project"
```

Replace the example path with the directory you want DeepSeek Agent to access.

**Important:**

- Always launch the bridge using `python`.
- Do not launch `bridge.py` directly through its Windows file association. Doing so can open it in a separate window and drop the `--root` argument.
- `--root` is required.
- The bridge refuses drive roots, system folders, and your entire home folder.
- Check the startup banner to make sure the correct project directory is selected.

The bridge prints an authentication token. It is also saved in:

```text
~/.dsagent/token
```

You only need to copy the token into the extension once, unless it changes.

## Quick Start

Once the extension and bridge are installed:

1. Click the DeepSeek Agent extension icon.
2. Paste the token printed by the bridge.
3. Press **Save and test**.
4. Confirm that the extension displays **Connected** and the correct project path.
5. Open <https://chat.deepseek.com>.
6. Start a new chat.
7. Click **Start session** in the agent panel at the bottom-right corner.
8. Wait for DeepSeek to reply:

   ```text
   Ready.
   ```

Now give it a coding task.

For example:

> The tests in this project are failing. Investigate the cause, fix the problems, and run the tests again to verify your changes.

DeepSeek can now interact with your local project through the available tools.

---

## Agent Tools

DeepSeek Agent provides the following built-in tools:

| Tool | Purpose |
|---|---|
| `list_dir` | Explore directories and view the project structure. |
| `read_file` | Read files, optionally within a specified line range. |
| `search` | Search project files using regular expressions. |
| `write_file` | Create or overwrite files. |
| `replace_in_file` | Replace an exact section of a file. The search must match exactly one place. |
| `delete_file` | Delete a file. Directories cannot be deleted with this tool. |
| `run_command` | Execute shell commands with a configurable timeout. |

### Tool call format

DeepSeek writes tool calls inside fenced `tool` blocks.

The format is plain text, not JSON. Source code therefore does not need JSON escaping.

Example:

````text
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

For `write_file` and `replace_in_file`, the block must end with:

```text
=====END=====
```

A block without this marker is treated as incomplete and rejected. No file changes are made by that incomplete operation.

### How the agent loop works

The extension:

1. Reads DeepSeek's latest reply.
2. Extracts recognized tool calls.
3. Executes the calls through the local bridge.
4. Sends their results back to DeepSeek.
5. Waits for the next response.
6. Repeats until DeepSeek responds without a tool block.

The automatic loop pauses after **40 steps** and displays **Keep going**. Sending your own message resets the counter.

### Incomplete DeepSeek responses

Sometimes DeepSeek stops generating and displays its **Continue** button.

The extension cannot click Continue automatically because the website ignores clicks made by scripts.

Instead, it:

- Highlights the Continue button with an orange outline.
- Rings an alert.
- Sends a desktop notification when appropriate.
- Waits until you click Continue and the response grows and settles.

It does not execute an incomplete tool block.

---

## Continuing Previous Sessions

### Resume session

You can return to an earlier DeepSeek conversation and continue working on the same project without repeating previous operations.

For example, suppose DeepSeek built a game a few days ago. You now want to add a pause menu.

1. Open the original DeepSeek conversation.
2. Start the bridge with the same project directory.
3. Press **Resume session**, not **Start session**.

The extension handles the rest.

### What happens when you resume?

**1. Chat recognition**

The extension recognizes conversations that contain a previous agent session.

When a previous session is found, the panel displays the associated project directory and how long ago it was used.

**2. Old tool calls are not executed again**

Previous tool calls are treated as conversation history.

Their cards are marked **Earlier** instead of being executed again.

This prevents old operations, such as file overwrites, from accidentally undoing changes you made after the previous session.

**3. The agent receives updated project information**

The extension sends a message containing:

- Current agent instructions.
- Enabled skills.
- A list of files that currently exist.
- Instructions to reread relevant files before modifying them.

DeepSeek is also warned that information remembered from earlier may be outdated.

It then responds:

```text
Ready to continue.
```

**4. Wrong-project protection**

If the bridge now points to a different directory, the extension displays both the previous and current project paths and asks before continuing.

Press **Cancel** to stop.

### Session privacy

Remembered sessions are stored in your browser.

The extension saves the conversation address, project directory, and time locally. This information is not sent anywhere.

**Start session** also works in an old conversation, but treats it as a new session and does not automatically show the agent the current files.

---

## Alerts and Notifications

DeepSeek Agent can notify you whenever it needs your attention.

This is useful when the agent is working while you are using another application or browser tab.

### Supported events

| Event | Sound | Desktop notification | Repeats |
|---|---|---|---|
| Command or skill tool requires approval | Yes | When away | Up to 3 times |
| DeepSeek needs you to click Continue | Yes | When away | Up to 3 times |
| Agent pauses after 40 steps | Yes | When away | Up to 3 times |
| Agent encounters an error | Yes | When away | Up to 3 times |
| Agent finishes | Softer sound, when away | When away | No |

An **away** state means the DeepSeek tab is hidden or the browser window is not focused.

When you are actively viewing the page, the extension provides sound and visual highlighting without a desktop pop-up.

Clicking a notification returns you to the relevant tab.

The browser tab title also displays a bell while something requires attention. Notifications clear when you respond.

### Alert settings

Open the extension popup and find the **Alerts** section.

Available settings include:

- Sound on/off.
- Desktop notifications on/off.
- Individual switches for each event.
- Ringtones: Chime, Bell, and Digital.
- Volume.
- Reminder intervals: never, 30 seconds, 1 minute, and 2 minutes.
- Test buttons.

Disabling an event prevents its sound, notification, and tab-title mark. The visual status line and highlight remain available.

The bell icon in the agent panel header provides a quick way to mute sound.

Ringtones are generated directly in the browser. No additional audio files are required.

---

## Skills

Skills extend DeepSeek Agent with additional instructions, tools, or both.

A skill can provide coding conventions, custom workflows, API integrations, or wrappers around local programs.

### Installing a skill

1. Click the extension icon.
2. Press **Manage skills...**. The bridge must be running.
3. Drag a `.zip` skill package or a skill folder onto the management page.
4. Review the tools, their declared risk levels, and the exact commands they will execute.
5. Press **Install**.
6. Return to DeepSeek and press **Start session**.

For an already active session, press **Sync** to inform DeepSeek about newly installed or changed skills.

Skill tools appear as dropdown cards in the chat, similar to built-in tools.

### Included example skills

The repository includes six prepackaged example skills:

| Skill | Description |
|---|---|
| `project-notes` | Python tool, multiline body, and read/write tools. |
| `fetch-url` | Node.js tool that calls a web API. |
| `code-stats` | Demonstrates `input: "args"` mode and optional arguments. |
| `git-readonly` | A tool without custom code that wraps Git. |
| `python-style` | Instructions-only skill. |
| `canvas-draw` | Drawing toolkit that compiles a mini-language into SVG. |

Importable packages are available in:

```text
skills/examples/packaged/
```

The current repository includes the packaged `.dsskill.zip` files, but does not include the editable source directories for these example skills.

### Creating your own skills

Read:

```text
docs/SKILL_DEVELOPER_GUIDE.md
```

The helper script:

```text
bridge/skilltool.py
```

provides three operations:

| Operation | Purpose |
|---|---|
| `validate` | Validate a skill package. |
| `run` | Execute a skill tool. |
| `pack` | Package a skill for distribution. |

The JSON schema in `docs/skill.schema.json` provides editor assistance and validation for `skill.json`.

### Skill security

Skill tools are programs that run on your computer with your permissions.

- Only install skills from sources you trust.
- Tools that write files or run programs always require approval.
- Read-only tools also require approval unless the skill is explicitly marked **Trusted**.
- Updating a skill resets its Trusted status.

For more information, see sections 8 and 9 of the skill developer guide.

---

## Image Generation

DeepSeek cannot generate pictures directly, so DeepSeek Agent can connect to an external image generator website.

By default, it uses:

<https://perchance.org/5jsm6cis4r>

The feature adds a tool called:

```text
generate_image
```

### Example tool call

```text
name: generate_image
prompt: a cozy fox in a snowy forest, golden light
size: portrait
negative: blurry, text
seed: 777
filename: fox hero
```

Parameters:

| Parameter | Description |
|---|---|
| `prompt` | Description of the desired image. |
| `size` | `square`, `landscape`, `portrait`, or `large`. Default: `square`. |
| `negative` | Optional negative prompt. |
| `seed` | Optional generation seed. |
| `filename` | Optional filename used when saving the image. |

### How it works

1. DeepSeek requests an image using `generate_image`.
2. The extension asks you to approve the request. The prompt, size, negative prompt, and seed are displayed.
3. The extension communicates with the configured generator page.
4. The generated image is saved in your project.
5. The image and its details are returned to DeepSeek through the tool result.

Generated images are saved in:

```text
generated-images/
```

For example:

```text
generated-images/
    fox-12345.jpg
    index.jsonl
```

The actual extension is chosen according to the image format detected by the bridge. Supported saved formats are PNG, JPEG, GIF, and WebP.

The index records information such as the prompt, seed, and size.

The image appears in the tool card. **Open** displays it at full size, with an image download option.

DeepSeek cannot visually inspect the generated image. It is informed of this limitation and receives the saved file path.

### Generator connection methods

The generator requires a real browser.

| Method | Behavior |
|---|---|
| Hidden frame | Loads the generator in a hidden frame and communicates through `postMessage`. |
| Background tab | Opens the generator in a background tab, reads the result, and closes the tab. |
| Automatic | Tries the hidden frame first and falls back to a background tab when necessary. |

The default method is **Automatic**.

### Generator settings

Open the extension popup and find **Image generation**.

Settings include:

- Enable/disable image generation.
- Generator address.
- Connection method.
- Timeout.
- Test generator button.

The generator address must use HTTPS, or HTTP when connecting to your own computer.

If you enable or disable the feature during an active session, press **Sync**.

### Privacy and validation

Image prompts are sent to the generator address you configured. Only approve prompts you are comfortable sharing with that service.

Each image request requires approval.

The extension validates messages from the generator using its frame and origin, and accepts only supported image data URLs:

- PNG
- JPEG
- GIF
- WebP

The extension accepts image data URLs up to 14 MiB.

The bridge has separate limits:

- **12 MiB** for image previews.
- **10 MiB** for saving generated images.

The bridge selects the saved filename, validates the actual file type, and stores the image inside `generated-images/`.

This feature uses the following browser permissions:

- `offscreen`
- `scripting`
- Access to the configured generator origin, including Perchance by default.

### Generator page compatibility

If you edit or replace the generator page, the extension expects a compatible messaging interface.

For the hidden-frame method:

```js
// The generator announces readiness.
{ action: "ready" }

// It responds to a ping.
{ action: "ping" }

// Generation request.
{
  action: "generate",
  prompt,
  resolution,
  negativePrompt,
  seed
}

// Successful response.
{
  action: "result",
  dataUrl,
  seed,
  resolution
}

// Failed response.
{
  action: "error",
  message
}
```

Supported resolutions:

```text
512x512
768x512
512x768
768x768
```

For the background-tab method, the extension expects:

- `#resultImg` containing the resulting data URL in `src`, and not hidden.
- `#metaEl` containing the generated seed.
- `#statusEl` containing status text, where `failed` or `error` indicates failure.

It opens the generator using parameters similar to:

```text
?prompt=...&resolution=...&negative=...&seed=...&style=&autogen=1&embed=1
```

The empty `style` parameter is intentional: it prevents the generator page's default Style dropdown from automatically modifying the prompt.

When no negative prompt is supplied, the generator page's own default negative prompt is used.

### Testing a custom generator

The public repository snapshot does not include the offline Perchance mock page or mock server.

To test a custom generator, configure its address in the extension popup and use **Test generator**. The page must implement the messaging interface described above.

**Live compatibility note:** The real `perchance.org` service has not been verified in the documented test environment. Its behavior may differ.

---

## SVG Drawing

### The `canvas-draw` skill

The `canvas-draw` skill allows DeepSeek to create vector illustrations without an external image-generation API.

Instead of generating pixels, DeepSeek writes a short drawing script containing shapes and styling instructions.

The bridge validates the script and converts it into SVG.

Supported drawing elements include:

- Rectangles.
- Circles.
- Polygons.
- Paths.
- Text.
- Gradients.
- Groups.

### Getting started

1. Open the Skills management page.
2. Import the packaged skill:

   ```text
   skills/examples/packaged/canvas-draw-1.0.0.dsskill.zip
   ```

3. Press **Start session**, or **Sync** if a session is already running.
4. Ask DeepSeek:

   > Draw a flat-style logo of a fox in orange and navy, 512x512.

5. Approve the `draw` tool call.

The SVG appears inside the tool card.

### Editing drawings

You can request changes, such as:

> Make the sun bigger.

The skill allows DeepSeek to redraw individual shapes using their IDs rather than recreating the entire image.

The interface also supports:

- Full-size preview using **Open**.
- PNG export using **Download PNG**.
- Export at 1x to 4x scale.

### Quality and limitations

DeepSeek cannot see the rendered SVG result.

As a result, this approach works best for:

- Logos.
- Icons.
- Diagrams.
- Charts.
- Simple vector illustrations.
- Flat-style graphics.

Complex scenes and photorealistic images are outside the intended scope.

The tool warns about shapes that extend beyond the canvas or are invisible. The `draw_list` operation provides information about the position of each shape.

---

## Appearance

DeepSeek Agent can also improve the appearance of the DeepSeek website.

Each feature can be enabled or disabled in the extension popup under **Appearance**.

### Thinking block

The extension attempts to collapse DeepSeek's **Thought for...** section.

If it cannot locate the header, it hides the reasoning content and adds a **Show reasoning** button.

You can reopen it whenever you want. It will not automatically collapse again.

### Tool call cards

Tool calls are displayed as compact dropdown cards.

For example:

```text
replace_in_file   src/calc.py   [Done]
```

Opening a card reveals the original tool call and the result or error.

Supported status indicators include:

- Queued
- Running
- Done
- Failed

### Folded agent messages

Long instruction messages and `<<<RESULT ...>>>` replies sent by the extension can be collapsed into one-line cards.

### Cleaner formatting

The extension improves:

- Fonts.
- Code blocks.
- Tables.
- Headings.
- Spacing.
- Tool cards.
- Light and dark theme compatibility.

The agent panel also follows the DeepSeek page's theme.

Original page elements are hidden rather than removed, allowing the agent loop to continue reading them.

The font stack uses system fonts such as Inter, Segoe UI, SF, and Roboto because external web fonts may be blocked by the website's security policy.

---

## Security and Privacy

Security is important because the agent interacts with your local files and can execute commands.

### Local bridge protection

- The bridge listens on `127.0.0.1` only.
- Every request requires the authentication token.
- Requests carrying a web-page `Origin` are rejected.
- The bridge does not send CORS headers.
- Requests must remain within the configured project directory.

### Filesystem protection

All paths are resolved and checked against `--root`, including paths involving symbolic links.

The bridge also refuses to use drive roots, system folders, and the entire home directory as its project root.

### Backups

Before files are overwritten or deleted, the bridge copies them into:

```text
.dsagent_backups/<timestamp>/
```

The backup directory is located inside your project.

Add the following to your project's `.gitignore`:

```gitignore
.dsagent_backups/
```

Backups can be disabled with:

```bash
python bridge\bridge.py --root "C:\path\to\your\project" --no-backups
```

### Command approval

The `run_command` tool requires your approval through the agent panel.

Available options:

- **Allow** — approve the command.
- **Allow all this session** — approve subsequent commands during the current session.
- **Deny** — reject the command.

You can disable command execution completely with:

```bash
python bridge\bridge.py --root "C:\path\to\your\project" --no-commands
```

Commands execute with your operating-system user's permissions.

**Allow all is powerful:** it means commands written by the model can run without individual approval. Use it carefully.

### Prompt injection

DeepSeek reads project files and command output. These may contain malicious or misleading instructions designed to influence the model.

For that reason:

- Keep command approvals enabled when working with unfamiliar projects.
- Review commands before approving them.
- Avoid installing untrusted skills.
- Do not give the agent access to directories containing sensitive files.
- Use Git so you can inspect and revert changes.

The bridge's restrictions reduce certain risks, but they do not make arbitrary AI-generated code or commands safe.

### Image generation privacy

Image prompts are sent to the configured generator service after you approve the request.

The bridge stores generated images inside the project directory.

Your configured generator's own privacy policy and data handling practices also apply.

---

## Troubleshooting

| Problem | What to try |
|---|---|
| Extension cannot connect | Make sure the bridge is running and the token is correct. |
| Wrong project directory | Restart the bridge with the correct `--root`. |
| Agent does nothing | Press **Diagnose** in the agent panel. |
| Tool call is rejected | Check the tool syntax and required `=====END=====` marker. |
| DeepSeek stops mid-reply | Click **Continue** when the extension highlights the button. |
| Agent appears stuck | Press **Retry** to rerun the tool calls in DeepSeek's last reply. |
| Skill installation fails | Check the package and validate the skill using `bridge/skilltool.py`. |
| Image generation fails | Check the generator address, connection method, and **Test generator** result. |
| Thinking block does not collapse | Check the selectors in `extension/style.js`. |

### Diagnose

If nothing happens after sending a task, press **Diagnose** in the agent panel.

It reports information such as:

- Whether the message input was found.
- Whether assistant replies were found.
- How many tool blocks were detected in the latest reply.
- How many thinking blocks and tool cards were detected.

### Retry

If the agent appears stuck, **Retry** runs the recognized tool calls in DeepSeek's last reply again.

The panel log explains why a reply was skipped.

Use Retry carefully: it is an explicit retry operation, not a guarantee that repeating an operation is harmless.

### DeepSeek website selectors

The extension relies on the DeepSeek website's HTML structure.

Selectors are defined near the top of:

```text
extension/content.js
```

For example:

```js
const SEL = {
  input: 'textarea',
  reply: '.ds-markdown',
  ignoreInside: '...',
};
```

If DeepSeek changes its website markup, these selectors may need updating.

The reasoning-block selector is also used in:

```text
extension/style.js
```

Look for `thinkContent` and `THINK_HEADER` when troubleshooting the collapsed reasoning section.

Use the browser's Inspect tool to examine the live page and find the appropriate replacements.

---

## Limitations

- **Live website compatibility:** DeepSeek Agent depends on the structure of the consumer DeepSeek website. Changes to its markup may break selectors or automation.
- **Terms of service:** Automating the consumer chat page may conflict with DeepSeek's terms and may encounter rate limits. Review the applicable terms before use. The DeepSeek API is the supported route for heavy or production use.
- **Continue button:** The extension cannot automatically click DeepSeek's Continue button. You must click it when prompted.
- **Hidden-tab timers:** Chrome can throttle timers in tabs hidden for extended periods, particularly after more than five minutes. Notifications may therefore be delayed.
- **Sound restrictions:** Browsers require a user interaction before allowing audio. Pressing **Start session** provides that interaction.
- **Response completion:** A reply is considered finished after it stops changing for 2.5 seconds (`stableMs`). Long responses may need a higher value.
- **Markdown and code fences:** If a file contains a line consisting of exactly three backticks, DeepSeek must use four backticks to wrap the outer tool block.
- **Duplicate protection:** Duplicate tool-call detection works within a turn. The same call appearing in a later turn can execute again.
- **Single active chat:** Only one chat tab can be active for an agent session. Opening another chat ends the current session.
- **Model quality:** The agent depends on DeepSeek following the tool-call format. If it starts responding with plain text instead of tool blocks, remind it to use the expected format or start a new session.
- **Image generation:** The configured image generator is an external service. DeepSeek cannot visually inspect generated images.
- **SVG drawing:** DeepSeek draws without seeing the rendered result, so complex or photographic illustrations are not its intended use case.

---

## Disclaimer

DeepSeek Agent is an independent third-party project.

It is not affiliated with, sponsored by, or officially endorsed by DeepSeek.

DeepSeek is a trademark of its respective owner.

This project interacts with the DeepSeek consumer website through browser automation. Users are responsible for reviewing and complying with applicable terms and policies.

---

## License

DeepSeek Agent is licensed under the MIT License.

You are free to use, modify, distribute, and commercially use this software,
subject to the conditions of the license.

See [LICENSE](LICENSE) for the complete license text.

Third-party components, dependencies, and external services remain subject
to their respective licenses and terms.

---

**DeepSeek Agent — Give DeepSeek access to your project, not just your code snippets.**
