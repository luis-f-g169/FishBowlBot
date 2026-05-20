from playwright.sync_api import sync_playwright
from bot import watch_for_room
import time
import csv
import requests
import os
import json
from datetime import datetime, timedelta

BASE_URL = "https://schedule.lib.calpoly.edu"

PREFERRED_ROOMS = ["216Q", "216L", "216K", "216M", "216N", "216P", "216R", "216S", "224"]

# Set to a day number string like "24" to override the auto date, or None to use today+6
TEST_DATE = None


def send_log_and_cleanup(log_path="booking_log.txt", master_path="all_bookings.json"):
    with open(log_path, "r") as f:
        log_content = f.read()

    webhook_url = os.environ["SLACK_WEBHOOK_URL"]
    target = get_target_date()
    date_key = target.strftime("%Y-%m-%d")

    try:
        with open(master_path, "r") as f:
            all_bookings = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        all_bookings = {}

    # Add today's booking
    all_bookings[date_key] = log_content

    # Only keep the next 8 days worth, drop anything older than today
    today = datetime.now().date()
    all_bookings = {k: v for k, v in all_bookings.items() if datetime.strptime(k, "%Y-%m-%d").date() >= today}

    with open(master_path, "w") as f:
        json.dump(all_bookings, f, indent=2)

    requests.post(webhook_url, json={"text": f"📚 *Fishbowl Bookings for {date_key}*\n```{log_content}```"})
    os.remove(log_path)


def load_people_from_csv(path="emails.csv"):
    people = []

    with open(path, newline="") as file:
        reader = csv.DictReader(file)

        for row in reader:
            people.append({"first_name": row["first_name"].strip(), "last_name": row["last_name"].strip(), "email": row["email"].strip()})

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


def go_to_date(page, day_text="24"):
    print(f"Opening date picker and selecting day {day_text}...")

    page.locator("button.fc-goToDate-button").click(force=True)
    page.wait_for_timeout(1000)

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
    """Bookings open at midnight for the date 6 days from now."""
    target = datetime.now() + timedelta(days=7)
    return target


def get_time_slots():
    target = get_target_date()
    weekday = target.weekday()  # 0=Mon, 6=Sun
    slots = HOURS_BY_WEEKDAY.get(weekday, [])
    day = TEST_DATE if TEST_DATE else str(target.day)
    print(f"Target date: {target.strftime('%A, %B %d, %Y')} (day={day}) — {len(slots)} slots available")
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


def run():
    with sync_playwright() as p:
        people = load_people_from_csv("emails.csv")
        TIME_SLOTS, TARGET_DAY = get_time_slots()

        if not TIME_SLOTS:
            print("No slots available for target date. Exiting.")
            return

        if len(people) < len(TIME_SLOTS):
            print(f"WARNING: Only {len(people)} people in CSV but {len(TIME_SLOTS)} slots to fill. " f"Will book as many slots as there are people.")

        browser = p.chromium.launch(headless=True)  # headless for cloud
        page = browser.new_page()
        page.on("dialog", lambda dialog: (print(f"Auto-dismissed dialog: {dialog.message}"), dialog.dismiss()))

        # ── Initial page load ──────────────────────────────────────────────
        page.goto(BASE_URL + "/spaces?lid=22959&gid=49556", wait_until="networkidle")
        dismiss_modal(page)

        try:
            page.select_option("#lid", value="22959")
            page.select_option("#gid", value="49556")
        except Exception:
            pass

        page.wait_for_load_state("networkidle")
        go_to_date(page, TARGET_DAY)
        dismiss_modal(page)

        # ── Loop: one booking per (person, time slot) pair ─────────────────
        results = []
        for i, (start, end) in enumerate(TIME_SLOTS):
            if i >= len(people):
                print(f"Ran out of people at slot {start}-{end}, stopping.")
                break

            person = people[i]

            print(f"\n── Booking {i+1}/{min(len(TIME_SLOTS), len(people))}: " f"{start}-{end} for {person['first_name']} {person['last_name']} ──")

            # Try each preferred room in order until one succeeds for this slot
            booked_room = None
            for room in PREFERRED_ROOMS:
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

                # Immediately click Make Another Booking while still on the confirmation page
                more_slots = i < len(TIME_SLOTS) - 1
                more_people = i < len(people) - 1
                if more_slots and more_people:
                    click_make_another_booking(page)  # button is right there on confirmation page
                    go_to_date(page, TARGET_DAY)
                    dismiss_modal(page)
            else:
                print(f"✗ No rooms available for {start}-{end} — all preferred rooms taken.")
                results.append({"slot": f"{start}-{end}", "room": "none", "person": person["email"], "status": "failed"})

        # ── Summary ────────────────────────────────────────────────────────
        print("\n══ Booking Summary ══")
        lines = []
        for r in results:
            icon = "✓" if r["status"] == "booked" else "✗"
            line = f"  {icon}  {r['slot']:22s}  {r['room']:8s}  {r['person']}"
            print(line)
            lines.append(line)

        # Write log file for GitHub Actions artifact upload
        target = get_target_date()
        with open("booking_log.txt", "w") as f:
            f.write(f"Run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Target: {target.strftime('%A, %B %d, %Y')}\n\n")
            f.write("\n".join(lines))

        browser.close()
    # Sends to slack channel
    send_log_and_cleanup()


if __name__ == "__main__":
    run()
