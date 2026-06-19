"""
WikiIndex — 混合检索核心类

三层架构：
1. BM25倒排索引 (Whoosh) — 关键词匹配 + 标签/日期过滤
2. TurboVec向量索引 (IdMapIndex) — 语义相似度 + allowlist内核过滤
3. DocStore文档存储 — 原始文本回溯

所有层共享同一套 uint64 ID 空间。
"""

import os
import json
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any

import numpy as np
from turbovec import IdMapIndex
from whoosh.index import create_in, open_dir
from whoosh.fields import Schema, ID, TEXT, DATETIME, STORED, KEYWORD
from whoosh.qparser import QueryParser
from whoosh.analysis import StandardAnalyzer

# 中文分词器（jieba）
try:
    import jieba
    from whoosh.analysis import Tokenizer, Token

    class JiebaTokenizer(Tokenizer):
        """基于 jieba 的中文分词器"""
        def __call__(self, text, posmap=None, group=False, origin=False, mode='default', **kwargs):
            t = Token()
            for i, word in enumerate(jieba.cut(text, cut_all=False)):
                if len(word.strip()) == 0:
                    continue
                t.position = i
                setattr(t, 'pos', i)  # Whoosh 内部用 t.pos 访问
                t.text = word
                yield t.copy()

    JIEBA_ANALYZER = JiebaTokenizer()
except ImportError:
    from whoosh.analysis import StandardAnalyzer
    JIEBA_ANALYZER = StandardAnalyzer()

from .chunker import MarkdownChunker, ChunkStrategy
from .embedder import Embedder
from .docstore import DocStore
from .file_scanner import FileScanner, generate_file_id
from .file_registry import FileRegistry


