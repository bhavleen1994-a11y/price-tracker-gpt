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

### Single store product

This is the simple format you are already using:

```json
{
  "name": "Example Product",
  "url": "https://example.com/product",
  "target_price": 49.99
}
```

### Same product on multiple stores

Use `offers` when you want to track the same item across different websites:

```json
{
  "name": "CeraVe Daily Moisturising Lotion 473ml",
  "target_price": 18.99,
  "offers": [
    {
      "retailer": "Chemist Warehouse",
      "url": "https://www.chemistwarehouse.com.au/buy/91317/cerave-daily-moisturising-lotion-473ml"
    },
    {
      "retailer": "Priceline",
      "url": "https://www.example.com/priceline-cerave-page"
    },
    {
      "retailer": "Amazon AU",
      "url": "https://www.example.com/amazon-cerave-page"
    }
  ]
}
```

Notes:

- `name`: The label you want to see in Telegram
- `url`: The product page URL for a single-store item
- `offers`: Use this instead of `url` when one product should be checked on multiple stores
- `retailer`: Optional but recommended when using `offers`, so alerts show which store matched
- `target_price`: Optional. Leave it as `null` if you only want alerts for first detection or price drops. You can set it at the product level or per offer.

### How to add a new product

1. Find the direct product page URL.
2. Copy the product name you want in Telegram.
3. Add a new item in `products.json`.
4. If you want to compare multiple stores, use one `name` with several entries inside `offers`.
5. Run the GitHub Action manually once to test it.

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

For multi-store products, each retailer is checked separately and logged separately.

## Files In This Repo

- `tracker.py`: Main scraping and Telegram alert logic
- `products.json`: Product list to monitor
- `requirements.txt`: Python packages used by the tracker
- `.github/workflows/run.yml`: GitHub Actions workflow

## Important

- Keep Telegram secrets in GitHub Secrets only
- Do not put your bot token or chat ID directly into the code
- Some stores change their website structure over time, so occasional scraper updates may still be needed
