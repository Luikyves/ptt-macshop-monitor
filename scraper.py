#!/usr/bin/env python3
"""PTT MacShop MacBook Air 指定規格價格爬蟲"""

import re
import json
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from collections import defaultdict
from datetime import datetime

BASE_URL = "https://www.ptt.cc"
BOARD = "MacShop"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.ptt.cc/bbs/MacShop/index.html",
}
COOKIES = {"over18": "1"}

def _make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(HEADERS)
    session.cookies.update(COOKIES)
    return session

SESSION = _make_session()

# 目標規格：(chip, ram_gb, storage_gb) -> 顯示名稱
TARGET_SPECS: dict[tuple[str, int, int], str] = {
    ("M1",  8,  256): "MacBook Air M1 8/256",
    ("M1", 16,  512): "MacBook Air M1 16/512",
    ("M2",  8,  256): "MacBook Air M2 8/256",
    ("M2", 16,  512): "MacBook Air M2 16/512",
    ("M1", 16,  512): "MacBook Pro M1 16/512",   # Pro 與 Air 同 key，由搜尋關鍵字區分
    ("M2", 16,  512): "MacBook Pro M2 16/512",
}

# Air / Pro 各自的 key
AIR_SPECS: dict[tuple[str, int, int], str] = {
    ("M1",  8,  256): "MacBook Air M1 8/256",
    ("M1", 16,  512): "MacBook Air M1 16/512",
    ("M2",  8,  256): "MacBook Air M2 8/256",
    ("M2", 16,  512): "MacBook Air M2 16/512",
}

PRO_SPECS: dict[tuple[str, int, int], str] = {
    ("M1", 16, 512): "MacBook Pro M1 16/512",
    ("M2", 16, 512): "MacBook Pro M2 16/512",
}

SELL_TAGS_RE = re.compile(r"^\[(?:販售|出售|售|賣|[Ss])\]")

# 找晶片型號（只抓 M1 / M2，不管 Pro/Max）
CHIP_RE = re.compile(r"MacBook\s*(?:Air|Pro)\s*(M[12])", re.IGNORECASE)

# RAM pattern：支援 8G / 8GB / 8g / 16G / 16GB
RAM_RE = re.compile(r"\b(8|16|24|32)\s*[Gg][Bb]?\b")

# Storage pattern：256G、256GB、512G、512GB、1TB 等
STORAGE_RE = re.compile(r"(?<!\d)(128|256|512|1024|2048)\s*[Gg][Bb]?(?!\d)|\b(1|2)\s*T[Bb]?\b")

# 複合規格 pattern：8/256、16/512、8G/256G、8GB/256GB 等
SPEC_RE = re.compile(r"(?<!\d)(8|16|24|32)\s*[Gg][Bb]?\s*/\s*(128|256|512|1024)\s*[Gg][Bb]?(?!\d)")

# 售價欄位
PRICE_FIELD_RE = re.compile(r"\[售價[^\]]*\](.*?)(?=\[|$)", re.DOTALL)
RAW_PRICE_RE = re.compile(r"(?<!\d)(\d{4,6})(?!\d)")
EXCLUDE_NUMS = {128, 256, 512, 1024, 2048, 8192, 16384}


def is_sell_post(title: str) -> bool:
    return bool(SELL_TAGS_RE.match(title))


def extract_chip(text: str) -> str | None:
    """抓 M1 或 M2"""
    m = CHIP_RE.search(text)
    return m.group(1).upper() if m else None


def extract_ram_storage(text: str) -> tuple[int, int] | None:
    """
    嘗試從文字抓 RAM 和 Storage。
    優先抓複合格式 (8/256、16/512)，再分別找 RAM 和 Storage。
    回傳 (ram_gb, storage_gb) 或 None。
    """
    # 複合格式最可靠
    m = SPEC_RE.search(text)
    if m:
        ram = int(m.group(1))
        stor = int(m.group(2))
        return (ram, stor)

    # 分別找
    ram_m = RAM_RE.search(text)
    stor_m = STORAGE_RE.search(text)
    if ram_m and stor_m:
        ram = int(ram_m.group(1))
        stor_raw = stor_m.group(1) or stor_m.group(2)
        if stor_raw in ("1", "1T", "1TB"):
            stor = 1024
        elif stor_raw in ("2", "2T", "2TB"):
            stor = 2048
        else:
            stor = int(stor_raw.replace(" ", ""))
        return (ram, stor)

    return None


