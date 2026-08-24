from fastapi import FastAPI, Response

app = FastAPI(
    title="GraphRAG Platform API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "backend", "version": "0.1.0"}


@app.get("/metrics")
def metrics() -> Response:
    body = (
        "# HELP app_up Backend availability flag.\n"
        "# TYPE app_up gauge\n"
        "app_up 1\n"
    )
    return Response(content=body, media_type="text/plain; version=0.0.4")
