# firebase

Owns Firebase CLI login storage through XDG_CONFIG_HOME and clears inherited Firebase overrides inside the Firebase process. The Google dependency supplies its persona credentials. Other commands in the shell keep their existing XDG and Google settings; children launched by Firebase inherit Firebase's environment.

Environment names, storage paths, and lifecycle rules are defined in [tool.toml](tool.toml). See the repository [contribution guide](../../CONTRIBUTING.md) for extension and testing instructions.

`loki who` reads the selected-account summary from `firebase login:list`, including project-specific account selection. It does not expose the other saved accounts or the credentials contained in the command's JSON output. If no CLI login exists, it reports persona ADC credentials when present. See the [Firebase CLI reference](https://firebase.google.com/docs/cli).
