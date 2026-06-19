"""
Markdown分层切片器

支持三种策略：
- FLAT: 扁平切片（按字数切分，MemFileCli原有模式）
- HIERARCHICAL: 父子层级（L1章节 + L2段落，推荐企业版）
- ADAPTIVE: 自适应切片（按内容复杂度动态调整chunk大小）
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class ChunkStrategy(Enum):
    FLAT = "flat"
    HIERARCHICAL = "hierarchical"
    ADAPTIVE = "adaptive"


@dataclass
class Chunk:
    """切片单元"""
    content: str          # 切片内容
    title: str            # 标题（L1为章节标题，L2为空或继承父级）
    chunk_type: str       # "L1" (章节) or "L2" (段落)
    parent_id: Optional[int]   # L2的parent是L1的id
    order: int            # 在所属层级中的序号（从1开始）


class MarkdownChunker:
    """
    Markdown文档分层切片器。
    
    使用示例：
        chunker = MarkdownChunker(strategy=ChunkStrategy.HIERARCHICAL)
        chunks = chunker.chunk(markdown_content, filename="test.md")
        for chunk in chunks:
            print(f"[{chunk.chunk_type}] {chunk.title}: {chunk.content[:50]}...")
    """
    
    def __init__(self, strategy: ChunkStrategy = ChunkStrategy.HIERARCHICAL,
                 l1_max_size: int = 600, l2_max_size: int = 300):
        """
        Args:
            strategy: 切片策略
            l1_max_size: L1章节级最大字数（默认600）
            l2_max_size: L2段落级最大字数（默认300）
        """
        self.strategy = strategy
        self.l1_max_size = l1_max_size
        self.l2_max_size = l2_max_size
    
    def chunk(self, content: str, filename: str = "") -> List[Chunk]:
        """
        对Markdown文档进行切片。
        
        Args:
            content: Markdown文本内容
            filename: 文件名（用于提取标题）
        
        Returns:
            Chunk列表，按阅读顺序排列
        """
        if self.strategy == ChunkStrategy.HIERARCHICAL:
            return self._hierarchical_chunk(content, filename)
        elif self.strategy == ChunkStrategy.FLAT:
            return self._flat_chunk(content, filename)
        else:
            return self._adaptive_chunk(content, filename)
    
    def _hierarchical_chunk(self, content: str, filename: str) -> List[Chunk]:
        """
        分层切片：L1章节 + L2段落。
        
        流程：
        1. 按 ## 标题分割文档为章节
        2. 每个章节作为L1 chunk（如果超过l1_max_size则保留，不进一步切）
        3. 每个章节内的段落拆分为L2 chunks
        """
        chunks = []
        
        # 提取文档标题（第一个 ## 或文件名）
        doc_title = self._extract_doc_title(content, filename)
        
        # 按 ## 标题分割（支持 ## 到 ######，但只认 ## 作为章节边界）
        sections = re.split(r'(?=^## )', content, flags=re.MULTILINE)
        sections = [s.strip() for s in sections if s.strip()]
        
        # 如果没有找到任何 ## 标题，整个文档作为一个L1 chunk
        if not sections:
            chunks.append(Chunk(
                content=content,
                title=doc_title,
                chunk_type="L1",
                parent_id=None,
                order=1,
            ))
            return chunks
        
        l1_order = 0
        for section in sections:
            # 提取章节标题和内容
            heading_match = re.match(r'^(##[^\n]*)\n(.*)', section, re.DOTALL)
            if not heading_match:
                continue
            
            heading = heading_match.group(1).strip()
            body = heading_match.group(2).strip()
            
            l1_order += 1
            
            # L1 chunk: 标题 + 完整章节内容
            l1_content = f"{heading}\n\n{body}"
            chunks.append(Chunk(
                content=l1_content,
                title=heading,
                chunk_type="L1",
                parent_id=None,
                order=l1_order,
            ))
            
            # L2 chunks: 按段落分割
            paragraphs = self._split_paragraphs(body)
            if not paragraphs:
                continue
            
            l2_chunks = self._chunk_by_size(paragraphs, self.l2_max_size)
            for i, para in enumerate(l2_chunks):
                chunks.append(Chunk(
                    content=para,
                    title=f"{heading} [段落{i+1}]",
                    chunk_type="L2",
                    parent_id=None,  # 实际parent在WikiIndex中通过metadata记录
                    order=i + 1,
                ))
        
        return chunks
    
    def _flat_chunk(self, content: str, filename: str) -> List[Chunk]:
        """
        扁平切片：按字数切分（MemFileCli原有模式）。
        """
        doc_title = self._extract_doc_title(content, filename)
        paragraphs = self._split_paragraphs(content)
        chunks = self._chunk_by_size(paragraphs, self.l1_max_size)
        
        result = []
        for i, chunk in enumerate(chunks):
            result.append(Chunk(
                content=chunk,
                title=f"{doc_title} [切片{i+1}]",
                chunk_type="L1",  # 扁平模式下所有chunk都是L1
                parent_id=None,
                order=i + 1,
            ))
        
        return result
    
    def _adaptive_chunk(self, content: str, filename: str) -> List[Chunk]:
        """
        自适应切片：短内容用L2细粒度，长内容用L1粗粒度。
        """
        doc_title = self._extract_doc_title(content, filename)
        
        if len(content) <= self.l2_max_size:
            # 短文档：直接作为单个chunk
            return [Chunk(
                content=content,
                title=doc_title,
                chunk_type="L1",
                parent_id=None,
                order=1,
            )]
        
        # 长文档：按章节分割，每个章节内自适应调整
        sections = re.split(r'(?=^## )', content, flags=re.MULTILINE)
        sections = [s.strip() for s in sections if s.strip()]
        
        chunks = []
        order = 0
        for section in sections:
            heading_match = re.match(r'^(##[^\n]*)\n(.*)', section, re.DOTALL)
            if not heading_match:
                continue
            
            heading = heading_match.group(1).strip()
            body = heading_match.group(2).strip()
            order += 1
            
            # 如果章节内容很短，直接作为L2
            if len(body) <= self.l2_max_size:
                chunks.append(Chunk(
                    content=f"{heading}\n\n{body}",
                    title=heading,
                    chunk_type="L1",
                    parent_id=None,
                    order=order,
                ))
            else:
                # 长章节：拆分为L2段落
                paragraphs = self._split_paragraphs(body)
                l2_chunks = self._chunk_by_size(paragraphs, self.l2_max_size)
                for i, para in enumerate(l2_chunks):
                    chunks.append(Chunk(
                        content=para,
                        title=f"{heading} [段落{i+1}]",
                        chunk_type="L2",
                        parent_id=None,
                        order=i + 1,
                    ))
        
        return chunks
    
    def _split_paragraphs(self, text: str) -> List[str]:
        """
        按空行分割段落，过滤空白段落。
        """
        paragraphs = re.split(r'\n\s*\n', text)
        return [p.strip() for p in paragraphs if p.strip() and len(p.strip()) > 10]
    
    def _chunk_by_size(self, items: List[str], max_size: int) -> List[str]:
        """
        将段落列表按最大字数合并为chunks。
        
        Args:
            items: 段落列表
            max_size: 每个chunk的最大字数
        
        Returns:
            chunk列表
        """
        if not items:
            return []
        
        chunks = []
        current_chunk = []
        current_size = 0
        
        for item in items:
            item_size = len(item)
            
            # 如果单个段落超过max_size，直接切分（按字符）
            if item_size > max_size * 2:
                # 先添加当前累积的chunk
                if current_chunk:
                    chunks.append("\n\n".join(current_chunk))
                    current_chunk = []
                    current_size = 0
                
                # 按字符切分长段落
                chars = list(item)
                for i in range(0, len(chars), max_size):
                    chunk_text = "".join(chars[i:i+max_size])
                    chunks.append(chunk_text)
                continue
            
            # 如果当前chunk + 新段落超过限制，提交当前chunk并开始新的
            if current_size + item_size > max_size and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = [item]
                current_size = item_size
            else:
                current_chunk.append(item)
                current_size += item_size
        
        # 添加最后一个chunk
        if current_chunk:
            chunks.append("\n\n".join(current_chunk))
        
        return chunks
    
    def _extract_doc_title(self, content: str, filename: str) -> str:
        """
        提取文档标题。
        
        优先级：第一个 ## 标题 > 文件名（去扩展名）
        """
        # 尝试从内容中提取第一个 ## 标题
        heading_match = re.search(r'^## (.+)$', content, re.MULTILINE)
        if heading_match:
            return heading_match.group(1).strip()
        
        # 回退到文件名（去掉扩展名）
        import os
        base = os.path.splitext(filename)[0]
        # 尝试从文件名提取日期并格式化
        date_match = re.search(r'(\d{8})', base)
        if date_match:
            date_str = date_match.group(1)
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        
        return base
