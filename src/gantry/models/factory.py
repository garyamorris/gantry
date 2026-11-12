"""Chat models: offline (default) or a real provider. The ENGINE proposes and enforces; models WRITE and JUDGE.

Two seams, and every model call in the app goes through one of them:

* `voice(persona, draft)`     free text, token-streamed (the editor's note, host messages, recaps, lessons)
* `structured(schema, ...)`   a typed decision (a poll's wording, the editor's picks)

Both hand the model a deterministic DRAFT that the engine already produced and that is known to be valid. Offline,
the model echoes it back (so streaming, structured output and every graph path work with no keys). Online, the
model improves on it. Either way the result is validated by pure code and the draft is the fallback, so a slow,
refusing or misbehaving provider can never stop a live broadcast: `STATS` counts how often that happened.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables.config import merge_configs
from pydantic import BaseModel

from gantry.models.offline import OfflineChatModel, Rule, Script, anywhere, struct
from gantry.settings import Settings, get_settings

log = logging.getLogger("gantry.models")
STATS = {"calls": 0, "changed": 0, "fallbacks": 0}  # surfaced by /api/boot
DRAFT_RE = re.compile(r"<<draft>>(.*?)<</draft>>", re.S)
T = TypeVar("T", bound=BaseModel)


def _draft_of(call: Any) -> str:
    m = DRAFT_RE.search(call.human)
    return m.group(1).strip() if m else "..."


def _echo(call: Any) -> Any:
    """Offline behaviour: return the engine's draft, as text or (when a schema is bound) as the structured value."""
    draft = _draft_of(call)
    if call.schema is not None:
        try:
            return struct(json.loads(draft))
        except json.JSONDecodeError:
            return struct({})
    return draft


ECHO_SCRIPT = Script([Rule("echo_draft", anywhere("<<draft>>"), _echo)])


@lru_cache(maxsize=8)
def _anthropic(name: str, timeout: float, max_tokens: int, effort: str, api_key: str, workspace_id: str | None = None) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic  # lazy: offline mode never needs the key

    # No temperature/top_p (rejected by current Claude models). Thinking is adaptive and cannot be switched off on
    # Opus 5.5, so depth is set with `output_config.effort`: low by default, because this is live.
    headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
    return ChatAnthropic(model=name, api_key=api_key, timeout=timeout, max_retries=1, max_tokens=max_tokens, output_config={"effort": effort}, default_headers=headers)


@lru_cache(maxsize=8)
def _openai(name: str, timeout: float, max_tokens: int, api_key: str) -> BaseChatModel:
    from langchain_openai import ChatOpenAI  # lazy: offline mode never imports it

    return ChatOpenAI(model=name, api_key=api_key, timeout=timeout, max_retries=1, max_completion_tokens=max_tokens)


def get_chat_model(role: str = "fast", *, script: Script | None = None, settings: Settings | None = None) -> BaseChatModel:
    """`role` is `smart` (judgement: poll wording, the editor's picks) or `fast` (voice lines)."""
    s = settings or get_settings()
    provider = s.resolved_provider()
    if provider == "offline":
        return OfflineChatModel(role=role, script=script or ECHO_SCRIPT, token_delay_s=s.offline_token_delay_s)
    if provider == "openai":
        okey = s.openai_api_key
        assert okey is not None, "GANTRY_PROVIDER=openai needs OPENAI_API_KEY"
        return _openai(s.model_name(role), s.llm_timeout_s, s.openai_max_tokens, okey.get_secret_value())
    key = s.anthropic_api_key
    assert key is not None, "GANTRY_PROVIDER=anthropic needs ANTHROPIC_API_KEY"
    return _anthropic(s.model_name(role), s.llm_timeout_s, s.llm_max_tokens, s.llm_effort, key.get_secret_value(), s.anthropic_workspace_id)


async def voice(persona: str, draft: str, *, style: str = "", role: str = "fast", config: Any = None, tag: str | None = None, words: int = 45) -> str:
    """Give `draft` a voice. Always returns text; never raises (falls back to the draft).

    `tag` labels the model call (`voice:<tag>`) so `messages` stream chunks can be attributed in the UI.
    """
    try:
        if tag:
            config = merge_configs(config, {"tags": [f"voice:{tag}"]})
        system = (
            f"You are {persona}, working a live sports broadcast. Rewrite the DRAFT in your own voice in at most {words} words. "
            f"Keep every fact, name, minute and number from the draft and add none of your own. Reply with the line only. {style}"
        ).strip()
        msg = await get_chat_model(role).ainvoke([SystemMessage(system), HumanMessage(f"<<draft>>{draft}<</draft>>")], config=config)
        text = str(msg.text).strip()
        STATS["calls"] += 1
        if text and text != draft:
            STATS["changed"] += 1
        return text or draft
    except Exception as exc:  # a flaky provider must never break a broadcast, but it must not fail SILENTLY either
        STATS["fallbacks"] += 1
        log.warning("voice() fell back to the draft: %s: %s", type(exc).__name__, str(exc)[:200])
        return draft


async def structured(schema: type[T], *, system: str, facts: dict[str, Any], draft: T, role: str = "smart", config: Any = None) -> tuple[T, str]:
    """Ask the model for a `schema` value, given `facts` and the engine's `draft`. Returns (value, source).

    `source` is "model" or "fallback" (the draft, on any error, refusal or invalid output). The call is tagged
    `nostream` so its JSON never reaches a user-facing token stream.
    """
    try:
        config = merge_configs(config, {"tags": ["nostream"]})
        model = get_chat_model(role).with_structured_output(schema, method="json_schema")
        human = (
            f"FACTS (the only facts you may use):\n{json.dumps(facts, ensure_ascii=False)}\n\n"
            f"A valid first draft, to improve on or keep:\n<<draft>>{draft.model_dump_json()}<</draft>>"
        )
        out = await model.ainvoke([SystemMessage(system), HumanMessage(human)], config=config)
        value = out if isinstance(out, schema) else schema.model_validate(out)
        STATS["calls"] += 1
        if value != draft:
            STATS["changed"] += 1
        return value, "model"
    except Exception as exc:
        STATS["fallbacks"] += 1
        log.warning("structured(%s) fell back to the draft: %s: %s", schema.__name__, type(exc).__name__, str(exc)[:200])
        return draft, "fallback"
