import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

import requests
from bs4 import BeautifulSoup

PRODUCTS_FILE = Path("products.json")
STATE_FILE = Path("data/prices.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
}

PRICE_KEYS = {"price", "lowPrice", "highPrice", "salePrice", "finalPrice", "amount"}


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def clean_price(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "")
    m = re.search(r"(?:A\$|AUD|\$)?\s*([0-9]+(?:\.[0-9]{1,2})?)", text, flags=re.I)
    if not m:
        return None
    price = float(m.group(1))
    if price <= 0 or price > 100000:
        return None
    return price


def walk_prices(obj) -> List[float]:
    prices = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in PRICE_KEYS:
                p = clean_price(v)
                if p is not None:
                    prices.append(p)
            prices.extend(walk_prices(v))
    elif isinstance(obj, list):
        for item in obj:
            prices.extend(walk_prices(item))
    return prices


def extract_from_json_ld(soup: BeautifulSoup) -> Optional[float]:
    prices = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text(" ")
        try:
            data = json.loads(raw)
        except Exception:
            continue
        prices.extend(walk_prices(data))
    return min(prices) if prices else None


def extract_from_meta(soup: BeautifulSoup) -> Optional[float]:
    candidates = []
    selectors = [
        {"property": "product:price:amount"},
        {"property": "og:price:amount"},
        {"name": "twitter:data1"},
        {"itemprop": "price"},
    ]
    for attrs in selectors:
        tag = soup.find(attrs=attrs)
        if tag:
            p = clean_price(tag.get("content") or tag.get("value") or tag.get_text(" "))
            if p is not None:
                candidates.append(p)
    return min(candidates) if candidates else None


def extract_from_text(html: str) -> Optional[float]:
    # fallback: finds prices near common product price words, avoids huge unrelated numbers
    patterns = [
        r'"price"\s*:\s*"?\$?([0-9]+(?:\.[0-9]{1,2})?)',
        r'"salePrice"\s*:\s*"?\$?([0-9]+(?:\.[0-9]{1,2})?)',
        r'\$\s*([0-9]+(?:\.[0-9]{2}))',
    ]
    found = []
    for pat in patterns:
        for m in re.finditer(pat, html, flags=re.I):
            p = clean_price(m.group(1))
            if p is not None:
                found.append(p)
    # Pick a sensible low price, but avoid tiny values that are often shipping/ratings.
    filtered = [p for p in found if p >= 2]
    return min(filtered) if filtered else None


def fetch_price(url: str) -> Dict[str, Any]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        status = r.status_code
        html = r.text
        if status >= 400:
            return {"price": None, "status": f"HTTP {status}"}
        soup = BeautifulSoup(html, "lxml")
        price = extract_from_json_ld(soup) or extract_from_meta(soup) or extract_from_text(html)
        return {"price": price, "status": "ok" if price is not None else "price_not_found"}
    except Exception as e:
        return {"price": None, "status": f"error: {type(e).__name__}: {e}"}


def send_telegram(message: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram secrets missing. Skipping alert.")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, data={"chat_id": chat_id, "text": message, "disable_web_page_preview": False}, timeout=20)
    if resp.status_code >= 400:
        print("Telegram error:", resp.status_code, resp.text)


def money(p: Optional[float]) -> str:
    return "N/A" if p is None else f"${p:,.2f}"


def main():
    products = load_json(PRODUCTS_FILE, [])
    state = load_json(STATE_FILE, {})
    now = datetime.now(timezone.utc).isoformat()
    alerts = []

    for product in products:
        name = product["name"]
        url = product["url"]
        target = product.get("target_price")
        result = fetch_price(url)
        current = result["price"]
        key = url
        previous = state.get(key, {}).get("price")

        print(f"{name}: current={current}, previous={previous}, status={result['status']}")

        if current is not None:
            if previous is None:
                alerts.append(f"✅ First price detected\n{name}\nCurrent: {money(current)}\n{url}")
            elif current < previous:
                alerts.append(f"🔥 PRICE DROP ALERT\n{name}\nOld: {money(previous)}\nNew: {money(current)}\n{url}")
            elif target is not None and current <= float(target):
                alerts.append(f"🎯 TARGET PRICE HIT\n{name}\nTarget: {money(float(target))}\nCurrent: {money(current)}\n{url}")

        state[key] = {
            "name": name,
            "url": url,
            "price": current,
            "previous_price": previous,
            "status": result["status"],
            "checked_at": now,
        }

    save_json(STATE_FILE, state)

    if alerts:
        send_telegram("\n\n---\n\n".join(alerts))
    else:
        print("No alerts to send.")


if __name__ == "__main__":
    main()
