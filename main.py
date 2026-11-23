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
        "name": "新沂好就业-电工",
        "url": "https://www.xyrsjjy.com/job/?c=search&keyword=%E7%94%B5%E5%B7%A5",
        "enabled": True,
    },
]

# 只过滤导航类，不要把正常岗位误杀
BAD_TITLE_WORDS = [
    "登录", "注册", "首页", "更多", "搜索", "联系我们",
    "返回", "上一页", "下一页", "微信扫一扫", "公众号", "客服"
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


def fetch_html(url):
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    return r.text


def parse_jobs(source_name, html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    jobs = []

    # 适配这个站点：先找岗位列表区域，根据页面实际结构抓取岗位卡片链接
    job_items = soup.select("a[href*='job']")
    for a in job_items:
        href = a.get("href", "").strip()
        if not href or href.startswith(("javascript:", "#")):
            continue
        full_url = urljoin(base_url, href)

        # 排除搜索、公司、新闻等无关链接
        skip_patterns = ["c=search", "keyword=", "company", "comapply", "news", "article"]
        if any(p in full_url for p in skip_patterns):
            continue

        title = clean(a.get_text())
        if len(title) < 2:
            continue
        if any(w in title for w in BAD_TITLE_WORDS):
            continue

        # md5 去重标识
        jid = hashlib.md5(f"{source_name}::{full_url}".encode("utf-8")).hexdigest()
        jobs.append({
            "id": jid,
            "source": source_name,
            "title": title[:120],
            "url": full_url
        })

    # 去重
    jobs = list({j["id"]: j for j in jobs}.values())
    return jobs


def push(title: str, content: str):
    push_type = os.getenv("PUSH_TYPE", "dingtalk").lower()
    push_url = os.environ.get("PUSH_URL", "").strip()
    if not push_url:
        print("未配置 PUSH_URL，跳过推送")
        return

    try:
        if push_type == "bark":
            url = push_url.rstrip("/") + "/" + quote(title) + "/" + quote(content)
            requests.get(url, timeout=15)
        elif push_type in ("dingtalk", "wecom"):
            requests.post(
                push_url,
                json={"msgtype": "text", "text": {"content": f"{title}\n\n{content}"}},
                timeout=15
            )
        else:
            requests.post(push_url, json={"title": title, "content": content}, timeout=15)
    except Exception as e:
        print(f"推送异常：{e}")


def main():
    first_run = not SEEN_FILE.exists()
    seen = load_seen()
    all_jobs = []

    for src in SOURCES:
        if not src["enabled"]:
            continue
        try:
            html = fetch_html(src["url"])
            jobs = parse_jobs(src["name"], html, src["url"])
            print(f"{src['name']} 解析到 {len(jobs)} 条岗位")
            all_jobs.extend(jobs)
        except Exception as e:
            print(f"{src['name']} 抓取失败：{e}")

    all_jobs = list({j["id"]: j for j in all_jobs}.values())
    new_jobs = [j for j in all_jobs if j["id"] not in seen]

    if first_run or new_jobs:
        jobs_to_push = all_jobs if first_run else new_jobs
        msg_title = "【当前电工岗位汇总】" if first_run else "【发现新电工岗位】"
        content = ""
        for idx, job in enumerate(jobs_to_push, 1):
            content += (
                f"{idx}. {job['title']}\n"
                f"来源：{job['source']}\n"
                f"链接：{job['url']}\n\n"
            )
        if len(content) > 7500:
            content = content[:7500] + "\n...内容过长截断，请访问网站查看"
        push(msg_title, content)

    seen.update(j["id"] for j in all_jobs)
    save_seen(seen)
    print(f"执行完成，历史共记录 {len(seen)} 条，本次新增 {len(new_jobs)} 条")


if __name__ == "__main__":
    main()
