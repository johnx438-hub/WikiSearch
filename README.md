# 📚 WikiVecSearch

基于 **BM25 + TurboVec** 混合检索的企业级文档索引系统。支持增量同步、语义搜索和跨会话持久化。

---

## ✨ 特性

- 🔍 **混合检索** — BM25 关键词召回 + TurboVec 向量重排
- ⚡ **增量同步** — SHA256 Hash 驱动，只处理变化文件
- 💾 **SQLite 持久化** — DocStore/FileRegistry 跨会话保留数据
- 📊 **分层切片** — L1 章节 + L2 段落，父子联动检索
- 🔧 **CLI 友好** — 命令行管理索引、搜索、统计

---

## 🚀 快速开始

### 安装依赖
```bash
pip install turbovec whoosh jieba numpy pyyaml
```

### 初始化项目
```bash
python -m src.cli init --force
# 生成 config.yaml，编辑 watch_dirs 和 embedding 配置
```

### 增量同步
```bash
python -m src.cli sync
# 扫描目录 → Hash比对 → 切片 → Embedding → 入库
```

### 搜索文档
```bash
python -m src.cli search "TurboVec压缩率" --top 5
```

---

## 📖 CLI 命令参考

| 命令 | 说明 |
|------|------|
| `wiki init` | 生成 config.yaml（含 embedding 配置） |
| `wiki sync` | 增量同步目录到索引 |
| `wiki search <query>` | 混合检索 + 格式化输出 |
| `wiki list [--status]` | 列出已索引文件（支持状态过滤） |
| `wiki show <path>` | 查看原文 |
| `wiki stats` | 查看统计信息 |

---

## 🏗️ 架构概览

```
CLI → WikiIndex → BM25(Whoosh) + TurboVec(IdMapIndex) + DocStore(SQLite)
                      ↑                    ↑                  ↑
                   jieba分词          4-bit压缩8x        content/metadata
```

---

## 📊 技术指标

| 指标 | 实测值 |
|------|--------|
| Embedding 模型 | qwen3-embedding:8b (4096维) |
| 向量压缩率 | 8.0x (TurboVec 4-bit) |
| 中文分词 | jieba |
| 测试规模 | 95 文件 / 1153 chunks |

---

## 🔮 后续规划

- **Phase 4**: 转换路由器（pandoc/markitdown/mineru）支持 Office + PDF
- **Phase 5**: GUI 前端（Tauri/PyQt6）类 Obsidian 体验
- **Phase 6**: 企业级特性（权限控制/协作编辑/API 服务）

---

## 📝 配置文件示例

```yaml
watch_dirs:
  - /home/archer/memory_vault

supported_extensions:
  - .md

embedding:
  backend: ollama
  model_name: qwen3-embedding:8b

indexing:
  db_path: ./wiki_db
  bit_width: 4
```

---

## 📄 License

MIT
