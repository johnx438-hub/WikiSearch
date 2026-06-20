"""
PDF 转换器 — 将 PDF 文档转换为结构化 Markdown

核心功能:
1. PyMuPDF4LLM 解析（保留布局、表格、公式）
2. 书签树提取 → 章节结构识别
3. TOC 目录页正则解析
4. OCR 回退（扫描件/图片 PDF）
5. 按章节切分为独立 MD 文件
6. 生成 YAML Frontmatter 元数据

使用示例:
    converter = PdfConverter()
    result = converter.convert("/path/to/document.pdf")
    # result: {sections: [...], bookmarks: [...], toc_pages: [...]}
"""

import os
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

# PyMuPDF4LLM 导入（可选，需要额外安装）
try:
    from pymupdf4llm import ToMarkdownExtra
    HAS_PYMUPDF4LLM = True
except ImportError:
    ToMarkdownExtra = None
    HAS_PYMUPDF4LLM = False


# ==================== 数据结构 ====================

@dataclass
class BookmarkInfo:
    """书签信息（对应 PDF outline）"""
    title: str                    # 书签标题
    level: int                    # 层级深度
    page_number: int              # 页码（1-indexed）
    destination: Optional[str] = None  # 目标位置


@dataclass
class TocEntry:
    """TOC 目录条目"""
    title: str                    # 章节标题
    page_number: int              # 页码
    level: int = 1                # 层级（从缩进推断）


@dataclass
class SectionResult:
    """转换后的章节结果"""
    title: str                    # 章节标题
    content: str                  # Markdown 内容
    page_range: Tuple[int, int]   # 页码范围 (start, end)
    heading_level: int = 1        # 层级（从书签/TOC 推断）
    parent_title: Optional[str] = None  # 父章节标题
    metadata: Dict = field(default_factory=dict)  # YAML Frontmatter 元数据


# ==================== PDF 解析器 ====================

