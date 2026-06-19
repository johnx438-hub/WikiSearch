"""
SQLite 文件注册表 — 持久化文件 Hash 和 ID 映射

核心功能：
1. 存储文件路径 → file_id → content_hash 的映射关系
2. 记录索引状态（indexed/pending/error）和切片数量
3. 支持增量查询：找出需要更新的文件
4. 提供统计信息：总文件数、各状态分布等

表结构：
    file_registry (
        file_path TEXT PRIMARY KEY,     -- 文件绝对路径
        file_id TEXT UNIQUE,            -- UUID5，同路径永远相同
        content_hash TEXT,              -- SHA256 内容指纹
        chunk_count INTEGER,            -- 切片数量（L1+L2）
        status TEXT,                    -- 'indexed' / 'pending' / 'error'
        indexed_at DATETIME,            -- 最后索引时间
        error_message TEXT              -- 错误信息（如果有）
    )

使用示例：
    registry = FileRegistry(db_path="./wiki_db/file_registry.db")
    
    # 注册新文件
    registry.register("/path/to/doc.md", "uuid-001", "sha256:abc123...")
    
    # 批量更新（增量同步后）
    registry.batch_update(scan_result, changes)
    
    # 查询需要重新索引的文件
    pending = registry.get_pending_files()
"""

import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple


class FileRegistry:
    """
    SQLite 文件注册表，持久化文件 Hash 和 ID 映射。
    
    Attributes:
        db_path: SQLite 数据库文件路径
    """
    
    def __init__(self, db_path: str):
        """
        Args:
            db_path: SQLite 数据库文件路径（如 "./wiki_db/file_registry.db"）
        """
        self.db_path = db_path
        
        # 确保目录存在
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        
        # 初始化数据库
        self._init_db()
    
    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # 启用 WAL 模式提升并发性能
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
    
    def _init_db(self):
        """初始化数据库表结构"""
        conn = self._get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS file_registry (
                    file_path TEXT PRIMARY KEY,
                    file_id TEXT UNIQUE,
                    content_hash TEXT NOT NULL,
                    chunk_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    indexed_at DATETIME,
                    error_message TEXT
                )
            """)
            
            # 创建索引加速查询
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_status 
                ON file_registry(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_content_hash 
                ON file_registry(content_hash)
            """)
            
            conn.commit()
        finally:
            conn.close()
    
    # ==================== 核心接口 ====================
    
    def register(self, 
                 file_path: str, 
                 file_id: str, 
                 content_hash: str,
                 chunk_count: int = 0) -> bool:
        """
        注册新文件或更新已有文件记录。
        
        Args:
            file_path: 文件绝对路径
            file_id: UUID5 生成的稳定 ID
            content_hash: SHA256 内容指纹
            chunk_count: 切片数量（索引完成后更新）
            
        Returns:
            True: 成功注册/更新
        """
        conn = self._get_connection()
        try:
            conn.execute("""
                INSERT INTO file_registry 
                    (file_path, file_id, content_hash, chunk_count, status, indexed_at)
                VALUES (?, ?, ?, ?, 'pending', NULL)
                ON CONFLICT(file_path) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    status = 'pending',
                    indexed_at = NULL,
                    error_message = NULL
            """, (file_path, file_id, content_hash, chunk_count))
            
            conn.commit()
            return True
        except Exception as e:
            print(f"❌ 注册失败 {file_path}: {e}")
            return False
        finally:
            conn.close()
    
    def batch_register(self, scan_result: Dict[str, str]) -> int:
        """
        批量注册扫描结果（用于首次同步或增量更新）。
        
        Args:
            scan_result: {file_path: content_hash} 字典
            
        Returns:
            成功注册的文件数量
        """
        from .file_scanner import generate_file_id
        
        conn = self._get_connection()
        try:
            count = 0
            for file_path, content_hash in scan_result.items():
                file_id = generate_file_id(file_path)
                
                conn.execute("""
                    INSERT INTO file_registry 
                        (file_path, file_id, content_hash, chunk_count, status, indexed_at)
                    VALUES (?, ?, ?, 0, 'pending', NULL)
                    ON CONFLICT(file_path) DO UPDATE SET
                        content_hash = excluded.content_hash,
                        status = 'pending',
                        indexed_at = NULL,
                        error_message = NULL
                """, (file_path, file_id, content_hash))
                
                count += 1
            
            conn.commit()
            return count
        except Exception as e:
            print(f"❌ 批量注册失败: {e}")
            return 0
        finally:
            conn.close()
    
    def mark_indexed(self, file_path: str, chunk_count: int) -> bool:
        """
        标记文件索引完成。
        
        Args:
            file_path: 文件路径
            chunk_count: 实际切片数量
            
        Returns:
            True: 成功更新
        """
        conn = self._get_connection()
        try:
            result = conn.execute("""
                UPDATE file_registry 
                SET status = 'indexed', 
                    chunk_count = ?, 
                    indexed_at = ?,
                    error_message = NULL
                WHERE file_path = ?
            """, (chunk_count, datetime.now().isoformat(), file_path))
            
            conn.commit()
            return result.rowcount > 0
        except Exception as e:
            print(f"❌ 标记索引完成失败 {file_path}: {e}")
            return False
        finally:
            conn.close()
    
    def mark_error(self, file_path: str, error_message: str) -> bool:
        """
        标记文件索引失败。
        
        Args:
            file_path: 文件路径
            error_message: 错误信息
            
        Returns:
            True: 成功更新
        """
        conn = self._get_connection()
        try:
            conn.execute("""
                UPDATE file_registry 
                SET status = 'error', 
                    indexed_at = ?,
                    error_message = ?
                WHERE file_path = ?
            """, (datetime.now().isoformat(), error_message, file_path))
            
            conn.commit()
            return True
        except Exception as e:
            print(f"❌ 标记错误失败 {file_path}: {e}")
            return False
        finally:
            conn.close()
    
    def get_by_path(self, file_path: str) -> Optional[dict]:
        """
        通过文件路径查询记录。
        
        Args:
            file_path: 文件绝对路径
            
        Returns:
            {file_path, file_id, content_hash, chunk_count, status, indexed_at} 或 None
        """
        conn = self._get_connection()
        try:
            row = conn.execute("""
                SELECT * FROM file_registry WHERE file_path = ?
            """, (file_path,)).fetchone()
            
            if row:
                return dict(row)
            return None
        finally:
            conn.close()
    
    def get_pending_files(self) -> List[dict]:
        """
        获取待索引的文件列表。
        
        Returns:
            [{file_path, file_id, content_hash}, ...]
        """
        conn = self._get_connection()
        try:
            rows = conn.execute("""
                SELECT file_path, file_id, content_hash 
                FROM file_registry 
                WHERE status = 'pending'
                ORDER BY file_path
            """).fetchall()
            
            return [dict(row) for row in rows]
        finally:
            conn.close()
    
    def get_all_hashes(self) -> Dict[str, str]:
        """
        获取所有已注册文件的 {file_path: content_hash} 映射。
        
        Returns:
            {file_path: content_hash} 字典（用于增量比对）
        """
        conn = self._get_connection()
        try:
            rows = conn.execute("""
                SELECT file_path, content_hash 
                FROM file_registry
            """).fetchall()
            
            return {row['file_path']: row['content_hash'] for row in rows}
        finally:
            conn.close()
    
    def remove(self, file_path: str) -> bool:
        """
        从注册表删除文件记录。
        
        Args:
            file_path: 文件路径
            
        Returns:
            True: 成功删除
        """
        conn = self._get_connection()
        try:
            result = conn.execute("""
                DELETE FROM file_registry WHERE file_path = ?
            """, (file_path,))
            
            conn.commit()
            return result.rowcount > 0
        except Exception as e:
            print(f"❌ 删除失败 {file_path}: {e}")
            return False
        finally:
            conn.close()
    
    def batch_remove(self, file_paths: List[str]) -> int:
        """
        批量删除文件记录。
        
        Args:
            file_paths: 要删除的文件路径列表
            
        Returns:
            成功删除的数量
        """
        conn = self._get_connection()
        try:
            placeholders = ','.join(['?' for _ in file_paths])
            result = conn.execute(f"""
                DELETE FROM file_registry 
                WHERE file_path IN ({placeholders})
            """, file_paths)
            
            conn.commit()
            return result.rowcount
        except Exception as e:
            print(f"❌ 批量删除失败: {e}")
            return 0
        finally:
            conn.close()
    
    # ==================== 统计与查询 ====================
    
    def stats(self) -> dict:
        """
        获取注册表统计信息。
        
        Returns:
            {total_files, indexed_count, pending_count, error_count, total_chunks}
        """
        conn = self._get_connection()
        try:
            total = conn.execute("SELECT COUNT(*) as count FROM file_registry").fetchone()['count']
            
            indexed = conn.execute("""
                SELECT COUNT(*) as count FROM file_registry WHERE status = 'indexed'
            """).fetchone()['count']
            
            pending = conn.execute("""
                SELECT COUNT(*) as count FROM file_registry WHERE status = 'pending'
            """).fetchone()['count']
            
            error = conn.execute("""
                SELECT COUNT(*) as count FROM file_registry WHERE status = 'error'
            """).fetchone()['count']
            
            total_chunks = conn.execute("""
                SELECT COALESCE(SUM(chunk_count), 0) as total 
                FROM file_registry WHERE status = 'indexed'
            """).fetchone()['total']
            
            return {
                'total_files': total,
                'indexed_count': indexed,
                'pending_count': pending,
                'error_count': error,
                'total_chunks': total_chunks
            }
        finally:
            conn.close()
    
    def get_errors(self) -> List[dict]:
        """
        获取索引失败的文件列表。
        
        Returns:
            [{file_path, error_message, indexed_at}, ...]
        """
        conn = self._get_connection()
        try:
            rows = conn.execute("""
                SELECT file_path, error_message, indexed_at 
                FROM file_registry 
                WHERE status = 'error'
                ORDER BY indexed_at DESC
            """).fetchall()
            
            return [dict(row) for row in rows]
        finally:
            conn.close()
    
    def cleanup_deleted_files(self, current_scan: Dict[str, str]) -> List[str]:
        """
        清理已删除文件的记录（在注册表中但不在当前扫描结果中）。
        
        Args:
            current_scan: 当前扫描的 {file_path: content_hash} 字典
            
        Returns:
            被清理的文件路径列表
        """
        conn = self._get_connection()
        try:
            # 找出在注册表中但不在扫描结果中的文件
            registered = conn.execute("SELECT file_path FROM file_registry").fetchall()
            registered_paths = {row['file_path'] for row in registered}
            
            deleted_paths = list(registered_paths - set(current_scan.keys()))
            
            if deleted_paths:
                placeholders = ','.join(['?' for _ in deleted_paths])
                conn.execute(f"""
                    DELETE FROM file_registry WHERE file_path IN ({placeholders})
                """, deleted_paths)
                conn.commit()
            
            return sorted(deleted_paths)
        except Exception as e:
            print(f"❌ 清理失败: {e}")
            return []
        finally:
            conn.close()
