"""AI Provider abstraction layer."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import Message
from zhipuai import ZhipuAI

from src.config import settings

logger = logging.getLogger(__name__)


class AIProvider(ABC):
    """Abstract AI provider interface."""

    @abstractmethod
    async def create_message(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Message:
        """Create AI message."""
        pass


class AnthropicProvider(AIProvider):
    """Anthropic Claude provider."""

    def __init__(self) -> None:
        self.client = AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value()
        )
        logger.info("Initialized Anthropic provider with model: %s", settings.model)

    async def create_message(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Message:
        """Create message using Anthropic API."""
        return await self.client.messages.create(
            model=settings.model,
            max_tokens=settings.max_tokens,
            system=system,
            messages=messages,
            tools=tools or [],
            **kwargs,
        )


class ZAIProvider(AIProvider):
    """Z.AI (ZhipuAI) provider."""

    def __init__(self) -> None:
        if not settings.zai_api_key:
            raise ValueError("ZAI_API_KEY not set in environment")
        
        self.client = ZhipuAI(api_key=settings.zai_api_key.get_secret_value())
        logger.info("Initialized Z.AI provider with model: %s", settings.zai_model)

    async def create_message(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Message:
        """Create message using Z.AI API."""
        # Convert Anthropic format to Z.AI format
        zai_messages = self._convert_messages_to_zai(system, messages)
        
        # Call Z.AI API (synchronous)
        response = self.client.chat.completions.create(
            model=settings.zai_model,
            messages=zai_messages,
        )
        
        # Convert Z.AI response to Anthropic format
        return self._convert_response_to_anthropic(response)

    def _convert_messages_to_zai(
        self,
        system: str,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """Convert Anthropic messages to Z.AI format."""
        zai_messages = []
        
        # Add system message first
        if system:
            zai_messages.append({"role": "system", "content": system})
        
        # Convert messages
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            
            # Handle content (can be string or list)
            if isinstance(content, list):
                # Extract text from content blocks
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                content_str = "\n".join(text_parts)
            else:
                content_str = content or ""
            
            zai_messages.append({"role": role, "content": content_str})
        
        return zai_messages

    def _convert_response_to_anthropic(self, response: Any) -> Message:
        """Convert Z.AI response to Anthropic Message format."""
        # Extract content from Z.AI response
        choice = response.choices[0]
        content_text = choice.message.content
        
        # Create Anthropic-compatible Message object
        # This is a simplified version - real implementation needs proper Message object
        from dataclasses import dataclass, field
        from typing import Literal
        
        @dataclass
        class SimpleMessage:
            id: str
            type: Literal["message"] = "message"
            role: Literal["assistant"] = "assistant"
            content: list[dict[str, Any]] = field(default_factory=list)
            model: str = ""
            stop_reason: str | None = None
            usage: dict[str, Any] = field(default_factory=dict)
        
        msg = SimpleMessage(
            id=response.id,
            content=[{"type": "text", "text": content_text}],
            model=response.model,
            stop_reason="end_turn" if choice.finish_reason == "stop" else None,
            usage={
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            },
        )
        
        return msg  # type: ignore


def create_provider() -> AIProvider:
    """Create AI provider based on settings."""
    if settings.provider == "zai":
        logger.info("Creating Z.AI provider")
        return ZAIProvider()
    else:
        logger.info("Creating Anthropic provider (default)")
        return AnthropicProvider()
