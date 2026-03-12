# ETL Manager

An ETL pipeline that automatically collects course information from Canvas LMS and sends notifications via email, calendar, Todoist, and LINE.

## Features

- **Assignments** — Parses due dates, sends email/calendar/Todoist notifications
- **Announcements** — Generates summaries via OpenAI, sends email/LINE/Todoist notifications
- **Files** — Detects new file uploads, sends email/Todoist notifications
- **Other module items** — Detects Zoom sessions, quizzes, etc., sends email/Todoist notifications

Uses SQLite-based hash checking to prevent duplicate notifications.

## Structure

```
etl-manager/
├── manager.py       # Main ETL orchestration
├── trash_etl.py     # Canvas API wrapper
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
