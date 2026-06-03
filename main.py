import os
import json
import requests
from datetime import datetime, timezone

SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
CLOB_HISTORY_URL = "https://clob.polymarket.com/prices-history"

WATCH_KEYWORDS = [
    # macroeconomic positioning
    "fed", "rate cut", "interest rate", "inflation", "cpi", "recession",
    "unemployment", "gdp", "tariff", "dollar", "yen",

    # geopolitical developments
    "iran", "israel", "russia", "ukraine", "china", "taiwan", "war",
    "conflict", "ceasefire", "nato", "middle east",

    # regulatory risk
    "regulation", "sec", "antitrust", "ban", "export control",
    "sanction", "lawsuit",

    # sector rotation
    "ai", "semiconductor", "chip", "nvidia", "oil", "energy",
    "defense", "crypto", "bitcoin", "ev"
]

MIN_LIQUIDITY = 500
MIN_VOLUME_24H = 500
MAX_MARKETS_TO_CHECK = 30

PREVIOUS_TREND_HOURS = 6
REVERSAL_WINDOW_HOURS = 1
REVERSAL_THRESHOLD = 0.05  # 5 percentage points


def send_slack_message(text: str):
    response = requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=20)
    response.raise_for_status()


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def parse_json_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return []
    return []


def fetch_events():
    params = {
        "active": "true",
        "closed": "false",
        "order": "volume_24hr",
        "ascending": "false",
        "limit": 100,
    }
    response = requests.get(GAMMA_EVENTS_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def is_relevant_market(text: str) -> bool:
    lower = text.lower()
    return any(keyword in lower for keyword in WATCH_KEYWORDS)


def get_yes_token_id(market):
    outcomes = parse_json_list(market.get("outcomes"))
    token_ids = parse_json_list(market.get("clobTokenIds"))

    if not outcomes or not token_ids:
        return None

    for i, outcome in enumerate(outcomes):
        if str(outcome).lower() == "yes" and i < len(token_ids):
            return token_ids[i]

    return token_ids[0] if token_ids else None


def fetch_price_history(token_id):
    params = {
        "market": token_id,
        "interval": "max",
        "fidelity": 60,
    }
    response = requests.get(CLOB_HISTORY_URL, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data.get("history", [])


def nearest_price(history, target_timestamp):
    if not history:
        return None
    return min(history, key=lambda x: abs(int(x.get("t", 0)) - target_timestamp)).get("p")


def detect_reversal(history):
    """
    MVP logic:
    - Compare current price with 1h ago, 3h ago, and 6h ago.
    - If 6h -> 3h -> 1h was uptrend, but current dropped by 5%p+ vs 1h ago:
      Uptrend to Downtrend Reversal.
    - If 6h -> 3h -> 1h was downtrend, but current rose by 5%p+ vs 1h ago:
      Downtrend to Uptrend Reversal.
    """
    if len(history) < 4:
        return None

    now_ts = int(datetime.now(timezone.utc).timestamp())

    p_now = safe_float(history[-1].get("p"))
    p_1h = safe_float(nearest_price(history, now_ts - 1 * 3600))
    p_3h = safe_float(nearest_price(history, now_ts - 3 * 3600))
    p_6h = safe_float(nearest_price(history, now_ts - 6 * 3600))

    if not all([p_now, p_1h, p_3h, p_6h]):
        return None

    was_uptrend = p_6h < p_3h < p_1h
    was_downtrend = p_6h > p_3h > p_1h

    recent_change = p_now - p_1h

    if was_uptrend and recent_change <= -REVERSAL_THRESHOLD:
        return {
            "signal": "Uptrend → Downtrend Reversal",
            "previous_trend": "Uptrend",
            "current_direction": "Downtrend",
            "p_6h": p_6h,
            "p_3h": p_3h,
            "p_1h": p_1h,
            "p_now": p_now,
            "change": recent_change,
        }

    if was_downtrend and recent_change >= REVERSAL_THRESHOLD:
        return {
            "signal": "Downtrend → Uptrend Reversal",
            "previous_trend": "Downtrend",
            "current_direction": "Uptrend",
            "p_6h": p_6h,
            "p_3h": p_3h,
            "p_1h": p_1h,
            "p_now": p_now,
            "change": recent_change,
        }

    return None


def category_for_text(text: str) -> str:
    lower = text.lower()

    if any(k in lower for k in ["fed", "rate", "inflation", "cpi", "recession", "gdp", "unemployment"]):
        return "Macroeconomic Positioning"
    if any(k in lower for k in ["iran", "israel", "russia", "ukraine", "china", "taiwan", "war", "conflict"]):
        return "Geopolitical Developments"
    if any(k in lower for k in ["regulation", "sec", "antitrust", "ban", "export", "sanction", "lawsuit"]):
        return "Regulatory Risk"
    if any(k in lower for k in ["ai", "semiconductor", "chip", "oil", "energy", "defense", "crypto", "bitcoin", "ev"]):
        return "Sector Rotation"

    return "Other Market Signal"


def implication_for_category(category: str, signal: str) -> str:
    if category == "Macroeconomic Positioning":
        return "Check rates, USD/JPY, banks, real estate, and duration-sensitive equities."
    if category == "Geopolitical Developments":
        return "Check defense, energy, airlines, shipping, and broader risk-off positioning."
    if category == "Regulatory Risk":
        return "Check affected sector ETFs and large-cap names exposed to policy or legal risk."
    if category == "Sector Rotation":
        return "Check whether momentum in the affected sector is becoming crowded or reversing."
    return "Review related assets and confirm whether the move is investable."


def build_alert(event_title, market_question, reversal, category, liquidity, volume_24h):
    return f"""🚨 *Prediction Market Trend Reversal Alert*

*Category:* {category}
*Event:* {event_title}
*Market:* {market_question}

*Signal:* {reversal["signal"]}
*Previous Trend:* {reversal["previous_trend"]}
*Current Direction:* {reversal["current_direction"]}

*Probability Path:*
6h ago: {reversal["p_6h"]:.1%}
3h ago: {reversal["p_3h"]:.1%}
1h ago: {reversal["p_1h"]:.1%}
Now: {reversal["p_now"]:.1%}

*Recent Change:* {reversal["change"]:+.1%}

*Liquidity:* ${liquidity:,.0f}
*24h Volume:* ${volume_24h:,.0f}

*Investment Implication:*
{implication_for_category(category, reversal["signal"])}

_This is an automated MVP alert. Please verify market liquidity and news context before taking action._
"""


def main():
    events = fetch_events()
    checked = 0
    alerts_sent = 0

    for event in events:
        event_title = event.get("title") or event.get("question") or "Unknown Event"
        markets = event.get("markets", [])

        for market in markets:
            if checked >= MAX_MARKETS_TO_CHECK:
                break

            question = market.get("question", "")
            text = f"{event_title} {question}"

            if not is_relevant_market(text):
                continue

            liquidity = safe_float(market.get("liquidity"))
            volume_24h = safe_float(market.get("volume24hr") or market.get("volume_24hr"))

            if liquidity < MIN_LIQUIDITY or volume_24h < MIN_VOLUME_24H:
                continue

            token_id = get_yes_token_id(market)
            if not token_id:
                continue

            checked += 1

            try:
                history = fetch_price_history(token_id)
                reversal = detect_reversal(history)
            except Exception as e:
                print(f"Error checking market: {question} | {e}")
                continue

            if reversal:
                category = category_for_text(text)
                alert = build_alert(
                    event_title=event_title,
                    market_question=question,
                    reversal=reversal,
                    category=category,
                    liquidity=liquidity,
                    volume_24h=volume_24h,
                )
                send_slack_message(alert)
                alerts_sent += 1

        if checked >= MAX_MARKETS_TO_CHECK:
            break

    print(f"Checked markets: {checked}")
    print(f"Alerts sent: {alerts_sent}")


if __name__ == "__main__":
    send_slack_message("""
🚨 TEST REVERSAL SIGNAL

This is a test alert from the Prediction Market Alert System.

If you can see this message, Slack integration is working correctly.
""")
