"""
转换管道主引擎 — Phase 4 核心

将 DOCX/PDF 转换为结构化 Markdown，并集成到现有 WikiIndex。

模块结构:
    Router          ← 智能路由层（格式判断 + 转换器选择）
    TreeGenerator   ← 树状 MD 生成器（按标题切分 + YAML Frontmatter）
    QualityChecker  ← 质量自检引擎（Stage 0-3 非对称拦截）
    ConversionPipeline ← 主管道（串联所有组件）

使用示例:
    pipeline = ConversionPipeline(db_path="./wiki_db")
    result = pipeline.convert("/path/to/document.docx")
"""

import os
import re
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

# 导入转换器
from .converters.docx_converter import DocxConverter, SectionResult as DocxSection
from .converters.pdf_converter import PdfConverter, SectionResult as PdfSection


# ==================== 数据结构 ====================

@dataclass
class ConversionResult:
    """转换结果"""
    file_path: str                          # 原始文件路径
    sections: List[dict]                    # 章节列表（统一格式）
    total_pages: int                        # 总页数
    conversion_method: str                  # 'docx' | 'pdf'
    quality_score: float = 0.0              # 质量评分 (0-1)
    md_files_created: List[str] = field(default_factory=list)  # 生成的 MD 文件路径列表
    metadata: Dict = field(default_factory=dict)  # 额外元数据


@dataclass
class QualityCheckResult:
    """质量检查结果"""
    score: float                            # 综合评分 (0-1)
    issues: List[str] = field(default_factory=list)  # 发现的问题列表
    stage: int = 0                          # 触发的拦截阶段 (0-3)
    needs_llm_fix: bool = False             # 是否需要 LLM 修复


# ==================== 智能路由层 ====================

class Router:
    """
    文件类型路由器 — 根据扩展名和 Magic Bytes 选择转换器。
    
    支持格式:
        .docx → DocxConverter (python-docx)
        .pdf  → PdfConverter (PyMuPDF4LLM / pdfplumber)
    """
    
    # 支持的扩展名映射
    SUPPORTED_FORMATS = {
        '.docx': 'docx',
        '.pdf': 'pdf',
    }
    
    # Magic Bytes（文件头签名）
    MAGIC_BYTES = {
        b'\xD0\xCF\x11\xE0': 'docx',  # Office Open XML
        b'%PDF': 'pdf',                  # PDF
    }
    
    def __init__(self):
        self.docx_converter = DocxConverter()
        self.pdf_converter = PdfConverter()
    
    def detect_format(self, filepath: str) -> Optional[str]:
        """
        检测文件格式（优先 Magic Bytes，其次扩展名）。
        
        Args:
            filepath: 文件路径
            
        Returns:
            'docx' | 'pdf' | None
        """
        filepath = Path(filepath)
        
        # 1. 尝试 Magic Bytes（最可靠）
        try:
            with open(filepath, 'rb') as f:
                header = f.read(8)
                for magic, fmt in self.MAGIC_BYTES.items():
                    if header[:len(magic)] == magic:
                        return fmt
        except (IOError, OSError):
            pass
        
        # 2. 回退到扩展名
        ext = filepath.suffix.lower()
        return self.SUPPORTED_FORMATS.get(ext)
    
    def get_converter(self, format_type: str):
        """
        根据格式类型获取转换器。
        
        Args:
            format_type: 'docx' | 'pdf'
            
        Returns:
            对应的转换器实例
        """
        if format_type == 'docx':
            return self.docx_converter
        elif format_type == 'pdf':
            return self.pdf_converter
        else:
            raise ValueError(f"不支持的格式: {format_type}")
    
    def convert(self, filepath: str) -> Optional[str]:
        """
        自动检测格式并转换。
        
        Args:
            filepath: 文件路径
            
        Returns:
            格式类型 ('docx' | 'pdf')，失败返回 None
        """
        format_type = self.detect_format(filepath)
        if not format_type:
            print(f"⚠️ 无法识别文件格式: {filepath}")
            return None
        
        converter = self.get_converter(format_type)
        result = converter.convert(filepath)
        
        return format_type, result


# ==================== 质量自检引擎 ====================

