# Driving a computer from an AI agent

HID-Fi is a keyboard and mouse the computer cannot tell from a real one. That
makes it a way for an agent to work any computer - a locked one, one with no
API, one you are not allowed to install software on - as long as the agent
speaks in as few words as possible. The board does the rest.

## The idea in one table

| The agent says | The board does on macOS | on Windows | on Linux (GNOME) |
|---|---|---|---|
| `do copy` | Cmd+C | Ctrl+C | Ctrl+C |
| `do lock` | Ctrl+Cmd+Q | Win+L | Super+L |
| `do screenshot_area` | Shift+Cmd+4 | Win+Shift+S | Print |
| `open Calculator` | Spotlight, types it, Enter | Start, types it, Enter | Activities, types it, Enter |
| `run calc` | *refused - no run box* | Win+R, types it, Enter | Alt+F2, types it, Enter |

The board detects which OS it is plugged into, so the agent never has to know,
look up, or get wrong a key combination. It sends one word.

## Why this costs few tokens

| | Without | With HID-Fi actions |
|---|---|---|
| What the agent must know | every combo, per OS, per version | ~80 names, one list |
| Learning them | in the prompt, every turn | `actions` once: about 730 characters, only what works here |
| Doing five things | five round trips | one `steps` call |
| What comes back | full JSON | `ok copy = GUI+C (mac)` |

A typed-decision model (Laya, Jev) fits it exactly: the action list *is* the
list of choices. Ask the model which one matches what the person said, then send
it.

## Three ways to connect

| Use | How | Good for |
|---|---|---|
| MCP | `python3 tools/hidfi.py mcp` as an MCP server | Claude, and any agent that speaks MCP |
| Command line | `python3 tools/hidfi.py do copy` | shell scripts, any agent that can run a command |
| HTTP | `POST http://<board>/api/command` with JSON | anything else |

### MCP

Add it to the agent's MCP configuration. For Claude Code:

```bash
claude mcp add hidfi --env HIDFI_HOST=192.168.4.1 -- python3 /path/to/HID-Fi/tools/hidfi.py mcp
```

If the board has an access PIN, set `HIDFI_PIN` in that server's environment -
never in a prompt or on a command line. The agent then sees five tools:

| Tool | Takes | Example |
|---|---|---|
| `hidfi_do` | `a`: one of the board's actions (the list is the tool's choices) | `{"a": "new_tab"}` |
| `hidfi_open` | `app`: the name the OS's search shows | `{"app": "Terminal"}` |
| `hidfi_type` | `text` | `{"text": "Hello"}` |
| `hidfi_steps` | `s`: up to 32 steps | `{"s": ["do address_bar", "type example.com", "key ENTER"]}` |
| `hidfi_key` | `keys`: a raw combo, when no action fits | `{"keys": "CTRL+SHIFT+ESC"}` |

The whole tool list is under 3.5 KB, and it is paid for on every turn, so it is
kept that small on purpose (`tests/unit/test_hidfi_tool.py` checks it).

### Command line

| Command | Does |
|---|---|
| `hidfi.py actions` | lists what works on this computer |
| `hidfi.py do NAME` | one action |
| `hidfi.py open NAME` | opens an app |
| `hidfi.py run LINE` | runs a command or URL (Windows, Linux) |
| `hidfi.py type TEXT` | types |
| `hidfi.py key COMBO` | presses a combo |
| `hidfi.py steps "STEP" "STEP" ...` | several in one call |
| `hidfi.py status` | the board's status |

Where the board is: `HIDFI_HOST` (default `192.168.4.1`, its own WiFi) or
`HIDFI_PORT` for the USB COM cable. Exit codes: 0 done, 1 refused, 2 usage,
3 board unreachable.

### Steps

| Step | Does |
|---|---|
| `do NAME` | an action |
| `open APP` | opens an app |
| `run LINE` | a command or URL (not macOS) |
| `type TEXT` | types the rest of the line, any character |
| `key COMBO` | presses a combo |
| `wait MS` | up to 5000 per step, 10 s per call |
| `click left` / `right` / `middle` | clicks |

A call stops at the first step it cannot do and says which (`at`), so an agent
knows exactly where it got to.

## What it cannot do - read this

| Limit | Why | What to do |
|---|---|---|
| It cannot see the screen | it is a keyboard and mouse, nothing more | pair it with a screenshot from elsewhere, or trust the step results |
| `open` depends on the launcher finding the name | it types into the OS's search | use the name the OS shows: "System Settings", "Text Editor" |
| `run` is not on macOS | macOS has no run box | `open` the app instead |
| Linux means GNOME | other desktops use other keys | use `key` with that desktop's combo |
| Text is US layout | see [COMMANDS.md](COMMANDS.md#text-that-is-not-plain-english) | set the computer to US, or turn on Unicode typing |
| A request is at most about 900 bytes | the board's parser | split long text over several calls |

## Safety

These commands type into a real computer. Everything that applies to the
dashboard applies here: over WiFi they need the access PIN once one is set, and
over your home network they are refused until one is. Give an agent the PIN
through the environment only. Anything it can type, it can type into the wrong
window - give it `do` and `steps` for what it should do, and watch the first
runs.
