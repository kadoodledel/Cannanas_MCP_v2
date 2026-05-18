# Cannanas MCP Server

A small MCP server that turns the Cannanas OpenAPI spec into a practical AI-facing interface.

It does three things well:

- search Cannanas operations by tag, method, or free text
- inspect an operation before calling it
- call supported Cannanas endpoints with your API key

It also exposes normalized reporting tools intended for production agents:

- `get_weekly_metrics`
- `get_revenue_summary`
- `get_dispensed_amounts`
- `get_strain_performance`
- `get_category_breakdown`

This project is set up to work locally and to deploy on Alpic.

## What it exposes

- `search_operations`
- `describe_operation`
- `auth_test`
- `call_operation`
- `get_weekly_metrics`
- `get_revenue_summary`
- `get_dispensed_amounts`
- `get_strain_performance`
- `get_category_breakdown`

The server intentionally does not auto-expose every OpenAPI route as its own MCP tool. The Cannanas spec is large, and a curated interface gives LLM clients much better tool selection behavior.

`call_operation` is intentionally hardened for deployment:

- operations must be on the allowlist
- read-only mode is the default
- destructive write endpoints are blocked
- safe report-export endpoints can still be enabled intentionally

## Environment variables

- `CANNANAS_API_KEY`: your Cannanas personal API key
- `CANNANAS_BASE_URL`: defaults to `https://api.cannanas.club`
- `CANNANAS_OPENAPI_PATH`: optional override for the spec file path
- `CANNANAS_TIMEOUT_SECONDS`: defaults to `45`
- `MCP_TRANSPORT`: defaults to `stdio`
- `CANNANAS_PRODUCTION_MODE`: when `true`, fail fast if the API key is missing
- `CANNANAS_READ_ONLY_MODE`: defaults to `true`
- `CANNANAS_ENABLE_SEARCH_OPERATIONS`: defaults to `false` in production mode
- `CANNANAS_ALLOWED_OPERATION_IDS`: optional comma-separated allowlist override
- `CANNANAS_MAX_RETRIES`: defaults to `2`
- `CANNANAS_MAX_PAGES`: defaults to `10`
- `CANNANAS_MAX_RECORDS`: defaults to `1000`

## Local setup

```bash
uv sync
uv run cannanas-mcp
```

To test over HTTP locally instead of stdio:

```bash
MCP_TRANSPORT=streamable-http uv run cannanas-mcp
```

## Alpic deployment

This repository uses a `pyproject.toml` layout, which matches Alpic's default Python build flow.

1. Push this project to GitHub.
2. In Alpic, create a new project and import the repository.
3. Add `CANNANAS_API_KEY` as an environment variable in the target environment.
4. Deploy.

Alpic can run MCP servers from stdio or Streamable HTTP. This server defaults to `stdio`, which Alpic can host behind its public MCP endpoint. If you prefer, set `MCP_TRANSPORT=streamable-http` in Alpic as well.

After deployment, your server will be available on your Alpic endpoint, typically:

- `https://<your-env>.alpic.live/`
- `https://<your-env>.alpic.live/mcp`

## Example usage

Ask an MCP client:

- "Search Cannanas operations for finance reports"
- "Describe the `getClubBatches` operation"
- "Run the Cannanas auth test"
- "Get weekly metrics for club `<club-id>`"
- "Get revenue summary for club `<club-id>` from `2026-05-01` to `2026-05-07`"
- "Get dispensed amounts for club `<club-id>` from `2026-05-01` to `2026-05-07`"

## Deployment checklist

1. Set `CANNANAS_API_KEY`.
2. Verify the repo includes `cannanas-api-docs.yaml`.
3. Start the server and run `auth_test`.
4. Confirm `cannanas://info` lists the expected reporting tools.
5. Confirm the deployment is running with the intended allowlisted operation IDs.
