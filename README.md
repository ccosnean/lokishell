<img src="assets/lokishell-logo.png" alt="LokiShell logo" width="160" height="160">

# LokiShell

**Let your shell shapeshift personas with every `cd`.**

Automatic CLI account and environment switching by directory for **zsh and bash**. Use personal and work **GitHub, Firebase, Google Cloud, Claude Code, and Codex** accounts across projects, Git worktrees, and parallel coding agents.

Choose a global persona, override it by directory, and connect each account once. Ordinary `gh`, `firebase`, `gcloud`, `claude`, and `codex` commands use the selected persona.

## Install

```sh
brew install ccosnean/tap/lokishell
loki use personal --global
```

Open a new terminal once to load the shell hooks. Homebrew installs LokiShell and its dependencies; install the service CLIs you use separately.

## Switch personal and work accounts by directory

```sh
cd ~/projects/side-project
loki use                                # assign the global persona: personal
loki add github --user personal-user
loki add firebase

cd ~/work/acme-api
loki use work                           # create and assign work
loki add github --user work-user
loki add firebase

firebase projects:list                  # work account
gh repo list                            # work account

cd ~/projects/side-project
firebase projects:list                  # personal account
gh repo list                            # personal account
```

Personas share saved logins across their assigned folders. New personas start empty; only enabled integrations affect the environment. The GitHub integration also selects Git authentication and commit author identity.

`loki use NAME` creates missing personas. `loki use` assigns the global persona to the current folder. `loki use NAME --global` changes the default while preserving folder and worktree overrides.

## Git worktrees and parallel coding agents

Assign a persona at the repository root. New and existing worktrees inherit it automatically, including worktrees created by agents.

```sh
cd ~/work/acme-api
loki use work
loki add codex                          # connect once for the work persona

git worktree add ../acme-api-fix -b fix/login
cd ../acme-api-fix
loki status                             # work, inherited from the repository
codex                                   # work account
```

Run another agent under your personal account in a second terminal:

```sh
cd ~/projects/side-project
loki add codex                          # connect once for the personal persona
codex                                   # personal account; work agent continues
```

Override just one worktree with `loki use personal` from its root. Outside assigned directory trees and repositories, the global persona applies.

## Manage integrations

Bundled integrations: **GitHub/Git, Firebase CLI, gcloud, Google ADC, Claude Code, and Codex**.

| Command | Purpose |
| --- | --- |
| `loki tools list` | Installed integrations for this persona |
| `loki tools catalog` | Browse the full catalog |
| `loki add TOOL` | Enable an integration and sign in |
| `loki tools install TOOL` | Enable without signing in |
| `loki tools remove TOOL` | Disable; keep saved credentials |
| `loki tools show TOOL` | Inspect configuration and login details |
| `loki --profile work add gcloud` | Connect another persona without changing this folder |

Local integrations show their installed directory in a `Source` column. `list` and `catalog` accept `--all` to include shared dependencies, or `--json` for structured output.

For direct MCP servers already configured in a persona's client, use `loki add mcp codex SERVER` or `loki add mcp claude SERVER`.

## Agent and shell behavior

`loki status` returns JSON with the effective persona and its source. `loki tools list --json` reports its installed integrations.

Fresh zsh shells load the persona even in noninteractive agent commands. Bash commands inherit an initialized shell's environment; runners that skip startup files can load the hook explicitly:

```sh
eval "$(loki hook bash)"                # use zsh for a zsh runner
```

Launch agents from an initialized terminal. Existing desktop app accounts and hosted connections follow their app login.

## Clean up or reset

| Command | Effect |
| --- | --- |
| `loki clean` | Unlink this folder; fall back to the global persona. Keep shared logins. |
| `loki clean --global` | Clear this persona's managed logins. Keep settings and agent history. |
| `loki reset` | Erase all personas, managed credentials, history, custom integrations, assignments, and hooks in the current Loki store. Keep the executable. |

Reset requires two interactive confirmations. With shell integration loaded, it unloads the current terminal's Loki hooks immediately; other terminals unload at their next prompt or directory change. Stop running agents first. Preserved `.envrc` files may need `direnv allow` again. Local cleanup does not revoke browser sessions or provider-side grants.

## How it works

The shell hook refreshes the selected persona on startup, directory changes, and prompts. It loads project settings through direnv, then exports the enabled tools' configuration paths. Native commands inherit those paths. Switching personas restores the previous values of variables no longer managed by Loki.

For example, starting with a new `work` persona:

```sh
cd ~/work/acme-api
loki use work
loki add gcloud
gcloud config set project acme-dev
```

Loki's store defaults to `~/.config/lokishell/`; `LOKI_HOME` can override it. After this example, its main files are:

```text
~/.config/lokishell/
  settings.json                 # global persona, folder/worktree assignments, shell setup
  profiles/work/
    profile.json                # enabled integrations and their settings
    env.sh                      # generates this persona's environment exports
    gcloud/                     # Google CLI credentials, configuration, and logs
      configurations/config_default

~/work/acme-api/.envrc          # marked pointer to the shared work persona
```

| Command | What changes |
| --- | --- |
| `loki use work` | Creates the persona, records the folder assignment, and adds a marked block to the project's `.envrc`, preserving existing content. |
| `loki add gcloud` | Enables the integration in `profile.json` and runs Google's login with `CLOUDSDK_CONFIG` pointing to `profiles/work/gcloud/`. Login saves credentials and account settings there. |
| `gcloud config set project acme-dev` | Writes the project setting into that persona's active gcloud configuration. Other folders using `work` share it. |
| `cd ~/projects/side-project` | Refreshes the shell environment for that folder's persona; it does not copy credentials between folders. |

`loki use` also installs marked startup blocks in `.zshenv`/`.zshrc` or `.bashrc`/`.bash_profile`; `--no-shell` skips this. Existing shell settings and direnv hooks remain. Other integrations use their own paths under the persona directory, such as `codex/` and `claude/`. Only enabled integrations and their dependencies contribute exports. Direnv still controls project environment approval.

Personas share your OS user's permissions. Credentials use native stores or private local files; LokiShell does not encrypt those files. See each [integration's documentation](tools/) for supported login modes and cleanup limits.

## Add an integration

```sh
loki tools install --from ./my-tool
```

A tool directory contains `tool.toml` and, when needed, `adapter.py`. See [CONTRIBUTING.md](CONTRIBUTING.md) for the schema and tests.
