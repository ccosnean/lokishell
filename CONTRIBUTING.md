# Contributing a tool

Add one directory under `tools/`. Loki discovers definitions automatically; there is no provider list or authentication switch statement to edit in core.

```text
tools/acme/
  tool.toml          # required: environment names and lifecycle rules
  adapter.py         # optional: Python hooks for unusual authentication
  test_adapter.py    # integration tests, automatically discovered
  README.md          # supported account modes and limitations
```

The persona, folder, worktree, and shell logic lives in `lib/lokishell/`. Provider-specific environment names, paths, login rules, and tests belong in the tool directory.

## A declarative definition

Most integrations need only a TOML file:

```toml
schema_version = 1
id = "acme"
name = "Acme CLI"
description = "Acme accounts and configuration"

[cli]
command = "acme"
install_hint = "Install the Acme CLI using its official instructions"

[env]
unset = ["ACME_TOKEN"]
unset_prefixes = ["ACME_AUTH_"]

[env.set]
ACME_CONFIG_DIR = "{profile}/acme"
ACME_REGION = "{config.region}"

[state]
directories = ["acme"]

[login]
command = ["acme", "login"]

[cleanup]
files = ["acme/credentials.json"]
```

`loki tools install acme` enables that definition for a persona and prepares its state directories. It does not install the upstream executable or log in. `loki add acme` enables it and runs its login action. Unselected definitions do not change the environment, and unselected Python hooks are not imported.

Set user-specific, nonsecret options with:

```sh
loki tools configure acme --set region=eu
loki add acme --set organization=example
```

Persistent settings live under `tool_config.acme` in the persona's `profile.json`. `--set` on `add` supplies options for that login only. Commands can use `{option.organization}`. Store tokens through the native CLI or private credential files, rather than in a definition or command-line argument.

## Definition fields

| Field | Meaning |
| --- | --- |
| `schema_version` | Currently `1` |
| `id` | Lowercase identifier matching the directory name |
| `name`, `description` | Labels shown by `loki tools` |
| `depends_on` | Other integration IDs required for the credential environment |
| `internal` | Hide a shared dependency from the default catalog listing |
| `legacy_default` | Migration flag for pre-registry integrations; leave unset for new contributions |
| `cli.command`, `cli.install_hint` | Executable detection and installation instructions |
| `cli.optional` | Documents an executable that is optional, such as GitHub CLI for token import |
| `env.unset` | Exact inherited environment variables to clear |
| `env.unset_prefixes` | Tool-owned variable prefixes to clear |
| `env.set` | Environment names mapped to string values/templates |
| `env.from_file.VARIABLE` | A persona-relative credential `path` and a missing-file `default` |
| `env.from_config.VARIABLE` | A tool setting `key` and optional `default` |
| `state.directories` | Persona-relative directories to create |
| `state.files` | Bootstrap entries with `path` and `content`; existing files are preserved |
| `login.command` | Argument array for the native login command |
| `login.unset_env` | Variables temporarily cleared for this login |
| `who.command` | Account-check argv; prints only the selected username/email to stdout |
| `who.timeout` | Command timeout in seconds, greater than 0 and at most 60; default 10 |
| `who.signed_out_exit_codes` | Nonzero exit codes meaning signed out; other failures remain errors |
| `cleanup.files`, `cleanup.directories` | Persona-relative credential paths to remove during global clean |
| `cleanup.command` | Optional logout command, run before removing credential files |
| `cleanup.when_nonempty` | Run the logout command only when this relative directory has state |
| `mcp.login`, `mcp.logout` | Optional argument arrays accepting `{server}` |
| `hooks` | Optional named Python hooks described below |

Templates support `{profile}`, `{persona}`, `{id}`, `{home}`, `{loki}`, `{loki_quoted}`, `{config.KEY}`, and `{option.KEY}`. MCP commands also receive `{server}`. Use doubled braces for literal braces in templated content. Paths in `state`, `cleanup`, and `env.from_file` are relative to the persona directory; absolute paths and `..` are rejected.

Commands are argument arrays, not shell strings. Loki does not evaluate shell substitutions in environment values. Explicit Python hooks and explicit shell commands in a definition are executable code, so review them before installing third-party definitions.

Tools cannot own Loki/direnv bookkeeping variables or change shell startup variables such as `HOME`, `PATH`, or `BASH_ENV`. Conflicting values for the same environment variable are rejected. Shared ownership should be factored into a dependency; see `tools/google/` and the Firebase/gcloud/ADC integrations.

