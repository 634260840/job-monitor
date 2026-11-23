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

SOURCES = [
    {
        "name": "新沂好就业",
        "url": "https://www.xyrsjjy.com/job/?c=search&keyword=%E7%94%B5%E5%B7%A5",
        "enabled": True,
    }
]

BAD_TITLE_WORDS = [
    "登录", "注册", "首页", "更多", "搜索", "找工作", "联系我们", "返回", 
    "上一页", "下一页", "微信扫一扫", "公众号", "客服", "清除所选", "申请",
    "投递", "详情", "查看", "筛选", "列表", "地图", "已选", "确定", "取消"
]

def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def load_seen():
    """读取历史记录，现在存储格式是 {"岗位ID": "发现日期"}"""
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):  # 兼容你之前的旧数据
                return {jid: datetime.now().strftime("%Y-%m-%d") for jid in data}
            return data
        except Exception:
            return {}
    return {}

def save_seen(seen):
    SEEN_FILE.write_text(
        json.dumps(seen, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def extract_date_from_html(text):
    """尝试从网页文字中提取日期，提取不到返回 None"""
    # 匹配 2023-11-20, 2023年11月20日 等格式
    match = re.search(r'(202\d)[-年](\d{1,2})[-月](\d{1,2})', text)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except:
            pass
    return None

def fetch_html(src):
    r = requests.get(src["url"], headers=HEADERS, timeout=25)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    return r.text

def parse_jobs(src, html):
    soup = BeautifulSoup(html, "html.parser")
    jobs_dict = {}
    
    # 找到包含所有岗位的父容器（如果知道具体的div class可以写死，这里用body兜底）
    body_text = soup.get_text()
    # 尝试在页面找一个全局的日期（有些网站只在顶部写一个更新时间）
    page_date = extract_date_from_html(body_text)

    for a in soup.find_all("a", href=True):
        href = urljoin(src["url"], a.get("href", ""))
        id_match = re.search(r'id=(\d+)', href)
        if not id_match:
            continue
            
        job_id = id_match.group(1)
        title = clean(a.get_text(" ", strip=True))
        
        if len(title) < 2 or any(w in title for w in BAD_TITLE_WORDS):
            continue
            
        if job_id in jobs_dict:
            if len(title) > len(jobs_dict[job_id]["title"]):
                jobs_dict[job_id]["title"] = title
        else:
            # 尝试找该链接附近的日期
            parent_text = a.parent.get_text() if a.parent else ""
            job_date = extract_date_from_html(parent_text) or page_date or datetime.now()

            jobs_dict[job_id] = {
                "id": hashlib.md5(f"{src['name']}::{job_id}".encode("utf-8")).hexdigest(),
                "source": src["name"],
                "title": title[:120],
                "url": href,
                "date": job_date.strftime("%Y-%m-%d")
            }

    return list(jobs_dict.values())

def push(title: str, content: str):
    push_type = os.getenv("PUSH_TYPE", "dingtalk").lower()
    push_url = os.environ.get("PUSH_URL", "").strip()
    if not push_url: return
    if push_type == "bark":
        requests.get(push_url.rstrip("/") + "/" + quote(title) + "/" + quote(content), timeout=15)
    else:
        requests.post(push_url, json={"msgtype": "text", "text": {"content": f"{title}\n{content}"}}, timeout=15)

def main():
    first_run = not SEEN_FILE.exists()
    seen = load_seen()
    all_jobs = []
    
    for src in SOURCES:
        if not src.get("enabled"): continue
        try:
            html = fetch_html(src)
            jobs = parse_jobs(src, html)
            print(f"{src['name']} 解析到 {len(jobs)} 条真实岗位")
            all_jobs.extend(jobs)
        except Exception as e:
            print(f"{src['name']} 抓取失败：{e}")

    # 按照 ID 去重
    all_jobs = {j["id"]: j for j in all_jobs}.values()
    
    new_jobs_recent = []  # 最近30天的新岗位
    new_jobs_old = []     # 超过30天的新岗位
    now = datetime.now()

    for j in all_jobs:
        if j["id"] not in seen:
            # 发现新岗位
            job_date = datetime.strptime(j["date"], "%Y-%m-%d")
            days_diff = (now - job_date).days
            
            if days_diff <= 30:
                new_jobs_recent.append(j)
            else:
                new_jobs_old.append(j)
            
            # 记录到历史，以后不再重复发现
            seen[j["id"]] = j["date"]

    save_seen(seen)

    if first_run:
        push("监控启动", f"已初始化记录 {len(all_jobs)} 条岗位。以后有新动态会立刻通知。")
        return

    # 1. 推送最近30天的新岗位（逐条推）
    for j in new_jobs_recent[:15]:  # 限制最多15条防刷屏
        push(f"新岗位：{j['title']}", f"发布时间：{j['date']}\n来源：{j['source']}\n{j['url']}")

    # 2. 推送30天前的老岗位（汇总推）
    if new_jobs_old:
        summary_text = f"发现 {len(new_jobs_old)} 条发布超30天的岗位：\n"
        for j in new_jobs_old[:5]: # 最多列5个名字
            summary_text += f"- {j['title']}\n"
        if len(new_jobs_old) > 5:
            summary_text += f"...等共 {len(new_jobs_old)} 条"
        
        push("【30日前招聘汇总】", summary_text)

    print(f"本次新增：30天内 {len(new_jobs_recent)} 条，30天前 {len(new_jobs_old)} 条")

if __name__ == "__main__":
    main()
