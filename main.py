#!/usr/bin/env python3

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import requests
import re
import dataclasses
import os
import sys
import pickle
import logging
from typing import Set
import hashlib

requests.packages.urllib3.disable_warnings()

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger("kyg")


@dataclasses.dataclass
class CourseEntry:
    idx: int = dataclasses.field(compare=False)
    course_id: str
    course_name: str
    course_category: str
    course_credits: int
    course_grade: str
    course_attendance: str

    def __post_init__(self):
        self.idx = int(self.idx)
        self.course_id = self.course_id.strip().upper()
        self.course_name = self.course_name.strip().title()
        self.course_category = self.course_category.strip().title()
        self.course_credits = int(self.course_credits)
        self.course_grade = self.course_grade.strip().upper()
        self.course_attendance = self.course_attendance.strip().upper()

    def __repr__(self) -> str:
        return "/".join(
            map(
                str,
                [
                    self.course_id,
                    self.course_name,
                    self.course_category,
                    self.course_credits,
                    self.course_grade,
                    self.course_attendance,
                ],
            )
        )

    def __hash__(self) -> int:
        return hash(repr(self))

    def stable_hash(self) -> str:
        return hashlib.md5(repr(self).encode("utf-8")).hexdigest()


def fetch_raw_html(username: str, password: str) -> str:
    logger.info("Fetching raw HTML from IITM viewgrades")
    with requests.Session() as s:
        r = s.post(
            "https://www.iitm.ac.in/viewgrades/",
            data={"username": username, "password": password, "submit": "LogIn"},
            verify=False,
        )
        src = r.text

        logger.info(f"Fetch status: {r.status_code}")
    return src


def parse_courses_from_raw_html(data: str) -> Set[CourseEntry]:
    pattern = (
        r"""<td style=".+">(?P<idx>\d+)</td>\s+"""
        r"""<td style=".+">(?P<course_id>[\w+]+)\s*</td>\s*"""
        r"""<td style=".+">(?P<course_name>.+)</td>\s*"""
        r"""<td style=".+">(?P<course_category>\w*)\s*</td>\s*"""
        r"""<td style=".+">(?P<course_credits>\d+)</td>\s*"""
        r"""<td style=".+">(?P<course_grade>[A-Z*]?\s*)</td>\s*"""
        r"""<td style=".+">(?P<course_attendance>[\w*]*)\s*</td>\s*"""
    )
    pattern = re.compile(pattern)

    matches = [m.groupdict() for m in pattern.finditer(data)]
    courses = set(map(lambda e: CourseEntry(**e), matches))

    logger.info(f"Retrieved {len(courses)} course info")
    return courses


def generate_ntfy_message(courses: Set[CourseEntry]) -> str:
    msg = ""
    for i, course in enumerate(courses):
        msg += f"{i+1}. {course.course_id} : {course.course_grade}\n"

    return msg


def send_push_notification(msg: str, topic: str) -> bool:
    if len(msg) == 0:
        return True

    logger.info("Attempting to notify clients")
    hdrs = {"Title": "Uh oh! Grades out", "Priority": "high", "Tags": "facepalm"}
    r = requests.post(
        f"https://ntfy.sh/{topic}",
        data=msg.encode(encoding="utf-8"),
        timeout=2,
        headers=hdrs,
    )
    logger.info(f"ntfy status: {r.status_code}")
    return r.ok


def load_from_cache(filename: str = "cache.pkl") -> Set[CourseEntry]:
    if not os.path.exists(filename):
        return set()

    with open(filename, "rb") as f:
        data = pickle.load(f)

    return data


def dump_to_cache(data, filename: str = "cache.pkl") -> None:
    if os.getenv("ENV", "").upper() == "DEV":
        data.pop()  # remove a random element during dev testing

    with open(filename, "wb") as f:
        pickle.dump(data, f, pickle.HIGHEST_PROTOCOL)


def main():
    LDAP_USR = os.getenv("LDAP_USR")
    LDAP_PWD = os.getenv("LDAP_PWD")
    NTFY_TOPIC = os.getenv("NTFY_TOPIC")

    logger.info(f"ntfy topic: {NTFY_TOPIC}")

    html = fetch_raw_html(LDAP_USR, LDAP_PWD)
    new_entries = parse_courses_from_raw_html(html)

    courseid_to_coursedata_new = {c.course_id: c for c in new_entries}
    coursehash_new = set((c.course_id, c.stable_hash()) for c in new_entries)
    coursehash_old = load_from_cache()
    updates = coursehash_new.difference(coursehash_old)

    msg = generate_ntfy_message(
        courseid_to_coursedata_new[course_id] for course_id, _ in updates
    )

    # Send update to clients
    max_retries = 5
    sent = False
    while max_retries != 0 and not sent:
        sent |= send_push_notification(msg, NTFY_TOPIC)
        max_retries -= 1
    if not sent:
        logger.error("Failed to notify clients")
        sys.exit(1)

    dump_to_cache(coursehash_new)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error("Uncaught exception", exc_info=True)
        sys.exit(2)
