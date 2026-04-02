"""Azure Functions HTTP triggers for slack-pd-pager."""

from __future__ import annotations

import json
import logging
import urllib.parse

import azure.functions as func

from pager_core import handle_home, handle_slack_oncall, handle_slack_page

logging.basicConfig(level=logging.INFO)

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


def _normalize_headers(req: func.HttpRequest) -> dict[str, str]:
    return {k: str(v) for k, v in req.headers.items()}


def _form_dict(raw_body: str) -> dict[str, str]:
    parsed = urllib.parse.parse_qs(raw_body, keep_blank_values=True)
    return {k: (v[0] if v else "") for k, v in parsed.items()}


@app.route(route="", methods=["GET"])
def home(req: func.HttpRequest) -> func.HttpResponse:
    text, status = handle_home()
    return func.HttpResponse(text, status_code=status, mimetype="text/plain")


@app.route(route="slack/page", methods=["POST"])
def slack_page(req: func.HttpRequest) -> func.HttpResponse:
    raw_body = req.get_body().decode("utf-8")
    headers = _normalize_headers(req)
    form = _form_dict(raw_body)
    body, status = handle_slack_page(raw_body, form, headers)
    return func.HttpResponse(
        json.dumps(body),
        status_code=status,
        mimetype="application/json",
    )


@app.route(route="slack/oncall", methods=["POST"])
def slack_oncall(req: func.HttpRequest) -> func.HttpResponse:
    raw_body = req.get_body().decode("utf-8")
    headers = _normalize_headers(req)
    body, status = handle_slack_oncall(raw_body, headers)
    return func.HttpResponse(
        json.dumps(body),
        status_code=status,
        mimetype="application/json",
    )