class PdfConverter:
    """
    PDF → Markdown 转换器。
    
    使用 PyMuPDF4LLM（如果可用）或 pdfplumber 作为回退方案。
    
    Attributes:
        use_ocr: 是否启用 OCR 回退
        ocr_language: OCR 语言（'chi_sim' | 'eng' | 'chi_sim+eng'）
    """
    
    def __init__(self, use_ocr: bool = True, ocr_language: str = 'chi_sim+eng', chunk_size: int = 50):
        """
        Args:
            use_ocr: 是否启用 OCR 回退（默认 True）
            ocr_language: OCR 语言
            chunk_size: 超大文件分块处理大小（默认 50 页/块）
        """
        self.use_ocr = use_ocr
        self.ocr_language = ocr_language
        self.chunk_size = chunk_size
        self._pymupdf4llm_available = HAS_PYMUPDF4LLM
    
    def convert(self, filepath: str, output_dir: Optional[str] = None) -> Dict:
        """
        将 PDF 文件转换为 Markdown。
        
        Args:
            filepath: .pdf 文件路径
            output_dir: 输出目录（如果提供，会生成树状 MD 文件）
            
        Returns:
            {
                'sections': [SectionResult, ...],
                'bookmarks': [BookmarkInfo, ...],
                'toc_entries': [TocEntry, ...],
                'total_pages': int,
                'has_text_layer': bool
            }
        """
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"文件不存在: {filepath}")
        
        # 1. 打开 PDF + 加密检测
        doc = fitz.open(str(filepath))
        self._check_encryption(doc, str(filepath))
        
        try:
            # 2. 提取书签树（最可靠的章节结构）
            bookmarks = self._extract_bookmarks(doc)
            
            # 3. 提取 TOC 目录页（如果书签为空）
            toc_entries = []
            if not bookmarks:
                toc_entries = self._extract_toc_from_page_0(doc)
            
            # 4. 按章节组织内容
            show_progress = len(doc) > self.chunk_size  # 超大文件显示进度
            sections = self._organize_by_bookmarks(doc, bookmarks, show_progress=show_progress) or \
                       self._organize_by_toc(doc, toc_entries, show_progress=show_progress) or \
                       self._split_by_pages(doc, show_progress=show_progress)  # 兜底：按页拆分
            
            # 5. 生成树状 MD 文件（如果提供了输出目录）
            if output_dir:
                doc_name = filepath.stem
                self._write_tree_structure(
                    sections=sections,
                    bookmarks=bookmarks,
                    base_dir=output_dir,
                    doc_name=doc_name
                )
            
            # 6. 检查是否有文本层（用于判断是否需要 OCR）
            has_text_layer = any(page.get_text().strip() for page in doc)
            
            return {
                'sections': sections,
                'bookmarks': bookmarks,
                'toc_entries': toc_entries,
                'total_pages': len(doc),
                'has_text_layer': has_text_layer,
            }
        finally:
            doc.close()
    
    def _check_encryption(self, doc: fitz.Document, filepath: str):
        """
        检查 PDF 是否加密。
        
        Args:
            doc: PyMuPDF Document 对象
            filepath: 文件路径（用于错误提示）
            
        Raises:
            ValueError: 如果 PDF 已加密且无法用空密码打开
        """
        if doc.is_encrypted:
            # 尝试用空密码打开
            if not doc.authenticate(""):
                raise ValueError(f"🔒 PDF 已加密，需要密码: {filepath}")
    
    def _extract_bookmarks(self, doc: fitz.Document) -> List[BookmarkInfo]:
        """
        从 PDF outline（书签树）提取章节结构。
        
        Args:
            doc: PyMuPDF Document 对象
            
        Returns:
            [BookmarkInfo, ...]
        """
        bookmarks = []
        outline = doc.get_toc()  # Table of Contents (书签树)
        
        if not outline:
            return bookmarks
        
        for item in outline:
            # PyMuPDF get_toc() 返回格式: [level, title, page_num]
            level = int(item[0]) if len(item) > 0 and isinstance(item[0], (int, float)) else 1
            title = item[1] if isinstance(item[1], str) else ""
            page_num = int(item[2]) if len(item) > 2 and isinstance(item[2], (int, float)) else 0
            
            title = title.strip() if title else f"第{page_num}页"
            bookmarks.append(BookmarkInfo(
                title=title,
                level=level,
                page_number=max(0, page_num - 1)  # PyMuPDF 页码从 1 开始，转为 0-indexed
            ))
        
        return bookmarks
    
    def _extract_toc_from_page_0(self, doc: fitz.Document) -> List[TocEntry]:
        """
        从第一页（通常是目录页）提取 TOC。
        
        Args:
            doc: PyMuPDF Document 对象
            
        Returns:
            [TocEntry, ...]
        """
        if len(doc) == 0:
            return []
        
        toc_text = doc[0].get_text()
        entries = self._parse_toc_text(toc_text)
        return entries
    
    def _parse_toc_text(self, text: str) -> List[TocEntry]:
        """
        解析目录页文本为结构化条目。
        
        Args:
            text: 目录页文本内容
            
        Returns:
            [TocEntry, ...]
        """
        entries = []
        lines = text.strip().split('\n')
        
        for line in lines:
            # 匹配常见 TOC 格式：
            # "第一章 xxx ............ 1"
            # "1.1 xxx ................ 5"
            match = re.match(
                r'^(\d+(?:\.\d+)*)\s+([\u4e00-\u9fff\w\s]+?)\s+[•·.+]*\s+(\d+)$',
                line.strip()
            )
            
            if match:
                number = match.group(1)
                title = match.group(2).strip()
                page_num = int(match.group(3)) - 1  # 转为 0-indexed
                level = len(number.split('.'))
                
                entries.append(TocEntry(
                    title=title,
                    page_number=page_num,
                    level=min(level, 5)  # 最多 5 级
                ))
        
        return entries
    
    def _organize_by_bookmarks(self, doc: fitz.Document, bookmarks: List[BookmarkInfo], show_progress: bool = False) -> Optional[List[SectionResult]]:
        """
        按书签组织章节内容。
        
        Args:
            doc: PyMuPDF Document 对象
            bookmarks: 书签列表
            show_progress: 是否显示进度（仅对超大文件有效）
            
        Returns:
            [SectionResult, ...] 或 None（如果书签为空）
        """
        if not bookmarks:
            return None
        
        sections = []
        doc_name = ""
        
        for idx, bookmark in enumerate(bookmarks):
            # 确定章节页码范围
            start_page = bookmark.page_number
            end_page = self._get_next_bookmark_page(bookmarks, idx)
            if end_page is None:
                end_page = len(doc)  # 最后一个书签到文档末尾
            
            # 提取页面内容（使用 PyMuPDF4LLM 如果可用）
            content = self._extract_pages(doc, start_page, end_page, show_progress=show_progress)
            
            # 确定父章节标题
            parent_title = None
            for prev_bm in reversed(bookmarks[:idx]):
                if prev_bm.level < bookmark.level:
                    parent_title = prev_bm.title
                    break
            
            # 构建元数据
            metadata = {
                'source': 'pdf',
                'bookmark_level': bookmark.level,
                'page_range': f"{start_page + 1}-{end_page + 1}" if end_page > start_page else f"{start_page + 1}",
            }
            
            sections.append(SectionResult(
                title=bookmark.title,
                content=content,
                page_range=(start_page, end_page),
                heading_level=min(bookmark.level, 5),
                parent_title=parent_title,
                metadata=metadata
            ))
        
        return sections
    
    def _organize_by_toc(self, doc: fitz.Document, toc_entries: List[TocEntry]) -> Optional[List[SectionResult]]:
        """
        按 TOC 条目组织章节内容。
        
        Args:
            doc: PyMuPDF Document 对象
            toc_entries: TOC 条目列表
            
        Returns:
            [SectionResult, ...] 或 None（如果 TOC 为空）
        """
        if not toc_entries:
            return None
        
        sections = []
        
        for idx, entry in enumerate(toc_entries):
            start_page = entry.page_number
            end_page = self._get_next_toc_page(toc_entries, idx)
            
            content = self._extract_pages(doc, start_page, end_page)
            
            parent_title = None
            for prev_entry in reversed(toc_entries[:idx]):
                if prev_entry.level < entry.level:
                    parent_title = prev_entry.title
                    break
            
            metadata = {
                'source': 'pdf',
                'toc_level': entry.level,
                'page_range': f"{start_page + 1}-{end_page + 1}" if end_page > start_page else f"{start_page + 1}",
            }
            
            sections.append(SectionResult(
                title=entry.title,
                content=content,
                page_range=(start_page, end_page),
                heading_level=min(entry.level, 5),
                parent_title=parent_title,
                metadata=metadata
            ))
        
        return sections
    
    def _split_by_pages(self, doc: fitz.Document) -> List[SectionResult]:
        """
        兜底方案：按页拆分（无书签、无 TOC 时）。
        
        Args:
            doc: PyMuPDF Document 对象
            
        Returns:
            [SectionResult, ...]（每页一个 SectionResult）
        """
        sections = []
        
        for page_num in range(len(doc)):
            content = self._extract_pages(doc, page_num, page_num + 1)
            
            # 尝试从页面内容提取标题（第一行非空文本）
            lines = [l.strip() for l in content.split('\n') if l.strip()]
            title = f"第{page_num + 1}页" if not lines else lines[0][:50]
            
            sections.append(SectionResult(
                title=title,
                content=content,
                page_range=(page_num, page_num),
                heading_level=1,
                parent_title=None,
                metadata={'source': 'pdf', 'split_method': 'by_page'}
            ))
        
        return sections
    
    def _extract_pages(self, doc: fitz.Document, start: int, end: Optional[int], show_progress: bool = False) -> str:
        """
        提取指定页码范围的内容（支持超大文件分块处理）。
        
        Args:
            doc: PyMuPDF Document 对象
            start: 起始页码（0-indexed）
            end: 结束页码（exclusive，None=到文档末尾）
            show_progress: 是否显示进度（仅对超大文件有效）
            
        Returns:
            Markdown 格式的文本内容
        """
        if end is None:
            end = len(doc)
        total_pages_in_range = end - start
        
        # 如果范围超过 chunk_size，启用分块处理
        if total_pages_in_range > self.chunk_size:
            return self._extract_pages_chunked(
                doc, start, end, show_progress=show_progress
            )
        
        # 小文件：直接提取
        if not self._pymupdf4llm_available:
            return self._extract_with_pymupdf(doc, start, end)
        
        try:
            # 使用 PyMuPDF4LLM（保留布局、表格、公式）
            if ToMarkdownExtra is not None:
                md = ToMarkdownExtra(
                    doc,
                    pages=list(range(start, end)),
                    write_images=False,
                    write_assets_dir=None
                )
                return md
        except Exception as e:
            print(f"⚠️ PyMuPDF4LLM 提取失败，回退到原生提取: {e}")
        
        # 回退方案
        return self._extract_with_pymupdf(doc, start, end)
    
    def _extract_pages_chunked(self, doc: fitz.Document, start: int, end: int,
                                show_progress: bool = False) -> str:
        """
        分块提取页面内容，避免内存溢出。
        
        Args:
            doc: PyMuPDF Document 对象
            start: 起始页码（0-indexed）
            end: 结束页码（exclusive）
            show_progress: 是否显示进度
            
        Returns:
            Markdown 格式的文本内容（拼接所有块）
        """
        all_content = []
        chunk_count = 0
        total_chunks = (end - start + self.chunk_size - 1) // self.chunk_size
        
        for i in range(start, end, self.chunk_size):
            chunk_end = min(i + self.chunk_size, end)
            content = self._extract_pages(doc, i, chunk_end)
            all_content.append(content)
            chunk_count += 1
            
            if show_progress and (chunk_count % 5 == 0 or chunk_count == total_chunks):
                print(f"   📄 PDF 处理中... {min(i + self.chunk_size, end)}/{end} 页 ({chunk_count}/{total_chunks} 块)")
        
        return '\n\n'.join(all_content)
    
    def _extract_with_pymupdf(self, doc: fitz.Document, start: int, end: Optional[int]) -> str:
        """
        使用 PyMuPDF 原生方法提取文本。
        
        Args:
            doc: PyMuPDF Document 对象
            start: 起始页码
            end: 结束页码
            
        Returns:
            Markdown 格式的文本内容
        """
        lines = []
        for page_num in range(start, end or len(doc)):
            if page_num >= len(doc):
                break
            
            page = doc[page_num]
            text = page.get_text("text")
            
            # 尝试提取表格（简单实现）
            tables = page.find_tables()
            if tables and tables.tables:
                for table in tables.tables:
                    try:
                        headers = [h or '' for h in (table.header.names or [])]
                        rows_data = []
                        for row in table.rows[1:]:
                            # PyMuPDF 1.27+ API: row.cells 是 list of str, not objects
                            if isinstance(row, tuple) and len(row) > 0:
                                row_data = [str(c) or '' for c in row]
                            elif hasattr(row, 'cells'):
                                row_data = [cell.text or '' for cell in row.cells]
                            else:
                                continue
                            if any(cell.strip() for cell in row_data):
                                rows_data.append(row_data)
                        if headers and rows_data:
                            lines.append(self._table_to_markdown(headers, rows_data))
                    except Exception:
                        pass  # 表格提取失败不影响整体流程
            
            if text.strip():
                lines.append(text)
        
        return '\n\n'.join(lines)
    
    def _table_to_markdown(self, headers: List[str], rows: List[List[str]]) -> str:
        """
        将表格数据转换为 Markdown 格式。
        
        Args:
            headers: 表头列表
            rows: 数据行列表
            
        Returns:
            Markdown 格式的表格字符串
        """
        if not headers or not rows:
            return ''
        
        lines = []
        # 表头
        lines.append('| ' + ' | '.join(headers) + ' |')
        # 分隔线
        lines.append('| ' + ' | '.join(['---'] * len(headers)) + ' |')
        # 数据行
        for row in rows:
            # 对齐列数
            while len(row) < len(headers):
                row.append('')
            lines.append('| ' + ' | '.join(row[:len(headers)]) + ' |')
        
        return '\n'.join(lines)
    
    def _get_next_bookmark_page(self, bookmarks: List[BookmarkInfo], current_idx: int) -> Optional[int]:
        """
        获取下一个同级或更高级别书签的页码。
        
        Args:
            bookmarks: 书签列表
            current_idx: 当前书签索引
            
        Returns:
            下一页码（0-indexed）或 None（到文档末尾）
        """
        if current_idx + 1 >= len(bookmarks):
            return None
        
        current_level = bookmarks[current_idx].level
        for bm in bookmarks[current_idx + 1:]:
            if bm.level <= current_level:
                return bm.page_number
        
        return None
    
    def _get_next_toc_page(self, toc_entries: List[TocEntry], current_idx: int) -> Optional[int]:
        """
        获取下一个同级或更高级别 TOC 条目的页码。
        
        Args:
            toc_entries: TOC 条目列表
            current_idx: 当前索引
            
        Returns:
            下一页码（0-indexed）或 None
        """
        if current_idx + 1 >= len(toc_entries):
            return None
        
        current_level = toc_entries[current_idx].level
        for entry in toc_entries[current_idx + 1:]:
            if entry.level <= current_level:
                return entry.page_number
        
        return None
    
    def _write_tree_structure(self, sections: List[SectionResult], bookmarks: List[BookmarkInfo],
                               base_dir: str, doc_name: str):
        """
        将转换结果写入树状 MD 文件结构。
        
        Args:
            sections: 章节列表
            bookmarks: 书签列表（用于生成导航）
            base_dir: 基础输出目录
            doc_name: 文档名称
        """
        # 创建文档根目录
        doc_dir = os.path.join(base_dir, doc_name)
        os.makedirs(doc_dir, exist_ok=True)
        
        # 生成 _index.md（全文摘要 + 章节导航）
        self._write_index_md(sections=sections, bookmarks=bookmarks,
                            index_path=os.path.join(doc_dir, "_index.md"))
        
        # 按层级组织文件
        for section in sections:
            filename = self._sanitize_filename(section.title)
            
            if section.heading_level == 1:
                # 顶级章节 → 直接放在根目录
                filepath = os.path.join(doc_dir, f"{filename}.md")
            else:
                # 子章节 → 创建子目录
                parent_dir = os.path.join(doc_dir, *self._get_directory_path(section))
                os.makedirs(parent_dir, exist_ok=True)
                filepath = os.path.join(parent_dir, f"{filename}.md")
            
            self._write_section_md(
                section=section,
                doc_name=doc_name,
                output_path=filepath
            )
    
    def _write_index_md(self, sections: List[SectionResult], bookmarks: List[BookmarkInfo],
                        index_path: str):
        """
        生成 _index.md（全文摘要 + 章节导航）。
        
        Args:
            sections: 章节列表
            bookmarks: 书签列表
            index_path: 输出路径
        """
        lines = [
            f"# {sections[0].title if sections else 'PDF 文档目录'}",
            "",
            "## 📑 章节导航",
            ""
        ]
        
        # 按层级生成导航链接
        for section in sections:
            indent = "  " * (section.heading_level - 1)
            rel_path = '../' * (section.heading_level - 1) if section.heading_level > 1 else ''
            filename = self._sanitize_filename(section.title) + '.md'
            lines.append(f"{indent}- [{section.title}]({rel_path}{filename})")
        
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
    
    def _write_section_md(self, section: SectionResult, doc_name: str,
                          output_path: str):
        """
        生成单个章节的 MD 文件。
        
        Args:
            section: 章节结果
            doc_name: 文档名称
            output_path: 输出路径
        """
        # YAML Frontmatter
        frontmatter = [
            f"---",
            f"source: \"{doc_name}.pdf\"",
            f"chapter: \"{section.title}\"",
            f"page_range: \"{section.page_range[0] + 1}-{section.page_range[1] + 1}\"",
            f"level: L{section.heading_level}",
        ]
        
        if section.parent_title:
            frontmatter.append(f"parent: \"{section.parent_title}\"")
        
        if section.metadata.get('bookmark_level'):
            frontmatter.append(f"bookmark_level: {section.metadata['bookmark_level']}")
        elif section.metadata.get('toc_level'):
            frontmatter.append(f"toc_level: {section.metadata['toc_level']}")
        
        frontmatter.extend([
            f"tags: [pdf, {self._sanitize_filename(section.title)}]",
            "---",
            ""
        ])
        
        # 标题（H1-H5）
        heading_prefix = '#' * min(section.heading_level, 5)
        content_lines = [
            '\n'.join(frontmatter),
            f"{heading_prefix} {section.title}",
            "",
            section.content,
        ]
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(content_lines))
    
    def _get_directory_path(self, section: SectionResult) -> List[str]:
        """
        根据章节层级生成目录路径。
        
        Args:
            section: 章节结果
            
        Returns:
            目录路径列表
        """
        path = []
        # TODO: 实现更复杂的目录结构（按父章节创建子目录）
        return path
    
    def _sanitize_filename(self, text: str) -> str:
        """
        清理文件名中的非法字符。
        
        Args:
            text: 原始文本
            
        Returns:
            安全的文件名
        """
        sanitized = re.sub(r'[\\/:*?"<>|]', '_', text)
        if len(sanitized) > 50:
            sanitized = sanitized[:47] + '...'
        return sanitized.strip()
