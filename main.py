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

# ================== 站点配置 ==================
SOURCES = [
    {
        "name": "新沂好就业",
        "url": "https://www.xyrsjjy.com/job/?c=search&keyword=%E7%94%B5%E5%B7%A5",
        "enabled": True,
        "check_auth": False,
        "card_selector": "",
        # 真实岗位链接通常带有 index.php?c=show&id= 或者是 /job/数字.html
        "link_pattern": r"/job/(index\.php\?c=show|detail|\d+)",
        # 把刚才截图里出现的筛选链接全部屏蔽
        "exclude_href": ["c=search", "keyword=", "all=", "tp=", "page=", "?c=list"],
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

# 屏蔽网页上常见的按钮和无意义文字
BAD_TITLE_WORDS = [
    "登录", "注册", "首页", "更多", "搜索", "找工作", "联系我们", "返回", 
    "上一页", "下一页", "微信扫一扫", "公众号", "客服", "清除所选", "申请",
    "投递", "详情", "查看", "筛选", "列表", "地图", "已选", "确定", "取消"
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
    return r.text

def parse_jobs(src, html):
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    cards = soup.find_all("a", href=True)

    for a in cards:
        href = urljoin(src["url"], a.get("href", ""))
        
        # 1. 过滤基础无效链接
        if not href or href.startswith("javascript") or href == "#":
            continue
            
        # 2. 必须匹配真实岗位的链接特征
        if src.get("link_pattern") and not re.search(src["link_pattern"], href):
            continue
            
        # 3. 过滤黑名单链接
        if any(x in href for x in src.get("exclude_href", [])):
            continue

        # 4. 提取标题并清洗
        title = clean(a.get_text(" ", strip=True))
        
        # 如果标题太短，或者包含垃圾词汇，直接丢弃
        if len(title) < 3:
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
        print("未配置 PUSH_URL，跳过推送")
        return

    if push_type == "bark":
        url = push_url.rstrip("/") + "/" + quote(title) + "/" + quote(content)
        requests.get(url, timeout=15)
    elif push_type in ["dingtalk", "wecom"]:
        requests.post(push_url, json={
            "msgtype": "text",
            "text": {"content": f"{title}\n{content}"}
        }, timeout=15)

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
    
    # 恢复正常的逻辑：只推真正的新岗位
    new_jobs = [j for j in all_jobs if j["id"] not in seen]

    if first_run:
        seen.update(j["id"] for j in all_jobs)
        save_seen(seen)
        push("监控启动", f"已记录目前所有真实岗位 {len(all_jobs)} 条。以后有新增会立刻通知。")
        return

    # 发现新岗位，推送（最多20条防刷屏）
    for j in new_jobs[:20]:
        push(f"新岗位：{j['title']}", f"来源：{j['source']}\n{j['url']}")

    seen.update(j["id"] for j in all_jobs)
    save_seen(seen)
    print(f"本次新增 {len(new_jobs)} 条，累计记录 {len(seen)} 条")

if __name__ == "__main__":
    main()
