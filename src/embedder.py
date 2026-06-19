"""
Embedding模型封装

支持多种后端：
- Ollama (本地，免费)
- OpenAI Compatible API (云端)
- ChromaDB内置 (无需外部服务)

默认使用 bge-m3 via Ollama（768维，多语言强）。
"""

import json
import subprocess
from typing import Optional, List


class Embedder:
    """
    统一Embedding接口。
    
    使用示例：
        embedder = Embedder(model_name="bge-m3")  # Ollama默认
        vector = embedder.embed("格瑞迪斯石油营收数据")
        print(f"向量维度: {len(vector)}")  # 768
    """
    
    def __init__(self, backend: str = "ollama", model_name: str = "qwen3-embedding:8b",
                 api_url: Optional[str] = None, api_key: Optional[str] = None):
        """
        Args:
            backend: "ollama" | "openai" | "chromadb"
            model_name: 模型名称
            api_url: API地址（Ollama默认 http://localhost:11434/api/embeddings）
            api_key: API密钥（OpenAI兼容模式需要）
        """
        self.backend = backend
        self.model_name = model_name
        self.api_url = api_url or self._default_url(backend)
        self.api_key = api_key
    
    def _default_url(self, backend: str) -> str:
        if backend == "ollama":
            return "http://localhost:11434/api/embeddings"
        elif backend == "openai":
            return "https://api.openai.com/v1/embeddings"
        else:
            raise ValueError(f"Unknown backend: {backend}")
    
    def embed(self, text: str) -> Optional[List[float]]:
        """
        生成文本的embedding向量。
        
        Args:
            text: 输入文本
        
        Returns:
            embedding向量（List[float]）或 None（失败时）
        """
        if not text or not text.strip():
            return None
        
        try:
            if self.backend == "ollama":
                return self._embed_ollama(text)
            elif self.backend == "openai":
                return self._embed_openai(text)
            else:
                print(f"⚠️ 未知后端: {self.backend}，使用Ollama回退")
                return self._embed_ollama(text)
        except Exception as e:
            print(f"❌ Embedding失败: {e}")
            return None
    
    def embed_batch(self, texts: List[str]) -> Optional[List[List[float]]]:
        """
        批量生成embedding（Ollama支持批量输入）。
        
        Args:
            texts: 文本列表
        
        Returns:
            embedding向量列表，或 None
        """
        if not texts:
            return None
        
        try:
            if self.backend == "ollama":
                return self._embed_batch_ollama(texts)
            else:
                # 其他后端逐条处理
                return [self.embed(t) for t in texts]
        except Exception as e:
            print(f"❌ Batch embedding失败: {e}")
            return None
    
    def _embed_ollama(self, text: str) -> Optional[List[float]]:
        """
        通过Ollama API生成embedding。
        
        Ollama embeddings端点：
        POST /api/embeddings
        Body: {"model": "bge-m3", "prompt": "text"}
        Response: {"embedding": [0.1, 0.2, ...]}
        """
        import urllib.request
        
        payload = json.dumps({
            "model": self.model_name,
            "prompt": text,
        }).encode("utf-8")
        
        req = urllib.request.Request(
            self.api_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result.get("embedding")
    
    def _embed_batch_ollama(self, texts: List[str]) -> Optional[List[List[float]]]:
        """
        通过Ollama API批量生成embedding。
        
        Ollama支持批量输入：
        POST /api/embed
        Body: {"model": "bge-m3", "input": ["text1", "text2"]}
        Response: {"embeddings": [[0.1, ...], [0.2, ...]]}
        """
        import urllib.request
        
        payload = json.dumps({
            "model": self.model_name,
            "input": texts,
        }).encode("utf-8")
        
        req = urllib.request.Request(
            f"{self.api_url.rsplit('/', 1)[0]}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result.get("embeddings")
    
    def _embed_openai(self, text: str) -> Optional[List[float]]:
        """
        通过OpenAI兼容API生成embedding。
        
        OpenAI embeddings端点：
        POST /v1/embeddings
        Body: {"model": "text-embedding-3-small", "input": "text"}
        Response: {"data": [{"embedding": [0.1, 0.2, ...]}]}
        """
        import urllib.request
        
        payload = json.dumps({
            "model": self.model_name,
            "input": text,
        }).encode("utf-8")
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        
        req = urllib.request.Request(
            self.api_url,
            data=payload,
            headers=headers,
            method="POST",
        )
        
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result.get("data", [{}])[0].get("embedding")
    
    def check_ollama(self) -> bool:
        """
        检查Ollama服务是否可用。
        
        Returns:
            True: Ollama运行中且模型已拉取
            False: Ollama未运行或模型不存在
        """
        if self.backend != "ollama":
            return True  # 非Ollama后端跳过检查
        
        try:
            import urllib.request
            req = urllib.request.Request(
                "http://localhost:11434/api/tags",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
                models = [m["name"] for m in result.get("models", [])]
                return any(self.model_name in m for m in models)
        except Exception:
            return False
    
    def list_ollama_models(self) -> List[str]:
        """
        列出Ollama中可用的模型。
        
        Returns:
            模型名称列表
        """
        if self.backend != "ollama":
            return []
        
        try:
            import urllib.request
            req = urllib.request.Request(
                "http://localhost:11434/api/tags",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
                return [m["name"] for m in result.get("models", [])]
        except Exception:
            return []
