# Price Tracker GPT

Tracks product prices and sends Telegram alerts when a price is first detected, drops, or hits a target price.

It is designed to run automatically in GitHub Actions, so you do not need to keep your computer on.

## What This Repo Does

- Reads your product list from `products.json`
- Checks each product page in `tracker.py`
- Saves the latest detected prices in `data/prices.json`
- Sends Telegram alerts using GitHub Secrets

## One-Time GitHub Setup

1. Upload these files to your GitHub repository.
2. In GitHub, open your repository.
3. Go to **Settings** -> **Secrets and variables** -> **Actions**.
4. Click **New repository secret** and add:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. Open the **Actions** tab.
6. Open the **Price Tracker** workflow.
7. Click **Run workflow** to test it once.
8. After that, GitHub Actions will run it automatically every 6 hours.

## How To Add Products

Open `products.json` and add or remove products.

Optional target price example:

```json
{
  "name": "Example Product",
  "url": "https://example.com/product",
  "target_price": 49.99
}
```

Notes:

- `name`: The label you want to see in Telegram
- `url`: The product page URL
- `target_price`: Optional. Leave it as `null` if you only want alerts for first detection or price drops

## How Alerts Work

You will get a Telegram message when:

- A product price is detected for the first time
- A tracked price drops
- A product reaches or goes below your `target_price`

## Running The Workflow Manually

If you want to test the tracker any time:

1. Go to your GitHub repository.
2. Click **Actions**.
3. Select **Price Tracker**.
4. Click **Run workflow**.
5. Open the latest run to see the log output.

The logs now show which products succeeded, which failed, and where the price was found.

## Files In This Repo

- `tracker.py`: Main scraping and Telegram alert logic
- `products.json`: Product list to monitor
- `requirements.txt`: Python packages used by the tracker
- `.github/workflows/run.yml`: GitHub Actions workflow

## Important

- Keep Telegram secrets in GitHub Secrets only
- Do not put your bot token or chat ID directly into the code
- Some stores change their website structure over time, so occasional scraper updates may still be needed
