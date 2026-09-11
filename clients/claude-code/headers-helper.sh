#!/bin/bash
# headersHelper for Claude Code: emit a fresh Authorization header from the
# Databricks CLI's auto-refreshing OAuth session. Output MUST be a JSON object
# of headers on stdout.
#
# Why this exists: an Apps-hosted MCP server accepts only OAuth tokens (not
# PATs), and Databricks disables OAuth Dynamic Client Registration, so Claude
# Code cannot self-register a client. Without account admin to register a static
# OAuth app, reusing the CLI's existing OAuth session is the supported fallback.
#
# Set DBX_PROFILE to the Databricks CLI profile whose login to reuse.
PROFILE="${DBX_PROFILE:-DEFAULT}"
TOKEN=$(databricks auth token --profile "$PROFILE" 2>/dev/null \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
printf '{"Authorization": "Bearer %s"}' "$TOKEN"
