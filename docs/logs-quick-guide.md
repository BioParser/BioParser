# BioParser logs: quick guide

Oct 9, 2026

Every BioParser container sends its logs to one place. You read and search them in Grafana at http://localhost:3000, and Loki keeps them for 7 days.

Alloy, Loki and Grafana share the `logs` network, so the app containers cannot reach Loki.

## Quick Start

1. Make your config file, if you do not have one yet: `cp .env.example .env`
2. In `.env`, set `GRAFANA_ADMIN_PASSWORD` to a strong password. Compose refuses to start without it. If the password has `$` or spaces, put it in single quotes.
3. Check that `COMPOSE_FILE` in `.env` ends with `:docker-compose.logging.yml`. An `.env` made before this change does not: add it to the end of your line, and copy the `GRAFANA_*` and `LOKI_*` lines from `.env.example`.
4. Start everything: `docker compose up -d --build`
5. Check the state with `docker compose ps`. After 1 to 2 minutes, the STATUS of `loki` and `grafana` shows `(healthy)`, and `alloy` shows `Up`.

## Open Grafana

On your own machine, open http://localhost:3000 and log in as `admin` with the password from `.env`.

Grafana listens only on `127.0.0.1`, so other computers cannot open it. If the stack runs on a remote GPU server, open an SSH tunnel from your laptop and keep it running:

```sh
ssh -N -L 3000:localhost:3000 you@gpu-server
```

Then open http://localhost:3000 on your laptop.

## Find and search logs

**Dashboard:** after login you land on **BioParser logs**. It shows log lines, errors and warnings per service, API requests by status code, and the newest errors. Pick services with the **Service** filter at the top. The list is read when the page opens, so reload the page to see a new service. To change the dashboard, edit `docker/grafana/provisioning/dashboards/bioparser-logs.json`; changes made in the Grafana UI cannot be saved.

**Health checks:** under the numbers at the top, the API, MinerU and vLLM each have a row. Docker calls their `/health` address every 10 to 30 seconds, and the panel reads those calls from their logs. Green (passing) means a check passed in that minute, red (failing) means the checks failed, and an empty stretch means no check was logged: the service was down or not answering. The parser worker and Redis do not log health checks, so they have no row.

**Browse:** in the left menu, open **Drilldown → Logs**. You see one row per service (`bioparser`, `mineru`, `vllm`, `redis` and the log services), each with a chart of how many lines it wrote. Click a service to read its lines and filter them by level.

**Search:** open **Explore**, choose the **Loki** data source, switch the editor to **Code**, paste a query and press **Run query**. Pick a short time range first (top right): small ranges are fast.

```logql
# All lines from the API
{service_name="bioparser"}

# Errors from every service
{service_name=~".+"} | detected_level=~"error|critical|fatal"

# API lines that contain a word, ignoring upper and lower case
{service_name="bioparser"} |~ "(?i)timeout"

# Everything about one PDF sent to /extract (the API logs the file's SHA-256 as checksum)
{service_name="bioparser"} | json | checksum="<sha256 of the file>"

# Failed requests
{service_name="bioparser"} | json | status_code >= 500

# Errors per service in the last 5 minutes, at each point in time (shown as a graph)
sum by (service_name) (count_over_time({service_name=~".+"} | detected_level=~"error|critical|fatal" [5m]))
```

How to read a query:

- `{service_name="bioparser"}` picks lines by **label**. Labels are indexed, so this part is fast. There are only two labels: `service_name` (the Compose service) and `container`.
- `|= "word"` keeps the lines that contain the word, with the same upper and lower case. `|~ "(?i)word"` ignores the case.
- `| json` turns the JSON line into fields, such as `level`, `logger`, `msg`, `checksum` or `status_code`. After it, `| field = value` filters on a field.
- `detected_level` is the level Loki finds in each line, such as `info`, `warn`, `error` or `critical`. It is `unknown` when Loki cannot tell, for example for Redis lines.

## Log your own fields

To make a value searchable, log it as a field. Our logger writes every `extra` value and every `log_context` value into the JSON line:

```python
import logging

from bioparser.logging_config import log_context

logger = logging.getLogger(__name__)

logger.info("pdf parsed", extra={"pages": 12, "parser": "mineru"})

with log_context(job_id=job_id):  # every line inside gets job_id
    logger.info("parse started")
```

Then find it with `{service_name=~".+"} | json | pages > 50`. Do not use these field names: `name`, `msg`, `message`, `level`, `logger`, `timestamp`. Python's logging refuses some of them, and our JSON formatter drops the others without a warning.

Do not turn such values into Loki labels in `docker/alloy/config.alloy`. Every new label value makes a new stream, and many streams make Loki slow.

## Everyday tasks

| Task | How |
| --- | --- |
| Change your Grafana password | In Grafana: your profile → **Change password** |
| Reset a lost password | `docker compose exec grafana grafana cli --homepath /usr/share/grafana admin reset-admin-password 'NEW-PASSWORD'` |
| Keep logs longer, for example 14 days | Set `LOKI_RETENTION_PERIOD=336h` in `.env`, then run `docker compose up -d loki` |
| Read Docker's own copy of the logs | `docker compose logs -f bioparser` |
| Turn central logs off | Remove `:docker-compose.logging.yml` from `COMPOSE_FILE` in `.env`, then run `docker compose up -d --remove-orphans` |

## FAQ

| What you see | What to do |
| --- | --- |
| `required variable GRAFANA_ADMIN_PASSWORD is missing a value` | Set `GRAFANA_ADMIN_PASSWORD` in `.env`. |
| Login fails after you changed `GRAFANA_ADMIN_PASSWORD` | Grafana reads it only on its first start. Use the old password, or reset it (Everyday tasks). |
| Grafana shows no logs | Check the time range (top right). Run `docker compose ps`: `loki` must show `(healthy)` and `alloy` must show `Up`. Then read `docker compose logs alloy` for errors. |
| A new container has no logs yet | Wait 15 seconds. Alloy looks for new containers every 15 seconds. |
| Some minutes of logs are missing | Loki was down for more than about 4 minutes, so Alloy gave up on some lines. Read them with `docker compose logs <service>`. |
| One very long line is missing | Lines over 256 KB are dropped. `docker compose logs alloy` shows `max entry size` or `line too big` for them. |
| Port 3000 is already in use | Set `GRAFANA_PORT=3001` in `.env`, then run `docker compose up -d grafana`. |
| Grafana warns `Skipping migration: Already executed, but not recorded in migration log` | Nothing to do. Grafana prints this once, on its first start with a new `grafana-data` volume, then records it and does not show it again. |
| `docker compose logs` mixes JSON and text lines | That is normal. BioParser writes JSON, and the other services keep their own formats. Read the logs in Grafana, where you can pick one service. In the terminal, follow one service, for example `docker compose logs -f mineru`, or hide the health checks: `docker compose logs -f \| grep -v /health`. |

## Rules

- **Never log secrets.** Passwords, tokens and API keys must not reach a log line. Everything a container prints is kept for 7 days, and everyone with the Grafana login can read it.
- **Treat logs as personal data.** They can hold IP addresses. Do not copy them outside the team.
- **Keep Loki closed.** Loki has no login. Do not publish its port, and do not add other services to the `logs` network.
