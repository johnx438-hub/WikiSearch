"""
FileRegistry 模块测试

验证 SQLite 文件注册表的核心功能：
1. 单文件注册/更新
2. 批量注册扫描结果
3. 索引状态管理（pending → indexed/error）
4. 增量查询与清理
5. 统计信息准确性
"""

import os
import sys
import tempfile
import time
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.file_registry import FileRegistry


def setup_test_registry() -> tuple:
    """创建临时数据库和测试文件"""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_registry.db")
    
    registry = FileRegistry(db_path=db_path)
    
    # 创建测试文件
    test_files = {}
    for i in range(3):
        fp = os.path.join(temp_dir, f"doc{i}.md")
        with open(fp, 'w', encoding='utf-8') as f:
            f.write(f"# 文档{i}\n\n这是测试文档{i}的内容。")
        test_files[f"doc{i}.md"] = fp
    
    return registry, temp_dir, test_files


def cleanup_test(temp_dir: str):
    """清理临时目录"""
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_register_single_file():
    """测试1：单文件注册"""
    print("\n" + "="*60)
    print("测试1：单文件注册")
    print("="*60)
    
    registry, temp_dir, test_files = setup_test_registry()
    
    try:
        file_path = test_files["doc0.md"]
        
        # 首次注册
        result = registry.register(file_path, "uuid-001", "sha256:abc123")
        assert result == True, "注册应该成功"
        
        # 查询记录
        record = registry.get_by_path(file_path)
        assert record is not None, "应该能查到记录"
        assert record['file_id'] == "uuid-001", "file_id 应该匹配"
        assert record['content_hash'] == "sha256:abc123", "content_hash 应该匹配"
        assert record['status'] == 'pending', "初始状态应该是 pending"
        
        print(f"✅ 单文件注册成功")
        print(f"   file_id: {record['file_id']}")
        print(f"   status: {record['status']}")
        
    finally:
        cleanup_test(temp_dir)


def test_register_update():
    """测试2：更新已有文件"""
    print("\n" + "="*60)
    print("测试2：更新已有文件（Hash变化）")
    print("="*60)
    
    registry, temp_dir, test_files = setup_test_registry()
    
    try:
        file_path = test_files["doc0.md"]
        
        # 首次注册
        registry.register(file_path, "uuid-001", "sha256:old_hash")
        
        # 更新（模拟内容变化）
        registry.register(file_path, "uuid-001", "sha256:new_hash")
        
        record = registry.get_by_path(file_path)
        assert record['content_hash'] == "sha256:new_hash", "Hash应该被更新"
        assert record['status'] == 'pending', "状态应该重置为 pending"
        
        print(f"✅ 文件更新成功")
        print(f"   旧 Hash: sha256:old_hash → 新 Hash: {record['content_hash']}")
        
    finally:
        cleanup_test(temp_dir)


def test_batch_register():
    """测试3：批量注册"""
    print("\n" + "="*60)
    print("测试3：批量注册扫描结果")
    print("="*60)
    
    registry, temp_dir, test_files = setup_test_registry()
    
    try:
        scan_result = {fp: f"hash_{i}" for i, fp in enumerate(test_files.values())}
        
        count = registry.batch_register(scan_result)
        assert count == 3, f"应该注册3个文件，实际{count}个"
        
        # 验证所有文件都已注册
        stats = registry.stats()
        assert stats['total_files'] == 3, "总文件数应该是3"
        assert stats['pending_count'] == 3, "待索引数应该是3"
        
        print(f"✅ 批量注册 {count} 个文件")
        print(f"   统计: {stats}")
        
    finally:
        cleanup_test(temp_dir)


def test_mark_indexed():
    """测试4：标记索引完成"""
    print("\n" + "="*60)
    print("测试4：标记索引完成（pending → indexed）")
    print("="*60)
    
    registry, temp_dir, test_files = setup_test_registry()
    
    try:
        file_path = test_files["doc0.md"]
        
        # 注册后标记为待索引
        registry.register(file_path, "uuid-001", "sha256:abc")
        
        # 模拟索引完成
        result = registry.mark_indexed(file_path, chunk_count=5)
        assert result == True, "标记应该成功"
        
        record = registry.get_by_path(file_path)
        assert record['status'] == 'indexed', "状态应该是 indexed"
        assert record['chunk_count'] == 5, "切片数应该是5"
        assert record['indexed_at'] is not None, "应该有索引时间"
        
        print(f"✅ 标记索引完成")
        print(f"   status: {record['status']}")
        print(f"   chunk_count: {record['chunk_count']}")
        
    finally:
        cleanup_test(temp_dir)


def test_mark_error():
    """测试5：标记索引失败"""
    print("\n" + "="*60)
    print("测试5：标记索引失败（pending → error）")
    print("="*60)
    
    registry, temp_dir, test_files = setup_test_registry()
    
    try:
        file_path = test_files["doc0.md"]
        
        registry.register(file_path, "uuid-001", "sha256:abc")
        registry.mark_error(file_path, "Embedding 模型超时")
        
        record = registry.get_by_path(file_path)
        assert record['status'] == 'error', "状态应该是 error"
        assert record['error_message'] == "Embedding 模型超时", "错误信息应该匹配"
        
        print(f"✅ 标记索引失败")
        print(f"   status: {record['status']}")
        print(f"   error: {record['error_message']}")
        
    finally:
        cleanup_test(temp_dir)


