import os
import re
from datetime import datetime, timedelta
import pytz
import requests
from bs4 import BeautifulSoup
from flask import Flask, send_file
from apscheduler.schedulers.background import BackgroundScheduler
from playwright.sync_api import sync_playwright

app = Flask(__name__)
FILE_PATH = 'ids.txt'

def scrape_task():
    tz = pytz.timezone('Asia/Shanghai')
    now = datetime.now(tz)
    current_year = now.year

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }

    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] 🚀 开始执行抓取任务(修复详情页跳转 + 资源拦截)...")
    
    js_url = 'https://im-imgs-bucket.oss-accelerate.aliyuncs.com/index.js?t_5'
    try:
        response = requests.get(js_url, headers=headers, timeout=10)
        response.encoding = 'utf-8'
    except Exception as e:
        print(f"❌ 请求主 JS 失败: {e}")
        return

    html_snippets = re.findall(r"document\.write\('(.*?)'\);", response.text)
    html_content = "".join(html_snippets)
    
    # 用正则快速切分比赛区块
    ul_blocks = re.split(r'<ul class="item play', html_content)[1:]
    
    play_urls_to_visit = []

    for block in ul_blocks:
        time_match = re.search(r'<li class="lab_time">(.*?)</li>', block)
        if not time_match:
            continue
        time_str = time_match.group(1).strip()
        
        try:
            match_time_naive = datetime.strptime(f"{current_year}-{time_str}", "%Y-%m-%d %H:%M")
            match_time = tz.localize(match_time_naive)
            if match_time > now + timedelta(days=300):
                match_time = tz.localize(match_time_naive.replace(year=current_year - 1))
            elif match_time < now - timedelta(days=300):
                match_time = tz.localize(match_time_naive.replace(year=current_year + 1))
        except ValueError:
            continue

        time_diff = (match_time - now).total_seconds() / 3600
        
        # 筛选前后 3 小时
        if -3 <= time_diff <= 3:
            # 1. 先从 index.js 区块中提取出 steamXXXX.html 的详情页链接
            detail_links = re.findall(r'href="(http://play\.sportsteam368\.com/play/steam\d+\.html)"', block)
            
            for detail_url in set(detail_links):
                try:
                    # 2. 访问详情页
                    detail_res = requests.get(detail_url, headers=headers, timeout=10)
                    detail_res.encoding = 'utf-8'
                    soup = BeautifulSoup(detail_res.text, 'html.parser')
                    
                    # 3. 在详情页里寻找“高清直播”
                    hd_links = [a for a in soup.find_all('a') if a.get_text() and '高清直播' in a.get_text()]
                    
                    for hd in hd_links:
                        data_play = hd.get('data-play')
                        if data_play:
                            final_play_url = f"http://play.sportsteam368.com{data_play}"
                            play_urls_to_visit.append(final_play_url)
                except Exception as e:
                    print(f"   ❌ 获取详情页失败 ({detail_url}): {e}")

    play_urls_to_visit = list(set(play_urls_to_visit))
    print(f"📊 筛选出 {len(play_urls_to_visit)} 个需要在浏览器中打开的高清播放页。")

    target_ids = set()

    # 启动真实的浏览器内核进行资源抓包
    if play_urls_to_visit:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )

            for play_url in play_urls_to_visit:
                print(f"   🔗 浏览器正在加载: {play_url}")
                page = context.new_page()
                captured_ids = []

                # 拦截资源请求
                def handle_request(request):
                    url = request.url
                    # 匹配超过 80 个字符的 Base64 长串
                    match = re.search(r'([A-Za-z0-9+/=]{80,})', url)
                    if match:
                        extracted_id = match.group(1)
                        if extracted_id not in captured_ids:
                            captured_ids.append(extracted_id)
                            source_file = url.split('?')[0][-30:] 
                            print(f"   📡 [底层抓包] 成功抓到 ID: {extracted_id[:15]}... (隐蔽在: ...{source_file})")

                page.on("request", handle_request)

                try:
                    page.goto(play_url, timeout=15000)
                    page.wait_for_timeout(3000) 
                except Exception as e:
                    pass
                finally:
                    page.close()

                for cid in captured_ids:
                    target_ids.add(cid)

            browser.close()

    # 写入最终的 txt 文件
    with open(FILE_PATH, 'w', encoding='utf-8') as f:
        for item in target_ids:
            f.write(item + '\n')
    
    print(f"🎉 抓取任务完成！共提取 {len(target_ids)} 个不重复的 ID。")

scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
scheduler.add_job(func=scrape_task, trigger="interval", minutes=30, id='scrape_job', replace_existing=True)
scheduler.start()

scrape_task()

@app.route('/')
def get_ids():
    if os.path.exists(FILE_PATH) and os.path.getsize(FILE_PATH) > 0:
        return send_file(FILE_PATH, mimetype='text/plain')
    return "✅ 抓取任务已运行，但当前文件为空（前后3小时可能无比赛）。", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
