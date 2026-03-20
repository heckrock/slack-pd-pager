import hashlib
import hmac
import json
import os
import time
from pathlib import Path

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

PAGERDUTY_KEY = os.environ.get("PAGERDUTY_ROUTING_KEY")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
ALLOWED_USERS_FILE = Path("allowed_users.json")


def load_allowed_user_ids() -> set[str]:
    try:
        with ALLOWED_USERS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        users = data.get("allowed_users", [])
        return {
            user["id"]
            for user in users
            if isinstance(user, dict) and "id" in user
        }
    except FileNotFoundError:
        app.logger.error("allowed_users.json not found")
        return set()
    except json.JSONDecodeError:
        app.logger.error("allowed_users.json is not valid JSON")
        return set()
    except Exception as exc:
        app.logger.error(f"Unexpected error loading allowed users: {exc}")
        return set()


def verify_slack_request(req) -> bool:
    if not SLACK_SIGNING_SECRET:
        app.logger.error("SLACK_SIGNING_SECRET is not set")
        return False

    timestamp = req.headers.get("X-Slack-Request-Timestamp", "")
    slack_signature = req.headers.get("X-Slack-Signature", "")

    if not timestamp or not slack_signature:
        app.logger.warning("Missing Slack signature headers")
        return False

    try:
        timestamp_int = int(timestamp)
    except ValueError:
        app.logger.warning("Invalid Slack timestamp header")
        return False

    # Reject requests older than 5 minutes to help prevent replay attacks
    if abs(time.time() - timestamp_int) > 60 * 5:
        app.logger.warning("Slack request timestamp too old")
        return False

    # IMPORTANT: use the raw request body exactly as Slack sent it
    raw_body = req.get_data(as_text=True)
    basestring = f"v0:{timestamp}:{raw_body}"

    computed_signature = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode("utf-8"),
        basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed_signature, slack_signature)


@app.route("/slack/command", methods=["POST"])
def slack_command():
    if not verify_slack_request(request):
        return jsonify({
            "response_type": "ephemeral",
            "text": "Request verification failed."
        }), 401

    user_id = request.form.get("user_id")

    if not user_id:
        return jsonify({
            "response_type": "ephemeral",
            "text": "Missing Slack user ID in request."
        }), 400

    allowed_user_ids = load_allowed_user_ids()

    if user_id not in allowed_user_ids:
        return jsonify({
            "response_type": "ephemeral",
            "text": "You are not allowed to page SRE, follow the proper escalation process."
        }), 403

    if not PAGERDUTY_KEY:
        app.logger.error("PAGERDUTY_ROUTING_KEY is not set")
        return jsonify({
            "response_type": "ephemeral",
            "text": "PagerDuty integration is not configured."
        }), 500

    try:
        pd_payload = {
            "routing_key": PAGERDUTY_KEY,
            "event_action": "trigger",
            "payload": {
                "summary": f"PagerDuty event triggered from Slack by {user_id}",
                "severity": "critical",
                "source": "slack-demo"
        }
    }

        app.logger.info(f"Sending PagerDuty event: {pd_payload}")

        response = requests.post(
            "https://events.pagerduty.com/v2/enqueue",
            json=pd_payload,
            timeout=10,
        )

        app.logger.info(
            "PagerDuty response status=%s body=%s",
            response.status_code,
            response.text
        )

        response.raise_for_status()

    except requests.RequestException as exc:
        app.logger.error(f"Failed to trigger PagerDuty event: {exc}")
        return jsonify({
            "response_type": "ephemeral",
            "text": "Failed to submit the PagerDuty request."
        }), 502


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
