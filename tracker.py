import json
import os
import re
from urllib.parse import urlparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

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
VISIBLE_PRICE_SELECTORS = [
    '[itemprop="price"]',
    '[data-price]',
    '[data-sale-price]',
    '[data-product-price]',
    '[class*="price"]',
    '[id*="price"]',
]
PRICE_CONTEXT_WORDS = ("price", "now", "sale", "our price", "add to cart", "buy now")
PRICE_IGNORE_WORDS = ("rrp", "was", "save", "off", "afterpay", "zip", "clearance")


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


def parse_jsonish_block(raw: str):
    try:
        return json.loads(raw)
    except Exception:
        return None


def extract_from_json_ld(soup: BeautifulSoup) -> Optional[float]:
    prices = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text(" ")
        data = parse_jsonish_block(raw)
        if data is None:
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


def extract_from_visible_html(soup: BeautifulSoup) -> Optional[float]:
    candidates = []
    for selector in VISIBLE_PRICE_SELECTORS:
        for tag in soup.select(selector):
            text = " ".join(
                part.strip()
                for part in [tag.get("content"), tag.get("value"), tag.get("data-price"), tag.get_text(" ", strip=True)]
                if part
            )
            if not text:
                continue
            lowered = text.lower()
            if any(word in lowered for word in PRICE_IGNORE_WORDS):
                continue
            if "$" not in text and not any(word in lowered for word in PRICE_CONTEXT_WORDS):
                continue
            p = clean_price(text)
            if p is not None:
                candidates.append(p)
    return min(candidates) if candidates else None


def extract_from_embedded_scripts(html: str) -> Optional[float]:
    patterns = [
        r'"price"\s*:\s*"?\$?([0-9]+(?:\.[0-9]{1,2})?)',
        r'"salePrice"\s*:\s*"?\$?([0-9]+(?:\.[0-9]{1,2})?)',
        r'"currentPrice"\s*:\s*"?\$?([0-9]+(?:\.[0-9]{1,2})?)',
        r'"offerPrice"\s*:\s*"?\$?([0-9]+(?:\.[0-9]{1,2})?)',
        r'"price"\s*:\s*\{\s*"value"\s*:\s*"?\$?([0-9]+(?:\.[0-9]{1,2})?)',
    ]
    found = []
    for pat in patterns:
        for match in re.finditer(pat, html, flags=re.I):
            p = clean_price(match.group(1))
            if p is not None:
                found.append(p)
    filtered = [p for p in found if p >= 2]
    return min(filtered) if filtered else None


def extract_chemist_warehouse_price(soup: BeautifulSoup) -> Optional[float]:
    title_tag = soup.find(["h1", "h2"])
    title = title_tag.get_text(" ", strip=True) if title_tag else ""
    visible_text = soup.get_text("\n", strip=True)

    if title:
        title_index = visible_text.find(title)
        if title_index != -1:
            window = visible_text[title_index : title_index + 600]
            prices = []
            for raw_match in re.findall(r"\$\s*[0-9]+(?:\.[0-9]{2})", window):
                p = clean_price(raw_match)
                if p is not None:
                    prices.append(p)
            if prices:
                return prices[0]

    add_to_cart_index = visible_text.lower().find("add to cart")
    if add_to_cart_index != -1:
        window = visible_text[max(0, add_to_cart_index - 300) : add_to_cart_index + 100]
        prices = []
        for raw_match in re.findall(r"\$\s*[0-9]+(?:\.[0-9]{2})", window):
            p = clean_price(raw_match)
            if p is not None:
                prices.append(p)
        if prices:
            return prices[0]

    return None


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


def get_domain(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def extract_price(soup: BeautifulSoup, html: str, url: str) -> Tuple[Optional[float], Optional[str]]:
    domain = get_domain(url)
    extractors = [
        ("json_ld", lambda: extract_from_json_ld(soup)),
        ("meta_tags", lambda: extract_from_meta(soup)),
        ("visible_html", lambda: extract_from_visible_html(soup)),
        ("embedded_scripts", lambda: extract_from_embedded_scripts(html)),
    ]

    if "chemistwarehouse.com.au" in domain:
        extractors.insert(2, ("chemist_warehouse_visible_text", lambda: extract_chemist_warehouse_price(soup)))

    for source, extractor in extractors:
        price = extractor()
        if price is not None:
            return price, source

    fallback_price = extract_from_text(html)
    if fallback_price is not None:
        return fallback_price, "regex_fallback"

    return None, None


def fetch_price(url: str) -> Dict[str, Any]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        status = r.status_code
        html = r.text
        if status >= 400:
            return {"price": None, "source": None, "status": f"HTTP {status}"}
        soup = BeautifulSoup(html, "lxml")
        price, source = extract_price(soup, html, url)
        return {"price": price, "source": source, "status": "ok" if price is not None else "price_not_found"}
    except Exception as e:
        return {"price": None, "source": None, "status": f"error: {type(e).__name__}: {e}"}


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
    success_count = 0
    failure_count = 0

    print(f"Checking {len(products)} products at {now}")

    for index, product in enumerate(products, start=1):
        name = product["name"]
        url = product["url"]
        target = product.get("target_price")
        print(f"[{index}/{len(products)}] Checking: {name}")
        result = fetch_price(url)
        current = result["price"]
        source = result.get("source")
        key = url
        previous = state.get(key, {}).get("price")

        if current is not None:
            success_count += 1
            print(
                f"[SUCCESS] {name} | current={money(current)} | previous={money(previous)} | "
                f"source={source or 'unknown'} | status={result['status']}"
            )
        else:
            failure_count += 1
            print(f"[FAILED] {name} | source={source or 'none'} | status={result['status']} | url={url}")

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
            "source": source,
            "status": result["status"],
            "checked_at": now,
        }

    save_json(STATE_FILE, state)
    print(f"Finished. Success: {success_count}, Failed: {failure_count}, Alerts: {len(alerts)}")

    if alerts:
        send_telegram("\n\n---\n\n".join(alerts))
    else:
        print("No alerts to send.")


if __name__ == "__main__":
    main()
