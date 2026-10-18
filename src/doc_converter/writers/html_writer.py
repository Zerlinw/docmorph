"""doc_converter.writers.html_writer —— HTML 写入器。

生成语义化、自带样式的完整 HTML 文档：
  * 标题/段落/列表/表格/引用/代码块/水平线完整保留；
  * 图片保存到 ``<输出名>_assets/`` 并相对引用（也可切换 data URI 模式）；
  * 附件落盘并以带图标的链接引用；
  * 流程图渲染为 Mermaid 容器（含 CDN 脚本与降级预填列表）。
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
:root { --fg:#24292f; --muted:#57606a; --border:#d0d7de; --accent:#0969da;
        --bg:#ffffff; --code-bg:#f6f8fa; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", "Noto Sans", Helvetica, Arial,
       "PingFang SC", "Microsoft YaHei", sans-serif;
       color: var(--fg); background: var(--bg); line-height: 1.65;
       max-width: 860px; margin: 2rem auto; padding: 0 1rem; }
h1,h2,h3,h4,h5,h6 { line-height:1.3; margin:1.6em 0 .6em; font-weight:600; }
h1 { font-size: 2em; border-bottom: 1px solid var(--border); padding-bottom:.3em; }
h2 { font-size: 1.5em; border-bottom: 1px solid var(--border); padding-bottom:.25em; }
p { margin: .8em 0; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
img { max-width: 100%; height: auto; border-radius: 4px; }
figure { margin: 1.2em 0; text-align: center; }
figcaption { color: var(--muted); font-size: .9em; margin-top: .4em; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; display:block;
        overflow-x:auto; }
th, td { border: 1px solid var(--border); padding: .45em .7em; text-align:left; }
th { background: var(--code-bg); font-weight:600; }
tr:nth-child(2n) td { background: #fafbfc; }
pre { background: var(--code-bg); padding: 1em; border-radius: 6px;
      overflow-x: auto; }
code { font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
       background: var(--code-bg); padding:.15em .35em; border-radius:4px;
       font-size:.92em; }
pre code { background: none; padding: 0; }
blockquote { border-left: 4px solid var(--border); margin:1em 0;
             padding: .2em 1em; color: var(--muted); }
hr { border: none; border-top: 1px solid var(--border); margin: 2em 0; }
.attachment { display:inline-flex; align-items:center; gap:.4em;
              border:1px solid var(--border); border-radius:6px;
              padding:.3em .7em; margin:.3em 0; background:var(--code-bg); }
.flowchart { border:1px dashed var(--border); border-radius:8px;
             padding:1em; margin:1em 0; }
.flowchart .mermaid { text-align:center; }
.flowchart ol { margin:.5em 0 0; color:var(--muted); }
.doc-meta { color: var(--muted); font-size: .85em; border-bottom:1px solid
            var(--border); padding-bottom:1em; margin-bottom:1.5em; }
"""

_MERMAID_SCRIPT = """
<script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
<script>
window.addEventListener('load', function () {
  if (window.mermaid) {
    mermaid.initialize({ startOnLoad: true, securityLevel: 'loose' });
  }
});
</script>
"""


