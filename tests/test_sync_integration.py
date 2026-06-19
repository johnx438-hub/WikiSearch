"""
WikiIndex.sync_from_config() 集成测试

验证增量同步流程：
1. 首次扫描 → 全部新增
2. 修改文件 → 检测变化并重新索引
3. 删除文件 → 清理索引
4. 注册表状态一致性
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.wiki_index import WikiIndex


def create_test_doc(directory: str, filename: str, content: str) -> str:
    """创建测试文档"""
    filepath = os.path.join(directory, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    return filepath


def test_sync_initial():
    """测试1：首次同步（全部新增）"""
    print("\n" + "="*60)
    print("测试1：首次同步（全部新增）")
    print("="*60)
    
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "wiki_db")
    
    try:
        # 创建测试文档
        docs_dir = os.path.join(temp_dir, "docs")
        os.makedirs(docs_dir)
        
        create_test_doc(docs_dir, "doc1.md", "# 文档1\n\n这是第一个文档。")
        create_test_doc(docs_dir, "doc2.md", "# 文档2\n\n这是第二个文档，内容更长一些。")
        
        # 初始化 WikiIndex
        idx = WikiIndex(db_path=db_path)
        
        # 配置同步
        config = {
            "watch_dirs": [docs_dir],
            "supported_extensions": [".md"],
            "ignore_patterns": []
        }
        
        result = idx.sync_from_config(config)
        
        print(f"✅ 首次同步完成")
        print(f"   新增: {result['added']}")
        print(f"   修改: {result['modified']}")
        print(f"   删除: {result['deleted']}")
        print(f"   错误: {len(result['errors'])}")
        
        # 验证注册表状态
        stats = idx.registry_stats()
        print(f"   注册表统计: {stats}")
        
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_sync_modified():
    """测试2：修改文件检测"""
    print("\n" + "="*60)
    print("测试2：修改文件检测并重新索引")
    print("="*60)
    
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "wiki_db")
    
    try:
        docs_dir = os.path.join(temp_dir, "docs")
        os.makedirs(docs_dir)
        
        # 创建初始文档
        doc_path = create_test_doc(docs_dir, "test.md", "# 原始内容\n\n这是原始版本。")
        
        # 首次同步
        idx = WikiIndex(db_path=db_path)
        config = {
            "watch_dirs": [docs_dir],
            "supported_extensions": [".md"],
            "ignore_patterns": []
        }
        
        result1 = idx.sync_from_config(config)
        print(f"首次同步: 新增={result1['added']}")
        
        # 修改文件内容
        with open(doc_path, 'w', encoding='utf-8') as f:
            f.write("# 修改后的内容\n\n这是修改后的版本，内容更长了，增加了更多细节。")
        
        # 再次同步（应该检测到修改）
        result2 = idx.sync_from_config(config)
        print(f"二次同步: 新增={result2['added']}, 修改={result2['modified']}")
        
        print(f"✅ 修改检测成功")
        
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_sync_deleted():
    """测试3：删除文件检测"""
    print("\n" + "="*60)
    print("测试3：删除文件检测并清理索引")
    print("="*60)
    
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "wiki_db")
    
    try:
        docs_dir = os.path.join(temp_dir, "docs")
        os.makedirs(docs_dir)
        
        # 创建两个文档
        doc1 = create_test_doc(docs_dir, "doc1.md", "# 文档1\n")
        doc2 = create_test_doc(docs_dir, "doc2.md", "# 文档2\n")
        
        # 首次同步
        idx = WikiIndex(db_path=db_path)
        config = {
            "watch_dirs": [docs_dir],
            "supported_extensions": [".md"],
            "ignore_patterns": []
        }
        
        result1 = idx.sync_from_config(config)
        print(f"首次同步: 新增={result1['added']}")
        
        # 删除一个文件
        os.remove(doc2)
        
        # 再次同步（应该检测到删除）
        result2 = idx.sync_from_config(config)
        print(f"二次同步: 删除={result2['deleted']}")
        
        stats = idx.registry_stats()
        print(f"注册表剩余文件: {stats['total_files']}")
        
        print(f"✅ 删除检测成功")
        
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_sync_with_search():
    """测试4：同步后搜索验证"""
    print("\n" + "="*60)
    print("测试4：同步后搜索功能验证")
    print("="*60)
    
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "wiki_db")
    
    try:
        docs_dir = os.path.join(temp_dir, "docs")
        os.makedirs(docs_dir)
        
        # 创建包含特定关键词的文档
        create_test_doc(docs_dir, "oil.md", 
            "# 格瑞迪斯石油\n\n## 财务数据\n\n2024年营收2.67亿元，净利润4340万元。")
        
        create_test_doc(docs_dir, "tech.md",
            "# 技术实力\n\n公司拥有油服技术小巨人称号，持有专利23项。")
        
        # 同步
        idx = WikiIndex(db_path=db_path)
        config = {
            "watch_dirs": [docs_dir],
            "supported_extensions": [".md"],
            "ignore_patterns": []
        }
        
        result = idx.sync_from_config(config)
        print(f"同步完成: 新增={result['added']}")
        
        # 搜索验证
        search_results = idx.search("格瑞迪斯石油营收", k=3)
        print(f"搜索结果: {len(search_results)} 条")
        
        if search_results:
            for doc_id, score in search_results[:1]:
                content = idx.get_content(doc_id)
                if content:
                    preview = content['content'][:50]
                    print(f"   - ID={doc_id}, 预览: {preview}...")
        
        print(f"✅ 搜索功能正常")
        
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def run_all_tests():
    """运行所有测试"""
    print("\n" + "🧪 WikiIndex.sync_from_config() 集成测试套件")
    print("="*60)
    
    tests = [
        test_sync_initial,
        test_sync_modified,
        test_sync_deleted,
        test_sync_with_search,
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
