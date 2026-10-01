---
name: cron-scheduler-skill
description: Wait a set time before the next step, or set a workflow's repeating schedule, with the Cron Scheduler (every few seconds up to monthly, in a chosen time zone).
allowed-tools: cron_scheduler
metadata:
  author: opencompany
  version: "2.0"
  category: automation

---

# Cron Scheduler

The **Cron Scheduler** node does two jobs:

- **Your `cron_scheduler` tool.** One call waits once for the time its fields describe, then returns. Use it to pause before the next step. It does not set up anything that repeats.
- **A workflow trigger**, as the first node. Once the workflow is started, it runs the workflow on the schedule its fields describe, until the workflow is paused or reset.

There is no cron-expression field. Set `frequency`, then only the fields that frequency uses.

To give an agent the tool, connect the Cron Scheduler node to the agent's Tools input.

## Fields

| `frequency` | Fields it uses | One tool call waits |
|---|---|---|
| `seconds` | `interval`: 5 to 59 | `interval` seconds |
| `minutes` | `interval_minutes`: 1 to 59 | that many minutes |
| `hours` | `interval_hours`: 1 to 23 | that many hours |
| `days` | `daily_time` | 24 hours |
| `weeks` | `weekday`, `weekly_time` | 7 days |
| `months` | `month_day`, `monthly_time` | 30 days |
| `once` | none | no time at all |

- `daily_time` and `weekly_time`: `00:00`, `02:00`, `04:00`, `06:00`, `08:00`, `09:00`, `10:00`, `12:00`, `14:00`, `16:00`, `18:00`, `20:00` or `22:00`.
- `weekday`: `"0"` (Sunday) to `"6"` (Saturday).
- `month_day`: `"1"` to `"28"`, or `"L"` for the last day of the month.
- `monthly_time`: `HH:MM`, default `09:00`.
- `timezone`: `UTC` (the default), `America/New_York`, `America/Los_Angeles`, `Europe/London`, `Europe/Berlin`, `Asia/Tokyo` or `Asia/Kolkata`.

## As a tool

A call holds up your run for the whole wait. Keep it to `seconds` or `minutes`. `days`, `weeks` and `months` wait a day or more, which is longer than a run step may take.

Wait 30 seconds:

```json
{"frequency": "seconds", "interval": 30}
```

Wait 10 minutes:

```json
{"frequency": "minutes", "interval_minutes": 10}
```

The result reports the wait:

| Field | Meaning |
|---|---|
| `waited_seconds` | how long the call waited |
| `scheduled_time`, `triggered_at` | when the wait was due to end, and when it ended |
| `schedule` | the fields in words, such as "Every 10 minutes" |
| `message` | a one-line summary |

## As a workflow trigger

- Starting the workflow starts the schedule. Pause stops new runs, Resume continues them, and Reset removes the schedule.
- `once` runs the workflow a single time, when it is started.
- `seconds`, `minutes` and `hours` count from the top of the minute, hour or day. So every 7 minutes runs at :00, :07 ... :56, then at :00 again.
- `days`, `weeks` and `months` run at their time in `timezone`. With `month_day` `"L"`, a run lands on the 31st, the 30th, or February's 28th or 29th, as each month needs.
- There is no weekdays-only frequency. To work only on weekdays, use `days` and skip weekend runs in the workflow.

Each run starts with the trigger's output: `timestamp` (when it fired), `frequency`, `timezone`, `schedule` (the fields in words) and `cron_expression` (the schedule string it runs on).

Daily at 09:00, New York time:

```json
{"frequency": "days", "daily_time": "09:00", "timezone": "America/New_York"}
```

Mondays at 10:00, Tokyo time:

```json
{"frequency": "weeks", "weekday": "1", "weekly_time": "10:00", "timezone": "Asia/Tokyo"}
```

The last day of every month at 18:00:

```json
{"frequency": "months", "month_day": "L", "monthly_time": "18:00"}
```
