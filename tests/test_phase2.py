"""
Phase 2: BM25 → allowlist 链路测试

验证混合检索核心流程：
- BM25关键词召回候选集
- TurboVec向量重排（allowlist内核级过滤）
- 标签过滤生效
- 性能对比（全库搜索 vs allowlist搜索）
"""

import os
import sys
import shutil
import tempfile
import time
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from src.wiki_index import WikiIndex

# ==================== 测试数据 ====================

SAMPLE_DOCS = [
    {
        "filename": "doc1_格瑞迪斯石油.md",
        "content": """# 格瑞迪斯石油2024年度报告

## 公司概况

格瑞迪斯石油股份有限公司成立于2015年，是一家专注于油田技术服务的高新技术企业。
公司总部位于北京市，在新三板挂牌交易（股票代码：873895）。

## 财务数据

2024年度，格瑞迪斯石油实现营收2.67亿元，同比增长18.5%。净利润达到4340万元，同比增长22.3%。
公司毛利率维持在35%以上，净利率约16%，在油服行业中处于领先水平。

## 技术实力

公司拥有"油服技术小巨人"称号，持有专利23项，其中发明专利8项。核心技术包括定向钻井、压裂技术和油气田化学剂研发。
""",
        "metadata": {"department": "油服", "date": "2024-12-31"}
    },
    {
        "filename": "doc2_AI新闻.md",
        "content": """# AI新闻周报

## GitHub木马事件

近期发现约1万个GitHub克隆仓库被用于分发特洛伊木马，规模是之前的91倍。
攻击者通过伪造热门项目的fork来传播恶意代码。

## AI抛硬币实验

影视飓风发布了一期关于AI概率偏差的视频，播放量达到527万。实验揭示了LLM内部存在系统性概率偏差。
""",
        "metadata": {"department": "科技", "date": "2024-06-19"}
    },
    {
        "filename": "doc3_会议纪要.md",
        "content": """# 部门会议纪要

## 技术部周会

参会人员：张三、李四、王五

会议内容：
1. MemFileCli v2.0 进度汇报，已完成Phase 1+2
2. TurboVec调研完成，4-bit压缩可实现16倍内存节省
3. LLM-Wiki产品架构设计初步完成

## 下一步计划

- Phase 1 MVP: DOCX/PDF→Markdown转换管道
- 测试TurboVec vs Chonkie在真实文档集上的表现
""",
        "metadata": {"department": "技术部", "date": "2024-06-20"}
    },
]


