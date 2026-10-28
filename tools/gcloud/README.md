# gcloud

Runs gcloud auth login using persona-scoped Google storage. Install the upstream Google Cloud CLI separately.

`loki who` uses [`gcloud auth list --filter=status:ACTIVE --format=value(account)`](https://docs.cloud.google.com/sdk/gcloud/reference/auth/list) to report the active credentialed account. ADC is a separate login and has its own row when installed.

Environment names, storage paths, and lifecycle rules are defined in [tool.toml](tool.toml). See the repository [contribution guide](../../CONTRIBUTING.md) for extension and testing instructions.
