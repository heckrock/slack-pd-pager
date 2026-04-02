# slack-pd-pager

Small Slack integration that lets approved users page SRE via PagerDuty and query who is on call using the PagerDuty API. Core logic lives in `pager_core.py`; you can run it behind **Flask** (e.g. Render) or **Azure Functions**.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | Health check |
| `POST` | `/slack/page` | Slash command: trigger a PagerDuty incident (allowed users only) |
| `POST` | `/slack/oncall` | Slash command: list current on-call from configured schedules |

Slack signs all `POST` requests; `SLACK_SIGNING_SECRET` must match your Slack app.

## Configuration

### Environment variables

| Variable | Required for | Description |
|----------|----------------|-------------|
| `SLACK_SIGNING_SECRET` | `POST` routes | From Slack app **Basic Information** → Signing Secret |
| `PAGERDUTY_ROUTING_KEY` | `/slack/page` | Events API v2 integration routing key |
| `PAGERDUTY_API_TOKEN` | `/slack/oncall` | REST API token with access to schedules |
| `PAGERDUTY_SCHEDULES` | `/slack/oncall` | Comma-separated PagerDuty schedule IDs |

Optional for local Flask only:

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `10000` | HTTP port |

### Allowed users

Edit `allowed_users.json` and deploy it with the app. Only Slack users listed there can use `/slack/page`.

```json
{
  "allowed_users": [
    { "id": "U01234567", "name": "Display Name" }
  ]
}
```

Use each member’s Slack user ID (starts with `U`).

### Slack app URLs

Point your slash commands at your public base URL (no `/api` prefix when using the included `host.json` on Azure):

- Request URL for paging: `https://<host>/slack/page`
- Request URL for on-call: `https://<host>/slack/oncall`

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Export the environment variables (or use a `.env` loader if you add one), then either:

**Flask (same as a simple Render-style run):**

```bash
export PORT=10000
python app.py
```

**Azure Functions (requires [Azure Functions Core Tools](https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local)):**

```bash
cp local.settings.json.example local.settings.json
# Edit local.settings.json and set Values
func start
```

## Deployment

### Azure Functions

1. Create a Python Function App on Azure.
2. Set application settings: `AzureWebJobsStorage`, `FUNCTIONS_WORKER_RUNTIME` (`python`), plus the variables in the table above.
3. Deploy this repository root (include `allowed_users.json`, `function_app.py`, `host.json`, `pager_core.py`, `requirements.txt`).
4. Update Slack slash command URLs to your function app hostname.

`host.json` sets `routePrefix` to empty so paths stay `/slack/page` and `/slack/oncall`.

### Flask / Render (or similar)

- **Start command:** e.g. `gunicorn app:app --bind 0.0.0.0:$PORT`
- **Build:** `pip install -r requirements.txt`
- Set the same secrets as environment variables on the service.

## Project layout

| File | Role |
|------|------|
| `pager_core.py` | PagerDuty + Slack verification + request handlers |
| `function_app.py` | Azure Functions HTTP triggers (Python v2 model) |
| `app.py` | Flask wrapper for non-Azure hosting |
| `host.json` | Azure Functions host settings (including HTTP route prefix) |
| `local.settings.json.example` | Template for local Azure Functions secrets |

## License

See the repository license (if any) on GitHub.