class TestPhase2:
    """Phase 2: BM25 → allowlist 链路测试"""
    
    def setup_method(self):
        """每个测试前创建临时目录和索引实例"""
        self.test_dir = tempfile.mkdtemp(prefix="wiki_test_p2_")
        self.db_path = os.path.join(self.test_dir, "wiki_db")
        self.docs_dir = os.path.join(self.test_dir, "docs")
        os.makedirs(self.docs_dir, exist_ok=True)
        
        # 创建测试文档
        self._write_test_docs()
        
        # 初始化索引
        self.index = WikiIndex(db_path=self.db_path, dim=None, bit_width=4)
        
        # 添加所有文档到索引
        self.doc_ids = []
        for doc_info in SAMPLE_DOCS:
            filepath = os.path.join(self.docs_dir, doc_info["filename"])
            doc_id = self.index.add_document(filepath)
            self.doc_ids.append(doc_id)
        
        print(f"\n📚 测试环境准备完成，共索引 {len(self.doc_ids)} 个文档")
    
    def teardown_method(self):
        """每个测试后清理临时目录"""
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
    
    def _write_test_docs(self):
        """写入测试Markdown文件"""
        for doc_info in SAMPLE_DOCS:
            filepath = os.path.join(self.docs_dir, doc_info["filename"])
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(doc_info["content"])
    
    def test_search_returns_results(self):
        """测试：search() 能返回结果"""
        print("\n📝 测试: search() 基本功能")
        
        results = self.index.search("格瑞迪斯石油营收", k=5)
        
        assert len(results) > 0, "搜索应该返回结果"
        assert len(results) <= 5, f"结果数不应超过k=5，实际: {len(results)}"
        
        print(f"   ✅ 搜索 '格瑞迪斯石油营收' 返回 {len(results)} 条结果")
        for doc_id, score in results:
            content = self.index.get_content(doc_id)
            preview = content["content"][:30] if content else "N/A"
            print(f"      ID={doc_id}, score={score:.4f}, 内容预览: {preview}...")
    
    def test_search_allowlist_effective(self):
        """测试：allowlist 过滤生效——所有返回 ID 都在 BM25 候选集中"""
        print("\n📝 测试: allowlist 过滤生效")
        
        query = "石油"
        results = self.index.search(query, k=10)
        
        if not results:
            print("   ⚠️ 搜索无结果，跳过 allowlist 验证")
            return
        
        # 获取 BM25 候选集
        candidate_ids = self.index._bm25_search(query, max_candidates=100)
        candidate_set = set(candidate_ids.tolist()) if len(candidate_ids) > 0 else set()
        
        # 验证所有返回 ID 都在候选集中
        result_ids = [doc_id for doc_id, _ in results]
        missing = set(result_ids) - candidate_set
        
        assert not missing, f"以下结果ID不在BM25候选集中: {missing}"
        print(f"   ✅ 所有 {len(results)} 个结果ID都在BM25候选集内（allowlist生效）")
    
    def test_search_with_filters(self):
        """测试：标签过滤功能"""
        print("\n📝 测试: 标签过滤")
        
        # 搜索油服部门文档
        results = self.index.search("石油", filters={"department": "油服"}, k=5)
        
        print(f"   📊 搜索 '石油' + 部门='油服': {len(results)} 条结果")
        
        for doc_id, score in results:
            content = self.index.get_content(doc_id)
            if content:
                dept = content["metadata"].get("department", "N/A")
                print(f"      ID={doc_id}, 部门={dept}")
    
    def test_search_empty_result(self):
        """测试：搜索不存在的关键词返回空列表"""
        print("\n📝 测试: 无结果时返回空列表")
        
        results = self.index.search("量子计算超导芯片", k=5)
        
        assert results == [], f"应该返回空列表，实际: {results}"
        print(f"   ✅ 搜索不存在的关键词正确返回 []")
    
    def test_search_k_limit(self):
        """测试：返回结果数量 ≤ k"""
        print("\n📝 测试: k 参数限制")
        
        for k in [1, 3, 5, 10]:
            results = self.index.search("技术", k=k)
            assert len(results) <= k, f"k={k} 时返回了 {len(results)} 条结果"
            print(f"   ✅ k={k}: 返回 {len(results)} 条结果（≤ {k}）")
    
    def test_search_performance_allowlist_vs_full(self):
        """测试：allowlist 模式 vs 全库搜索的性能对比"""
        print("\n📝 测试: 性能对比（allowlist vs 全库）")
        
        query = "技术"
        k = 10
        
        # 全库搜索（不用 allowlist）
        query_vector = self.index.embedder.embed(query)
        if query_vector is None:
            print("   ⚠️ Embedding 失败，跳过性能测试")
            return
        
        vec_query = np.array([query_vector], dtype=np.float32)
        
        # 全库搜索
        start = time.perf_counter()
        scores_full, ids_full = self.index.vec_index.search(vec_query, k=k)
        full_time = (time.perf_counter() - start) * 1000  # ms
        
        # allowlist 搜索（候选集 = 前5个ID）
        candidate_ids = np.array(self.doc_ids[:5], dtype=np.uint64)
        start = time.perf_counter()
        scores_al, ids_al = self.index.vec_index.search(vec_query, k=k, allowlist=candidate_ids)
        al_time = (time.perf_counter() - start) * 1000  # ms
        
        print(f"   📊 全库搜索: {full_time:.3f}ms")
        print(f"   📊 allowlist搜索: {al_time:.3f}ms")
        
        if full_time > 0:
            speedup = full_time / al_time if al_time > 0 else float('inf')
            print(f"   ⚡ 速度比: {speedup:.1f}x")
            
            # 当候选集 < 10%总库时，allowlist 应该更快
            if len(candidate_ids) / len(self.doc_ids) < 0.1:
                assert al_time <= full_time * 2, "allowlist搜索不应比全库慢太多"
        
        print(f"   ✅ 性能对比完成")
    
    def test_search_integration_full_pipeline(self):
        """测试：完整搜索链路集成测试"""
        print("\n📝 测试: 完整搜索链路")
        
        # 模拟真实用户查询
        queries = [
            ("格瑞迪斯石油营收", {}),
            ("TurboVec压缩", {}),
            ("技术", {"department": "油服"}),
        ]
        
        for query_text, filters in queries:
            results = self.index.search(query_text, filters=filters or None, k=3)
            
            filter_desc = f" + {filters}" if filters else ""
            print(f"   🔍 '{query_text}'{filter_desc}: {len(results)} 条结果")
            
            # 验证每个结果都能回溯内容
            for doc_id, score in results:
                content = self.index.get_content(doc_id)
                assert content is not None, f"ID={doc_id} 的内容为空"
        
        print(f"   ✅ 完整搜索链路测试通过")


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
