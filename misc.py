import requests
from io import BytesIO
from PIL import Image
import base64

from common.log import logger
from config import conf
from bridge.reply import Reply, ReplyType
from plugins.event import EventContext, EventAction

def is_gewe():
    return conf().get("channel_type", "") == "gewechat"

def get_card_image_url(message):
    api_url = "https://api.suyanw.cn/api/zt.php"
    try:
        response = requests.get(api_url, params={"msg": message})
        response.raise_for_status()
        
        # 检查响应内容类型是否为图片
        content_type = response.headers.get('Content-Type')
        if 'image' in content_type:
            logger.debug("Image content detected")
            return response.url
        
        data = response.json()
        return data.get("image")
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed: {e}")
        return None
    except ValueError:
        logger.error("Failed to parse JSON response")
        return None
    
def download_image_by_url(image_url):
    try:
        response = requests.get(image_url)
        response.raise_for_status()
        image_data = BytesIO(response.content)
        logger.info("Image downloaded successfully")
        return image_data
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to download image: {e}")
        return None

def download_image(path_img, content_xml):
    from channel.gewechat.gewechat_channel import GeWeChatChannel
    channel = GeWeChatChannel()
    
    try:
        try:
            xml_start = content_xml.find('<?xml version=')
            if xml_start != -1:
                content_xml = content_xml[xml_start:]
            image_info = channel.client.download_image(app_id=channel.app_id, xml=content_xml, type=1)
        except Exception as e:
            logger.warning(f"[gewechat] Failed to download high-quality image: {e}")
            # 尝试下载普通图片
            image_info = channel.client.download_image(app_id=channel.app_id, xml=content_xml, type=2)
        if image_info['ret'] == 200 and image_info['data']:
            file_url = image_info['data']['fileUrl']
            logger.info(f"[gewechat] Download image file from {file_url}")
            download_url = conf().get("gewechat_download_url").rstrip('/')
            full_url = download_url + '/' + file_url
            try:
                file_data = requests.get(full_url).content
            except Exception as e:
                logger.error(f"[gewechat] Failed to download image file: {e}")
                return
            with open(path_img, "wb") as f:
                f.write(file_data)
        else:
            logger.error(f"[gewechat] Failed to download image file: {image_info}")
    except Exception as e:
        logger.error(f"[gewechat] Failed to download image file: {e}")
        
def image_to_base64(image_path, max_size_mb=5, max_pixels=6000*6000):
    img = Image.open(image_path)
    
    if img.width * img.height > max_pixels:
        ratio = (max_pixels / (img.width * img.height)) ** 0.5
        new_width = int(img.width * ratio)
        new_height = int(img.height * ratio)
        img = img.resize((new_width, new_height), Image.ANTIALIAS)
    

    max_size_bytes = max_size_mb * 1024 * 1024
    
    while True:
        img_byte_arr = BytesIO()
        img.save(img_byte_arr, format='JPEG' if img.format == 'JPEG' else 'PNG')
        current_size = img_byte_arr.tell()
        
        if current_size <= max_size_bytes:
            break
        
        compression_ratio = (max_size_bytes / current_size) ** 0.5
        resize_ratio = min(0.9, compression_ratio)
        
        new_width = int(img.width * resize_ratio)
        new_height = int(img.height * resize_ratio)
        img = img.resize((new_width, new_height), Image.ANTIALIAS)
    
    img_base64 = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')
    
    return img_base64

def set_reply_text(content: str, e_context: EventContext, level: ReplyType = ReplyType.ERROR):
    reply = Reply(level, content)
    e_context["reply"] = reply
    e_context.action = EventAction.BREAK_PASS
    
def is_none_or_empty(s):
    return s is None or s == ""