from telethon import events, Button
from app.streamer.manager import session_manager
from app.database.connection import files_col, users_col, settings
from app.models.schemas import FileMetadata, User
from app.utils.helpers import generate_short_code
from app.utils.fsub import is_user_fsubbed
from app.utils.rate_limit import check_rate_limit
import datetime
import logging
import aiohttp
import aiofiles
import os
import uuid
@bot.on(events.NewMessage(pattern=r'https?://[^\s]+'))
    async def link_handler(event):
        """Handle direct download links and convert to permanent bot link"""

        # Ban Check
        user_data = await users_col.find_one({"user_id": event.sender_id})
        if user_data and user_data.get('is_banned'):
            return await event.reply("🚫 You are banned from using this bot.")

        # Rate Limit Check
        if not await check_rate_limit(event.sender_id):
            return await event.reply("⚠️ **Slow down!** Please wait a moment before sending more files.")

        # Force Sub Check
        if not await is_user_fsubbed(bot, event.sender_id):
            return await event.reply(
                "❌ **Access Denied!**\n\n"
                "You must join our channels to use this bot.",
                buttons=[[Button.url("Join Channel", "https://t.me/cantarellabots")]]
            )

        url = event.raw_text.strip()
        tmp_path = None

        try:
            filename = url.split('/')[-1].split('?')[0] or "downloaded_file"
            tmp_path = f"/tmp/{uuid.uuid4().hex}_{filename}"

            timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return await event.reply("❌ Failed to fetch the file from the given URL.")

                    async with aiofiles.open(tmp_path, 'wb') as f:
                        async for chunk in resp.content.iter_chunked(1024 * 1024):
                            await f.write(chunk)

            msg = await event.reply("📤 Uploading to Telegram...")
            file_msg = await bot.send_file(event.chat_id, tmp_path, caption=filename)

            if file_msg.document:
                doc = file_msg.document
                file_id = f"{doc.id}_{doc.access_hash}"
                file_name = filename
                file_size = doc.size
                mime_type = doc.mime_type or "application/octet-stream"
            else:
                return await event.reply("❌ Failed to upload file to Telegram.")

        except Exception as e:
            logger.error(f"Link handler error: {e}")
            return await event.reply(f"❌ Error processing link: {str(e)[:100]}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

        # Generate permanent link
        short_code = generate_short_code()
        expiry_time = None  # Permanent

        file_meta = FileMetadata(
            file_id=file_id,
            file_unique_id=str(file_msg.id),
            filename=file_name,
            mime_type=mime_type,
            file_size=file_size,
            uploader_id=event.sender_id,
            short_code=short_code,
            chat_id=event.chat_id,
            message_id=file_msg.id,
            expiry_time=expiry_time
        )

        await files_col.insert_one(file_meta.dict())

        download_url = f"{settings.BASE_URL}/dl/{short_code}"
        stream_url = f"{settings.BASE_URL}/watch/{short_code}"

        caption = (
            f"✅ **Permanent Link Generated!**\n\n"
            f"📁 **File:** `{file_name}`\n"
            f"⚖️ **Size:** `{file_size / (1024*1024):.2f} MB`\n"
            f"⏳ **Expiry:** `Permanent`\n\n"
            f"📥 **Download:** {download_url}\n"
            f"🎬 **Stream:** {stream_url}"
        )

        await event.reply(
            caption,
            buttons=[
                [Button.url("Download", download_url), Button.url("Watch Online", stream_url)],
                [Button.inline("Delete Link", f"del_{short_code}".encode())]
            ]
        )
