# Cloudflare Service (`cf` CLI)

The `cloudflareAction` node wraps the **official Cloudflare CLI `cf`**
(npm package `cf`, in open beta since 2026-09-28 as "the next version of
Wrangler"; docs at [developers.cloudflare.com/cf](https://developers.cloudflare.com/cf/)).
One self-contained plugin folder at
[`server/nodes/cloudflare/`](../server/nodes/cloudflare/), following the
CLI-managed-auth pattern (stripe -> vercel -> github lineage). Its login
is gh's device-flow shape: cf prints a URL and a one-time code, and the
credentials modal shows them.

| | |
|---|---|
| Node type | `cloudflareAction` (palette group `deployment`, dual-purpose AI tool `cloudflare`) |
| Operations | `whoami` / `search_commands` / `zones_list` / `dns_records_list` / `dns_record_create` / `dns_record_edit` / `dns_record_delete` / `graphql_query` / `custom` |
| CLI pin | `cf@1.0.0-beta.12` (`_NPM_VERSION` in `_install.py`), `bun add`ed into the shared `packages_dir()` tree via `core/js_runtime.add_package` and run on bun; the pin is enforced against the installed `node_modules/cf/package.json` version |
| Auth | Dual-path: cf-owned OAuth device login OR optional canonical `apiKey` field (stored under the provider id `cloudflare`) -> `CLOUDFLARE_API_TOKEN` env. A `cfk_` Global API Key in that field (plus `cloudflare_email`) serves only `graphql_query` |
| Task queue | `TaskQueue.REST_API` |
| Output | `ui_hints = {"outputMode": "terminal"}`; `_shape` contract (parsed JSON -> `result`, text -> `stdout`, never both) |
| Tests | [`server/tests/test_cloudflare_plugin.py`](../server/tests/test_cloudflare_plugin.py) (`pytest --collect-only -q` for the count) |
| Paired skill | [`server/skills/cloudflare/cloudflare-skill/SKILL.md`](../server/skills/cloudflare/cloudflare-skill/SKILL.md) |

## Folder map

```
server/nodes/cloudflare/
├── __init__.py           # register_ws_handlers(WS_HANDLERS) + register_output_schema
├── cloudflare_action.py  # CloudflareActionNode + Params/Output + _run/_failure/_zone_id/_shape + _graphql_post
├── _credentials.py       # CloudflareCredential — resolve() returns the optional token + email rows; prefix-routed _probe
├── _handlers.py          # cloudflare_login (device flow) / cloudflare_logout / cloudflare_status
├── _install.py           # ensure_cf_cli() — pinned, version-enforcing `bun add` (core/js_runtime); system cf NEVER consulted
├── _service.py           # cf_env(token, account_id) / cf_workdir() / login_env() / api_auth_headers() / whoami_snapshot() / stored_token() / stored_email() / resolve_cf_light()
└── meta.json             # {"color": "#F38020"}
```

Icon: `lobehub:Cloudflare` via `visuals.json` (`cloudflareAction` entry
+ lowercase `cloudflare` alias for the tool name — no folder
`icon.svg`). Catalogue entry in `credential_providers.json` (vercel
dual-path shape: optional fields only, never gating the Login button).

## Install — pinned, enforced, never the system PATH

`_install.py` uses vercel's install **mechanism** (`core.js_runtime.add_package`
— `bun add --cwd <packages_dir> <spec>` into the shared tree, the
`node_modules/.bin` shim via `shared_tree_bin("cf")`, `asyncio.to_thread`,
double-checked lock; no `--trust`, since no lifecycle script is needed)
but github's **resolution philosophy**: `shutil.which("cf")` is never
consulted. The command tree is generated from Cloudflare's OpenAPI schema
and drifts between releases, and the argv builders are verified against
the pinned version only.

