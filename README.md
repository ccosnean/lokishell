# LokiShell

Use personal and work personas side by side. Choose a global default, override it by folder, and carry repository personas into Git worktrees. Each persona enables its own tool integrations. Ordinary commands use the selected environment for those tools.

LokiShell is a command-line account and environment manager for **zsh and bash**. The executable is `loki`.

## Install

```sh
brew install ccosnean/tap/lokishell
```

The Homebrew formula installs LokiShell, Python, Click, and direnv. Click is installed in LokiShell's private Python environment. Install the service CLIs you use separately.

## Start

```sh
loki use personal --global
```

Creates `personal` if needed and makes it the global persona. Ordinary commands outside a configured folder use the enabled tools' personal credentials. The first setup installs shell hooks: **open a new terminal once** to activate newly installed hooks. A command cannot change its parent shell's environment by itself.

To create a persona without selecting it, run `loki create work`. Creation does not change the global default or any folder assignment.

New personas start with no integrations enabled. Choose from `loki tools list`; connect only the accounts you use, once per persona:

```sh
loki add firebase
loki add gcloud
loki add adc
loki add github --user your-personal-github-user
loki add claude
loki add codex
```

Set up a project with your global persona:

```sh
cd ~/projects/my-project
loki use
```

Use another persona in a folder:

```sh
cd ~/work/project
loki use work
loki add firebase          # enable and sign in once for work
firebase projects:list     # work
cd /tmp
firebase projects:list     # personal
```

`loki use NAME` selects a persona for the current folder, creating it if needed. `loki use` without a name assigns the global persona to the current folder, even if the folder previously used another persona. `loki use NAME --global` changes the global default without overriding assigned folders or worktrees. If no global persona exists yet, `loki use` explains how to set one.

`loki status` shows the effective persona and whether it came from the folder, repository/worktree, or global default. To manage another persona without shifting or changing the folder:

```sh
loki --profile work add firebase
```

Logins belong to the shared persona, not the folder. Concurrent shells can use different personas.

## Tool catalog

```sh
loki tools list                      # available integrations and enabled state
loki tools show firebase             # variables, dependencies, and login definition
loki tools install firebase codex    # enable integrations without logging in
loki add firebase                    # enable if needed, then sign in
loki tools remove codex              # disable; keep saved credentials
loki tools validate                  # validate enabled tools and their dependencies
```

Install enables the Loki integration, not the upstream CLI binary. Missing executables are reported with installation instructions. Every persona has its own selection; use `loki --profile work tools install firebase` to manage another one.

Only enabled integrations and their dependencies contribute environment variables. Unselected tools use their normal, unmanaged configuration. When changing personas or disabling a tool, Loki restores variables it previously owned rather than carrying that persona's tokens into the next environment.

Firebase, gcloud, and ADC share a Google credential-path dependency; `loki tools list --all` includes it. They do not change Claude or Codex settings unless those integrations are also enabled. GitHub's integration additionally owns GitHub Git authentication and author identity.

Definitions are separate directories under [`tools/`](tools/). Simple integrations need only `tool.toml`; unusual logins can add an `adapter.py`. Users can install their own definitions:

```sh
loki tools install --from /path/to/my-tool
loki tools configure my-tool --set region=eu
```

Adding a contribution does not require editing core code or a central tool list. See [CONTRIBUTING.md](CONTRIBUTING.md) for the schema and automatically discovered integration tests.

`loki tools list` includes the contribution link. Validation skips unenabled definitions; contributors can explicitly run `loki tools validate --all` for the entire catalog, or `loki tools validate ./my-tool` for one local definition.

### Upgrading from 0.3

`init`, `shift`, and `exec` were removed. Replace `loki init` with `loki use`, and `loki shift NAME` with `loki use NAME --global`. Existing persona data and folder/worktree assignments remain compatible. Commands now use Click for help, parsing, errors, and reset prompts.

### Upgrading from 0.2

Existing personas retain their previous integrations, settings, and credential paths. New personas start empty. `loki tools remove TOOL` can narrow an existing persona's selection. Open a fresh terminal after upgrading so the new environment tracking starts without stale exports from the old version.

## Worktrees

A persona assigned at a Git repository's main root becomes that repository's default. New and existing linked worktrees inherit it automatically, including worktrees created by coding agents. The association uses Git's common directory and is stored in LokiShell's global settings; no tracked files or Git configuration are required.

```sh
cd ~/work/project
loki use work
git worktree add ../feature-branch -b feature-branch
cd ../feature-branch
loki status               # work, inherited from the repository
loki use personal         # override only this worktree
```

A worktree's own assignment wins over the repository default. A repository assignment wins over a surrounding folder's persona. The global persona applies where there is no folder or repository assignment. If a repository has no default yet, its first initialized worktree establishes one.

## Clean

```sh
loki clean
```

Removes only this folder's LokiShell block from `.envrc`, preserving other project settings. Shared logins remain available in other linked folders. This folder then follows the global persona; this is not a ban on access from that folder. Cleaning a worktree leaves the other worktrees and repository default unchanged.

```sh
loki clean --global
```

Clears the selected persona's locally managed logins for every linked folder, including saved credentials from integrations that were previously disabled. Folder pointers, identity settings, and agent history remain. The other profiles remain separate. This does **not** revoke provider-side OAuth grants, browser sessions, hosted app connections, custom MCP token stores, or tokens already held by running processes. Restart running agents after a global reset. Failed Claude Keychain/MCP logouts are reported as an error rather than hidden.

