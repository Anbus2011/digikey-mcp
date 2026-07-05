import os
import json
import time
import logging
import threading
from fastmcp import FastMCP
from dotenv import load_dotenv
import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
USE_SANDBOX = os.getenv("USE_SANDBOX", "true").lower() == "true"
# Optional: DigiKey account id for 2-legged (client_credentials) calls that
# consume it (e.g. customer/MyPricing). Sent as X-DIGIKEY-Account-Id when set.
DIGIKEY_ACCOUNT_ID = os.getenv("DIGIKEY_ACCOUNT_ID")

# DigiKey OAuth2 token endpoint
if USE_SANDBOX:
    TOKEN_URL = "https://sandbox-api.digikey.com/v1/oauth2/token"
    API_BASE = "https://sandbox-api.digikey.com"
else:
    TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
    API_BASE = "https://api.digikey.com"

# Initialize FastMCP server
mcp = FastMCP("DigiKey MCP Server")

# --- OAuth token state (thread-safe, with expiry tracking) ---
_token_lock = threading.Lock()
_access_token = None
_token_expires_at = 0.0          # time.monotonic() deadline; encodes fetch time + expires_in
REFRESH_MARGIN_SECONDS = 30      # refresh proactively when within 30s of expiry

def _fetch_token():
    """Request a fresh OAuth2 token from DigiKey. Returns (access_token, expires_in)."""
    # Check if credentials are loaded
    if not CLIENT_ID or not CLIENT_SECRET:
        raise ValueError("CLIENT_ID and CLIENT_SECRET must be set in .env file")

    data = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    endpoint = "SANDBOX" if USE_SANDBOX else "PRODUCTION"
    logger.info(f"Requesting token from {endpoint}")
    resp = requests.post(TOKEN_URL, data=data, headers=headers)

    if resp.status_code != 200:
        logger.error(f"OAuth error: {resp.status_code} - {resp.text}")
        resp.raise_for_status()

    logger.info("Successfully obtained access token")
    payload = resp.json()
    return payload["access_token"], payload.get("expires_in", 599)

def get_access_token(force_refresh=False):
    """Return a valid OAuth2 access token, refreshing proactively near expiry.

    Thread-safe. Refreshes when there is no cached token, when the cached token
    is within REFRESH_MARGIN_SECONDS of expiring, or when force_refresh is set
    (used by the reactive 401 retry path).
    """
    global _access_token, _token_expires_at
    with _token_lock:
        now = time.monotonic()
        if force_refresh or _access_token is None or now >= _token_expires_at - REFRESH_MARGIN_SECONDS:
            token, expires_in = _fetch_token()
            _access_token = token
            _token_expires_at = time.monotonic() + expires_in
        return _access_token

# Attempt to warm the token cache at startup (non-fatal)
logger.info("=== STARTING DIGIKEY MCP SERVER ===")
try:
    get_access_token()
    logger.info("=== SERVER READY ===")
except Exception as e:
    logger.warning(f"Could not obtain access token at startup: {e}")
    logger.warning("Server will start, but API calls will fail until valid credentials are configured in .env")

def _get_headers(customer_id: str = "0"):
    """Get standard headers for DigiKey API requests."""
    try:
        token = get_access_token()
    except Exception as e:
        raise ValueError(
            "No valid access token. Please set CLIENT_ID and CLIENT_SECRET in your .env file "
            f"with valid DigiKey API credentials. ({e})"
        )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-DIGIKEY-Client-Id": CLIENT_ID,
        "Content-Type": "application/json",
        "X-DIGIKEY-Locale-Site": "US",
        "X-DIGIKEY-Locale-Language": "en",
        "X-DIGIKEY-Locale-Currency": "USD",
        "X-DIGIKEY-Customer-Id": customer_id,
    }
    # Some 2-legged endpoints (productdetails, pricing, pricingbyquantity,
    # digireelpricing, packagetypebyquantity) consume the account id; harmless
    # elsewhere. Sent on every endpoint (all go through _get_headers) when set.
    if DIGIKEY_ACCOUNT_ID:
        headers["X-DIGIKEY-Account-Id"] = DIGIKEY_ACCOUNT_ID
    return headers

def _send(method: str, url: str, headers: dict, data: dict = None):
    """Perform the actual HTTP request."""
    if method.upper() == "GET":
        return requests.get(url, headers=headers)
    else:
        return requests.post(url, headers=headers, json=data)

