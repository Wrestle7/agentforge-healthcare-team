"""Per-answer UI metadata stored alongside the existing serialized AI message.

No new table or browser PHI cache is needed. Only our explicitly named metadata
is exposed to the UI, and it is removed from copies sent to the model.
"""
from copy import deepcopy

from langchain_core.messages import AIMessage, BaseMessage

DETAILS_KEY = "agentforge_response_details"
DETAIL_FIELDS = ("tool_calls", "confidence", "disclaimers", "verification", "latency_ms", "token_usage")


def with_response_details(message: AIMessage, response: dict) -> AIMessage:
    details = {key: deepcopy(response[key]) for key in DETAIL_FIELDS if key in response}
    return message.model_copy(update={
        "response_metadata": {
            **message.response_metadata,
            DETAILS_KEY: {"version": 1, "details": details},
        },
    })


def get_response_details(message: BaseMessage) -> dict | None:
    if not isinstance(message, AIMessage):
        return None
    saved = message.response_metadata.get(DETAILS_KEY)
    if not isinstance(saved, dict) or saved.get("version") != 1:
        return None
    details = saved.get("details")
    if not isinstance(details, dict):
        return None
    return {key: deepcopy(details[key]) for key in DETAIL_FIELDS if key in details} or None


def messages_for_model(messages: list) -> list:
    """Do not feed UI details back into model requests; keep persisted originals."""
    return [
        message.model_copy(update={"response_metadata": {
            key: value for key, value in message.response_metadata.items() if key != DETAILS_KEY
        }}) if DETAILS_KEY in message.response_metadata else message
        for message in messages
    ]