def test_get_pending_files():
    """测试6：获取待索引文件"""
    print("\n" + "="*60)
    print("测试6：获取待索引文件列表")
    print("="*60)
    
    registry, temp_dir, test_files = setup_test_registry()
    
    try:
        # 注册3个文件，标记1个为 indexed
        for i, fp in enumerate(test_files.values()):
            registry.register(fp, f"uuid-{i}", f"hash_{i}")
        
        registry.mark_indexed(test_files["doc0.md"], chunk_count=2)
        
        pending = registry.get_pending_files()
        assert len(pending) == 2, f"应该有2个待索引文件，实际{len(pending)}个"
        
        print(f"✅ 获取 {len(pending)} 个待索引文件")
        for p in pending:
            print(f"   - {os.path.basename(p['file_path'])}: status=pending")
        
    finally:
        cleanup_test(temp_dir)


def test_get_all_hashes():
    """测试7：获取所有 Hash 映射"""
    print("\n" + "="*60)
    print("测试7：获取所有 Hash 映射（用于增量比对）")
    print("="*60)
    
    registry, temp_dir, test_files = setup_test_registry()
    
    try:
        # 注册文件
        for i, fp in enumerate(test_files.values()):
            registry.register(fp, f"uuid-{i}", f"hash_{i}")
        
        hashes = registry.get_all_hashes()
        assert len(hashes) == 3, "应该有3个 Hash 记录"
        
        # 验证格式
        for path, hash_val in hashes.items():
            assert isinstance(path, str), "路径应该是字符串"
            assert isinstance(hash_val, str), "Hash 应该是字符串"
        
        print(f"✅ 获取 {len(hashes)} 个 Hash 映射")
        
    finally:
        cleanup_test(temp_dir)


def test_remove_and_batch_remove():
    """测试8：删除文件记录"""
    print("\n" + "="*60)
    print("测试8：单删和批量删除")
    print("="*60)
    
    registry, temp_dir, test_files = setup_test_registry()
    
    try:
        # 注册所有文件
        for i, fp in enumerate(test_files.values()):
            registry.register(fp, f"uuid-{i}", f"hash_{i}")
        
        # 单删
        result = registry.remove(test_files["doc0.md"])
        assert result == True, "删除应该成功"
        
        stats = registry.stats()
        assert stats['total_files'] == 2, "剩余文件数应该是2"
        
        # 批量删除
        count = registry.batch_remove([test_files["doc1.md"]])
        assert count == 1, "应该删除1个文件"
        
        stats = registry.stats()
        assert stats['total_files'] == 1, "最终剩余文件数应该是1"
        
        print(f"✅ 单删和批量删除成功")
        print(f"   剩余文件: {stats['total_files']}")
        
    finally:
        cleanup_test(temp_dir)


def test_cleanup_deleted_files():
    """测试9：清理已删除文件"""
    print("\n" + "="*60)
    print("测试9：清理已删除文件的记录")
    print("="*60)
    
    registry, temp_dir, test_files = setup_test_registry()
    
    try:
        # 注册所有文件
        for i, fp in enumerate(test_files.values()):
            registry.register(fp, f"uuid-{i}", f"hash_{i}")
        
        # 模拟删除一个物理文件
        os.remove(test_files["doc2.md"])
        
        # 当前扫描结果（只有2个文件）
        current_scan = {
            test_files["doc0.md"]: "hash_0",
            test_files["doc1.md"]: "hash_1"
        }
        
        deleted = registry.cleanup_deleted_files(current_scan)
        assert len(deleted) == 1, f"应该清理1个文件，实际{len(deleted)}个"
        
        stats = registry.stats()
        assert stats['total_files'] == 2, "剩余记录数应该是2"
        
        print(f"✅ 清理 {len(deleted)} 个已删除文件的记录")
        
    finally:
        cleanup_test(temp_dir)


def test_stats():
    """测试10：统计信息"""
    print("\n" + "="*60)
    print("测试10：注册表统计信息")
    print("="*60)
    
    registry, temp_dir, test_files = setup_test_registry()
    
    try:
        # 注册并标记不同状态
        registry.register(test_files["doc0.md"], "uuid-0", "hash_0")
        registry.mark_indexed(test_files["doc0.md"], chunk_count=3)
        
        registry.register(test_files["doc1.md"], "uuid-1", "hash_1")
        # doc1 保持 pending
        
        registry.register(test_files["doc2.md"], "uuid-2", "hash_2")
        registry.mark_error(test_files["doc2.md"], "测试错误")
        
        stats = registry.stats()
        
        assert stats['total_files'] == 3, "总文件数应该是3"
        assert stats['indexed_count'] == 1, "已索引数应该是1"
        assert stats['pending_count'] == 1, "待索引数应该是1"
        assert stats['error_count'] == 1, "错误数应该是1"
        assert stats['total_chunks'] == 3, "总切片数应该是3"
        
        print(f"✅ 统计信息准确")
        for key, value in stats.items():
            print(f"   {key}: {value}")
        
    finally:
        cleanup_test(temp_dir)


def run_all_tests():
    """运行所有测试"""
    print("\n" + "🧪 FileRegistry 模块测试套件")
    print("="*60)
    
    tests = [
        test_register_single_file,
        test_register_update,
        test_batch_register,
        test_mark_indexed,
        test_mark_error,
        test_get_pending_files,
        test_get_all_hashes,
        test_remove_and_batch_remove,
        test_cleanup_deleted_files,
        test_stats,
    ]
    
    passed = 0
    failed = 0
    
    for test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"❌ {test_func.__name__} 失败: {e}")
            failed += 1
        except Exception as e:
            print(f"💥 {test_func.__name__} 异常: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "="*60)
    print(f"📊 测试结果: {passed} 通过, {failed} 失败")
    print("="*60)
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
