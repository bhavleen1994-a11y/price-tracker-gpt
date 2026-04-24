# Price Tracker GPT

Tracks product prices and sends Telegram alerts when a price is first detected, drops, or hits a target price.

It is designed to run automatically in GitHub Actions, so you do not need to keep your computer on.

## What This Repo Does

- Reads product links you send to the bot in Telegram
- Checks each product page in `tracker.py`
- Saves the latest detected prices in `data/prices.json`
- Sends Telegram alerts using GitHub Secrets
- Saves Telegram bot inbox progress in `data/bot_state.json`

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
8. After that, GitHub Actions will run it automatically every hour.

## How To Use It

Everything can be done through Telegram.

### Add a product

1. Open Telegram
2. Message your bot with a product URL
3. Wait for the next GitHub Actions run, or run it manually
4. The bot will:
   - read your message
   - detect the URL
   - guess the product name
   - add it to the tracker
   - start monitoring it automatically

Example message:

```text
https://www.jbhifi.com.au/products/apple-iphone-17-256gb-lavender
```

### Telegram commands

- `/help` shows what the bot can do
- `/add <url>` adds a product link
- `/list` shows the products currently being tracked
- `/run` tells you the tracker will check again on the next automatic cycle

You do not need to edit JSON or spreadsheet files yourself.

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

The automatic workflow now runs every hour. If you want an immediate check, you can still run the workflow manually from GitHub Actions.

## Files In This Repo

- `tracker.py`: Main scraping and Telegram alert logic
- `requirements.txt`: Python packages used by the tracker
- `.github/workflows/run.yml`: GitHub Actions workflow
- `data/bot_state.json`: Remembers which Telegram messages have already been processed

## Important

- Keep Telegram secrets in GitHub Secrets only
- Do not put your bot token or chat ID directly into the code
- Some stores change their website structure over time, so occasional scraper updates may still be needed
