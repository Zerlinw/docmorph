"""doc_converter.writers.html_writer —— HTML 写入器。

生成语义化、自带样式、可离线阅读的完整 HTML 文档：
  * 标题/段落/列表（含嵌套与有序编号）/表格（colspan、rowspan、caption）/
    引用/代码块/水平线完整保留；
  * 图片保存到 ``<输出名>_assets/`` 并相对引用（也可切换 data URI 模式）；
  * 附件落盘并以带图标的链接引用；
  * 流程图渲染为 Mermaid 容器（含 CDN 脚本与无脚本降级列表）。

HTML 同时作为 PDF 写入器的排版输入，因此额外提供 :func:`print_stylesheet`，
由 PDF 引擎注入打印专用样式（A4/页边距/分页控制）。
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from ..assets import AssetStore
from ..errors import WriteError
from ..model import (Attachment, Block, CodeBlock, Document, FlowchartBlock,
                     Heading, HorizontalRule, ImageBlock, ImageInline, Inline,
                     LineBreak, Link, ListBlock, ListItem, Paragraph,
                     QuoteBlock, TableBlock, TableCell, Text)
from ..utils import needs_title_header
from .base import BaseWriter, register_writer

_CSS = """
:root {
  --fg:#1f2328; --muted:#59636e; --border:#d1d9e0; --accent:#0969da;
  --bg:#ffffff; --code-bg:#f6f8fa; --quote-bg:#f8fafc;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  font-family: -apple-system, "Segoe UI", "Noto Sans", "PingFang SC",
               "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif;
  color: var(--fg); background: var(--bg); line-height: 1.7;
  max-width: 880px; margin: 2.5rem auto; padding: 0 1.25rem;
  font-size: 16px; word-wrap: break-word;
}
h1,h2,h3,h4,h5,h6 { line-height:1.3; margin:1.6em 0 .6em; font-weight:650;
                    scroll-margin-top: 1rem; }
