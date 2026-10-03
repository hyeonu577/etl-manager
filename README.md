# ETL Manager

An ETL pipeline that automatically collects course information from Canvas LMS and sends notifications via email, calendar, Todoist, and LINE.

## Features

- **Assignments** — Parses due dates, sends email/calendar/Todoist notifications
- **Lecture videos** — Reads the attendance deadline (학습인정기간) from LearningX, sends email/calendar/Todoist notifications
- **Announcements** — Generates summaries via OpenAI, sends email/LINE/Todoist notifications
- **Files** — Detects new file uploads, sends email/Todoist notifications
- **Other module items** — Detects Zoom sessions, quizzes, lecture videos with no open deadline, etc., sends email/Todoist notifications

Uses SQLite-based hash checking to prevent duplicate notifications.

Lecture video deadlines are not exposed by the Canvas API, so they are read from the LearningX API with a token issued through a Canvas LTI launch (no extra credentials).

- A deadline is registered once per lecture while it is still ahead and the lecture is unwatched, even if the lecture is not open for viewing yet. A changed deadline is registered again.
- A lecture first seen after its deadline, or already watched, is announced as an ordinary module item instead.
- If LearningX cannot be read for a course, lectures not seen before are announced as ordinary module items, the rest of the run continues, and the run is reported to the health check as failed (`/fail`).

## Structure

```
etl-manager/
├── manager.py       # Main ETL orchestration
├── trash_etl.py     # Canvas / LearningX API wrapper
├── test_*.py        # Unit tests
├── true_email/      # Email sending (submodule)
├── true_line/       # LINE messaging (submodule)
└── true_calendar/   # CalDAV calendar (submodule)
```

## Environment Variables

Set the following variables in a `.env` file:

| Variable | Description |
|---|---|
| `CANVAS_API_URL` | Canvas LMS API URL |
| `CANVAS_API_KEY` | Canvas API key |
| `TODOIST_API_TOKEN` | Todoist API token |
| `OPENAI_API_KEY_ETL` | OpenAI API key (for announcement summaries) |
| `HEALTHCHECK_ETL_MANAGER` | Health check endpoint URL |

## Dependencies

```
canvasapi
html2text
xxhash
openai
todoist-api-python
requests
caldav
icalendar
python-dotenv
```

## Usage

```bash
python manager.py
```

## Tests

```bash
python -m unittest
```
