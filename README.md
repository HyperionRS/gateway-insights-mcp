# Gateway Insights MCP

> **⚠ One of these servers changes data.** `gateway-insights-actions` changes data in your payment console
> through its batch operations: it switches requestors and routes, changes tariffs, names and restriction
> parameters, and prepares order operations and credential changes. Install it only if that is what you
> want an assistant to do, give it its own profile and token, and read [what it can run](#actions--the-operator).
> The other two servers are read-only by construction.

[Model Context Protocol](https://modelcontextprotocol.io) servers that let an AI assistant work with a payment
gateway's admin console: answer business questions — how the business is doing, where it earns, who grew
and who left, how much money is on hold, where and why card payments decline — and turn the answers into
prepared changes.

| server | for | what it does to data | console screens |
|---|---|---|---|
| `gateway-insights-exec` | the business owner | reads | Executive Dashboard, transaction monitor, money on hold |
| `gateway-insights-decline` | the approval-rate analyst | reads | Decline Dashboard |
| `gateway-insights-actions` | the operator | **changes** | entity lists, Orders, batch operations |

They need no third-party packages and run on Python 3.9+. Every analytic answer can also be saved as a
self-contained HTML report in your own visual theme.

## Install and run

```bash
uvx --from git+https://github.com/HyperionRS/gateway-insights-mcp gateway-insights-exec
uvx --from git+https://github.com/HyperionRS/gateway-insights-mcp gateway-insights-decline
uvx --from git+https://github.com/HyperionRS/gateway-insights-mcp gateway-insights-actions   # changes data
# or, from a checkout:
PYTHONPATH=src python -m gateway_insights_exec
```

Register them with an MCP client as stdio servers, for example:

```json
{ "mcpServers": {
  "gateway-insights-exec": { "command": "uvx",
    "args": ["--from", "git+https://github.com/HyperionRS/gateway-insights-mcp", "gateway-insights-exec"] },
  "gateway-insights-decline": { "command": "uvx",
    "args": ["--from", "git+https://github.com/HyperionRS/gateway-insights-mcp", "gateway-insights-decline"] }
} }
```

## Configure

Issue a **personal read-only API token** in your console profile. A browser session token is refused: it
would carry write rights.

`~/.config/gateway-insights/config.json`, readable by you only (`chmod 600`):

```json
{
  "default_profile": "prod",
  "profiles": {
    "prod": {
      "base_url": "https://console.example.com/console-api/api",
      "token": "<personal read-only API token>",
      "currency_id": null,
      "theme": null,
      "report_dir": "~/gateway-insights/reports"
    }
  }
}
```

Environment variables override the file: `GI_CONFIG`, `GI_PROFILE`, `GI_BASE_URL`, `GI_TOKEN`,
`GI_CURRENCY_ID`, `GI_CA_BUNDLE`, `GI_THEME`, `GI_REPORT_DIR`. `GI_INSECURE=1` turns TLS verification off and
prints a warning on every start — for a test console only.

Run `exec_check` or `decline_check` first: they show the profile, the address, a hint of the token, the report theme and folder, and a probe
read.

## What you can ask

- *How did last week go against the week before? What went the wrong way, and who caused it?*
- *How much did we earn in September against August, and on whom?*
- *Which merchants have large volume but earn below our average rate?*
- *Who grew, who fell, who is new and who went quiet this month?*
- *How dependent are we on our five largest merchants?*
- *How much merchants' money do we hold, and how much is released in the next 30 days?*
- *How is merchant «Alpha Shop» doing this month, by day, and through which providers?*
- *Which providers does merchant 1042 work through?*
- *Compare providers 12 and 19 by approval rate this week.*
- *Is the chargeback ratio rising, and which merchants are above 1%?*
- *Declines jumped yesterday — is it the cards, or something above them?*
- *For Polish credit cards, which provider declines least?*
- *Where are the most excess declines against last week, by issuing bank?*

## Tools

### exec — the business owner

Business tools (start here). Each answers in one to three lines, a table, the caveats that apply, and «how
obtained» — the raw calls behind every number:

| tool | answers |
|---|---|
| `ceo_briefing` | headline KPIs against the base, what moved the bad way, the merchants that added and lost the most, providers with the highest decline rate, chargebacks, fraud and reversals |
| `ceo_earnings` | the margin of a tier (manager, reseller, both) against the base, the top entities by it, effective rate, large volume earning below the average rate |
| `ceo_movers` | who grew, fell, appeared and went quiet, by approved amount, approved count or margin |
| `ceo_concentration` | share of the largest 1, 5 and 10 entities and HHI, against the base |
| `ceo_holds` | money on hold now (from merchants, by banks), the largest holders, release in 7 / 30 / N days |
| `ceo_entity` | one merchant, provider or manager: KPIs, margins, approved amount by day, and through whom its volume goes |
| `ceo_compare` | 2–5 entities by day and side by side, with each one's weakest day |
| `ceo_split` | merchant × provider: approved amount, share, approval rate and margin for up to 6 entities |
| `ceo_risk` | chargebacks, fraud and reversals against the base, the chargeback ratio, entities above a threshold |

Raw tools, one per console read:

| tool | returns |
|---|---|
| `exec_check` | connection check and a probe read |
| `entity_search` | find merchants, providers, managers, bank terminals, requestors, blueprints, channels or resellers by id or name, optionally linked to other entities; returns their ids |
| `exec_kpi` | the period against its base: transactions, approved count and amount, declined, filtered, approval and decline rate, average ticket, decline breakdown — for deposits and withdrawals |
| `exec_trend` | one metric by day or hour, the period against its base; optionally selected entities, combined or one line each |
| `exec_top` | merchants, providers or managers ranked by a metric (margins included), with base values and earnings by tier |
| `exec_hold_top` | money on hold now, by entity |
| `exec_hold_schedule` | when the money on hold is released, by day |
| `monitor_total` | transaction monitor totals by operation type |
| `monitor_by_entity` | one operation type by merchant, manager or provider |

### decline — the approval-rate analyst

| tool | answers |
|---|---|
| `decline_overview` | the decline rate against the base and the card segments declining most above the average |
| `decline_change` | why the rate moved: per dimension the console's verdict (concentrated, mix, broad, …) and the segments with the most excess declines; broad in every card dimension means the cause is above the cards |
| `decline_where` | drill down by one dimension within the card criteria given: country → bank → product |
| `decline_route` | for a card segment, which provider or bank terminal declines it least, and how many declines each route would avoid at the best rate |
| `decline_opportunity` | where the excess declines against the base sit, and how many returning those segments to their base rates would avoid |
| `decline_payouts` | the overview for the recipient's card (payouts, transfers) |
| `decline_check`, `decline_kpi`, `decline_heatmap`, `decline_compare` | connection check and the raw reads |
| `entity_search` | the same entity search as in the exec server |
| `blueprint_routing`, `entity_restrictions` | a blueprint's routes and rules, and an entity's restrictions with their parameters — read live, to see what a route or a restriction does before changing it with the actions server |

### Naming an entity

Every analytic tool takes entity ids. When a question names a merchant, the assistant first looks it up
with `entity_search`; that read is listed in «how obtained» with the rest. The search is the console's own:
by id, by the beginning of a name or a whole word of it, and it may match fields it does not show (such as
a login). Only the id, name, status and currency of a match leave the tool — never a login, an e-mail
address or a description. Each entity type needs its list right in the console.

### actions — the operator

**This server changes data.** It finds entities and orders and changes them through the console's batch
operations — never through single operations. Every change is first a **preview**: the console checks each
object and shows what it was, what it will be, and why an object is refused. Nothing changes but the task
itself until the preview is run, and a preview is run only once (an idempotency key on every register and
run).

Who runs what depends on the **class** of the task:

| class | what | who runs it |
|---|---|---|
| 1 — reversible | requestor on/off, a terminal out of routing, names and descriptions, tariffs, restriction parameters, replacing a routing criterion value, requestor configs and attributes, API hosts, customer-data generator, a terminal copy with routing off, asking providers for a status | this server, with `batch_run` — after asking you, when your client can ask |
| 2 — money and money paths | reversal, capture, void, cancel, approve, decline, chargeback, disputes, status and date changes; a terminal into routing; replacing a terminal; adding a routing criterion value (no batch removes it) | **you, in the console** — the server prepares the task and answers with its link; it has no tool to run it |
| 3 — secrets | PSP passwords and any credential | **you, in the console**; the value comes from your secret store, not from the chat |

A task's class is the highest of what it actually does: switching routing rows on makes it class 2, a
credential column makes it class 3. A batch type the server does not know yet is class 2.

Tools:

| tool | does |
|---|---|
| `entity_search`, `entity_count` | find entities by id or name — and their manager or reseller, by searching the owner's type with the entity as a filter; count what a selection filter picks |
| `blueprint_routing` | a blueprint's routes, rules and criterion values (the `value` routing batches take), and the whole graph as Mermaid with the terminals of each cascade |
| `entity_restrictions` | an entity's restrictions, read live; one restriction's parameters with ids, types and current values |
| `api_hosts` | the API hosts a requestor's tokens may move to, by manager |
| `batch_dictionaries` | reason codes for chargeback, fraud, reversal and faux reversal; manager and reseller tariffs |
| `order_search`, `order_dictionaries` | find orders as on the Orders screen — three date types (last change, order creation, transaction creation) with the filters each allows, 26 kinds of simple search; ids for type, status, card type, currency and error filters |
| `batch_types`, `batch_fields` | the batch types your token may use, their class and their form |
| `batch_preview` | **prepares** any batch type from ids, objects with parameters, a selection filter or CSV |
| `plan_status_check`, `plan_order_action`, `plan_disable`, `plan_route_status`, `plan_reroute`, `plan_terminal_pool`, `plan_restriction`, `plan_rename`, `plan_rate_plan`, `plan_api_host`, `plan_secret` | **prepare** a task from a finding, building its input in the type's own CSV format |
| `plan_revert` | **prepares** the way back for a run: names, tariffs, routing switches, requestor switches, API hosts, routing criterion replacements, restriction parameters, requestor configs and attributes, merchant generator, terminal properties. A value that was inherited before, and an attached restriction, are listed — a batch cannot undo them |
| `batch_run` | **runs** a prepared class-1 task |
| `batch_task`, `batch_items`, `batch_history`, `batch_list`, `batch_cancel`, `batch_check` | read tasks, cancel one, check the connection |

Every writing tool's description starts with `PREPARES:` or `WRITES:` and carries the matching MCP
annotations; every answer says whether anything changed. The server prints a warning naming the console it
will change on every start.

**Its profile.** `console_ui_url` is the address the console opens at in a browser — the server builds
links to prepared tasks from it; without it, an API at `…/console-api/api` is taken to serve its screens at
`…/console`. Give it a separate profile with a personal **read-write** token (the batch operations and
the order dictionaries are refused to a read-only one), and only the rights you want it to use:

```json
"actions": {
  "base_url": "https://console.example.com/console-api/api",
  "console_ui_url": "https://console.example.com/console",
  "token": "<personal read-write API token>",
  "warn_orders": 100,
  "warn_entities": 50,
  "show_customer_data": false,
  "secret_sources": {
    "op":    {"type": "1password"},
    "kc":    {"type": "keychain", "service": "gateway-insights"},
    "files": {"type": "dir", "path": "~/.config/gateway-insights/secrets"},
    "corp":  {"type": "command", "argv": ["/usr/local/bin/get-secret", "--name", "{name}"]}
  }
}
```

**Secrets.** Pass a credential as a reference, `source:name` — the server reads it from your store and
sends it to the console; the assistant sees the reference and a fingerprint only. Sources: `1password`
(`op read`), `keychain` (macOS), `pass`, `env`, `dir` (files readable by you only), and `command` — any
command you write into the profile, run without a shell, for every other store. There are no sources by
default. A credential typed into the chat is accepted, with a warning: it has reached the model provider
and the chat history, so issue a new one.

**Warnings, not bans.** The server says, before anything else in its answer, when a task holds more orders
or entities than the profile's thresholds, when many objects are refused, when a search sends a customer's
personal data, when a value looks like a credential, and when a run was not confirmed by you. Customer
names, cardholder and IP are left out of order search results unless `show_customer_data` is on.

**Source lines stay out.** The console keeps each object's source line (a CSV row); for a CSV with credential
columns that line holds the credentials, so the server drops it from every item it reads.

**How a run is admitted.** When `batch_run` is called, the task's class is taken from the task as the
console shows it then; what the server noted when it prepared the task can only raise it, and only if it
was noted for the same console, profile, task id and type. A task this server did not prepare, of a type
whose class depends on its contents, is not run here.

**Journal.** Every preview, run and cancel is written to `actions-journal.jsonl` in the report folder
(readable by you only): task, class, idempotency key, the reads it was built from, secret references —
never a secret value.

## What the servers will not do

- exec and decline: write anything. actions: change data other than through a batch preview, run a task
  that moves money or sets a credential, or read a credential back. No server has a tool that calls an
  arbitrary console method.
- Show a number the console hides from you. Earnings by tier, bank cost, the blueprint rate and money on hold
  follow the console's tariff rights; without them you see «no access» or a refusal, never zero, and nothing
  is computed from other fields to fill the gap. Ranking by a margin you may not see is refused.
- Answer what the console does not know: bank balances and liquidity, plans and forecasts.
- exec and decline: make more than 8 console reads for one question, or more than 3 at a time.
  actions: make more than 120 console calls for one tool call, waiting for a preview and paging
  included.

## Numbers the tools compute themselves

Everything else — totals, rates, deltas, directions, excess declines, z-scores, verdicts — is the console's
own value.

| value | formula |
|---|---|
| change | now − base |
| relative change | (now − base) / \|base\|; `n/a` when the base is 0 (the absolute value keeps «up» meaning up for a negative base) |
| share of gains / falls | a growing entity's change / the sum of all growth; a falling entity's change / the sum of all falls |
| share | an entity's value / the total of the rows it belongs to; `n/a` when the total is ≤ 0 |
| effective rate | margin / approved amount |
| concentration | share of the top 1, 5, 10; HHI = Σ share², over entities with a positive value |
| new / left | new: nothing in the base, something now; left: something in the base, nothing now |
| released within N days | Σ hold released on days before today + N |
| chargeback ratio | chargebacks / approved sales, both counts |
| a trend rate over several buckets | Σ numerator / Σ denominator (not the mean of the rates) |
| decline rate vs reference | segment rate − reference rate, percentage points |
| declines avoidable on a route | (route rate − best route rate) × route transactions — an upper estimate, not a promise |

## Caveats the tools state

- Average ticket = approved amount / all transactions, declines and filtered included.
- Approval rate counts filtered transactions in the denominator.
- Amounts are in the display currency at the latest rate for both periods; not for reconciliation.
- Earnings are summed over all statuses.
- A base of a different length: rates compare, counts and amounts do not.
- Today is incomplete.
- An entity filter you have no list right for is dropped by the console; the result widens.
- `ceo_holds` always reads at least 30 days of the release schedule, so its 30-day line is complete for a
  shorter window too.
- Money on hold by entity is a nightly snapshot; the release schedule reads the dated table.
- Chargebacks and fraud are counted by status-change date; the executive KPIs by bank date.
- Declines are counted by bank date in whole days; «total» includes every status.
- Decline verdicts are the console's heuristic, not a statistical test.

## Reports and themes

Pass `"report": true` to a business tool to also save the answer as an HTML page. The page is self-contained
and inert — no script, nothing loaded from the network — and drawn in the theme set in your profile. The
repository ships a neutral theme; a brand is a theme pack of your own: colours, fonts, logo
([docs/themes.md](docs/themes.md)). The structure every report follows is a public, versioned spec
([docs/report-spec.md](docs/report-spec.md)) any other tool can draw too.

## Security

Every server: a fixed list of console paths in code, a personal API token, TLS always verified, redirects
never followed, every console value masked and escaped and treated as data rather than instructions.
exec and decline are read-only by construction; actions changes data only through batch previews, runs only
reversible tasks, and takes credentials by reference. See [SECURITY.md](SECURITY.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