h1 { font-size: 2em; border-bottom: 1px solid var(--border); padding-bottom:.3em; }
h2 { font-size: 1.5em; border-bottom: 1px solid var(--border); padding-bottom:.25em; }
h3 { font-size: 1.25em; }
h4 { font-size: 1.05em; }
h5,h6 { font-size: 1em; color: var(--muted); }
p { margin: .8em 0; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
img { max-width: 100%; height: auto; border-radius: 4px; }
figure { margin: 1.2em 0; text-align: center; }
figcaption { color: var(--muted); font-size: .9em; margin-top: .4em; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; }
th, td { border: 1px solid var(--border); padding: .45em .7em; text-align:left;
         vertical-align: top; }
th { background: var(--code-bg); font-weight:600; }
tr:nth-child(2n) td { background: #fafbfc; }
caption { caption-side: top; text-align: left; color: var(--muted);
          font-size: .9em; padding-bottom: .4em; }
pre { background: var(--code-bg); padding: 1em; border-radius: 6px;
      overflow-x: auto; line-height: 1.5; }
code { font-family: ui-monospace, SFMono-Regular, "Cascadia Code", Consolas,
                   "Liberation Mono", monospace;
       background: var(--code-bg); padding:.15em .35em; border-radius:4px;
       font-size:.92em; }
pre code { background: none; padding: 0; font-size: .9em; }
blockquote { border-left: 4px solid var(--border); margin:1em 0;
             padding: .2em 1em; color: var(--muted); background: var(--quote-bg); }
blockquote > :first-child { margin-top: .4em; }
blockquote > :last-child { margin-bottom: .4em; }
hr { border: none; border-top: 1px solid var(--border); margin: 2em 0; }
ul, ol { padding-left: 1.6em; margin: .6em 0; }
li + li { margin-top: .2em; }
.attachment { display:inline-flex; align-items:center; gap:.4em;
              border:1px solid var(--border); border-radius:6px;
              padding:.3em .7em; margin:.3em 0; background:var(--code-bg); }
.flowchart { border:1px dashed var(--border); border-radius:8px;
             padding:1em; margin:1em 0; overflow-x: auto; }
.flowchart .mermaid { text-align:center; }
.flowchart details { margin-top:.6em; color:var(--muted); font-size:.9em; }
.flowchart summary { cursor: pointer; }
.doc-title { font-size: 2em; font-weight: 650; border-bottom: 1px solid var(--border);
             padding-bottom:.3em; margin: 0 0 1em; }
@media (max-width: 600px) {
  body { margin: 1.25rem auto; font-size: 15px; }
  table { font-size: .9em; }
}
"""

_MERMAID_SCRIPT = """
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script>
window.addEventListener('load', function () {
  if (window.mermaid) {
    mermaid.initialize({ startOnLoad: true, securityLevel: 'loose' });
  }
});
</script>
"""


def print_stylesheet(page_size: str = "A4") -> str:
    """PDF 排版专用样式（由 PDF 引擎注入，不影响普通 HTML 阅读）。"""
    size = page_size if page_size in ("A4", "Letter", "A3", "A5") else "A4"
    return f"""
@page {{ size: {size}; margin: 18mm 16mm 20mm 16mm; }}
html, body {{ background: #fff; }}
body {{ max-width: none; margin: 0; padding: 0; font-size: 10.5pt;
        line-height: 1.65; -webkit-print-color-adjust: exact;
        print-color-adjust: exact; }}
h1, h2, h3, h4, h5, h6 {{ break-after: avoid-page; page-break-after: avoid; }}
p, li {{ orphans: 3; widows: 3; }}
table, figure, pre, blockquote {{ break-inside: avoid; page-break-inside: avoid; }}
img {{ max-width: 100% !important; }}
a {{ color: #0969da; }}
pre {{ white-space: pre-wrap; word-break: break-word; }}
"""


@register_writer
class HtmlWriter(BaseWriter):
    extension = "html"
    #: PDF 等无法执行脚本的场景置为 False，流程图降级为文本结构
    render_mermaid = True
    #: 是否在页面顶部插入"由 doc-converter 生成"的说明条（默认关闭，避免污染内容）
    render_meta_banner = False

    def __init__(self, embed_images: bool = False) -> None:
        self.embed_images = embed_images

    def render_to_string(self, doc: Document, assets: AssetStore) -> str:
        """渲染为完整 HTML 字符串（不落盘，供 PDF 写入器复用）。"""
        lang = (doc.metadata.get("lang") or "zh-CN").replace('"', "")
        parts: list[str] = [
            "<!DOCTYPE html>",
            f'<html lang="{escape(lang)}">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{escape(doc.title or 'document')}</title>",
            '<meta name="generator" content="doc-converter">',
        ]
        for key, name in (("author", "author"), ("description", "description"),
                          ("keywords", "keywords"), ("subject", "subject")):
            value = doc.metadata.get(key)
            if value:
                parts.append(f'<meta name="{name}" content="{escape(value, quote=True)}">')
        parts.append(f"<style>{_CSS}</style>")
        parts.append("</head>")
        parts.append("<body>")
        if self.render_meta_banner:
            parts.append('<div class="doc-meta">由 doc-converter 自动转换生成'
                         f"（源格式：{escape(doc.source_format or '未知')}）</div>")
        if needs_title_header(doc):
            parts.append(f'<h1 class="doc-title">{escape(doc.title or "")}</h1>')
        for block in doc.blocks:
            rendered = self._render_block(block, assets)
            if rendered:
                parts.append(rendered)
        parts.append("</body>")
        parts.append("</html>")
        return "\n".join(parts) + "\n"

    def write(self, doc: Document, out_path: Path, assets: AssetStore) -> Path:
        text = self.render_to_string(doc, assets)
        try:
            out_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise WriteError(str(out_path), str(exc))
        return out_path

    # ------------------------------------------------------------------ 块级
    def _render_block(self, block: Block, assets: AssetStore) -> str:
        if isinstance(block, Heading):
            level = max(1, min(6, block.level))
            return f"<h{level}>{self._inlines(block.children, assets)}</h{level}>"
        if isinstance(block, Paragraph):
            content = self._inlines(block.children, assets)
            if not content.strip():
                return ""
            style = f' style="text-align:{block.align}"' if block.align else ""
            return f"<p{style}>{content}</p>"
        if isinstance(block, ListBlock):
            return self._render_list(block, assets)
        if isinstance(block, TableBlock):
            return self._render_table(block, assets)
        if isinstance(block, ImageBlock):
            return self._render_image_block(block, assets)
        if isinstance(block, CodeBlock):
            lang = f' class="language-{escape(block.lang)}"' if block.lang else ""
            return f"<pre><code{lang}>{escape(block.text)}</code></pre>"
        if isinstance(block, QuoteBlock):
            if block.blocks:
                inner = "".join(self._render_block(child, assets)
                                for child in block.blocks)
            else:
                inner = f"<p>{self._inlines(block.children, assets)}</p>"
            return f"<blockquote>{inner}</blockquote>"
        if isinstance(block, FlowchartBlock):
            return self._render_flowchart(block)
        if isinstance(block, Attachment):
            ref = self._attachment_ref(block, assets)
            return (f'<p class="attachment">📎 <a href="{escape(ref, quote=True)}">'
                    f"{escape(block.name)}</a></p>")
        if isinstance(block, HorizontalRule):
            return "<hr>"
        return ""

    def _render_list(self, block: ListBlock, assets: AssetStore) -> str:
        tree = _build_list_tree(block.items)
        return self._render_list_nodes(tree, block.ordered, assets,
                                       start=block.start if block.ordered else 1)

    def _render_list_nodes(self, nodes: list[dict], ordered: bool,
                           assets: AssetStore, start: int = 1) -> str:
        tag = "ol" if ordered else "ul"
        start_attr = f' start="{start}"' if ordered and start != 1 else ""
        items: list[str] = []
        for node in nodes:
            item: ListItem = node["item"]
            content = self._inlines(item.children, assets)
            inner = ""
            if node["children"]:
                inner += self._render_list_nodes(node["children"], ordered, assets)
            for child in node["blocks"]:
                inner += self._render_block(child, assets)
            items.append(f"<li>{content}{inner}</li>")
        return f"<{tag}{start_attr}>{''.join(items)}</{tag}>"

    def _render_table(self, block: TableBlock, assets: AssetStore) -> str:
        if not block.rows:
            return ""
        out = ["<table>"]
        if block.caption:
            out.append(f"<caption>{escape(block.caption)}</caption>")
        body_rows = block.rows
        if block.header:
            header = block.rows[0]
            out.append("<thead><tr>")
            out.append("".join(self._render_cell(cell, "th", assets) for cell in header))
            out.append("</tr></thead>")
            body_rows = block.rows[1:]
        out.append("<tbody>")
        for row in body_rows:
            out.append("<tr>")
            out.append("".join(self._render_cell(cell, "td", assets) for cell in row))
            out.append("</tr>")
        out.append("</tbody></table>")
        return "".join(out)

    def _render_cell(self, cell: TableCell, tag: str, assets: AssetStore) -> str:
        attrs = ""
        if cell.colspan > 1:
            attrs += f' colspan="{cell.colspan}"'
        if cell.rowspan > 1:
            attrs += f' rowspan="{cell.rowspan}"'
        return f"<{tag}{attrs}>{self._inlines(cell.children, assets)}</{tag}>"

    def _render_image_block(self, image: ImageBlock, assets: AssetStore) -> str:
        ref = self._image_ref(image, assets)
        attrs = f' src="{escape(ref, quote=True)}" alt="{escape(image.alt or "")}"'
        if image.width_px:
            attrs += f' width="{image.width_px}"'
        if image.height_px:
            attrs += f' height="{image.height_px}"'
        cap = (f"<figcaption>{escape(image.caption)}</figcaption>"
               if image.caption else "")
        return f"<figure><img{attrs}>{cap}</figure>"

    def _render_flowchart(self, block: FlowchartBlock) -> str:
        edges = block.edges or [(block.nodes[i], block.nodes[i + 1])
                                for i in range(len(block.nodes) - 1)]
        if not self.render_mermaid:
            # 无脚本环境（如 PDF）：降级为"节点 + 连线"文本结构
            nodes = "".join(f"<li>{escape(n)}</li>" for n in block.nodes)
            links = "".join(f"<li>{escape(a)} → {escape(b)}</li>" for a, b in edges)
            return (f'<div class="flowchart"><strong>流程图</strong>'
                    f"<ol>{nodes}</ol>"
                    f'<div><strong>连线关系：</strong></div><ul>{links}</ul>'
                    f"</div>")
        if block.source and block.source.strip():
            mermaid = block.source.strip()
        else:
            index = {node: i for i, node in enumerate(block.nodes)}
            lines = ["graph TD"]
            for i, node in enumerate(block.nodes):
                lines.append(f'    N{i}["{node}"]')
            for a, b in edges:
                if a in index and b in index:
                    lines.append(f"    N{index[a]} --> N{index[b]}")
            mermaid = "\n".join(lines)
        fallback = "".join(f"<li>{escape(n)}</li>" for n in block.nodes)
        script = ""
        if not getattr(self, "_mermaid_script_emitted", False):
            self._mermaid_script_emitted = True
            script = _MERMAID_SCRIPT
        return (f'<div class="flowchart">'
                f'<div class="mermaid">{escape(mermaid)}</div>'
                f"<noscript><ol>{fallback}</ol></noscript>"
                f"<details><summary>查看流程节点文本</summary><ol>{fallback}</ol></details>"
                f"</div>{script}")

    # ------------------------------------------------------------------ 行内
    def _inlines(self, inlines: list[Inline], assets: AssetStore) -> str:
        parts: list[str] = []
        for inline in inlines:
            if isinstance(inline, Text):
                parts.append(self._text(inline))
            elif isinstance(inline, Link):
                label = self._inlines(inline.children, assets)
                title = f' title="{escape(inline.title, quote=True)}"' if inline.title else ""
                parts.append(f'<a href="{escape(inline.target, quote=True)}"'
                             f'{title}>{label}</a>')
            elif isinstance(inline, ImageInline):
                ref = self._image_ref(inline, assets)
                attrs = f' src="{escape(ref, quote=True)}" alt="{escape(inline.alt or "")}"'
                if inline.width_px:
                    attrs += f' width="{inline.width_px}"'
                if inline.height_px:
                    attrs += f' height="{inline.height_px}"'
                parts.append(f"<img{attrs}>")
            elif isinstance(inline, LineBreak):
                parts.append("<br>")
        return "".join(parts)

    @staticmethod
    def _text(text: Text) -> str:
        content = escape(text.text)
        if text.code:
            content = f"<code>{content}</code>"
        if text.bold:
            content = f"<strong>{content}</strong>"
        if text.italic:
            content = f"<em>{content}</em>"
        if text.underline:
            content = f"<u>{content}</u>"
        if text.strike:
            content = f"<s>{content}</s>"
        if text.superscript:
            content = f"<sup>{content}</sup>"
        if text.subscript:
            content = f"<sub>{content}</sub>"
        if text.color:
            content = f'<span style="color:{escape(text.color, quote=True)}">{content}</span>'
        return content

    # ------------------------------------------------------------------ 资源
    def _image_ref(self, image, assets: AssetStore) -> str:
        asset = assets.resolve_image(image.src, image.data, image.mime,
                                     hint=image.alt or "image")
        if self.embed_images and asset.data:
            from ..utils import make_data_uri
            return make_data_uri(asset.data, asset.mime)
        return asset.ref or image.src

    @staticmethod
    def _attachment_ref(att: Attachment, assets: AssetStore) -> str:
        if att.data is None:
            return att.name
        return assets.add_attachment(att.name, att.data, att.mime).ref


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _build_list_tree(items: list[ListItem]) -> list[dict]:
    """把扁平（带 level）的列表项还原为嵌套树；item.blocks 单独保留。"""
    root: list[dict] = []
    stack: list[tuple[int, list[dict]]] = [(-1, root)]
    for item in items:
        node = {"item": item, "children": [], "blocks": list(item.blocks)}
        while stack and stack[-1][0] >= item.level:
            stack.pop()
        parent = stack[-1][1] if stack else root
        parent.append(node)
        stack.append((item.level, node["children"]))
    return root
