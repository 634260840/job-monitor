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
    }
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
    jobs_dict = {}
    
    # 找到页面里所有的链接
    for a in soup.find_all("a", href=True):
        href = urljoin(src["url"], a.get("href", ""))
        
        # 核心逻辑：只要链接里包含 "id=数字"，就认定是职位详情
        id_match = re.search(r'id=(\d+)', href)
        if not id_match:
            continue
            
        job_id = id_match.group(1)
        
        # 抓取这个链接的文字
        title = clean(a.get_text(" ", strip=True))
        
        # 如果抓到的是空，或者只有“申请”这种废话，就跳过
        if len(title) < 2 or any(w in title for w in BAD_TITLE_WORDS):
            continue
            
        # 智能去重：同一个职位可能有多个链接，我们保留文字最长的那个（通常是最完整的标题）
        if job_id in jobs_dict:
            if len(title) > len(jobs_dict[job_id]["title"]):
                jobs_dict[job_id]["title"] = title
        else:
            jobs_dict[job_id] = {
                "id": hashlib.md5(f"{src['name']}::{job_id}".encode("utf-8")).hexdigest(),
                "source": src["name"],
                "title": title[:120],
                "url": href
            }

    return list(jobs_dict.values())

def push(title: str, content: str):
    push_type = os.getenv("PUSH_TYPE", "dingtalk").lower()
    push_url = os.environ.get("PUSH_URL", "").strip()
    if not push_url:
        print("未配置 PUSH_URL")
        return
    if push_type == "bark":
        url = push_url.rstrip("/") + "/" + quote(title) + "/" + quote(content)
        requests.get(url, timeout=15)
    else:
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
        if not src.get("enabled"): continue
        try:
            html = fetch_html(src)
            jobs = parse_jobs(src, html)
            print(f"{src['name']} 解析到 {len(jobs)} 条真实岗位")
            all_jobs.extend(jobs)
        except Exception as e:
            errors.append(f"{src['name']} 抓取失败：{e}")

    # 再全局按 id 去重一次
    all_jobs = list({j["id"]: j for j in all_jobs}.values())
    new_jobs = [j for j in all_jobs if j["id"] not in seen]

    if first_run:
        seen.update(j["id"] for j in all_jobs)
        save_seen(seen)
        push("监控启动", f"已成功记录目前所有真实岗位 {len(all_jobs)} 条。以后有新增会立刻通知。")
        return

    for j in new_jobs[:20]:
        push(f"新岗位：{j['title']}", f"来源：{j['source']}\n{j['url']}")

    seen.update(j["id"] for j in all_jobs)
    save_seen(seen)
    print(f"本次新增 {len(new_jobs)} 条，累计记录 {len(seen)} 条")

if __name__ == "__main__":
    main()
