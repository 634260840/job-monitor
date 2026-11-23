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
        "link_pattern": r"/job/",
        # 严格过滤：排除非职位的链接（如导航栏、公司主页、投递页等）
        "exclude_href": ["c=search", "keyword=", "company", "comapply", "news", "article"],
    },
]

# 严格过滤：排除不是职位的标题
BAD_TITLE_WORDS = [
    "登录", "注册", "首页", "更多", "搜索", "找工作", "联系我们",
    "返回", "上一页", "下一页", "微信扫一扫", "公众号", "客服",
    "公开招考", "凤还巢", "政策法规", "有限公司", "有限责任公司", "查看详情"
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
    return html

def parse_jobs(src, html):
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    cards = soup.find_all("a", href=True)

    for card in cards:
        href = urljoin(src["url"], card.get("href", ""))
        if not href or href.startswith("javascript") or href == "#":
            continue
        if src.get("link_pattern") and not re.search(src["link_pattern"], href):
            continue
        if any(x in href for x in src.get("exclude_href", [])):
            continue

        title = clean(card.get_text(" ", strip=True))
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
    push_type = os.getenv("PUSH_TYPE", "dingtalk").lower()
    push_url = os.environ.get("PUSH_URL", "").strip()

    if not push_url:
        print("未配置 PUSH_URL，跳过推送：", title, content)
        return

    if push_type == "bark":
        url = push_url.rstrip("/") + "/" + quote(title) + "/" + quote(content)
        requests.get(url, timeout=15)
    elif push_type in ["dingtalk", "wecom"]:
        requests.post(push_url, json={
            "msgtype": "text",
            "text": {"content": f"{title}\n\n{content}"}
        }, timeout=15)
    else:
        requests.post(push_url, json={"title": title, "content": content}, timeout=15)

def main():
    first_run = not SEEN_FILE.exists()
    seen = load_seen()
    all_jobs = []

    for src in SOURCES:
        if not src.get("enabled"):
            continue
        try:
            html = fetch_html(src)
            jobs = parse_jobs(src, html)
            print(f"{src['name']} 过滤后解析到 {len(jobs)} 条有效职位")
            all_jobs.extend(jobs)
        except Exception as e:
            print(f"{src['name']} 抓取失败：{e}")

    all_jobs = list({j["id"]: j for j in all_jobs}.values())
    new_jobs = [j for j in all_jobs if j["id"] not in seen]

    # 如果是第一次运行，或者发现了新岗位
    if first_run or new_jobs:
        jobs_to_push = all_jobs if first_run else new_jobs
        
        # 按你要求的格式拼接成一段文字
        title = "【当前岗位汇总】" if first_run else "【发现新岗位汇总】"
        content = ""
        
        for idx, j in enumerate(jobs_to_push, 1):
            content += f"{idx}. {j['title']} \n来源：{j['source']} \n网址：{j['url']}\n\n"
        
        # 防止消息过长被钉钉拦截（保留前 8000 个字符）
        if len(content) > 8000:
            content = content[:8000] + "\n\n...（文字过长已截断，请去网站查看）"

        push(title, content)

    seen.update(j["id"] for j in all_jobs)
    save_seen(seen)
    print(f"本次处理完毕。总记录 {len(seen)} 条，本次准备推送 {len(new_jobs)} 条。")

if __name__ == "__main__":
    main()
