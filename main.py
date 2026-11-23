import os
import re
import json
import hashlib
from pathlib import Path
from urllib.parse import urljoin, quote

import requests
from bs4 import BeautifulSoup

SEEN_FILE = Path("seen_ids.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0 Safari/537.36"
    )
}

SOURCES = [
    {
        "name": "新沂好就业",
        "url": "https://www.xyrsjjy.com/job/?c=search&keyword=%E7%94%B5%E5%B7%A5",
        "enabled": True,
        "check_auth": False,
        "card_selector": "",
        "link_pattern": r"/job/",
        "exclude_href": ["c=search", "keyword="],
    },
    {
        "name": "钟吾直聘",
        "url": "https://www.zhongwuzhipin.cn/search?pageIndex=1&keyword=%E7%94%B5%E5%B7%A5",
        "enabled": False,
        "check_auth": True,
        "card_selector": "",
        "link_pattern": r"/job/",
        "exclude_href": [],
        "reason": "需要登录和验证码",
    },
    {
        "name": "新沂招聘网",
        "url": "https://www.221400job.com/list.php?searchkey=%E7%94%B5%E5%B7%A5",
        "enabled": False,
        "check_auth": True,
        "card_selector": "",
        "link_pattern": r"/job/",
        "exclude_href": [],
        "reason": "需要短信登录+滑块验证",
    },
]

BAD_TITLE_WORDS = [
    "登录", "注册", "首页", "更多", "搜索", "找工作", "联系我们",
    "返回", "上一页", "下一页", "微信扫一扫", "公众号", "客服",
]

def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def load_seen():
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()

def save_seen(seen):
    SEEN_FILE.write_text(
        json.dumps(sorted(seen), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def fetch_html(src):
    r = requests.get(src["url"], headers=HEADERS, timeout=25)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    html = r.text

    if src.get("check_auth"):
        auth_words = ["拖动滑块完成拼图", "短信验证登录", "扫码登录", "请完成安全验证", "图形验证码"]
        if any(w in html for w in auth_words):
            raise RuntimeError("页面要求登录或验证码")
    return html

def parse_jobs(src, html):
    soup = BeautifulSoup(html, "html.parser")
    jobs = []

    if src.get("card_selector"):
        cards = soup.select(src["card_selector"])
    else:
        cards = soup.find_all("a", href=True)

    for card in cards:
        a = card if card.name == "a" else card.find("a", href=True)
        if not a:
            continue

        href = urljoin(src["url"], a.get("href", ""))
        if not href or href.startswith("javascript") or href == "#":
            continue
        if src.get("link_pattern") and not re.search(src["link_pattern"], href):
            continue
        if any(x in href for x in src.get("exclude_href", [])):
            continue

        title = clean(a.get_text(" ", strip=True))
        if len(title) < 4 or title in ["查看详情", "投递简历", "详情"]:
            title = clean(card.get_text(" ", strip=True))[:100]

        if not title or len(title) < 2:
            continue
        if any(w in title for w in BAD_TITLE_WORDS):
            continue

        title = title[:120]
        jid = hashlib.md5(f"{src['name']}::{href}".encode("utf-8")).hexdigest()

        jobs.append({"id": jid, "source": src["name"], "title": title, "url": href})

    dedup = {j["id"]: j for j in jobs}
    return list(dedup.values())

def push(title: str, content: str):
    push_type = os.getenv("PUSH_TYPE", "pushplus").lower()
    push_url = os.environ.get("PUSH_URL", "").strip()

    if not push_url:
        print("未配置 PUSH_URL，跳过推送：", title, content)
        return

    if push_type == "bark":
        url = push_url.rstrip("/") + "/" + quote(title) + "/" + quote(content)
        requests.get(url, timeout=15)
    elif push_type == "pushplus":
        requests.post(
            "https://www.pushplus.plus/send",
            json={"token": push_url, "title": title, "content": content.replace("\n", "<br>"), "template": "html"},
            timeout=15,
        )
    else:
        requests.post(push_url, json={"title": title, "content": content}, timeout=15)

def main():
    first_run = not SEEN_FILE.exists()
    seen = load_seen()
    all_jobs = []
    errors = []

    for src in SOURCES:
        if not src.get("enabled"):
            continue
        try:
            html = fetch_html(src)
            jobs = parse_jobs(src, html)
            print(f"{src['name']} 解析到 {len(jobs)} 条")
            if not jobs:
                errors.append(f"{src['name']} 解析到0条")
            all_jobs.extend(jobs)
        except Exception as e:
            errors.append(f"{src['name']} 抓取失败：{e}")

    all_jobs = list({j["id"]: j for j in all_jobs}.values())
    new_jobs = [j for j in all_jobs if j["id"] not in seen]

    if first_run:
        seen.update(j["id"] for j in all_jobs)
        save_seen(seen)
        push("招聘监控已启动", f"已记录现有岗位 {len(all_jobs)} 条，之后只推送新岗位。")
        return

    for j in new_jobs[:20]:
        push(f"新岗位：{j['title']}", f"来源：{j['source']}\n{j['url']}")

    seen.update(j["id"] for j in all_jobs)
    save_seen(seen)
    print(f"本次新增 {len(new_jobs)} 条，累计记录 {len(seen)} 条")

if __name__ == "__main__":
    main()