class QualityChecker:
    """
    四阶段非对称拦截协议。
    
    Stage 0: 零成本规则拦截（断言层）
    Stage 1: 启发式打分
    Stage 2: LLM 局部修复（可选）
    Stage 3: LLM 全量转写兜底
    """
    
    def __init__(self):
        self.stage_thresholds = {
            'stage_0_fail': 0.3,   # Stage 0 失败 → score < 0.3
            'stage_1_low': 0.5,    # Stage 1 低分 → score < 0.5
            'stage_2_trigger': 0.8, # Stage 2 触发 → 0.5 <= score < 0.8
        }
    
    def check(self, sections: List[dict]) -> QualityCheckResult:
        """
        执行四阶段质量检查。
        
        Args:
            sections: 章节列表（统一格式）
            
        Returns:
            QualityCheckResult
        """
        if not sections:
            return QualityCheckResult(
                score=0.0,
                issues=['无章节内容'],
                stage=3,
                needs_llm_fix=True
            )
        
        # Stage 0: 零成本规则拦截
        stage_0_result = self._stage_0_assertions(sections)
        if not stage_0_result['passed']:
            return QualityCheckResult(
                score=stage_0_result['score'],
                issues=stage_0_result['issues'],
                stage=0,
                needs_llm_fix=True
            )
        
        # Stage 1: 启发式打分
        stage_1_score = self._stage_1_heuristic_scoring(sections)
        
        if stage_1_score >= self.stage_thresholds['stage_2_trigger']:
            # Score >= 0.8 → 直接通过
            return QualityCheckResult(
                score=stage_1_score,
                issues=[],
                stage=1,
                needs_llm_fix=False
            )
        
        elif stage_1_score >= self.stage_thresholds['stage_1_low']:
            # 0.5 <= Score < 0.8 → LLM 局部修复
            return QualityCheckResult(
                score=stage_1_score,
                issues=['结构不完整，建议 LLM 修复'],
                stage=2,
                needs_llm_fix=True
            )
        
        else:
            # Score < 0.5 → LLM 全量转写兜底
            return QualityCheckResult(
                score=stage_1_score,
                issues=['质量较低，建议 LLM 全量转写'],
                stage=3,
                needs_llm_fix=True
            )
    
    def _stage_0_assertions(self, sections: List[dict]) -> dict:
        """
        Stage 0: 零成本规则拦截（断言层）。
        
        检查明显的格式崩塌：
        - 单页字符数异常超标
        - 无任何标题标记 #
        - 连续出现 5+ 空行
        - 乱码率 > 5%
        """
        issues = []
        total_chars = 0
        has_heading = False
        consecutive_empty = 0
        max_consecutive_empty = 0
        garbled_count = 0
        
        for section in sections:
            content = section.get('content', '')
            total_chars += len(content)
            
            # 检查标题标记（改用 metadata 中的 heading_level，而非 content 里的 #）
            if section.get('heading_level', 1) >= 1:
                has_heading = True
            
            # 检查连续空行
            for line in content.split('\n'):
                if not line.strip():
                    consecutive_empty += 1
                    max_consecutive_empty = max(max_consecutive_empty, consecutive_empty)
                else:
                    consecutive_empty = 0
            
            # 检查乱码（正确排除中文字符：CJK Unified Ideographs + CJK Symbols & Punctuation）
            if content:
                non_ascii = sum(
                    1 for c in content
                    if ord(c) > 127 and not (
                        (0x4E00 <= ord(c) <= 0x9FFF) or   # CJK Unified Ideographs
                        (0x3000 <= ord(c) <= 0x303F)      # CJK Symbols & Punctuation
                    )
                )
                garbled_count += non_ascii
        
        # 断言检查
        if total_chars == 0:
            issues.append('总字符数为 0（可能提取失败）')
        
        if not has_heading and len(sections) > 1:
            issues.append('无标题标记（#），结构可能不完整')
        
        if max_consecutive_empty > 5:
            issues.append(f'连续空行过多（{max_consecutive_empty} 行）')
        
        # 乱码率检查
        if total_chars > 0:
            garbled_rate = garbled_count / total_chars
            if garbled_rate > 0.05:
                issues.append(f'乱码率过高（{garbled_rate:.1%}）')
        
        # 计算分数
        score = 1.0
        if not has_heading and len(sections) > 1:
            score -= 0.3
        if max_consecutive_empty > 5:
            score -= 0.2
        if total_chars == 0:
            score = 0.0
        
        return {
            'passed': len(issues) == 0,
            'score': max(0.0, min(1.0, score)),
            'issues': issues
        }
    
    def _stage_1_heuristic_scoring(self, sections: List[dict]) -> float:
        """
        Stage 1: 启发式打分。
        
        评分维度：
        - 标题层级完整性（30%）
        - 内容长度合理性（30%）
        - 表格/图片存在性（20%）
        - 元数据完整性（20%）
        """
        if not sections:
            return 0.0
        
        score = 0.0
        
        # 1. 标题层级完整性 (30%)
        heading_score = self._score_headings(sections)
        score += heading_score * 0.3
        
        # 2. 内容长度合理性 (30%)
        length_score = self._score_content_length(sections)
        score += length_score * 0.3
        
        # 3. 表格/图片存在性 (20%)
        media_score = self._score_media_presence(sections)
        score += media_score * 0.2
        
        # 4. 元数据完整性 (20%)
        meta_score = self._score_metadata_completeness(sections)
        score += meta_score * 0.2
        
        return max(0.0, min(1.0, score))
    
    def _score_headings(self, sections: List[dict]) -> float:
        """
        评分标题层级完整性。
        
        Returns:
            0-1 分数
        """
        if not sections:
            return 0.0
        
        has_level_1 = any(s.get('heading_level') == 1 for s in sections)
        has_multiple_levels = len(set(s.get('heading_level', 1) for s in sections)) > 1
        
        score = 0.5  # 基础分
        if has_level_1:
            score += 0.2
        if has_multiple_levels:
            score += 0.3
        
        return min(1.0, score)
    
    def _score_content_length(self, sections: List[dict]) -> float:
        """
        评分内容长度合理性。
        
        Returns:
            0-1 分数
        """
        if not sections:
            return 0.0
        
        total_chars = sum(len(s.get('content', '')) for s in sections)
        avg_chars = total_chars / len(sections)
        
        # 理想每节 100-5000 字符
        if 100 <= avg_chars <= 5000:
            return 1.0
        elif avg_chars < 10:
            return 0.3  # 太短，可能提取失败
        else:
            return 0.7  # 较长但可接受
    
    def _score_media_presence(self, sections: List[dict]) -> float:
        """
        评分表格/图片存在性。
        
        Returns:
            0-1 分数
        """
        has_tables = any('tables' in s.get('metadata', {}) for s in sections)
        has_images = any('images' in s.get('metadata', {}) for s in sections)
        
        if has_tables or has_images:
            return 0.8
        else:
            return 1.0  # 没有表格/图片也是正常的
    
    def _score_metadata_completeness(self, sections: List[dict]) -> float:
        """
        评分元数据完整性。
        
        Returns:
            0-1 分数
        """
        if not sections:
            return 0.0
        
        complete_count = sum(
            1 for s in sections
            if all(k in s.get('metadata', {}) for k in ['source', 'page_range'])
        )
        
        return complete_count / len(sections)


