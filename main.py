import os
import re
import json
import hashlib
from pathlib import Path
from urllib.parse import urljoin, quote
from datetime import datetime, timedelta

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
# 岗位发布时间允许范围：30天内
DAYS_LIMIT = 30

BAD_TITLE_WORDS = [
    "登录", "注册", "首页", "更多", "搜索", "联系我们",
    "返回", "上一页", "下一页", "微信扫一扫", "公众号", "客服"
]


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_publish_date(text: str) -> datetime | None:
    """
    解析网站日期：支持 2026-11-20 / 昨天 / 前天
    """
    text = clean(text)
    today = datetime.now()
    if "昨天" in text:
        return today - timedelta(days=1)
    if "前天" in text:
        return today - timedelta(days=2)
    m = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            return None
    return None


def is_recent(dt: datetime | None) -> bool:
    if dt is None:
        return False
    return (datetime.now() - dt).days <= DAYS_LIMIT


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


def get_job_detail_publish(job_url: str) -> datetime | None:
    """进入详情页尝试提取发布时间，列表取不到的时候兜底"""
    try:
        html = fetch_html(job_url)
        soup = BeautifulSoup(html, "html.parser")
        text_all = soup.get_text()
        return parse_publish_date(text_all)
    except Exception:
        return None


def search_jobs_by_keyword(keyword: str) -> list:
    """通用搜索：给关键词，返回30天内岗位列表，供定时任务 + 钉钉回调共用"""
    base_search = "https://www.xyrsjjy.com/job/?c=search&keyword=" + quote(keyword)
    html = fetch_html(base_search)
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    skip_patterns = ["c=search", "keyword=", "company", "comapply", "news", "article"]

    job_cards = soup.select("a[href*='job']")
    for a in job_cards:
        href = a.get("href", "").strip()
        if not href or href.startswith(("javascript:", "#")):
            continue
        full_url = urljoin(base_search, href)
        if any(p in full_url for p in skip_patterns):
            continue

        title = clean(a.get_text())
        if len(title) < 2:
            continue
        if any(w in title for w in BAD_TITLE_WORDS):
            continue

        # 优先从卡片文本拿日期
        card_text = clean(a.parent.get_text())
        pub_dt = parse_publish_date(card_text)
        if not is_recent(pub_dt):
            pub_dt = get_job_detail_publish(full_url)
            if not is_recent(pub_dt):
                continue

        jid = hashlib.md5(f"xyrs::{full_url}".encode("utf-8")).hexdigest()
        jobs.append({
            "id": jid,
            "keyword": keyword,
            "title": title[:120],
            "url": full_url,
            "pub_date": pub_dt.strftime("%Y-%m-%d") if pub_dt else "未知"
        })
    jobs = list({j["id"]: j for j in jobs}.values())
    return jobs


def push_dingtalk(title: str, content: str, push_url: str):
    if not push_url:
        print("没有推送地址，跳过")
        return
    try:
        if len(content) > 7000:
            content = content[:7000] + "\n...过长截断"
        requests.post(
            push_url,
            json={"msgtype": "text", "text": {"content": f"{title}\n\n{content}"}},
            timeout=15
        )
    except Exception as e:
        print(f"推送异常: {e}")


def main():
    push_url = os.getenv("PUSH_URL", "").strip()
    keyword = os.getenv("WATCH_KEYWORD", "电工")
    first_run = not SEEN_FILE.exists()
    seen = load_seen()

    try:
        all_jobs = search_jobs_by_keyword(keyword)
        print(f"关键词【{keyword}】筛选后（近30天）：{len(all_jobs)} 条")
    except Exception as e:
        print(f"抓取失败：{e}")
        return

    new_jobs = [j for j in all_jobs if j["id"] not in seen]
    if first_run or new_jobs:
        jobs_push = all_jobs if first_run else new_jobs
        msg_title = f"【{keyword}】近30天岗位汇总" if first_run else f"【新{keyword}岗位】近30天发布"
        msg = ""
        for idx, j in enumerate(jobs_push, 1):
            msg += f"{idx}. {j['title']}\n发布：{j['pub_date']}\n链接：{j['url']}\n\n"
        push_dingtalk(msg_title, msg, push_url)

    seen.update(j["id"] for j in all_jobs)
    save_seen(seen)
    print(f"完成，历史总数：{len(seen)}，本次新增：{len(new_jobs)}")


if __name__ == "__main__":
    main()
