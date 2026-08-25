"""Добавляет папку observability в Postman-коллекцию (этап 7). Одноразовый скрипт."""

import json

p = "tests/postman/collection.json"
with open(p, encoding="utf-8") as f:
    c = json.load(f)

c["info"]["name"] = "GraphRAG Platform - Stage 7 (auth + ingest + chat + RBAC + observability)"
c["info"]["description"] = c["info"]["description"].replace(
    "сквозной RBAC-сценарий (этап 6)",
    "сквозной RBAC-сценарий (этап 6), observability: /metrics и корреляция заголовков (этап 7)",
)

metrics_exec = [
    'pm.test("200", () => pm.response.to.have.status(200));',
    'pm.test("prometheus text", () => pm.expect(pm.response.headers.get("Content-Type")).to.include("text/plain"));',
    'pm.test("families present", () => {',
    '  const b = pm.response.text();',
    '  ["http_requests_total", "http_request_duration_seconds", "chat_status_total",',
    '   "guardrail_events_total", "graph_node_duration_seconds"].forEach(f => pm.expect(b).to.include(f));',
    '});',
    'pm.test("no /metrics self-scrape series", () => {',
    '  pm.expect(pm.response.text()).to.not.include(\'route="/metrics"\');',
    '});',
]
echo_prereq = [
    'const hex = () => Math.floor(Math.random() * 16).toString(16);',
    'let tid = "a1b2c3d4e5f60718";',
    'for (let i = 0; i < 16; i++) tid += hex();',
    'pm.collectionVariables.set("trace_id_probe", tid);',
    'let rid = ""; for (let i = 0; i < 32; i++) rid += hex();',
    'pm.collectionVariables.set("request_id_probe", rid);',
]
echo_test = [
    'pm.test("200", () => pm.response.to.have.status(200));',
    'const tid = pm.collectionVariables.get("trace_id_probe");',
    'pm.test("X-Trace-Id echoed as sent", () => pm.expect(pm.response.headers.get("X-Trace-Id")).to.eql(tid));',
    'pm.test("X-Request-Id echoed as sent", () => pm.expect(pm.response.headers.get("X-Request-Id")).to.eql(pm.collectionVariables.get("request_id_probe")));',
]

obs = {
    "name": "observability",
    "item": [
        {
            "name": "Metrics endpoint",
            "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": metrics_exec}}],
            "request": {"method": "GET", "url": "{{baseUrl}}/metrics"},
        },
        {
            "name": "Trace headers echo",
            "event": [
                {"listen": "prerequest", "script": {"type": "text/javascript", "exec": echo_prereq}},
                {"listen": "test", "script": {"type": "text/javascript", "exec": echo_test}},
            ],
            "request": {
                "method": "GET",
                "header": [
                    {"key": "X-Trace-Id", "value": "{{trace_id_probe}}"},
                    {"key": "X-Request-Id", "value": "{{request_id_probe}}"},
                ],
                "url": "{{baseUrl}}/health",
            },
        },
    ],
}

c["item"] = [i for i in c["item"] if i["name"] != "observability"]
c["item"].append(obs)

with open(p, "w", encoding="utf-8") as f:
    json.dump(c, f, ensure_ascii=False, indent=2)
print("OK, folders:", [i["name"] for i in c["item"]])
