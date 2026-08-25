"""Locust-профили нагрузки этапа 9: /health и /api/chat (non-stream).

Запуск через scripts/run_load_test.ps1|.sh (не вручную): профиль выбирается
env-переменной LOCUST_PROFILE=health|chat, параметры гонки - LOAD_*.

Ресурсная гигиена: фиксированный --run-time снаружи, таймаут на каждом
запросе, никакого бесконечного ожидания; чат-профиль держит мало
пользователей (LLM - узкое место, LM Studio/vLLM обслуживает очередь).
"""

import json
import os
import uuid

from locust import HttpUser, between, task

PROFILE = os.environ.get("LOCUST_PROFILE", "health")

CHAT_LOGIN_USER = os.environ.get("LOAD_CHAT_USER", "analyst")
CHAT_LOGIN_PASS = os.environ.get("LOAD_CHAT_PASS", "analyst123")
CHAT_TIMEOUT = float(os.environ.get("LOAD_CHAT_TIMEOUT", "170"))

QUESTION = os.environ.get(
    "LOAD_CHAT_QUESTION",
    "Что регулирует Гражданский процессуальный кодекс Российской Федерации? "
    "Ответь двумя предложениями.",
)


def _fire_status_event(environment, status: str, elapsed_ms: float) -> None:
    """Отдельный ряд в статистике на каждый итоговый статус чата."""
    environment.events.request.fire(
        request_type="CHAT",
        name=f"chat status={status}",
        response_time=elapsed_ms,
        response_length=0,
        exception=None,
        context={},
    )


if PROFILE == "health":

    class HealthUser(HttpUser):
        """Лёгкий профиль: только /health (без LLM и БД-конвейера)."""

        wait_time = between(0.0, 0.2)

        @task
        def health(self) -> None:
            self.client.get("/health", timeout=10)

else:

    class ChatUser(HttpUser):
        """Профиль чата: логин once, далее non-stream ходы агента.

        Сессия переиспользуется между ходами одного пользователя
        (реалистичный многоходовой диалог + меньший рост chat_sessions).
        """

        wait_time = between(1.0, 3.0)
        access_token: str | None = None
        session_id: str | None = None

        def on_start(self) -> None:
            resp = self.client.post(
                "/auth/login",
                json={"username": CHAT_LOGIN_USER, "password": CHAT_LOGIN_PASS},
                timeout=30,
                name="auth/login",
            )
            if resp.status_code == 200:
                self.access_token = resp.json().get("access_token")
            else:
                resp.failure(f"login failed: {resp.status_code}")
                self.environment.runner.quit()

        @task
        def chat_turn(self) -> None:
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "X-Trace-Id": uuid.uuid4().hex,
            }
            payload: dict[str, object] = {"message": QUESTION, "stream": False}
            if self.session_id:
                payload["session_id"] = self.session_id
            resp = self.client.post(
                "/api/chat",
                json=payload,
                headers=headers,
                timeout=CHAT_TIMEOUT,
                name="api/chat non-stream",
            )
            if resp.status_code != 200:
                return
            try:
                body = resp.json()
            except json.JSONDecodeError:
                resp.failure("non-JSON ответ чата")
                return
            status = str(body.get("status", "?"))
            _fire_status_event(self.environment, status, resp.elapsed.total_seconds() * 1000)
            sid = body.get("session_id")
            if isinstance(sid, str) and sid:
                self.session_id = sid
