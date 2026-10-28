from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuração lida de variáveis de ambiente (ou do arquivo .env)."""

    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen3.5:4b")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    # O Ollama corta o prompt sem avisar quando ele passa do contexto configurado.
    ollama_num_ctx: int = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
    temperature: float = float(os.getenv("OLLAMA_TEMPERATURE", "0.2"))
    cache_dir: Path = Path(
        os.getenv("CONSELHEIRO_CACHE", str(Path.home() / ".cache" / "genai-langchain-osrs"))
    )
    user_agent: str = os.getenv(
        "CONSELHEIRO_USER_AGENT",
        "genai-langchain-osrs/1.0 (educational project; github.com/rafaeldellag/genai-langchain-osrs)",
    )