def _make_request(method: str, url: str, headers: dict, data: dict = None) -> dict:
    """Make an API request with error handling and logging.

    On a 401 (expired/invalid token), refresh the token and retry exactly once.
    """
    logger.info(f"Making {method} request to {url}")
    logger.debug(f"Headers: {json.dumps({k: v for k, v in headers.items() if 'Authorization' not in k}, indent=2)}")
    if data:
        logger.debug(f"Request body: {json.dumps(data, indent=2)}")

    resp = _send(method, url, headers, data)

    if resp.status_code == 401:
        logger.warning("Received 401 Unauthorized - refreshing token and retrying once")
        fresh_token = get_access_token(force_refresh=True)
        headers = {**headers, "Authorization": f"Bearer {fresh_token}"}
        resp = _send(method, url, headers, data)

    logger.info(f"Response status: {resp.status_code}")
    if resp.status_code != 200:
        logger.error(f"API error: {resp.status_code} - {resp.text}")
        resp.raise_for_status()

    return resp.json()

def _fetch_product_details(part_number: str, headers: dict, manufacturer_id: str = None) -> dict:
    """GET /search/{pn}/productdetails, with optional manufacturerId disambiguation."""
    url = f"{API_BASE}/products/v4/search/{part_number}/productdetails"
    if manufacturer_id:
        url += f"?manufacturerId={manufacturer_id}"
    return _make_request("GET", url, headers)

def _fetch_pricing_by_quantity(part_number: str, requested_quantity: int, headers: dict, manufacturer_id: str = None) -> dict:
    """GET /search/{pn}/pricingbyquantity/{qty} (quantity is a PATH segment),
    with optional manufacturerId disambiguation."""
    url = f"{API_BASE}/products/v4/search/{part_number}/pricingbyquantity/{requested_quantity}"
    if manufacturer_id:
        url += f"?manufacturerId={manufacturer_id}"
    return _make_request("GET", url, headers)

@mcp.tool()
def keyword_search(keywords: str, limit: int = 5, manufacturer_id: str = None, category_id: str = None, search_options: str = None, sort_field: str = None, sort_order: str = "Ascending"):
    """Search DigiKey products by keyword.
    
    Args:
        keywords: Search terms or part numbers
        limit: Maximum number of results (default: 5)
        manufacturer_id: Filter by specific manufacturer ID
        category_id: Filter by specific category ID  
        search_options: Comma-delimited filters like LeadFree,RoHSCompliant,InStock
        sort_field: Field to sort by. Options: None, Packaging, ProductStatus, DigiKeyProductNumber, ManufacturerProductNumber, Manufacturer, MinimumQuantity, QuantityAvailable, Price, Supplier, PriceManufacturerStandardPackage
        sort_order: Sort direction - Ascending or Descending (default: Ascending)
    """
    url = f"{API_BASE}/products/v4/search/keyword"
    headers = _get_headers()
    
    body = {
        "Keywords": keywords,
        "Limit": limit
    }
    
    if manufacturer_id:
        body["ManufacturerId"] = manufacturer_id
    if category_id:
        body["CategoryId"] = category_id
    if search_options:
        body["SearchOptionList"] = search_options.split(",")
    
    # Add sort options if specified
    if sort_field:
        body["SortOptions"] = {
            "Field": sort_field,
            "SortOrder": sort_order
        }
    
    return _make_request("POST", url, headers, body)

@mcp.tool()
def product_details(product_number: str, manufacturer_id: str = None, customer_id: str = "0"):
    """Get detailed information for a specific product.
    
    Args:
        product_number: DigiKey or manufacturer part number
        manufacturer_id: Optional manufacturer ID for disambiguation
        customer_id: Customer ID for pricing (default: "0")
    """
    headers = _get_headers(customer_id)
    return _fetch_product_details(product_number, headers, manufacturer_id=manufacturer_id)

@mcp.tool()
def search_manufacturers():
    """Search and retrieve all product manufacturers."""
    url = f"{API_BASE}/products/v4/search/manufacturers"
    headers = _get_headers()
    return _make_request("GET", url, headers)

@mcp.tool()
def search_categories():
    """Search and retrieve all product categories."""
    url = f"{API_BASE}/products/v4/search/categories"
    headers = _get_headers()
    return _make_request("GET", url, headers)

