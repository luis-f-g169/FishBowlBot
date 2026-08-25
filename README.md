# FishBowlBot

# FishBowlBot

FishBowlBot automatically reserves Kennedy Library fishbowl rooms and provides the current day's reservation schedule through Slack.

The system has two main components:

1. `submitTimeC.py` automatically makes reservations and stores the results in `all_bookings.json`.
2. `slackBot.py` listens for `@FishBowlBot` mentions in Slack and responds with that day's reservations.

## Project Structure

| File                   | Purpose                                                            |
| ---------------------- | ------------------------------------------------------------------ |
| `submitTimeC.py`       | Normal reservation automation                                      |
| `submitTime24hours.py` | Optional 24-hour booking version, primarily for finals week        |
| `slackBot.py`          | Slack `@FishBowlBot` listener                                      |
| `bot.py`               | Reservation/API helper code                                        |
| `emails.csv`           | Member information used for reservations; **not stored in GitHub** |
| `all_bookings.json`    | Generated reservation schedule                                     |
| `run.log`              | Reservation/cron logs                                              |
| `slackbot.log`         | Slack listener logs                                                |

## Initial Setup

Clone the repository:

```bash
git clone https://github.com/luis-f-g169/FishBowlBot.git
cd FishBowlBot
```

Create a Python virtual environment and install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate

pip install playwright requests slack-bolt
playwright install chromium
```

The production server currently keeps the project at:

```text
/root/fishbowlbot
```

### `emails.csv`

The booking script requires `emails.csv`, which contains the members used to make reservations.

This file contains personal information and is intentionally excluded from GitHub through `.gitignore`. It should be transferred privately between webmasters.

Do **not** commit `emails.csv`.

## Slack Credentials

The bot requires the following environment variables:

```text
SLACK_BOT_TOKEN
SLACK_APP_TOKEN
SLACK_WEBHOOK_URL
```

Never commit these credentials to GitHub.

The Slack app uses **Socket Mode**. The Slack app should have:

- Socket Mode enabled
- `app_mention` subscribed under Bot Events
- `app_mentions:read`
- `chat:write`
- An app-level token with `connections:write`

## Automatic Reservations

The normal reservation script is:

```text
submitTimeC.py
```

It creates reservations and updates:

```text
all_bookings.json
```

An alternate script is available:

```text
submitTime24hours.py
```

This was created for periods such as finals week when the library operates 24 hours. Use it only when appropriate.

### Cron Job

Reservations are normally automated using cron.

Edit the root user's crontab:

```bash
crontab -e
```

Normal booking job:

```cron
0 0 * * * cd /root/fishbowlbot && venv/bin/python submitTimeC.py >> /root/fishbowlbot/run.log 2>&1
```

This runs the reservation script every day at midnight and appends its output/errors to `run.log`.

Check the current cron configuration with:

```bash
crontab -l
```

If the cron line begins with `#`, it is disabled.

Cron uses the server's timezone, so verify it with:

```bash
timedatectl
```

The reservation setup was designed around **Pacific Time**.

## Slack `@FishBowlBot`

`slackBot.py` runs separately from the reservation cron job.

When someone sends:

```text
@FishBowlBot
```

the bot:

1. Receives Slack's `app_mention` event.
2. Gets the current date.
3. Reads `all_bookings.json`.
4. Finds today's reservations.
5. Responds in Slack with the day's fishbowl schedule.

### Starting the Slack Listener

From the project directory:

```bash
cd /root/fishbowlbot
nohup venv/bin/python slackBot.py >> slackbot.log 2>&1 &
```

`nohup` keeps the Slack listener running after the SSH session is closed.

Check that it is running:

```bash
ps aux | grep slackBot | grep -v grep
```

View recent logs:

```bash
tail -100 slackbot.log
```

Watch the logs live:

```bash
tail -f slackbot.log
```

Stop the listener:

```bash
pkill -f slackBot.py
```

**Important:** `nohup` does not automatically restart the bot after a server reboot. If the VPS reboots, start `slackBot.py` again manually.

## System Flow

```text
Cron
  |
  v
submitTimeC.py
  |
  |-- Books fishbowl rooms
  |
  v
all_bookings.json
  |
  v
slackBot.py (running with nohup)
  |
  v
@FishBowlBot
  |
  v
Today's reservation schedule
```

## Summer / Academic Breaks

The automatic reservation cron job and Slack listener may intentionally be disabled during summer or other long breaks.

Before reservations are needed again:

1. Check/update `emails.csv`.
2. Verify the Slack credentials.
3. Verify the server timezone.
4. Uncomment the cron job with `crontab -e`.
5. Start `slackBot.py` using `nohup`.
6. Confirm it is running with:

```bash
ps aux | grep slackBot | grep -v grep
```

7. Test `@FishBowlBot` in Slack.
8. Confirm `all_bookings.json` is being updated.

## Troubleshooting

### Reservations are not being created

Check:

```bash
crontab -l
tail -100 run.log
```

Verify:

- The cron job is enabled.
- The server timezone is correct.
- `emails.csv` exists and contains current members.
- The library reservation website has not changed.
- Room IDs, library hours, or reservation rules have not changed.

You can manually test the normal script with:

```bash
cd /root/fishbowlbot
venv/bin/python submitTimeC.py
```

### `@FishBowlBot` does not respond

Check whether the listener is running:

```bash
ps aux | grep slackBot | grep -v grep
```

Check the logs:

```bash
tail -100 slackbot.log
```

Then verify:

- `SLACK_BOT_TOKEN` is configured.
- `SLACK_APP_TOKEN` is configured.
- Socket Mode is enabled.
- The Slack app is installed and added to the channel.
- `all_bookings.json` exists.
- `all_bookings.json` contains an entry for today's date.

## Maintenance

The most likely parts of the bot to require future updates are:

- Library website/Playwright selectors
- Library reservation hours
- Fishbowl room IDs
- `emails.csv` membership
- Slack credentials or Slack app configuration
- Reservation policies

If the library website changes, start by checking `submitTimeC.py`, since most of the reservation automation is implemented there.
