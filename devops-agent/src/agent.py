"""DevOps Agent with Agentic Loop.

Provides the main agent class that orchestrates Claude API calls,
tool execution, and conversation management.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from anthropic import AsyncAnthropic
from src.ai_provider import create_provider
from anthropic.types import Message, ToolUseBlock

from src.config import settings
from src.security import SecurityGuard
from src.state import StateManager
from src.tools import ToolRegistry, ToolResult

if TYPE_CHECKING:
    from src.ssh_manager import SSHManager

logger = logging.getLogger(__name__)

SSH_SYSTEM_PROMPT = """Ты DevOps агент проекта Biotact MiniApp. Ты живёшь на сервере внутри проекта.

## Где ты находишься
Сервер: biotact-miniapp (localhost)
Проект: /var/www/biotact/
Ты: /var/www/biotact/devops-agent/

## Доступные серверы

ВАЖНО: Для работы с разными серверами используй параметр host в ssh_execute!

| Сервер | Описание |
|--------|----------|
| localhost | Biotact MiniApp (по умолчанию) |
| oltinkey | Oltin Server (удалённый) |

Примеры вызова ssh_execute:
- Команда на localhost: ssh_execute(command="df -h")
- Команда на oltinkey: ssh_execute(command="df -h", host="oltinkey")

НИКОГДА не выполняй команду "ssh oltinkey" - это неправильно!
Правильно: ssh_execute(command="hostname", host="oltinkey")

## Структура проекта Biotact (localhost)

/var/www/biotact/
├── packages/
│   ├── api/          # FastAPI backend (Python, uvicorn)
│   ├── bot/          # Telegram бот магазина (aiogram)
│   └── frontend/     # React Mini App
├── backups/          # Бэкапы
├── devops-agent/     # Ты здесь!
└── ecosystem.config.js

## Сервисы PM2 (localhost)
- api — FastAPI на порту 3001
- bot — Telegram бот магазина
- devops-agent — это ты

## База данных PostgreSQL (localhost)
Host: localhost:5432
Database: biotact_db
User: biotact
Password: biotact_secure_2024

Таблицы: users, orders, order_items, products, categories, banners, leads, family_members, admin_users, settings

Запрос к БД:
PGPASSWORD=biotact_secure_2024 psql -h localhost -U biotact -d biotact_db -c "SELECT ..."

## Полезные команды (localhost)

PM2:
pm2 list
pm2 logs api --lines 50
pm2 restart api

PostgreSQL:
PGPASSWORD=biotact_secure_2024 psql -h localhost -U biotact -d biotact_db -c "SELECT id, total_amount, status FROM orders ORDER BY created_at DESC LIMIT 10;"

Логи:
tail -50 /root/.pm2/logs/api-error.log

## Правила работы

1. ФОРМАТ ОТВЕТОВ
   - НЕ используй markdown: * ** _ ~ 
   - Пиши простым текстом
   - Для списков используй - или цифры

2. РАБОТА С СЕРВЕРАМИ
   - По умолчанию команды на localhost
   - Для oltinkey указывай host="oltinkey"
   - НЕ используй команду ssh внутри ssh_execute!

3. Последовательность: GATHER -> ANALYZE -> ACT -> VERIFY

4. Безопасность
   - Не удаляй данные без подтверждения
   - Не показывай пароли в ответах

5. Будь кратким и полезным

