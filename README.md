# LokiShell

Use personal and work accounts side by side. A folder points to a shared account profile; ordinary commands use that profile through direnv.

LokiShell is a command-line account and environment manager for **zsh and bash**. The executable is `loki`.

## Install

```sh
brew install ccosnean/tap/lokishell
```

The Homebrew formula installs LokiShell, Python, and direnv. Install the service CLIs you use separately.

## Start in a project

```sh
cd ~/projects/my-project
loki init
```

Choose `personal`, `work`, or another profile name. Alternatively:

```sh
loki init --profile personal
```

The first initialization installs shell hooks. **Open a new terminal once** to activate newly installed hooks. If the current shell already uses direnv, it can load the folder immediately. A command cannot change its parent shell's environment by itself.

Connect accounts once per profile:

```sh
loki add firebase
loki add gcloud
loki add adc
loki add github --user your-personal-github-user
loki add claude
loki add codex
```

Then run ordinary commands:

```sh
firebase projects:list
gcloud auth list
git fetch
claude
codex
```

Another folder initialized with the same profile shares those logins. A folder initialized with `work` uses independent stores. Concurrent shells can select different profiles.

## Clean

```sh
loki clean
```

Removes only this folder's LokiShell block from `.envrc`, preserving other project settings. Shared logins remain available in other linked folders. The shell returns to its inherited/default environment; this is not a ban on access from that folder.

```sh
loki clean --global
```

Clears the selected profile's locally managed logins for every linked folder. Folder pointers, identity settings, and agent history remain. The other profiles remain separate. This does **not** revoke provider-side OAuth grants, browser sessions, hosted app connections, custom MCP token stores, or tokens already held by running processes. Restart running agents after a global reset. Failed Claude Keychain/MCP logouts are reported as an error rather than hidden.

## Agents and shells

LokiShell installs a hook in `.zshenv` as well as `.zshrc`. Fresh zsh command shells launched by agents load the folder's environment, including noninteractive shells. Directory changes refresh the selection. Bash integration is installed in `.bash_profile` and `.bashrc`; arbitrary `bash -c` processes must inherit an initialized environment or load the hook.

An agent launched from an initialized terminal inherits the selected profile. An already-running desktop app's own account and hosted connections do not switch when its child shell changes directories. Runners that bypass startup files or sanitize the environment should use:

```sh
loki --profile personal exec COMMAND ARGUMENTS
```

Shell setup can also be installed independently with `loki setup`, or supplied explicitly:

```sh
eval "$(loki hook zsh)"
# or
eval "$(loki hook bash)"
```

`loki init --no-shell --profile personal` creates the folder pointer without editing startup files. Startup edits preserve other content and make backups. Initialize a child folder explicitly if it has its own `.envrc`; direnv does not automatically merge parent and child files.

## What is stored where

```text
~/.config/lokishell/
  profiles/
    personal/
      profile.json          # identity metadata; no tokens
      env.sh                # environment loader
      auth/github-token     # selected GitHub token
      xdg/configstore/      # Firebase CLI login
      gcloud/               # gcloud and application default credentials
      claude/               # Claude Code configuration and sessions
      codex/                # Codex configuration, credentials, sessions, MCP tokens
    work/
  backups/                  # shell startup backups

project/.envrc              # pointer to one shared profile
```

`LOKI_HOME` can override the settings root. Its location is independent of `XDG_CONFIG_HOME`, so nested profile selection does not create nested stores. Profile directories are private to the current OS user (`0700`); files LokiShell creates are `0600`.

GitHub and Codex tokens use local files, not encryption supplied by LokiShell. Claude subscription credentials use Claude Code's directory-scoped Keychain storage on macOS. This separates account selection under one OS user; it is not an OS security boundary. Never commit profile stores or copy their contents into logs.

## GitHub and Git

`loki add github --user USERNAME` first attempts to import exactly that user's existing `gh` credential, verifies it against GitHub, and saves it in the selected profile. It never runs `gh auth switch`, changes the shared Keychain, or signs another account out. If no named login is available, interactive setup accepts a personal access token with hidden input. Set `LOKI_GH_IMPORT_DIR` to import from a nondefault GitHub CLI configuration directory.

GitHub CLI and `gh-axi` receive the selected token. Git uses an account-scoped credential helper, and GitHub SSH remotes are translated to HTTPS. Existing SSH keys are not copied, modified, or deleted. Other SSH remotes are intentionally disabled in this initial release. Git author identity comes from the connected GitHub account; edit `git_name` and `git_email` in the profile's `profile.json` to customize them, then run `loki init` again. Without configured identity, Git commits fail rather than borrow a repository identity.

If the earlier `dev-account` setup exists, initial profile creation reads its Git identity metadata. Saved credentials are not automatically copied. Reconnect services with `loki add`.

## MCP

Configure a direct MCP server in the selected Claude/Codex CLI first, then:

```sh
loki add mcp claude tickets
loki add mcp codex tickets
```

The login uses that profile's client configuration. Registered Claude MCP logins are logged out during a global reset; Codex file-backed MCP credentials are removed. MCP servers that maintain their own credentials need a separate adapter. Hosted Claude/ChatGPT connections follow their app account.

Supported Claude authentication is a claude.ai subscription login (`--claudeai`). Keyless Claude Console sign-ins and arbitrary cloud-provider authentication are outside this initial adapter's scope. Keep Codex's `cli_auth_credentials_store` and `mcp_oauth_credentials_store` set to `file` for the managed reset behavior.

## Development

Python 3.11+ and direnv are required. No Python packages are needed.

```sh
python3 -m unittest discover -s tests -v
```

Tests use temporary settings and fictional credentials. They cover concurrent profiles, inherited-token removal, ordinary shell commands, folder cleanup, global reset, and failed logouts. They do not run live OAuth logins.

References: [direnv](https://direnv.net/man/direnv.1.html), [Claude account directories](https://code.claude.com/docs/en/authentication), [Codex credential storage](https://learn.chatgpt.com/docs/auth).
