# claude

Supports claude.ai subscription logins through CLAUDE_CONFIG_DIR and registered direct MCP logins. Claude manages its directory-scoped Keychain entries on macOS. Keyless Console, third-party provider credentials, and desktop/hosted connections are outside this adapter’s scope.

Environment names, storage paths, and lifecycle rules are defined in [tool.toml](tool.toml). See the repository [contribution guide](../../CONTRIBUTING.md) for extension and testing instructions.

`loki who` reads [`claude auth status`](https://code.claude.com/docs/en/cli-reference), displaying the reported email or authentication method. Unknown output from an older CLI is reported as a failed check, without exposing the raw response.
