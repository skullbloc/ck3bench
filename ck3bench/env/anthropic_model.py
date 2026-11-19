"""An optional `Model` adapter for Anthropic's Messages API (agent.py's protocol).

Thin on purpose: it translates the neutral messages to the Messages API and the
reply back, and nothing else. The model id is the caller's (there is no
default); so are `max_tokens` and any further request fields (`request`, e.g.
{"output_config": {"effort": "medium"}}), passed through unchanged. The
`anthropic` package is imported only when no client is handed in, and its
absence is a typed refusal. NEVER CALLED IN THIS PACKAGE'S TESTS: they hand in a
stub client, so nothing here has been run against the live API.

Translation:
  system messages   -> the request's `system`
  user              -> {"role": "user", "content": text}
  assistant         -> its `raw` content blocks unchanged when present (thinking
                       blocks must go back as they came), else text + tool_use blocks
  tool (consecutive)-> ONE user message of tool_result blocks (parallel calls'
                       results belong together)
  tool specs        -> {"name", "description", "input_schema"}
  reply             -> text blocks joined, tool_use blocks as tool calls, usage
                       as reported (input/output and cache token counts), stop_reason
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from .agent import ModelReply, ModelToolCall
from .errors import EnvRefusal

R_MODEL_UNAVAILABLE = "model_unavailable"
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def _get(o: Any, k: str) -> Any:
    return o.get(k) if isinstance(o, Mapping) else getattr(o, k, None)


def _block_dict(b: Any) -> dict:
    if isinstance(b, Mapping):
        return dict(b)
    dump = getattr(b, "model_dump", None)
    if dump is None:
        raise TypeError(f"cannot replay a content block of type {type(b).__name__}")
    return dump(exclude_none=True)


def to_request(messages: list[dict], tools: list[dict]) -> dict[str, Any]:
    """The Messages API request body (without model / max_tokens) for neutral messages."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    out: list[dict] = []
    for m in messages:
        role = m["role"]
        if role == "system":
            continue
        if role == "user":
            out.append({"role": "user", "content": m["content"]})
        elif role == "assistant":
            if m.get("raw"):
                content = [_block_dict(b) for b in m["raw"]]
            else:
                content = ([{"type": "text", "text": m["content"]}] if m.get("content") else []) + [
                    {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["arguments"]}
                    for c in m.get("tool_calls") or []]
            out.append({"role": "assistant", "content": content})
        elif role == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"],
                     **({"is_error": True} if m.get("is_error") else {})}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                    and all(b.get("type") == "tool_result" for b in out[-1]["content"]):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        else:
            raise ValueError(f"unknown message role {role!r}")
    req: dict[str, Any] = {"messages": out,
                           "tools": [{"name": t["name"], "description": t.get("description", ""),
                                      "input_schema": t.get("inputSchema") or {"type": "object"}} for t in tools]}
    if system:
        req["system"] = system
    return req


def from_response(resp: Any) -> ModelReply:
    blocks = list(_get(resp, "content") or [])
    text = "".join(_get(b, "text") or "" for b in blocks if _get(b, "type") == "text")
    calls = [ModelToolCall(_get(b, "id"), _get(b, "name"), _get(b, "input"))
             for b in blocks if _get(b, "type") == "tool_use"]
    u = _get(resp, "usage")
    usage = {k: _get(u, k) for k in USAGE_KEYS if isinstance(_get(u, k), int)} if u is not None else {}
    return ModelReply(text=text, tool_calls=calls, usage=usage or None, stop=_get(resp, "stop_reason"),
                      raw=blocks)


class AnthropicModel:
    """`complete` = one `client.messages.create` call."""

    def __init__(self, model: str, *, max_tokens: int, client: Any = None,
                 request: Optional[Mapping[str, Any]] = None):
        if not isinstance(model, str) or not model:
            raise EnvRefusal(R_MODEL_UNAVAILABLE, "the model id is the caller's to name", "model")
        if client is None:
            try:
                import anthropic
            except ImportError as e:
                raise EnvRefusal(R_MODEL_UNAVAILABLE, f"the anthropic package is not installed: {e}",
                                 "model") from None
            client = anthropic.Anthropic()
        self.model, self.max_tokens, self.client = model, max_tokens, client
        self.request = dict(request or {})

    def complete(self, messages: list[dict], tools: list[dict]) -> ModelReply:
        body = {**self.request, **to_request(messages, tools), "model": self.model, "max_tokens": self.max_tokens}
        return from_response(self.client.messages.create(**body))
