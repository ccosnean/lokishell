# adc

Runs gcloud auth application-default login, temporarily clearing GOOGLE_APPLICATION_CREDENTIALS for the login command. Explicitly enabling ADC exports the persona's GOOGLE_APPLICATION_CREDENTIALS to the shell so Python, Node, and other SDK clients use it. Existing Google project settings remain. Disabling ADC restores the prior credential path. Enabling only Firebase or gcloud leaves SDK scripts' credentials unchanged.

`loki who` reads only identity fields from the persona's ADC file. It reports saved credentials, not a verified live session. Service-account credentials usually contain an email; user ADC may not. It never substitutes the gcloud CLI account or prints an access token.

Environment names, storage paths, and lifecycle rules are defined in [tool.toml](tool.toml). See the repository [contribution guide](../../CONTRIBUTING.md) for extension and testing instructions.
