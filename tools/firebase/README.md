# firebase

Owns Firebase CLI login storage through XDG_CONFIG_HOME and clears inherited Firebase overrides. Uses the shared Google dependency to keep SDK credentials persona-scoped. XDG_CONFIG_HOME also affects other XDG-aware programs in the same shell.

Environment names, storage paths, and lifecycle rules are defined in [tool.toml](tool.toml). See the repository [contribution guide](../../CONTRIBUTING.md) for extension and testing instructions.
