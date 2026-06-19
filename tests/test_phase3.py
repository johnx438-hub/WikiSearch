"""
Phase 3: 分层切片 + 质量验证测试

验证父子层级索引在实际检索中的效果：
- L2 chunk → L1 parent 回溯
- search_with_context() 返回 L2 片段 + L1 章节上下文
- 压缩率实测验证
"""

import os
import sys
import shutil
import tempfile
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from src.wiki_index import WikiIndex
from src.chunker import MarkdownChunker, ChunkStrategy

# ==================== 测试数据 ====================

SAMPLE_DOC_HIERARCHICAL = """# 格瑞迪斯石油2024年度报告

## 公司概况

格瑞迪斯石油股份有限公司成立于2015年，是一家专注于油田技术服务的高新技术企业。
公司总部位于北京市，在新三板挂牌交易（股票代码：873895）。

## 财务数据

2024年度，格瑞迪斯石油实现营收2.67亿元，同比增长18.5%。净利润达到4340万元，同比增长22.3%。
公司毛利率维持在35%以上，净利率约16%，在油服行业中处于领先水平。

## 技术实力

公司拥有"油服技术小巨人"称号，持有专利23项，其中发明专利8项。核心技术包括定向钻井、压裂技术和油气田化学剂研发。

## 上市计划

公司正在筹备北交所IPO，预计2025年完成申报。保荐机构为中信证券。
"""