@mcp.tool()
def get_category_by_id(category_id: int):
    """Get specific category details by ID.
    
    Args:
        category_id: The category ID to retrieve
    """
    url = f"{API_BASE}/products/v4/search/categories/{category_id}"
    headers = _get_headers()
    return _make_request("GET", url, headers)

@mcp.tool()
def search_product_substitutions(product_number: str, limit: int = 10, search_options: str = None, exclude_marketplace: bool = False):
    """Search for product substitutions for a given product.
    
    Args:
        product_number: The product to get substitutions for
        limit: Number of substitutions (default: 10)
        search_options: Filters like LeadFree,RoHSCompliant,InStock
        exclude_marketplace: Exclude marketplace products (default: False)
    """
    url = f"{API_BASE}/products/v4/search/{product_number}/substitutions"
    headers = _get_headers()
    
    params = {"limit": limit, "excludeMarketPlaceProducts": exclude_marketplace}
    if search_options:
        params["searchOptionList"] = search_options
    
    url += "?" + "&".join([f"{k}={v}" for k, v in params.items()])
    return _make_request("GET", url, headers)

@mcp.tool()
def get_product_media(product_number: str):
    """Get media (images, documents, videos) for a product.
    
    Args:
        product_number: The product to get media for
    """
    url = f"{API_BASE}/products/v4/search/{product_number}/media"
    headers = _get_headers()
    return _make_request("GET", url, headers)

@mcp.tool()
def get_product_pricing(product_number: str, customer_id: str = "0", requested_quantity: int = 1):
    """Get detailed pricing information for a product.
    
    Args:
        product_number: The product to get pricing for
        customer_id: Customer ID for pricing (default: "0")
        requested_quantity: Quantity for pricing calculation (default: 1)
    """
    headers = _get_headers(customer_id)
    return _fetch_pricing_by_quantity(product_number, requested_quantity, headers)

@mcp.tool()
def get_digi_reel_pricing(product_number: str, requested_quantity: int, customer_id: str = "0"):
    """Get DigiReel pricing for a product.
    
    Args:
        product_number: DigiKey product number (must be DigiReel compatible)
        requested_quantity: Quantity for DigiReel pricing
        customer_id: Customer ID for pricing (default: "0")
    """
    url = f"{API_BASE}/products/v4/search/{product_number}/digireelpricing"
    headers = _get_headers(customer_id)
    
    params = {"requestedQuantity": requested_quantity}
    url += "?" + "&".join([f"{k}={v}" for k, v in params.items()])
    
    return _make_request("GET", url, headers)


def _price_break_at_qty(standard_pricing, requested_quantity, moq):
    """Unit price for requested_quantity from a variation's StandardPricing.

    Selects the price break with the largest BreakQuantity <= requested_quantity.
    If the requested quantity is below the smallest break or below MOQ, returns
    the smallest break's price with is_estimate=True (rendered with an asterisk).
    Returns (unit_price, is_estimate); unit_price is None when there is no pricing.
    """
    breaks = sorted(
        (b for b in (standard_pricing or [])
         if b.get("BreakQuantity") is not None and b.get("UnitPrice") is not None),
        key=lambda b: b["BreakQuantity"],
    )
    if not breaks:
        return None, False
    applicable = [b for b in breaks if b["BreakQuantity"] <= requested_quantity]
    below_smallest = not applicable
    below_moq = moq is not None and requested_quantity < moq
    if below_smallest or below_moq:
        return breaks[0]["UnitPrice"], True
    return applicable[-1]["UnitPrice"], False


def _best_variation(product, requested_quantity):
    """Pick the ProductVariation with the lowest unit price at requested_quantity.

    Returns (variation, unit_price, is_estimate). Falls back to the first
    variation with a None price when none of them carry pricing.
    """
    variations = product.get("ProductVariations") or []
    best = None  # (unit_price, is_estimate, variation)
    for v in variations:
        unit_price, is_estimate = _price_break_at_qty(
            v.get("StandardPricing"), requested_quantity, v.get("MinimumOrderQuantity")
        )
        if unit_price is None:
            continue
        if best is None or unit_price < best[0]:
            best = (unit_price, is_estimate, v)
    if best is not None:
        return best[2], best[0], best[1]
    return (variations[0] if variations else {}), None, False


