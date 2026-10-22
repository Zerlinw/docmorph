"""生成用于演示与测试的四种格式示例文档。

运行::

    python examples/make_samples.py

产物（samples/ 目录）：
    diagram.png          示例图片（PIL 绘制，使用系统中文字体）
    sample.docx          含标题/列表/表格/超链接/图片/流程图/嵌入附件的 DOCX
    sample.md            等价的 Markdown（含嵌套列表与 Mermaid 流程图）
    sample.html          由 Markdown 经 doc-converter 生成的 HTML
    sample.pdf           由 HTML 经 doc-converter 排版的 PDF（优先 Edge/Chromium）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

SAMPLES = ROOT / "samples"

DIAGRAM_MD = """# 文档转换助手示例文档

本文档用于演示 **doc-converter** 对四种格式的处理能力，涵盖标题、列表、
表格、超链接、图片、流程图（Mermaid）与代码块。

## 功能特性

1. 支持 PDF、DOCX、HTML、Markdown 任意互转
2. 保留标题、段落、列表、表格等结构
3. 正确处理图片、流程图与嵌入附件
   - 图片保存为文件并在目标格式中引用
   - 流程结构转换为目标格式可表达的形式
4. 支持单文件与批量转换

## 结构对比表

| 格式 | 结构保留 | 图片 | 流程图 | 附件 |
| --- | --- | --- | --- | --- |
| PDF | 启发式重建 | 裁剪导出 | 转为文本结构 | EmbeddedFile |
| DOCX | 完整 | 嵌入 | 组合形状提取 | OLE 对象 |
| HTML | 完整 | 文件引用 | Mermaid 渲染 | 链接引用 |
| Markdown | 完整 | 文件引用 | Mermaid 代码块 | 链接引用 |

## 流程图示例

```mermaid
graph TD
    A["读取源文档"] --> B["解析为统一 IR"]
    B --> C{"目标格式"}
    C -->|PDF| D["HTML 排版引擎"]
    C -->|DOCX| E["python-docx 写入"]
    C -->|Markdown| F["Mermaid + 管道表格"]
    C -->|HTML| G["语义化 HTML"]
```

## 代码示例

```python
from doc_converter import convert

result = convert("sample.docx", target="md", output="out/")
print(result.output_path)
```

## 图片与链接

![架构示意图](diagram.png)

项目地址：<https://github.com/example/doc-converter>

## 引用与提示

> 提示：PDF 是版式驱动格式，标题层级由字号启发式推断。

***

*—— 由示例生成器创建 ——*
"""


def _cjk_font(size: int):
    """加载系统中文字体，保证示意图中的中文可正常显示。"""
    from PIL import ImageFont

    candidates = [
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\Deng.ttf"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
    ]
    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


def make_diagram(path: Path) -> None:
    """用 PIL 绘制一张简单的架构示意图。"""
    from PIL import Image, ImageDraw

    width, height = 900, 420
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    font = _cjk_font(22)
    small = _cjk_font(18)
    boxes = [
        (60, 60, "DOCX", "#dbeafe"),
        (280, 60, "HTML", "#dcfce7"),
        (500, 60, "Markdown", "#fef9c3"),
        (720, 60, "PDF", "#fee2e2"),
    ]

    def centered(text: str, box, used_font, color="#0f172a") -> None:
        x, y, w, h = box
        bbox = draw.textbbox((0, 0), text, font=used_font)
        draw.text((x + (w - (bbox[2] - bbox[0])) / 2 - bbox[0],
                   y + (h - (bbox[3] - bbox[1])) / 2 - bbox[1]),
                  text, fill=color, font=used_font)

    for x, y, label, color in boxes:
        draw.rectangle([x, y, x + 130, y + 70], fill=color, outline="#334155",
                       width=3)
        centered(label, (x, y, 130, 70), font)
    draw.rectangle([260, 240, 640, 320], fill="#e0e7ff", outline="#334155",
                   width=3)
    centered("统一 IR（doc_converter.model）", (260, 240, 380, 80), small)
    for x, y, _, _ in boxes:
        draw.line([x + 65, y + 70, x + 65, 150], fill="#475569", width=3)
        draw.line([x + 65, 150, 450, 150], fill="#475569", width=3)
        draw.line([450, 150, 450, 240], fill="#475569", width=3)
    centered("读取器 Readers", (150, 158, 240, 26), small, "#475569")
    centered("写入器 Writers", (150, 330, 240, 26), small, "#475569")
    draw.line([450, 320, 450, 366], fill="#475569", width=3)
    draw.polygon([(438, 366), (462, 366), (450, 384)], fill="#475569")
    image.save(path)


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

_FLOW_XML = """<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
 xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
 xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">
