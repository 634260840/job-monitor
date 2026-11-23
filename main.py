import os
import re
import json
import hashlib
from pathlib import Path
from urllib.parse import urljoin, quote
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

# 全局配置
DAYS_LIMIT = 15
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0 Safari/537.36"
    )
}
WATCH_KEYWORDS = ["电工", "仪表"]

BAD_TITLE_WORDS = [
    "登录", "注册", "首页", "更多", "搜索", "联系我们",
    "返回", "上一页", "下一页", "微信扫一扫", "公众号", "客服"
]

SEEN_FILE_XY = Path("seen_ids_xy.json")   # 新沂好就业已推送记录
SEEN_FILE_ZW = Path("seen_ids_zw.json")   # 钟吾直聘已推送记录


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_publish_date(text: str) -> datetime | None:
    text = clean(text)
    now = datetime.now()
    m = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            pass
    m_min = re.search(r"(\d+)分钟前", text)
    if m_min:
        return now - timedelta(minutes=int(m_min.group(1)))
    m_h = re.search(r"(\d+)小时前", text)
    if m_h:
        return now - timedelta(hours=int(m_h.group(1)))
    if "昨天" in text:
        return now - timedelta(days=1)
    if "前天" in text:
        return now - timedelta(days=2)
    m_day = re.search(r"(\d+)天前", text)
    if m_day:
        return now - timedelta(days=int(m_day.group(1)))
    return None


def is_within_limit(dt: datetime | None) -> bool:
    if dt is None:
        return False
    return (datetime.now() - dt).days <= DAYS_LIMIT


