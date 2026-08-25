"""CLI ingestion: python -m app.ingestion [--dir PATH] [--no-concepts] [--concepts-only]

Запускается с хоста (env из infra/.env подтягивается автоматически) или из
контейнера backend (corpus смонтирован в APP_INGEST_CORPUS_DIR).
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from app.config import Settings, load_env_file_into_environ
from app.ingestion.pipeline import run_ingestion


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.ingestion", description="Ingestion RusLawOD")
    parser.add_argument(
        "--dir", type=Path, default=None, help="Каталог XML (по умолчанию APP_INGEST_CORPUS_DIR)"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--no-concepts", action="store_true", help="Только детерминированная часть графа"
    )
    mode.add_argument(
        "--concepts", action="store_true", help="Принудительно включить LLM-экстракцию"
    )
    args = parser.parse_args(argv)

    load_env_file_into_environ()
    settings = Settings(_env_file=None)
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s: %(message)s")

    corpus_dir: Path = args.dir or settings.ingest_corpus_dir
    extract = True if args.concepts else False if args.no_concepts else None

    stats = asyncio.run(
        run_ingestion(settings, corpus_dir, extract_concepts=extract)
    )
    print(json.dumps(stats.__dict__, ensure_ascii=False, indent=2, default=list))
    return 1 if stats.parse_errors and stats.acts_parsed == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
