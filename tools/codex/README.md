# codex

Uses CODEX_HOME with file-backed account and MCP credentials. Existing config.toml is preserved. Keep cli_auth_credentials_store and mcp_oauth_credentials_store set to file for the declared cleanup paths to cover those credentials. Hosted app connections are separate.

Environment names, storage paths, and lifecycle rules are defined in [tool.toml](tool.toml). See the repository [contribution guide](../../CONTRIBUTING.md) for extension and testing instructions.

`loki who` uses [`codex login status`](https://learn.chatgpt.com/docs/developer-commands). For ChatGPT logins, it reads the email claim from the persona's saved ID token when available; this is saved identity, not a new server verification. API-key logins show the authentication method without exposing any part of the key.
