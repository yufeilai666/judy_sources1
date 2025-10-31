import os
import sys
import re
import json
import time
import random
import argparse
import requests
import datetime
import pytz
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET
from xml.dom import minidom

# 全局時區設置
TAIPEI_TZ = pytz.timezone('Asia/Taipei')

# 代理設置 (從環境變量讀取)
HTTP_PROXY = os.environ.get('http_proxy', '') or os.environ.get('HTTP_PROXY', '')
HTTPS_PROXY = os.environ.get('https_proxy', '') or os.environ.get('HTTPS_PROXY', '')

PROXIES = {}
if HTTP_PROXY:
    PROXIES['http'] = HTTP_PROXY
if HTTPS_PROXY:
    PROXIES['https'] = HTTPS_PROXY

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

def create_session():
    """創建帶有代理的會話"""
    session = requests.Session()
    session.headers.update(HEADERS)
    
    if PROXIES:
        print(f"使用代理: {PROXIES}")
        session.proxies.update(PROXIES)
    else:
        print("未設置代理，使用直接連接")
    
    return session

def parse_channel_list(session):
    """從LiTV API獲取頻道清單，只抓取特定ID模式的頻道"""
    print("開始獲取LiTV頻道清單...")
    
    # LiTV頻道API
    channel_url = "https://www.litv.tv/_next/data/322e31352e3138/channel.json"
    
    try:
        response = session.get(channel_url, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        print(f"獲取的頻道數據結構: {list(data.keys())}")
        
        # 從 pageProps.introduction.channels 獲取頻道列表
        channels_data = data.get('pageProps', {}).get('introduction', {}).get('channels', [])
        
        if not channels_data:
            print("❌ 未找到頻道數據")
            return []
        
        print(f"找到 {len(channels_data)} 個頻道")
        
        # 定義要抓取的頻道ID模式
        target_patterns = [
            r'^4gtv-4gtv.*',      # 4gtv-4gtv開頭的所有頻道
            r'^litv-ftv.*',       # litv-ftv開頭的所有頻道
            r'^iNEWS$',           # 精確匹配iNEWS
            r'^litv-longturn.*'   # litv-longturn開頭的所有頻道
        ]
        
        channels = []
        for channel in channels_data:
            channel_name = channel.get('title', '').strip()
            channel_id = channel.get('cdn_code', '').strip()
            
            if not channel_name or not channel_id:
                continue
            
            # 檢查頻道ID是否符合目標模式
            is_target = False
            for pattern in target_patterns:
                if re.match(pattern, channel_id):
                    is_target = True
                    break
            
            if not is_target:
                continue
                
            # 處理logo URL
            logo = channel.get('picture', '')
            if logo and not logo.startswith('http'):
                logo = f"https://fino.svc.litv.tv/{logo.lstrip('/')}"
            
            channels.append({
                "channelName": channel_name,
                "id": channel_id,
                "logo": logo,
                "description": channel.get('description', ''),
                "content_type": channel.get('content_type', 'channel')
            })
        
        print(f"✅ 成功獲取 {len(channels)} 個目標頻道")
        for channel in channels:
            print(f"   - {channel['channelName']} (ID: {channel['id']})")
        return channels
        
    except Exception as e:
        print(f"❌ 獲取頻道清單失敗: {str(e)}")
        import traceback
        traceback.print_exc()
        return []

def parse_date_from_title(date_text):
    """從日期標題解析日期"""
    try:
        # 處理 "今日 / 11月1日 / 星期六" 格式
        parts = date_text.split(' / ')
        if len(parts) >= 2:
            date_part = parts[1]  # "11月1日"
            
            # 獲取當前年份
            current_year = datetime.datetime.now().year
            
            # 解析月份和日期
            month_match = re.search(r'(\d+)月', date_part)
            day_match = re.search(r'(\d+)日', date_part)
            
            if month_match and day_match:
                month = int(month_match.group(1))
                day = int(day_match.group(1))
                
                # 創建日期對象
                date_obj = datetime.datetime(current_year, month, day, tzinfo=TAIPEI_TZ)
                return date_obj
    except Exception as e:
        print(f"⚠️ 日期解析失敗: {date_text}, 錯誤: {str(e)}")
    
    return None

def fetch_channel_epg(session, channel_id, channel_name):
    """從頻道頁面獲取節目表數據"""
    print(f"\n開始獲取頻道 {channel_name} 的節目表...")
    
    # 頻道頁面URL
    channel_url = f"https://www.litv.tv/channel/watch/{channel_id}"
    
    try:
        response = session.get(channel_url, timeout=30)
        response.raise_for_status()
        
        # 解析HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 調試：保存HTML以便檢查
        with open(f"debug_{channel_id}.html", "w", encoding="utf-8") as f:
            f.write(soup.prettify())
        print(f"✅ 已保存HTML到 debug_{channel_id}.html 用於調試")
        
        programs = []
        current_date = None
        
        # 方法1: 查找包含節目表的容器
        # 嘗試多種可能的選擇器
        selectors = [
            'div.grow.overflow-y-auto',
            'div[class*="overflow-y-auto"]',
            'div[class*="epg"]',
            'div[class*="schedule"]',
            'div[class*="program"]'
        ]
        
        epg_container = None
        for selector in selectors:
            epg_container = soup.select_one(selector)
            if epg_container:
                print(f"✅ 使用選擇器找到節目表容器: {selector}")
                break
        
        if not epg_container:
            print("❌ 未找到節目表容器，嘗試備用方法...")
            # 備用方法：查找所有包含時間和節目名稱的div
            all_divs = soup.find_all('div')
            for div in all_divs:
                text = div.get_text(strip=True)
                if re.match(r'\d{1,2}:\d{2}\s+.+', text):
                    print(f"找到節目行: {text}")
        
        # 如果找到容器，解析其中的節目
        if epg_container:
            # 查找所有直接子元素
            for child in epg_container.children:
                if child.name == 'div':
                    classes = child.get('class', [])
                    class_str = ' '.join(classes) if classes else ''
                    text = child.get_text(strip=True)
                    
                    print(f"檢查元素: class='{class_str}', text='{text}'")
                    
                    # 檢查是否是日期標題
                    if text and ('今日' in text or '月' in text and '日' in text):
                        print(f"📅 找到日期標題: {text}")
                        current_date = parse_date_from_title(text)
                        if current_date:
                            print(f"  解析為: {current_date.strftime('%Y-%m-%d')}")
                    
                    # 檢查是否是節目行 - 使用更寬鬆的條件
                    elif text and re.match(r'\d{1,2}:\d{2}\s+.+', text):
                        time_match = re.match(r'(\d{1,2}):(\d{2})\s+(.+)', text)
                        if time_match and current_date:
                            hour = int(time_match.group(1))
                            minute = int(time_match.group(2))
                            program_name = time_match.group(3)
                            
                            # 計算節目開始時間
                            program_start = current_date.replace(hour=hour, minute=minute, second=0)
                            
                            # 預設節目時長為1小時
                            program_end = program_start + datetime.timedelta(hours=1)
                            
                            programs.append({
                                "channelName": channel_name,
                                "programName": program_name,
                                "description": "",
                                "subtitle": "",
                                "start": program_start,
                                "end": program_end
                            })
                            
                            print(f"   📺 節目: {hour:02d}:{minute:02d} - {program_name}")
        
        # 方法2: 如果上面沒找到，嘗試搜索整個文檔中的節目行
        if not programs:
            print("嘗試方法2: 搜索整個文檔中的節目行")
            all_elements = soup.find_all(text=re.compile(r'\d{1,2}:\d{2}\s+.+'))
            for element in all_elements:
                text = element.strip()
                time_match = re.match(r'(\d{1,2}):(\d{2})\s+(.+)', text)
                if time_match and current_date:
                    hour = int(time_match.group(1))
                    minute = int(time_match.group(2))
                    program_name = time_match.group(3)
                    
                    program_start = current_date.replace(hour=hour, minute=minute, second=0)
                    program_end = program_start + datetime.timedelta(hours=1)
                    
                    programs.append({
                        "channelName": channel_name,
                        "programName": program_name,
                        "description": "",
                        "subtitle": "",
                        "start": program_start,
                        "end": program_end
                    })
                    
                    print(f"   📺 節目: {hour:02d}:{minute:02d} - {program_name}")
        
        print(f"✅ 頻道 {channel_name} 獲取到 {len(programs)} 個節目")
        return programs
        
    except Exception as e:
        print(f"❌ 獲取頻道 {channel_name} 節目表失敗: {str(e)}")
        import traceback
        traceback.print_exc()
        return []

def get_litv_epg():
    """獲取LiTV電視節目表"""
    print("="*50)
    print("開始獲取LiTV電視節目表")
    print("="*50)
    
    # 創建會話
    session = create_session()
    
    # 獲取頻道清單
    channels_info = parse_channel_list(session)
    if not channels_info:
        print("❌ 無法獲取頻道清單")
        return [], [], []  # 返回三個空列表
    
    # 為每個頻道獲取節目表
    all_programs = []
    for channel in channels_info:
        channel_id = channel["id"]
        channel_name = channel["channelName"]
        
        # 獲取該頻道的節目表
        programs = fetch_channel_epg(session, channel_id, channel_name)
        all_programs.extend(programs)
        
        # 添加隨機延遲，避免請求過於頻繁
        delay = random.uniform(2, 5)
        print(f"等待 {delay:.1f} 秒後繼續...")
        time.sleep(delay)
    
    # 格式化頻道資訊（用於XMLTV生成）
    all_channels = []
    for channel in channels_info:
        channel_info = {
            "name": channel["channelName"],
            "channelName": channel["channelName"],
            "id": channel["id"],
            "url": f"https://www.litv.tv/channel/{channel['id']}",
            "source": "litv",
            "desc": channel.get("description", ""),
            "sort": "台灣"
        }
        
        if channel.get("logo"):
            channel_info["logo"] = channel["logo"]
        
        all_channels.append(channel_info)
    
    # 統計結果
    print("\n" + "="*50)
    print(f"✅ 成功獲取 {len(all_channels)} 個頻道")
    print(f"✅ 成功獲取 {len(all_programs)} 個節目")
    
    # 按頻道名稱分組顯示節目數量
    channel_counts = {}
    for program in all_programs:
        channel_counts[program["channelName"]] = channel_counts.get(program["channelName"], 0) + 1
    
    for channel, count in channel_counts.items():
        print(f"📺 頻道 {channel}: {count} 個節目")
    
    print("="*50)
    return channels_info, all_channels, all_programs  # 返回三個值

def generate_xmltv(channels, programs, output_file="litv.xml"):
    """生成XMLTV格式的EPG數據"""
    print(f"\n生成XMLTV檔案: {output_file}")
    
    if not channels or not programs:
        print("❌ 沒有頻道或節目數據，無法生成XMLTV")
        return False
    
    # 建立XML根元素
    root = ET.Element("tv", generator="LITV-EPG-Generator", source="www.litv.tv")
    
    # 頻道1 -> 頻道1節目 -> 頻道2-> 頻道2節目 -> ...
    program_count = 0
    for channel in channels:
        channel_name = channel['name']
        
        # 添加頻道定義
        channel_elem = ET.SubElement(root, "channel", id=channel_name)
        ET.SubElement(channel_elem, "display-name", lang="zh").text = channel_name
        
        if channel.get('logo'):
            ET.SubElement(channel_elem, "icon", src=channel['logo'])
        
        # 獲取該頻道的所有節目
        channel_programs = [p for p in programs if p['channelName'] == channel_name]
        if not channel_programs:
            print(f"⚠️ 頻道 {channel_name} 沒有節目數據")
            continue
            
        # 按開始時間排序
        channel_programs.sort(key=lambda p: p['start'])
        
        # 添加該頻道的所有節目
        for program in channel_programs:
            try:
                start_time = program['start'].strftime('%Y%m%d%H%M%S %z')
                end_time = program['end'].strftime('%Y%m%d%H%M%S %z')
                
                program_elem = ET.SubElement(
                    root, 
                    "programme", 
                    channel=channel_name,
                    start=start_time, 
                    stop=end_time
                )
                
                title = program.get('programName', '未知節目')
                ET.SubElement(program_elem, "title", lang="zh").text = title
                
                if program.get('subtitle'):
                    ET.SubElement(program_elem, "sub-title", lang="zh").text = program['subtitle']
                
                if program.get('description'):
                    ET.SubElement(program_elem, "desc", lang="zh").text = program['description']
                
                program_count += 1
            except Exception as e:
                print(f"⚠️ 跳過無效的節目數據: {str(e)}")
                continue
    
    # 生成XML字符串
    xml_str = ET.tostring(root, encoding='utf-8').decode('utf-8')
    
    # 美化XML格式
    try:
        parsed = minidom.parseString(xml_str)
        pretty_xml = parsed.toprettyxml(indent="  ", encoding='utf-8')
    except Exception as e:
        print(f"⚠️ XML美化失敗, 使用原始XML: {str(e)}")
        pretty_xml = xml_str.encode('utf-8')
    
    # 儲存到檔案
    try:
        with open(output_file, 'wb') as f:
            f.write(pretty_xml)
        
        print(f"✅ XMLTV檔案已生成: {output_file}")
        print(f"📺 頻道數: {len(channels)}")
        print(f"📺 節目數: {program_count}")
        print(f"💾 檔案大小: {os.path.getsize(output_file) / 1024:.2f} KB")
        return True
    except Exception as e:
        print(f"❌ 儲存XML檔案失敗: {str(e)}")
        return False

def generate_channel_json(channels_info, output_file="litv.json"):
    """生成JSON格式的頻道資訊"""
    print(f"\n生成JSON頻道檔案: {output_file}")
    
    if not channels_info:
        print("❌ 沒有頻道數據，無法生成JSON")
        return False
    
    try:
        # 格式化頻道資訊為所需的JSON格式
        json_channels = []
        for channel in channels_info:
            json_channel = {
                "channelName": channel["channelName"],
                "id": channel["id"],
                "logo": channel.get("logo", ""),
                "description": channel.get("description", "")
            }
            json_channels.append(json_channel)
        
        # 寫入JSON檔案
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(json_channels, f, ensure_ascii=False, indent=2)
        
        print(f"✅ JSON頻道檔案已生成: {output_file}")
        print(f"📺 頻道數: {len(json_channels)}")
        print(f"💾 檔案大小: {os.path.getsize(output_file) / 1024:.2f} KB")
        return True
        
    except Exception as e:
        print(f"❌ 生成JSON頻道檔案失敗: {str(e)}")
        return False

def main():
    """主函數，處理命令行參數"""
    parser = argparse.ArgumentParser(description='LiTV電視節目表')
    parser.add_argument('--output', type=str, default='output/litv.xml', 
                       help='輸出XML檔案路徑 (默認: output/litv.xml)')
    parser.add_argument('--json', type=str, default='output/litv.json',
                       help='輸出JSON頻道檔案路徑 (默認: output/litv.json)')
    parser.add_argument('--debug', action='store_true',
                       help='啟用調試模式，保存HTML文件')
    
    args = parser.parse_args()
    
    # 確保輸出目錄存在
    output_dir = os.path.dirname(args.output)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
        print(f"建立輸出目錄: {output_dir}")
    
    json_dir = os.path.dirname(args.json)
    if json_dir and not os.path.exists(json_dir):
        os.makedirs(json_dir, exist_ok=True)
        print(f"建立JSON輸出目錄: {json_dir}")
    
    try:
        # 獲取EPG數據
        channels_info, all_channels, programs = get_litv_epg()
        
        if not channels_info:
            print("❌ 未獲取到頻道數據，無法生成XML和JSON")
            sys.exit(1)
            
        # 生成XMLTV檔案
        if not generate_xmltv(all_channels, programs, args.output):
            print("⚠️ XMLTV檔案生成失敗，但繼續生成JSON檔案")
            
        # 生成JSON頻道檔案
        if not generate_channel_json(channels_info, args.json):
            print("❌ JSON頻道檔案生成失敗")
            sys.exit(1)
            
        print(f"\n🎉 所有檔案生成完成！")
        print(f"📄 XMLTV EPG檔案: {args.output}")
        print(f"📄 JSON頻道檔案: {args.json}")
            
    except Exception as e:
        print(f"❌ 主程序錯誤: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
