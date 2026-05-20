import requests
import time
import random
from datetime import datetime, timedelta
from typing import List, Dict, Optional

BASE = "https://schedule.lib.calpoly.edu"

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded"})

# From the page's JS resource map
ROOM_ID_TO_NAME = {
    211666: "216K",
    211659: "216L",
    211660: "216M",
    211661: "216N",
    211662: "216P",
    211663: "216Q",
    211664: "216R",
    211665: "216S",
    211786: "224",
}

ROOM_NAME_TO_ID = {v: k for k, v in ROOM_ID_TO_NAME.items()}


def get_availability(start_date: str, end_date: str) -> Optional[List[Dict]]:
    url = BASE + "/spaces/availability/grid"

    payload = {
        "lid": 22959,  # Library Fishbowls
        "gid": 49556,  # Student Fishbowls
        "eid": -1,
        "seat": 0,
        "seatId": 0,
        "zone": 0,
        "filters": [],
        "start": start_date,
        "end": end_date,
        "pageIndex": 0,
        "pageSize": 18,
        "bookings": [],
    }

    try:
        res = session.post(url, data=payload, timeout=20)

        if res.status_code == 429:
            wait = random.randint(120, 300)
            print(f"Rate limited. Backing off for {wait} seconds.")
            time.sleep(wait)
            return None

        res.raise_for_status()
        return res.json()

    except Exception as e:
        print("Availability request failed:", e)
        return None


def parse_slot_time(slot: Dict) -> tuple[datetime, datetime]:
    start_dt = datetime.fromisoformat(slot["start"])
    end_dt = datetime.fromisoformat(slot["end"])
    return start_dt, end_dt


def slot_matches_exact_time(slot: Dict, target_date: str, start_h: int, start_m: int, end_h: int, end_m: int) -> bool:
    start_dt, end_dt = parse_slot_time(slot)

    if start_dt.strftime("%Y-%m-%d") != target_date:
        return False

    return start_dt.hour == start_h and start_dt.minute == start_m and end_dt.hour == end_h and end_dt.minute == end_m


def find_best_room_for_time(
    slots: List[Dict], target_date: str, start_h: int, start_m: int, end_h: int, end_m: int, preferred_room_name: str = "216K"
) -> Optional[Dict]:
    preferred_room_id = ROOM_NAME_TO_ID.get(preferred_room_name)

    candidates = []
    for slot in slots:
        if slot.get("status") != 0:
            continue

        if not slot_matches_exact_time(slot, target_date, start_h, start_m, end_h, end_m):
            continue

        room_id = slot.get("itemId")
        room_name = ROOM_ID_TO_NAME.get(room_id, f"Room {room_id}")

        candidates.append({"room_id": room_id, "room_name": room_name, "start": slot["start"], "end": slot["end"], "raw": slot})

    if not candidates:
        return None

    # First choice: preferred room
    for c in candidates:
        if c["room_id"] == preferred_room_id:
            return c

    # Fallback: any room at that exact time
    candidates.sort(key=lambda x: x["room_name"])
    return candidates[0]


def watch_for_room(preferred_room_name: str = "216K", target_days_ahead: int = 1, start_h: int = 8, start_m: int = 0, end_h: int = 9, end_m: int = 0):
    while True:
        target = datetime.now() + timedelta(days=target_days_ahead)
        target_date = target.strftime("%Y-%m-%d")

        # fetch a small window around the target date
        start_date = target_date
        end_date = (target + timedelta(days=1)).strftime("%Y-%m-%d")

        print(f"Checking {target_date} for {preferred_room_name} from " f"{start_h:02d}:{start_m:02d} to {end_h:02d}:{end_m:02d}")

        data = get_availability(start_date, end_date)

        if data:
            best = find_best_room_for_time(
                data, target_date=target_date, start_h=start_h, start_m=start_m, end_h=end_h, end_m=end_m, preferred_room_name=preferred_room_name
            )

            if best:
                picked = best["room_name"]
                if picked == preferred_room_name:
                    print(f"\nFound your preferred room: {picked}")
                else:
                    print(f"\n{preferred_room_name} unavailable. Fallback found: {picked}")

                print(f"Time: {best['start']} -> {best['end']}")
                return best
            else:
                print("No matching room for that exact time yet.")

        sleep_time = random.randint(30, 60)
        print(f"Sleeping {sleep_time} seconds...\n")
        time.sleep(sleep_time)


if __name__ == "__main__":
    result = watch_for_room(preferred_room_name="216K", target_days_ahead=1, start_h=8, start_m=0, end_h=9, end_m=0)
    print(result)
