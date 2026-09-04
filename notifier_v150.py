"""Watch-A-Launch v1.5.0 entrypoint with Space Jellyfish predictions.

The existing notifier remains the core scheduler. This wrapper adds the
solar-geometry predictor to the public feed and to launch notifications while
keeping the established scheduling/state behavior intact.
"""

from __future__ import annotations

import jellyfish
import launch_source
import notifier as core

_ORIGINAL_GET_LAUNCHES = launch_source.get_launches
_ORIGINAL_TO_DICT = launch_source.Launch.to_dict
_ORIGINAL_NOTIFICATION_SPEC = core.notification_spec
_ORIGINAL_SEND_NTFY = core.send_ntfy

_PREDICTED_LAUNCHES: list[tuple[object, dict]] = []


def _prediction_for_launch(launch) -> dict:
    prediction = jellyfish.predict_launch(
        getattr(launch, "launch_time_utc", None),
        getattr(launch, "location_code", None),
    )
    setattr(launch, "_jellyfish_prediction", prediction)
    return prediction


def _patched_get_launches():
    launches, source = _ORIGINAL_GET_LAUNCHES()
    _PREDICTED_LAUNCHES.clear()

    for launch in launches:
        prediction = _prediction_for_launch(launch)
        _PREDICTED_LAUNCHES.append((launch, prediction))

    return launches, source


def _patched_to_dict(self):
    data = _ORIGINAL_TO_DICT(self)
    prediction = getattr(self, "_jellyfish_prediction", None)

    if not isinstance(prediction, dict):
        prediction = _prediction_for_launch(self)

    data["jellyfish"] = prediction
    return data


def _jellyfish_line(prediction: dict | None) -> str:
    if not isinstance(prediction, dict) or not prediction.get("likely"):
        return ""

    return "🌅 There's going to be a space jellyfish! (weather permitting)"


def _append_jellyfish_line(message: str, prediction: dict | None) -> str:
    line = _jellyfish_line(prediction)

    if not line or "space jellyfish" in message.lower():
        return message

    return f"{message}\n{line}"


def _prediction_for_message(message: str) -> dict | None:
    message_lower = message.lower()

    for launch, prediction in _PREDICTED_LAUNCHES:
        if not prediction.get("likely"):
            continue

        mission = str(getattr(launch, "mission", "") or "").strip().lower()
        rocket = str(getattr(launch, "rocket", "") or "").strip().lower()

        if mission and mission in message_lower:
            return prediction

        if rocket and mission and rocket in message_lower and mission in message_lower:
            return prediction

    return None


def _patched_notification_spec(launch, event_kind: str):
    spec = _ORIGINAL_NOTIFICATION_SPEC(launch, event_kind)

    if spec is None:
        return None

    prediction = getattr(launch, "_jellyfish_prediction", None)
    spec = dict(spec)
    spec["message"] = _append_jellyfish_line(
        spec["message"],
        prediction,
    )
    return spec


def _patched_send_ntfy(
    topic: str,
    title: str,
    message: str,
    priority: str = "default",
    tags: str = "rocket",
) -> bool:
    prediction = _prediction_for_message(message)
    message = _append_jellyfish_line(message, prediction)

    return _ORIGINAL_SEND_NTFY(
        topic,
        title,
        message,
        priority,
        tags,
    )


launch_source.get_launches = _patched_get_launches
launch_source.Launch.to_dict = _patched_to_dict
core.notification_spec = _patched_notification_spec
core.send_ntfy = _patched_send_ntfy


if __name__ == "__main__":
    core.main()
