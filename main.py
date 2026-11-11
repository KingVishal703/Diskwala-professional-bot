import asyncio
import html
import logging
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile
)
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError

from config import (
    BOT_TOKEN, ADMIN_IDS, MAX_TELEGRAM_UPLOAD_MB, DOWNLOAD_DIR,
    API_TIMEOUT_SECONDS
)
from diskwala_api import DiskwalaAPI, DiskwalaAPIError, DiskwalaFileTooLarge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("diskwala-bot")

dp = Dispatcher()
api = DiskwalaAPI()
# Metadata is cached per bot process. If the bot restarts, users can resend the link.
file_cache: dict[tuple[int, str], dict] = {}
user_locks: dict[int, asyncio.Lock] = {}

URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
ALLOWED_HOST_PARTS = ("diskwala.com", "diskwala.litedns.xyz")


def is_diskwala_link(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").lower()
        return parsed.scheme in ("http", "https") and any(
            host == domain or host.endswith("." + domain)
            for domain in ALLOWED_HOST_PARTS
        )
    except Exception:
        return False


def human_size(size_bytes, fallback="Unknown"):
    try:
        value = int(size_bytes)
        if value < 0:
            return fallback
        units = ("B", "KB", "MB", "GB", "TB")
        amount = float(value)
        for unit in units:
            if amount < 1024 or unit == units[-1]:
                return f"{amount:.2f} {unit}"
            amount /= 1024
    except (TypeError, ValueError):
        pass
    return fallback


def file_keyboard(file_id: str, direct_link: str | None):
    buttons = []
    if direct_link:
        buttons.append([InlineKeyboardButton(
            text="⬇️ Direct Download",
            url=direct_link
        )])
    buttons.append([InlineKeyboardButton(
        text="📥 Download to Telegram",
        callback_data=f"tgdl:{file_id}"
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "👋 <b>Welcome to Diskwala Downloader!</b>\n\n"
        "Send me a Diskwala sharing link. I will show the file details "
        "and give you two options:\n"
        "• Direct download in your browser\n"
        "• Send the file to this Telegram chat\n\n"
        "Use /help to see instructions."
    )


@dp.message(Command("help"))
async def help_handler(message: Message):
    await message.answer(
        "<b>How to use</b>\n"
        "1. Copy a Diskwala sharing link.\n"
        "2. Send it here.\n"
        "3. Choose Direct Download or Download to Telegram.\n\n"
        "If a file is too large for this bot's configured Telegram upload "
        "limit, use Direct Download instead."
    )


@dp.message(Command("id"))
async def id_handler(message: Message):
    await message.answer(f"Your Telegram user ID: <code>{message.from_user.id}</code>")


@dp.message(F.text)
async def link_handler(message: Message):
    match = URL_RE.search(message.text or "")
    if not match:
        await message.answer("Please send a valid Diskwala sharing link.")
        return

    share_url = match.group(0).rstrip(".,);]}")
    if not is_diskwala_link(share_url):
        await message.answer(
            "This does not look like a supported Diskwala link.\n"
            "Please send a link from diskwala.com."
        )
        return

    status = await message.answer("🔎 <b>Checking your link...</b>")
    try:
        items = await api.list_files(share_url)
        if not items:
            await status.edit_text("No downloadable files were returned for this link.")
            return

        # The API may return multiple files. Show each one separately.
        for item in items[:10]:
            file_id = str(item.get("file_id") or "").strip()
            if not file_id:
                continue

            file_name = str(item.get("file_name") or "Unknown file")
            size = item.get("size") or human_size(item.get("sizebytes"))
            direct_link = item.get("direct_link")
            file_cache[(message.from_user.id, file_id)] = item

            safe_name = html.escape(file_name)
            safe_size = html.escape(str(size))
            text = (
                f"📄 <b>{safe_name}</b>\n"
                f"📦 Size: <b>{safe_size}</b>\n"
                f"🆔 File ID: <code>{html.escape(file_id)}</code>\n\n"
                "Choose your download method:"
            )
            await message.answer(
                text,
                reply_markup=file_keyboard(file_id, direct_link)
            )

        await status.delete()
    except DiskwalaAPIError as exc:
        log.warning("Diskwala API error: %s", exc)
        await status.edit_text(f"⚠️ <b>API error:</b> {html.escape(str(exc))}")
    except Exception:
        log.exception("Unexpected link-processing error")
        await status.edit_text("Something went wrong while processing this link. Please try again.")


@dp.callback_query(F.data.startswith("tgdl:"))
async def telegram_download_handler(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    file_id = callback.data.split(":", 1)[1]
    item = file_cache.get((callback.from_user.id, file_id))
    if not item:
        await callback.message.answer(
            "This file information has expired from the bot's cache. "
            "Please send the Diskwala link again."
        )
        return

    size_bytes = item.get("sizebytes")
    try:
        if size_bytes is not None and int(size_bytes) > MAX_TELEGRAM_UPLOAD_MB * 1024 * 1024:
            await callback.message.answer(
                f"⚠️ This file is larger than the bot's configured "
                f"{MAX_TELEGRAM_UPLOAD_MB} MB Telegram upload limit.\n"
                "Please use the Direct Download button."
            )
            return
    except (TypeError, ValueError):
        pass

    lock = user_locks.setdefault(callback.from_user.id, asyncio.Lock())
    if lock.locked():
        await callback.message.answer("Your previous download is still running. Please wait.")
        return

    async with lock:
        status = await callback.message.answer("⏳ <b>Preparing your Telegram file...</b>")
        temp_path = None
        try:
            DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
            suffix = Path(str(item.get("file_name") or "download.bin")).suffix[:12]
            with tempfile.NamedTemporaryFile(
                prefix="diskwala_", suffix=suffix, dir=DOWNLOAD_DIR, delete=False
            ) as temp_file:
                temp_path = Path(temp_file.name)

            actual_size = await api.download_file(
                file_id, temp_path, MAX_TELEGRAM_UPLOAD_MB * 1024 * 1024
            )

            filename = Path(str(item.get("file_name") or "diskwala_file")).name
            document = FSInputFile(temp_path, filename=filename)
            await status.edit_text("📤 <b>Uploading file to Telegram...</b>")
            await bot.send_document(
                chat_id=callback.message.chat.id,
                document=document,
                caption=f"✅ {html.escape(filename)}"
            )
            await status.delete()
        except DiskwalaFileTooLarge:
            await status.edit_text(
                f"⚠️ This file exceeds the configured {MAX_TELEGRAM_UPLOAD_MB} MB "
                "Telegram upload limit. Use the Direct Download button instead."
            )
        except DiskwalaAPIError as exc:
            log.warning("Download failed: %s", exc)
            await status.edit_text(f"❌ <b>Download failed:</b> {html.escape(str(exc))}")
        except (TelegramBadRequest, TelegramNetworkError) as exc:
            log.warning("Telegram upload error: %s", exc)
            await status.edit_text(
                "❌ Telegram could not upload this file. "
                "Try the Direct Download option."
            )
        except Exception:
            log.exception("Unexpected Telegram download error")
            await status.edit_text("❌ An unexpected error occurred during download.")
        finally:
            if temp_path:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    log.warning("Could not remove temporary file: %s", temp_path)


@dp.message(Command("health"))
async def health_handler(message: Message):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        return
    try:
        healthy = await api.health()
        await message.answer("✅ API healthy" if healthy else "⚠️ API health check failed")
    except DiskwalaAPIError as exc:
        await message.answer(f"⚠️ API check failed: {html.escape(str(exc))}")


async def main():
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        log.info("Diskwala bot is starting")
        await dp.start_polling(bot)
    finally:
        await api.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