**The pin is enforced, not just installed once.** An existing shim says
nothing about which version it runs, so `ensure_cf_cli()` compares
`core.js_runtime.installed_version("cf")` with `_NPM_VERSION` and re-runs
`bun add` when they differ; `cf_cli_path()` (the no-install probe the
status/logout handlers use) returns `None` for an older version. A
version change clears the catalogue's login marker (`_forget_cli_login`),
because the new version may not see the old login (1.0 does not migrate
0.2.0's `auth.jsonc`); the login handler's whoami short-circuit re-marks
it at once when the session did survive. `company clean` keeps
`packages/`, so this check is the only thing that moves an existing
install to a new pin.

cf 1.0 depends on miniflare + workerd (for `cf dev` / `--local`): the
first install downloads about 300 MB (0.2.0 was 18 MB).

**Version-bump recipe**: change `_NPM_VERSION`, re-check every wrapped
command with `cf <cmd> --help` and `--dry-run`, adjust argv builders +
tests, update this page and the skill.

### What changed from the old 0.2.0 pin

- Login: device authorization became the default (0.11.0); the PKCE
  loopback flow on `localhost:8877` is `--no-device` only. `--scopes`
  exists (0.4.0); the default grant requests 475 scopes, analytics
  included.
- Auth store moved to `<xdg-config>/cloudflare/config/default.json`
  (`%APPDATA%\xdg.config\cloudflare\...` on Windows); named profiles
  (`cf auth create|activate|list`) exist.
- `cf context` and `cf agent-context` are gone; `cf cli search` and
  `cf schema` replace them.
- `whoami` gained an `accounts` array.
- Telemetry was added (on by default).

## Runtime — bun, delegation, and the working directory

cf declares `engines.node >= 22`; bun 1.4 reports a Node version that
passes the bin's guard, and every API command works on it (verified with
no Node on PATH). Two documented limits follow from running on bun:

- **`cloudflare.config.ts` cannot load under bun** ("Bun is not
  supported" — commands that load it fail with *"cloudflare.config.ts
  loading is not supported on Bun"*). API commands walk up from the
  working directory looking for that file, so ops run from a **neutral
  directory**, `cf_workdir()` = `<DATA_DIR>/cloudflare`. Project
  commands (`init`, `build`, `dev`, `deploy`) need Node 22.18+; the
  bun shim uses `node` when one is on PATH.
- **cf reads its working directory**: API commands load
  `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_ZONE_ID`
  (and a few more) from `.env`, `.env.local`, `.env.<mode>` there (real
  process env wins), and cache the auto-selected account under
  `.cloudflare/cache/`. Running from the server's own cwd would pick up
  whatever `.env` sits there. `custom` runs from the workflow workspace
  instead, so relative paths (`--body @file.json`, `--file`, `--dir`)
  resolve against the workflow's files.

`cf_env()` also sets **`CF_DELEGATION=1`**: `bin/cf` otherwise hands the
command to any other cf copy it can resolve from the working directory —
under bun that includes bun's install cache, even from an empty
directory — so the pinned, verified version might not be the one that
runs. And `CF_SEND_TELEMETRY=false` opts out of cf's usage telemetry
(sibling precedent: vercel's `telemetry disable`, gcloud's
`CLOUDSDK_CORE_DISABLE_USAGE_REPORTING`).

## Auth — CLI-owned OAuth device login

`cf auth login --force --device --no-browser` runs the OAuth 2.0 Device
Authorization Grant (RFC 8628) against `dash.cloudflare.com/oauth2/device/auth`.
cf prints (stderr, source-verified in the 1.0.0-beta.12 bundle):

```
To authorize cf, please visit:

  <verification_uri>

and enter the code:

  <user_code>

You have 5 minutes to approve this request.
```

then polls until the user approves, at most 5 minutes, and exits. The
handler parses the URL + code (`parse_login_banner`) and answers
`{success, url, verification_code}`; `useCredentialPanel` opens the URL
and `OAuthConnect` renders the code (the gh device-flow contract). No
callback server is involved, so the login works from any browser on any
machine. `--no-browser` stops cf from also opening a tab on the server
machine (a duplicate tab on desktop installs, a failure on remote
servers); `--device` pins the flow against a future default change;
`--force` pushes past a stale stored token (the spawn only happens when
whoami says there is no live login).

Handler shape (`_handlers.py`):

1. **Single flight** (`_active_login`: `task`, `proc`, `url`, `code`,
   `install`). While a device flow waits for approval, a repeat click
   returns the same URL + code; while one is starting, it returns
   `pending`.
2. **Install before login.** When `cf_cli_path()` is `None` (missing or
   older version) the install runs as a background task and the login
   waits for it only while enough of the 30 s request window remains for
   cf to print its code (`_RESPONSE_BUDGET_SECONDS` 22 minus
   `_BANNER_DEADLINE_SECONDS` 15); otherwise the click answers `pending`
   and the next click logs in. A login is never spawned after its
   request was answered, so cf never issues a code nobody sees.
3. **whoami short-circuit** — a live login (from a terminal, or a flow
   that completed after the modal closed) marks + broadcasts without a
   spawn.
4. Spawn (`_spawn_login`, `login_env()` + `cf_workdir()`), pumps drain
   both pipes for the process lifetime and store the banner as soon as
   it appears (a click answered "still preparing" picks it up next time).
5. Background `_complete_login` waits for cf to exit (600 s backstop;
   cf gives up after 5 minutes), gates on `whoami_snapshot()`, then the
   marker row + `credential.oauth.connected` broadcast via the shared
   `services/cli_agent/_cli_auth.py`.

**Never kill the login process** (test-locked): the installed binary is
bun's `cf.exe` launcher shim (plus a `cf.bunx` file, never `.cmd`), and
killing it orphans the child bun process running the CLI. The old fixed
port 8877 hazard is gone with the device flow.

**Success gate**: `cf auth whoami` prints
`{authenticated, authSource, tokenValid, email, accounts[], scopes[], expiresAt}`
and **exits 0 in BOTH auth states** (`{"authenticated": false, "error":
"Not logged in"}` when logged out) — exit codes are never consulted.

`login_env()` strips `CLOUDFLARE_API_TOKEN` / `CF_API_TOKEN`: with a
token set, whoami reports the token and `cf auth login` refuses to start
("CLOUDFLARE_API_TOKEN is set and takes precedence"). The stored modal
token is never injected there either (test-locked).

## Credentials — token, Global API Key, and what each reaches

cf's credential order: `CLOUDFLARE_API_TOKEN` (deprecated alias
`CF_API_TOKEN`) -> the OAuth profile. **cf does not accept a Global API
Key** (`allowGlobalAuthKey: false`; the `CLOUDFLARE_API_KEY` +
`CLOUDFLARE_EMAIL` pair is ignored — 0.2.0 behaved the same).

- Optional canonical `apiKey` field (`required: false` — Login gates on
  required fields only, the OAuthConnect invariant; stored under the
  provider id so the base `Credential.validate` scaffold needs no
  storage override). An API token (`cfut_` / `cfat_`) rides every CLI
  call as `CLOUDFLARE_API_TOKEN` and takes precedence over the login. A
  `cfk_` Global API Key is NOT injected into cf (nothing cf reads would
  carry it); with the `cloudflare_email` companion it authenticates the
  direct API calls only.
- `_service.py` routes on Cloudflare's documented prefixes (`cfk_` =
  Global API Key, `cfut_` = user token, `cfat_` = account token):
  - `cf_env(token, account_id)`: tokens -> `CLOUDFLARE_API_TOKEN`;
    `cfk_` -> nothing; `account_id` -> `CLOUDFLARE_ACCOUNT_ID`.
  - `api_auth_headers(key, email)`: `Bearer` for tokens,
    `X-Auth-Email`/`X-Auth-Key` for Global keys (GraphQL, probes).
  - The Validate probe: cfk_ -> `GET /user` with the X-Auth pair (or an
    "add the Account Email" guidance when the companion is missing);
    cfat_ -> authenticated `GET /accounts` read (`/user/tokens/verify`
    is user-token-only); cfut_/legacy -> `/user/tokens/verify`.
