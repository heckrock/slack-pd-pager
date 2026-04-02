"""Flask app for local dev or non-Azure hosts (e.g. Render). Azure deploy uses function_app.py."""

import os

from flask import Flask, jsonify, request

from pager_core import (
    handle_home,
    handle_slack_oncall,
    handle_slack_page,
)

app = Flask(__name__)


@app.route("/", methods=["GET"])
def home():
    text, status = handle_home()
    return text, status


@app.route("/slack/page", methods=["POST"])
def slack_command():
    raw_body = request.get_data(as_text=True)
    form = request.form.to_dict()
    body, status = handle_slack_page(raw_body, form, dict(request.headers))
    return jsonify(body), status


@app.route("/slack/oncall", methods=["POST"])
def oncall():
    raw_body = request.get_data(as_text=True)
    body, status = handle_slack_oncall(raw_body, dict(request.headers))
    return jsonify(body), status


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
