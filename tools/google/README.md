# google

Internal dependency for Firebase, gcloud, and ADC. Supplies Google credential paths and clears inherited CLOUDSDK_* overrides inside managed CLI processes. Other shell commands keep their existing Google configuration. The separate ADC integration explicitly opts SDK scripts into the persona's credentials. A missing persona ADC file fails instead of selecting global credentials in a managed process.

gcloud manages its active configuration inside the persona directory. Loki does not force a configuration name: gcloud can create `default` on first use and retain any named configuration you activate within that persona.

Environment names, storage paths, and lifecycle rules are defined in [tool.toml](tool.toml). See the repository [contribution guide](../../CONTRIBUTING.md) for extension and testing instructions.