## Инструменты
- ssh_execute(command, host) — выполнить команду на сервере
- ssh_list_hosts — показать доступные серверы
"""


@dataclass(slots=True)
class AgentResult:
    """Result of agent execution."""

    success: bool
    response: str
    tools_used: list[str] = field(default_factory=list)
    iterations: int = 0
    duration_seconds: float = 0.0
    error: str | None = None


class DevOpsAgent:
    """DevOps Agent with Agentic Loop."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        state_manager: StateManager,
        security_guard: SecurityGuard,
        ssh_manager: SSHManager | None = None,
        client: AsyncAnthropic | None = None,
    ) -> None:
        self._provider = create_provider()
        self._client = client or self._provider.client  # For backward compatibility
        self._tools = tool_registry
        self._state = state_manager
        self._security = security_guard
        self._ssh = ssh_manager
        self._max_iterations = settings.max_iterations
        self._system_prompt = SSH_SYSTEM_PROMPT

    async def run(
        self,
        user_id: int,
        query: str,
        session_id: str | None = None,
        model: str | None = None,
    ) -> AgentResult:
        active_model = model or settings.model
        start_time = time.monotonic()
        tools_used: list[str] = []

        if not self._security.is_user_allowed(user_id):
            self._security.audit_log(
                user_id, "agent_run", query, allowed=False, warnings=["Unauthorized"]
            )
            return AgentResult(
                success=False,
                response="",
                error="User not authorized",
                duration_seconds=time.monotonic() - start_time,
            )

        try:
            session = await self._get_or_create_session(user_id, session_id)
            messages = await self._build_messages(session.id, query)
            await self._state.add_message(session.id, "user", query)
            tool_schemas = self._tools.get_all_schemas()

            iterations = 0
            final_response = ""

            while iterations < self._max_iterations:
                iterations += 1
                logger.info(f"Agent iteration {iterations}/{self._max_iterations}, model={active_model}")

                response = await self._call_claude(messages, tool_schemas, active_model)
                text_response, tool_calls = self._parse_response(response)

                if text_response:
                    final_response = text_response

                if response.stop_reason == "end_turn":
                    break

                if response.stop_reason == "max_tokens":
                    break

                if tool_calls:
                    messages.append({"role": "assistant", "content": response.content})
                    tool_results = await self._execute_tools(tool_calls, user_id, tools_used)
                    messages.append({"role": "user", "content": tool_results})
                else:
                    break

            if iterations >= self._max_iterations and response.stop_reason != "end_turn":
                if not final_response:
                    final_response = "Достигнут лимит шагов. Попробуй упростить запрос."

            if final_response:
                await self._state.add_message(session.id, "assistant", final_response)

            duration = time.monotonic() - start_time

            await self._state.save_incident(
                user_id=user_id,
                query=query,
                resolution=final_response,
                tools_used=tools_used,
                success=True,
                duration_seconds=duration,
            )

            self._security.audit_log(user_id, "agent_run", query, allowed=True)

            return AgentResult(
                success=True,
                response=final_response,
                tools_used=tools_used,
                iterations=iterations,
                duration_seconds=duration,
            )

        except Exception as e:
            logger.exception("Agent execution error")
            duration = time.monotonic() - start_time

            await self._state.save_incident(
                user_id=user_id,
                query=query,
                resolution=None,
                tools_used=tools_used,
                success=False,
                duration_seconds=duration,
            )

            return AgentResult(
                success=False,
                response="",
                tools_used=tools_used,
                iterations=0,
                duration_seconds=duration,
                error=str(e),
            )

    async def _get_or_create_session(self, user_id: int, session_id: str | None) -> Any:
        if session_id:
            session = await self._state.get_session(session_id)
            if session and session.status == "active":
                return session

        session = await self._state.get_active_session(user_id)
        if session:
            return session

        return await self._state.create_session(user_id)

    async def _build_messages(self, session_id: str, current_query: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        history = await self._state.get_messages(session_id, limit=20)

        for msg in history:
            if msg.role in ("user", "assistant"):
                messages.append({"role": msg.role, "content": msg.content})

        messages.append({"role": "user", "content": current_query})
        return messages

    async def _call_claude(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str,
    ) -> Message:
        # Use provider abstraction instead of direct client
        return await self._provider.create_message(
            system=self._system_prompt,
            messages=messages,
            tools=tools,
        )

    def _parse_response(self, response: Message) -> tuple[str, list[ToolUseBlock]]:
        text_parts: list[str] = []
        tool_calls: list[ToolUseBlock] = []

        for block in response.content:
            # Handle both dict (from Z.AI) and object (from Anthropic) formats
            if isinstance(block, dict):
                block_type = block.get("type")
                if block_type == "text":
                    text_parts.append(block.get("text", ""))
                elif block_type == "tool_use":
                    # Convert dict to object if needed
                    tool_calls.append(block)
            else:
                # Object format (Anthropic)
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls.append(block)

        return "\n".join(text_parts), tool_calls

    async def _execute_tools(
        self,
        tool_calls: list[ToolUseBlock],
        user_id: int,
        tools_used: list[str],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        for tool_call in tool_calls:
            tool_name = tool_call.name
            tool_input = tool_call.input
            tool_id = tool_call.id

            logger.info(f"Executing tool: {tool_name}")

            if tool_name not in tools_used:
                tools_used.append(tool_name)

            result = await self._tools.execute(tool_name, user_id=user_id, **tool_input)
            results.append(self._format_tool_result(tool_id, result))

        return results

    def _format_tool_result(self, tool_id: str, result: ToolResult) -> dict[str, Any]:
        if result.success:
            content = result.output
        else:
            content = f"Error: {result.error}" if result.error else "Unknown error"

        return {
            "type": "tool_result",
            "tool_use_id": tool_id,
            "content": content,
            "is_error": not result.success,
        }
