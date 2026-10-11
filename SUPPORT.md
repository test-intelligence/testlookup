# Getting help

TestLookup is a community project maintained by volunteers. There is no paid
support and no service-level agreement; answers come from the maintainers and
other users as time allows.

| You want to... | Go to |
|---|---|
| Learn how to use it | The in-app guide at `/docs` on your instance, and [user-guide/](user-guide/README.md) for operators |
| Install or deploy it | [GETTING_STARTED.md](GETTING_STARTED.md) and [docs/operations/deployment.md](docs/operations/deployment.md) |
| Look up a setting or a feature flag | [docs/operations/configuration.md](docs/operations/configuration.md), [docs/operations/feature-flags.md](docs/operations/feature-flags.md) |
| Ask a question or share an idea | GitHub Discussions on the repository, if enabled; otherwise an issue labelled `question` |
| Report a bug | A new issue with the **Bug report** form |
| Request a feature | A new issue with the **Feature request** form |
| Report a vulnerability | **Not an issue** — GitHub private vulnerability reporting, see [SECURITY.md](SECURITY.md) |
| Report a Code of Conduct concern | The **Code of Conduct report** form, see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) |

Before you ask, please check [user-guide/troubleshooting.md](user-guide/troubleshooting.md)
and search the existing issues.

## What is supported

- The latest release line and `main` (see [SECURITY.md](SECURITY.md#supported-versions)).
- The deployment modes documented in [docs/operations/deployment.md](docs/operations/deployment.md).
- Bug reports need the TestLookup version (the `/health/version` endpoint
  shows the commit), the deployment mode, and the relevant logs with secrets
  removed.

Third-party services you run next to TestLookup (PostgreSQL, MongoDB, Valkey,
the object store, Ollama and model weights, identity providers) are supported
by their own projects.
