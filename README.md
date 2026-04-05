# DigiKey MCP Server

A Model Context Protocol (MCP) server for DigiKey's Product Search API using FastMCP.

## Requirements

- Python 3.10+
- uv package manager
- DigiKey API credentials (CLIENT_ID and CLIENT_SECRET)

## Setup & Credentials

### 1. Obtain DigiKey API credentials

To use this server, you need a **Client ID** and **Client Secret** from the DigiKey API Developer Portal.

1. Go to the [DigiKey API Developer Portal](https://developer.digikey.com/) and sign in with your **My DigiKey** account. If you don't have one, create a free account first.
2. Navigate to **Apps** in the top menu.
3. Choose one of the following:
   - **For testing:** Click **Create Sandbox App**. This gives you access to the sandbox API with mock data at no cost.
   - **For production:** Set up an **Organization** first (under your account settings), then create a **Production App** within that organization.
4. Fill out the required application details (app name, description, and select the **Product Information** API).
5. Once created, click on your app name to view its properties.
6. Copy the **Client ID** and **Client Secret** shown on the app properties page.

### 2. Install dependencies

```bash
uv sync
```

### 3. Configure environment variables

Create a `.env` file in the project root:

```
CLIENT_ID=your_client_id_here
CLIENT_SECRET=your_client_secret_here
USE_SANDBOX=true
```

| Variable | Description |
|---|---|
| `CLIENT_ID` | Your DigiKey app Client ID |
| `CLIENT_SECRET` | Your DigiKey app Client Secret |
| `USE_SANDBOX` | Set to `true` for the sandbox API (recommended for testing), or `false` for production |

> **Note:** The `.env` file is listed in `.gitignore` and will not be committed to version control. Never share your Client Secret publicly.

### 4. Add the MCP server to Claude Desktop

Open your Claude Desktop config file in a text editor:

| Platform | Config file location |
|----------|---------------------|
| **Windows** | `%APPDATA%\Claude\claude_desktop_config.json` |
| **macOS** | `~/Library/Application Support/Claude/claude_desktop_config.json` |

Add the `"digikey"` entry to the `"mcpServers"` object. Replace the `cwd` path with the full path to your local copy of this repository:

**Windows example:**
```json
{
  "mcpServers": {
    "digikey": {
      "command": "uv",
      "args": ["run", "python", "digikey_mcp_server.py"],
      "cwd": "C:\\Users\\YourName\\projects\\digikey-mcp"
    }
  }
}
```

**macOS / Linux example:**
```json
{
  "mcpServers": {
    "digikey": {
      "command": "uv",
      "args": ["run", "python", "digikey_mcp_server.py"],
      "cwd": "/Users/yourname/projects/digikey-mcp"
    }
  }
}
```

> **Note:** If you already have other MCP servers in your config, merge the `"digikey"` entry into your existing `"mcpServers"` object — do not replace the entire file.

### 5. Restart Claude Desktop

Close and reopen Claude Desktop. The DigiKey tools will now appear in your available tools. You can verify by asking Claude to look up a part number:

> _"Look up part number LM358N on DigiKey"_

**Running standalone (without Claude Desktop):**
```bash
uv run python digikey_mcp_server.py
```

## Available Tools

### Quick Part Lookup
- `part_lookup(part_number, requested_quantity=1, customer_id="0")` - Look up any DigiKey or manufacturer part number and get availability, pricing, and pricing tiers in a single call

### Search Methods
- `keyword_search(keywords, limit=5, manufacturer_id=None, category_id=None, search_options=None, sort_field=None, sort_order="Ascending")` - Search DigiKey products by keyword with sorting and filtering
- `search_manufacturers()` - Get all product manufacturers
- `search_categories()` - Get all product categories
- `search_product_substitutions(product_number, limit=10, search_options=None, exclude_marketplace=False)` - Find substitute products

### Product Details
- `product_details(product_number, manufacturer_id=None, customer_id="0")` - Get detailed product information
- `get_category_by_id(category_id)` - Get specific category details
- `get_product_media(product_number)` - Get product images, documents, and videos
- `get_product_pricing(product_number, customer_id="0", requested_quantity=1)` - Get detailed pricing information
- `get_digi_reel_pricing(product_number, requested_quantity, customer_id="0")` - Get DigiReel pricing

### Sort Options for keyword_search
Available sort fields:
- `Packaging` - Sort by packaging type
- `ProductStatus` - Sort by product status
- `DigiKeyProductNumber` - Sort by DigiKey part number
- `ManufacturerProductNumber` - Sort by manufacturer part number
- `Manufacturer` - Sort by manufacturer name
- `MinimumQuantity` - Sort by minimum order quantity
- `QuantityAvailable` - Sort by available quantity
- `Price` - Sort by price
- `Supplier` - Sort by supplier
- `PriceManufacturerStandardPackage` - Sort by manufacturer standard package price

Sort orders: `Ascending` or `Descending`

### Search Options
Available filters for search methods:
- `LeadFree` - Lead-free products only
- `RoHSCompliant` - RoHS compliant products only
- `InStock` - In-stock products only
- `HasDatasheet` - Products with datasheets
- `HasProductPhoto` - Products with photos
- `Has3DModel` - Products with 3D models
- `NewProduct` - New products only

## Example Usage

The server exposes MCP tools that can be used by MCP clients like Claude Desktop, or programmatically via FastMCP clients.

### Part Lookup (most common use case)
```python
# Look up a part by DigiKey or manufacturer number — returns availability, pricing, and price tiers
part_lookup("296-8875-1-ND")

# Look up with a specific quantity for accurate tier pricing
part_lookup("LM358N", requested_quantity=100)
```

### Search Examples
```python
# Basic keyword search
keyword_search("resistor", limit=10)

# Search with sorting by price (lowest first)
keyword_search("capacitor", limit=5, sort_field="Price", sort_order="Ascending")

# Search with filters
keyword_search("LED", limit=10, search_options="InStock,RoHSCompliant")
```

### Detailed Queries
```python
# Get full product details
product_details("296-8875-1-ND")

# Get pricing separately
get_product_pricing("296-8875-1-ND", requested_quantity=100)
```

 