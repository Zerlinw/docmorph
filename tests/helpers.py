"""测试用文档构造工具：尽量模拟真实 Word 文档的结构。"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Inches


def build_word_like_docx(path: Path, image: Path | None = None) -> Path:
    """构造一个包含标题/编号列表/合并表格/超链接/图片/内容控件/修订/文本框/附件的 DOCX。"""
    doc = Document()
    doc.core_properties.title = "Word 结构测试"
    doc.core_properties.author = "测试作者"
    doc.core_properties.subject = "自动化测试"

    doc.add_heading("Word 结构测试", level=0)
    doc.add_paragraph("这是一段包含加粗与斜体的普通文本。")
    bold_run = doc.add_paragraph().add_run("加粗片段")
    bold_run.bold = True
    italic_run = doc.add_paragraph().add_run("斜体片段")
    italic_run.italic = True

    doc.add_heading("编号列表", level=1)
    for level, text, num_id in (
            (0, "编号第一项", 5),
            (0, "编号第二项", 5),
            (1, "嵌套编号项", 5),
            (0, "编号第三项", 5)):
        para = doc.add_paragraph(text, style="List Paragraph")
        para._p.get_or_add_pPr().insert(
            0, parse_xml(
                f'<w:numPr {nsdecls("w")}><w:ilvl w:val="{level}"/>'
                f'<w:numId w:val="{num_id}"/></w:numPr>'))

    doc.add_heading("项目符号", level=1)
    for text in ("符号第一项", "符号第二项"):
        doc.add_paragraph(text, style="List Bullet")

    doc.add_heading("合并表格", level=1)
    table_xml = (
        f'<w:tbl {nsdecls("w")}>'
        '<w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/></w:tblPr>'
        '<w:tblGrid><w:gridCol w:w="2200"/><w:gridCol w:w="2200"/><w:gridCol w:w="2200"/></w:tblGrid>'
        '<w:tr><w:trPr><w:tblHeader/></w:trPr>'
        '<w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:rPr><w:b/></w:rPr>'
        '<w:t>合并表头</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>纵向合并</w:t></w:r></w:p></w:tc>'
        '</w:tr>'
        '<w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc></w:tr>'
        '<w:tr><w:tc><w:p><w:r><w:t>C</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:p><w:r><w:t>D</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:p><w:r><w:t>E</w:t></w:r></w:p></w:tc></w:tr>'
        '</w:tbl>')
    doc.element.body.append(parse_xml(table_xml))

    doc.add_heading("链接与图片", level=1)
    para = doc.add_paragraph("项目地址：")
    _add_hyperlink(para, "https://example.com/project", "示例项目")
    if image is not None and image.exists():
        doc.add_paragraph("行内图片：").add_run().add_picture(str(image), width=Inches(2.5))

    doc.add_heading("内容控件与修订", level=1)
    doc.element.body.append(parse_xml(
        f'<w:sdt {nsdecls("w")}><w:sdtPr/><w:sdtContent>'
        '<w:p><w:r><w:t>内容控件中的文字</w:t></w:r></w:p>'
        '</w:sdtContent></w:sdt>'))
    doc.element.body.append(parse_xml(
        f'<w:p {nsdecls("w")}><w:r><w:t>修订前</w:t></w:r>'
        '<w:ins w:id="99" w:author="a" w:date="2024-01-01T00:00:00Z">'
        '<w:r><w:t>插入的文字</w:t></w:r></w:ins></w:p>'))
    doc.element.body.append(parse_xml(
        f'<w:p {nsdecls("w")}><w:r><w:drawing>'
        '<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
        '<wp:extent cx="1000000" cy="500000"/>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        '<wps:wsp xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        '<wps:cNvPr id="1" name="TextBox 1"/><wps:txbx><w:txbxContent>'
        '<w:p><w:r><w:t>这是一个文本框</w:t></w:r></w:p>'
        '</w:txbxContent></wps:txbx></wps:wsp>'
        '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'))

    doc.add_heading("代码与附件", level=1)
    code = doc.add_paragraph()
    run = code.add_run("def hello():\n    return 'hi'")
    run.font.name = "Consolas"

    payload = b"name,value\nsample,1\n"
    _attach_part(doc, "sample-data.csv", payload)
    doc.add_paragraph("上面嵌入了一个 CSV 附件。")

    doc.save(str(path))
    _localize_style_names(path)
    return path


def _add_hyperlink(paragraph, url: str, text: str) -> None:
    from docx.oxml import OxmlElement

    part = paragraph.part
    r_id = part.relate_to(
        url, "http://schemas.openxmlformats.org/officeDocument/2006/"
             "relationships/hyperlink", is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    properties = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    properties.append(color)
    run.append(properties)
    text_element = OxmlElement("w:t")
    text_element.text = text
    run.append(text_element)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _attach_part(doc, name: str, payload: bytes) -> None:
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part

    part = Part(
        PackURI(f"/word/embeddings/{name}"),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        payload,
        doc.part.package,
    )
    doc.part.relate_to(
        part,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package",
    )


def _localize_style_names(path: Path) -> None:
    """把内置样式名改成中文（模拟 WPS / 中文版 Word 写出的文档）。"""
    replacements = {
        'w:val="heading 1"': 'w:val="标题 1"',
        'w:val="heading 2"': 'w:val="标题 2"',
        'w:val="Title"': 'w:val="标题"',
        'w:val="List Number"': 'w:val="编号列表"',
        'w:val="List Bullet"': 'w:val="项目符号"',
        'w:val="List Paragraph"': 'w:val="列表段落"',
    }
    temp = path.with_suffix(".localized.docx")
    with zipfile.ZipFile(path) as source, \
            zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "word/styles.xml":
                text = data.decode("utf-8")
                for source_text, replacement in replacements.items():
                    text = text.replace(source_text, replacement)
                data = text.encode("utf-8")
            target.writestr(item, data)
    shutil.move(str(temp), str(path))
