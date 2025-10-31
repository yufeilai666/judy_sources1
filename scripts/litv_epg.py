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

# 全局时区设置
TAIPEI_TZ = pytz.timezone('Asia/Taipei')

# 代理设置
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
    """创建带有代理的会话"""
    session = requests.Session()
    session.headers.update(HEADERS)
    
    if PROXIES:
        print(f"使用代理: {PROXIES}")
        session.proxies.update(PROXIES)
    else:
        print("未设置代理，使用直接连接")
    
    return session

def parse_channel_list(session):
    """从LiTV API获取频道清单，只抓取特定ID模式的频道"""
    print("开始获取LiTV频道清单...")
    
    # LiTV频道API
    channel_url = "https://www.litv.tv/_next/data/322e31352e3138/channel.json"
    
    try:
        response = session.get(channel_url, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        
        # 从 pageProps.introduction.channels 获取频道列表
        channels_data = data.get('pageProps', {}).get('introduction', {}).get('channels', [])
        
        if not channels_data:
            print("❌ 未找到频道数据")
            return []
        
        print(f"找到 {len(channels_data)} 个频道")
        
        # 定义要抓取的频道ID模式
        target_patterns = [
            r'^4gtv-4gtv.*',      # 4gtv-4gtv开头的所有频道
            r'^litv-ftv.*',       # litv-ftv开头的所有频道
            r'^iNEWS$',           # 精确匹配iNEWS
            r'^litv-longturn.*'   # litv-longturn开头的所有频道
        ]
        
        channels = []
        for channel in channels_data:
            channel_name = channel.get('title', '').strip()
            channel_id = channel.get('cdn_code', '').strip()
            
            if not channel_name or not channel_id:
                continue
            
            # 检查频道ID是否符合目标模式
            is_target = False
            for pattern in target_patterns:
                if re.match(pattern, channel_id):
                    is_target = True
                    break
            
            if not is_target:
                continue
                
            # 处理logo URL
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
        
        print(f"✅ 成功获取 {len(channels)} 个目标频道")
        for channel in channels:
            print(f"   - {channel['channelName']} (ID: {channel['id']})")
        return channels
        
    except Exception as e:
        print(f"❌ 获取频道清单失败: {str(e)}")
        return []

def fetch_channel_epg(session, channel_id, channel_name):
    """从频道页面获取节目表数据 - 新方法"""
    print(f"\n开始获取频道 {channel_name} 的节目表...")
    
    # 频道页面URL
    channel_url = f"https://www.litv.tv/channel/watch/{channel_id}"
    
    try:
        response = session.get(channel_url, timeout=30)
        response.raise_for_status()
        
        # 解析HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 保存HTML用于调试
        with open(f"debug_{channel_id}.html", "w", encoding="utf-8") as f:
            f.write(soup.prettify())
        print(f"✅ 已保存HTML到 debug_{channel_id}.html 用于调试")
        
        programs = []
        
        # 方法1: 查找包含节目表的容器
        # 根据您提供的图片，节目表可能在一个特定的容器中
        epg_containers = soup.find_all('div', class_=lambda x: x and 'overflow-y-auto' in x)
        
        if not epg_containers:
            print("❌ 未找到节目表容器")
            return []
        
        print(f"找到 {len(epg_containers)} 个可能的节目表容器")
        
        for container in epg_containers:
            # 检查容器是否包含节目信息
            container_text = container.get_text(strip=True)
            if re.search(r'\d{1,2}:\d{2}\s+.+', container_text):
                print("✅ 找到包含节目信息的容器")
                
                # 查找所有日期标题
                date_headers = container.find_all('div', class_=lambda x: x and 'text-[#fff]' in x)
                print(f"找到 {len(date_headers)} 个日期标题")
                
                for date_header in date_headers:
                    date_text = date_header.get_text(strip=True)
                    print(f"处理日期: {date_text}")
                    
                    # 解析日期
                    current_date = parse_date_from_text(date_text)
                    if not current_date:
                        continue
                    
                    # 查找这个日期下的所有节目
                    # 查找日期标题后面的所有节目行
                    next_elem = date_header.find_next_sibling()
                    while next_elem:
                        # 检查是否是节目行
                        if next_elem.name == 'div' and next_elem.get('class'):
                            class_str = ' '.join(next_elem.get('class', []))
                            program_text = next_elem.get_text(strip=True)
                            
                            # 检查是否是新的日期标题
                            if re.search(r'\d+月\d+日', program_text):
                                break
                                
                            # 检查是否是节目行
                            time_match = re.match(r'(\d{1,2}):(\d{2})\s+(.+)', program_text)
                            if time_match:
                                hour = int(time_match.group(1))
                                minute = int(time_match.group(2))
                                program_name = time_match.group(3)
                                
                                # 计算节目开始时间
                                program_start = current_date.replace(hour=hour, minute=minute, second=0)
                                
                                # 预设节目时长为1小时
                                program_end = program_start + datetime.timedelta(hours=1)
                                
                                programs.append({
                                    "channelName": channel_name,
                                    "programName": program_name,
                                    "description": "",
                                    "subtitle": "",
                                    "start": program_start,
                                    "end": program_end
                                })
                                
                                print(f"  节目: {hour:02d}:{minute:02d} - {program_name}")
                        
                        next_elem = next_elem.find_next_sibling()
                
                break  # 只处理第一个有效的节目表容器
        
        # 方法2: 如果上面没找到，尝试直接搜索所有包含时间和节目名称的元素
        if not programs:
            print("尝试方法2: 直接搜索所有节目行")
            all_elements = soup.find_all(text=re.compile(r'\d{1,2}:\d{2}\s+.+'))
            for element in all_elements:
                text = element.strip()
                time_match = re.match(r'(\d{1,2}):(\d{2})\s+(.+)', text)
                if time_match:
                    # 使用当前日期作为默认值
                    current_date = datetime.datetime.now(TAIPEI_TZ).replace(hour=0, minute=0, second=0)
                    
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
                    
                    print(f"  节目: {hour:02d}:{minute:02d} - {program_name}")
        
        print(f"✅ 频道 {channel_name} 获取到 {len(programs)} 个节目")
        return programs
        
    except Exception as e:
        print(f"❌ 获取频道 {channel_name} 节目表失败: {str(e)}")
        return []

def parse_date_from_text(date_text):
    """从日期文本解析日期"""
    try:
        # 处理 "今日 / 11月1日 / 星期六" 或 "明日 / 11月2日 / 星期五" 格式
        parts = date_text.split(' / ')
        if len(parts) >= 2:
            date_part = parts[1]  # "11月1日"
            
            # 获取当前年份
            current_year = datetime.datetime.now().year
            
            # 解析月份和日期
            month_match = re.search(r'(\d+)月', date_part)
            day_match = re.search(r'(\d+)日', date_part)
            
            if month_match and day_match:
                month = int(month_match.group(1))
                day = int(day_match.group(1))
                
                # 创建日期对象
                date_obj = datetime.datetime(current_year, month, day, tzinfo=TAIPEI_TZ)
                return date_obj
    except Exception as e:
        print(f"⚠️ 日期解析失败: {date_text}, 错误: {str(e)}")
    
    return None

def get_litv_epg():
    """获取LiTV电视节目表"""
    print("="*50)
    print("开始获取LiTV电视节目表")
    print("="*50)
    
    # 创建会话
    session = create_session()
    
    # 获取频道清单
    channels_info = parse_channel_list(session)
    if not channels_info:
        print("❌ 无法获取频道清单")
        return [], [], []  # 返回三个空列表
    
    # 为每个频道获取节目表
    all_programs = []
    for channel in channels_info:
        channel_id = channel["id"]
        channel_name = channel["channelName"]
        
        # 获取该频道的节目表
        programs = fetch_channel_epg(session, channel_id, channel_name)
        all_programs.extend(programs)
        
        # 添加随机延迟，避免请求过于频繁
        delay = random.uniform(2, 5)
        print(f"等待 {delay:.1f} 秒后继续...")
        time.sleep(delay)
    
    # 格式化频道资讯（用于XMLTV生成）
    all_channels = []
    for channel in channels_info:
        channel_info = {
            "name": channel["channelName"],
            "channelName": channel["channelName"],
            "id": channel["id"],
            "url": f"https://www.litv.tv/channel/{channel['id']}",
            "source": "litv",
            "desc": channel.get("description", ""),
            "sort": "台湾"
        }
        
        if channel.get("logo"):
            channel_info["logo"] = channel["logo"]
        
        all_channels.append(channel_info)
    
    # 统计结果
    print("\n" + "="*50)
    print(f"✅ 成功获取 {len(all_channels)} 个频道")
    print(f"✅ 成功获取 {len(all_programs)} 个节目")
    
    # 按频道名称分组显示节目数量
    channel_counts = {}
    for program in all_programs:
        channel_counts[program["channelName"]] = channel_counts.get(program["channelName"], 0) + 1
    
    for channel, count in channel_counts.items():
        print(f"📺 频道 {channel}: {count} 个节目")
    
    print("="*50)
    return channels_info, all_channels, all_programs

def generate_xmltv(channels, programs, output_file="litv.xml"):
    """生成XMLTV格式的EPG数据"""
    print(f"\n生成XMLTV档案: {output_file}")
    
    if not channels or not programs:
        print("❌ 没有频道或节目数据，无法生成XMLTV")
        return False
    
    # 建立XML根元素
    root = ET.Element("tv", generator="LITV-EPG-Generator", source="www.litv.tv")
    
    program_count = 0
    for channel in channels:
        channel_name = channel['name']
        
        # 添加频道定义
        channel_elem = ET.SubElement(root, "channel", id=channel_name)
        ET.SubElement(channel_elem, "display-name", lang="zh").text = channel_name
        
        if channel.get('logo'):
            ET.SubElement(channel_elem, "icon", src=channel['logo'])
        
        # 获取该频道的所有节目
        channel_programs = [p for p in programs if p['channelName'] == channel_name]
        if not channel_programs:
            print(f"⚠️ 频道 {channel_name} 没有节目数据")
            continue
            
        # 按开始时间排序
        channel_programs.sort(key=lambda p: p['start'])
        
        # 添加该频道的所有节目
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
                
                title = program.get('programName', '未知节目')
                ET.SubElement(program_elem, "title", lang="zh").text = title
                
                program_count += 1
            except Exception as e:
                print(f"⚠️ 跳过无效的节目数据: {str(e)}")
                continue
    
    # 生成XML字符串
    xml_str = ET.tostring(root, encoding='utf-8').decode('utf-8')
    
    # 美化XML格式
    try:
        parsed = minidom.parseString(xml_str)
        pretty_xml = parsed.toprettyxml(indent="  ", encoding='utf-8')
    except Exception as e:
        print(f"⚠️ XML美化失败, 使用原始XML: {str(e)}")
        pretty_xml = xml_str.encode('utf-8')
    
    # 储存到档案
    try:
        with open(output_file, 'wb') as f:
            f.write(pretty_xml)
        
        print(f"✅ XMLTV档案已生成: {output_file}")
        print(f"📺 频道数: {len(channels)}")
        print(f"📺 节目数: {program_count}")
        return True
    except Exception as e:
        print(f"❌ 储存XML档案失败: {str(e)}")
        return False

def generate_channel_json(channels_info, output_file="litv.json"):
    """生成JSON格式的频道资讯"""
    print(f"\n生成JSON频道档案: {output_file}")
    
    if not channels_info:
        print("❌ 没有频道数据，无法生成JSON")
        return False
    
    try:
        # 格式化频道资讯为所需的JSON格式
        json_channels = []
        for channel in channels_info:
            json_channel = {
                "channelName": channel["channelName"],
                "id": channel["id"],
                "logo": channel.get("logo", ""),
                "description": channel.get("description", "")
            }
            json_channels.append(json_channel)
        
        # 写入JSON档案
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(json_channels, f, ensure_ascii=False, indent=2)
        
        print(f"✅ JSON频道档案已生成: {output_file}")
        print(f"📺 频道数: {len(json_channels)}")
        return True
        
    except Exception as e:
        print(f"❌ 生成JSON频道档案失败: {str(e)}")
        return False

def main():
    """主函数，处理命令行参数"""
    parser = argparse.ArgumentParser(description='LiTV电视节目表')
    parser.add_argument('--output', type=str, default='output/litv.xml', 
                       help='输出XML档案路径 (默认: output/litv.xml)')
    parser.add_argument('--json', type=str, default='output/litv.json',
                       help='输出JSON频道档案路径 (默认: output/litv.json)')
    
    args = parser.parse_args()
    
    # 确保输出目录存在
    output_dir = os.path.dirname(args.output)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
        print(f"建立输出目录: {output_dir}")
    
    json_dir = os.path.dirname(args.json)
    if json_dir and not os.path.exists(json_dir):
        os.makedirs(json_dir, exist_ok=True)
        print(f"建立JSON输出目录: {json_dir}")
    
    try:
        # 获取EPG数据
        channels_info, all_channels, programs = get_litv_epg()
        
        if not channels_info:
            print("❌ 未获取到频道数据，无法生成XML和JSON")
            sys.exit(1)
            
        # 生成XMLTV档案
        if not generate_xmltv(all_channels, programs, args.output):
            print("⚠️ XMLTV档案生成失败，但继续生成JSON档案")
            
        # 生成JSON频道档案
        if not generate_channel_json(channels_info, args.json):
            print("❌ JSON频道档案生成失败")
            sys.exit(1)
            
        print(f"\n🎉 所有档案生成完成！")
        print(f"📄 XMLTV EPG档案: {args.output}")
        print(f"📄 JSON频道档案: {args.json}")
            
    except Exception as e:
        print(f"❌ 主程序错误: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
