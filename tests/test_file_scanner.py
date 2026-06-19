"""
FileScanner 模块测试

验证文件扫描器的核心功能：
1. 目录递归扫描 + 扩展名过滤
2. SHA256 Hash 计算准确性
3. 增量变化检测（新增/修改/删除）
4. 忽略模式匹配
"""

import os
import sys
import tempfile
import shutil
import time
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.file_scanner import (
    FileScanner, 
    generate_file_id, 
    batch_compute_hashes
)


def setup_test_directory(temp_dir: str) -> dict:
    """创建测试用的临时文件结构"""
    # 创建子目录
    os.makedirs(os.path.join(temp_dir, "subdir"), exist_ok=True)
    os.makedirs(os.path.join(temp_dir, "__pycache__"), exist_ok=True)
    
    files = {}
    
    # 根目录文件
    file1 = os.path.join(temp_dir, "test1.md")
    with open(file1, 'w', encoding='utf-8') as f:
        f.write("# 测试文档1\n\n这是第一个测试文档的内容。")
    files["test1.md"] = file1
    
    file2 = os.path.join(temp_dir, "report.docx")
    with open(file2, 'w', encoding='utf-8') as f:
        f.write("DOCX content here")
    files["report.docx"] = file2
    
    # 子目录文件
    file3 = os.path.join(temp_dir, "subdir", "nested.pdf")
    with open(file3, 'w', encoding='utf-8') as f:
        f.write("PDF content here")
    files["nested.pdf"] = file3
    
    # 忽略的文件
    cache_file = os.path.join(temp_dir, "__pycache__", "module.pyc")
    with open(cache_file, 'w', encoding='utf-8') as f:
        f.write("cached content")
    
    tmp_file = os.path.join(temp_dir, "draft.tmp")
    with open(tmp_file, 'w', encoding='utf-8') as f:
        f.write("temporary file")
    
    return files


def test_scan_directories_basic():
    """测试1：基础目录扫描功能"""
    print("\n" + "="*60)
    print("测试1：基础目录扫描功能")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        files = setup_test_directory(temp_dir)
        
        scanner = FileScanner(
            supported_extensions=['.md', '.docx', '.pdf'],
            ignore_patterns=['__pycache__', '*.tmp']
        )
        
        result = scanner.scan_directories([temp_dir])
        
        # 验证：应该扫描到3个文件（test1.md, report.docx, nested.pdf）
        assert len(result) == 3, f"期望3个文件，实际{len(result)}个"
        
        # 验证：每个文件都有 Hash
        for path, file_hash in result.items():
            assert isinstance(file_hash, str), "Hash应该是字符串"
            assert len(file_hash) == 64, f"SHA256应该是64位十六进制，实际{len(file_hash)}位"
        
        print(f"✅ 扫描到 {len(result)} 个文件")
        for path in sorted(result.keys()):
            filename = os.path.basename(path)
            print(f"   - {filename}: {result[path][:16]}...")


def test_scan_extension_filter():
    """测试2：扩展名过滤"""
    print("\n" + "="*60)
    print("测试2：扩展名过滤")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        files = setup_test_directory(temp_dir)
        
        # 只扫描 .md 文件
        scanner_md = FileScanner(supported_extensions=['.md'])
        result_md = scanner_md.scan_directories([temp_dir])
        
        assert len(result_md) == 1, f"期望1个.md文件，实际{len(result_md)}个"
        print(f"✅ .md 过滤：扫描到 {len(result_md)} 个文件")
        
        # 不指定扩展名（所有文件）
        scanner_all = FileScanner()
        result_all = scanner_all.scan_directories([temp_dir])
        
        assert len(result_all) > 3, f"无过滤应扫描到更多文件，实际{len(result_all)}个"
        print(f"✅ 无过滤：扫描到 {len(result_all)} 个文件")


