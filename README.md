# Diskwala Telegram Downloader Bot

Telegram bot that accepts Diskwala sharing links and offers:
1. Direct Download button using the API's `direct_link`.
2. Download to Telegram using the authenticated `/fast-download` endpoint.

Built with Python 3.12, aiogram 3 and aiohttp. Designed for Northflank Docker deployment.

## Important security note

The API token previously pasted into chat should be treated as exposed. Revoke/rotate it with the API provider and use a fresh token only in Northflank environment variables. Never commit `.env` or a real token to GitHub.

## Features

- `/start`, `/help`, `/id`, `/health`
- Accepts Diskwala sharing URLs
- Handles documented list response and common wrapped JSON responses
- Shows filename, size and file ID
- Direct download and Telegram upload buttons
- Authenticated streaming download to a temporary file
- Temporary file cleanup after upload
- Handles API 401, 403, 429, timeouts and network errors
- Conservative Telegram upload size cap (default 45 MB)
- API concurrency limited to five simultaneous requests per process

## Local run

Python 3.12 is recommended.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set:
- `BOT_TOKEN`
- `DISKWALA_API_TOKEN` (fresh token)
- Optionally `ADMIN_IDS`

Then run:

```bash
python main.py
```

## Northflank deployment

1. Push this project to a private GitHub repository.
2. In Northflank, create a service from that repository.
3. Select **Build: Dockerfile** (Dockerfile is in the project root).
4. Add these environment variables in the service's Environment section:
   - `BOT_TOKEN` = token from BotFather
   - `DISKWALA_API_TOKEN` = fresh API bearer token
   - `DISKWALA_API_BASE_URL` = `https://disk-api.apipanel.in`
   - `MAX_TELEGRAM_UPLOAD_MB` = `45`
   - `API_TIMEOUT_SECONDS` = `180`
   - `DOWNLOAD_DIR` = `/tmp/diskwala_downloads`
   - `ADMIN_IDS` = your numeric Telegram ID (optional)
5. Deploy. Check logs for `Diskwala bot is starting`.
6. Open the bot in Telegram and send `/start`.

This is a long-running polling service. Do not configure it as a one-off job or serverless function. Run only one active replica using polling with the same bot token.

## How the API is used

List files:

```http
GET https://disk-api.apipanel.in/?url=<URL_ENCODED_DISKWALA_SHARING_LINK>
Authorization: Bearer <DISKWALA_API_TOKEN>
```

Download bytes:

```http
GET https://disk-api.apipanel.in/fast-download?file_id=<FILE_ID>
Authorization: Bearer <DISKWALA_API_TOKEN>
```

Health check:

```http
GET https://disk-api.apipanel.in/health
Authorization: Bearer <DISKWALA_API_TOKEN>
```

## File size and Telegram limits

The bot defaults to a 45 MB cap for Telegram uploads. This is intentionally conservative because Telegram upload limits depend on the Bot API endpoint and hosting setup. If the file is larger, the bot tells the user to use the Direct Download button. The direct link is supplied by the API response and may have its own expiry or access requirements.

The bot downloads Telegram-bound files to temporary disk storage, not RAM, and removes the temporary file after the upload attempt. Ensure the Northflank service has enough ephemeral disk space for the largest file you intend to send.

## API response expectations

The documented API returns an array containing objects such as:

```json
[
  {
    "direct_link": "https://example.invalid/fast-download?file_id=abc123",
    "file_id": "abc123",
    "file_name": "video.mp4",
    "m3u8_url": "https://example.invalid/stream?file_id=abc123",
    "size": "1024 MB",
    "sizebytes": 1073741824,
    "stream_url": "https://example.invalid/watch?file_id=abc123"
  }
]
```

Only `direct_link`, `file_id`, `file_name`, `size`, and `sizebytes` are needed for the current bot. Streaming/player links are not exposed as download buttons.

## Troubleshooting

- **401**: API token is invalid or expired. Generate a new token and update Northflank environment variables.
- **403**: API plan is inactive or its usage limit has been reached.
- **429**: API rate limit. The bot retries briefly and then asks the user to try again.
- **Bot does not start**: Check `BOT_TOKEN`, `DISKWALA_API_TOKEN`, build logs and service logs.
- **Telegram upload fails**: Lower `MAX_TELEGRAM_UPLOAD_MB`, use Direct Download, and check the Telegram Bot API upload limits for your setup.
- **Bot receives no updates**: Ensure only one active polling replica is running for this bot token.

## Notes

- The file metadata cache is in memory. After a service restart, the user should send the sharing link again.
- The bot does not bypass Diskwala access controls, API plans, or download restrictions.
- Use it only for files you are authorized to access and download.
