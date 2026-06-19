"""
Phase 1: ID映射层测试

验证三层ID一致性：
- BM25倒排索引 (Whoosh)
- TurboVec向量索引 (IdMapIndex)
- DocStore文档存储

所有层共享同一套 uint64 ID 空间。
"""

import os
import sys
import shutil
import tempfile
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from turbovec import IdMapIndex

from src.wiki_index import WikiIndex
from src.chunker import MarkdownChunker, ChunkStrategy


# ==================== 测试数据 ====================

SAMPLE_DOC1 = """# 格瑞迪斯石油2024年度报告

## 公司概况

格瑞迪斯石油股份有限公司（简称"格瑞迪斯石油"）成立于2015年，是一家专注于油田技术服务的高新技术企业。
公司总部位于北京市，在新三板挂牌交易（股票代码：873895）。

## 财务数据

2024年度，格瑞迪斯石油实现营收2.67亿元，同比增长18.5%。净利润达到4340万元，同比增长22.3%。
公司毛利率维持在35%以上，净利率约16%，在油服行业中处于领先水平。

## 技术实力

公司拥有"油服技术小巨人"称号，持有专利23项，其中发明专利8项。核心技术包括定向钻井、压裂技术和油气田化学剂研发。

## 上市计划

公司正在筹备北交所IPO，预计2025年完成申报。保荐机构为中信证券。
"""

SAMPLE_DOC2 = """# AI新闻周报2024-06-19

## GitHub木马事件

近期发现约1万个GitHub克隆仓库被用于分发特洛伊木马，规模是之前的91倍。
攻击者通过伪造热门项目的fork来传播恶意代码。

## AI抛硬币实验

影视飓风发布了一期关于AI概率偏差的视频，播放量达到527万。实验揭示了LLM内部存在系统性概率偏差。
"""

SAMPLE_DOC3 = """# 部门会议纪要

## 技术部周会

参会人员：张三、李四、王五

会议内容：
1. MemFileCli v2.0 进度汇报，已完成Phase 1+2
2. TurboVec调研完成，4-bit压缩可实现16倍内存节省
3. LLM-Wiki产品架构设计初步完成

## 下一步计划

- Phase 1 MVP: DOCX/PDF→Markdown转换管道
- 测试TurboVec vs Chonkie在真实文档集上的表现
"""