# ==================== 树状 MD 生成器 ====================

class TreeGenerator:
    """
    将转换结果写入树状 MD 文件结构。
    
    输出示例:
        WikiSearch/docs/
        └── document_name/
            ├── _index.md              ← 全文摘要 + 章节导航
            ├── 01_概述.md             ← 第一章
            ├── 02_市场分析.md         ← 第二章
            │   ├── 02_01_行业趋势.md  ← 长章节分页
            │   └── 02_02_竞品分析.md
            └── assets/                ← 提取的图片/图表
    """
    
    def __init__(self, base_dir: str):
        """
        Args:
            base_dir: 基础输出目录（如 WikiSearch/docs/）
        """
        self.base_dir = Path(base_dir)
        os.makedirs(self.base_dir, exist_ok=True)
    
    def generate_tree(self, result: ConversionResult) -> List[str]:
        """
        生成树状 MD 文件结构。
        
        Args:
            result: ConversionResult
            
        Returns:
            生成的 MD 文件路径列表
        """
        doc_name = Path(result.file_path).stem
        doc_dir = self.base_dir / doc_name
        os.makedirs(doc_dir, exist_ok=True)
        
        md_files = []
        
        # 1. 生成 _index.md（全文摘要 + 章节导航）
        index_path = doc_dir / "_index.md"
        self._write_index_md(result=result, index_path=str(index_path))
        md_files.append(str(index_path))
        
        # 2. 按层级组织文件
        for section in result.sections:
            filename = self._sanitize_filename(section['title'])
            heading_level = section.get('heading_level', 1)
            
            if heading_level == 1:
                # 顶级章节 → 直接放在根目录
                filepath = doc_dir / f"{filename}.md"
            else:
                # 子章节 → 创建子目录（简化：按父章节分组）
                parent_title = section.get('parent_title', '')
                if parent_title:
                    parent_dir = doc_dir / self._sanitize_filename(parent_title)
                    os.makedirs(parent_dir, exist_ok=True)
                    filepath = parent_dir / f"{filename}.md"
                else:
                    filepath = doc_dir / f"{filename}.md"
            
            # 3. 写入 MD 文件
            self._write_section_md(
                section=section,
                doc_name=doc_name,
                output_path=str(filepath)
            )
            md_files.append(str(filepath))
        
        return md_files
    
    def _write_index_md(self, result: ConversionResult, index_path: str):
        """
        生成 _index.md（全文摘要 + 章节导航）。
        
        Args:
            result: ConversionResult
            index_path: 输出路径
        """
        lines = [
            f"# {Path(result.file_path).stem}",
            "",
            "## 📑 章节导航",
            ""
        ]
        
        # 按层级生成导航链接
        for section in result.sections:
            indent = "  " * (section.get('heading_level', 1) - 1)
            rel_path = '../' * (section.get('heading_level', 1) - 1) if section.get('heading_level', 1) > 1 else ''
            filename = self._sanitize_filename(section['title']) + '.md'
            lines.append(f"{indent}- [{section['title']}]({rel_path}{filename})")
        
        # 添加统计信息
        lines.extend([
            "",
            "## 📊 文档统计",
            f"- **总页数**: {result.total_pages}",
            f"- **转换方式**: {result.conversion_method.upper()}",
            f"- **章节数**: {len(result.sections)}",
        ])
        
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
    
    def _write_section_md(self, section: dict, doc_name: str, output_path: str):
        """
        生成单个章节的 MD 文件。
        
        Args:
            section: 章节数据（统一格式）
            doc_name: 文档名称
            output_path: 输出路径
        """
        # YAML Frontmatter
        frontmatter = [
            f"---",
            f"source: \"{doc_name}\"",
            f"chapter: \"{section['title']}\"",
            f"level: L{section.get('heading_level', 1)}",
        ]
        
        if section.get('parent_title'):
            frontmatter.append(f"parent: \"{section['parent_title']}\"")
        
        if 'page_range' in section:
            frontmatter.append(f"page_range: \"{section['page_range']}\"")
        
        tags = [section.get('conversion_method', 'unknown'), self._sanitize_filename(section['title'])]
        frontmatter.extend([
            f"tags: [{', '.join(tags)}]",
            "---",
            ""
        ])
        
        # 标题（H1-H5）
        heading_level = min(section.get('heading_level', 1), 5)
        heading_prefix = '#' * heading_level
        content_lines = [
            '\n'.join(frontmatter),
            f"{heading_prefix} {section['title']}",
            "",
            section.get('content', ''),
        ]
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(content_lines))
    
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


