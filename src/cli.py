"""
LLM-Wiki CLI — 命令行界面

提供完整的 Wiki 索引管理功能：
- wiki init: 初始化项目（生成 config.yaml）
- wiki sync: 增量同步目录到索引
- wiki search: 混合检索
- wiki list: 列出已索引文件
- wiki show: 查看原文
- wiki stats: 查看统计信息

使用示例：
    python -m src.cli init --db ./my_wiki_db
    python -m src.cli sync
    python -m src.cli search "TurboVec压缩率" --top 5
    python -m src.cli list --status indexed
    python -m src.cli show /path/to/file.md
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

# 确保项目根目录在路径中
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml


# ==================== 默认配置模板 ====================

DEFAULT_CONFIG = """# LLM-Wiki 配置文件
# ====================

# 监控的文档目录（支持多个）
watch_dirs:
  - /home/archer/memory_vault
  - /home/archer/docs/work

# 支持的文件扩展名
supported_extensions:
  - .md
  # - .docx
  # - .pdf

# 忽略的文件/目录模式（fnmatch 格式）
ignore_patterns:
  - __pycache__
  - "*.tmp"
  - "*.bak"

# Embedding 模型配置
embedding:
  backend: ollama              # ollama / openai
  model_name: qwen3-embedding:8b  # Ollama 模型名称
  api_url: http://localhost:11434/api/embeddings

# 索引配置
indexing:
  db_path: ./wiki_db           # 数据库存储路径
  dim: null                    # 向量维度（null=自动检测）
  bit_width: 4                 # TurboVec 压缩位数（2/3/4）
