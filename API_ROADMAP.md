# Toss Securities Open API roadmap

Source of truth:

- `https://openapi.tossinvest.com/openapi-docs/latest/openapi.json`
- 27 paths / 30 operations as of 2026-07-09

## Safety tiers

### Tier 1 — read-only market data

- `GET /api/v1/orderbook` — orderbook
- `GET /api/v1/prices` — latest prices
- `GET /api/v1/trades` — recent trades
- `GET /api/v1/price-limits` — daily price limits
- `GET /api/v1/candles` — candles
- `GET /api/v1/stocks` — stock master data
- `GET /api/v1/stocks/{symbol}/warnings` — trading warnings
- `GET /api/v1/exchange-rate` — exchange rate
- `GET /api/v1/market-calendar/KR` — Korean market calendar
- `GET /api/v1/market-calendar/US` — US market calendar
- `GET /api/v1/rankings` — rankings
- `GET /api/v1/market-indicators/prices` — index prices
- `GET /api/v1/market-indicators/{symbol}/candles` — index candles
- `GET /api/v1/market-indicators/{symbol}/investor-trading` — investor flows

### Tier 2 — read-only private account data

- `GET /api/v1/accounts` — accounts
- `GET /api/v1/holdings` — holdings
- `GET /api/v1/orders` — order history
- `GET /api/v1/orders/{orderId}` — order detail
- `GET /api/v1/conditional-orders` — conditional-order history
- `GET /api/v1/conditional-orders/{conditionalOrderId}` — conditional-order detail
- `GET /api/v1/buying-power` — buying power
- `GET /api/v1/sellable-quantity` — sellable quantity
- `GET /api/v1/commissions` — commissions

### Tier 3 — state-changing trading operations

These must never be called by page load, background refresh, or an AI-generated
suggestion. The UI must require an explicit preview and final user confirmation.

- `POST /api/v1/orders` — create order
- `POST /api/v1/orders/{orderId}/modify` — modify order
- `POST /api/v1/orders/{orderId}/cancel` — cancel order
- `POST /api/v1/conditional-orders` — create conditional order
- `POST /api/v1/conditional-orders/{conditionalOrderId}/modify` — modify conditional order
- `DELETE /api/v1/conditional-orders/{conditionalOrderId}` — cancel conditional order

## Implementation order

1. Harden OAuth/error handling and keep credentials server-only.
2. Add a typed, allow-listed read-only gateway for Tier 1.
3. Add account-aware read endpoints for Tier 2.
4. Build UI modules for quotes, charts, rankings, account, and order history.
5. Add request validation, rate-limit handling, caching, and pagination.
6. Add order previews and explicit confirmations for Tier 3.
7. Add an audit log that excludes tokens, secrets, and full account identifiers.

## Non-negotiable safeguards

- Never expose the access token, client secret, or account identifier to browser JavaScript.
- Never log query strings that could contain account or order identifiers.
- Validate symbols, enums, dates, numeric ranges, pagination, and order payloads.
- Default all trading screens to preview-only.
- Require a separate final confirmation for every state-changing request.
- Prevent duplicate submissions with a client order id and disabled submit state.
- Treat API rate-limit and partial-response failures as visible errors, not empty data.
