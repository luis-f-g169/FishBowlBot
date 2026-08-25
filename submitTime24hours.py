from playwright.sync_api import sync_playwright
import time
import csv
import requests
import os
import json
from collections import Counter
from datetime import datetime, timedelta

BASE_URL = "https://schedule.lib.calpoly.edu"
MASTER_PATH = "all_bookings.json"
ALL_BOOKINGS_URL_ENV = "ALL_BOOKINGS_URL"
BOOKING_ROOM_DATE_ENV = "BOOKING_ROOM_DATE"
TARGET_DATE_ENV = "TARGET_DATE"
TARGET_DATES_ENV = "TARGET_DATES"

PREFERRED_ROOMS = ["216R", "216Q", "216K", "216M", "216N", "216P", "216L", "216S", "224"]
ALLOWED_24_HOUR_DAYS = {0, 1, 2, 3, 6}

# Set to a day number string like "24" to override the auto date, or None to use today+6
TEST_DATE = None


def format_hour(hour):
    parsed = datetime.strptime(f"{hour % 24:02d}:00", "%H:%M")
    return parsed.strftime("%I:%M%p").lstrip("0").lower()


def make_24_hour_slots():
    return [(format_hour(hour), format_hour(hour + 1)) for hour in range(24)]


def load_all_bookings(master_path=MASTER_PATH):
    bookings_url = os.environ.get(ALL_BOOKINGS_URL_ENV)

    if bookings_url:
        response = requests.get(bookings_url, timeout=20)
        response.raise_for_status()
        return response.json()

    try:
        with open(master_path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def extract_successful_rooms(log_content):
    rooms = []

    for line in log_content.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue

        status, room = parts[0], parts[2]
        if status != "✓" or room.lower() == "none":
            continue

        rooms.append(room)

    return rooms


def extract_successful_slots(log_content):
    slots = []

    for line in log_content.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue

        status, slot, room = parts[0], parts[1], parts[2]
        if status != "✓" or room.lower() == "none":
            continue

        slots.append(slot)

    return slots


def extract_successful_emails(log_content):
    emails = []

    for line in log_content.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue

        status, room, email = parts[0], parts[2], parts[3]
        if status != "✓" or room.lower() == "none":
            continue

        emails.append(email.lower())

    return emails


def get_room_for_date(all_bookings, date_key):
    log_content = all_bookings.get(date_key, "")
    rooms = extract_successful_rooms(log_content)

    if not rooms:
        return None

    return Counter(rooms).most_common(1)[0][0]


def get_booking_date_key(target):
    if os.environ.get(TARGET_DATES_ENV):
        return target.strftime("%Y-%m-%d")

    return os.environ.get(BOOKING_ROOM_DATE_ENV, target.strftime("%Y-%m-%d"))


def get_used_emails_for_date(all_bookings, date_key):
    log_content = all_bookings.get(date_key, "")
    return set(extract_successful_emails(log_content))


def get_used_slot_strings_for_date(all_bookings, date_key):
    log_content = all_bookings.get(date_key, "")
    return set(extract_successful_slots(log_content))


def filter_people_without_existing_booking(people, all_bookings, date_key):
    used_emails = get_used_emails_for_date(all_bookings, date_key)

    if not used_emails:
        print(f"No existing booked emails found in booking JSON for {date_key}.")
        return people

    available_people = []
    skipped_people = []

    for person in people:
        if person["email"].lower() in used_emails:
            skipped_people.append(person["email"])
            continue

        available_people.append(person)

    print(f"Skipping {len(skipped_people)} emails already booked on {date_key}.")
    return available_people


def filter_time_slots_without_existing_booking(time_slots, all_bookings, date_key):
    used_slots = get_used_slot_strings_for_date(all_bookings, date_key)

    if not used_slots:
        print(f"No existing booked slots found in booking JSON for {date_key}.")
        return time_slots

    available_slots = []
    skipped_slots = []

    for start, end in time_slots:
        slot = f"{start}-{end}"
        if slot in used_slots:
            skipped_slots.append(slot)
            continue

        available_slots.append((start, end))

    print(f"Skipping {len(skipped_slots)} slots already booked on {date_key}.")
    return available_slots


def get_room_order_for_target_date(all_bookings, date_key):
    room = get_room_for_date(all_bookings, date_key)

    if room:
        print(f"Using room {room} from booking JSON for {date_key}.")
        return [room]

    print(f"No successful room found in booking JSON for {date_key}. Skipping this date.")
    return []


def split_booking_log(log_content):
    if "\n\n" not in log_content:
        return log_content, []

    header, body = log_content.split("\n\n", 1)
    return header, [line for line in body.splitlines() if line.strip()]


def merge_booking_logs(existing_log, new_log):
    if not existing_log:
        return new_log

    existing_header, existing_lines = split_booking_log(existing_log)
    _, new_lines = split_booking_log(new_log)
    merged_lines = existing_lines + new_lines

    return f"{existing_header}\n\n" + "\n".join(merged_lines)


def send_log_and_cleanup(log_path="booking_log.txt", master_path="all_bookings.json", target=None):
    with open(log_path, "r") as f:
        new_log_content = f.read()

    webhook_url = os.environ["SLACK_WEBHOOK_URL"]
    target = target or get_target_date()
    date_key = target.strftime("%Y-%m-%d")

    try:
        all_bookings = load_all_bookings(master_path)
    except (requests.RequestException, ValueError):
        all_bookings = {}

    all_bookings[date_key] = merge_booking_logs(all_bookings.get(date_key, ""), new_log_content)

    # Only keep the next 8 days worth, drop anything older than today
    today = datetime.now().date()
    all_bookings = {k: v for k, v in all_bookings.items() if datetime.strptime(k, "%Y-%m-%d").date() >= today}

    with open(master_path, "w") as f:
        json.dump(all_bookings, f, indent=2)

    requests.post(webhook_url, json={"text": f"📚 *Fishbowl Bookings for {date_key}*\n```{new_log_content}```"})
    os.remove(log_path)
    return all_bookings


def load_people_from_csv(path="emails.csv", unique_by_email=False):
    people = []
    seen_emails = set()

    with open(path, newline="") as file:
        reader = csv.DictReader(file)

        for row in reader:
            person = {"first_name": row["first_name"].strip(), "last_name": row["last_name"].strip(), "email": row["email"].strip()}
            email_key = person["email"].lower()

            if unique_by_email and email_key in seen_emails:
                print(f"Skipping duplicate email in {path}: {person['email']}")
                continue

            seen_emails.add(email_key)
            people.append(person)

    return people


def normalize_text(text):
    return text.lower().replace("–", "-").replace("—", "-").replace("\xa0", " ").strip()


def dismiss_modal(page):
    """Dismiss any DOM-based modal/popup that might be blocking the UI."""
    try:
        close_btn = page.locator("button[aria-label='Close'], .modal-close, button:has-text('Close'), button:has-text('×'), button:has-text('OK')")
        if close_btn.count() > 0:
            close_btn.first.click()
            print("Dismissed DOM modal.")
            page.wait_for_timeout(500)
    except Exception:
        pass


def dismiss_chat_widget(page):
    """Hide the floating chat widget that intercepts pointer events."""
    try:
        page.evaluate("""
            const widget = document.querySelector('#s-lch-widget-26147, [aria-label="Chat Widget"], .s-lch-widget-float');
            if (widget) widget.style.display = 'none';
        """)
        print("Chat widget hidden.")
    except Exception:
        pass


def go_to_date(page, day_text, target_month=None):
    """target_month: a datetime whose .month/.year we want to land on."""
    print(f"Opening date picker and selecting day {day_text}...")

    page.locator("button.fc-goToDate-button").click(force=True)
    page.wait_for_timeout(1000)

    if target_month:
        # Advance the datepicker until we're on the right month/year
        for _ in range(3):  # safety limit
            header = page.locator(".datepicker-switch").first.inner_text()
            # header is e.g. "May 2026"
            from datetime import datetime as dt

            shown = dt.strptime(header.strip(), "%B %Y")
            if shown.month == target_month.month and shown.year == target_month.year:
                break
            page.locator(".next").first.click()
            page.wait_for_timeout(400)

    day = page.locator("td.day:not(.old):not(.new)", has_text=day_text).first

    print("Matching datepicker day count:", day.count())

    if day.count() == 0:
        raise RuntimeError(f"Could not find day {day_text} in the current month.")

    day.click(force=True)

    # FIX: Wait for network idle instead of a hardcoded sleep
    page.wait_for_load_state("networkidle")

    print("Clicked calendar day.")


def scroll_grid_to_time(page, target_time="12:00pm"):
    """
    Horizontally scroll the time grid so that the target time column is visible.
    The grid header cells contain the time labels — we find the right one and
    scroll it into view, which drags the whole grid to that position.
    """
    print(f"Scrolling grid to bring {target_time} into view...")

    # Try to find a header cell whose text matches the target time
    # LibCal renders times like "12pm" or "12:00pm" in th/td header cells
    normalized = target_time.lower().replace(":00", "").replace(" ", "")  # "12pm"
    variants = [target_time, normalized, target_time.replace(":00", "")]  # ["12:00pm", "12pm", "12pm"]

    header = None
    for variant in variants:
        candidate = page.locator(f"th:has-text('{variant}'), td.fc-axis:has-text('{variant}')").first
        if candidate.count() > 0:
            header = candidate
            print(f"Found time header using variant: '{variant}'")
            break

    if header and header.count() > 0:
        header.scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        print("Scrolled time header into view.")
    else:
        # Fallback: scroll the grid container by a calculated pixel offset.
        # The grid typically starts around 5am and each hour is ~42px wide.
        # 12pm = 7 hours from 5am = ~294px
        print("Time header not found — falling back to pixel scroll.")
        page.evaluate("""
            const grid = document.querySelector('.fc-scroller, .s-lc-eq-scroll, #eq-time-grid');
            if (grid) grid.scrollLeft = 294;
        """)
        page.wait_for_timeout(500)


def choose_room_and_submit(page, booking):
    room_name = booking["room_name"]
    target_start = booking["start"]
    target_end = booking["end"]

    page.wait_for_selector("#eq-time-grid, body", timeout=15000)

    # Scroll the grid so the target time column is visible before trying to click
    scroll_grid_to_time(page, target_time=target_start)

    print(f"Looking for {room_name} at {target_start}")

    room_locator = page.locator(f"text={room_name}")
    print(f"Room count for {room_name}:", room_locator.count())

    if room_locator.count() == 0:
        print("Room not found on page.")
        return False

    # Filter available slots by BOTH room name and start time in title/aria-label
    slot = page.locator(
        f"xpath=//*[contains(normalize-space(), '{room_name}')]/ancestor::tr[1]//*["
        f"(contains(@class,'s-lc-eq-avail') or contains(@class,'available') or contains(@title,'Available'))"
        f" and (contains(@title, '{target_start}') or contains(@aria-label, '{target_start}'))"
        f" and (contains(@title, '{room_name}') or contains(@aria-label, '{room_name}'))]"
    ).first

    print("Matching slot count (time-filtered):", slot.count())

    if slot.count() == 0:
        # The title/aria-label format might differ — print all available slot titles for debugging
        all_slots = page.locator(
            f"xpath=//*[contains(normalize-space(), '{room_name}')]/ancestor::tr[1]//*["
            f"contains(@class,'s-lc-eq-avail') or contains(@class,'available') or contains(@title,'Available')]"
        )
        print(f"DEBUG: Found {all_slots.count()} unfiltered slots. Titles:")
        for i in range(min(all_slots.count(), 5)):
            print(f"  slot[{i}] title='{all_slots.nth(i).get_attribute('title')}' aria-label='{all_slots.nth(i).get_attribute('aria-label')}'")

        # Try a looser time match (just the hour, e.g. "12pm") but still pin to room name
        loose_time = target_start.replace(":00", "")
        slot = page.locator(
            f"xpath=//*[contains(normalize-space(), '{room_name}')]/ancestor::tr[1]//*["
            f"(contains(@class,'s-lc-eq-avail') or contains(@class,'available') or contains(@title,'Available'))"
            f" and (contains(@title, '{loose_time}') or contains(@aria-label, '{loose_time}'))"
            f" and (contains(@title, '{room_name}') or contains(@aria-label, '{room_name}'))]"
        ).first

        print(f"Loose match ({loose_time}) slot count:", slot.count())

    if slot.count() > 0:
        print("Clicking available slot.")
        dismiss_chat_widget(page)  # hide chat widget that intercepts clicks
        slot.scroll_into_view_if_needed()
        slot.click(force=True)  # force=True bypasses pointer-interception checks

        # Wait for the end-time dropdown to appear
        page.wait_for_selector(".b-end-date", timeout=10000)

        set_end_time_and_continue(page, booking["person"], target_end=target_end)
        return True

    print("No matching slot found.")
    return False


def fill_booking_details_and_submit(page, first_name, last_name, email):
    print("Filling booking details...")

    # Wait for the Booking Details page to load
    page.wait_for_selector("text=Booking Details", timeout=15000)

    # Fill by known field IDs — much safer than nth() which breaks if field order changes
    page.locator("#fname").fill(first_name)
    page.locator("#lname").fill(last_name)
    page.locator("#email").fill(email)
    print(f"Filled: {first_name} {last_name} <{email}>")

    # Check the Student checkbox by its value attribute
    try:
        page.locator("input[type='checkbox'][value='Student']").check(force=True)
        print("Checked Student checkbox.")
    except Exception as e:
        print(f"Could not check Student checkbox: {e}")

    # Dismiss chat widget again in case it reappeared over the submit button
    dismiss_chat_widget(page)

    submit_button = page.locator("button:has-text('SUBMIT MY BOOKING'), input[type='submit']")
    print("Submit button count:", submit_button.count())
    submit_button.first.click(force=True)

    # Wait for confirmation page or error — use a short poll so we react instantly
    try:
        page.wait_for_selector("h1:has-text('Booking Confirmed'), #s-lc-eq-errors:not(:empty)", timeout=15000)
    except Exception:
        pass

    if page.locator("h1:has-text('Booking Confirmed')").count() > 0:
        print("Booking submitted successfully.")
        return True
    elif page.locator("#s-lc-eq-errors").is_visible():
        print("Booking error:", page.locator("#s-lc-eq-errors").inner_text())
        return False
    else:
        print("Submitted; check page state manually.")
        return False


def set_end_time_and_continue(page, person, target_end="1:00pm"):
    dropdown = page.locator(".b-end-date").first
    dropdown.wait_for(timeout=5000)

    options = dropdown.locator("option")
    matched = False

    normalized_target = target_end.lower().replace(" ", "")

    for i in range(options.count()):
        text = (options.nth(i).text_content() or "").strip()
        normalized_text = text.lower().replace(" ", "")

        if normalized_target in normalized_text:
            value = options.nth(i).get_attribute("value")
            dropdown.select_option(value=value)
            matched = True
            print(f"Selected end time option: {text}")
            break

    if not matched:
        raise RuntimeError(f"Couldn't find the {target_end} end-time option.")

    page.locator("#submit_times").click()
    print("Clicked Submit Times")

    page.wait_for_selector("text=Booking Details", timeout=15000)

    return fill_booking_details_and_submit(page, first_name=person["first_name"], last_name=person["last_name"], email=person["email"])


# Hours the library is open per weekday (0=Mon, 6=Sun)
HOURS_BY_WEEKDAY = {
    0: [
        ("12:00pm", "1:00pm"),
        ("1:00pm", "2:00pm"),
        ("11:00am", "12:00pm"),
        ("2:00pm", "3:00pm"),
        ("10:00am", "11:00am"),
        ("3:00pm", "4:00pm"),
        ("9:00am", "10:00am"),
        ("4:00pm", "5:00pm"),
        ("8:00am", "9:00am"),
        ("5:00pm", "6:00pm"),
        ("6:00pm", "7:00pm"),
        ("7:00pm", "8:00pm"),
    ],  # Mon
    1: [
        ("8:00am", "9:00am"),
        ("9:00am", "10:00am"),
        ("10:00am", "11:00am"),
        ("11:00am", "12:00pm"),
        ("12:00pm", "1:00pm"),
        ("1:00pm", "2:00pm"),
        ("2:00pm", "3:00pm"),
        ("3:00pm", "4:00pm"),
        ("4:00pm", "5:00pm"),
        ("5:00pm", "6:00pm"),
        ("6:00pm", "7:00pm"),
        ("7:00pm", "8:00pm"),
    ],  # Tue
    2: [
        ("8:00am", "9:00am"),
        ("9:00am", "10:00am"),
        ("10:00am", "11:00am"),
        ("11:00am", "12:00pm"),
        ("12:00pm", "1:00pm"),
        ("1:00pm", "2:00pm"),
        ("2:00pm", "3:00pm"),
        ("3:00pm", "4:00pm"),
        ("4:00pm", "5:00pm"),
        ("5:00pm", "6:00pm"),
        ("6:00pm", "7:00pm"),
        ("7:00pm", "8:00pm"),
    ],  # Wed
    3: [
        ("8:00am", "9:00am"),
        ("9:00am", "10:00am"),
        ("10:00am", "11:00am"),
        ("11:00am", "12:00pm"),
        ("12:00pm", "1:00pm"),
        ("1:00pm", "2:00pm"),
        ("2:00pm", "3:00pm"),
        ("3:00pm", "4:00pm"),
        ("4:00pm", "5:00pm"),
        ("5:00pm", "6:00pm"),
        ("6:00pm", "7:00pm"),
        ("7:00pm", "8:00pm"),
    ],  # Thu
    4: [
        ("8:00am", "9:00am"),
        ("9:00am", "10:00am"),
        ("10:00am", "11:00am"),
        ("11:00am", "12:00pm"),
        ("12:00pm", "1:00pm"),
        ("1:00pm", "2:00pm"),
        ("2:00pm", "3:00pm"),
        ("3:00pm", "4:00pm"),
        ("4:00pm", "5:00pm"),
        ("5:00pm", "6:00pm"),
        ("6:00pm", "7:00pm"),
        ("7:00pm", "8:00pm"),
    ],  # Fri
    5: [
        ("10:00am", "11:00am"),
        ("11:00am", "12:00pm"),
        ("12:00pm", "1:00pm"),
        ("1:00pm", "2:00pm"),
        ("2:00pm", "3:00pm"),
        ("3:00pm", "4:00pm"),
        ("4:00pm", "5:00pm"),
        ("5:00pm", "6:00pm"),
    ],  # Sat
    6: [
        ("12:00pm", "1:00pm"),
        ("1:00pm", "2:00pm"),
        ("2:00pm", "3:00pm"),
        ("3:00pm", "4:00pm"),
        ("4:00pm", "5:00pm"),
        ("5:00pm", "6:00pm"),
        ("6:00pm", "7:00pm"),
    ],  # Sun
}


def get_target_date():
    """Bookings open at midnight for the date 7 days from now."""
    target = datetime.now() + timedelta(days=7)
    return target


def parse_target_date(date_text):
    return datetime.strptime(date_text.strip(), "%Y-%m-%d")


def get_target_dates():
    target_dates = os.environ.get(TARGET_DATES_ENV)

    if target_dates:
        return [parse_target_date(date_text) for date_text in target_dates.split(",") if date_text.strip()]

    target_date = os.environ.get(TARGET_DATE_ENV)

    if target_date:
        return [parse_target_date(target_date)]

    return [get_target_date()]


def get_time_slots(target=None):
    target = target or get_target_date()
    weekday = target.weekday()  # 0=Mon, 6=Sun
    day = TEST_DATE if TEST_DATE else str(target.day)

    # Only run 24-hour booking for Sunday through Thursday
    # Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Sunday=6
    if weekday not in ALLOWED_24_HOUR_DAYS:
        print(f"Target date: {target.strftime('%A, %B %d, %Y')} " f"is not a Sunday–Thursday 24-hour booking day. Exiting.")
        return [], day

    slots = make_24_hour_slots()

    print(f"Target date: {target.strftime('%A, %B %d, %Y')} " f"(day={day}) — {len(slots)} 24-hour slots available")

    return slots, day


def click_make_another_booking(page):
    """Click 'MAKE ANOTHER BOOKING' on the confirmation page and wait for the grid."""
    print("Clicking 'MAKE ANOTHER BOOKING'...")
    btn = page.locator("a:has-text('MAKE ANOTHER BOOKING'), button:has-text('MAKE ANOTHER BOOKING')")
    btn.first.wait_for(timeout=10000)
    btn.first.click()
    page.wait_for_load_state("networkidle")
    dismiss_modal(page)
    print("Back on the booking grid.")


def open_booking_grid(page):
    page.goto(BASE_URL + "/spaces?lid=22959&gid=49556", wait_until="networkidle")
    dismiss_modal(page)

    try:
        page.select_option("#lid", value="22959")
        page.select_option("#gid", value="49556")
    except Exception:
        pass

    page.wait_for_load_state("networkidle")


def write_booking_log(target, results, log_path="booking_log.txt"):
    print("\n══ Booking Summary ══")
    lines = []
    for r in results:
        icon = "✓" if r["status"] == "booked" else "✗"
        line = f"  {icon}  {r['slot']:22s}  {r['room']:8s}  {r['person']}"
        print(line)
        lines.append(line)

    with open(log_path, "w") as f:
        f.write(f"Run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Target: {target.strftime('%A, %B %d, %Y')}\n\n")
        f.write("\n".join(lines))


def run_for_target_date(page, target, all_bookings, all_people):
    date_key = get_booking_date_key(target)

    print(f"\n════ Processing {date_key} ({target.strftime('%A')}) ════")

    people = filter_people_without_existing_booking(all_people, all_bookings, date_key)
    time_slots, target_day = get_time_slots(target)
    time_slots = filter_time_slots_without_existing_booking(time_slots, all_bookings, date_key)
    preferred_rooms = get_room_order_for_target_date(all_bookings, date_key)

    if not preferred_rooms:
        return

    if not time_slots:
        print(f"No missing slots available for {date_key}. Skipping.")
        return

    if not people:
        print(f"No available people left for {date_key}. Skipping.")
        return

    if len(people) < len(time_slots):
        print(f"WARNING: Only {len(people)} available people in CSV but {len(time_slots)} slots to fill. " f"Will book as many slots as there are people.")

    open_booking_grid(page)
    go_to_date(page, target_day, target_month=target)
    dismiss_modal(page)

    results = []
    for i, (start, end) in enumerate(time_slots):
        if i >= len(people):
            print(f"Ran out of people at slot {start}-{end}, stopping.")
            break

        person = people[i]

        print(f"\n── Booking {i+1}/{min(len(time_slots), len(people))}: " f"{start}-{end} for {person['first_name']} {person['last_name']} ──")

        booked_room = None
        for room in preferred_rooms:
            print(f"  Trying room {room}...")
            booking = {
                "room_name": room,
                "start": start,
                "end": end,
                "person": person,
            }
            success = choose_room_and_submit(page, booking)
            if success:
                booked_room = room
                break

        if booked_room:
            print(f"✓ Booked {start}-{end} in {booked_room} for {person['email']}")
            results.append({"slot": f"{start}-{end}", "room": booked_room, "person": person["email"], "status": "booked"})

            more_slots = i < len(time_slots) - 1
            more_people = i < len(people) - 1
            if more_slots and more_people:
                click_make_another_booking(page)
                go_to_date(page, target_day, target_month=target)
                dismiss_modal(page)
        else:
            print(f"✗ No rooms available for {start}-{end} — all preferred rooms taken.")
            results.append({"slot": f"{start}-{end}", "room": "none", "person": person["email"], "status": "failed"})

    if not results:
        print(f"No booking attempts were made for {date_key}.")
        return

    write_booking_log(target, results)
    return send_log_and_cleanup(target=target)


def run():
    all_bookings = load_all_bookings()
    all_people = load_people_from_csv("emails.csv", unique_by_email=True)
    target_dates = get_target_dates()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)  # headless for cloud
        page = browser.new_page()
        page.on("dialog", lambda dialog: (print(f"Auto-dismissed dialog: {dialog.message}"), dialog.dismiss()))

        for target in target_dates:
            updated_bookings = run_for_target_date(page, target, all_bookings, all_people)
            if updated_bookings is not None:
                all_bookings = updated_bookings

        browser.close()


if __name__ == "__main__":
    run()
