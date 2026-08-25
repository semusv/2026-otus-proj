"""Сборка сервисов агента (runtime) для create_app.

Клиенты создаются лениво: ни Qdrant, ни Neo4j, ни модели (bge-m3/reranker)
не трогаются на старте - только при первом реальном запросе чата.
Подмена компонентов в интеграционных тестах - через app.state.chat_runtime.
"""

from qdrant_client import AsyncQdrantClient

from app.agents.graph import AgentRuntime
from app.agents.memory import ChatMemory
from app.config import Settings
from app.db.base import Database
from app.ingestion.embeddings import Embedder
from app.llm.client import LLMClient, LLMConfig
from app.rag.reranker import Reranker
from app.rag.retrievers import GraphRetriever, VectorRetriever
from app.rag.tools import AgentTools, QueryEmbedder


def build_chat_runtime(settings: Settings, db: Database) -> tuple[AgentRuntime,
                                                                   GraphRetriever,
                                                                   AsyncQdrantClient]:
    """Создаёт runtime чата и возвращает его вместе с ресурсами для закрытия."""
    qdrant_client = AsyncQdrantClient(url=settings.qdrant_url, timeout=60)
    vector_retriever = VectorRetriever(qdrant_client, settings.qdrant_collection)

    llm = LLMClient(
        LLMConfig(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
        )
    )

    embedder = Embedder(
        settings.embedding_model,
        batch_size=settings.embedding_batch_size,
        device=settings.embedding_device,
    )
    query_embedder = QueryEmbedder(embedder)

    reranker = Reranker(settings.rerank_model, device=settings.rerank_device)

    graph_retriever = GraphRetriever(
        settings.neo4j_uri,
        settings.neo4j_user,
        settings.neo4j_password.get_secret_value(),
    )

    tools = AgentTools(
        vector_retriever=vector_retriever,
        graph_retriever=graph_retriever,
        embedder=query_embedder,
        settings=settings,
    )
    memory = ChatMemory(db)

    runtime = AgentRuntime(
        settings=settings,
        llm=llm,
        tools=tools,
        reranker=reranker,
        memory=memory,
    )
    return runtime, graph_retriever, qdrant_client
