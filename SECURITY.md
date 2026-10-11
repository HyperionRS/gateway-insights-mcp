# Security

## Reporting a vulnerability

Please report security issues privately through GitHub's **private vulnerability reporting** for this
repository (Security → Report a vulnerability). Do not open a public issue. We acknowledge reports within
five working days.

## Security model

- **One server changes data.** `gateway-insights-actions` changes data through batch previews only; it runs
  reversible tasks and leaves money operations and credentials to a person in the console, with no tool to
  run them. Credentials come by reference from the user's own store and are never returned. The other two
  servers are read-only by construction.
- **Who may run what, in one place** (actions). The class of a task as the console shows it at run time is
  the floor; what the server recorded when it prepared the task may only raise it, and only for the same
  console, profile, task id and type. Every input of a request — parameters, objects, CSV, `changes`
  lists — and everything the console echoes of it is cleaned of credentials before an answer, a log or the
  journal sees it. Two requests are the same operation only if they are the same request, secrets
  included, keyed so the key reveals none. A secret store's output never appears in an error.
- **Read-only by construction** (exec, decline). Each server has a fixed list of console paths in code; a path outside it
  is refused before any connection is opened. There is no tool that calls an arbitrary method, and no
  argument sets a host, a path or a query string.
- **A personal token** — read-only for exec and decline, read-write for actions in a profile of its own.
  Session tokens are refused. The configuration file must be readable by
  its owner only. The token never appears in output, logs or errors; diagnostics show a hint only.
- **TLS** is verified always; disabling it is an explicit setting that warns on every start.
- **Redirects** are never followed: a 3xx from the console is an error, so the token cannot be carried to an
  address the server chose.
- **Console data is untrusted.** Every value is masked (card-number shapes, secret-like keys), escaped for
  markdown and HTML, and every result reminds the model that it is data, not instructions.
- **Permissions are the console's.** Values the console hides are shown as «no access» and never
  reconstructed from other values.
- **Reports are inert.** HTML pages have no script and a Content-Security-Policy that forbids external
  loads; themes are data and are validated.
- **No third-party runtime dependencies.**