@register_writer
class HtmlWriter(BaseWriter):
    extension = "html"
    #: PDF 等无法执行脚本的场景置为 False，流程图降级为文本结构
    render_mermaid = True

    def __init__(self, embed_images: bool = False) -> None:
        self.embed_images = embed_images

    def render_to_string(self, doc: Document, assets: AssetStore) -> str:
        """渲染为完整 HTML 字符串（不落盘，供 PDF 写入器复用）。"""
        parts: list[str] = []
        parts.append("<!DOCTYPE html>")
        parts.append('<html lang="zh-CN">')
        parts.append("<head>")
        parts.append('<meta charset="utf-8">')
        parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
        parts.append(f"<title>{escape(doc.title or 'document')}</title>")
        parts.append(f"<style>{_CSS}</style>")
        parts.append("</head>")
        parts.append("<body>")
        if needs_title_header(doc):
            parts.append(f"<h1>{escape(doc.title)}</h1>")
        parts.append('<div class="doc-meta">本文档由 doc-converter 自动转换生成'
                     f"（源格式：{escape(doc.source_format or '未知')}）</div>")
        for block in doc.blocks:
            parts.append(self._render_block(block, assets))
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
            return f"<p>{self._inlines(block.children, assets)}</p>"
        if isinstance(block, ListBlock):
            return self._render_list(block, assets)
        if isinstance(block, TableBlock):
            return self._render_table(block, assets)
        if isinstance(block, ImageBlock):
            ref = self._image_ref(block, assets)
            cap = (f"<figcaption>{escape(block.caption)}</figcaption>"
                   if block.caption else "")
            return (f'<figure><img src="{escape(ref, quote=True)}" '
                    f'alt="{escape(block.alt or "")}">{cap}</figure>')
        if isinstance(block, CodeBlock):
            lang = f' class="language-{escape(block.lang)}"' if block.lang else ""
            return f"<pre><code{lang}>{escape(block.text)}</code></pre>"
        if isinstance(block, QuoteBlock):
            return f"<blockquote>{self._inlines(block.children, assets)}</blockquote>"
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
        tag = "ol" if block.ordered else "ul"
        start_attr = f' start="{block.start}"' if block.ordered and block.start != 1 else ""
        items: list[str] = []
        for item in block.items:
            content = self._inlines(item.children, assets)
            items.append(f"<li>{content}</li>")
        return f"<{tag}{start_attr}>{''.join(items)}</{tag}>"

    def _render_table(self, block: TableBlock, assets: AssetStore) -> str:
        if not block.rows:
            return ""
        width = max(len(r) for r in block.rows)
        rows = [r + [TableCell()] * (width - len(r)) for r in block.rows]
        out = ["<table>"]
        if block.header:
            cells = "".join(f"<th>{self._inlines(c.children, assets)}</th>"
                            for c in rows[0])
            out.append(f"<thead><tr>{cells}</tr></thead>")
            body = rows[1:]
        else:
            body = rows
        out.append("<tbody>")
        for row in body:
            cells = "".join(f"<td>{self._inlines(c.children, assets)}</td>"
                            for c in row)
            out.append(f"<tr>{cells}</tr>")
        out.append("</tbody></table>")
        return "".join(out)

    def _render_flowchart(self, block: FlowchartBlock) -> str:
        if not self.render_mermaid:
            # 无脚本环境（如 PDF）：降级为"节点 + 连线"文本结构
            edges = block.edges or [(block.nodes[i], block.nodes[i + 1])
                                    for i in range(len(block.nodes) - 1)]
            nodes = "".join(f"<li>{escape(n)}</li>" for n in block.nodes)
            links = "".join(
                f"<li>{escape(a)} → {escape(b)}</li>" for a, b in edges)
            return (f'<div class="flowchart"><strong>流程图</strong>'
                    f"<ol>{nodes}</ol>"
                    f'<div><strong>连线关系：</strong></div><ul>{links}</ul>'
                    f"</div>")
        edges = block.edges or [(block.nodes[i], block.nodes[i + 1])
                                for i in range(len(block.nodes) - 1)]
        index = {n: i for i, n in enumerate(block.nodes)}
        mermaid = ["graph TD"]
        for i, node in enumerate(block.nodes):
            mermaid.append(f'    N{i}["{node}"]')
        for a, b in edges:
            if a in index and b in index:
                mermaid.append(f"    N{index[a]} --> N{index[b]}")
        fallback = "".join(f"<li>{escape(n)}</li>" for n in block.nodes)
        return (f'<div class="flowchart">'
                f'<div class="mermaid">{escape(chr(10).join(mermaid))}</div>'
                f"<noscript><ol>{fallback}</ol></noscript>"
                f"</div>{_MERMAID_SCRIPT}")

    # ------------------------------------------------------------------ 行内
    def _inlines(self, inlines: list[Inline], assets: AssetStore) -> str:
        parts: list[str] = []
        for inline in inlines:
            if isinstance(inline, Text):
                parts.append(self._text(inline))
            elif isinstance(inline, Link):
                label = self._inlines(inline.children, assets)
                title = f' title="{escape(inline.title)}"' if inline.title else ""
                parts.append(f'<a href="{escape(inline.target, quote=True)}"'
                             f'{title}>{label}</a>')
            elif isinstance(inline, ImageInline):
                ref = self._image_ref(inline, assets)
                parts.append(f'<img src="{escape(ref, quote=True)}" '
                             f'alt="{escape(inline.alt or "")}">')
            elif isinstance(inline, LineBreak):
                parts.append("<br>")
        return "".join(parts)

    @staticmethod
    def _text(text: Text) -> str:
        content = escape(text.text)
        if text.code:
            return f"<code>{content}</code>"
        if text.bold:
            content = f"<strong>{content}</strong>"
        if text.italic:
            content = f"<em>{content}</em>"
        if text.underline:
            content = f"<u>{content}</u>"
        if text.strike:
            content = f"<s>{content}</s>"
        return content

    # ------------------------------------------------------------------ 资源
    def _image_ref(self, image, assets: AssetStore) -> str:
        try:
            asset = assets.resolve_image(image.src, image.data, image.mime,
                                         hint=image.alt or "image")
            if self.embed_images and asset.path:
                from ..utils import make_data_uri
                return make_data_uri(asset.data, asset.mime)
            return asset.ref
        except ValueError:
            return image.src or ""

    @staticmethod
    def _attachment_ref(att: Attachment, assets: AssetStore) -> str:
        if att.data is None:
            return att.name
        return assets.add_attachment(att.name, att.data, att.mime).ref
