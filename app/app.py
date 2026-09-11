"""Lightweight FastMCP server that queries a Databricks SQL warehouse
on behalf of the calling user (OBO).

The Databricks Apps proxy authenticates the user and forwards their token
in the `x-forwarded-access-token` header. We use that token for the SQL
connection, so every query runs with the caller's own Unity Catalog grants,
never the app's service principal.
"""

import os

from databricks import sql
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

mcp = FastMCP("databricks-sql-obo")

WAREHOUSE_ID = os.environ["DATABRICKS_WAREHOUSE_ID"]
# DATABRICKS_HOST is auto-injected, e.g. https://workspace.cloud.databricks.com
HOSTNAME = os.environ["DATABRICKS_HOST"].replace("https://", "").replace("http://", "").rstrip("/")
HTTP_PATH = f"/sql/1.0/warehouses/{WAREHOUSE_ID}"


def _user_token() -> str:
    """Pull the OBO token forwarded by the Databricks Apps proxy."""
    headers = get_http_headers()
    token = headers.get("x-forwarded-access-token")
    if not token:
        raise RuntimeError(
            "No x-forwarded-access-token header. User authorization (OBO) must be "
            "enabled for this app, and the caller must present a Databricks token."
        )
    return token


@mcp.tool
def execute_sql(query: str, limit: int = 1000) -> dict:
    """Run a SQL query against the Databricks SQL warehouse as the calling user.

    Args:
        query: The SQL statement to execute.
        limit: Max rows to return (applied client-side after fetch). Default 1000.

    Returns:
        A dict with `columns`, `rows`, and `row_count`.
    """
    with sql.connect(
        server_hostname=HOSTNAME,
        http_path=HTTP_PATH,
        access_token=_user_token(),
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            columns = [c[0] for c in cur.description] if cur.description else []
            fetched = cur.fetchmany(limit) if columns else []
            rows = [[_json_safe(v) for v in row] for row in fetched]
    return {"columns": columns, "rows": rows, "row_count": len(rows)}


def _json_safe(value):
    """Coerce non-JSON-native values (Decimal, datetime, bytes) to strings."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


if __name__ == "__main__":
    port = int(os.environ.get("DATABRICKS_APP_PORT", 8000))
    mcp.run(transport="http", host="0.0.0.0", port=port, path="/mcp")
