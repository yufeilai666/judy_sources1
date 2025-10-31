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
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def parse_channel_list():
    """從網頁動態解析頻道清單"""
    # 嘗試使用頻道列表頁面
    url = "https://www.ofiii.com/channel"
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 方法1: 從__NEXT_DATA__中解析（增強版）
        script_tag = soup.find('script', id='__NEXT_DATA__')
        if script_tag and script_tag.string:
            try:
                data = json.loads(script_tag.string)
                print("🔍 分析__NEXT_DATA__結構...")
                
                # 增強的解析方法
                channels_from_next_data = extract_channels_from_next_data_enhanced(data)
                if channels_from_next_data:
                    print(f"✅ 從__NEXT_DATA__解析到 {len(channels_from_next_data)} 個頻道")
                    return channels_from_next_data
                else:
                    print("⚠️ __NEXT_DATA__中未找到頻道列表，嘗試調試...")
                    debug_next_data(data)  # 調試函數，幫助分析數據結構
                    
            except json.JSONDecodeError as e:
                print(f"⚠️ __NEXT_DATA__ JSON解析失敗: {str(e)}")
        
        # 方法2: 從HTML中解析所有頻道鏈接
        print("🔍 從HTML鏈接解析頻道...")
        channel_links = soup.find_all('a', href=re.compile(r'/channel/watch/'))
        if not channel_links:
            print("❌ 未找到頻道鏈接")
            return []
        
        channel_list = []
        for link in channel_links:
            try:
                href = link.get('href', '')
                # 提取頻道ID（/channel/watch/後面的部分）
                if '/channel/watch/' in href:
                    channel_id = href.split('/channel/watch/')[-1].strip('/')
                    if channel_id and channel_id not in channel_list:
                        channel_list.append(channel_id)
            except Exception as e:
                print(f"⚠️ 解析頻道鏈接失敗: {str(e)}")
                continue
        
        print(f"✅ 從HTML鏈接解析到 {len(channel_list)} 個頻道")
        
        # 如果HTML解析的數量較少，嘗試其他方法補充
        if len(channel_list) < 50:  # 假設實際頻道數應該大於50
            print("⚠️ 頻道數量較少，嘗試其他方法補充...")
            additional_channels = get_additional_channels(url, soup)
            for channel in additional_channels:
                if channel not in channel_list:
                    channel_list.append(channel)
            
            print(f"✅ 補充後共有 {len(channel_list)} 個頻道")
        
        return channel_list
        
    except Exception as e:
        print(f"❌ 動態獲取頻道列表失敗: {str(e)}")
        return []

def extract_channels_from_next_data_enhanced(data):
    """增強版：從__NEXT_DATA__中提取頻道列表"""
    channels = []
    
    try:
        # 方法1: 標準Next.js結構
        props = data.get('props', {})
        page_props = props.get('pageProps', {})
        
        # 嘗試不同的可能字段名和嵌套結構
        possible_paths = [
            ['props', 'pageProps', 'channels'],
            ['props', 'pageProps', 'channelList'],
            ['props', 'pageProps', 'items'],
            ['props', 'pageProps', 'data'],
            ['props', 'pageProps', 'initialState', 'channels'],
            ['props', 'pageProps', 'dehydratedState', 'queries'],
            ['props', 'pageProps', '__APOLLO_STATE__'],
            ['buildId'],
            ['page'],
            ['query'],
        ]
        
        for path in possible_paths:
            result = get_nested_value(data, path)
            if result:
                extracted = extract_channels_from_object(result)
                channels.extend(extracted)
        
        # 方法2: 搜索整個數據結構中的頻道模式
        if not channels:
            channels = search_channels_in_data_enhanced(data)
        
        # 去重
        channels = list(set(channels))
        
    except Exception as e:
        print(f"⚠️ 從__NEXT_DATA__提取頻道失敗: {str(e)}")
    
    return channels

def get_nested_value(obj, keys):
    """安全地獲取嵌套字典的值"""
    try:
        for key in keys:
            if isinstance(obj, dict) and key in obj:
                obj = obj[key]
            else:
                return None
        return obj
    except:
        return None

