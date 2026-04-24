# Price Tracker GPT

Tracks product prices and sends Telegram alerts when a price is first detected, drops, or hits a target price.

## Setup

1. Upload these files to your GitHub repo.
2. In GitHub, go to **Settings → Secrets and variables → Actions → New repository secret**.
3. Add these secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Go to **Actions → Price Tracker → Run workflow**.
5. After the first run, the tracker will run every 6 hours automatically.

## Products

Edit `products.json` to add or remove products.

Optional target price example:

```json
{
  "name": "Example Product",
  "url": "https://example.com/product",
  "target_price": 49.99
}
```
