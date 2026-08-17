"""Anthropic Claude client + tool-use loop."""
import asyncio
import inspect
import os

import anthropic

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


async def run_tool_loop(
    system_prompt: str,
    user_message: str,
    tool_schemas: list[dict],
    tool_impls: dict,
    max_turns: int = 6,
) -> str:
    """Run Claude with tool use until it produces a final text answer."""
    client = _get_client()
    messages = [{"role": "user", "content": user_message}]

    for _ in range(max_turns):
        response = await asyncio.to_thread(
            client.messages.create,
            model=MODEL,
            max_tokens=8192,
            system=system_prompt,
            tools=tool_schemas,
            messages=messages,
        )

        if response.stop_reason == "max_tokens":
            # Ran out of budget before finishing — either mid-thinking (no
            # text yet) or mid-final-answer (truncated text, e.g. a renewal
            # comparison table cut off mid-row). Either way any text present
            # is incomplete and must NOT be returned as the answer; retry as
            # a fresh turn rather than falling through to the branches below.
            continue

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                impl = tool_impls[block.name]
                result = impl(**block.input)
                if inspect.isawaitable(result):
                    result = await result
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(result),
                })
            messages.append({"role": "user", "content": tool_results})
            continue

        # Any other stop_reason (end_turn, stop_sequence, ...): a genuine
        # finish, so its text (if any) is complete and safe to return.
        text = "".join(block.text for block in response.content if block.type == "text")
        if text:
            return text
        # No text despite a "finished" stop_reason (e.g. an all-thinking,
        # no-answer turn) — retry rather than return an empty reply.

    return "I wasn't able to finish reasoning about this in time — please try rephrasing."
