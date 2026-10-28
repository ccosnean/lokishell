# google

Shared dependency for Firebase, gcloud, and ADC. Owns Google SDK credential paths and clears inherited CLOUDSDK_* overrides. A missing persona ADC file fails instead of selecting global credentials.

Environment names, storage paths, and lifecycle rules are defined in [tool.toml](tool.toml). See the repository [contribution guide](../../CONTRIBUTING.md) for extension and testing instructions.
