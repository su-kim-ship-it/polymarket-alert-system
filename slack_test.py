import os
import requests

webhook_url = os.environ["SLACK_WEBHOOK_URL"]

message = {
    "text": "🚨 Slack Alert Test\n\nPrediction Market Alert System is working."
}

response = requests.post(webhook_url, json=message)
response.raise_for_status()
