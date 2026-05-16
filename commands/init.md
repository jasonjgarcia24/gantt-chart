---
description: Setup or cleanup for gantt. Default runs first-run setup — verifies Python, short-form slash command, Claude Code permissions, skill-local venv, OAuth credentials + token, portfolio workbook bootstrap, and a final read-back (7 gates). Use `--remove` to clean up the short-form command alias and any leftover CLI symlink from older installs. Idempotent — safe to re-run either mode.
---

# gantt:init [--remove]

Two modes, dispatched by flag.

| Flag | Mode | What it does |
|------|------|--------------|
| (none) | **Setup** | Run the 7-gate first-run setup. |
| `--remove` | **Cleanup** | Remove the user-level shims setup created (plus any leftover from older versions). |

Both modes are idempotent — safe to re-run.

## Step 0 — Parse flag and dispatch

Inspect `$ARGUMENTS` (or the user's invocation text):

| Flag | Section to follow |
|------|-------------------|
| (none) | **Setup mode** below — skip the Cleanup mode section. |
| `--remove` | **Cleanup mode** below — skip the Setup mode section. |

If both flags appear, prefer `--remove` (cleanup wins).

---

# Setup mode (default)

First-run setup. **Does not modify any program tabs or task data.** Idempotent — re-running only fixes what's missing.

At the end of a successful run, the user has:
- Skill-local venv with all Python deps installed at `<PLUGIN_ROOT>/skills/gantt/.venv/`
- Short-form `/gantt` slash command available alongside `/gantt:gantt`
- Claude Code permissions merged so the skill can invoke its bundled CLI without per-call prompts
- Google OAuth credentials installed and a valid token for Sheets + Drive + Slides scopes
- Portfolio workbook (`Jason — Program Portfolio`) created with a seeded `_Config` tab
- A printed sheet URL the user can bookmark

There is **no** `gantt` on `$PATH` — the CLI is intentionally skill-local. Drive it through the `/gantt` slash command, not from a shell.

## Output format

Every setup run produces three kinds of output: an opening banner once, a status line per gate as each one finishes, and a final report. Keep it tight — no novels. Never dump raw Python tracebacks; translate errors into one-sentence plain English.

### Opening banner

Print once, at the very start:

```
/gantt:init — first-run setup
Checking 7 gates. One step (Google OAuth consent) may need your browser.
Legend: ✓ passed · → fixing · ⚠ needs you · ✗ blocking
```

### Per-gate status line

```
[K/7] <Gate name> — <symbol> <one-line outcome>
```

If a gate does real work (Gate 4 venv install, Gate 6 OAuth, Gate 7 workbook bootstrap), print a `→ fixing` line when starting and a final `✓` / `⚠` / `✗` line when done — so the user sees motion instead of a long pause.

### Remediation block (when a gate is ⚠ or ✗)

```
[K/7] <Gate name> — ⚠ needs you
  What's wrong:  <one sentence in plain English>
  Next step:
    1. <concrete action, ideally a runnable command>
    2. <next step>
  Blocking: yes — <which subsequent gates are paused>
```

For `✗` (hard fail), append: `Aborting init. Re-run /gantt:init after fixing.`

### Final report

Three shapes — success / partial / aborted.

**Success — all 7 ✓:**

```
═══════════════════════════════════════════════════════════
 /gantt:init — complete (YYYY-MM-DD)
═══════════════════════════════════════════════════════════

All 7 gates passed.

Skill CLI:  <PLUGIN_ROOT>/skills/gantt/scripts/gantt
Workbook:   <sheet URL>
Programs:   N (ask /gantt for `info` to see)

Try it:
  /gantt                                — short form (NL routing)
  /gantt:gantt                          — namespaced form
  /gantt create a new program called Q3Launch
  /gantt add a 5-day "Define OKRs" task to TPM90
  /gantt recalc TPM90
```

**Partial — at least one ⚠, no ✗:**

```
═══════════════════════════════════════════════════════════
 /gantt:init — incomplete (YYYY-MM-DD)
═══════════════════════════════════════════════════════════

Passed:     [1] [2] [4] [5]
Needs you:  [3] Claude Code permissions — jq merge not run
            [6] OAuth credentials — drop file in ~/Downloads and reply "done"

Re-run /gantt:init after resolving. It's idempotent — already-passed gates get confirmed quickly.
```

**Aborted — any ✗:**

```
═══════════════════════════════════════════════════════════
 /gantt:init — aborted at gate [N] (YYYY-MM-DD)
═══════════════════════════════════════════════════════════

Blocking:       [N] <gate name> — <one-line reason>
Passed so far:  [1] [2] ... [N-1]

Fix the blocking gate (see remediation above) and re-run /gantt:init.
```

## Finding plugin-root

Several gates need the plugin root (where the skill bundle lives). Resolve once at the start by scanning marketplace dirs for a plugin.json with `name: gantt`:

```bash
PLUGIN_ROOT=""
for d in ~/.claude/plugins/marketplaces/*/; do
  manifest="$d/.claude-plugin/plugin.json"
  if [ -f "$manifest" ]; then
    name=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('name',''))" "$manifest" 2>/dev/null || \
           grep -E '"name"\s*:\s*"' "$manifest" | head -1 | sed -E 's/.*"name"\s*:\s*"([^"]+)".*/\1/')
    if [ "$name" = "gantt" ]; then
      PLUGIN_ROOT="${d%/}"
      break
    fi
  fi
done

# Fallback to dev-clone locations if marketplace install isn't the source of truth
[ -z "$PLUGIN_ROOT" ] && PLUGIN_ROOT="$(ls -d ~/code/gantt-chart ~/Documents/gantt-chart 2>/dev/null | head -1)"

GANTT_BIN="$PLUGIN_ROOT/skills/gantt/scripts/gantt"
SKILL_DIR="$PLUGIN_ROOT/skills/gantt"
VENV_DIR="$SKILL_DIR/.venv"

if [ -z "$PLUGIN_ROOT" ] || [ ! -f "$GANTT_BIN" ]; then
  echo "Plugin install broken — gantt CLI not found at \$PLUGIN_ROOT/skills/gantt/scripts/gantt. Abort." >&2
  exit 1
fi
```

## Gate 1 — Python 3.10+

- Run `python3 --version`. Require 3.10+ (the CLI uses match statements and modern type hints in places).
- If the version is too old, print "Python 3.10+ required (you have X.Y.Z). Install via your OS package manager or pyenv, then re-run /gantt:init." Mark `✗` (blocking).

## Gate 2 — Short-form slash command alias

Enables bare `/gantt` invocation alongside `/gantt:gantt`. Both resolve to the same file, so no drift. (Note: this short-form points at the **NL-routing** command, not at this init command. Init is invoked as `/gantt:init` only — Claude Code has a built-in `/init` for CLAUDE.md initialization that the short form would collide with.)

- Target: `~/.claude/commands/gantt.md`. Source: `$PLUGIN_ROOT/commands/gantt.md`.
- If target exists AND is a symlink pointing at source: ✓ already set.
- If target is missing:
  ```bash
  mkdir -p ~/.claude/commands
  ln -sf "$PLUGIN_ROOT/commands/gantt.md" ~/.claude/commands/gantt.md
  ```
- If target exists but is a regular file OR a symlink pointing elsewhere: **stop and confirm before overwriting** — the user may have a custom `/gantt` command. Print the conflict (what's at the path, what it points to) and ask `Overwrite? (y/N)`. Default no.
- Verify: `test -L ~/.claude/commands/gantt.md && readlink ~/.claude/commands/gantt.md` returns the source path.
- Non-blocking: if the user declines, `/gantt:gantt` still works.
- Caveat: if the plugin is uninstalled, this symlink dangles. `/gantt:init --remove` cleans it up.

## Gate 3 — Claude Code permissions

- Parse `~/.claude/settings.json` with `jq '.permissions.allow // []'`.
- Required entries (read `$PLUGIN_ROOT/settings.fragment.json` for the authoritative list):
  - `Bash(*/skills/gantt/scripts/gantt:*)`
- If missing, print the exact jq-merge command and ask the user to run it themselves:
  ```bash
  cp ~/.claude/settings.json ~/.claude/settings.json.bak
  jq -s '
    (.[0].permissions.allow // []) as $a
    | (.[1].permissions.allow // []) as $b
    | .[0] * .[1]
    | .permissions.allow = ($a + $b | unique)
  ' ~/.claude/settings.json "$PLUGIN_ROOT/settings.fragment.json" \
    > /tmp/settings.json && mv /tmp/settings.json ~/.claude/settings.json
  ```
- **Never rewrite `settings.json` without an explicit "yes" from the user.** Modifying it silently inside a slash command is a trust violation.
- Missing perms don't block init (the user can approve the path-glob interactively). Note what's missing and continue.

## Gate 4 — Skill-local venv

The CLI uses a self-exec trampoline that re-execs under `$SKILL_DIR/.venv/bin/python3`. We auto-create that venv via the CLI's own `setup` subcommand — it installs into a skill-local `.venv` dir, not the user's system Python.

- Probe: `[ -x "$VENV_DIR/bin/python3" ]` AND `"$VENV_DIR/bin/python3" -c "import gspread, google_auth_oauthlib"`.
- If venv missing or imports fail: print `→ fixing (creating venv at $VENV_DIR and installing deps)` then run:
  ```bash
  "$GANTT_BIN" setup
  ```
- Re-probe to verify.
- Blocking: `✗` if the venv create fails (e.g., `python3-venv` package missing on Debian/Ubuntu — print `sudo apt install python3-venv`).

## Gate 5 — OAuth credentials file

Target: `$GANTT_CREDS` if set, else `~/.config/gantt/credentials.json`.

1. If the target exists: `jq . "$target"` to verify it parses as JSON. ✓.
2. If missing:
   - `mkdir -p ~/.config/gantt`.
   - Look for a downloaded file: `ls ~/Downloads/credentials.json ~/Downloads/client_secret_*.json 2>/dev/null`. If any match, ask the user: *"Found credentials.json in ~/Downloads. Move it to `~/.config/gantt/credentials.json`?"* Move on "yes."
   - The CLI also seeds from `~/Documents/ai-project-model/credentials.json` if present. Probe and offer if found.
   - If no candidate, print the Google Cloud Console walkthrough:
     1. Go to [console.cloud.google.com](https://console.cloud.google.com) → create a new project (e.g. `gantt`) or reuse an existing one.
     2. APIs & Services → Library → enable **Google Sheets API**, **Google Drive API**, AND **Google Slides API**.
     3. APIs & Services → OAuth consent screen → External → add your own email under *Test users* → add scopes `https://www.googleapis.com/auth/spreadsheets`, `https://www.googleapis.com/auth/drive.file`, and `https://www.googleapis.com/auth/presentations`.
     4. APIs & Services → Credentials → Create Credentials → OAuth client ID → **Desktop app** → download the JSON.
     5. Save as `~/.config/gantt/credentials.json` (or drop it in `~/Downloads` and reply "done").
   - Wait for the user to confirm placement. Re-check the target path.
3. Validate: the file must be a JSON object with a top-level key `installed` (Desktop OAuth client shape). If it has `web` instead, the user created the wrong client type — walk them back to step 4.
4. Blocking: `✗` if missing — Gates 6 and 7 cannot proceed.

## Gate 6 — Portfolio workbook bootstrap

- Probe: read `~/.config/gantt/config.json` and check for `sheet_id`.
- If config exists with `sheet_id`: ✓ already bootstrapped. Print the URL.
- If missing: print `→ fixing (running gantt bootstrap — opens browser for OAuth consent and creates the portfolio workbook)`.
- Confirm with the user FIRST: *"About to open a browser for Google OAuth consent and create a new Google Sheet titled 'Jason — Program Portfolio'. Proceed? (Y/n)"* — workbook creation is a real side effect on the user's Drive.
- On "yes":
  ```bash
  "$GANTT_BIN" bootstrap
  ```
  This blocks until the user completes OAuth in their browser. Capture the printed sheet URL.
- On error: if `invalid_grant` or token issues, delete `~/.config/gantt/token.json` and re-run. Otherwise translate the error to one-sentence English.
- Blocking: `✗` if bootstrap fails (Gate 7 verification can't run without a workbook).

## Gate 7 — Final verification

- Run `"$GANTT_BIN" info`. Confirm it prints a sheet URL and the venv path resolves under `$SKILL_DIR/.venv`.
- On success: print `[7/7] Final verification — ✓ sheet URL + venv resolved`, then the **Final report** per `## Output format` above. Pick the success / partial / aborted shape based on how gates 1-6 turned out.

## Setup mode safety rules

- **Never** auto-mutate `~/.claude/settings.json` — print remediation instead.
- **Never** auto-run `pip install` against the user's system Python — but `gantt setup` creating a skill-local `.venv` is OK; it's an isolated install the user opted into by running /gantt:init.
- **Never** download or commit OAuth credentials to the repo.
- **Never** auto-run `gantt bootstrap` without an explicit "yes" — it creates a real Sheet in the user's Drive.
- Each gate is independent — a non-fatal failure (Gate 2 alias, Gate 3 perms) shouldn't stop subsequent gates.

---

# Cleanup mode (`--remove`)

Removes the user-level shims this skill (or a prior version of it) may have created — the short-form `/gantt` command alias and any leftover `~/.local/bin/gantt` symlink from before the self-contained-skill refactor. **Does not touch any task data, the workbook, OAuth credentials, settings.json permissions, or the skill-local venv.** Sensitive state stays put with printed manual-cleanup guidance.

At the end of a successful run, the user has:

- `~/.local/bin/gantt` symlink removed if present (legacy from pre-refactor installs — no longer created by setup)
- `~/.claude/commands/gantt.md` symlink removed (only if it points at gantt-chart) — short-form `/gantt` invocation goes away; `/gantt:gantt` (namespaced) still works until `/plugin uninstall`
- A printed checklist for what to do about the workbook, credentials, token, settings permissions, and the skill venv — left alone by default

This command does NOT actually invoke `/plugin uninstall` itself — that's a Claude Code slash command, not a Bash command. Cleanup mode only handles file-system shim removal; full plugin teardown requires the two slash commands at the end.

## Cleanup implementation

Run the following Bash sequence:

```bash
removed=0
foreign=0
not_symlink=0
absent=0

remove_symlink() {
  local dst="$1"
  local label="$2"
  if [ -L "$dst" ]; then
    local target=$(readlink "$dst")
    if echo "$target" | grep -qE '(gantt-chart|jason-gantt)'; then
      rm "$dst"
      echo "  ✓ removed: $label"
      removed=$((removed+1))
    else
      echo "  ⚠ kept: $label — symlink points elsewhere ($target); not touching"
      foreign=$((foreign+1))
    fi
  elif [ -e "$dst" ]; then
    echo "  ⚠ kept: $label — exists but is not a symlink (real file); not touching"
    not_symlink=$((not_symlink+1))
  else
    absent=$((absent+1))
  fi
}

echo "Symlinks:"
remove_symlink "$HOME/.local/bin/gantt" "~/.local/bin/gantt (legacy)"
remove_symlink "$HOME/.claude/commands/gantt.md" "~/.claude/commands/gantt.md"

echo ""
echo "gantt:init --remove complete — $removed removed, $absent already absent, $foreign kept (foreign), $not_symlink kept (not a symlink)."
echo ""
echo "Items NOT touched (manage them yourself if you want full cleanup):"
echo ""
echo "  ~/.config/gantt/credentials.json   ← OAuth client (sensitive — keeping it means no new Cloud Console setup if you reinstall)"
echo "  ~/.config/gantt/token.json         ← OAuth refresh token (sensitive — re-bootstrap will re-prompt for consent)"
echo "  ~/.config/gantt/config.json        ← Sheet ID + URL — deleting forces re-bootstrap (creates a NEW workbook; old one stays in your Drive)"
echo "  ~/.claude/settings.json            ← Has gantt permission entries from setup Gate 3"
echo "  Your portfolio workbook            ← 'Jason — Program Portfolio' stays in your Google Drive — delete from Drive UI if unwanted"
echo "  <PLUGIN_ROOT>/skills/gantt/.venv   ← Skill-local venv — gets removed when /plugin uninstall runs"
echo ""
echo "If you want full cleanup, manually:"
echo "  rm -rf ~/.config/gantt"
echo "  Edit ~/.claude/settings.json to remove the gantt permissions block (back up first: cp ~/.claude/settings.json ~/.claude/settings.json.bak)"
echo "  Delete the workbook from Google Drive if unwanted"
echo ""
echo "To uninstall the plugin itself:"
echo ""
echo "  /plugin uninstall gantt@jason-gantt"
echo "  /plugin marketplace remove jason-gantt"
echo ""
echo "(Skipping those keeps the plugin installed — symlinks can be recreated with /gantt:init.)"
```

Report the command output verbatim.

## Cleanup safety rules

- **Never** auto-edit `~/.claude/settings.json` — it's the user's config; print remediation instead.
- **Never** auto-delete OAuth credentials or tokens (`~/.config/gantt/credentials.json`, `~/.config/gantt/token.json`) — print warnings + manual `rm` commands.
- **Never** auto-delete `~/.config/gantt/config.json` — it points at the user's actual workbook in Drive; deleting it forces a re-bootstrap that creates a NEW sheet.
- **Never** delete the workbook itself from Drive — that's user data.
- Only remove symlinks that point at gantt-chart or jason-gantt paths — leave foreign symlinks alone.
- **Never** auto-invoke `/plugin uninstall` — print the command and let the user decide.
