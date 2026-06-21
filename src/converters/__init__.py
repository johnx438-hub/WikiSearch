"""
Phase 4: Office+PDF 智能转换管道

将 DOCX/PDF 转换为结构化 Markdown（树状文件集合）。

模块结构:
    converters/
        __init__.py          ← 统一导出接口
        docx_converter.py    ← DOCX 解析器 (python-docx)
        pdf_converter.py     ← PDF 解析器 (PyMuPDF4LLM)
    conversion_pipeline.py   ← 转换管道主引擎（含 Tree Generator）
"""