def load_seen(path: Path):
    if path.exists():
        try:
            return set(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_seen(path: Path, seen_set):
    path.write_text(
        json.dumps(sorted(seen_set), ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


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


# ---------------------- 【新沂好就业】抓取逻辑 ----------------------
def fetch_html_xy(url):
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    return r.text


def get_detail_pubdate_xy(job_url: str) -> datetime | None:
    try:
        html = fetch_html_xy(job_url)
        soup = BeautifulSoup(html, "html.parser")
        return parse_publish_date(soup.get_text())
    except Exception:
        return None


def search_xy_jobs(keyword: str) -> list:
    url = f"https://www.xyrsjjy.com/job/?c=search&keyword={quote(keyword)}"
    html = fetch_html_xy(url)
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or href.startswith(("javascript:", "#")):
            continue
        full_url = urljoin(url, href)
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

        card_text = clean(a.parent.get_text())
        pub_dt = parse_publish_date(card_text)
        if not is_within_limit(pub_dt):
            pub_dt = get_detail_pubdate_xy(full_url)
        if not is_within_limit(pub_dt):
            continue

        jid = hashlib.md5(f"xy::{keyword}::{full_url}".encode("utf-8")).hexdigest()
        jobs.append({
            "source": "新沂好就业",
            "id": jid,
            "keyword": keyword,
            "title": title[:120],
            "url": full_url,
            "pub_date": pub_dt.strftime("%Y-%m-%d")
        })
    jobs = list({j["id"]: j for j in jobs}.values())
    return jobs


# ---------------------- 【钟吾直聘】抓取逻辑 ----------------------
def get_zw_session():
    session = requests.Session()
    cookie_str = os.getenv("ZW_COOKIE", "").strip()
    if not cookie_str:
        raise Exception("未配置 ZW_COOKIE 环境变量（钟吾直聘Cookie）")
    session.headers["Cookie"] = cookie_str
    session.headers.update(HEADERS)
    return session


def fetch_html_zw(session, url):
    r = session.get(url, timeout=25)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    html = r.text
    if "登录账号" in html:
        raise Exception("钟吾直聘 Cookie 已失效，请重新复制登录Cookie！")
    return html


def get_detail_pubdate_zw(session, job_url: str) -> datetime | None:
    try:
        html = fetch_html_zw(session, job_url)
        soup = BeautifulSoup(html, "html.parser")
        return parse_publish_date(soup.get_text())
    except Exception:
        return None


def search_zw_jobs(session, keyword: str) -> list:
    url = f"https://www.zhongwuzhipin.cn/search?pageIndex=1&keyword={quote(keyword)}"
    html = fetch_html_zw(session, url)
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or href.startswith(("javascript:", "#")):
            continue
        full_url = urljoin(url, href)

        # ========== 这里改成你实际的岗位详情URL特征 ==========
        if "/seekwap/recruit/show?id=" not in full_url:
            continue
        if "/seekwap/company/" in full_url:
            continue
        # =====================================================

        title = clean(a.get_text())
        if len(title) < 2:
            continue
        if any(w in title for w in BAD_TITLE_WORDS):
            continue
        if keyword not in title:
            continue

        card_text = clean(a.parent.get_text())
        pub_dt = parse_publish_date(card_text)
        if not is_within_limit(pub_dt):
            pub_dt = get_detail_pubdate_zw(session, full_url)
        if not is_within_limit(pub_dt):
            continue

        jid = hashlib.md5(f"zw::{keyword}::{full_url}".encode("utf-8")).hexdigest()
        jobs.append({
            "source": "钟吾直聘",
            "id": jid,
            "keyword": keyword,
            "title": title[:120],
            "url": full_url,
            "pub_date": pub_dt.strftime("%Y-%m-%d")
        })
    jobs = list({j["id"]: j for j in jobs}.values())
    return jobs


def main():
    push_url = os.getenv("PUSH_URL", "").strip()
    all_new_jobs = []
    all_found_jobs = []

    # --- 1. 抓取【新沂好就业】---
    seen_xy = load_seen(SEEN_FILE_XY)
    first_xy = not SEEN_FILE_XY.exists()
    xy_list = []
    try:
        for kw in WATCH_KEYWORDS:
            xy_list.extend(search_xy_jobs(kw))
        xy_list = list({j["id"]: j for j in xy_list}.values())
        new_xy = [j for j in xy_list if j["id"] not in seen_xy]
        all_found_jobs.extend(xy_list)
        all_new_jobs.extend(new_xy)
        print(f"【新沂好就业】共抓到 {len(xy_list)} 条，新增 {len(new_xy)} 条")
        seen_xy.update(j["id"] for j in xy_list)
        save_seen(SEEN_FILE_XY, seen_xy)
    except Exception as e:
        print(f"【新沂好就业】抓取异常：{e}")
        push_dingtalk("【岗位监控异常｜新沂好就业】", f"抓取失败：{e}", push_url)

    # --- 2. 抓取【钟吾直聘】---
    seen_zw = load_seen(SEEN_FILE_ZW)
    first_zw = not SEEN_FILE_ZW.exists()
    zw_list = []
    try:
        sess = get_zw_session()
        for kw in WATCH_KEYWORDS:
            zw_list.extend(search_zw_jobs(sess, kw))
        zw_list = list({j["id"]: j for j in zw_list}.values())
        new_zw = [j for j in zw_list if j["id"] not in seen_zw]
        all_found_jobs.extend(zw_list)
        all_new_jobs.extend(new_zw)
        print(f"【钟吾直聘】共抓到 {len(zw_list)} 条，新增 {len(new_zw)} 条")
        seen_zw.update(j["id"] for j in zw_list)
        save_seen(SEEN_FILE_ZW, seen_zw)
    except Exception as e:
        print(f"【岗位监控异常｜钟吾直聘】", f"抓取失败：{e}", push_url)

    # --- 3. 推送汇总 ---
    if len(all_found_jobs) == 0:
        push_dingtalk("【双平台岗位查询】", "本次在【新沂好就业、钟吾直聘】均未找到近15天电工、仪表岗位。", push_url)
    else:
        # 首次运行推送全部，否则只推送新增
        is_first_run = first_xy or first_zw
        send_list = all_found_jobs if is_first_run else all_new_jobs
        if send_list:
            msg_title = "【双平台｜发现新岗位】电工/仪表（近15天）"
            msg = ""
            for idx, job in enumerate(send_list, 1):
                msg += (
                    f"{idx}.【{job['source']}】{job['title']}（关键词：{job['keyword']}）\n"
                    f"发布日期：{job['pub_date']}\n"
                    f"链接：{job['url']}\n\n"
                )
            push_dingtalk(msg_title, msg, push_url)


if __name__ == "__main__":
    main()