## Reset everything

```sh
loki reset
```

Returns LokiShell to a fresh-install state for **all personas**. It displays what will be lost, asks for confirmation with a default of No, and then requires the exact phrase `RESET LOKISHELL`. Both checks require an interactive terminal; there is no force/yes bypass.

Reset deletes every persona, managed credential, agent session/history file, custom tool definition, assignment, and setup backup inside `LOKI_HOME`. It runs managed logout actions, removes LokiShell blocks from registered project and shell startup files, and removes their direnv approvals. If a logout fails, reset reports the failure and retains setup so it can be retried; some logins may already have been cleared.

Project files, unrelated shell settings, installed binaries, browser sessions, and provider-side grants remain. Stop running agents first and open a fresh terminal afterward. Preserved project `.envrc` files may need `direnv allow` again. `reset` resets the current LokiShell store, including every persona in it; independently configured stores under other `LOKI_HOME` locations are separate.

## Agents and shells

LokiShell installs a hook in `.zshenv` as well as `.zshrc`. Fresh zsh command shells launched by agents load the effective persona, including noninteractive shells and worktrees without an `.envrc`. Prompts and directory changes refresh selection, so other open terminals pick up a global shift at their next prompt. Bash integration is installed in `.bash_profile` and `.bashrc`; arbitrary `bash -c` processes must inherit an initialized environment or load the hook.

An agent launched from an initialized terminal inherits the selected persona. An already-running desktop app's own account and hosted connections do not switch when its child shell changes directories. Runners that bypass startup files must explicitly load the shell hook before running tool commands.

Shell setup can also be installed independently with `loki setup`, or supplied explicitly:

```sh
eval "$(loki hook zsh)"
# or
eval "$(loki hook bash)"
```

`loki use personal --no-shell` creates the folder pointer without editing startup files. Startup edits preserve other content and make backups. LokiShell persona selection also applies beneath folders with their own `.envrc`. Direnv still governs the project's other environment variables and its normal trust/allow mechanism.

## What is stored where

```text
~/.config/lokishell/
  settings.json             # global persona, folder pointers, repository/worktree associations
  profiles/
    personal/
      profile.json          # enabled tools and tool-specific settings; no tokens
      env.sh                # environment loader
      auth/github-token     # selected GitHub token
      xdg/configstore/      # Firebase CLI login
      gcloud/               # gcloud and application default credentials
      claude/               # Claude Code configuration and sessions
      codex/                # Codex configuration, credentials, sessions, MCP tokens
    work/
  tools/                    # user-installed definitions
  backups/                  # shell startup backups

project/.envrc              # direnv pointer to one shared persona
```

`LOKI_HOME` can override the settings root. Its location is independent of `XDG_CONFIG_HOME`, so nested profile selection does not create nested stores. Profile directories are private to the current OS user (`0700`); files LokiShell creates are `0600`.

GitHub and Codex tokens use local files, not encryption supplied by LokiShell. Claude subscription credentials use Claude Code's directory-scoped Keychain storage on macOS. Only enabled tools receive these settings. This separates account selection under one OS user; it is not an OS security boundary. Never commit profile stores or copy their contents into logs.

## GitHub and Git

`loki add github --user USERNAME` first attempts to import exactly that user's existing `gh` credential, verifies it against GitHub, and saves it in the selected profile. It never runs `gh auth switch`, changes the shared Keychain, or signs another account out. If no named login is available, interactive setup accepts a personal access token with hidden input. Set `LOKI_GH_IMPORT_DIR` to import from a nondefault GitHub CLI configuration directory.

GitHub CLI and `gh-axi` receive the selected token. Git uses an account-scoped credential helper, and GitHub SSH remotes are translated to HTTPS. Existing SSH keys are not copied, modified, or deleted. Git author identity comes from the connected GitHub account; customize it with `loki tools configure github --set git_name=NAME --set git_email=EMAIL`. Without configured identity, Git commits fail rather than borrow a repository identity.

If the earlier `dev-account` setup exists, enabling the GitHub integration reads its Git identity metadata. Saved credentials are not automatically copied. Reconnect services with `loki add`.

## MCP

Configure a direct MCP server in the selected Claude/Codex CLI first, then:

```sh
loki add mcp claude tickets
loki add mcp codex tickets
```

The login uses that profile's client configuration. Registered Claude MCP logins are logged out during a global reset; Codex file-backed MCP credentials are removed. MCP servers that maintain their own credentials need a separate adapter. Hosted Claude/ChatGPT connections follow their app account.

The bundled Claude integration supports a claude.ai subscription login (`--claudeai`). Keyless Claude Console sign-ins and arbitrary cloud-provider authentication are outside this initial adapter's scope. Keep Codex's `cli_auth_credentials_store` and `mcp_oauth_credentials_store` set to `file` for the managed reset behavior.

## Development

Python 3.11+, direnv, and Click are required. Use a project environment for Python dependencies:

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
bin/loki tools validate --all
python3 -m unittest discover -s tests -v
```

Tests use temporary settings and fictional credentials. They cover concurrent personas and registry updates, global shifts, real Git worktrees, inherited-token removal, ordinary shell commands, folder cleanup, global reset, and failed logouts. They do not run live OAuth logins.

References: [direnv](https://direnv.net/man/direnv.1.html), [Claude account directories](https://code.claude.com/docs/en/authentication), [Codex credential storage](https://learn.chatgpt.com/docs/auth).
