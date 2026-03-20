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
        'Referer': 'http://play.sportsteam368.com/'
    }

    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] 🚀 开始执行抓取任务(精确解析 + 底层抓包)...")
    
    # 1. 获取主列表
    js_url = 'https://im-imgs-bucket.oss-accelerate.aliyuncs.com/index.js?t_5'
    try:
        response = requests.get(js_url, headers=headers, timeout=10)
        response.encoding = 'utf-8'
        response.raise_for_status()
    except Exception as e:
        print(f"❌ 请求主 JS 失败: {e}")
        return

    html_snippets = re.findall(r"document\.write\('(.*?)'\);", response.text)
    html_content = "".join(html_snippets)
    soup = BeautifulSoup(html_content, 'html.parser')

    match_lists = soup.select('ul.item.play')
    print(f"📊 成功解析 JS 文件，共找到 {len(match_lists)} 场比赛信息。")

    play_urls_to_visit = []

    # 2. 筛选比赛并进入详情页寻找“高清直播”
    for match in match_lists:
        time_li = match.find('li', class_='lab_time')
        if not time_li:
            continue
        
        time_str = time_li.get_text(strip=True)
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
        
        if -3 <= time_diff <= 3:
            print(f"🕒 找到符合时间的比赛: {time_str}")
            links = match.find_all('a', href=re.compile(r'play\.sportsteam368\.com'))
            
            for link in links:
                match_url = link.get('href')
                
                try:
                    # 请求详情页
                    match_res = requests.get(match_url, headers=headers, timeout=10)
                    match_res.encoding = 'utf-8'
                    match_soup = BeautifulSoup(match_res.text, 'html.parser')
                    
                    # 在详情页中寻找“高清直播”
                    hd_links = [a for a in match_soup.find_all('a') if a.get_text() and '高清直播' in a.get_text()]

                    for hd in hd_links:
                        data_play = hd.get('data-play')
                        if data_play:
                            play_url = f"http://play.sportsteam368.com{data_play}"
                            play_urls_to_visit.append(play_url)
                except Exception as e:
                    print(f"   ❌ 请求详情页失败: {e}")

    # 去重处理
    play_urls_to_visit = list(set(play_urls_to_visit))
    print(f"📊 筛选完毕，共有 {len(play_urls_to_visit)} 个需要在浏览器中拦截的【高清播放页】。")

    target_ids = set()

    # 3. 启动无头浏览器进行资源请求抓包拦截
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

                # 监听网络请求
                def handle_request(request):
                    url = request.url
                    # 特征匹配：超过 80 位的连续 Base64 字符
                    match = re.search(r'([A-Za-z0-9+/=]{80,})', url)
                    if match:
                        extracted_id = match.group(1)
                        if extracted_id not in captured_ids:
                            captured_ids.append(extracted_id)
                            source_file = url.split('?')[0][-30:] 
                            print(f"   📡 [底层抓包] 成功截获 ID: {extracted_id[:15]}... (源文件: ...{source_file})")

                page.on("request", handle_request)

                try:
                    # 超时设置为 15 秒，只要底层资源开始请求就截获
                    page.goto(play_url, timeout=15000)
                    page.wait_for_timeout(3000) # 多等3秒让 JS 加载图片和视频源
                except Exception as e:
                    pass
                finally:
                    page.close()

                # 合并到总池
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
    return "✅ 抓取任务运行完成，但当前文件为空（前后3小时可能无比赛）。", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