def test_scan_ignore_patterns():
    """测试3：忽略模式匹配"""
    print("\n" + "="*60)
    print("测试3：忽略模式匹配")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        files = setup_test_directory(temp_dir)
        
        # 不忽略任何文件
        scanner_no_ignore = FileScanner(supported_extensions=['.md', '.docx', '.pdf'])
        result_no_ignore = scanner_no_ignore.scan_directories([temp_dir])
        
        # 忽略 __pycache__ 和 *.tmp
        scanner_with_ignore = FileScanner(
            supported_extensions=['.md', '.docx', '.pdf'],
            ignore_patterns=['__pycache__', '*.tmp']
        )
        result_with_ignore = scanner_with_ignore.scan_directories([temp_dir])
        
        # 忽略后应该少一些文件
        assert len(result_with_ignore) <= len(result_no_ignore), "忽略模式应该减少扫描结果"
        print(f"✅ 无忽略: {len(result_no_ignore)} 个文件")
        print(f"✅ 有忽略: {len(result_with_ignore)} 个文件")


def test_compute_file_hash():
    """测试4：Hash计算准确性"""
    print("\n" + "="*60)
    print("测试4：Hash计算准确性")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        # 创建已知内容的文件
        test_file = os.path.join(temp_dir, "known_content.txt")
        content = "Hello, World! 你好，世界！"
        with open(test_file, 'w', encoding='utf-8') as f:
            f.write(content)
        
        scanner = FileScanner()
        file_hash = scanner.compute_file_hash(test_file)
        
        # 验证 Hash 格式
        assert isinstance(file_hash, str), "Hash应该是字符串"
        assert len(file_hash) == 64, "SHA256应该是64位十六进制"
        
        # 验证相同内容产生相同 Hash
        file_hash2 = scanner.compute_file_hash(test_file)
        assert file_hash == file_hash2, "相同文件应该产生相同Hash"
        
        # 修改内容后 Hash 应该不同
        with open(test_file, 'w', encoding='utf-8') as f:
            f.write(content + " 追加内容")
        
        file_hash3 = scanner.compute_file_hash(test_file)
        assert file_hash != file_hash3, "内容改变后Hash应该不同"
        
        print(f"✅ Hash格式正确：{file_hash[:16]}...")
        print(f"✅ 相同文件产生相同Hash")
        print(f"✅ 内容改变后Hash不同")


def test_incremental_changes_added():
    """测试5a：增量检测 - 新增文件"""
    print("\n" + "="*60)
    print("测试5a：增量检测 - 新增文件")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        scanner = FileScanner(supported_extensions=['.md'])
        
        # 初始状态为空
        previous = {}
        
        # 创建新文件
        new_file = os.path.join(temp_dir, "new_doc.md")
        with open(new_file, 'w', encoding='utf-8') as f:
            f.write("# 新文档\n\n这是新增的文档。")
        
        current = scanner.scan_directories([temp_dir])
        
        changes = scanner.get_incremental_changes(previous, current)
        
        assert len(changes['added']) == 1, f"期望1个新增文件，实际{len(changes['added'])}个"
        assert new_file in changes['added'], "新文件应该在added列表中"
        assert len(changes['modified']) == 0, "不应该有修改的文件"
        assert len(changes['deleted']) == 0, "不应该有删除的文件"
        
        print(f"✅ 检测到 {len(changes['added'])} 个新增文件")