"""


def load_config(config_path: str) -> dict:
    """加载配置文件"""
    if not os.path.exists(config_path):
        print(f"❌ 配置文件不存在: {config_path}")
        print("💡 运行 'wiki init' 生成默认配置")
        sys.exit(1)
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    return config


def cmd_init(args):
    """初始化项目（生成 config.yaml）"""
    config_path = args.config or "config.yaml"
    
    if os.path.exists(config_path):
        print(f"⚠️ 配置文件已存在: {config_path}")
        if not args.force:
            print("💡 使用 --force 覆盖现有配置")
            return
    
    with open(config_path, 'w', encoding='utf-8') as f:
        f.write(DEFAULT_CONFIG)
    
    print(f"✅ 配置文件已生成: {config_path}")
    print("📝 请编辑 config.yaml 设置 watch_dirs 和 embedding 模型")


def cmd_sync(args):
    """增量同步目录到索引"""
    from src.wiki_index import WikiIndex
    
    config = load_config(args.config or "config.yaml")
    
    # 提取配置
    db_path = config.get("indexing", {}).get("db_path", "./wiki_db")
    embedding = config.get("embedding", {})
    
    # 初始化 WikiIndex（使用配置的模型）
    idx = WikiIndex(
        db_path=db_path,
        dim=config.get("indexing", {}).get("dim"),
        bit_width=config.get("indexing", {}).get("bit_width", 4)
    )
    
    # 覆盖 Embedding 配置（如果指定了）
    if embedding:
        idx.embedder.model_name = embedding.get("model_name", idx.embedder.model_name)
        idx.embedder.backend = embedding.get("backend", idx.embedder.backend)
    
    print(f"🔄 开始增量同步...")
    print(f"   监控目录: {config.get('watch_dirs', [])}")
    print(f"   支持格式: {config.get('supported_extensions', ['.md'])}")
    
    result = idx.sync_from_config(config)
    
    # 输出结果
    print(f"\n✅ 同步完成:")
    print(f"   新增: {result['added']}")
    print(f"   修改: {result['modified']}")
    print(f"   删除: {result['deleted']}")
    
    if result['errors']:
        print(f"\n⚠️ 错误 ({len(result['errors'])} 个):")
        for err in result['errors'][:5]:  # 最多显示5个
            print(f"   - {err['file']}: {err['error']}")


def cmd_search(args):
    """混合检索"""
    from src.wiki_index import WikiIndex
    
    config = load_config(args.config or "config.yaml")
    db_path = config.get("indexing", {}).get("db_path", "./wiki_db")
    
    # 初始化 WikiIndex
    idx = WikiIndex(db_path=db_path)
    
    # 搜索并输出结果
    results = idx.print_search_results(args.query, k=args.top)


def cmd_list(args):
    """列出已索引文件"""
    from src.wiki_index import WikiIndex
    
    config = load_config(args.config or "config.yaml")
    db_path = config.get("indexing", {}).get("db_path", "./wiki_db")
    
    idx = WikiIndex(db_path=db_path)
    
    # 获取注册表记录
    all_hashes = idx.registry.get_all_hashes()
    
    if not all_hashes:
        print("📭 暂无索引文件")
        return
    
    # 按状态过滤（如果需要）
    records = []
    for file_path in sorted(all_hashes.keys()):
        record = idx.registry.get_by_path(file_path)
        if record and (not args.status or record['status'] == args.status):
            records.append(record)
    
    # 输出格式
    if args.format == "json":
        print(json.dumps(records, indent=2, ensure_ascii=False))
    else:
        # Table 格式
        print(f"\n📚 已索引文件 ({len(records)} 个)\n")
        print(f"{'状态':<10} {'文件名':<40} {'切片数':<8} {'最后更新'}")
        print("-" * 80)
        
        for record in records:
            status = record['status']
            emoji = {'indexed': '✅', 'pending': '⏳', 'error': '❌'}.get(status, '❓')
            filename = os.path.basename(record['file_path'])
            
            # 截断长文件名
            if len(filename) > 38:
                filename = "..." + filename[-35:]
            
            print(f"{emoji} {status:<9} {filename:<40} {record['chunk_count']:<8} {record.get('indexed_at', 'N/A')}")


def cmd_show(args):
    """查看原文"""
    file_path = args.file_path
    
    if not os.path.exists(file_path):
        print(f"❌ 文件不存在: {file_path}")
        return
    
    # 读取并输出内容
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    filename = os.path.basename(file_path)
    print(f"\n📄 {filename}\n")
    print(content)


def cmd_stats(args):
    """查看统计信息"""
    from src.wiki_index import WikiIndex
    
    config = load_config(args.config or "config.yaml")
    db_path = config.get("indexing", {}).get("db_path", "./wiki_db")
    
    idx = WikiIndex(db_path=db_path)
    
    # 索引统计
    idx_stats = idx.stats()
    reg_stats = idx.registry_stats()
    
    print("\n📊 LLM-Wiki 统计信息\n")
    print("=" * 50)
    print("🗂️ 文件注册表:")
    print(f"   总文件数: {reg_stats['total_files']}")
    print(f"   已索引: {reg_stats['indexed_count']}")
    print(f"   待处理: {reg_stats['pending_count']}")
    print(f"   错误: {reg_stats['error_count']}")
    print(f"   总切片数: {reg_stats['total_chunks']}")
    
    print("\n🔍 索引层:")
    print(f"   BM25 chunks: {idx_stats['bm25_chunks']}")
    print(f"   向量数: {idx_stats['vec_vectors']}")
    print(f"   DocStore: {idx_stats['store_docs']}")
    print(f"   压缩率: {idx_stats['compression_ratio']}")
    
    # 错误列表
    errors = idx.registry.get_errors()
    if errors:
        print(f"\n⚠️ 索引失败的文件 ({len(errors)} 个):")
        for err in errors[:5]:
            print(f"   - {os.path.basename(err['file_path'])}: {err['error_message']}")


def main():
    """CLI 入口"""
    parser = argparse.ArgumentParser(
        prog="wiki",
        description="LLM-Wiki — 企业级文档混合检索系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s init                    # 初始化项目
  %(prog)s sync                    # 增量同步目录
  %(prog)s search "关键词"         # 搜索文档
  %(prog)s list --status indexed   # 列出已索引文件
  %(prog)s show /path/to/file.md   # 查看原文
  %(prog)s stats                   # 查看统计信息
        """
    )
    
    parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="配置文件路径 (默认: config.yaml)"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # init 命令
    init_parser = subparsers.add_parser("init", help="初始化项目（生成 config.yaml）")
    init_parser.add_argument("--force", "-f", action="store_true", help="覆盖现有配置")
    init_parser.set_defaults(func=cmd_init)
    
    # sync 命令
    sync_parser = subparsers.add_parser("sync", help="增量同步目录到索引")
    sync_parser.set_defaults(func=cmd_sync)
    
    # search 命令
    search_parser = subparsers.add_parser("search", help="混合检索")
    search_parser.add_argument("query", type=str, help="搜索关键词")
    search_parser.add_argument("--top", "-k", type=int, default=5, help="返回结果数量 (默认: 5)")
    search_parser.set_defaults(func=cmd_search)
    
    # list 命令
    list_parser = subparsers.add_parser("list", help="列出已索引文件")
    list_parser.add_argument("--status", "-s", choices=["indexed", "pending", "error"], 
                            help="按状态过滤")
    list_parser.add_argument("--format", "-f", choices=["table", "json"], default="table",
                            help="输出格式 (默认: table)")
    list_parser.set_defaults(func=cmd_list)
    
    # show 命令
    show_parser = subparsers.add_parser("show", help="查看原文")
    show_parser.add_argument("file_path", type=str, help="文件路径")
    show_parser.set_defaults(func=cmd_show)
    
    # stats 命令
    stats_parser = subparsers.add_parser("stats", help="查看统计信息")
    stats_parser.set_defaults(func=cmd_stats)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    args.func(args)


if __name__ == "__main__":
    main()
