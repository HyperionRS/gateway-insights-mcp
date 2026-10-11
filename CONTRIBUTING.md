# Contributing

Thank you for helping. A few rules keep the servers safe to run against a production console.

1. **Reading stays reading; writing has one door.** In exec and decline a new tool reads one more console
   endpoint or combines raw tools; it never writes. Writing belongs to actions only, only through a batch
   preview, and a new operation gets its class and its guide before anything else. No tool calls an
   arbitrary method or takes a host, path or query from its arguments.
2. **Two layers.** Raw tools talk to the console (one per endpoint). Business tools use raw tools only —
   no HTTP of their own — and return a report spec with «how obtained».
3. **Hidden stays hidden.** A value the console returns as `null` because of permissions is «no access».
   Never compute it from other fields.
4. **Own arithmetic is documented.** A number the tool computes itself gets its formula in the README and
   a test. Money is `Decimal`.
5. **Standard library only.** No runtime dependencies.
6. **Neutral.** No brand, console address, customer or person in code, tests or docs. Fixtures are
   synthetic.
7. **Tests:** `python -m unittest discover -s tests`. Lint: `ruff check src tests scripts`.