class TestWikiIndex:
    """WikiIndex核心功能测试"""
    
    def setup_method(self):
        """每个测试前创建临时目录和索引实例"""
        self.test_dir = tempfile.mkdtemp(prefix="wiki_test_")
        self.db_path = os.path.join(self.test_dir, "wiki_db")
        self.docs_dir = os.path.join(self.test_dir, "docs")
        os.makedirs(self.docs_dir, exist_ok=True)
        
        # 创建测试文档
        self._write_test_docs()
        
        # 初始化索引（使用chromadb内置embedding，不需要Ollama）
        self.index = WikiIndex(db_path=self.db_path, dim=None, bit_width=4)
    
    def teardown_method(self):
        """每个测试后清理临时目录"""
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
    
    def _write_test_docs(self):
        """写入测试Markdown文件"""
        for i, content in enumerate([SAMPLE_DOC1, SAMPLE_DOC2, SAMPLE_DOC3], 1):
            filepath = os.path.join(self.docs_dir, f"doc{i}.md")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
    
    def test_add_document_returns_id(self):
        """测试：添加文档后返回有效ID"""
        print("\n📝 测试: add_document返回有效ID")
        
        filepath = os.path.join(self.docs_dir, "doc1.md")
        doc_id = self.index.add_document(filepath)
        
        # ID应该是正整数
        assert isinstance(doc_id, int), f"ID类型错误: {type(doc_id)}"
        assert doc_id > 0, f"ID应该为正数，实际: {doc_id}"
        print(f"   ✅ 文档添加成功，返回ID: {doc_id}")
    
    def test_add_multiple_documents(self):
        """测试：添加多个文档后各层ID一致"""
        print("\n📝 测试: 多文档添加")
        
        doc_ids = []
        for i in range(1, 4):
            filepath = os.path.join(self.docs_dir, f"doc{i}.md")
            doc_id = self.index.add_document(filepath)
            doc_ids.append(doc_id)
            print(f"   📄 doc{i}.md → ID: {doc_id}")
        
        # 验证ID唯一性
        assert len(set(doc_ids)) == len(doc_ids), "ID不唯一！"
        print(f"   ✅ 所有ID唯一: {doc_ids}")
    
    def test_verify_consistency(self):
        """测试：三层ID一致性校验"""
        print("\n📝 测试: ID一致性校验")
        
        # 添加文档
        for i in range(1, 4):
            filepath = os.path.join(self.docs_dir, f"doc{i}.md")
            self.index.add_document(filepath)
        
        # 验证一致性
        is_consistent = self.index.verify_consistency()
        assert is_consistent, "三层ID不一致！"
        print(f"   ✅ BM25 / TurboVec / DocStore ID完全对齐")
    
    def test_get_content(self):
        """测试：通过ID回溯文档内容"""
        print("\n📝 测试: get_content回溯文档")
        
        filepath = os.path.join(self.docs_dir, "doc1.md")
        doc_id = self.index.add_document(filepath)
        
        content = self.index.get_content(doc_id)
        assert content is not None, f"ID {doc_id} 的内容为空"
        assert "格瑞迪斯石油" in content["content"], "内容不匹配"
        print(f"   ✅ 成功回溯文档内容（{len(content['content'])}字）")
    
    def test_remove_document(self):
        """测试：删除文档后三层同步清理"""
        print("\n📝 测试: 删除文档")
        
        # 添加3个文档
        doc_ids = []
        for i in range(1, 4):
            filepath = os.path.join(self.docs_dir, f"doc{i}.md")
            doc_id = self.index.add_document(filepath)
            doc_ids.append(doc_id)
        
        # 验证一致性
        assert self.index.verify_consistency(), "删除前ID不一致"
        print(f"   ✅ 删除前: {self._count_bm25()} BM25 chunks, {len(self.index.vec_index)} TurboVec vectors")
        
        # 删除中间文档
        removed = self.index.remove_document(doc_ids[1])
        assert removed, f"ID {doc_ids[1]} 删除失败"
        print(f"   ✅ 成功删除ID: {doc_ids[1]}")
        
        # 验证一致性（删除后）
        is_consistent = self.index.verify_consistency()
        assert is_consistent, "删除后ID不一致！"
        print(f"   ✅ 删除后: {self._count_bm25()} BM25 chunks, {len(self.index.vec_index)} TurboVec vectors")
    
    def test_file_not_found(self):
        """测试：添加不存在的文件抛出异常"""
        print("\n📝 测试: 文件不存在异常")
        
        try:
            self.index.add_document("/nonexistent/path/doc.md")
            assert False, "应该抛出FileNotFoundError"
        except FileNotFoundError:
            print(f"   ✅ 正确抛出FileNotFoundError")
    
    def test_stats(self):
        """测试：索引统计信息"""
        print("\n📝 测试: 索引统计")
        
        # 添加文档
        for i in range(1, 4):
            filepath = os.path.join(self.docs_dir, f"doc{i}.md")
            self.index.add_document(filepath)
        
        stats = self.index.stats()
        print(f"   📊 统计信息:")
        for key, value in stats.items():
            print(f"      {key}: {value}")
        
        # 验证统计合理性
        assert stats["bm25_chunks"] > 0, "BM25 chunks应该>0"
        assert stats["vec_vectors"] == stats["bm25_chunks"], "向量数应与BM25 chunks一致"
        print(f"   ✅ 统计信息合理")
    
    def test_chunker_hierarchical(self):
        """测试：分层切片器（HIERARCHICAL模式）"""
        print("\n📝 测试: 分层切片器")
        
        chunker = MarkdownChunker(
            strategy=ChunkStrategy.HIERARCHICAL,
            l1_max_size=600,
            l2_max_size=300,
        )
        
        chunks = chunker.chunk(SAMPLE_DOC1, filename="test.md")
        
        # 应该有L1和L2两种chunk
        l1_chunks = [c for c in chunks if c.chunk_type == "L1"]
        l2_chunks = [c for c in chunks if c.chunk_type == "L2"]
        
        print(f"   📄 总chunks: {len(chunks)}")
        print(f"      L1 (章节): {len(l1_chunks)}")
        print(f"      L2 (段落): {len(l2_chunks)}")
        
        # 验证L1有标题
        for chunk in l1_chunks:
            assert "##" in chunk.title, f"L1标题应包含##: {chunk.title}"
        
        print(f"   ✅ 分层切片正确")
    
    def test_chunker_flat(self):
        """测试：扁平切片器（FLAT模式）"""
        print("\n📝 测试: 扁平切片器")
        
        chunker = MarkdownChunker(
            strategy=ChunkStrategy.FLAT,
            l1_max_size=300,
        )
        
        chunks = chunker.chunk(SAMPLE_DOC1, filename="test.md")
        
        # 扁平模式下所有chunk都是L1
        assert all(c.chunk_type == "L1" for c in chunks), "FLAT模式应全为L1"
        print(f"   📄 总chunks: {len(chunks)} (全部L1)")
        print(f"   ✅ 扁平切片正确")
    
    def _count_bm25(self):
        """辅助方法：统计BM25索引中的文档数"""
        from whoosh.index import open_dir
        from whoosh.fields import Schema, ID, TEXT, DATETIME, STORED, KEYWORD
        
        schema = Schema(
            id=ID(stored=True, unique=True),
            title=TEXT(stored=True),
            content=TEXT(stored=True),
            department=KEYWORD(stored=True, lowercase=True),
            date=DATETIME(stored=True),
            chunk_type=ID(stored=True),
            parent_id=ID(stored=True),
        )
        
        index_path = os.path.join(self.db_path, "index")
        if not os.path.exists(index_path):
            return 0
        
        ix = open_dir(index_path, schema=schema)
        with ix.reader() as reader:
            return reader.doc_count_all()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
