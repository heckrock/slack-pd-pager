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
PAGERDUTY_API_URL = "https://api.pagerduty.com/oncalls"
PAGERDUTY_API_TOKEN = os.environ.get("PAGERDUTY_API_TOKEN")

def get_oncall_users(schedule_id, api_token, escalation_level=1):
    headers = {
        "Authorization": f"Token token={api_token}",
        "Accept": "application/vnd.pagerduty+json;version=2"
    }

    params = {
        "schedule_ids[]": schedule_id,
        "include[]": "users"
    }

    response = requests.get(PAGERDUTY_API_URL, headers=headers, params=params)
    response.raise_for_status()

    data = response.json()

    # Filter by escalation level (default = primary on-call)
    oncalls = data.get("oncalls", [])
    print("RAW ONCALLS:", oncalls)

    users = []
    for oc in oncalls:
        user = oc.get("user", {})
        users.append({
            "id": user.get("id"),
            "name": user.get("summary"),
            "email": user.get("email")
        })

    return users

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
        app.logger.error("allowed_users.json not found")
        return {}
    except json.JSONDecodeError:
        app.logger.error("allowed_users.json is not valid JSON")
        return {}
    except Exception as exc:
        app.logger.error(f"Unexpected error loading allowed users: {exc}")
        return {}


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

    if abs(time.time() - timestamp_int) > 60 * 5:
        app.logger.warning("Slack request timestamp too old")
        return False

    raw_body = req.get_data(as_text=True)
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
                "issue_description": issue_description
            }
        }
    }

    app.logger.info("Sending PagerDuty event: %s", pd_payload)

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
    return response


@app.route("/", methods=["GET"])
def home():
    return "Slack PagerDuty app is running", 200


@app.route("/slack/page", methods=["POST"])
def slack_command():
    if not verify_slack_request(request):
        return jsonify({
            "response_type": "ephemeral",
            "text": "Request verification failed."
        }), 401

    user_id = request.form.get("user_id")
    issue_description = request.form.get("text", "").strip()

    if not user_id:
        return jsonify({
            "response_type": "ephemeral",
            "text": "Missing Slack user ID in request."
        }), 400

    if not issue_description:
        return jsonify({
            "response_type": "ephemeral",
            "text": "Please include a brief description, for example: /page-sre checkout is failing"
        }), 400

    allowed_users = load_allowed_users()
    user_name = allowed_users.get(user_id)

    if not user_name:
        return jsonify({
            "response_type": "ephemeral",
            "text": "User is not allowed."
        }), 403

    if not PAGERDUTY_KEY:
        app.logger.error("PAGERDUTY_ROUTING_KEY is not set")
        return jsonify({
            "response_type": "ephemeral",
            "text": "PagerDuty integration is not configured."
        }), 500

    try:
        response = trigger_pagerduty_event(user_name, issue_description)

        return jsonify({
            "response_type": "ephemeral",
            "text": f"PagerDuty request submitted successfully by {user_name} for: {issue_description}."
        }), 200

    except requests.RequestException as exc:
        app.logger.error(f"Failed to trigger PagerDuty event: {exc}")
        return jsonify({
            "response_type": "ephemeral",
            "text": f"Failed to submit the PagerDuty request for {user_name}."
        }), 502

@app.route("/slack/oncall", methods=["POST"])
def oncall():
    schedule_id = "PW30PB4,PNUHG1I"

    if not schedule_id:
        return jsonify({"error": "Missing schedule_id"}), 400

    try:
        users = get_oncall_users(schedule_id, PAGERDUTY_API_TOKEN)
        return jsonify({
            "response_type": "ephemeral",
            "text": f"SRE On-Call": {users}
            }),200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
