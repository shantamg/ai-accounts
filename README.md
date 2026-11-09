# ai-accounts

Use a different Claude Code / Codex CLI account in each project, automatically.

You might sign in to Claude and Codex with a personal account for side projects
and a work account for your job. `ai-accounts` keeps each account's login in its
own config directory and remembers which account each project uses. Run `c`
(Claude) or `codex` in a project, and the right account starts.

- **One account pair per company.** A *profile* (Personal, Work, …) pairs a
  Claude Code login with a Codex login.
- **Remembered per project.** The first launch in a project asks which account
  to use and saves the choice in a private `.ai-account.local` file at the Git
  root. The file is added to the repo's local Git exclude, so it is never
  committed. Subdirectories and linked worktrees inherit it.
- **Guided setup.** The picker can sign in an existing profile or add a new one.
  You don't need to remember any commands.
- **No credential leaks between accounts.** Inherited overrides such as
  `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` are cleared for each launch.

Requires macOS or Linux, Python 3.9+, and `claude` and/or `codex` on your `PATH`.
It uses only the Python standard library.

## Install

```sh
git clone https://github.com/shantamg/ai-accounts.git ~/ai-accounts
~/ai-accounts/install.sh --aliases
```

`install.sh` symlinks `~/bin/ai-accounts` to the script in this checkout
(set `BIN_DIR` to use another directory). `--aliases` adds these to your
`~/.zshrc` (or `~/.bashrc`) unless aliases with those names already exist:

```sh
alias c='ai-accounts run claude --'
alias codex='ai-accounts run codex --'
```

Anything after `--` is passed through to the CLI. For example,
`alias c='ai-accounts run claude -- --dangerously-skip-permissions'`.

### Update

```sh
~/ai-accounts/install.sh update
```

This runs `git pull --ff-only` and re-links. Because the command is a symlink, a
plain `git pull` in the checkout updates it too.

## Usage

### First launch in a project

```text
$ c
Choose an account for /Users/me/code/my-app
  1. Personal
  2. Work  (not signed in to claude; choose to sign in)
  3. + Add a new account
Account (number or name, Enter to cancel):
```

- Choose a signed-in account to launch right away.
- Choose an account that isn't signed in to start its browser sign-in. You can
  then sign in to the other CLI too, and the launch continues.
- Choose **+ Add a new account** to name a new profile. It gets its own config
  directories under `~/.config/ai-accounts/accounts/<name>/`, then goes through
  the same sign-in.

After that, `c` and `codex` in that project (and any subdirectory) use the
saved account without asking.

### Commands

| Command | What it does |
| --- | --- |
| `ai-accounts add NAME` | Create a profile and sign in to Claude and Codex. |
| `ai-accounts status` | Show which CLIs each profile is signed in to. |
| `ai-accounts login NAME claude\|codex` | Sign in (again) to one CLI for a profile. |
| `ai-accounts show` | Show the account used in the current directory. |
| `ai-accounts bind NAME [--replace]` | Set or change the account for the current directory. |
| `ai-accounts profile NAME [--claude-dir DIR] [--codex-dir DIR]` | Create a profile with explicit directories. |
| `ai-accounts run claude\|codex -- ARGS` | Launch a CLI with the bound account. |

### Keep your existing login as a profile

To keep using your current default logins (`~/.claude` and `~/.codex`) as one
profile, point it at `default`:

```sh
ai-accounts profile Personal --claude-dir default --codex-dir default
```

### Switch a project to another account

```sh
cd ~/code/my-app
ai-accounts bind Work --replace
```

Claude Code and Codex keep session history per config directory, so sessions
from the old account won't appear under the new one.

## How it works

- Profiles are private JSON files (mode 600) in `~/.config/ai-accounts/profiles/`.
  Each one maps to a `CLAUDE_CONFIG_DIR` and a `CODEX_HOME`. A `null` value
  means the CLI's default directory.
- Claude Code scopes its macOS Keychain entry by config directory, so each
  profile has its own login. For non-default Codex homes, file-backed
  credentials are used inside that home.
- Bindings are looked up from the working directory upward and stop at the Git
  root or your home directory. A linked worktree falls back to its primary
  checkout's binding.
- Profile and binding files that are not owner-only, or that point to a
  missing profile, are rejected (fail closed). No account is guessed.
- Without an interactive terminal (scripts, CI), a launch needs an existing
  binding and never prompts.

## Development

```sh
python3 -m unittest discover -s tests
```

## License

Apache 2.0. See [LICENSE](LICENSE).