class WikiIndex:
    """
    企业级Wiki索引核心类。
    
    使用示例：
        idx = WikiIndex(db_path="./wiki_db")
        doc_id = idx.add_document("./docs/company_info.md")
        results = idx.search("格瑞迪斯石油营收", filters={"department": "油服"}, k=10)
    """
    
    def __init__(self, db_path: str = "./wiki_db", dim: Optional[int] = None, bit_width: int = 4):
        """
        初始化Wiki索引。
        
        Args:
            db_path: 数据库存储路径
            dim: 向量维度（None=自动检测，qwen3-embedding:8b为4096维）
            bit_width: TurboVec压缩位数（2/3/4，默认4-bit = 16x压缩）
        """
        self.db_path = Path(db_path)
        self.dim = dim
        self.bit_width = bit_width
        
        # ID生成器：自增uint64计数器
        self._id_counter: int = 0
        
        # file_id → uint64 ID 映射（用于追踪文件对应的 chunk IDs）
        self._file_to_chunks: Dict[str, List[int]] = {}
        
        # 初始化各层组件
        self._init_bm25_index()
        self._init_vec_index()
        self._init_chunker()
        self._init_embedder()
        self._init_registry()
        self._init_docstore()
        self._restore_state()  # 恢复 ID 计数器和 file_to_chunks 映射
    
    def _init_bm25_index(self):
        """初始化BM25倒排索引（Whoosh + jieba 中文分词）"""
        schema = Schema(
            id=ID(stored=True, unique=True),
            title=TEXT(analyzer=JIEBA_ANALYZER, stored=True),
            content=TEXT(analyzer=JIEBA_ANALYZER, stored=True),
            department=KEYWORD(stored=True, lowercase=True),
            date=DATETIME(stored=True),
            chunk_type=ID(stored=True),      # "L1" or "L2"
            parent_id=ID(stored=True),       # L2的parent是L1的id
            file_id=STORED,                  # UUID5 文件 ID（关联到 FileRegistry）
            chunk_order=STORED,              # 在所属层级中的序号
            total_chunks=STORED,             # 文档总chunk数
        )
        
        if not self.db_path.exists():
            os.makedirs(self.db_path, exist_ok=True)
        
        # 确保 index 子目录存在（Whoosh create_in 要求目标目录已存在）
        bm25_dir = self.db_path / "index"
        os.makedirs(bm25_dir, exist_ok=True)
        
        if list(bm25_dir.iterdir()):
            self.bm25_index = open_dir(str(bm25_dir), schema=schema)
        else:
            self.bm25_index = create_in(str(bm25_dir), schema=schema)
    
    def _init_vec_index(self):
        """初始化TurboVec向量索引（IdMapIndex）"""
        # dim=None 表示惰性模式，第一次add时自动推断
        self.vec_index = IdMapIndex(dim=self.dim, bit_width=self.bit_width)
        
        # 加载已有索引（如果存在）
        index_path = self.db_path / "vec_index.tvim"
        if index_path.exists():
            self.vec_index = IdMapIndex.load(str(index_path))
            # 恢复ID计数器：从 DocStore 或注册表获取最大 ID
            # （TurboVec 加载后没有 slot_to_id 属性）
            self._id_counter = len(self.vec_index)  # 保守估计
    
    def _init_chunker(self):
        """初始化Markdown切片器"""
        self.chunker = MarkdownChunker(
            strategy=ChunkStrategy.HIERARCHICAL,
            l1_max_size=600,   # 章节级最大字数
            l2_max_size=300,   # 段落级最大字数
        )
    
    def _init_embedder(self):
        """初始化Embedding模型（qwen3-embedding:8b via Ollama，4096维）"""
        self.embedder = Embedder(model_name="qwen3-embedding:8b")
    
    def _init_registry(self):
        """初始化文件注册表（SQLite 持久化）"""
        registry_path = self.db_path / "file_registry.db"
        self.registry = FileRegistry(db_path=str(registry_path))
    
    def _init_docstore(self):
        """初始化 DocStore（SQLite 持久化文档存储）"""
        docstore_path = self.db_path / "docstore.db"
        self._doc_store = DocStore(db_path=str(docstore_path))
    
    def _restore_state(self):
        """恢复 ID 计数器和 file_to_chunks 映射（从注册表加载）"""
        # 1. 恢复 ID 计数器（用最大 chunk_id + 1）
        self._id_counter = self.registry.get_max_chunk_id()
        
        # 2. 恢复 file_to_chunks 映射（从注册表批量加载）
        conn = self.registry._get_connection()
        try:
            rows = conn.execute("""
                SELECT DISTINCT file_id FROM chunk_mapping
            """).fetchall()
            
            for row in rows:
                file_id = row['file_id']
                self._file_to_chunks[file_id] = self.registry.get_chunk_ids(file_id)
        finally:
            conn.close()
    
    # ==================== 核心接口 ====================
    
    def add_document(self, filepath: str) -> int:
        """
        添加文档到索引。
        
        Args:
            filepath: Markdown文件路径
        
        Returns:
            doc_id: 文档的uint64 ID（实际返回int，可安全转为np.uint64）
        
        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 切片后无有效内容
        """
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"文档不存在: {filepath}")
        
        # 1. 读取Markdown内容
        content = filepath.read_text(encoding="utf-8")
        filename = filepath.name
        
        # 2. 提取元数据（从文件名或文件头）
        metadata = self._extract_metadata(filepath, content)
        
        # 3. 分层切片 (L1章节 + L2段落)
        chunks = self.chunker.chunk(content, filename=filename)
        if not chunks:
            raise ValueError(f"文档切片后无有效内容: {filepath}")
        
        # 4. 为每个chunk分配ID并写入各层（L2 → L1 父子联动）
        chunk_ids = []
        current_l1_id = None  # 记录当前 L1 chunk 的 ID
        
        for chunk in chunks:
            doc_id = self._allocate_id()
            chunk_ids.append(doc_id)
            
            if chunk.chunk_type == "L1":
                # L1 chunk：更新当前 L1 ID，parent_id 为空
                current_l1_id = doc_id
                parent_id_str = ""
            else:
                # L2 chunk：关联到最近的 L1
                parent_id_str = str(current_l1_id) if current_l1_id else ""
            
            # 构建元数据
            chunk_meta = {
                **metadata,
                "chunk_type": chunk.chunk_type,  # "L1" or "L2"
                "parent_id": parent_id_str,
                "chunk_order": chunk.order,
                "total_chunks": len(chunks),
            }
            
            # 写入BM25索引
            self._write_to_bm25(doc_id, chunk.title, chunk.content, chunk_meta)
            
            # 生成embedding并写入TurboVec
            embedding = self.embedder.embed(chunk.content)
            if embedding is not None:
                self.vec_index.add_with_ids(
                    np.array([embedding], dtype=np.float32),
                    np.array([doc_id], dtype=np.uint64)
                )
            
            # 写入DocStore
            self._doc_store[doc_id] = {
                "content": chunk.content,
                "title": chunk.title,
                "metadata": chunk_meta,
                "filepath": str(filepath),
            }
        
        # 5. 保存TurboVec索引
        self.vec_index.write(str(self.db_path / "vec_index.tvim"))
        
        # 始终返回第一个chunk的ID作为文档代表ID（保持API一致性）
        return chunk_ids[0]
    
    def search(self, query_text: str, filters: Optional[Dict[str, Any]] = None, k: int = 10) -> List[Tuple[int, float]]:
        """
        混合检索入口。
        
        Args:
            query_text: 用户查询文本
            filters: {"department": "油服", "date_gte": "2024-01-01"}
            k: 返回结果数量
        
        Returns:
            [(doc_id, score), ...] Top-K排序结果（score越大越相关）
        """
        # Step 1: BM25 + 标签过滤 → 候选ID集（最多max_candidates个）
        max_candidates = min(k * 10, 100)  # 动态调整，但不超过100
        candidate_ids = self._bm25_search(query_text, filters, max_candidates)
        
        if len(candidate_ids) == 0:
            return []
        
        # Step 2: 向量检索在候选集内重排（allowlist内核级过滤）
        query_vector = self.embedder.embed(query_text)
        if query_vector is None:
            return []
        
        scores, ranked_ids = self.vec_index.search(
            np.array([query_vector], dtype=np.float32),
            k=min(k, len(candidate_ids)),
            allowlist=candidate_ids
        )
        
        # 转换为 (id, score) 列表（距离→相似度：score = 1 - distance）
        results = []
        for rid, dist in zip(ranked_ids[0], scores[0]):
            similarity = 1.0 - float(dist)  # 距离转相似度
            results.append((int(rid), similarity))
        
        # 按相似度降序排列（越大越相关）
        results.sort(key=lambda x: x[1], reverse=True)
        
        return results[:k]
    
    def get_content(self, doc_id: int) -> Optional[dict]:
        """
        通过ID回溯原始文档内容。
        
        Args:
            doc_id: 文档ID
        
        Returns:
            {content, title, metadata} 或 None
        """
        return self._doc_store.get(doc_id)
    
    def get_parent_chunk(self, child_id: int) -> Optional[dict]:
        """
        通过L2 chunk ID回溯到L1章节上下文。
        
        Args:
            child_id: L2 chunk的ID
        
        Returns:
            {content, title, metadata} 或 None（如果本身就是L1）
        """
        doc_info = self._doc_store.get(child_id)
        if not doc_info:
            return None
        
        # 如果已经是L1，直接返回
        if doc_info["metadata"].get("chunk_type") == "L1":
            return doc_info
        
        # L2 → 查找parent_id对应的L1 chunk
        parent_id_str = doc_info["metadata"].get("parent_id", "")
        if not parent_id_str:
            return None
        
        parent_id = int(parent_id_str)
        return self._doc_store.get(parent_id)
    
    def search_with_context(self, query_text: str, filters: Optional[Dict[str, Any]] = None, k: int = 10) -> List[dict]:
        """
        增强版搜索：返回结构化结果（文件路径 + 完整段落 + 章节上下文）。
        
        Args:
            query_text: 用户查询文本
            filters: {"department": "油服", "date_gte": "2024-01-01"}
            k: 返回结果数量
        
        Returns:
            [{
                "id": doc_id,
                "score": float,
                "file_path": "/path/to/file.md",
                "file_name": "file.md",
                "chunk_type": "L1" or "L2",
                "matched_content": "匹配的完整段落内容",
                "section_title": "章节标题",
                "parent_context": "父章节上下文（仅L2有）",
                "parent_title": "父章节标题（仅L2有）"
            }, ...]
        """
        # Step 1: 标准搜索获取 Top-K
        results = self.search(query_text, filters=filters, k=k)
        
        enriched_results = []
        for doc_id, score in results:
            doc_info = self._doc_store.get(doc_id)
            if not doc_info:
                continue
            
            chunk_type = doc_info["metadata"].get("chunk_type", "L1")
            file_path = doc_info.get("filepath", "")
            file_name = os.path.basename(file_path) if file_path else ""
            
            # Step 2: 根据 chunk 类型组装上下文
            if chunk_type == "L2":
                # L2：获取 L1 父节点作为上下文
                parent = self.get_parent_chunk(doc_id)
                result = {
                    "id": doc_id,
                    "score": float(score),
                    "file_path": file_path,
                    "file_name": file_name,
                    "chunk_type": "L2",
                    "matched_content": doc_info["content"],      # L2 完整段落
                    "section_title": doc_info["title"],          # L2 段落标题
                    "parent_context": parent["content"] if parent else "",   # L1 章节内容
                    "parent_title": parent["title"] if parent else "",       # L1 章节标题
                }
            else:
                # L1：自身就是完整上下文
                result = {
                    "id": doc_id,
                    "score": float(score),
                    "file_path": file_path,
                    "file_name": file_name,
                    "chunk_type": "L1",
                    "matched_content": doc_info["content"],      # L1 完整章节
                    "section_title": doc_info["title"],          # L1 章节标题
                    "parent_context": "",
                    "parent_title": "",
                }
            
            enriched_results.append(result)
        
        return enriched_results
    
    def format_search_result(self, result: dict) -> str:
        """
        格式化单个搜索结果（用于终端/日志输出）。
        
        Args:
            result: search_with_context() 返回的单条结果
            
        Returns:
            格式化的字符串
        """
        lines = []
        lines.append(f"📄 {result['file_name']} ({result['chunk_type']})")
        lines.append(f"   分数: {result['score']:.3f}")
        
        if result['chunk_type'] == 'L2' and result.get('parent_title'):
            lines.append(f"   章节: {result['parent_title']}")
        
        lines.append(f"   标题: {result['section_title']}")
        lines.append(f"   内容预览: {result['matched_content'][:100]}...")
        
        return "\n".join(lines)
    
    def print_search_results(self, query_text: str, k: int = 5) -> List[dict]:
        """
        搜索并打印格式化结果（便捷方法）。
        
        Args:
            query_text: 查询文本
            k: 返回数量
            
        Returns:
            搜索结果列表
        """
        results = self.search_with_context(query_text, k=k)
        
        if not results:
            print(f"🔍 \"{query_text}\" → 未找到匹配结果")
            return results
        
        print(f"\n🔍 \"{query_text}\" → {len(results)} 条结果\n")
        for i, result in enumerate(results, 1):
            print(f"[{i}] {self.format_search_result(result)}")
            if i < len(results):
                print()
        
        return results
    
    def verify_consistency(self) -> bool:
        """
        验证三层ID一致性。以 DocStore 为基准，检查 BM25 和 TurboVec 是否包含所有 ID。
        
        Returns:
            True: 所有层ID完全对齐
            False: 存在不一致
        """
        store_ids = set(self._doc_store.keys())
        
        # 收集 BM25 中的业务 ID
        bm25_ids = set()
        with self.bm25_index.reader() as reader:
            for internal_id in reader.all_doc_ids():
                fields = reader.stored_fields(internal_id)
                if fields and "id" in fields:
                    bm25_ids.add(int(fields["id"]))
        
        # TurboVec 没有遍历 ID 的接口，用 contains() 抽样验证
        vec_missing = [did for did in store_ids if not self.vec_index.contains(did)]
        
        if bm25_ids != store_ids:
            print(f"⚠️ BM25 vs DocStore ID不一致")
            print(f"  BM25独有: {bm25_ids - store_ids}")
            print(f"  DocStore独有: {store_ids - bm25_ids}")
            return False
        
        if vec_missing:
            print(f"⚠️ TurboVec 缺少以下ID: {vec_missing[:5]}...")
            return False
        
        # 检查数量是否匹配（TurboVec 不应有多余的向量）
        if len(self.vec_index) != len(store_ids):
            print(f"⚠️ TurboVec 向量数({len(self.vec_index)}) vs DocStore({len(store_ids)})不匹配")
            return False
        
        return True
    
    def remove_document(self, doc_id: int) -> bool:
        """
        从所有层删除文档。
        
        Args:
            doc_id: 要删除的文档ID
        
        Returns:
            True: 成功删除
            False: ID不存在
        """
        if doc_id not in self._doc_store:
            return False
        
        # 从DocStore删除
        del self._doc_store[doc_id]
        
        # 从TurboVec删除（IdMapIndex支持O(1)按ID删除）
        removed = self.vec_index.remove(doc_id)
        if not removed:
            print(f"⚠️ TurboVec中未找到ID: {doc_id}")
        
        # 从BM25删除（Whoosh需要重建索引，原型阶段简化处理）
        with self.bm25_index.reader() as reader:
            for internal_id in reader.all_doc_ids():
                fields = reader.stored_fields(internal_id)
                if int(fields["id"]) == doc_id:
                    writer = self.bm25_index.writer()
                    writer.delete_by_term("id", str(doc_id))
                    writer.commit()
                    break
        
        # 保存TurboVec索引
        self.vec_index.write(str(self.db_path / "vec_index.tvim"))
        
        return True
    
    # ==================== 内部方法 ====================
    
    def _allocate_id(self) -> int:
        """分配新的uint64 ID"""
        self._id_counter += 1
        return self._id_counter
    
    def _extract_metadata(self, filepath: Path, content: str) -> dict:
        """
        从文件名和文件头提取元数据。
        
        Args:
            filepath: 文件路径
            content: 文件内容
        
        Returns:
            {department, date, ...}
        """
        from datetime import datetime
        metadata = {}
        
        # 从文件名提取日期（YYYYMMDD格式）→ 转 datetime 对象
        filename = filepath.stem
        import re
        date_match = re.search(r'(\d{8})', filename)
        if date_match:
            date_str = date_match.group(1)
            try:
                metadata["date"] = datetime.strptime(date_str, "%Y%m%d")
            except ValueError:
                pass  # 日期格式不对就跳过
        
        # 从文件名提取部门（如果文件名包含部门信息）
        if "_" in filename:
            parts = filename.split("_")
            if len(parts) > 1:
                metadata["department"] = parts[0]
        
        return metadata
    
    def _write_to_bm25(self, doc_id: int, title: str, content: str, metadata: dict):
        """
        写入BM25倒排索引。
        """
        with self.bm25_index.searcher() as searcher:
            writer = self.bm25_index.writer()
            
            doc_dict = {
                "id": str(doc_id),
                "title": title,
                "content": content,
            }
            doc_dict.update(metadata)
            
            writer.add_document(**doc_dict)
            writer.commit()
    
    def _bm25_search(self, query_text: str, filters: Optional[Dict[str, Any]] = None, max_candidates: int = 100) -> np.ndarray:
        """
        BM25搜索 + 标签过滤 → 候选ID集。
        
        Args:
            query_text: 查询文本
            filters: {"department": "油服", "date_gte": "2024-01-01"}
            max_candidates: 最大候选数量
        
        Returns:
            np.uint64数组: 候选ID列表
        """
        schema = self.bm25_index.schema
        query_parser = QueryParser("content", schema=schema)
        query = query_parser.parse(query_text)
        
        candidate_ids = []
        with self.bm25_index.searcher() as searcher:
            results = searcher.search(query, limit=max_candidates)
            
            for hit in results:
                doc_id = int(hit["id"])
                
                # 应用标签过滤
                if filters and not self._apply_filters(hit, filters):
                    continue
                
                candidate_ids.append(doc_id)
        
        return np.array(candidate_ids, dtype=np.uint64) if candidate_ids else np.array([], dtype=np.uint64)
    
    def _apply_filters(self, hit: dict, filters: Dict[str, Any]) -> bool:
        """
        应用标签过滤条件。
        
        Args:
            hit: Whoosh搜索结果
            filters: {"department": "油服", "date_gte": "2024-01-01"}
        
        Returns:
            True: 通过过滤
            False: 被过滤掉
        """
        for key, value in filters.items():
            if key == "department":
                # 部门匹配（Whoosh KEYWORD字段支持）
                doc_dept = hit.get("department", "")
                if doc_dept != value:
                    return False
            
            elif key == "date_gte":
                # 日期 >= 指定值
                doc_date = hit.get("date", "")
                if doc_date < value:
                    return False
            
            elif key == "date_lte":
                # 日期 <= 指定值
                doc_date = hit.get("date", "")
                if doc_date > value:
                    return False
        
        return True
    
    def stats(self) -> dict:
        """
        获取索引统计信息。
        
        Returns:
            {total_docs, total_chunks, compression_ratio, ...}
        """
        bm25_count = 0
        with self.bm25_index.reader() as reader:
            bm25_count = reader.doc_count_all()
        
        vec_count = len(self.vec_index)
        store_count = len(self._doc_store)
        
        # 计算压缩率（假设原始float32）
        actual_dim = self.vec_index.dim  # 从 TurboVec 实例获取实际维度
        original_size = vec_count * actual_dim * 4  # float32 = 4 bytes
        compressed_size = vec_count * (actual_dim / 8) * self.bit_width  # bit-packed
        compression_ratio = original_size / compressed_size if compressed_size > 0 else 0
        
        return {
            "bm25_chunks": bm25_count,
            "vec_vectors": vec_count,
            "store_docs": store_count,
            "compression_ratio": f"{compression_ratio:.1f}x",
            "id_counter": self._id_counter,
        }
    
    # ==================== 增量同步接口 ====================
    
    def sync_from_config(self, config: dict) -> dict:
        """
        从配置文件扫描目录并增量索引。
        
        Args:
            config: {
                "watch_dirs": ["/path/to/docs1", "/path/to/docs2"],
                "supported_extensions": [".md", ".docx", ".pdf"],
                "ignore_patterns": ["__pycache__", "*.tmp"]
            }
            
        Returns:
            {added: N, modified: N, deleted: N, errors: [...]}
        """
        watch_dirs = config.get("watch_dirs", [])
        supported_extensions = config.get("supported_extensions", [".md"])
        ignore_patterns = config.get("ignore_patterns", [])
        
        # 1. FileScanner 扫描目录
        scanner = FileScanner(
            supported_extensions=supported_extensions,
            ignore_patterns=ignore_patterns
        )
        current_scan = scanner.scan_directories(watch_dirs)
        
        # 2. 获取注册表中的 Hash 映射
        previous_registry = self.registry.get_all_hashes()
        
        # 3. 增量变化检测
        changes = scanner.get_incremental_changes(previous_registry, current_scan)
        
        added_count = 0
        modified_count = 0
        deleted_count = 0
        errors = []
        
        # 4. 先批量注册扫描结果（新增/修改的文件设为 pending）
        self.registry.batch_register(current_scan)
        
        # 5a. 处理新增文件
        for file_path in changes['added']:
            try:
                chunk_ids = self._process_file(file_path, current_scan[file_path])
                if chunk_ids:
                    added_count += 1
            except Exception as e:
                errors.append({"file": file_path, "error": str(e)})
        
        # 5b. 处理修改文件
        for file_path in changes['modified']:
            try:
                # 先删除旧索引
                old_record = self.registry.get_by_path(file_path)
                if old_record:
                    file_id = old_record['file_id']
                    chunk_ids = self._remove_file_by_id(file_id)
                
                # 重新索引
                new_chunk_ids = self._process_file(file_path, current_scan[file_path])
                if new_chunk_ids:
                    modified_count += 1
            except Exception as e:
                errors.append({"file": file_path, "error": str(e)})
        
        # 5c. 处理删除文件
        for file_path in changes['deleted']:
            try:
                record = self.registry.get_by_path(file_path)
                if record:
                    self._remove_file_by_id(record['file_id'])
                    deleted_count += 1
            except Exception as e:
                errors.append({"file": file_path, "error": str(e)})
        
        # 6. 统一保存 TurboVec 索引（所有文件处理完后只写一次磁盘）
        self.vec_index.write(str(self.db_path / "vec_index.tvim"))
        
        return {
            'added': added_count,
            'modified': modified_count,
            'deleted': deleted_count,
            'errors': errors
        }
    
    def _process_file(self, file_path: str, content_hash: str) -> List[int]:
        """
        处理单个文件：转换 → 切片 → Embedding → 入库（批量写入优化版）。
        
        Args:
            file_path: 文件路径
            content_hash: SHA256 Hash
            
        Returns:
            chunk IDs 列表
        """
        # 1. 生成稳定 file_id
        file_id = generate_file_id(file_path)
        
        # 2. 读取 Markdown 内容（初期只支持 .md，后续加转换器）
        content = Path(file_path).read_text(encoding="utf-8")
        filename = os.path.basename(file_path)
        
        # 3. 分层切片
        chunks = self.chunker.chunk(content, filename=filename)
        if not chunks:
            return []
        
        # 4. 预分配所有 chunk IDs
        chunk_ids = [self._allocate_id() for _ in chunks]
        
        # 5. 构建元数据（L1/L2 父子联动）
        metadata_base = self._extract_metadata(Path(file_path), content)
        current_l1_id = None
        
        chunk_metas = []
        for i, chunk in enumerate(chunks):
            if chunk.chunk_type == "L1":
                current_l1_id = chunk_ids[i]
                parent_id_str = ""
            else:
                parent_id_str = str(current_l1_id) if current_l1_id else ""
            
            chunk_metas.append({
                **metadata_base,
                "chunk_type": chunk.chunk_type,
                "parent_id": parent_id_str,
                "chunk_order": chunk.order,
                "total_chunks": len(chunks),
                "file_id": file_id,
            })
        
        # 6. 批量生成 Embedding（Ollama batch API）
        embeddings = self.embedder.embed_batch([c.content for c in chunks])
        if embeddings is None:
            embeddings = [self.embedder.embed(c.content) for c in chunks]
        
        # 7a. 批量写入 BM25（一次性 commit）
        writer = self.bm25_index.writer()
        for i, (chunk, doc_id) in enumerate(zip(chunks, chunk_ids)):
            doc_dict = {
                "id": str(doc_id),
                "title": chunk.title,
                "content": chunk.content,
            }
            doc_dict.update(chunk_metas[i])
            writer.add_document(**doc_dict)
        writer.commit()
        
        # 7b. 批量写入 TurboVec（一次性 add）
        valid_embeddings = []
        valid_ids = []
        for i, (emb, doc_id) in enumerate(zip(embeddings, chunk_ids)):
            if emb is not None:
                # L2 归一化（提升余弦相似度精度）
                norm = np.linalg.norm(emb)
                if norm > 0:
                    emb = emb / norm
                valid_embeddings.append(emb)
                valid_ids.append(doc_id)
        
        if valid_embeddings:
            self.vec_index.add_with_ids(
                np.array(valid_embeddings, dtype=np.float32),
                np.array(valid_ids, dtype=np.uint64)
            )
        
        # 7c. 批量写入 DocStore
        for i, (chunk, doc_id) in enumerate(zip(chunks, chunk_ids)):
            self._doc_store[doc_id] = {
                "content": chunk.content,
                "title": chunk.title,
                "metadata": chunk_metas[i],
                "filepath": file_path,
            }
        
        # 8. 更新内存和注册表：file_id → chunk_ids 映射（持久化）
        self._file_to_chunks[file_id] = chunk_ids
        self.registry.save_chunk_mapping(file_id, chunk_ids)
        
        # 9. 标记索引完成
        self.registry.mark_indexed(file_path, chunk_count=len(chunk_ids))
        
        return chunk_ids
    
    def _remove_file_by_id(self, file_id: str) -> List[int]:
        """
        通过 file_id 删除文件的所有 chunks。
        
        Args:
            file_id: UUID5 生成的文件 ID
            
        Returns:
            被删除的 chunk IDs 列表
        """
        chunk_ids = self._file_to_chunks.get(file_id, [])
        
        if not chunk_ids:
            return []
        
        # 批量从三层删除
        for doc_id in chunk_ids:
            # DocStore
            del self._doc_store[doc_id]
            
            # TurboVec
            self.vec_index.remove(doc_id)
            
            # BM25（直接 delete_by_term，不用遍历）
            writer = self.bm25_index.writer()
            writer.delete_by_term("id", str(doc_id))
        
        # 一次性 commit BM25
        writer.commit()
        
        # 清理注册表映射
        self.registry.delete_chunk_mapping(file_id)
        
        # 清理内存映射
        if file_id in self._file_to_chunks:
            del self._file_to_chunks[file_id]
        
        return chunk_ids
    
    def registry_stats(self) -> dict:
        """获取文件注册表统计信息"""
        return self.registry.stats()