def _format_manufacturer_comparison(part_number, requested_quantity, matches):
    """Render duplicate-MPN keyword ExactMatches as a Markdown comparison table.

    Pricing varies widely between manufacturers of the same MPN, so this compares
    them side by side (cheapest in-stock first) instead of dumping raw JSON.
    """
    def val(v, fallback="N/A"):
        return fallback if v is None or v == "" else v

    mpn = (matches[0].get("ManufacturerProductNumber") if matches else None) or part_number

    rows = []
    for m in matches:
        variation, unit_price, is_estimate = _best_variation(m, requested_quantity)

        dk_pn = variation.get("DigiKeyProductNumber")
        pkg = (variation.get("PackageType") or {}).get("Name")
        dk_pn_cell = f"{dk_pn} ({pkg})" if (dk_pn and pkg) else val(dk_pn)

        qty_available = m.get("QuantityAvailable")
        has_qty = isinstance(qty_available, (int, float))
        zero_stock = not (has_qty and qty_available > 0)
        notes = []
        if zero_stock:
            notes.append("no stock")
        if m.get("BackOrderNotAllowed"):
            notes.append("no backorder")
        stock_cell = f"{qty_available:,}" if has_qty else val(qty_available)
        if notes:
            stock_cell += " (" + ", ".join(notes) + ")"

        lead = m.get("ManufacturerLeadWeeks")
        lead_cell = f"{lead} weeks" if lead not in (None, "") else "N/A"

        if unit_price is not None:
            up_cell = f"${unit_price:,.5f}" + ("*" if is_estimate else "")
            ext_cell = f"${unit_price * requested_quantity:,.2f}"
        else:
            up_cell = "N/A"
            ext_cell = "N/A"

        rows.append({
            "mfr": val((m.get("Manufacturer") or {}).get("Name")),
            "dk_pn": val(dk_pn),
            "dk_pn_cell": dk_pn_cell,
            "status": val((m.get("ProductStatus") or {}).get("Status")),
            "stock_cell": stock_cell,
            "moq": val(variation.get("MinimumOrderQuantity")),
            "lead_cell": lead_cell,
            "up_cell": up_cell,
            "ext_cell": ext_cell,
            "unit_price": unit_price,
            "zero_stock": zero_stock,
            "is_estimate": is_estimate,
        })

    # Cheapest first, but zero-stock parts always last regardless of price.
    rows.sort(key=lambda r: (r["zero_stock"], r["unit_price"] if r["unit_price"] is not None else float("inf")))

    lines = []
    lines.append(
        f"Multiple manufacturers found for {mpn} — comparing {len(rows)} exact matches "
        f"at quantity {requested_quantity}:"
    )
    lines.append("")
    lines.append("| Manufacturer | DigiKey PN | Status | In Stock | MOQ | Lead Time | Unit Price @ Qty | Ext. Price @ Qty |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        lines.append(
            f"| {r['mfr']} | {r['dk_pn_cell']} | {r['status']} | {r['stock_cell']} | "
            f"{r['moq']} | {r['lead_cell']} | {r['up_cell']} | {r['ext_cell']} |"
        )
    lines.append("")
    if any(r["is_estimate"] for r in rows):
        lines.append(
            "_* unit price shown is the smallest price break; requested quantity is "
            "below the smallest break or the minimum order quantity._"
        )
        lines.append("")
    for r in rows:
        lines.append(f"{r['mfr']}: re-run part_lookup with '{r['dk_pn']}' for full details")

    return "\n".join(lines)