# 編號區塊：找 "1.\n...內容...\n2.\n..." 這種分段格式
NUMBERED_SECTION_RE = re.compile(r"(\d+)[\.、]\s*\n(.*?)(?=\n\d+[\.、]|\[售價|$)", re.DOTALL)
NUMBERED_PRICE_RE = re.compile(r"(\d+)[\.、]\s*(\d{4,6})")


def extract_price(content: str, item_number: int | None = None) -> int | None:
    """
    從文章內容抓售價。
    若提供 item_number，優先在 [售價] 中找對應編號的價格。
    """
    price_block_m = PRICE_FIELD_RE.search(content)
    price_block = price_block_m.group(1) if price_block_m else None

    if item_number is not None and price_block:
        # 嘗試在售價區找 "2. 13500" 或 "2.13500"
        for m in NUMBERED_PRICE_RE.finditer(price_block):
            if int(m.group(1)) == item_number:
                val = int(m.group(2))
                if val not in EXCLUDE_NUMS and 5000 <= val <= 150000:
                    return val

    # fallback：從售價區（或全文）取第一個合理數字
    source = price_block if price_block else content
    for m2 in RAW_PRICE_RE.finditer(source):
        val = int(m2.group(1))
        if val not in EXCLUDE_NUMS and 5000 <= val <= 150000:
            return val
    return None


def find_item_number_for_macbook(content: str) -> int | None:
    """
    在編號列表文章中，找 MacBook Air 出現在第幾項（1, 2, 3...）。
    用於多商品文的售價對應。
    """
    for m in NUMBERED_SECTION_RE.finditer(content):
        num = int(m.group(1))
        section_text = m.group(2)
        if re.search(r"MacBook\s*Air", section_text, re.IGNORECASE):
            return num
    return None


def fetch(url: str) -> BeautifulSoup | None:
    for attempt in range(3):
        try:
            resp = SESSION.get(url, timeout=15)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                print(f"  [錯誤] {url}: {e}")
    return None


