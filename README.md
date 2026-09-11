# fastmcp-example

A super lightweight [FastMCP](https://github.com/jlowin/fastmcp) server, deployed as a Databricks App, that queries a SQL warehouse **on behalf of the calling user** (OBO). Every query runs with the caller's own Unity Catalog grants, not the app's service principal.

- **Transport:** MCP over streamable-HTTP at `/mcp`
- **Tool:** `execute_sql(query: str, limit: int = 1000)` -> `{columns, rows, row_count}`
- **Auth:** OBO via the `x-forwarded-access-token` header forwarded by the Databricks Apps proxy

## Layout

```
databricks.yml      # DABs bundle: app resource + warehouse binding + user_api_scopes
app/
  app.py            # FastMCP server (one tool)
  app.yaml          # app runtime config (start command + env)
  requirements.txt  # fastmcp, databricks-sdk
test_client.py      # smoke test against the deployed endpoint
```

Only `app/` is uploaded to the app (`source_code_path: ./app`); the README and tests stay out of the deployment.

## Prerequisites

- Databricks CLI `>= 0.292.0`, authenticated: `databricks auth profiles`
- A SQL warehouse (find its id: `databricks warehouses list --profile <PROFILE>`)
- **User authorization (OBO)** enabled for Apps in the workspace. This is a Public Preview a workspace admin must turn on. Without it the app runs but tool calls fail with `user token passthrough not enabled`.

## Deploy

```bash
# 1. deploy (uploads code, applies config). Pass your warehouse id.
databricks bundle deploy -t dev \
  --var="warehouse_id=<WAREHOUSE_ID>" \
  --profile <PROFILE>

# 2. start the app (a bare `bundle deploy` leaves it stopped)
databricks bundle run fastmcp_app -t dev --profile <PROFILE>

# 3. get the URL / status
databricks apps get <APP_NAME> --profile <PROFILE> -o json
```

Override the app name with `--var="app_name=my-name"` (<=26 chars, lowercase/hyphens). An `mcp-` prefix makes the app discoverable in the AI Gateway MCP picker (discovery only; it is not a governed MCP Service).

## Test

```bash
export MCP_URL="https://<app-url>/mcp"
export DBX_TOKEN=$(databricks auth token --profile <PROFILE> | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
pip install "fastmcp>=2.3.0"
python test_client.py
```

Expect `TOOLS: ['execute_sql']` and a result whose `me` column is your own username, confirming OBO.

## Connect a client

An Apps-hosted MCP server sits behind the Databricks OAuth proxy, which means:

- **OAuth tokens only.** PATs (`dapi…`) are rejected with 401. (PATs work only for *managed* MCP servers / MCP Services, never Apps-hosted.)
- **No Dynamic Client Registration.** Databricks disables DCR, so a client cannot self-register; it needs a pre-known client id, or it must reuse an existing OAuth session.

That leaves two supported paths.

### Path A — native OAuth (portable; needs account admin, one-time)

Register a **public** OAuth App Connection so the client has a `client_id` (this is the only reason the app is needed: DCR being off, nothing can mint the id automatically):

- Account Console → **Settings → App Connections → Add connection**
- Redirect URL: `http://localhost:8080/callback`; Client type: **Public** (no secret)

Then point Claude Code at it:

```bash
claude mcp add --transport http --scope user \
  --client-id <CLIENT_ID> --callback-port 8080 \
  dbx-sql https://<app-url>/mcp
# then run /mcp inside Claude Code -> Authenticate
```

This is portable to Cursor, ChatGPT connectors, and teammates (each does their own login).

### Path B — reuse your Databricks CLI OAuth session (no admin; the fallback)

If you can't register an OAuth app, reuse the CLI's already-authenticated, auto-refreshing session via a `headersHelper` script. **Fresh-machine setup, assuming the server already exists:**

```bash
# 1. authenticate the CLI to the workspace (OAuth U2M)
databricks auth login --host https://<workspace-hostname> --profile <PROFILE>

# 2. install the helper and point it at that profile
mkdir -p ~/.claude
cp clients/claude-code/headers-helper.sh ~/.claude/dbx-sql-mcp-headers.sh
chmod +x ~/.claude/dbx-sql-mcp-headers.sh
# edit the PROFILE line in the script (or export DBX_PROFILE=<PROFILE> before launching Claude Code)

# 3. register the server with the helper (add-json, since `add` has no headersHelper flag)
claude mcp add-json --scope user dbx-sql \
  '{"type":"http","url":"https://<app-url>/mcp","headersHelper":"'"$HOME"'/.claude/dbx-sql-mcp-headers.sh"}'

# 4. verify
claude mcp get dbx-sql        # expect: Status: Connected
```

The helper runs on every request, so tokens never go stale as long as the CLI login is valid (`databricks auth login --profile <PROFILE>` to refresh). This route is Claude-Code- and machine-specific and rides the CLI's broad token, so prefer Path A when you can register an app.

## Query engine

Queries run through the `databricks-sdk` `WorkspaceClient.statement_execution` API (the Statement Execution REST API), not the `databricks-sql-connector` driver. The user-scoped client is built with `auth_type="pat"` so the forwarded OBO token is used and the app SP's injected `DATABRICKS_CLIENT_ID`/`SECRET` are ignored (otherwise the SDK errors with "more than one authorization method configured").

Trade-offs vs. the SQL connector:
- Lighter dependency, server-side `row_limit`, and results serialize straight to JSON.
- Values come back as **strings** (`JSON_ARRAY` format), not native types.
- Best for lightweight, bounded result sets. For large analytical result sets, the `databricks-sql-connector` (Arrow / cloud fetch / streaming) is the better tool.

## Notes

- **OBO scopes** (`user_api_scopes: [sql]`) can be wiped by a destructive `apps create-update`. Deploy with `databricks bundle deploy` / `apps deploy`, which preserve them.
- **Renaming** a DABs app is a destroy-and-recreate: new URL, new service principal (the `CAN_USE` warehouse grant re-applies automatically since it is declared here).
