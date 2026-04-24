import json
import os
import re
import time
import csv
from urllib.parse import urlparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import requests
from bs4 import BeautifulSoup

PRODUCTS_FILE = Path("products.json")
PRODUCTS_CSV_FILE = Path("products.csv")
STATE_FILE = Path("data/prices.json")
BOT_STATE_FILE = Path("data/bot_state.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
}
CHEMIST_WAREHOUSE_HEADERS = {
    **HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Cache-Control": "max-age=0",
    "Pragma": "no-cache",
    "Referer": "https://www.chemistwarehouse.com.au/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
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
URL_PATTERN = re.compile(r"https?://[^\s<>()\"]+", flags=re.I)


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


def load_products() -> List[Dict[str, Any]]:
    if PRODUCTS_CSV_FILE.exists():
        return load_products_csv(PRODUCTS_CSV_FILE)
    return load_json(PRODUCTS_FILE, [])


def save_products_csv(path: Path, rows: List[Dict[str, Any]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["product_name", "retailer", "url", "target_price"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def load_products_csv(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            clean_row = {str(k).strip(): (v or "").strip() for k, v in row.items() if k}
            if not clean_row.get("product_name") or not clean_row.get("url"):
                continue
            target_price = clean_row.get("target_price")
            rows.append(
                {
                    "name": clean_row["product_name"],
                    "retailer": clean_row.get("retailer") or None,
                    "url": clean_row["url"],
                    "target_price": float(target_price) if target_price else None,
                }
            )
    return rows


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


def build_failure_status(response: requests.Response) -> str:
    title = ""
    if response.text:
        match = re.search(r"<title[^>]*>(.*?)</title>", response.text, flags=re.I | re.S)
        if match:
            title = re.sub(r"\s+", " ", match.group(1)).strip()[:80]
    if title:
        return f"HTTP {response.status_code} ({title})"
    return f"HTTP {response.status_code}"


def fetch_with_session(session: requests.Session, url: str, headers: Dict[str, str], timeout: int = 25) -> requests.Response:
    return session.get(url, headers=headers, timeout=timeout, allow_redirects=True)


def fetch_chemist_warehouse(session: requests.Session, url: str) -> requests.Response:
    variants = [
        ("direct", CHEMIST_WAREHOUSE_HEADERS),
        ("with_homepage_cookie", {**CHEMIST_WAREHOUSE_HEADERS, "Sec-Fetch-Site": "same-origin"}),
        ("cross_site_referer", {**CHEMIST_WAREHOUSE_HEADERS, "Sec-Fetch-Site": "cross-site"}),
    ]

    last_response = None
    for index, (label, headers) in enumerate(variants, start=1):
        if label == "with_homepage_cookie":
            try:
                session.get("https://www.chemistwarehouse.com.au/", headers=CHEMIST_WAREHOUSE_HEADERS, timeout=25)
                time.sleep(1)
            except requests.RequestException:
                pass

        response = fetch_with_session(session, url, headers)
        print(f"[DEBUG] Chemist Warehouse attempt {index}: status={response.status_code} final_url={response.url}")
        last_response = response

        if response.status_code < 400:
            return response
        if response.status_code not in {403, 429}:
            return response
        time.sleep(index)

    return last_response


def fetch_page(url: str) -> requests.Response:
    domain = get_domain(url)
    with requests.Session() as session:
        if "chemistwarehouse.com.au" in domain:
            return fetch_chemist_warehouse(session, url)
        return fetch_with_session(session, url, HEADERS)


def infer_retailer_name(url: str) -> str:
    hostname = get_domain(url)
    custom_names = {
        "www.jbhifi.com.au": "JB Hi-Fi",
        "www.chemistwarehouse.com.au": "Chemist Warehouse",
        "www.kathmandu.com.au": "Kathmandu",
        "www.columbiasportswear.com.au": "Columbia",
    }
    if hostname in custom_names:
        return custom_names[hostname]
    host = hostname.replace("www.", "").split(".")[0]
    return host.replace("-", " ").title() if host else "Store"


def infer_product_name(url: str, html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    meta_title = soup.find("meta", attrs={"property": "og:title"}) or soup.find("meta", attrs={"name": "twitter:title"})
    if meta_title and meta_title.get("content"):
        return meta_title["content"].strip()

    h1 = soup.find("h1")
    if h1:
        text = h1.get_text(" ", strip=True)
        if text:
            return text

    if soup.title and soup.title.string:
        title = re.sub(r"\s+", " ", soup.title.string).strip()
        title = re.split(r"\s+[|\-–]\s+", title)[0].strip()
        if title:
            return title

    path_name = urlparse(url).path.rsplit("/", 1)[-1].replace("-", " ").strip()
    return path_name.title() if path_name else "New Product"


def extract_message_text(update: Dict[str, Any]) -> str:
    message = update.get("message") or update.get("edited_message") or {}
    return (message.get("text") or message.get("caption") or "").strip()


def extract_urls_from_text(text: str) -> List[str]:
    return [match.rstrip(".,)") for match in URL_PATTERN.findall(text)]


def get_telegram_credentials() -> Tuple[Optional[str], Optional[str]]:
    return os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")


def telegram_request(method: str, payload: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    token, _ = get_telegram_credentials()
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/{method}"
    response = requests.post(url, data=payload or {}, timeout=20)
    if response.status_code >= 400:
        print(f"Telegram API error for {method}: {response.status_code} {response.text}")
        return None
    try:
        return response.json()
    except Exception:
        return None


def send_telegram(message: str):
    _, chat_id = get_telegram_credentials()
    if not chat_id:
        print("Telegram secrets missing. Skipping alert.")
        return
    telegram_request(
        "sendMessage",
        {"chat_id": chat_id, "text": message, "disable_web_page_preview": False},
    )


def build_tracked_products_message() -> str:
    rows = load_products_csv(PRODUCTS_CSV_FILE) if PRODUCTS_CSV_FILE.exists() else []
    if not rows:
        return "No products are being tracked yet. Send me a product link and I will add it."

    lines = ["Tracked products:"]
    for index, row in enumerate(rows, start=1):
        retailer = row.get("retailer") or "Store"
        lines.append(f"{index}. {row['name']} [{retailer}]")
        lines.append(row["url"])
    return "\n".join(lines)


def add_product_from_url(url: str, existing_urls: set, csv_rows: List[Dict[str, str]], notifications: List[str]):
    if url in existing_urls:
        notifications.append(f"Already tracking this link:\n{url}")
        return

    try:
        response = fetch_page(url)
        if response.status_code >= 400:
            notifications.append(f"I could not add this link yet because the store returned {response.status_code}:\n{url}")
            return

        product_name = infer_product_name(url, response.text)
        retailer = infer_retailer_name(url)
        csv_rows.append(
            {
                "product_name": product_name,
                "retailer": retailer,
                "url": url,
                "target_price": "",
            }
        )
        existing_urls.add(url)
        notifications.append(f"Added to tracker:\n{product_name}\nStore: {retailer}\n{url}")
        print(f"[ADDED] {product_name} | retailer={retailer} | url={url}")
    except Exception as exc:
        notifications.append(f"I could not add this link because of an error:\n{url}\n{type(exc).__name__}: {exc}")


def process_telegram_commands() -> List[str]:
    token, chat_id = get_telegram_credentials()
    if not token or not chat_id:
        print("Telegram secrets missing. Skipping Telegram inbox processing.")
        return []

    bot_state = load_json(BOT_STATE_FILE, {"last_update_id": 0})
    result = telegram_request("getUpdates", {"offset": bot_state.get("last_update_id", 0) + 1, "timeout": 0})
    updates = result.get("result", []) if result else []
    if not updates:
        return []

    csv_rows = load_products_csv(PRODUCTS_CSV_FILE) if PRODUCTS_CSV_FILE.exists() else []
    existing_urls = {row["url"] for row in csv_rows}
    notifications = []

    for update in updates:
        bot_state["last_update_id"] = max(bot_state.get("last_update_id", 0), update.get("update_id", 0))
        message = update.get("message") or update.get("edited_message") or {}
        sender_chat_id = str(message.get("chat", {}).get("id", ""))
        if sender_chat_id != str(chat_id):
            continue

        text = extract_message_text(update)
        if not text:
            continue

        lowered = text.lower()

        if lowered.startswith("/start") or lowered.startswith("/help"):
            notifications.append(
                "Send me a product link and I will add it to the tracker automatically.\n\n"
                "Commands:\n"
                "/help - show instructions\n"
                "/add <url> - add a product link\n"
                "/list - show tracked products\n"
                "/run - check everything on the next scheduled cycle"
            )
            continue

        if lowered.startswith("/list"):
            notifications.append(build_tracked_products_message())
            continue

        if lowered.startswith("/run"):
            notifications.append("Okay. I will process your tracker on the next automatic run. The workflow now checks every 5 minutes.")
            continue

        if lowered.startswith("/add"):
            urls = extract_urls_from_text(text)
            if not urls:
                notifications.append("Use /add followed by a full product link.\nExample:\n/add https://example.com/product")
                continue
            for url in urls:
                add_product_from_url(url, existing_urls, csv_rows, notifications)
            continue

        urls = extract_urls_from_text(text)
        if not urls:
            notifications.append("I could not find a product link in your message. Send a full product URL.")
            continue

        for url in urls:
            add_product_from_url(url, existing_urls, csv_rows, notifications)

    save_products_csv(PRODUCTS_CSV_FILE, csv_rows)
    save_json(BOT_STATE_FILE, bot_state)
    return notifications


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
        r = fetch_page(url)
        status = r.status_code
        html = r.text
        if status >= 400:
            return {"price": None, "source": None, "status": build_failure_status(r)}
        soup = BeautifulSoup(html, "lxml")
        price, source = extract_price(soup, html, url)
        return {"price": price, "source": source, "status": "ok" if price is not None else "price_not_found"}
    except Exception as e:
        return {"price": None, "source": None, "status": f"error: {type(e).__name__}: {e}"}


def money(p: Optional[float]) -> str:
    return "N/A" if p is None else f"${p:,.2f}"


def build_display_name(product_name: str, retailer: Optional[str]) -> str:
    return f"{product_name} [{retailer}]" if retailer else product_name


def normalize_products(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = []

    for product in products:
        product_name = product["name"]
        product_target = product.get("target_price")
        offers = product.get("offers")

        if offers:
            for offer in offers:
                retailer = offer.get("retailer")
                normalized.append(
                    {
                        "group_name": product_name,
                        "name": build_display_name(product_name, retailer),
                        "retailer": retailer,
                        "url": offer["url"],
                        "target_price": offer.get("target_price", product_target),
                    }
                )
            continue

        normalized.append(
            {
                "group_name": product_name,
                "name": build_display_name(product_name, product.get("retailer")),
                "retailer": product.get("retailer"),
                "url": product["url"],
                "target_price": product_target,
            }
        )

    return normalized


def main():
    inbox_notifications = process_telegram_commands()
    products = normalize_products(load_products())
    state = load_json(STATE_FILE, {})
    now = datetime.now(timezone.utc).isoformat()
    alerts = []
    success_count = 0
    failure_count = 0

    print(f"Checking {len(products)} product offers at {now}")

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
            "group_name": product.get("group_name"),
            "name": name,
            "retailer": product.get("retailer"),
            "url": url,
            "price": current,
            "previous_price": previous,
            "source": source,
            "status": result["status"],
            "checked_at": now,
        }

    save_json(STATE_FILE, state)
    print(f"Finished. Success: {success_count}, Failed: {failure_count}, Alerts: {len(alerts)}")

    outgoing_messages = []
    if inbox_notifications:
        outgoing_messages.append("\n\n---\n\n".join(inbox_notifications))
    if alerts:
        outgoing_messages.append("\n\n---\n\n".join(alerts))

    if outgoing_messages:
        send_telegram("\n\n==========\n\n".join(outgoing_messages))
    else:
        print("No alerts to send.")


if __name__ == "__main__":
    main()
