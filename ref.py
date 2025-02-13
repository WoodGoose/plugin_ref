# encoding:utf-8
import io
import plugins
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from channel.chat_message import ChatMessage
from common.log import logger
from plugins import *
from config import conf
from .misc import *
import xml.etree.ElementTree as ET
from common.expired_dict import ExpiredDict
from PIL import Image, ImageFilter
from io import BytesIO

from common.tmp_dir import TmpDir


@plugins.register(
    name="Ref",
    desire_priority=9,
    hidden=True,
    desc="A simple plugin that do ref test",
    version="0.1",
    author="fred",
)
class Ref(Plugin):
    def __init__(self):
        super().__init__()
        try:
            self.config = super().load_config()
            if not self.config:
                self.config = self._load_config_template()
            self.zhipu_api_key = self.config.get("zhipu_api_key")
            self.zhipu_image_model = self.config.get("zhipu_image_model")
            logger.info("[Ref] inited")
            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        except Exception as e:
            logger.error(f"[Ref]初始化异常：{e}")
            raise "[Ref] init failed, ignore "
        self.msg_cache = ExpiredDict(60 * 3)

    def handle_ref(self, e_context):
        if not is_gewe():
            return
        msg = e_context["context"]["msg"]
        raw_msg = msg.msg
        msg_type = raw_msg["Data"]["MsgType"]
        msg_id = str(raw_msg["Data"]["NewMsgId"])
        # cache image & emoji msg
        if msg_type == 3 or msg_type == 47:
            self.msg_cache[msg_id] = msg

        # ref: https://github.com/LC044/WeChatMsg/blob/master/doc/%E6%95%B0%E6%8D%AE%E5%BA%93%E4%BB%8B%E7%BB%8D.md
        if msg_type == 49:
            content_xml = raw_msg["Data"]["Content"]["string"]
            # Find the position of '<?xml' declaration and remove any prefix
            xml_start = content_xml.find("<?xml version=")
            if xml_start != -1 and xml_start != 0:
                logger.warning("notice this!!!!!!!!!!!!!!!!!!!!")
                logger.warning(content_xml)
                content_xml = content_xml[xml_start:]
            root = ET.fromstring(content_xml)
            appmsg = root.find("appmsg")
            if appmsg is not None:
                msg_type = appmsg.find("type")
                if msg_type is not None and msg_type.text == "57":  # ref text
                    title = appmsg.find("title").text
                    if msg.is_group:
                        logger.debug(f"[Ref]: in group{title}")
                        import re

                        title = re.sub(r"@[^\u2005]+\u2005", "", title)
                    title = title.strip()
                    refermsg = appmsg.find("refermsg")

                    if refermsg is not None:
                        ref_type = refermsg.find("type").text
                        svrid = refermsg.find("svrid").text
                        if ref_type == "1":  # TEXT
                            if title in ["举", "举牌"]:
                                quoted_content = refermsg.find("content").text
                                image_url = get_card_image_url(quoted_content)
                                if image_url:
                                    image_data = download_image_by_url(image_url)
                                    if image_data:
                                        # convert to jpg format
                                        image_data.seek(0)
                                        image = Image.open(image_data)
                                        jpg_image_data = BytesIO()
                                        image.convert("RGB").save(
                                            jpg_image_data, format="JPEG"
                                        )
                                        jpg_image_data.seek(0)

                                        reply = Reply()
                                        reply.type = ReplyType.IMAGE
                                        reply.content = jpg_image_data
                                        e_context["reply"] = reply
                                        e_context.action = EventAction.BREAK_PASS
                        elif ref_type == "3":  # IMAGE
                            filters = {
                                "BLUR": ImageFilter.BLUR,
                                "CONTOUR": ImageFilter.CONTOUR,
                                "DETAIL": ImageFilter.DETAIL,
                                "EDGE_ENHANCE": ImageFilter.EDGE_ENHANCE,
                                "EDGE_ENHANCE_MORE": ImageFilter.EDGE_ENHANCE_MORE,
                                "EMBOSS": ImageFilter.EMBOSS,
                                "FIND_EDGES": ImageFilter.FIND_EDGES,
                                "SHARPEN": ImageFilter.SHARPEN,
                                "SMOOTH": ImageFilter.SMOOTH,
                                "SMOOTH_MORE": ImageFilter.SMOOTH_MORE,
                            }

                            path_image = None
                            ref_image_msg = self.msg_cache.get(svrid)
                            if ref_image_msg:
                                ref_image_msg.prepare()
                                path_image = ref_image_msg.content
                                logger.debug(f"[Ref]{path_image}")
                            else:
                                path_image_me = TmpDir().path() + svrid + ".png"
                                logger.debug(f"[Ref]isfile {path_image_me}")
                                if os.path.isfile(path_image_me):
                                    path_image = path_image_me
                            if not path_image or not os.path.isfile(path_image):
                                logger.debug(f"[Ref]image path not ready: {path_image}")
                                return

                            if title in filters.keys():
                                with open(path_image, "rb") as img_file:
                                    byte_arr = io.BytesIO(img_file.read())
                                    byte_arr.seek(0)
                                    filter_img = Image.open(byte_arr).filter(
                                        filters[title]
                                    )
                                    buf = io.BytesIO()
                                    filter_img.save(buf, format="PNG")
                                    buf.seek(0)
                                    reply = Reply()
                                    reply.type = ReplyType.IMAGE
                                    reply.content = buf
                                    e_context["reply"] = reply
                                    e_context.action = EventAction.BREAK_PASS
                            else:
                                from zhipuai import ZhipuAI

                                zhipu_api_key = self.zhipu_api_key
                                if is_none_or_empty(zhipu_api_key):
                                    set_reply_text(
                                        "图像理解api key未设置",
                                        e_context,
                                        ReplyType.INFO,
                                    )
                                    return

                                prompt = title
                                prompts = {
                                    "分析": "先全局分析图片的主要内容，并按照逻辑分层次、段落，提炼出5个左右图片中的精华信息、关键要点，生动地向读者描述图片的主要内容。注意排版、换行、emoji、标签的合理搭配，清楚地展现图片讲了什么"
                                }
                                if title in prompts.keys():
                                    prompt = prompts[title]

                                base64_image = image_to_base64(path_image)
                                client = ZhipuAI(api_key=zhipu_api_key)
                                response = client.chat.completions.create(
                                    model=self.zhipu_image_model,
                                    messages=[
                                        {
                                            "role": "user",
                                            "content": [
                                                {"type": "text", "text": prompt},
                                                {
                                                    "type": "image_url",
                                                    "image_url": {
                                                        "url": f"data:image/jpeg;base64,{base64_image}"
                                                    },
                                                },
                                            ],
                                        }
                                    ],
                                )
                                assert len(response.choices) > 0
                                finish_reason = response.choices[0].finish_reason
                                assert finish_reason == "stop"
                                reply_content = response.choices[0].message.content
                                set_reply_text(reply_content, e_context, ReplyType.TEXT)

                        elif ref_type == "47":  # emoji
                            if title in ["下载"]:
                                import html
                                import re

                                ref_emoji_msg = self.msg_cache.get(svrid)
                                raw_msg = ref_emoji_msg.msg
                                content_xml = raw_msg["Data"]["Content"]["string"]
                                match = re.search(
                                    r'cdnurl\s*=\s*"([^"]+)"', content_xml
                                )
                                if match:
                                    cdnurl = match.group(1)
                                    cdnurl = html.unescape(cdnurl)
                                    logger.info(f"[Ref]got emoji, url: {cdnurl}")
                                    reply = Reply()
                                    reply.type = ReplyType.IMAGE_URL
                                    reply.content = cdnurl
                                    e_context["reply"] = reply
                                    e_context.action = EventAction.BREAK_PASS
        elif msg_type == 1:  # TEXT
            context = e_context["context"]
            content = context.content
            if content.startswith("画"):
                from zhipuai import ZhipuAI

                zhipu_api_key = self.zhipu_api_key
                if is_none_or_empty(zhipu_api_key):
                    set_reply_text("图像生成api key未设置", e_context, ReplyType.INFO)
                    return

                client = ZhipuAI(api_key=zhipu_api_key)

                response = client.images.generations(
                    model="cogview-3-flash",
                    prompt=content,
                )

                image_url = response.data[0].url
                reply = Reply()
                reply.type = ReplyType.IMAGE_URL
                reply.content = image_url
                logger.info(f"draw image: ({content}, {image_url})")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS

    def on_handle_context(self, e_context: EventContext):
        if e_context["context"].type not in [
            ContextType.TEXT,
            ContextType.IMAGE,
            ContextType.EMOJI,
        ]:
            return
        msg: ChatMessage = e_context["context"]["msg"]
        content = e_context["context"].content
        logger.debug("[Ref] on_handle_context. content: %s" % content)
        self.handle_ref(e_context)

    def get_help_text(self, **kwargs):
        help_text = """极简的引用交互demo，功能如下：

- 举、举牌: 以引用文字为输入生成小人举牌图片
- BLUR、CONTOUR、DETAIL、EDGE_ENHANCE、EDGE_ENHANCE_MORE、EMBOSS、FIND_EDGES、SHARPEN、SMOOTH、SMOOTH_MORE：以引用图片为输入做简单图片处理
- "分析"或任意文字：以引用图片为输入调用智谱的视觉理解模型
- 下载：下载引用表情，支持图片与gif
- 画：基于智谱cogview-3-flash的文生图"""
        return help_text

    def _load_config_template(self):
        logger.info("[Ref]use config.json.template")
        try:
            plugin_config_path = os.path.join(self.path, "config.json.template")
            if os.path.exists(plugin_config_path):
                with open(plugin_config_path, "r", encoding="utf-8") as f:
                    plugin_conf = json.load(f)
                    return plugin_conf
        except Exception as e:
            logger.exception(e)
