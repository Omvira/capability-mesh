"""Compatibility helpers for A2A Protocol 1.0 SDK/protobuf models."""

from __future__ import annotations

from typing import Any, Mapping

from google.protobuf.json_format import ParseDict

try:  # pragma: no cover - import failure is exercised by packaging environments.
    from a2a import types as a2a_types
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("capability-mesh requires the official a2a-sdk package for A2A Protocol 1.0 models") from exc

HTTP_JSON_BINDING_URI = "https://a2a-protocol.org/bindings/http-json/v1"
PROTOCOL_VERSION = "1.0"


def validate_agent_card_dict(card: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an AgentCard dict against the official a2a-sdk protobuf model."""

    ParseDict(dict(card), a2a_types.AgentCard())
    return dict(card)


def validate_send_message_response_dict(response: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a SendMessageResponse dict against the official a2a-sdk model."""

    ParseDict(dict(response), a2a_types.SendMessageResponse())
    return dict(response)


def validate_task_dict(task: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a Task dict against the official a2a-sdk protobuf model."""

    ParseDict(dict(task), a2a_types.Task())
    return dict(task)


def validate_list_tasks_response_dict(response: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a ListTasksResponse dict against the official a2a-sdk protobuf model."""

    ParseDict(dict(response), a2a_types.ListTasksResponse())
    return dict(response)


def validate_stream_response_dict(response: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a StreamResponse dict against the official a2a-sdk protobuf model."""

    ParseDict(dict(response), a2a_types.StreamResponse())
    return dict(response)


def validate_task_push_notification_config_dict(config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a TaskPushNotificationConfig dict against the official a2a-sdk model."""

    ParseDict(dict(config), a2a_types.TaskPushNotificationConfig())
    return dict(config)


def validate_list_task_push_notification_configs_response_dict(response: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a ListTaskPushNotificationConfigsResponse dict against the official a2a-sdk model."""

    ParseDict(dict(response), a2a_types.ListTaskPushNotificationConfigsResponse())
    return dict(response)