Only enabled integrations and their dependencies contribute variables. When selection changes, Loki restores the previous values for variables it no longer owns. Its private process-environment snapshot can contain inherited credentials, just as an environment diff can; never print the full environment in tests or logs.

## Account checks for `loki who`

An integration can declare a command that prints one identity, without logging in or switching accounts:

```toml
[who]
command = ["acme", "account", "current", "--format=email"]
signed_out_exit_codes = [1]
timeout = 10
```

A successful command with empty stdout means signed out. Nonempty stdout must contain only one username or email, never a token. Commands run with the inspected persona's full environment, the caller's working directory, and closed stdin. Loki captures both output streams and discards raw errors. One failed check does not stop the other integrations.

For structured output, multiple saved accounts, or a local credential file, declare `who = "who"` under `[hooks]` and implement `who(ctx)` in `adapter.py`. Return a dictionary containing `status`, optional `account`, and optional `detail`. Status values are `signed_in`, `signed_out`, `configured` (saved credentials), `unknown`, `unavailable`, `error`, `timeout`, or `unsupported`. Only those three result fields are displayed; do not put secrets in them.

The account-check context adds `ctx.capture()` to execute `who.command` with its timeout, returning a `subprocess.CompletedProcess`, and `ctx.read_json(relative)` to read a persona-relative file or return `None` when absent. Hooks that only read files need no `who.command`. Return the selected account, not the first account in a list. Report unknown identity honestly; ADC and API-key logins may not expose an email. Avoid printing from hooks, refreshing credentials yourself, or invoking login/logout commands. Missing account-check definitions appear as `Not supported`.

## Optional Python hooks

For an unusual login flow, declare hooks and put their implementations in `adapter.py`:

```toml
[hooks]
prepare = "prepare"
login = "login"
cleanup = "cleanup"
credential = "credential"
who = "who"
```

Only declare the hooks you need. Hooks receive one context:

```python
def prepare(ctx):
    # Prepare local configuration; preserve existing credentials.
    if not ctx.path("acme/config.json").exists():
        ctx.write("acme/config.json", '{"schema":1}\n')
```

Context operations:

- `ctx.persona`, `ctx.directory`, `ctx.tool.id`, `ctx.tool.data`: selected persona and definition.
- `ctx.options`: transient `loki add --set` options. The compatibility `--user` flag supplies `user`.
- `ctx.setting(key, default)`: persona-specific settings for this tool.
- `ctx.path(relative)`, `ctx.write(relative, content)`: scoped storage with path checks and private file modes.
- `ctx.render(value, extra=None)`: expand supported templates.
- `ctx.run(argv, unset=(), extra=None)`: execute with this tool's environment and dependencies, including when cleaning a disabled integration.
- `ctx.args`: arguments supplied to an internal tool helper.

`prepare` should prepare local state without authenticating or making external changes. It may be called repeatedly. Bootstrap files in `state.files` are preferable when no custom logic is needed.

Use `tools/github/adapter.py` for a real example: it imports an explicitly named GitHub credential and checks its identity without switching shared logins. Its provider-specific variables and Git behavior remain in that same tool directory.

## Test and submit

```sh
python3 -m pip install -r requirements.txt
bin/loki tools validate --all
python3 -m unittest discover -s tests -v
```

`tools/*/test_*.py` files are discovered automatically. Tests can use `tests.support.LokiFixture` with `initial_tools = ("your-tool",)` for disposable personas and command stubs. Add any extra native-command stub your tool needs. Use fictional credentials and mock network calls. Never run real login or logout commands against a contributor's account.

Plain `loki tools validate` checks only the current persona's enabled definitions and dependencies. Use `--all` when checking a contribution against the whole catalog. Click commands live in `lib/lokishell/commands.py`; ordinary tool contributions do not need to change them.

Cover the integration's meaningful boundaries: intended storage location, conflicting inherited credentials, account verification where applicable, and cleanup scope. Describe any browser, Keychain, or hosted-connector state the integration cannot isolate or clear.

For a private tool, no repository contribution is required:

```sh
loki tools install --from ./my-tool
```

This copies `tool.toml`, optional `adapter.py`, and optional `README.md` into `~/.config/lokishell/tools/<id>/`, then enables it for the selected persona. It does not automatically download remote code. Use `--replace` explicitly to override an existing definition. Other personas can enable the installed definition separately.

`loki tools list` shows installed integrations for the selected persona; local definitions show their installed directory in the table's `Source` column. `loki tools catalog` browses all definitions. Both accept `--all` to include shared dependencies and `--json` for structured output.
