import json
import os
import re
import time
import csv
from urllib.parse import urlparse, urlunparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import requests
from bs4 import BeautifulSoup

PRODUCTS_FILE = Path("products.json")
PRODUCTS_CSV_FILE = Path("products.csv")
STATE_FILE = Path("data/prices.json")
BOT_STATE_FILE = Path("data/bot_state.json")
PERCENT_DROP_ALERT_THRESHOLD = float(os.environ.get("PERCENT_DROP_ALERT_THRESHOLD", "10"))

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
SUPPORTED_RETAILERS = {
    "Chemist Warehouse": "chemistwarehouse.com.au",
    "Priceline": "priceline.com.au",
    "Amazon AU": "amazon.com.au",
    "JB Hi-Fi": "jbhifi.com.au",
    "The Good Guys": "thegoodguys.com.au",
    "Big W": "bigw.com.au",
    "Myer": "myer.com.au",
    "Kmart": "kmart.com.au",
    "eBay": "ebay.com.au",
    "Anaconda": "anacondastores.com",
    "BCF": "bcf.com.au",
    "Kathmandu": "kathmandu.com.au",
    "Columbia": "columbiasportswear.com.au",
    "Harvey Norman": "harveynorman.com.au",
    "Officeworks": "officeworks.com.au",
    "Nike": "nike.com",
    "Adidas": "adidas.com.au",
}


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
            writer.writerow(
                {
                    "product_name": row.get("product_name") or row.get("name") or "",
                    "retailer": row.get("retailer") or "",
                    "url": row.get("url") or "",
                    "target_price": row.get("target_price") if row.get("target_price") is not None else "",
                }
            )


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


def normalize_product_url(url: str) -> str:
    parsed = urlparse(url.strip())
    cleaned = parsed._replace(params="", query="", fragment="")
    return urlunparse(cleaned)


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
        "www.kmart.com.au": "Kmart",
        "www.ebay.com.au": "eBay",
        "www.kathmandu.com.au": "Kathmandu",
        "www.columbiasportswear.com.au": "Columbia",
    }
    if hostname in custom_names:
        return custom_names[hostname]
    host = hostname.replace("www.", "").split(".")[0]
    return host.replace("-", " ").title() if host else "Store"


def tokenize_text(text: str) -> set:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 1}


def similarity_score(left: str, right: str) -> float:
    left_tokens = tokenize_text(left)
    right_tokens = tokenize_text(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = left_tokens & right_tokens
    return len(overlap) / max(len(left_tokens), 1)


def looks_like_product_url(url: str) -> bool:
    lowered = url.lower()
    blocked_parts = ["/search", "/catalogsearch", "/s?", "/w?", "/search?", "/collections", "/brand/"]
    return not any(part in lowered for part in blocked_parts)


def search_store_candidates(product_name: str, retailer: str, domain: str) -> List[Dict[str, str]]:
    query = f'site:{domain} "{product_name}"'
    response = requests.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers=HEADERS,
        timeout=25,
    )
    if response.status_code >= 400:
        print(f"[DISCOVERY-FAILED] {retailer} | search_status=HTTP {response.status_code}")
        return []

    soup = BeautifulSoup(response.text, "lxml")
    candidates = []
    seen_urls = set()
    for link in soup.select("a.result__a, a[data-testid='result-title-a']"):
        href = (link.get("href") or "").strip()
        title = link.get_text(" ", strip=True)
        if not href or domain not in href or href in seen_urls:
            continue
        if not looks_like_product_url(href):
            continue
        score = similarity_score(product_name, title)
        if score < 0.35:
            continue
        seen_urls.add(href)
        candidates.append({"retailer": retailer, "url": href, "title": title, "score": f"{score:.2f}"})
        if len(candidates) >= 2:
            break
    return candidates


def discover_other_store_links(product_name: str, source_url: str, source_retailer: str, existing_urls: set) -> List[Dict[str, str]]:
    discovered = []
    source_domain = get_domain(source_url)

    for retailer, domain in SUPPORTED_RETAILERS.items():
        if retailer == source_retailer:
            continue
        if domain in source_domain:
            continue
        try:
            candidates = search_store_candidates(product_name, retailer, domain)
        except Exception as exc:
            print(f"[DISCOVERY-ERROR] {retailer} | {type(exc).__name__}: {exc}")
            continue

        for candidate in candidates:
            if candidate["url"] in existing_urls:
                continue
            discovered.append(candidate)
            existing_urls.add(candidate["url"])
            break

    return discovered


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
    return "\n".join(lines)


def format_added_message(product_name: str, retailer: str, url: str) -> str:
    return f"Added: {product_name} [{retailer}]"


def format_alert_message(icon: str, label: str, name: str, price_lines: List[str], url: str) -> str:
    lines = [f"{icon} {label}", name]
    lines.extend(price_lines)
    lines.append(url)
    return "\n".join(lines)


def format_failure_summary(failures: List[Dict[str, str]]) -> str:
    if not failures:
        return ""
    lines = ["Unavailable right now:"]
    for item in failures:
        lines.append(f"- {item['name']}")
    return "\n".join(lines)


def format_discovery_message(product_name: str, discovered: List[Dict[str, str]]) -> str:
    if not discovered:
        return ""
    lines = [f"Also found matches for {product_name}:"]
    for item in discovered:
        lines.append(f"- {item['retailer']}")
    return "\n".join(lines)