class TestPhase3:
    """Phase 3: 分层切片 + 质量验证测试"""
    
    def setup_method(self):
        """每个测试前创建临时目录和索引实例"""
        self.test_dir = tempfile.mkdtemp(prefix="wiki_test_p3_")
        self.db_path = os.path.join(self.test_dir, "wiki_db")
        self.docs_dir = os.path.join(self.test_dir, "docs")
        os.makedirs(self.docs_dir, exist_ok=True)
        
        # 创建测试文档
        doc_path = os.path.join(self.docs_dir, "test_hierarchical.md")
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write(SAMPLE_DOC_HIERARCHICAL)
        
        self.doc_path = doc_path
        
        # 初始化索引
        self.index = WikiIndex(db_path=self.db_path, dim=None, bit_width=4)
    
    def teardown_method(self):
        """每个测试后清理临时目录"""
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
    
    def test_chunker_hierarchical_parent_id(self):
        """测试：分层切片器正确设置 parent_id"""
        print("\n📝 测试: chunker 的 parent_id 设置")
        
        chunker = MarkdownChunker(
            strategy=ChunkStrategy.HIERARCHICAL,
            l1_max_size=600,
            l2_max_size=300,
        )
        
        chunks = chunker.chunk(SAMPLE_DOC_HIERARCHICAL, filename="test.md")
        
        l1_chunks = [c for c in chunks if c.chunk_type == "L1"]
        l2_chunks = [c for c in chunks if c.chunk_type == "L2"]
        
        print(f"   📄 L1 chunks: {len(l1_chunks)}, L2 chunks: {len(l2_chunks)}")
        
        # 验证 L1 的 parent_id 是 None
        for i, chunk in enumerate(l1_chunks):
            assert chunk.parent_id is None, f"L1 chunk {i} 的 parent_id 应为 None"
        
        print(f"   ✅ L1 chunks 的 parent_id 均为 None")
    
    def test_add_document_parent_linkage(self):
        """测试：添加文档后 L2 → L1 的父子关系正确建立"""
        print("\n📝 测试: add_document 后的父子关系")
        
        doc_id = self.index.add_document(self.doc_path)
        
        # 查找所有 L2 chunk
        l2_ids = []
        for cid, info in self.index._doc_store.items():
            if info["metadata"].get("chunk_type") == "L2":
                l2_ids.append(cid)
        
        print(f"   📄 找到 {len(l2_ids)} 个 L2 chunk")
        
        # 验证每个 L2 都有有效的 parent_id
        for l2_id in l2_ids:
            info = self.index._doc_store[l2_id]
            parent_id_str = info["metadata"].get("parent_id", "")
            
            assert parent_id_str != "", f"L2 chunk {l2_id} 的 parent_id 为空"
            
            parent_id = int(parent_id_str)
            parent_info = self.index._doc_store.get(parent_id)
            
            assert parent_info is not None, f"L2 chunk {l2_id} 的父节点 {parent_id} 不存在"
            assert parent_info["metadata"].get("chunk_type") == "L1", \
                f"L2 chunk {l2_id} 的父节点 {parent_id} 不是 L1 类型"
            
            print(f"   ✅ L2({l2_id}) → L1({parent_id}): {parent_info['title'][:30]}...")
    
    def test_get_parent_chunk(self):
        """测试：get_parent_chunk() 正确回溯 L1 上下文"""
        print("\n📝 测试: get_parent_chunk() 回溯")
        
        self.index.add_document(self.doc_path)
        
        # 找一个 L2 chunk
        l2_id = None
        for cid, info in self.index._doc_store.items():
            if info["metadata"].get("chunk_type") == "L2":
                l2_id = cid
                break
        
        assert l2_id is not None, "没有找到 L2 chunk"
        
        # 获取父节点
        parent = self.index.get_parent_chunk(l2_id)
        
        assert parent is not None, f"L2({l2_id}) 的父节点为空"
        assert parent["metadata"].get("chunk_type") == "L1", "父节点应为 L1 类型"
        
        print(f"   ✅ get_parent_chunk({l2_id}) → L1: {parent['title']}")
        print(f"      内容长度: {len(parent['content'])} 字")
    
    def test_search_with_context(self):
        """测试：search_with_context() 返回 L2 + L1 上下文"""
        print("\n📝 测试: search_with_context()")
        
        self.index.add_document(self.doc_path)
        
        # 搜索应该触发 search_with_context
        results = self.index.search_with_context("格瑞迪斯石油营收", k=3)
        
        assert len(results) > 0, "search_with_context 应返回结果"
        
        for result in results:
            doc_id = result["id"]
            l2_content = result.get("l2_content", "")
            l1_context = result.get("l1_context", "")
            
            assert l2_content != "", f"ID={doc_id} 的 L2 内容为空"
            # l1_context 可能为空（如果本身就是 L1）
            
            print(f"   📄 ID={doc_id}:")
            print(f"      L2: {l2_content[:40]}...")
            if l1_context:
                print(f"      L1上下文: {l1_context[:40]}...")
        
        print(f"   ✅ search_with_context 返回 {len(results)} 条结果（含父子上下文）")
    
    def test_compression_ratio(self):
        """测试：TurboVec 压缩率实测验证"""
        print("\n📝 测试: 压缩率验证")
        
        self.index.add_document(self.doc_path)
        
        stats = self.index.stats()
        
        vec_count = stats["vec_vectors"]
        dim = self.index.vec_index.dim
        
        # 计算理论大小
        original_size_mb = (vec_count * dim * 4) / (1024 * 1024)  # float32
        compressed_size_mb = (vec_count * (dim / 8) * self.index.bit_width) / (1024 * 1024)  # 4-bit
        
        print(f"   📊 向量数: {vec_count}")
        print(f"   📊 维度: {dim}")
        print(f"   📊 float32 大小: {original_size_mb:.6f} MB")
        print(f"   📊 4-bit 压缩后: {compressed_size_mb:.6f} MB")
        print(f"   📊 压缩率: {stats['compression_ratio']}")
        
        # 验证压缩率接近理论值（16x for 4-bit）
        ratio = float(stats['compression_ratio'].replace('x', ''))
        assert 8.0 <= ratio <= 20.0, f"压缩率异常: {ratio}x"
        
        print(f"   ✅ 压缩率验证通过（{stats['compression_ratio']}）")
    
    def test_l1_chunk_returns_self(self):
        """测试：对 L1 chunk 调用 get_parent_chunk() 返回自身"""
        print("\n📝 测试: L1 chunk 的 get_parent_chunk()")
        
        self.index.add_document(self.doc_path)
        
        # 找一个 L1 chunk
        l1_id = None
        for cid, info in self.index._doc_store.items():
            if info["metadata"].get("chunk_type") == "L1":
                l1_id = cid
                break
        
        assert l1_id is not None, "没有找到 L1 chunk"
        
        # 对 L1 调用 get_parent_chunk 应返回自身
        parent = self.index.get_parent_chunk(l1_id)
        
        assert parent is not None, "L1 的父节点不应为空"
        assert parent["metadata"].get("chunk_type") == "L1", "应返回自身（L1）"
        
        print(f"   ✅ L1({l1_id}) 的 get_parent_chunk() 返回自身")


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