# ==================== 主转换管道 ====================

class ConversionPipeline:
    """
    Phase 4 转换管道主引擎。
    
    串联所有组件：Router → Converter → QualityChecker → TreeGenerator
    
    Attributes:
        db_path: SQLite 数据库路径（用于 file_registry）
        output_dir: Markdown 输出目录
    """
    
    def __init__(self, db_path: str = "./wiki_db", output_dir: Optional[str] = None):
        """
        Args:
            db_path: SQLite 数据库路径（用于 file_registry）
            output_dir: Markdown 输出目录（如果提供，会生成树状 MD 文件）
        """
        self.db_path = Path(db_path)
        self.output_dir = output_dir or str(self.db_path / "converted_md")
        os.makedirs(self.output_dir, exist_ok=True)
        
        # 初始化组件
        self.router = Router()
        self.quality_checker = QualityChecker()
        self.tree_generator = TreeGenerator(base_dir=self.output_dir)
    
    def convert(self, filepath: str) -> ConversionResult:
        """
        执行完整的转换流程。
        
        Args:
            filepath: 原始文件路径（.docx | .pdf）
            
        Returns:
            ConversionResult
        """
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"文件不存在: {filepath}")
        
        print(f"🔄 开始转换: {filepath.name}")
        
        # Step 1: 智能路由 + 转换
        format_type, converter_result = self.router.convert(str(filepath))
        if not format_type:
            raise ValueError(f"无法识别文件格式: {filepath}")
        
        print(f"   ✅ 格式识别: {format_type.upper()}")
        
        # Step 2: 统一章节格式
        sections = self._normalize_sections(converter_result, format_type)
        
        # Step 3: 质量自检
        quality_result = self.quality_checker.check(sections)
        print(f"   📊 质量评分: {quality_result.score:.2f} (Stage {quality_result.stage})")
        
        if quality_result.issues:
            for issue in quality_result.issues[:3]:  # 最多显示 3 个
                print(f"      ⚠️ {issue}")
        
        # Step 4: 生成树状 MD（如果提供了输出目录）
        md_files = []
        if self.output_dir:
            result_for_tree = ConversionResult(
                file_path=str(filepath),
                sections=sections,
                total_pages=converter_result.get('total_pages', 0),
                conversion_method=format_type,
                quality_score=quality_result.score
            )
            md_files = self.tree_generator.generate_tree(result_for_tree)
            print(f"   📁 生成 {len(md_files)} 个 MD 文件")
        
        # Step 5: 构建最终结果
        conversion_result = ConversionResult(
            file_path=str(filepath),
            sections=sections,
            total_pages=converter_result.get('total_pages', 0),
            conversion_method=format_type,
            quality_score=quality_result.score,
            md_files_created=md_files,
            metadata={
                'quality_stage': quality_result.stage,
                'needs_llm_fix': quality_result.needs_llm_fix,
            }
        )
        
        print(f"✅ 转换完成: {filepath.name} → {len(sections)} 个章节")
        return conversion_result
    
    def _normalize_sections(self, converter_result: dict, format_type: str) -> List[dict]:
        """
        将不同转换器的结果统一为标准化格式。
        
        Args:
            converter_result: 转换器返回的结果
            format_type: 'docx' | 'pdf'
            
        Returns:
            [section_dict, ...]（统一格式）
        """
        sections = []
        
        if format_type == 'docx':
            for section in converter_result.get('sections', []):
                sections.append({
                    'title': section.title,
                    'content': section.content,
                    'heading_level': section.heading_level,
                    'parent_title': section.parent_title,
                    'page_range': '1',  # DOCX 无页码，占位为 1
                    'conversion_method': 'docx',
                    'metadata': {**section.metadata, 'page_range': '1'},
                })
        
        elif format_type == 'pdf':
            for section in converter_result.get('sections', []):
                page_start = section.page_range[0] + 1 if section.page_range else 0
                page_end = section.page_range[1] + 1 if section.page_range else 0
                
                sections.append({
                    'title': section.title,
                    'content': section.content,
                    'heading_level': section.heading_level,
                    'parent_title': section.parent_title,
                    'page_range': f"{page_start}-{page_end}" if page_start != page_end else str(page_start),
                    'conversion_method': 'pdf',
                    'metadata': section.metadata,
                })
        
        return sections
    
    def convert_and_index(self, filepath: str, wiki_index=None) -> ConversionResult:
        """
        转换并自动索引到 WikiIndex。
        
        Args:
            filepath: 原始文件路径
            wiki_index: WikiIndex 实例（如果提供，会自动建立索引）
            
        Returns:
            ConversionResult
        """
        result = self.convert(filepath)
        
        if wiki_index and result.md_files_created:
            print(f"   📥 开始索引 {len(result.md_files_created)} 个 MD 文件...")
            for md_file in result.md_files_created:
                try:
                    wiki_index.add_document(md_file)
                except Exception as e:
                    print(f"      ⚠️ 索引失败: {md_file} - {e}")
        
        return result
