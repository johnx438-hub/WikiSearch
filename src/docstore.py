"""
DocStore — SQLite 持久化文档存储

替代内存 dict，支持跨会话持久化 chunk 内容、标题和元数据。

表结构：
    doc_store (
        doc_id INTEGER PRIMARY KEY,     -- uint64 ID
        title TEXT,                      -- 章节/段落标题
        content TEXT NOT NULL,           -- chunk 完整内容
        metadata TEXT,                   -- JSON 格式的元数据
        filepath TEXT                    -- 源文件路径
    )

使用示例：
    store = DocStore(db_path="./wiki_db/docstore.db")
    store.put(123, "标题", "内容...", {"key": "value"}, "/path/to/file.md")
    doc = store.get(123)
"""

import json
import os
import sqlite3
from typing import Dict, List, Optional


class DocStore:
    """
    SQLite 持久化文档存储。
    
    Attributes:
        db_path: SQLite 数据库文件路径
    """
    
    def __init__(self, db_path: str):
        """
        Args:
            db_path: SQLite 数据库文件路径
        """
        self.db_path = db_path
        
        # 确保目录存在
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        
        self._init_db()
    
    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn
    
    def _init_db(self):
        """初始化数据库表结构"""
        conn = self._get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS doc_store (
                    doc_id INTEGER PRIMARY KEY,
                    title TEXT,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    filepath TEXT
                )
            """)
            
            # 创建索引加速查询
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_filepath 
                ON doc_store(filepath)
            """)
            
            conn.commit()
        finally:
            conn.close()
    
    # ==================== dict 兼容接口 ====================
    
    def __getitem__(self, doc_id: int) -> dict:
        """store[doc_id]"""
        result = self.get(doc_id)
        if result is None:
            raise KeyError(f"doc_id {doc_id} not found")
        return result
    
    def __setitem__(self, doc_id: int, value: dict):
        """store[doc_id] = {...}"""
        self.put(
            doc_id=doc_id,
            title=value.get("title", ""),
            content=value.get("content", ""),
            metadata=value.get("metadata", {}),
            filepath=value.get("filepath", "")
        )
    
    def __contains__(self, doc_id: int) -> bool:
        """doc_id in store"""
        return self.has(doc_id)
    
    def __delitem__(self, doc_id: int):
        """del store[doc_id]"""
        self.delete(doc_id)
    
    def __len__(self) -> int:
        """len(store)"""
        return self.count()
    
    def keys(self) -> List[int]:
        """store.keys()"""
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT doc_id FROM doc_store").fetchall()
            return [row['doc_id'] for row in rows]
        finally:
            conn.close()
    
    def items(self) -> List[tuple]:
        """store.items() → [(doc_id, {content, title, metadata, filepath}), ...]"""
        result = []
        for doc_id in self.keys():
            item = self.get(doc_id)
            if item is not None:
                result.append((doc_id, item))
        return result
    
    # ==================== 核心接口 ====================
    
    def put(self, 
            doc_id: int, 
            title: str, 
            content: str, 
            metadata: Optional[dict] = None,
            filepath: str = "") -> bool:
        """
        存储 chunk。
        
        Args:
            doc_id: uint64 ID
            title: 章节/段落标题
            content: chunk 完整内容
            metadata: 元数据字典（自动转 JSON）
            filepath: 源文件路径
            
        Returns:
            True: 成功存储
        """
        from datetime import datetime
        
        conn = self._get_connection()
        try:
            # 处理 datetime 对象转字符串
            if metadata:
                for key, val in metadata.items():
                    if isinstance(val, datetime):
                        metadata[key] = val.isoformat()
            metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
            
            conn.execute("""
                INSERT OR REPLACE INTO doc_store 
                    (doc_id, title, content, metadata, filepath)
                VALUES (?, ?, ?, ?, ?)
            """, (doc_id, title, content, metadata_json, filepath))
            
            conn.commit()
            return True
        except Exception as e:
            print(f"❌ DocStore 存储失败 doc_id={doc_id}: {e}")
            return False
        finally:
            conn.close()
    
    def get(self, doc_id: int) -> Optional[dict]:
        """
        获取 chunk。
        
        Args:
            doc_id: uint64 ID
            
        Returns:
            {content, title, metadata, filepath} 或 None
        """
        conn = self._get_connection()
        try:
            row = conn.execute("""
                SELECT * FROM doc_store WHERE doc_id = ?
            """, (doc_id,)).fetchone()
            
            if not row:
                return None
            
            result = {
                "content": row["content"],
                "title": row["title"] or "",
                "filepath": row["filepath"] or "",
            }
            
            # 解析 metadata JSON
            if row["metadata"]:
                try:
                    result["metadata"] = json.loads(row["metadata"])
                except json.JSONDecodeError:
                    result["metadata"] = {}
            else:
                result["metadata"] = {}
            
            return result
        finally:
            conn.close()
    
    def delete(self, doc_id: int) -> bool:
        """
        删除 chunk。
        
        Args:
            doc_id: uint64 ID
            
        Returns:
            True: 成功删除
        """
        conn = self._get_connection()
        try:
            result = conn.execute("""
                DELETE FROM doc_store WHERE doc_id = ?
            """, (doc_id,))
            
            conn.commit()
            return result.rowcount > 0
        except Exception as e:
            print(f"❌ DocStore 删除失败 doc_id={doc_id}: {e}")
            return False
        finally:
            conn.close()
    
    def has(self, doc_id: int) -> bool:
        """检查 chunk 是否存在"""
        conn = self._get_connection()
        try:
            row = conn.execute("""
                SELECT COUNT(*) as count FROM doc_store WHERE doc_id = ?
            """, (doc_id,)).fetchone()
            return row['count'] > 0
        finally:
            conn.close()
    
    def batch_delete(self, doc_ids: List[int]) -> int:
        """
        批量删除 chunks。
        
        Args:
            doc_ids: ID 列表
            
        Returns:
            成功删除的数量
        """
        if not doc_ids:
            return 0
        
        conn = self._get_connection()
        try:
            placeholders = ','.join(['?' for _ in doc_ids])
            result = conn.execute(f"""
                DELETE FROM doc_store WHERE doc_id IN ({placeholders})
            """, doc_ids)
            
            conn.commit()
            return result.rowcount
        except Exception as e:
            print(f"❌ DocStore 批量删除失败: {e}")
            return 0
        finally:
            conn.close()
    
    def count(self) -> int:
        """获取存储的 chunk 总数"""
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT COUNT(*) as count FROM doc_store").fetchone()
            return row['count']
        finally:
            conn.close()
    
    def get_by_filepath(self, filepath: str) -> List[dict]:
        """
        通过文件路径获取所有 chunks。
        
        Args:
            filepath: 文件路径
            
        Returns:
            [{doc_id, title, content, metadata}, ...]
        """
        conn = self._get_connection()
        try:
            rows = conn.execute("""
                SELECT doc_id, title, content, metadata 
                FROM doc_store 
                WHERE filepath = ?
                ORDER BY doc_id
            """, (filepath,)).fetchall()
            
            results = []
            for row in rows:
                result = {
                    "doc_id": row["doc_id"],
                    "title": row["title"] or "",
                    "content": row["content"],
                }
                if row["metadata"]:
                    try:
                        result["metadata"] = json.loads(row["metadata"])
                    except json.JSONDecodeError:
                        result["metadata"] = {}
                else:
                    result["metadata"] = {}
                
                results.append(result)
            
            return results
        finally:
            conn.close()
    
    def clear(self):
        """清空所有数据"""
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM doc_store")
            conn.commit()
        finally:
            conn.close()
