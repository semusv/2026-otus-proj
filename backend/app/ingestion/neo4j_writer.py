"""Построение графа знаний в Neo4j (детерминированная часть + MENTIONS).

Идемпотентность: все операции - MERGE по бизнес-ключам; constraints создаются
при инициализации. Ребро REFERENCES пишется через MATCH целевого акта, поэтому
ссылки на акты вне корпуса физически не могут появиться в графе.
"""

from typing import Any

from neo4j import AsyncGraphDatabase, AsyncManagedTransaction

from app.ingestion.ontology import act_properties
from app.ingestion.parser import ParsedAct


class Neo4jWriter:
    """Записывает акты и связи онтологии в Neo4j идемпотентными MERGE-батчами."""

    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    async def close(self) -> None:
        await self._driver.close()

    async def ensure_schema(self) -> None:
        """Constraints по ключам MERGE'а (idempotent)."""
        statements = (
            "CREATE CONSTRAINT act_id IF NOT EXISTS FOR (a:Act) REQUIRE a.id IS UNIQUE",
            "CREATE CONSTRAINT authority_name IF NOT EXISTS "
            "FOR (a:Authority) REQUIRE a.name IS UNIQUE",
            "CREATE CONSTRAINT topic_name IF NOT EXISTS "
            "FOR (t:Topic) REQUIRE t.name IS UNIQUE",
            "CREATE CONSTRAINT concept_name IF NOT EXISTS "
            "FOR (c:Concept) REQUIRE c.name IS UNIQUE",
        )
        async with self._driver.session() as session:
            for stmt in statements:
                await session.run(stmt)

    async def upsert_act(
        self,
        act: ParsedAct,
        *,
        clearance: str,
        topics: list[str],
        ref_ids: list[str],
        concepts: list[str] | None = None,
    ) -> None:
        """Пишет узел Act и все его детерминированные связи одним прогоном."""
        props = act_properties(act, clearance)  # type: ignore[arg-type]
        async with self._driver.session() as session:
            await session.execute_write(_merge_act_tx, props, act.authority or None)
            if topics:
                await session.execute_write(_merge_topics_tx, act.id, topics)
            if ref_ids:
                await session.execute_write(_merge_references_tx, act.id, ref_ids)
            if concepts:
                await session.execute_write(_merge_concepts_tx, act.id, concepts)

    async def clear_all(self) -> None:
        """Только для тестов/переиндексации: полная очистка графа."""
        async with self._driver.session() as session:
            await session.run("MATCH (n) DETACH DELETE n")

    async def counts_by_label(self) -> dict[str, int]:
        """Счётчики узлов по меткам (для smoke/тестов)."""
        query = (
            "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS cnt "
            "ORDER BY label"
        )
        async with self._driver.session() as session:
            result = await session.run(query)
            rows: list[dict[str, Any]] = await result.data()
        return {str(row["label"]): int(row["cnt"]) for row in rows}


async def _merge_act_tx(
    tx: AsyncManagedTransaction, props: dict[str, Any], authority: str | None
) -> None:
    await tx.run(
        """
        MERGE (a:Act {id: $id})
        SET a += $props
        """,
        id=props["id"],
        props=props,
    )
    if authority:
        await tx.run(
            """
            MATCH (a:Act {id: $id})
            MERGE (au:Authority {name: $authority})
            MERGE (a)-[:ISSUED_BY]->(au)
            """,
            id=props["id"],
            authority=authority,
        )


async def _merge_topics_tx(tx: AsyncManagedTransaction, act_id: str, topics: list[str]) -> None:
    await tx.run(
        """
        MATCH (a:Act {id: $act_id})
        UNWIND $topics AS topic
        MERGE (t:Topic {name: topic})
        MERGE (a)-[:HAS_TOPIC]->(t)
        """,
        act_id=act_id,
        topics=topics,
    )


async def _merge_references_tx(
    tx: AsyncManagedTransaction, act_id: str, ref_ids: list[str]
) -> None:
    await tx.run(
        """
        MATCH (a:Act {id: $act_id})
        UNWIND $refs AS ref_id
        MATCH (b:Act {id: ref_id})
        MERGE (a)-[:REFERENCES]->(b)
        """,
        act_id=act_id,
        refs=ref_ids,
    )


async def _merge_concepts_tx(tx: AsyncManagedTransaction, act_id: str, concepts: list[str]) -> None:
    await tx.run(
        """
        MATCH (a:Act {id: $act_id})
        UNWIND $concepts AS concept
        MERGE (c:Concept {name: concept})
        MERGE (a)-[:MENTIONS]->(c)
        """,
        act_id=act_id,
        concepts=concepts,
    )
