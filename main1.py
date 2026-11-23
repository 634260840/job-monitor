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
DAYS_LIMIT = 15  # 最近15天
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0 Safari/537.36"
    )
}

WATCH_KEYWORDS = ["电工", "仪表"]
SEARCH_URL_TPL = "https://www.xyrsjjy.com/job/?c=search&keyword={kw}"

BAD_TITLE_WORDS = [
    "登录", "注册", "首页", "更多", "搜索", "联系我们",
    "返回", "上一页", "下一页", "微信扫一扫", "公众号", "客服"
]


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_publish_date(text: str) -> datetime | None:
    text = clean(text)
    now = datetime.now()
    # 匹配标准日期  yyyy-mm-dd
    m = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            pass
    # 中文相对时间
    if "昨天" in text:
        return now - timedelta(days=1)
    if "前天" in text:
        return now - timedelta(days=2)
    m_day = re.search(r"(\d+)天前", text)
    if m_day:
        try:
            d = int(m_day.group(1))
            return now - timedelta(days=d)
        except Exception:
            pass
    return None


def is_within_limit(dt: datetime | None) -> bool:
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


def get_detail_pubdate(job_url: str) -> datetime | None:
    """进岗位详情页兜底拿发布时间"""
    try:
        html = fetch_html(job_url)
        soup = BeautifulSoup(html, "html.parser")
        return parse_publish_date(soup.get_text())
    except Exception:
        return None


def search_one_keyword(keyword: str) -> list:
    url = SEARCH_URL_TPL.format(kw=quote(keyword))
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    jobs = []

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or href.startswith(("javascript:", "#")):
            continue
        full_url = urljoin(url, href)

        # 只保留岗位申请页面
        if "c=comapply&id=" not in full_url:
            continue
        if "company/index.php?c=show" in full_url:
            continue

        title = clean(a.get_text())
        if len(title) < 2:
            continue
        if any(w in title for w in BAD_TITLE_WORDS):
            continue
        if keyword not in title:
            continue

        # 优先从列表附近文本提取时间
        card_text = clean(a.parent.get_text())
        pub_dt = parse_publish_date(card_text)
        # 列表取不到，去详情页拿
        if not is_within_limit(pub_dt):
            pub_dt = get_detail_pubdate(full_url)
        if not is_within_limit(pub_dt):
            continue

        jid = hashlib.md5(f"{keyword}::{full_url}".encode("utf-8")).hexdigest()
        jobs.append({
            "id": jid,
            "keyword": keyword,
            "title": title[:120],
            "url": full_url,
            "pub_date": pub_dt.strftime("%Y-%m-%d")
        })
    jobs = list({j["id"]: j for j in jobs}.values())
    return jobs


def push_dingtalk(title: str, content: str, push_url: str):
    if not push_url:
        print("PUSH_URL 为空，跳过推送")
        return
    try:
        if len(content) > 7000:
            content = content[:7000] + "\n...内容过长截断"
        requests.post(
            push_url,
            json={"msgtype": "text", "text": {"content": f"{title}\n\n{content}"}},
            timeout=15
        )
    except Exception as e:
        print(f"钉钉推送异常：{e}")


def main():
    push_url = os.getenv("PUSH_URL", "").strip()
    first_run = not SEEN_FILE.exists()
    seen = load_seen()
    all_jobs = []

    for kw in WATCH_KEYWORDS:
        try:
            jobs = search_one_keyword(kw)
            print(f"【{kw}】匹配且15天内岗位：{len(jobs)} 条")
            all_jobs.extend(jobs)
        except Exception as e:
            print(f"抓取关键词 {kw} 失败：{e}")

    all_jobs = list({j["id"]: j for j in all_jobs}.values())
    new_jobs = [j for j in all_jobs if j["id"] not in seen]

    if len(all_jobs) == 0:
        # 没有找到任何岗位，发送提示
        push_dingtalk("【电工/仪表｜近15天岗位查询】", "本次未找到近15天电工、仪表相关岗位。", push_url)
    elif first_run or new_jobs:
        jobs_push = all_jobs if first_run else new_jobs
        msg_title = "【电工/仪表｜近15天岗位汇总】" if first_run else "【发现新岗位】电工/仪表（近15天）"
        msg = ""
        for idx, job in enumerate(jobs_push, 1):
            msg += (
                f"{idx}. {job['title']}（关键词：{job['keyword']}）\n"
                f"发布日期：{job['pub_date']}\n"
                f"链接：{job['url']}\n\n"
            )
        push_dingtalk(msg_title, msg, push_url)

    seen.update(j["id"] for j in all_jobs)
    save_seen(seen)
    print(f"执行完成，历史共 {len(seen)} 条，本次新增 {len(new_jobs)} 条")


if __name__ == "__main__":
    main()