def test_incremental_changes_modified():
    """测试5b：增量检测 - 修改文件"""
    print("\n" + "="*60)
    print("测试5b：增量检测 - 修改文件")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        scanner = FileScanner(supported_extensions=['.md'])
        
        # 创建初始文件
        test_file = os.path.join(temp_dir, "test.md")
        with open(test_file, 'w', encoding='utf-8') as f:
            f.write("# 原始内容\n\n这是原始版本。")
        
        previous = scanner.scan_directories([temp_dir])
        
        # 修改文件内容
        time.sleep(0.1)  # 确保时间戳不同
        with open(test_file, 'w', encoding='utf-8') as f:
            f.write("# 修改后的内容\n\n这是修改后的版本，内容更长了。")
        
        current = scanner.scan_directories([temp_dir])
        
        changes = scanner.get_incremental_changes(previous, current)
        
        assert len(changes['modified']) == 1, f"期望1个修改文件，实际{len(changes['modified'])}个"
        assert test_file in changes['modified'], "修改的文件应该在modified列表中"
        assert len(changes['added']) == 0, "不应该有新增的文件"
        
        print(f"✅ 检测到 {len(changes['modified'])} 个修改文件")


def test_incremental_changes_deleted():
    """测试5c：增量检测 - 删除文件"""
    print("\n" + "="*60)
    print("测试5c：增量检测 - 删除文件")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        scanner = FileScanner(supported_extensions=['.md'])
        
        # 创建两个文件
        file1 = os.path.join(temp_dir, "doc1.md")
        file2 = os.path.join(temp_dir, "doc2.md")
        
        with open(file1, 'w', encoding='utf-8') as f:
            f.write("# 文档1\n")
        with open(file2, 'w', encoding='utf-8') as f:
            f.write("# 文档2\n")
        
        previous = scanner.scan_directories([temp_dir])
        assert len(previous) == 2, "初始应该有2个文件"
        
        # 删除一个文件
        os.remove(file2)
        
        current = scanner.scan_directories([temp_dir])
        
        changes = scanner.get_incremental_changes(previous, current)
        
        assert len(changes['deleted']) == 1, f"期望1个删除文件，实际{len(changes['deleted'])}个"
        assert file2 in changes['deleted'], "删除的文件应该在deleted列表中"
        assert len(changes['added']) == 0, "不应该有新增的文件"
        
        print(f"✅ 检测到 {len(changes['deleted'])} 个删除文件")


def test_generate_file_id():
    """测试6：文件ID生成"""
    print("\n" + "="*60)
    print("测试6：文件ID生成（UUID5稳定性）")
    print("="*60)
    
    # 同一路径应该产生相同 ID
    path = "/home/archer/docs/report.docx"
    id1 = generate_file_id(path)
    id2 = generate_file_id(path)
    
    assert id1 == id2, "同一路径应该生成相同ID"
    print(f"✅ 路径: {path}")
    print(f"   ID: {id1}")
    
    # 不同路径产生不同 ID
    path2 = "/home/archer/docs/other.docx"
    id3 = generate_file_id(path2)
    assert id1 != id3, "不同路径应该生成不同ID"
    print(f"✅ 不同路径产生不同ID")


def test_batch_compute_hashes():
    """测试7：批量Hash计算"""
    print("\n" + "="*60)
    print("测试7：批量Hash计算")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        # 创建多个文件
        file_paths = []
        for i in range(5):
            fp = os.path.join(temp_dir, f"file{i}.txt")
            with open(fp, 'w', encoding='utf-8') as f:
                f.write(f"Content {i}")
            file_paths.append(fp)
        
        result = batch_compute_hashes(file_paths)
        
        assert len(result) == 5, f"期望5个Hash结果，实际{len(result)}个"
        print(f"✅ 批量计算 {len(result)} 个文件的Hash")


def run_all_tests():
    """运行所有测试"""
    print("\n" + "🧪 FileScanner 模块测试套件")
    print("="*60)
    
    tests = [
        test_scan_directories_basic,
        test_scan_extension_filter,
        test_scan_ignore_patterns,
        test_compute_file_hash,
        test_incremental_changes_added,
        test_incremental_changes_modified,
        test_incremental_changes_deleted,
        test_generate_file_id,
        test_batch_compute_hashes,
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
            failed += 1
    
    print("\n" + "="*60)
    print(f"📊 测试结果: {passed} 通过, {failed} 失败")
    print("="*60)
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
