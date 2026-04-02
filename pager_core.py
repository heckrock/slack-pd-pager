"""Shared Slack + PagerDuty logic (used by Azure Functions and optional Flask)."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
ALLOWED_USERS_FILE = BASE_DIR / "allowed_users.json"

PAGERDUTY_KEY = os.environ.get("PAGERDUTY_ROUTING_KEY")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
PAGERDUTY_API_URL = "https://api.pagerduty.com/oncalls"
PAGERDUTY_API_TOKEN = os.environ.get("PAGERDUTY_API_TOKEN")
PAGERDUTY_SCHEDULES = os.environ.get("PAGERDUTY_SCHEDULES")


def get_oncall_users(schedule_id, api_token):
    headers = {
        "Authorization": f"Token token={api_token}",
        "Accept": "application/vnd.pagerduty+json;version=2",
    }
    now = datetime.now(timezone.utc)
    if isinstance(schedule_id, str):
        schedule_ids = [s.strip() for s in schedule_id.split(",") if s.strip()]
    elif isinstance(schedule_id, list):
        schedule_ids = schedule_id
    else:
        raise ValueError("Invalid schedule_id format")

    params = [(("schedule_ids[]", sid)) for sid in schedule_ids]
    params.append(("include[]", "users"))
    params.append(("since", (now - timedelta(days=1)).isoformat()))
    params.append(("until", (now + timedelta(days=1)).isoformat()))

    response = requests.get(PAGERDUTY_API_URL, headers=headers, params=params)
    response.raise_for_status()

    data = response.json()
    oncalls = data.get("oncalls", [])

    users = []
    for oc in oncalls:
        start = datetime.fromisoformat(oc["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(oc["end"].replace("Z", "+00:00"))
        if start <= now <= end:
            user = oc.get("user", {})
            users.append(
                {
                    "id": user.get("id"),
                    "name": user.get("summary"),
                    "email": user.get("email"),
                }
            )
    unique_users = {u["id"]: u for u in users}.values()
    return unique_users


def load_allowed_users() -> dict[str, str]:
    try:
        with ALLOWED_USERS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        users = data.get("allowed_users", [])
        return {
            user["id"]: user.get("name", user["id"])
            for user in users
            if isinstance(user, dict) and "id" in user
        }
    except FileNotFoundError:
        logger.error("allowed_users.json not found")
        return {}
    except json.JSONDecodeError:
        logger.error("allowed_users.json is not valid JSON")
        return {}
    except Exception as exc:
        logger.error("Unexpected error loading allowed users: %s", exc)
        return {}


def verify_slack_request(raw_body: str, headers: dict[str, str]) -> bool:
    if not SLACK_SIGNING_SECRET:
        logger.error("SLACK_SIGNING_SECRET is not set")
        return False

    timestamp = headers.get("X-Slack-Request-Timestamp") or headers.get("x-slack-request-timestamp") or ""
    slack_signature = headers.get("X-Slack-Signature") or headers.get("x-slack-signature") or ""

    if not timestamp or not slack_signature:
        logger.warning("Missing Slack signature headers")
        return False

    try:
        timestamp_int = int(timestamp)
    except ValueError:
        logger.warning("Invalid Slack timestamp header")
        return False

    if abs(time.time() - timestamp_int) > 60 * 5:
        logger.warning("Slack request timestamp too old")
        return False

    basestring = f"v0:{timestamp}:{raw_body}"
    computed_signature = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode("utf-8"),
        basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed_signature, slack_signature)


def trigger_pagerduty_event(user_name: str, issue_description: str) -> requests.Response:
    summary = f"{user_name} is paging SRE for help regarding {issue_description}"

    pd_payload = {
        "routing_key": PAGERDUTY_KEY,
        "event_action": "trigger",
        "payload": {
            "summary": summary,
            "severity": "critical",
            "source": "slack-demo",
            "custom_details": {
                "requested_by": user_name,
                "issue_description": issue_description,
            },
        },
    }

    logger.info("Sending PagerDuty event: %s", pd_payload)

    response = requests.post(
        "https://events.pagerduty.com/v2/enqueue",
        json=pd_payload,
        timeout=10,
    )

    logger.info(
        "PagerDuty response status=%s body=%s",
        response.status_code,
        response.text,
    )

    response.raise_for_status()
    return response


def handle_home() -> tuple[str, int]:
    return "Slack PagerDuty app is running", 200


def handle_slack_page(raw_body: str, form: dict[str, str], headers: dict[str, str]) -> tuple[dict[str, Any], int]:
    if not verify_slack_request(raw_body, headers):
        return (
            {"response_type": "ephemeral", "text": "Request verification failed."},
            401,
        )

    user_id = form.get("user_id", "")
    issue_description = form.get("text", "").strip()

    if not user_id:
        return (
            {"response_type": "ephemeral", "text": "Missing Slack user ID in request."},
            400,
        )

    if not issue_description:
        return (
            {
                "response_type": "ephemeral",
                "text": "Please include a brief description, for example: /page-sre checkout is failing",
            },
            400,
        )

    allowed_users = load_allowed_users()
    user_name = allowed_users.get(user_id)

    if not user_name:
        return (
            {"response_type": "ephemeral", "text": "User is not allowed."},
            403,
        )

    if not PAGERDUTY_KEY:
        logger.error("PAGERDUTY_ROUTING_KEY is not set")
        return (
            {
                "response_type": "ephemeral",
                "text": "PagerDuty integration is not configured.",
            },
            500,
        )

    try:
        trigger_pagerduty_event(user_name, issue_description)
        return (
            {
                "response_type": "in_channel",
                "text": (
                    f"PagerDuty request submitted successfully by {user_name} "
                    f"for: {issue_description}."
                ),
            },
            200,
        )
    except requests.RequestException as exc:
        logger.error("Failed to trigger PagerDuty event: %s", exc)
        return (
            {
                "response_type": "ephemeral",
                "text": f"Failed to submit the PagerDuty request for {user_name}.",
            },
            502,
        )


def handle_slack_oncall(raw_body: str, headers: dict[str, str]) -> tuple[dict[str, Any], int]:
    if not verify_slack_request(raw_body, headers):
        return {"error": "Request verification failed."}, 401

    if not PAGERDUTY_SCHEDULES:
        return {"error": "Missing schedule_id"}, 400

    if not PAGERDUTY_API_TOKEN:
        logger.error("PAGERDUTY_API_TOKEN is not set")
        return {"error": "PagerDuty API is not configured."}, 500

    try:
        users = get_oncall_users(PAGERDUTY_SCHEDULES, PAGERDUTY_API_TOKEN)

        if not users:
            message = "No one is currently on-call."
        else:
            message = "\n".join([f"• {u['name']} ({u['email']})" for u in users])

        return (
            {"response_type": "in_channel", "text": f"SRE On-Call:\n{message}"},
            200,
        )
    except Exception as e:
        logger.exception("oncall lookup failed")
        return {"error": str(e)}, 500