def extract_channels_from_object(obj):
    """從對象中提取頻道ID"""
    channels = []
    
    if isinstance(obj, list):
        for item in obj:
            channels.extend(extract_channels_from_object(item))
    elif isinstance(obj, dict):
        # 檢查常見頻道ID字段
        for key in ['id', 'channelId', 'slug', 'code', 'name', 'key']:
            if key in obj and isinstance(obj[key], str):
                channel_id = obj[key]
                if is_valid_channel_id(channel_id):
                    channels.append(channel_id)
        
        # 遞歸檢查所有值
        for value in obj.values():
            channels.extend(extract_channels_from_object(value))
    
    return channels

def is_valid_channel_id(channel_id):
    """檢查是否為有效的頻道ID"""
    if not isinstance(channel_id, str):
        return False
    
    # 有效的頻道ID模式
    patterns = [
        r'^4gtv-',
        r'^litv-',
        r'^ofiii',
        r'^nnews-',
        r'^iNEWS',
        r'^daystar',
    ]
    
    for pattern in patterns:
        if re.search(pattern, channel_id):
            return True
    
    return False

def search_channels_in_data_enhanced(data, max_depth=5):
    """增強版：在數據結構中遞歸搜索頻道ID"""
    channels = []
    
    def _search(obj, depth=0, path=""):
        if depth > max_depth:
            return
        
        if isinstance(obj, dict):
            for key, value in obj.items():
                current_path = f"{path}.{key}" if path else key
                # 如果值是字符串，檢查是否是頻道ID
                if isinstance(value, str) and is_valid_channel_id(value):
                    if value not in channels:
                        channels.append(value)
                        print(f"🔍 在路徑 {current_path} 找到頻道: {value}")
                else:
                    _search(value, depth + 1, current_path)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                current_path = f"{path}[{i}]" if path else f"[{i}]"
                _search(item, depth + 1, current_path)
    
    _search(data)
    return channels

def debug_next_data(data):
    """調試函數：分析__NEXT_DATA__結構"""
    print("🔍 調試__NEXT_DATA__結構:")
    
    # 打印頂層鍵
    print("頂層鍵:", list(data.keys()))
    
    # 檢查props結構
    props = data.get('props', {})
    if props:
        print("props鍵:", list(props.keys()))
        
        page_props = props.get('pageProps', {})
        if page_props:
            print("pageProps鍵:", list(page_props.keys()))
    
    # 查找所有包含"channel"的鍵
    def find_channel_keys(obj, path=""):
        channel_keys = []
        
        if isinstance(obj, dict):
            for key, value in obj.items():
                current_path = f"{path}.{key}" if path else key
                if "channel" in key.lower():
                    channel_keys.append(current_path)
                channel_keys.extend(find_channel_keys(value, current_path))
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                current_path = f"{path}[{i}]" if path else f"[{i}]"
                channel_keys.extend(find_channel_keys(item, current_path))
        
        return channel_keys
    
    channel_keys = find_channel_keys(data)
    if channel_keys:
        print("包含'channel'的鍵:", channel_keys[:10])  # 只顯示前10個
    
    # 統計數據結構大小
    def count_items(obj):
        if isinstance(obj, dict):
            return 1 + sum(count_items(v) for v in obj.values())
        elif isinstance(obj, list):
            return 1 + sum(count_items(item) for item in obj)
        else:
            return 1
    
    print("數據結構大小:", count_items(data))

def get_additional_channels(url, soup):
    """獲取額外的頻道列表"""
    additional_channels = []
    
    # 方法1: 查找可能的API端點
    scripts = soup.find_all('script')
    for script in scripts:
        if script.string:
            # 查找可能的API URL
            api_patterns = [
                r'https?://[^"\']+api[^"\']+channels[^"\']*',
                r'https?://[^"\']+channels[^"\']*',
                r'/api/[^"\']+channels[^"\']*',
            ]
            
            for pattern in api_patterns:
                matches = re.findall(pattern, script.string)
                for match in matches:
                    print(f"🔍 發現可能的API端點: {match}")
                    # 這里可以添加調用API的代碼
    
    # 方法2: 查找其他可能的頻道列表容器
    containers = soup.find_all(['div', 'section'], class_=re.compile(r'.*(list|grid|container|channel).*', re.I))
    for container in containers:
        links = container.find_all('a', href=re.compile(r'/channel/watch/'))
        for link in links:
            href = link.get('href', '')
            if '/channel/watch/' in href:
                channel_id = href.split('/channel/watch/')[-1].strip('/')
                if channel_id and channel_id not in additional_channels:
                    additional_channels.append(channel_id)
    
    return additional_channels

