#!/usr/bin/env python3
"""PTT MacShop MacBook Air 好價通知器"""

import json
import time
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from datetime import datetime

from scraper import (
    fetch, get_sell_links, scrape_post,
    TARGET_SPECS, SEARCH_QUERIES,
    BASE_URL, BOARD,
)
from config import GMAIL_USER, GMAIL_APP_PASSWORD, NOTIFY_EMAIL

BASE_DIR = Path(__file__).parent
PRICES_FILE = BASE_DIR / "macbook_air_prices.json"
SEEN_FILE   = BASE_DIR / "seen_hrefs.json"
LOG_FILE    = BASE_DIR / "monitor.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def load_medians() -> dict[str, int]:
    """從上次爬取的結果讀取各規格中位數"""
    if not PRICES_FILE.exists():
        return {}
    data = json.loads(PRICES_FILE.read_text(encoding="utf-8"))
    return {s["product"]: s["median_price"] for s in data.get("stats", [])}


def load_seen() -> set[str]:
    if not SEEN_FILE.exists():
        return set()
    return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))


def save_seen(seen: set[str]):
    SEEN_FILE.write_text(json.dumps(list(seen), ensure_ascii=False, indent=2), encoding="utf-8")


def check_new_listings(medians: dict[str, int], seen: set[str]) -> list[dict]:
    """掃描搜尋結果的第一頁，找出價格 <= 中位數的好價商品"""
    good_deals = []

    for chip, query in SEARCH_QUERIES.items():
        encoded_q = query.replace(" ", "+")
        url = f"{BASE_URL}/bbs/{BOARD}/search?q={encoded_q}"
        soup = fetch(url)
        if not soup:
            continue

        links = get_sell_links(soup)
        for title, href in links:
            if href in seen:
                continue

            seen.add(href)
            pairs = scrape_post(href, title)

            for spec_name, price in pairs:
                median = medians.get(spec_name)
                if median and price <= median:
                    good_deals.append({
                        "spec": spec_name,
                        "price": price,
                        "median": median,
                        "diff_pct": round((median - price) / median * 100, 1),
                        "title": title,
                        "url": BASE_URL + href,
                    })
                    logging.info(f"好價！{spec_name} ${price:,} (中位數 ${median:,}) {title[:40]}")

            time.sleep(0.25)
        time.sleep(0.4)

    return good_deals


def send_email(good_deals: list[dict]):
    now = datetime.now().strftime("%Y/%m/%d %H:%M")

    # 純文字版本
    lines = [f"PTT MacShop 好價通知 — {now}\n"]
    for d in good_deals:
        lines.append(
            f"【{d['spec']}】\n"
            f"  售價：${d['price']:,}　中位數：${d['median']:,}　便宜 {d['diff_pct']}%\n"
            f"  標題：{d['title']}\n"
            f"  連結：{d['url']}\n"
        )
    text_body = "\n".join(lines)

    # HTML 版本
    rows = ""
    for d in good_deals:
        rows += (
            f"<tr>"
            f"<td style='padding:6px 12px'>{d['spec']}</td>"
            f"<td style='padding:6px 12px;text-align:right'><b>${d['price']:,}</b></td>"
            f"<td style='padding:6px 12px;text-align:right'>${d['median']:,}</td>"
            f"<td style='padding:6px 12px;text-align:right;color:green'>↓{d['diff_pct']}%</td>"
            f"<td style='padding:6px 12px'><a href='{d['url']}'>{d['title'][:40]}</a></td>"
            f"</tr>"
        )
    html_body = f"""
    <html><body style='font-family:sans-serif'>
    <h2>PTT MacShop 好價通知 — {now}</h2>
    <p>以下商品售價 ≤ 中位數：</p>
    <table border='1' cellspacing='0' style='border-collapse:collapse'>
      <tr style='background:#f0f0f0'>
        <th style='padding:6px 12px'>規格</th>
        <th style='padding:6px 12px'>售價</th>
        <th style='padding:6px 12px'>中位數</th>
        <th style='padding:6px 12px'>差距</th>
        <th style='padding:6px 12px'>文章</th>
      </tr>
      {rows}
    </table>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[PTT好價] {len(good_deals)} 件 MacBook Air 值得關注 — {now}"
    msg["From"] = GMAIL_USER
    msg["To"] = NOTIFY_EMAIL
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        smtp.sendmail(GMAIL_USER, NOTIFY_EMAIL, msg.as_string())

    logging.info(f"通知信已寄出，共 {len(good_deals)} 件好價")
    print(f"通知信已寄出，共 {len(good_deals)} 件好價")


def main():
    logging.info("=== monitor 啟動 ===")
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] 開始掃描...")

    medians = load_medians()
    if not medians:
        logging.warning("找不到歷史價格資料，請先執行 scraper.py")
        print("找不到歷史價格資料，請先執行 scraper.py")
        return

    print(f"中位數基準：")
    for spec, med in medians.items():
        print(f"  {spec}: ${med:,}")

    seen = load_seen()
    good_deals = check_new_listings(medians, seen)
    save_seen(seen)

    if good_deals:
        print(f"\n找到 {len(good_deals)} 件好價，寄送通知...")
        for d in good_deals:
            print(f"  {d['spec']} ${d['price']:,} (↓{d['diff_pct']}%) {d['title'][:45]}")
        send_email(good_deals)
    else:
        print("沒有新的好價商品")
        logging.info("沒有新的好價商品")


if __name__ == "__main__":
    main()