@mcp.tool()
def part_lookup(part_number: str, requested_quantity: int = 1, customer_id: str = "0"):
    """Look up a part by DigiKey or manufacturer part number and return availability, pricing, and pricing tiers as formatted Markdown.

    Args:
        part_number: DigiKey part number or manufacturer part number
        requested_quantity: Quantity for pricing calculation (default: 1)
        customer_id: Customer ID for pricing (default: "0")
    """
    headers = _get_headers(customer_id)

    try:
        # Product details (availability, specs, description). The ProductDetails
        # response nests the product under a "Product" key.
        details = _fetch_product_details(part_number, headers)
        product = details.get("Product") or {}
        manufacturer = product.get("Manufacturer") or {}

        # Full pricing options for the requested quantity. Pass the resolved
        # manufacturer id so multi-manufacturer MPNs price unambiguously.
        pricing = _fetch_pricing_by_quantity(
            part_number, requested_quantity, headers,
            manufacturer_id=manufacturer.get("Id"),
        )
    except requests.exceptions.HTTPError as e:
        # Ambiguous ("Duplicate Products ... provide manufacturerId") or not found:
        # fall back to keyword search and render a Markdown comparison of the
        # exact matches so pricing across manufacturers is easy to compare.
        logger.warning(f"Direct lookup failed for {part_number} ({e}); falling back to keyword search")
        kw_url = f"{API_BASE}/products/v4/search/keyword"
        kw = _make_request("POST", kw_url, headers, {"Keywords": part_number, "Limit": 10})
        matches = kw.get("ExactMatches", [])
        if not matches:
            return f"No exact matches found for {part_number}."
        return _format_manufacturer_comparison(part_number, requested_quantity, matches)

    def val(value, fallback="N/A"):
        if value is None or value == "":
            return fallback
        return value

    # Extract fields from the nested Product object. DigiKey part number and MOQ
    # live on the first ProductVariation.
    variations = product.get("ProductVariations") or []
    first_variation = variations[0] if variations else {}

    dk_pn = val(first_variation.get("DigiKeyProductNumber"))
    mfr = val(manufacturer.get("Name"))
    mfr_pn = val(product.get("ManufacturerProductNumber"))
    desc = val((product.get("Description") or {}).get("ProductDescription"))
    detailed_desc = val((product.get("Description") or {}).get("DetailedDescription"))
    status = val((product.get("ProductStatus") or {}).get("Status"))
    datasheet = product.get("DatasheetUrl")
    product_url = product.get("ProductUrl")
    qty_available = val(product.get("QuantityAvailable"))
    lead_weeks = val(product.get("ManufacturerLeadWeeks"))
    moq = val(first_variation.get("MinimumOrderQuantity"))

    # Build Markdown output
    lines = []

    lines.append(f"## {dk_pn} — {mfr}")
    lines.append("")

    lines.append("### Product Details")
    lines.append(f"- **Manufacturer Part Number:** {mfr_pn}")
    lines.append(f"- **Description:** {desc}")
    if detailed_desc and detailed_desc != "N/A":
        lines.append(f"- **Detailed Description:** {detailed_desc}")
    lines.append(f"- **Product Status:** {status}")
    if datasheet:
        lines.append(f"- **Datasheet:** [{datasheet}]({datasheet})")
    else:
        lines.append("- **Datasheet:** N/A")
    if product_url:
        lines.append(f"- **Product Page:** [{product_url}]({product_url})")
    lines.append("")

    lines.append("### Availability")
    lines.append(f"- **Quantity In Stock:** {qty_available}")
    lines.append(f"- **Manufacturer Lead Time:** {lead_weeks} weeks" if lead_weeks != "N/A" else "- **Manufacturer Lead Time:** N/A")
    lines.append(f"- **Minimum Order Quantity:** {moq}")
    lines.append("")

    lines.append("### Pricing")
    # pricingbyquantity returns StandardPricingOptions: up to four pricing sets
    # (Exact, MinimumOrderQuantity, MaxOrderQuantity, BetterValue), each priced
    # at a total quantity with per-product unit pricing.
    options = pricing.get("StandardPricingOptions") or []
    if options:
        lines.append(f"_Requested quantity: {requested_quantity}_")
        lines.append("")
        lines.append("| Break Quantity | Unit Price | Total Price |")
        lines.append("|---------------:|------------|-------------|")
        for opt in options:
            bq = val(opt.get("TotalQuantityPriced"))
            tp = opt.get("TotalPrice")
            products = opt.get("Products") or []
            up = products[0].get("UnitPrice") if products else None
            if up is None and tp is not None and isinstance(bq, (int, float)) and bq:
                up = tp / bq
            up_str = f"${up:,.5f}" if up is not None else "N/A"
            tp_str = f"${tp:,.2f}" if tp is not None else "N/A"
            lines.append(f"| {bq} | {up_str} | {tp_str} |")
    else:
        lines.append("No pricing tiers available.")
    lines.append("")

    return "\n".join(lines)


def main():
    mcp.run()

if __name__ == "__main__":
    main() 