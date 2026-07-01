"""
微信公众号排版模板系统

功能:
1. 模板存储（名称/来源/HTML/字号/行距/段距/字距/图片样式/启用状态）
2. 占位符替换（{{TITLE}}/{{SUMMARY}}/{{INTRO}}/{{BODY_PARAGRAPHS}}/{{IMAGE_1}}等）
3. 默认排版标准（inline style, 兼容微信公众号编辑器）
4. 模板优先级逻辑
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.logger import logger


class TemplateManager:
    """微信公众号模板管理器"""

    # 默认占位符
    PLACEHOLDERS = [
        "{{TITLE}}", "{{SUMMARY}}", "{{INTRO}}", "{{BODY_PARAGRAPHS}}",
        "{{IMAGE_1}}", "{{IMAGE_2}}", "{{IMAGE_3}}", "{{IMAGE_4}}",
        "{{CAPTION_1}}", "{{CAPTION_2}}", "{{CAPTION_3}}", "{{CAPTION_4}}",
        "{{ENDING}}",
    ]

    def __init__(self, config: Dict):
        self.config = config
        self.template_cfg = config.get("template_system", {})
        project_root = config.get("project_root", ".")
        self.storage_path = Path(project_root) / self.template_cfg.get(
            "storage_path", "config/templates/templates.json"
        )
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.default_style = config.get("formatter_agent", {}).get("default_style", {})

        # 加载模板
        self.templates: Dict[str, Dict] = self._load_templates()

    def _load_templates(self) -> Dict[str, Dict]:
        """从存储文件加载模板"""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"[模板] 加载模板文件失败: {e}")

        # 初始化空模板文件
        self._save_templates({})
        return {}

    def _save_templates(self, templates: Dict):
        """保存模板到存储文件"""
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(templates, f, ensure_ascii=False, indent=2)

    def add_template(self, template: Dict) -> bool:
        """
        添加模板

        Args:
            template: {
                "name": str,
                "source": "公众号文章模板" / "已发布文章参考",
                "html": str,
                "font_size": str,
                "line_height": str,
                "paragraph_spacing": str,
                "letter_spacing": str,
                "image_style": str,
                "enabled": bool,
            }
        """
        name = template.get("name", "").strip()
        if not name:
            logger.error("[模板] 模板名称不能为空")
            return False

        self.templates[name] = {
            "name": name,
            "source": template.get("source", ""),
            "html": template.get("html", ""),
            "font_size": template.get("font_size", "16px"),
            "line_height": template.get("line_height", "1.9"),
            "paragraph_spacing": template.get("paragraph_spacing", "18px"),
            "letter_spacing": template.get("letter_spacing", "0.5px"),
            "image_style": template.get("image_style", "default"),
            "enabled": template.get("enabled", True),
            "created_at": template.get("created_at", ""),
        }

        self._save_templates(self.templates)
        logger.info(f"[模板] 模板已保存: {name}")
        return True

    def get_template(self, name: str) -> Optional[Dict]:
        """获取指定模板"""
        return self.templates.get(name)

    def get_enabled_template(self) -> Optional[Dict]:
        """获取默认启用的模板"""
        for name, tmpl in self.templates.items():
            if tmpl.get("enabled", False):
                return tmpl
        return None

    def list_templates(self) -> List[Dict]:
        """列出所有模板"""
        return list(self.templates.values())

    def delete_template(self, name: str) -> bool:
        """删除模板"""
        if name in self.templates:
            del self.templates[name]
            self._save_templates(self.templates)
            logger.info(f"[模板] 模板已删除: {name}")
            return True
        return False

    def resolve_template(self, article_template_name: Optional[str] = None) -> Optional[Dict]:
        """
        按优先级解析模板:
        1. 文章指定模板
        2. 默认启用模板
        3. 系统默认排版标准 (返回 None, 使用 default_style)
        """
        # 优先级1: 文章指定模板
        if article_template_name:
            tmpl = self.get_template(article_template_name)
            if tmpl:
                logger.info(f"[模板] 使用文章指定模板: {article_template_name}")
                return tmpl

        # 优先级2: 默认启用模板
        enabled = self.get_enabled_template()
        if enabled:
            logger.info(f"[模板] 使用默认启用模板: {enabled['name']}")
            return enabled

        # 优先级3: 系统默认排版
        logger.info("[模板] 无指定/启用模板，使用系统默认排版标准")
        return None

    def render(
        self,
        template: Optional[Dict],
        title: str,
        summary: str,
        intro: str,
        body_paragraphs: List[str],
        images: List[Dict],
        ending: str,
    ) -> str:
        """
        使用模板渲染最终 HTML

        Args:
            template: 模板字典 (None=使用默认排版)
            title: 文章标题
            summary: 摘要
            intro: 导语段
            body_paragraphs: 正文段落列表
            images: 图片列表 [{url, caption, position}]
            ending: 结尾段

        Returns:
            完整的公众号兼容 HTML (inline style)
        """
        if template and template.get("html"):
            # 检查是否是Banner模板（含Banner图 + 内容区域占位符）
            if template.get("has_banner") or "{{BODY_PARAGRAPHS}}" in template.get("html", ""):
                return self._render_with_banner(
                    template, title, summary, intro, body_paragraphs, images, ending
                )
            return self._render_with_template(
                template, title, summary, intro, body_paragraphs, images, ending
            )
        else:
            return self._render_default(
                title, summary, intro, body_paragraphs, images, ending
            )

    def _render_with_banner(
        self,
        template: Dict,
        title: str,
        summary: str,
        intro: str,
        body_paragraphs: List[str],
        images: List[Dict],
        ending: str,
    ) -> str:
        """使用Banner模板渲染：顶部Banner + 正文(含图片) + 底部图片

        模板HTML结构:
          <section>顶部Banner图片</section>
          <section>{{INTRO}}{{BODY_PARAGRAPHS}}{{ENDING}}</section>
          <section>底部模板图片</section>

        渲染后:
          <section>顶部Banner图片</section>
          <section>导语 + 正文段落(含图片插入) + 结尾</section>
          <section>底部模板图片</section>
        """
        template_html = template.get("html", "")

        # 渲染各部分内容
        style = self.default_style
        body_cfg = style.get("body", {})
        img_cfg = style.get("image", {})
        cap_cfg = style.get("caption", {})
        intro_cfg = style.get("intro", {})

        # 导语
        intro_html = ""
        if intro:
            intro_html = (
                f'<p style="font-size:{intro_cfg.get("font_size","16px")};'
                f'line-height:{intro_cfg.get("line_height","1.9")};'
                f'color:{intro_cfg.get("color","#333333")};'
                f'margin:{intro_cfg.get("margin","0 0 20px")};'
                f'font-weight:{intro_cfg.get("font_weight","normal")};'
                f'text-align:justify;">{self._escape(intro)}</p>'
            )

        # 正文段落 + 图片插入
        body_parts = []
        img_idx = 0
        total_paragraphs = len(body_paragraphs)

        for i, para in enumerate(body_paragraphs):
            body_parts.append(
                f'<p style="font-size:{body_cfg.get("font_size","16px")};'
                f'line-height:{body_cfg.get("line_height","1.9")};'
                f'letter-spacing:{body_cfg.get("letter_spacing","0.5px")};'
                f'color:{body_cfg.get("color","#333333")};'
                f'margin:{body_cfg.get("margin","0 0 18px")};'
                f'text-align:{body_cfg.get("text_align","justify")};">'
                f'{self._escape(para)}</p>'
            )

            # 按图片插入规则插入图片
            insert_pos = self._get_image_insert_position(
                img_idx, len(images), i, total_paragraphs
            )
            if insert_pos and img_idx < len(images):
                img = images[img_idx]
                body_parts.append(self._render_image_default(img, img_cfg))
                if img.get("caption") or img.get("description"):
                    body_parts.append(self._render_caption_default(img, cap_cfg))
                img_idx += 1

        # 插入剩余未消耗的图片
        while img_idx < len(images):
            img = images[img_idx]
            body_parts.append(self._render_image_default(img, img_cfg))
            if img.get("caption") or img.get("description"):
                body_parts.append(self._render_caption_default(img, cap_cfg))
            img_idx += 1

        # 结尾段
        ending_html = ""
        if ending:
            ending_html = (
                f'<p style="font-size:{body_cfg.get("font_size","16px")};'
                f'line-height:{body_cfg.get("line_height","1.9")};'
                f'letter-spacing:{body_cfg.get("letter_spacing","0.5px")};'
                f'color:{body_cfg.get("color","#333333")};'
                f'margin:{body_cfg.get("margin","0 0 18px")};'
                f'text-align:justify;">{self._escape(ending)}</p>'
            )

        body_html = "\n".join(body_parts)

        # 替换模板中的占位符（保留顶部Banner和底部图片不变）
        result = template_html.replace("{{INTRO}}", intro_html)
        result = result.replace("{{BODY_PARAGRAPHS}}", body_html)
        result = result.replace("{{ENDING}}", ending_html)

        return result

    def _render_with_template(
        self,
        template: Dict,
        title: str,
        summary: str,
        intro: str,
        body_paragraphs: List[str],
        images: List[Dict],
        ending: str,
    ) -> str:
        """使用自定义模板渲染"""
        html = template["html"]

        # 替换占位符
        html = html.replace("{{TITLE}}", self._escape(title))
        html = html.replace("{{SUMMARY}}", self._escape(summary))
        html = html.replace("{{INTRO}}", self._render_intro(intro, template))
        html = html.replace("{{BODY_PARAGRAPHS}}", self._render_body(body_paragraphs, template))
        html = html.replace("{{ENDING}}", self._render_ending(ending, template))

        # 替换图片和说明
        for i in range(1, 5):
            img_placeholder = f"{{{{IMAGE_{i}}}}}"
            cap_placeholder = f"{{{{CAPTION_{i}}}}}"

            if i <= len(images):
                img = images[i - 1]
                html = html.replace(img_placeholder, self._render_image(img, template))
                html = html.replace(cap_placeholder, self._render_caption(img, template))
            else:
                html = html.replace(img_placeholder, "")
                html = html.replace(cap_placeholder, "")

        return html

    def _render_default(
        self,
        title: str,
        summary: str,
        intro: str,
        body_paragraphs: List[str],
        images: List[Dict],
        ending: str,
    ) -> str:
        """使用系统默认排版标准渲染"""
        style = self.default_style
        body_cfg = style.get("body", {})
        img_cfg = style.get("image", {})
        cap_cfg = style.get("caption", {})
        intro_cfg = style.get("intro", {})

        parts = []

        # 导语段
        if intro:
            parts.append(
                f'<p style="font-size:{intro_cfg.get("font_size","16px")};'
                f'line-height:{intro_cfg.get("line_height","1.9")};'
                f'color:{intro_cfg.get("color","#333333")};'
                f'margin:{intro_cfg.get("margin","0 0 20px")};'
                f'font-weight:{intro_cfg.get("font_weight","normal")};'
                f'text-align:justify;">{self._escape(intro)}</p>'
            )

        # 正文段落 + 图片插入
        img_idx = 0
        total_paragraphs = len(body_paragraphs)

        for i, para in enumerate(body_paragraphs):
            parts.append(
                f'<p style="font-size:{body_cfg.get("font_size","16px")};'
                f'line-height:{body_cfg.get("line_height","1.9")};'
                f'letter-spacing:{body_cfg.get("letter_spacing","0.5px")};'
                f'color:{body_cfg.get("color","#333333")};'
                f'margin:{body_cfg.get("margin","0 0 18px")};'
                f'text-align:{body_cfg.get("text_align","justify")};">'
                f'{self._escape(para)}</p>'
            )

            # 按图片插入规则插入图片
            insert_pos = self._get_image_insert_position(
                img_idx, len(images), i, total_paragraphs
            )
            if insert_pos and img_idx < len(images):
                img = images[img_idx]
                parts.append(self._render_image_default(img, img_cfg))
                if img.get("caption"):
                    parts.append(self._render_caption_default(img, cap_cfg))
                img_idx += 1

        # 插入剩余未消耗的图片
        while img_idx < len(images):
            img = images[img_idx]
            parts.append(self._render_image_default(img, img_cfg))
            if img.get("caption"):
                parts.append(self._render_caption_default(img, cap_cfg))
            img_idx += 1

        # 结尾段
        if ending:
            parts.append(
                f'<p style="font-size:{body_cfg.get("font_size","16px")};'
                f'line-height:{body_cfg.get("line_height","1.9")};'
                f'letter-spacing:{body_cfg.get("letter_spacing","0.5px")};'
                f'color:{body_cfg.get("color","#333333")};'
                f'margin:{body_cfg.get("margin","0 0 18px")};'
                f'text-align:justify;">{self._escape(ending)}</p>'
            )

        # 包装在 section 中
        return (
            f'<section style="margin:0;padding:0;">\n'
            + "\n".join(parts)
            + "\n</section>"
        )

    def _get_image_insert_position(
        self, img_idx: int, total_images: int,
        current_para: int, total_paras: int
    ) -> bool:
        """根据图片数量和段落位置决定是否在此段落之后插入图片"""
        if total_images == 0:
            return False

        rules = self.config.get("formatter_agent.image_insertion.rules", {})

        if total_images == 1:
            rule = rules.get("1_image", ["after_paragraph_1"])
            return current_para == 0  # 第1段后

        elif total_images == 2:
            if img_idx == 0:
                return current_para == 0  # 第1段后
            elif img_idx == 1:
                return current_para >= total_paras // 2  # 中间段后

        elif total_images == 3:
            if img_idx == 0:
                return current_para == 0  # 第1段后
            elif img_idx == 1:
                return current_para >= total_paras // 2  # 中间
            elif img_idx == 2:
                return current_para >= total_paras - 2  # 结尾段前

        elif total_images >= 4:
            if img_idx == 0:
                return current_para == 0
            elif img_idx == 1:
                return current_para >= total_paras // 3
            elif img_idx == 2:
                return current_para >= total_paras * 2 // 3
            elif img_idx == 3:
                return current_para >= total_paras - 2

        return False

    def _render_image(self, img: Dict, template: Dict) -> str:
        """使用模板渲染图片"""
        url = img.get("url", img.get("path", ""))
        return f'<img src="{url}" style="width:100%;max-width:100%;height:auto;display:block;margin:18px auto;border-radius:6px;object-fit:contain;" />'

    def _render_caption(self, img: Dict, template: Dict) -> str:
        """默认不展示自动图片说明、文件名或技术元数据。"""
        return ""

    def _render_image_default(self, img: Dict, img_cfg: Dict) -> str:
        """默认排版渲染图片"""
        url = img.get("url", img.get("path", ""))
        return (
            f'<img src="{url}" '
            f'style="width:{img_cfg.get("width","100%")};'
            f'max-width:{img_cfg.get("max_width","100%")};'
            f'height:auto;'
            f'display:{img_cfg.get("display","block")};'
            f'margin:{img_cfg.get("margin","18px auto")};'
            f'border-radius:{img_cfg.get("border_radius","6px")};'
            f'object-fit:contain;" />'
        )

    def _render_caption_default(self, img: Dict, cap_cfg: Dict) -> str:
        """默认不展示自动图片说明、文件名或技术元数据。"""
        return ""

    def _render_intro(self, intro: str, template: Dict) -> str:
        """渲染导语段"""
        if not intro:
            return ""
        fs = template.get("font_size", "16px")
        lh = template.get("line_height", "1.9")
        return (
            f'<p style="font-size:{fs};line-height:{lh};color:#333333;'
            f'margin:0 0 20px;font-weight:normal;text-align:justify;">'
            f'{self._escape(intro)}</p>'
        )

    def _render_body(self, paragraphs: List[str], template: Dict) -> str:
        """渲染正文段落"""
        if not paragraphs:
            return ""

        fs = template.get("font_size", "16px")
        lh = template.get("line_height", "1.9")
        ls = template.get("letter_spacing", "0.5px")
        ps = template.get("paragraph_spacing", "18px")

        parts = []
        for para in paragraphs:
            parts.append(
                f'<p style="font-size:{fs};line-height:{lh};'
                f'letter-spacing:{ls};color:#333333;'
                f'margin:0 0 {ps};text-align:justify;">'
                f'{self._escape(para)}</p>'
            )

        return "\n".join(parts)

    def _render_ending(self, ending: str, template: Dict) -> str:
        """渲染结尾段"""
        if not ending:
            return ""
        fs = template.get("font_size", "16px")
        lh = template.get("line_height", "1.9")
        return (
            f'<p style="font-size:{fs};line-height:{lh};color:#333333;'
            f'margin:0 0 18px;text-align:justify;">'
            f'{self._escape(ending)}</p>'
        )

    def _escape(self, text: str) -> str:
        """HTML 转义"""
        if not text:
            return ""
        text = str(text)
        text = text.replace("&", "&amp;")
        text = text.replace("<", "&lt;")
        text = text.replace(">", "&gt;")
        text = text.replace('"', "&quot;")
        return text

    def split_paragraphs(
        self, body_text: str, min_chars: int = 40,
        max_chars: int = 90, split_threshold: int = 120
    ) -> List[str]:
        """
        按规则拆分段落:
        1. 先按空行拆分
        2. 每段建议40-90字
        3. 超过120字自动拆成两段
        """
        # 按空行拆分
        raw_paragraphs = re.split(r'\n\s*\n', body_text.strip())

        paragraphs = []
        for para in raw_paragraphs:
            para = para.strip()
            if not para:
                continue

            # 移除 Markdown 标题符号
            para = re.sub(r'^#+\s*', '', para)
            # 移除列表符号
            para = re.sub(r'^[\-\*\d]+\.\s*', '', para)
            # 移除 hashtag
            para = re.sub(r'#\S+', '', para)
            # 移除多余 emoji
            para = re.sub(r'[\U0001F000-\U0001FFFF]', '', para)

            para = para.strip()
            if not para:
                continue

            # 检查是否需要拆分
            char_count = len(para.replace(" ", "").replace("\n", ""))

            if char_count > split_threshold:
                # 在句号/问号/叹号处拆分
                sentences = re.split(r'(?<=[。！？.?!])', para)
                current_chunk = ""

                for sent in sentences:
                    sent = sent.strip()
                    if not sent:
                        continue

                    if len(current_chunk.replace(" ", "")) + len(sent.replace(" ", "")) <= max_chars:
                        current_chunk += sent
                    else:
                        if current_chunk:
                            paragraphs.append(current_chunk.strip())
                        current_chunk = sent

                if current_chunk:
                    paragraphs.append(current_chunk.strip())
            else:
                paragraphs.append(para)

        return paragraphs
