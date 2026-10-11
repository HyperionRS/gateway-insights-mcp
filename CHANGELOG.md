# Changelog

## 0.1.0 — unreleased

- Shared core: profiles, a read-only console client with a path allowlist, masking and escaping, Decimal
  numbers, report spec v1, an HTML renderer with theme packs and a neutral theme, a call-budgeted frame for
  business tools, a minimal MCP stdio server.
- Executive server — raw tools: `exec_check`, `exec_kpi`, `exec_trend`, `exec_top`, `exec_hold_top`,
  `exec_hold_schedule`, `monitor_total`, `monitor_by_entity`; business tools: `ceo_briefing`,
  `ceo_earnings`, `ceo_movers`, `ceo_concentration`, `ceo_holds`, `ceo_entity`, `ceo_compare`, `ceo_split`,
  `ceo_risk`.
- Actions server (changes data): order and entity search, batch types and forms, `batch_preview`, eleven
  `plan_*` tools, `plan_revert`, `batch_run` for reversible tasks only; classes by task; secrets by
  reference from user-configured sources; a local journal; a start-up warning; `PREPARES:` / `WRITES:`
  descriptions and annotations; confirmation through the client (elicitation) when it can ask.
- Shared core: order search with the Orders screen's date types and rules; secret sources.
- Both servers: `entity_search` — find an entity's id by name or id over the console's entity lists, with
  linked filters; only id, name, status and currency leave the tool.
- Decline server — raw tools: `decline_check`, `decline_kpi`, `decline_heatmap`, `decline_compare`; business
  tools: `decline_overview`, `decline_change`, `decline_where`, `decline_route`, `decline_opportunity`,
  `decline_payouts`.
- The console client never follows a redirect: the request carries the token.
- The release gate scans every version of every file in the history, and every file name ever committed;
  a forbidden file name is never printed to the log.
- Actions, after a run against a stand: adding a routing criterion value is class 2 (no batch removes it);
  plan_revert covers criterion replacements, restriction parameters and attribute changes; an item's source
  line is never returned (it may hold credentials); batch_fields works for types without attributes.
- Reads that batches need: `blueprint_routing` (routes, rules, criterion values, Mermaid graph) and
  `entity_restrictions` (restrictions and their parameters, read live) in the shared core — also in the decline
  server; `api_hosts` and `batch_dictionaries` in actions; an entity's manager through `entity_search`.
- `batch_fields` ends with a guide for every batch operation, written from running each against a stand.
- After an outside review: run admission, redaction and operation identity in one module (`guard.py`) —
  a journal entry counts only for the same console, profile, task id and type and never lowers a class;
  credentials are cleaned from every input form (parameters, objects, CSV, nested `changes`) and from the
  console's echoes (source lines, refusal examples); keys are an HMAC of the real request; a secret
  store's output stays out of errors; a way back reads every page; one actions call makes at most 120
  console calls; the publication scan reads non-ASCII file names. Shared id validation and entity filters
  in the core; dead code removed.
- `decline_check` shows the report theme and folder, as `exec_check` does.
