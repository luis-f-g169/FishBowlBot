from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from datetime import datetime
import json
import os

MASTER_PATH = "all_bookings.json"

app = App(token=os.environ["SLACK_BOT_TOKEN"])


@app.event("app_mention")
def handle_mention(event, say):
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        with open(MASTER_PATH, "r") as f:
            all_bookings = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        say("No booking data found!")
        return

    if today in all_bookings:
        say(f"📚 *Today's Fishbowl Bookings ({today})*\n```{all_bookings[today]}```")
    else:
        say(f"No fishbowl bookings found for today ({today}).")


if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
