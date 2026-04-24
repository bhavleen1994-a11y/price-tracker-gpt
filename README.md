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

Use `products.csv`.

This is meant to feel like a simple list, not code.

The columns are:

- `product_name`
- `retailer`
- `url`
- `target_price`

Example:

```csv
product_name,retailer,url,target_price
CeraVe Daily Moisturising Lotion 473ml,Chemist Warehouse,https://www.chemistwarehouse.com.au/buy/91317/cerave-daily-moisturising-lotion-473ml,18.99
CeraVe Daily Moisturising Lotion 473ml,Priceline,https://www.example.com/priceline-cerave-page,18.99
Apple iPhone 17 256GB Lavender,JB Hi-Fi,https://www.jbhifi.com.au/products/apple-iphone-17-256gb-lavender,
```

What to put in each column:

- `product_name`: The product name you want in Telegram
- `retailer`: The store name
- `url`: The direct product page
- `target_price`: Optional. Leave blank if you only want first-detected and price-drop alerts

### The easy way to use it

1. Open `products.csv`
2. Add one row per store page
3. Save the file
4. Run the GitHub Action manually once

If the same product is sold on 3 stores, add 3 rows with the same `product_name` and different `retailer` and `url` values.

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

If `products.csv` exists, the tracker uses that file automatically.

The older `products.json` format still works, but you do not need to use it.

## Files In This Repo

- `tracker.py`: Main scraping and Telegram alert logic
- `products.csv`: The easiest product list for non-developers
- `products.json`: Older advanced product list format
- `requirements.txt`: Python packages used by the tracker
- `.github/workflows/run.yml`: GitHub Actions workflow

## Important

- Keep Telegram secrets in GitHub Secrets only
- Do not put your bot token or chat ID directly into the code
- Some stores change their website structure over time, so occasional scraper updates may still be needed