def get_sell_links(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """從列表頁取得販售文連結（不限標題內容，交給 scrape_post 精確過濾）"""
    results = []
    for post in soup.select(".r-ent"):
        a = post.select_one(".title a")
        if a and is_sell_post(a.get_text(strip=True)):
            results.append((a.get_text(strip=True), a["href"]))
    return results


def get_search_last_page(soup: BeautifulSoup) -> int:
    """從搜尋結果頁面取得最後一頁的頁碼"""
    max_page = 1
    for a in soup.select(".btn-group-paging a"):
        m = re.search(r"[?&]page=(\d+)", a.get("href", ""))
        if m:
            max_page = max(max_page, int(m.group(1)))
    return max_page


def scrape_post(href: str, title: str, spec_table: dict) -> list[tuple[str, int]]:
    """回傳 [(spec_name, price), ...]，只包含目標規格"""
    soup = fetch(BASE_URL + href)
    if not soup:
        return []

    main = soup.select_one("#main-content")
    if not main:
        return []

    for tag in main.select(".push"):
        tag.decompose()

    content = main.get_text()
    full_text = title + "\n" + content

    chip = extract_chip(full_text)
    if not chip:
        return []

    # 先嘗試在 MacBook 所在段落內找規格（避免讀到其他商品的規格）
    item_num = find_item_number_for_macbook(content)
    if item_num is not None:
        for m in NUMBERED_SECTION_RE.finditer(content):
            if int(m.group(1)) == item_num:
                section_text = m.group(2)
                spec = extract_ram_storage(section_text)
                break
        else:
            spec = extract_ram_storage(full_text)
    else:
        spec = extract_ram_storage(full_text)

    if not spec:
        return []

    ram, storage = spec
    key = (chip, ram, storage)

    if key not in spec_table:
        return []

    price = extract_price(content, item_number=item_num)
    if not price:
        return []

    return [(spec_table[key], price)]


# 搜尋關鍵字 -> 對應的 spec table
SEARCH_QUERIES = {
    "macbook air m1":  AIR_SPECS,
    "macbook air m2":  AIR_SPECS,
    "macbook pro m1":  PRO_SPECS,
    "macbook pro m2":  PRO_SPECS,
}

# 去重用：記錄已處理的文章 href
_seen_hrefs: set[str] = set()


def scrape_search(query: str, spec_table: dict, max_search_pages: int = 50) -> dict[str, list[int]]:
    """用 PTT 搜尋功能爬特定關鍵字的所有頁面"""
    product_data: dict[str, list[int]] = defaultdict(list)
    encoded_q = query.replace(" ", "+")

    first_url = f"{BASE_URL}/bbs/{BOARD}/search?q={encoded_q}"
    soup = fetch(first_url)
    if not soup:
        return {}

    last_page = get_search_last_page(soup)
    total_pages = min(last_page, max_search_pages)
    print(f"  搜尋「{query}」：共 {last_page} 頁，爬取前 {total_pages} 頁")

    post_count = 0
    matched_count = 0

    for page_num in range(1, total_pages + 1):
        page_soup = soup if page_num == 1 else fetch(
            f"{BASE_URL}/bbs/{BOARD}/search?page={page_num}&q={encoded_q}"
        )
        if not page_soup:
            continue

        links = get_sell_links(page_soup)
        for title, href in links:
            if href in _seen_hrefs:
                continue
            _seen_hrefs.add(href)
            post_count += 1
            pairs = scrape_post(href, title, spec_table)
            for spec_name, price in pairs:
                product_data[spec_name].append(price)
                matched_count += 1
                print(f"    [符合] {spec_name} ${price:,}  <- {title[:45]}")
            time.sleep(0.25)

        time.sleep(0.4)

    print(f"  完成：掃描販售文 {post_count} 篇，符合 {matched_count} 筆\n")
    return dict(product_data)


def scrape(max_search_pages: int = 50) -> dict[str, list[int]]:
    all_specs = {**AIR_SPECS, **PRO_SPECS}
    print(f"開始爬取 PTT MacShop（使用搜尋功能）")
    print(f"目標規格：{', '.join(all_specs.values())}\n")

    all_data: dict[str, list[int]] = defaultdict(list)

    for query, spec_table in SEARCH_QUERIES.items():
        print(f"=== 搜尋：{query} ===")
        data = scrape_search(query, spec_table, max_search_pages=max_search_pages)
        for spec, prices in data.items():
            all_data[spec].extend(prices)

    total = sum(len(v) for v in all_data.values())
    print(f"全部完成！共 {total} 筆有效價格資料")
    return dict(all_data)


def compute_stats(product_data: dict[str, list[int]]) -> list[dict]:
    results = []
    for product, prices in sorted(product_data.items()):
        if not prices:
            continue
        prices_sorted = sorted(prices)
        n = len(prices_sorted)
        if n >= 5:
            trim = max(1, n // 10)
            trimmed = prices_sorted[trim:-trim]
        else:
            trimmed = prices_sorted
        results.append({
            "product": product,
            "count": n,
            "avg_price": round(sum(trimmed) / len(trimmed)),
            "median_price": prices_sorted[n // 2],
            "min_price": min(prices_sorted),
            "max_price": max(prices_sorted),
            "prices": prices_sorted,
        })
    return results


def print_summary(stats: list[dict]):
    print("\n" + "=" * 72)
    print(f"{'規格':<22} {'筆數':>4} {'平均價':>8} {'中位數':>8} {'最低':>8} {'最高':>8}")
    print("=" * 72)
    for s in stats:
        print(
            f"{s['product']:<22} {s['count']:>4} "
            f"{s['avg_price']:>8,} {s['median_price']:>8,} "
            f"{s['min_price']:>8,} {s['max_price']:>8,}"
        )
    print("=" * 72)


def main():
    product_data = scrape(max_search_pages=50)

    if not product_data:
        print("沒有爬到任何符合規格的資料")
        return

    stats = compute_stats(product_data)
    print_summary(stats)

    output = {
        "scraped_at": datetime.now().isoformat(),
        "board": BOARD,
        "target_specs": list(TARGET_SPECS.values()),
        "stats": stats,
    }

    output_path = "/Users/royarts/Desktop/ptt_macshop_commodity/macbook_air_prices.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n結果已儲存至 macbook_air_prices.json")


if __name__ == "__main__":
    main()
