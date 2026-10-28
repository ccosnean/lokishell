# github

Imports exactly one named GitHub CLI token, or accepts a token interactively, then verifies its account. It does not switch shared gh logins. Also owns Git author identity, the GitHub credential helper, and HTTPS mapping for GitHub SSH remotes. Tokens stay in a private persona file. Other SSH remotes are no longer disabled by Loki.

Environment names, storage paths, and lifecycle rules are defined in [tool.toml](tool.toml). See the repository [contribution guide](../../CONTRIBUTING.md) for extension and testing instructions.