<mc:AlternateContent>
<mc:Choice Requires="wps">
<w:drawing>
<wp:inline distT="0" distB="0" distL="0" distR="0">
<wp:extent cx="4600000" cy="2600000"/>
<wp:effectExtent l="0" t="0" r="0" b="0"/>
<wp:docPr id="90" name="FlowChartGroup"/>
<wp:cNvGraphicFramePr/>
<a:graphic>
<a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup">
<wpg:wgp>
<wpg:cNvGrpSpPr/>
<wpg:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="4600000" cy="2600000"/>
<a:chOff x="0" y="0"/><a:chExt cx="4600000" cy="2600000"/></a:xfrm></wpg:grpSpPr>
{wsp_nodes}
<wps:cxnSp>
<wps:cNvPr id="90" name="Connector1"/>
<wps:cNvCxnSpPr/>
<wps:spPr><a:xfrm><a:off x="1150000" y="800000"/><a:ext cx="0" cy="700000"/></a:xfrm>
<a:prstGeom prst="line"><a:avLst/></a:prstGeom></wps:spPr>
<wps:style><a:lnRef idx="2"/></wps:style>
</wps:cxnSp>
</wpg:wgp>
</a:graphicData>
</a:graphic>
</wp:inline>
</w:drawing>
</mc:Choice>
</mc:AlternateContent>
</w:r>"""

_FLOW_NODE = """<wps:wsp>
<wps:cNvPr id="{sid}" name="Node{sid}"/>
<wps:cNvSpPr/>
<wps:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="1600000" cy="600000"/></a:xfrm>
<a:prstGeom prst="roundRect"><a:avLst/></a:prstGeom></wps:spPr>
<wps:txbx><w:txbxContent><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:txbxContent></wps:txbx>
<wps:bodyPr/>
</wps:wsp>"""


def _flow_paragraph_xml() -> str:
    nodes = [
        ("读取源文档", 100000, 100000),
        ("解析为统一 IR", 1500000, 100000),
        ("写入目标格式", 2900000, 100000),
    ]
    wsp = "".join(
        _FLOW_NODE.format(sid=index + 10, x=x, y=y, text=text)
        for index, (text, x, y) in enumerate(nodes)
    )
    return _FLOW_XML.format(wsp_nodes=wsp)


def _add_hyperlink(paragraph, url: str, text: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    part = paragraph.part
    r_id = part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    r_pr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    r_pr.append(color)
    run.append(r_pr)
    t = OxmlElement("w:t")
    t.text = text
    run.append(t)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def make_docx(path: Path, diagram: Path) -> None:
    from docx import Document
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part
    from docx.oxml import parse_xml
    from docx.shared import Inches

    doc = Document()
    doc.core_properties.title = "文档转换助手示例文档"
    doc.core_properties.author = "doc-converter"
    doc.core_properties.subject = "转换示例"

    doc.add_heading("文档转换助手示例文档", level=0)
    doc.add_paragraph(
        "本文档用于演示 doc-converter 对 DOCX 的处理能力，涵盖标题、列表、"
        "表格、超链接、图片、流程图与嵌入附件。"
    )
    doc.add_heading("功能特性", level=1)
    for text in [
        "支持 PDF、DOCX、HTML、Markdown 任意互转",
        "保留标题、段落、列表、表格等结构",
        "正确处理图片、流程图与嵌入附件",
    ]:
        doc.add_paragraph(text, style="List Bullet")

    doc.add_heading("结构对比表", level=1)
    table = doc.add_table(rows=5, cols=4)
    table.style = "Table Grid"
    for index, text in enumerate(["格式", "结构保留", "图片", "附件"]):
        cell = table.cell(0, index)
        cell.text = ""
        run = cell.paragraphs[0].add_run(text)
        run.bold = True
    data = [
        ("PDF", "启发式重建", "裁剪导出", "EmbeddedFile"),
        ("DOCX", "完整", "嵌入", "OLE 对象"),
        ("HTML", "完整", "文件引用", "链接引用"),
        ("Markdown", "完整", "文件引用", "链接引用"),
    ]
    for row_index, row in enumerate(data, start=1):
        for column, text in enumerate(row):
            table.cell(row_index, column).text = text

    doc.add_heading("图片与链接", level=1)
    paragraph = doc.add_paragraph("项目地址：")
    _add_hyperlink(paragraph, "https://github.com/example/doc-converter",
                   "https://github.com/example/doc-converter")
    doc.add_paragraph("下面是行内图片：").add_run().add_picture(
        str(diagram), width=Inches(4))
    doc.add_paragraph("下面是块级图片：")
    block = doc.add_paragraph()
    block.alignment = 1
    block.add_run().add_picture(str(diagram), width=Inches(5))

    doc.add_heading("流程图", level=1)
    flow_paragraph = doc.add_paragraph()
    flow_paragraph._p.append(parse_xml(_flow_paragraph_xml()))

    doc.add_heading("代码示例", level=1)
    code = doc.add_paragraph()
    run = code.add_run('from doc_converter import convert\n'
                       'result = convert("sample.docx", target="md")\n'
                       'print(result.output)')
    run.font.name = "Consolas"

    payload = b"name,value\nsample-attachment,1\n"
    part = Part(
        PackURI("/word/embeddings/sample-data.csv"),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        payload,
        doc.part.package,
    )
    doc.part.relate_to(
        part,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package",
    )
    doc.add_paragraph("上面嵌入了一个 CSV 附件（sample-data.csv）。")

    doc.save(str(path))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    diagram = SAMPLES / "diagram.png"
    make_diagram(diagram)
    print(f"[ok] {diagram}")

    md_path = SAMPLES / "sample.md"
    md_path.write_text(DIAGRAM_MD, encoding="utf-8")
    print(f"[ok] {md_path}")

    docx_path = SAMPLES / "sample.docx"
    make_docx(docx_path, diagram)
    print(f"[ok] {docx_path}")

    from doc_converter import convert

    html_path = SAMPLES / "sample.html"
    convert(md_path, target="html", output=html_path)
    print(f"[ok] {html_path}")

    pdf_path = SAMPLES / "sample.pdf"
    result = convert(html_path, target="pdf", output=pdf_path)
    print(f"[ok] {pdf_path} (engine={result.engine})")


if __name__ == "__main__":
    main()