def format_checked_time(timestamp: str) -> str:
    if not timestamp:
        return "unknown time"
    cleaned = timestamp.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return timestamp


def build_latest_prices_message() -> str:
    state = load_json(STATE_FILE, {})
    if not state:
        return "No saved prices yet. Run the workflow first, then try /list again."

    entries = sorted(
        state.values(),
        key=lambda item: ((item.get("name") or ""), (item.get("retailer") or "")),
    )
    checked_at = max((item.get("checked_at") or "" for item in entries), default="")
    lines = [f"Latest prices ({format_checked_time(checked_at)}):"]

    for index, item in enumerate(entries, start=1):
        name = item.get("name") or "Product"
        price = item.get("price")
        if price is not None:
            lines.append(f"{index}. {name} - {money(price)}")
        else:
            lines.append(f"{index}. {name} - unavailable")

    return "\n".join(lines)


def add_product_from_url(url: str, existing_urls: set, csv_rows: List[Dict[str, str]], notifications: List[str]):
    url = normalize_product_url(url)
    if url in existing_urls:
        notifications.append(f"Already tracking this link:\n{url}")
        return

    try:
        response = fetch_page(url)
        retailer = infer_retailer_name(url)
        html = response.text if response.status_code < 400 else ""
        product_name = infer_product_name(url, html)
        csv_rows.append(
            {
                "product_name": product_name,
                "retailer": retailer,
                "url": url,
                "target_price": "",
            }
        )
        existing_urls.add(url)
        notifications.append(format_added_message(product_name, retailer, url))
        print(f"[ADDED] {product_name} | retailer={retailer} | url={url}")

        if response.status_code >= 400:
            notifications.append(f"Note:\n- {retailer} blocked this product page right now with HTTP {response.status_code}")

        discovered = discover_other_store_links(product_name, url, retailer, existing_urls)
        for item in discovered:
            csv_rows.append(
                {
                    "product_name": product_name,
                    "retailer": item["retailer"],
                    "url": item["url"],
                    "target_price": "",
                }
            )
            print(f"[DISCOVERED] {product_name} | retailer={item['retailer']} | url={item['url']}")

        notifications.append(format_discovery_message(product_name, discovered))
    except Exception as exc:
        notifications.append(f"I could not add this link because of an error:\n{url}\n{type(exc).__name__}: {exc}")


def process_telegram_commands() -> Tuple[List[str], bool]:
    token, chat_id = get_telegram_credentials()
    if not token or not chat_id:
        print("Telegram secrets missing. Skipping Telegram inbox processing.")
        return [], False

    bot_state = load_json(BOT_STATE_FILE, {"last_update_id": 0})
    result = telegram_request("getUpdates", {"offset": bot_state.get("last_update_id", 0) + 1, "timeout": 0})
    updates = result.get("result", []) if result else []
    if not updates:
        return [], False

    csv_rows = load_products_csv(PRODUCTS_CSV_FILE) if PRODUCTS_CSV_FILE.exists() else []
    existing_urls = {row["url"] for row in csv_rows}
    notifications = []
    list_requested = False

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
                "Send me a product link and I will add it automatically.\n\n"
                "Commands:\n"
                "/help - instructions\n"
                "/add <url> - add a product link\n"
                "/list - latest prices\n"
                "/run - check on the next cycle"
            )
            continue

        if lowered.startswith("/list"):
            list_requested = True
            continue

        if lowered.startswith("/run"):
            notifications.append("Okay. I will check again on the next hourly run.")
            continue

        if lowered.startswith("/add"):
            urls = extract_urls_from_text(text)
            if not urls:
                notifications.append("Use /add followed by a full product link.")
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
    return notifications, list_requested


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
    inbox_notifications, list_requested = process_telegram_commands()
    products = normalize_products(load_products())
    state = load_json(STATE_FILE, {})
    now = datetime.now(timezone.utc).isoformat()
    alerts = []
    failures = []
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
            failures.append({"name": name, "status": result["status"], "url": url})

        if current is not None:
            if previous is None:
                alerts.append(format_alert_message("✅", "First price detected", name, [f"Current: {money(current)}"], url))
            elif current < previous:
                drop_pct = ((previous - current) / previous * 100) if previous else 0
                if drop_pct >= PERCENT_DROP_ALERT_THRESHOLD:
                    alerts.append(
                        format_alert_message(
                            "🔥",
                            f"Price dropped {drop_pct:.1f}%",
                            name,
                            [f"Old: {money(previous)}", f"New: {money(current)}"],
                            url,
                        )
                    )
            elif target is not None and current <= float(target):
                alerts.append(
                    format_alert_message(
                        "🎯",
                        "Target price hit",
                        name,
                        [f"Target: {money(float(target))}", f"Current: {money(current)}"],
                        url,
                    )
                )

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
    if list_requested:
        outgoing_messages.append(build_latest_prices_message())
    if alerts:
        outgoing_messages.append("\n\n---\n\n".join(alerts))
    failure_summary = format_failure_summary(failures)
    if failure_summary:
        outgoing_messages.append(failure_summary)

    if outgoing_messages:
        send_telegram("\n\n==========\n\n".join(outgoing_messages))
    else:
        print("No alerts to send.")


if __name__ == "__main__":
    main()
