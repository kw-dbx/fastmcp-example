"""Lightweight FastMCP server that queries a Databricks SQL warehouse
on behalf of the calling user (OBO), via the databricks-sdk WorkspaceClient
Statement Execution API.

The Databricks Apps proxy authenticates the user and forwards their token
in the `x-forwarded-access-token` header. We build a user-scoped
WorkspaceClient from that token, so every statement runs with the caller's
own Unity Catalog grants, never the app's service principal.
"""

import os
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import (
    Disposition,
    ExecuteStatementRequestOnWaitTimeout,
    Format,
    StatementState,
)
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

mcp = FastMCP("databricks-sql-obo")

WAREHOUSE_ID = os.environ["DATABRICKS_WAREHOUSE_ID"]
HOST = os.environ["DATABRICKS_HOST"]  # full https URL, auto-injected

# States a statement can rest in; anything else means still executing.
_TERMINAL = {
    StatementState.SUCCEEDED,
    StatementState.FAILED,
    StatementState.CANCELED,
    StatementState.CLOSED,
}
# Cap total wait comfortably under the Apps proxy's 120s request timeout.
_MAX_WAIT_SECONDS = 100


def _user_client() -> WorkspaceClient:
    """Build a WorkspaceClient scoped to the OBO token forwarded by the proxy."""
    headers = get_http_headers()
    token = headers.get("x-forwarded-access-token")
    if not token:
        raise RuntimeError(
            "No x-forwarded-access-token header. User authorization (OBO) must be "
            "enabled for this app, and the caller must present a Databricks token."
        )
    # auth_type="pat" forces token auth and stops the SDK from also picking up
    # the app SP's injected DATABRICKS_CLIENT_ID/SECRET (which would conflict).
    return WorkspaceClient(host=HOST, token=token, auth_type="pat")


@mcp.tool
def execute_sql(query: str, limit: int = 1000) -> dict:
    """Run a SQL query against the Databricks SQL warehouse as the calling user.

    Args:
        query: The SQL statement to execute.
        limit: Max rows to return (server-side row_limit). Default 1000.

    Returns:
        A dict with `columns`, `rows`, and `row_count`.
    """
    w = _user_client()
    resp = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=query,
        row_limit=limit,
        disposition=Disposition.INLINE,
        format=Format.JSON_ARRAY,
        wait_timeout="30s",
        on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE,
    )

    # Poll if the statement is still running past the initial wait window.
    deadline = time.time() + _MAX_WAIT_SECONDS
    while resp.status and resp.status.state not in _TERMINAL:
        if time.time() > deadline:
            w.statement_execution.cancel_execution(resp.statement_id)
            raise RuntimeError(f"Query cancelled after {_MAX_WAIT_SECONDS}s timeout")
        time.sleep(1)
        resp = w.statement_execution.get_statement(resp.statement_id)

    state = resp.status.state if resp.status else None
    if state != StatementState.SUCCEEDED:
        detail = resp.status.error.message if resp.status and resp.status.error else str(state)
        raise RuntimeError(f"Query {state}: {detail}")

    columns = []
    if resp.manifest and resp.manifest.schema and resp.manifest.schema.columns:
        columns = [c.name for c in resp.manifest.schema.columns]
    rows = resp.result.data_array if (resp.result and resp.result.data_array) else []
    return {"columns": columns, "rows": rows, "row_count": len(rows)}


if __name__ == "__main__":
    port = int(os.environ.get("DATABRICKS_APP_PORT", 8000))
    mcp.run(transport="http", host="0.0.0.0", port=port, path="/mcp")