- The OAuth login's token never leaves cf (OpenCompany does not read
  cf's profile store), so `graphql_query` needs the panel credential
  even though cf's default grant now includes analytics scopes.

| Surface | OAuth login | API token | Global API Key + email |
|---|---|---|---|
| CLI operations (zones, DNS, Workers, R2, KV, D1, ...) | yes | yes (per token permissions) | **no — cf refuses it** |
| `graphql_query` (zone traffic, RUM, DNS analytics) | **no — token stays in the CLI** | yes (`Account Analytics: Read` / `Zone Analytics: Read`) | yes |

## Accounts and zones

- cf has **no `--account-id` flag** on most commands; the account comes
  from `CLOUDFLARE_ACCOUNT_ID`, then `cloudflare.config.ts`, then a
  cached choice, then the only account. With several accounts a
  non-interactive run fails (*"More than one account available but
  unable to select one in non-interactive mode"*), so the node exposes
  `account_id` (-> `CLOUDFLARE_ACCOUNT_ID`; `zones_list` also passes it
  as its `--account-id` filter) and `_failure` points at it.
- The zone is the global `-z/--zone`. cf passes a 32-hex ID (or UUID)
  through, but resolves a **domain** only after selecting an account and
  only within the first page (20) of that account's zones. The node
  therefore resolves domains itself (`_zone_id`: `zones list --name
  <domain> [--account-id]`, an exact match across every account the
  credential sees) and passes the ID; several matches in different
  accounts ask for `account_id`.

## Operations -> argv (verified against cf@1.0.0-beta.12 `--help` / `--dry-run`)

| Operation | argv / call |
|---|---|
| `whoami` | `auth whoami` |
| `search_commands` | `cli search <search_query>` (local index, no credentials or network) |
| `zones_list` | `zones list [--name <f>] [--account-id <id>] [--page N] [--per-page N]` |
| `dns_records_list` | `dns records list --zone <zone id> [--name <fqdn>] [--type <TYPE>] [--page N] [--per-page N]` |
| `dns_record_create` | `dns records create --zone <zone id> --body <json>` (create/update/edit take the record only as `--body`) |
| `dns_record_edit` | `dns records edit <record_id> --zone <zone id> --body <json>` (PATCH; `update` would overwrite) |
| `dns_record_delete` | `dns records delete <record_id> --zone <zone id> --force` |
| `graphql_query` | POST `client/v4/graphql` (not the CLI) |
| `custom` | `shlex.split(command)`, a leading `cf`/`cloudflare` dropped; runs from the workflow workspace with the custom timeout |

Contract notes:

- **Output**: one pretty-printed JSON document on stdout — the API
  `result`, unwrapped (no `{success, errors, result_info}` envelope);
  a change that returns no data prints nothing; text and binary
  responses (`dns records export`, `r2 objects get`) print raw. Status
  lines and errors go to stderr. There is **no output-format flag**
  (`--json` etc. are rejected — yargs strict mode).
- **No auto-pagination**: list commands return one page and drop
  `result_info`; the node exposes `page` / `per_page`.
- **Destructive commands need `--force`**: without it a non-interactive
  run prints *"(non-interactive; pass --force to confirm)"* and
  *"Aborted."* and **exits 0** having changed nothing. `dns_record_delete`
  always passes `--force` (choosing the operation is the confirmation),
  and `_run` turns that output into a `NodeUserError` for `custom`.
- **Errors** are a box on stderr (`┌ Error │ ... └`; API errors add
  `[<code>] <message>` and `<status> · HTTP <path>`) with exit 1.
  `_run` raises `NodeUserError("cf <command> failed: <stderr tail>")`
  plus a `hint` for known failures: no login / 401 (also
  `requires_user_action=True`, and a note when only a `cfk_` key is
  stored), several accounts, 403 permissions, the bun config-file limit,
  "Zone not found". Timeouts read `cf <command> timed out after Ns`; an
  install failure is a `NodeUserError` too.
- **`custom` guards**: unbalanced quotes are a `NodeUserError`; sign-in
  state changes (`auth login|logout|create|delete|activate|deactivate`,
  `login`) are refused — they belong to the Credentials modal, and a
  logout behind its back would leave the badge stale; `dev` (a
  long-running local server) is refused.
- No auth pre-flight (Stripe-strict): cf's own error surfaces.

## `graphql_query` — the GraphQL Analytics API

The legacy Zone Analytics REST API was deprecated (2021-03-01 in the
current deprecations table) and the DNS analytics REST API is
**removed 2026-12-01**; the official replacement for both is the
GraphQL Analytics API at `api.cloudflare.com/client/v4/graphql`. That
endpoint is **outside the REST OpenAPI schema** cf (and the official
SDKs) are generated from — cf has no `graphql` or raw-request command —
so the node calls it directly: one POST with `{query, variables}`
(`_graphql_post`).

- Requires the stored API token or Global API Key + email (else the
  `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_API_KEY` + `CLOUDFLARE_EMAIL`
  server env); without one the op raises `NodeUserError` with the
  credential guidance and `requires_user_action=True`. 401/403 map to
  the permission fix. Hard GraphQL errors (no `data`) surface as
  `NodeUserError`; partial data rides back in `result`.
- Key datasets: zone traffic `httpRequestsAdaptiveGroups` (under
  `viewer.zones`), Web Analytics/RUM `rumPageloadEventsAdaptiveGroups`
  / `rumPerformanceEventsAdaptiveGroups` /
  `rumWebVitalsEventsAdaptiveGroups` (under `viewer.accounts`), DNS
  `dnsAnalyticsAdaptive(Groups)` (both scopes).
- Limits: 300 queries per 5-minute window; one query spans <= 10 zones
  or 1 account; Adaptive Bit Rate sampling on wide ranges (multiply by
  `sampleInterval`).
- Web Analytics site configuration is a (hidden) cf command group:
  `rum site-info list|get|create|update|delete`, reachable via `custom`.

## Invariants locked by `test_cloudflare_plugin.py`

Pinned `_NPM_SPEC` (never `@latest`); version-enforcing install (older
version -> reinstall + marker cleared; matching -> reused;
`cf_cli_path()` ignores older versions); no `shutil.which("cf")`;
`cf_env` hygiene (`CF_DELEGATION`, telemetry off, `NO_COLOR`), stored
token injected, Global API Key never, `account_id` -> 
`CLOUDFLARE_ACCOUNT_ID`; `login_env` strips the token vars; typed ops
run from `cf_workdir()`, `custom` from the workspace; per-op argv shapes
incl. `--force` on delete, PATCH edit, domain -> zone ID lookup,
filters + paging; failure hints (`requires_user_action` for no login);
"Aborted." is an error; `custom` guards; operation enum == implemented
operations, every operation named in `tool_description`; device-flow
login (URL + code relayed, repeat click returns the same code, install
before any login, whoami short-circuit, **no `.kill(`/`.terminate(` in
the login code**, whoami-gated completion via the shared `_cli_auth`
marker + broadcasts); catalogue = vercel dual-path shape (`apiKey` +
`cloudflare_email`, both optional); prefix-routed probe; GraphQL
endpoint constant + official body shape + 403 credential guidance;
folder assets (no `icon.svg`, `#F38020`, both visuals entries, paired
skill).