def fetch_epg_data(channel_id, max_retries=3):
    """獲取指定頻道的電視節目表數據"""
    url = f"https://www.ofiii.com/channel/watch/{channel_id}"
    
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            
            if not response.text.strip():
                print(f"⚠️ 響應內容為空: {channel_id}")
                return None
                
            soup = BeautifulSoup(response.text, 'html.parser')
            script_tag = soup.find('script', id='__NEXT_DATA__')
            
            if script_tag and script_tag.string:
                try:
                    return json.loads(script_tag.string)
                except json.JSONDecodeError as e:
                    print(f"⚠️ JSON解析失敗: {channel_id}, {str(e)}")
                    return None
            else:
                print(f"⚠️ 未找到__NEXT_DATA__標簽: {channel_id}")
                return None
                
        except requests.RequestException as e:
            wait_time = random.uniform(1, 3) * (attempt + 1)
            print(f"⚠️ 請求失敗 (嘗試 {attempt+1}/{max_retries}), 等待 {wait_time:.2f}秒: {str(e)}")
            time.sleep(wait_time)
    
    print(f"❌ 無法獲取 電視節目表 數據: {channel_id}")
    return None

def parse_live_epg_data(json_data, channel_id):
    """解析直播頻道的電視節目表 JSON數據"""
    if not json_data:
        return []
    
    programs = []
    try:
        if not json_data.get('props') or not json_data['props'].get('pageProps') or not json_data['props']['pageProps'].get('channel'):
            print(f"❌ JSON結構無效: {channel_id}")
            return []
        
        schedule = json_data['props']['pageProps']['channel'].get('Schedule', [])
        
        for item in schedule:
            try:
                start_utc = datetime.datetime.strptime(
                    item['AirDateTime'], "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=pytz.utc)
                start_taipei = start_utc.astimezone(TAIPEI_TZ)
                
                duration = datetime.timedelta(seconds=item.get('Duration', 0))
                end_taipei = start_taipei + duration
                
                program_info = item.get('program', {})
                
                programs.append({
                    "channelName": channel_id,
                    "programName": program_info.get('Title', '未知節目'),
                    "description": program_info.get('Description', ''),
                    "subtitle": program_info.get('SubTitle', ''),
                    "start": start_taipei,
                    "end": end_taipei
                })
                
            except (KeyError, ValueError, TypeError) as e:
                print(f"⚠️ 跳過無效的節目數據: {channel_id}, {str(e)}")
                continue
                
    except (KeyError, TypeError, ValueError) as e:
        print(f"❌ 解析直播電視節目表數據失敗: {str(e)}")
    
    return programs

def parse_vod_epg_data(json_data, channel_id):
    """解析點播頻道的電視節目表 JSON數據"""
    if not json_data:
        return []
    
    programs = []
    try:
        if not json_data.get('props') or not json_data['props'].get('pageProps') or not json_data['props']['pageProps'].get('channel'):
            print(f"❌ JSON結構無效: {channel_id}")
            return []
        
        channel_data = json_data['props']['pageProps']['channel']
        vod_schedule = channel_data.get('vod_channel_schedule', {})
        
        if not vod_schedule:
            print(f"⚠️ 點播頻道 {channel_id} 沒有節目表數據")
            return []
        
        vod_programs = vod_schedule.get('programs', [])
        
        for item in vod_programs:
            try:
                start_timestamp = item.get('p_start', 0)
                if start_timestamp == 0:
                    continue
                    
                start_taipei = datetime.datetime.fromtimestamp(start_timestamp / 1000, TAIPEI_TZ)
                
                duration_ms = item.get('length', 0)
                duration = datetime.timedelta(milliseconds=duration_ms)
                end_taipei = start_taipei + duration
                
                programs.append({
                    "channelName": channel_id,
                    "programName": item.get('title', '未知節目'),
                    "description": item.get('vod_channel_description', ''),
                    "subtitle": item.get('subtitle', ''),
                    "start": start_taipei,
                    "end": end_taipei
                })
                
            except (KeyError, ValueError, TypeError) as e:
                print(f"⚠️ 跳過無效的時間格式: {channel_id}, {str(e)}")
                continue
            
    except (KeyError, TypeError, ValueError) as e:
        print(f"❌ 解析點播電視節目表數據失敗: {str(e)}")
    
    return programs

def parse_epg_data(json_data, channel_id):
    """解析電視節目表 JSON數據，自動判斷直播或點播"""
    if not json_data:
        return []
    
    try:
        channel_data = json_data['props']['pageProps']['channel']
        content_type = channel_data.get('content_type', '')
        
        if content_type == 'vod-channel' or channel_data.get('vod_channel_schedule'):
            print(f"📹 檢測到點播頻道: {channel_id}")
            return parse_vod_epg_data(json_data, channel_id)
        else:
            print(f"📺 檢測到直播頻道: {channel_id}")
            return parse_live_epg_data(json_data, channel_id)
            
    except (KeyError, TypeError, ValueError) as e:
        print(f"❌ 判斷頻道類型失敗: {str(e)}")
        return parse_live_epg_data(json_data, channel_id)

def get_channel_info(json_data, channel_id):
    """從JSON數據中提取頻道信息"""
    if not json_data:
        return None
    
    try:
        page_props = json_data.get('props', {}).get('pageProps', {})
        channel_data = page_props.get('channel', {})
        
        # 獲取頻道名稱
        channel_name = channel_data.get('title', channel_id)
        
        # 獲取頻道logo
        logo = channel_data.get('picture', '')
        if logo and not logo.startswith("http"):
            logo = f"https://p-cdnstatic.svc.litv.tv/{logo}"
            # 將logo路徑中的_tv替換為_mobile以獲取移動版logo
            if '_tv' in logo:
                logo = logo.replace('_tv', '_mobile')
        
        # 獲取頻道描述
        description = channel_data.get('description', '')
        
        return {
            "channelName": channel_name,
            "id": channel_id,
            "logo": logo,
            "description": description
        }
    except Exception as e:
        print(f"❌ 提取頻道信息失敗: {channel_id}, {str(e)}")
        return None

def get_ofiii_epg():
    """獲取歐飛電視節目表"""
    print("="*50)
    print("開始獲取歐飛電視節目表")
    print("="*50)
    
    # 獲取頻道清單
    channels = parse_channel_list()
    if not channels:
        print("❌ 無法解析頻道清單")
        return [], []
    
    all_channels_info = []
    all_programs = []
    failed_channels = []
    
    # 遍歷所有頻道
    for idx, channel_id in enumerate(channels):
        print(f"\n處理頻道 [{idx+1}/{len(channels)}]: {channel_id}")
        
        # 獲取EPG數據
        json_data = fetch_epg_data(channel_id)
        if not json_data:
            failed_channels.append(channel_id)
            continue
            
        # 提取頻道信息
        channel_info = get_channel_info(json_data, channel_id)
        if channel_info:
            all_channels_info.append(channel_info)
        
        # 解析節目數據
        programs = parse_epg_data(json_data, channel_id)
        all_programs.extend(programs)
            
        # 隨機延遲
        if idx < len(channels) - 1:
            delay = random.uniform(1, 3)
            print(f"⏱️ 隨機延遲 {delay:.2f}秒")
            time.sleep(delay)
    
    # 統計結果
    print("\n" + "="*50)
    print(f"✅ 成功獲取 {len(all_channels_info)} 個頻道信息")
    print(f"✅ 成功獲取 {len(all_programs)} 個節目")
    
    if failed_channels:
        print(f"⚠️ 失敗頻道 ({len(failed_channels)}): {', '.join(failed_channels)}")
    
    channel_counts = {}
    for program in all_programs:
        channel_counts[program["channelName"]] = channel_counts.get(program["channelName"], 0) + 1
    
    for channel, count in channel_counts.items():
        print(f"📺 頻道 {channel}: {count} 個節目")
    
    print("="*50)
    return all_channels_info, all_programs

def generate_xmltv(channels_info, programs, output_file="ofiii.xml"):
    """生成XMLTV格式的EPG數據"""
    print(f"\n生成XMLTV檔案: {output_file}")
    
    root = ET.Element("tv", generator="OFIII-EPG-Generator", source="www.ofiii.com")
    
    # 添加頻道定義
    for channel in channels_info:
        channel_id = channel['id']
        channel_name = channel['channelName']
        
        channel_elem = ET.SubElement(root, "channel", id=channel_id)
        ET.SubElement(channel_elem, "display-name", lang="zh").text = channel_name
        
        if channel.get('logo'):
            ET.SubElement(channel_elem, "icon", src=channel['logo'])
    
    # 添加節目
    program_count = 0
    for program in programs:
        try:
            channel_id = program['channelName']
            start_time = program['start'].strftime('%Y%m%d%H%M%S %z')
            end_time = program['end'].strftime('%Y%m%d%H%M%S %z')
            
            program_elem = ET.SubElement(
                root, 
                "programme", 
                channel=channel_id,
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
    
    # 生成XML
    xml_str = ET.tostring(root, encoding='utf-8').decode('utf-8')
    
    try:
        parsed = minidom.parseString(xml_str)
        pretty_xml = parsed.toprettyxml(indent="  ", encoding='utf-8')
    except Exception as e:
        print(f"⚠️ XML美化失敗, 使用原始XML: {str(e)}")
        pretty_xml = xml_str.encode('utf-8')
    
    try:
        with open(output_file, 'wb') as f:
            f.write(pretty_xml)
        
        print(f"✅ XMLTV檔案已生成: {output_file}")
        print(f"📺 頻道數: {len(channels_info)}")
        print(f"📺 節目數: {program_count}")
        print(f"💾 檔案大小: {os.path.getsize(output_file) / 1024:.2f} KB")
        return True
    except Exception as e:
        print(f"❌ 儲存XML檔案失敗: {str(e)}")
        return False

def generate_json_file(channels_info, output_file="ofiii.json"):
    """生成JSON格式的頻道數據"""
    print(f"\n生成JSON檔案: {output_file}")
    
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(channels_info, f, ensure_ascii=False, indent=2)
        
        print(f"✅ JSON檔案已生成: {output_file}")
        print(f"📺 頻道數: {len(channels_info)}")
        print(f"💾 檔案大小: {os.path.getsize(output_file) / 1024:.2f} KB")
        
        # 顯示前幾個頻道作為示例
        print("\nJSON檔案前5個頻道示例:")
        for i, channel in enumerate(channels_info[:5]):
            print(f"  {i+1}. {channel}")
            
        return True
    except Exception as e:
        print(f"❌ 儲存JSON檔案失敗: {str(e)}")
        return False

def main():
    """主函數，處理命令行參數"""
    parser = argparse.ArgumentParser(description='歐飛電視節目表')
    parser.add_argument('--output', type=str, default='output/ofiii.xml', 
                       help='輸出XML檔案路徑 (默認: output/ofiii.xml)')
    
    args = parser.parse_args()
    
    # 確保輸出目錄存在
    output_dir = os.path.dirname(args.output)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
        print(f"建立輸出目錄: {output_dir}")
    
    try:
        # 獲取EPG數據
        channels_info, programs = get_ofiii_epg()
        
        if not channels_info:
            print("❌ 未獲取到有效頻道信息，無法生成檔案")
            sys.exit(1)
            
        # 生成XMLTV檔案
        xml_output = args.output
        if not generate_xmltv(channels_info, programs, xml_output):
            sys.exit(1)
            
        # 生成JSON檔案
        json_output = os.path.join(output_dir, "ofiii.json")
        if not generate_json_file(channels_info, json_output):
            print("⚠️ JSON檔案生成失敗，但XML已成功生成")
            
    except Exception as e:
        print(f"❌ 主程序錯誤: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
