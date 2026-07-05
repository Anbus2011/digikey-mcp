# CLAUDE.md

## Project Overview

DigiKey MCP Server — a Model Context Protocol server that wraps the DigiKey Product Search API v4 using FastMCP. It provides tools for part lookup, keyword search, pricing, and product details.

## Tech Stack

- Python 3.10+
- FastMCP (MCP server framework)
- Requests (HTTP client)
- python-dotenv (environment variable loading)
- uv (package manager)

## Project Structure

- `digikey_mcp_server.py` — Single-file server containing all MCP tools and DigiKey API logic
- `pyproject.toml` — Project metadata and dependencies
- `uv.lock` — Locked dependency versions
- `api-swagger/` — DigiKey Product Search Swagger/OpenAPI docs for reference

## Key Patterns

- All API calls go through `_make_request()` for consistent error handling and logging
- All DigiKey endpoints use `API_BASE` which switches between sandbox and production via `USE_SANDBOX`
- OAuth2 client credentials flow via `get_access_token()` — the token is cached with expiry tracking (`_token_expires_at`), refreshed proactively when within 30s of expiry, and re-fetched reactively on a 401 (retried once); refresh is guarded by `_token_lock` for thread-safety
- Headers are built by `_get_headers()` including locale and authorization
- Credentials are loaded from `.env` via `load_dotenv()` — never hardcode secrets

## Environment Variables

- `CLIENT_ID` — DigiKey API client ID
- `CLIENT_SECRET` — DigiKey API client secret
- `USE_SANDBOX` — `true` for sandbox API, `false` for production (defaults to `true`)
- `DIGIKEY_ACCOUNT_ID` — optional; sent as `X-DIGIKEY-Account-Id`. Not required for basic 2-legged product lookups; used for customer/MyPricing scenarios

## Commands

- Install: `uv sync`
- Run: `uv run python digikey_mcp_server.py`

## Guidelines

- Do not add hardcoded credentials or log sensitive values (keys, tokens, secrets)
- All new tools should use `_make_request()` and `_get_headers()` for consistency
- Keep the server as a single file unless complexity demands splitting
- DigiKey API docs are in `api-swagger/` — consult them when adding new endpoints
