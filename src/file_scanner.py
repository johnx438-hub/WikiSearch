"""
文件扫描器 — 目录遍历 + SHA256 Hash 计算 + 增量变化检测

核心功能：
1. 递归扫描指定目录，支持扩展名过滤
2. 计算文件 SHA256 Hash（大文件分块读取）
3. 增量变化检测：对比新旧扫描结果，识别新增/修改/删除的文件

使用示例：
    scanner = FileScanner(
        supported_extensions=['.md', '.docx', '.pdf'],
        ignore_patterns=['__pycache__', '*.tmp']
    )
    
    # 首次扫描
    current_scan = scanner.scan_directories(['/home/archer/docs'])
    
    # 增量检测（对比上次的注册表）
    changes = scanner.get_incremental_changes(previous_registry, current_scan)
    print(f"新增: {len(changes['added'])}")
    print(f"修改: {len(changes['modified'])}")
    print(f"删除: {len(changes['deleted'])}")
"""

import hashlib
import os
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


class FileScanner:
    """
    文件扫描器，支持目录遍历、Hash计算和增量变化检测。
    
    Attributes:
        supported_extensions: 支持的文件扩展名列表（如 ['.md', '.docx']）
        ignore_patterns: 忽略的文件/目录模式（如 ['__pycache__', '*.tmp']）
        chunk_size: Hash计算时的分块大小（默认8KB）
    """
    
    def __init__(self, 
                 supported_extensions: Optional[List[str]] = None,
                 ignore_patterns: Optional[List[str]] = None,
                 chunk_size: int = 8192):
        """
        Args:
            supported_extensions: 支持的文件扩展名（None=所有文件）
            ignore_patterns: 忽略模式列表（fnmatch格式）
            chunk_size: Hash分块大小（字节），默认8KB
        """
        self.supported_extensions = supported_extensions or []
        self.ignore_patterns = ignore_patterns or []
        self.chunk_size = chunk_size
    
    def scan_directories(self, directories: List[str]) -> Dict[str, str]:
        """
        递归扫描目录，返回 {文件路径: SHA256 Hash} 字典。
        
        Args:
            directories: 要扫描的目录路径列表
            
        Returns:
            {absolute_path: sha256_hash} 字典
        """
        result = {}
        
        for dir_path in directories:
            if not os.path.isdir(dir_path):
                print(f"⚠️ 目录不存在，跳过: {dir_path}")
                continue
            
            # 递归遍历目录
            for root, dirs, files in os.walk(dir_path):
                # 过滤忽略的目录（原地修改 dirs 阻止 os.walk 进入）
                dirs[:] = [d for d in dirs if not self._should_ignore(d)]
                
                for filename in files:
                    # 过滤忽略的文件
                    if self._should_ignore(filename):
                        continue
                    
                    # 过滤扩展名
                    if self.supported_extensions and not self._has_supported_extension(filename):
                        continue
                    
                    file_path = os.path.join(root, filename)
                    
                    # 跳过符号链接和特殊文件
                    if os.path.islink(file_path) or not os.path.isfile(file_path):
                        continue
                    
                    try:
                        file_hash = self.compute_file_hash(file_path)
                        result[file_path] = file_hash
                    except (PermissionError, OSError) as e:
                        print(f"⚠️ 无法读取文件，跳过: {file_path} ({e})")
        
        return result
    
    def compute_file_hash(self, file_path: str) -> str:
        """
        计算文件的 SHA256 Hash（大文件分块读取）。
        
        Args:
            file_path: 文件路径
            
        Returns:
            SHA256 Hex字符串
            
        Raises:
            FileNotFoundError: 文件不存在
            PermissionError: 无权限访问
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")
        
        sha256 = hashlib.sha256()
        
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(self.chunk_size)
                if not chunk:
                    break
                sha256.update(chunk)
        
        return sha256.hexdigest()
    
    def get_incremental_changes(self, 
                                previous_registry: Dict[str, str],
                                current_scan: Dict[str, str]) -> Dict[str, List[str]]:
        """
        对比新旧扫描结果，识别增量变化。
        
        Args:
            previous_registry: 上次的 {文件路径: Hash} 字典（来自 SQLite 注册表）
            current_scan: 本次扫描的 {文件路径: Hash} 字典
            
        Returns:
            {
                'added': [新增文件路径列表],
                'modified': [内容修改的文件路径列表],
                'deleted': [已删除的文件路径列表]
            }
        """
        previous_paths = set(previous_registry.keys())
        current_paths = set(current_scan.keys())
        
        # 新增：在当前扫描中但不在注册表中
        added = list(current_paths - previous_paths)
        
        # 删除：在注册表中但不在当前扫描中
        deleted = list(previous_paths - current_paths)
        
        # 修改：路径存在但 Hash 不同
        modified = []
        common_paths = previous_paths & current_paths
        for path in common_paths:
            if previous_registry[path] != current_scan[path]:
                modified.append(path)
        
        return {
            'added': sorted(added),
            'modified': sorted(modified),
            'deleted': sorted(deleted)
        }
    
    def _should_ignore(self, name: str) -> bool:
        """检查文件或目录名是否匹配忽略模式"""
        import fnmatch
        
        for pattern in self.ignore_patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False
    
    def _has_supported_extension(self, filename: str) -> bool:
        """检查文件是否有支持的扩展名"""
        _, ext = os.path.splitext(filename.lower())
        return ext in [e.lower() for e in self.supported_extensions]


def generate_file_id(file_path: str) -> str:
    """
    为文件生成稳定的 UUID（基于路径）。
    
    同一路径永远生成相同 UUID，方便追踪。
    
    Args:
        file_path: 文件绝对路径
        
    Returns:
        UUID5 字符串
    """
    import uuid
    
    # 使用 NAMESPACE_URL 作为命名空间，文件路径作为名称
    return str(uuid.uuid5(uuid.NAMESPACE_URL, os.path.abspath(file_path)))


def batch_compute_hashes(file_paths: List[str], chunk_size: int = 8192) -> Dict[str, str]:
    """
    批量计算文件 Hash（并发优化预留接口）。
    
    Args:
        file_paths: 文件路径列表
        chunk_size: Hash分块大小
        
    Returns:
        {文件路径: SHA256 Hash} 字典
    """
    scanner = FileScanner(chunk_size=chunk_size)
    result = {}
    
    for path in file_paths:
        try:
            result[path] = scanner.compute_file_hash(path)
        except Exception as e:
            print(f"⚠️ Hash计算失败: {path} ({e})")
    
    return result
