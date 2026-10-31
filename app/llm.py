"""LLM-слой (провайдеры): Claude (Anthropic API) или любая модель с OpenAI-совместимым API,
например локальная через Ollama. Остальной код работает только с нейтральным форматом.

Нейтральная история диалога:
  {"role": "user", "text": str}
  {"role": "assistant", "text": str, "tool_calls": [ToolCall, ...]}
  {"role": "tool", "results": [{"id": str, "name": str, "content": str}, ...]}
"""
import json
import os
import uuid
from dataclasses import dataclass, field


class LLMError(Exception):
    """Ошибка обращения к модели с понятным пользователю текстом."""


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Turn:
    text: str = ""
    tool_calls: list = field(default_factory=list)


# ---------- Anthropic (Claude) ----------

def to_anthropic_messages(messages):
    out = []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["text"]})
        elif m["role"] == "assistant":
            content = []
            if m.get("text"):
                content.append({"type": "text", "text": m["text"]})
            content += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.args}
                        for c in m.get("tool_calls", [])]
            out.append({"role": "assistant", "content": content})
        else:  # tool
            out.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"]}
                for r in m["results"]]})
    return out


class AnthropicProvider:
    def __init__(self, model):
        import anthropic
        self._sdk = anthropic
        self.model = model
        self.client = anthropic.Anthropic()  # ключ берётся из ANTHROPIC_API_KEY

    def complete(self, system, messages, tools):
        try:
            resp = self.client.messages.create(
                model=self.model, max_tokens=1024, system=system,
                tools=[{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                       for t in tools],
                messages=to_anthropic_messages(messages))
        except self._sdk.APIConnectionError as e:
            raise LLMError("Не удалось подключиться к API Anthropic. Проверьте интернет и доступность сервиса из вашего региона.") from e
        except self._sdk.AuthenticationError as e:
            raise LLMError("Неверный или отсутствует ANTHROPIC_API_KEY.") from e
        except self._sdk.APIStatusError as e:
            raise LLMError(f"Ошибка API Anthropic ({e.status_code}): {e.message}") from e
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
        return Turn(text, calls)


# ---------- OpenAI-совместимый API (Ollama, LM Studio, Groq, OpenRouter и т.д.) ----------

def to_openai_messages(system, messages):
    out = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["text"]})
        elif m["role"] == "assistant":
            msg = {"role": "assistant", "content": m.get("text") or None}
            if m.get("tool_calls"):
                msg["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": json.dumps(c.args, ensure_ascii=False)}}
                    for c in m["tool_calls"]]
            out.append(msg)
        else:  # tool
            out += [{"role": "tool", "tool_call_id": r["id"], "content": r["content"]} for r in m["results"]]
    return out


def _parse_args(raw):
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}  # битый JSON от маленькой модели: инструмент вернёт ошибку, модель повторит
    return data if isinstance(data, dict) else {}


class OpenAICompatProvider:
    def __init__(self, model, base_url, api_key):
        import openai
        self._sdk = openai
        self.model = model
        self.base_url = base_url
        self.client = openai.OpenAI(base_url=base_url, api_key=api_key or "ollama")

    def complete(self, system, messages, tools):
        try:
            resp = self.client.chat.completions.create(
                model=self.model, temperature=0.2,
                tools=[{"type": "function", "function": {
                    "name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
                    for t in tools],
                messages=to_openai_messages(system, messages))
        except self._sdk.APIConnectionError as e:
            raise LLMError(
                f"Нет связи с {self.base_url}. Если это Ollama: запущена ли она (`ollama serve`) "
                f"и скачана ли модель (`ollama pull {self.model}`)?") from e
        except self._sdk.APIStatusError as e:
            raise LLMError(f"Ошибка модели ({e.status_code}): {e.message}") from e
        msg = resp.choices[0].message
        calls = [ToolCall(tc.id or f"call_{uuid.uuid4().hex[:12]}", tc.function.name, _parse_args(tc.function.arguments))
                 for tc in (msg.tool_calls or [])]
        return Turn(msg.content or "", calls)


def create_provider():
    """LLM_PROVIDER=anthropic (по умолчанию) или ollama (любой OpenAI-совместимый сервер)."""
    name = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
    if name == "anthropic":
        return AnthropicProvider(os.getenv("MODEL", "claude-sonnet-5-5"))
    if name in ("ollama", "openai"):
        return OpenAICompatProvider(
            os.getenv("MODEL", "llama3.1:8b"),
            os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1"),
            os.getenv("OPENAI_API_KEY", "ollama"))
    raise LLMError(f"Неизвестный LLM_PROVIDER={name!r}. Допустимо: anthropic, ollama")
