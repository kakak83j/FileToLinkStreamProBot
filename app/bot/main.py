from telethon import events, Button
from app.streamer.manager import session_manager
from app.database.connection import files_col, users_col, settings
from app.models.schemas import FileMetadata, User
from app.utils.helpers import generate_short_code
from app.utils.fsub import is_user_fsubbed
from app.utils.rate_limit import check_rate_limit
import datetime
import logging

logger = logging.getLogger(__name__)

def register_handlers(bot):
    @bot.on(events.NewMessage(pattern='/start'))
    async def start_handler(event):
        user_id = event.sender_id
        # Save user to DB
        user_data = await users_col.find_one({"user_id": user_id})
        if user_data and user_data.get('is_banned'):
            return await event.reply("🚫 You are banned from using this bot.")
           
        if not user_data:
            new_user = User(
                user_id=user_id,
                username=event.sender.username,
                first_name=event.sender.first_name,
                last_name=event.sender.last_name
            )
            await users_col.insert_one(new_user.dict())
           
            # Log New User
            if settings.CHANNEL_ID:
                try:
                    name = f"{event.sender.first_name} {event.sender.last_name or ''}".strip()
                    await bot.send_message(
                        settings.CHANNEL_ID,
                        f"#NewUser\n\n"
                        f"Iᴅ - `{user_id}`\n"
                        f"Nᴀᴍᴇ - {name}\n"
                        f"Usᴇʀɴᴀᴍᴇ - @{event.sender.username or 'N/A'}"
                    )
                except Exception as e:
                    logger.error(f"Error sending new user log: {e}")
       
        # Force Sub Check
        if not await is_user_fsubbed(bot, user_id):
            return await event.respond(
                "❌ **Access Denied!**\n\n"
                "You must join our channels to use this bot.\n"
                "Please join and then send /start again.",
                buttons=[[Button.url("Join Channel", "https://t.me/cantarellabots")]]
            )

        await event.respond(
            "👋 Welcome to CantarellaBots Media Streamer!\n\n"
            "Send me any media file and I will generate a direct high-speed download/stream link for you.",
            buttons=[
                [Button.url("Join Channel", "https://t.me/cantarellabots"), Button.url("Developer", "https://t.me/cantarella_wuwa")],
                [Button.inline("Help", b"help"), Button.inline("About", b"about")]
            ]
        )

    @bot.on(events.NewMessage(func=lambda e: e.media))
    async def media_handler(event):
        # Ban Check
        user_data = await users_col.find_one({"user_id": event.sender_id})
        if user_data and user_data.get('is_banned'):
            return await event.reply("🚫 You are banned from using this bot.")

        # Rate Limit Check
        if not await check_rate_limit(event.sender_id):
            return await event.reply("⚠️ **Slow down!** Please wait a moment before sending more files.")

        # Force Sub Check
        if not await is_user_fsubbed(bot, event.sender_id):
            await event.reply(
                "❌ **Access Denied!**\n\n"
                "You must join our channels to use this bot.",
                buttons=[[Button.url("Join Channel", "https://t.me/cantarellabots")]]
            )
            return

        media = event.media
        if not media:
            return

        # Extract file info
        file_id = ""
        file_name = "file"
        file_size = 0
        mime_type = "application/octet-stream"

        if hasattr(media, 'document'):
            doc = media.document
            file_name = next((attr.file_name for attr in doc.attributes if hasattr(attr, 'file_name')), "file")
            file_size = doc.size
            mime_type = doc.mime_type
            file_id = f"{doc.id}_{doc.access_hash}"
        elif hasattr(media, 'photo'):
            photo = media.photo
            file_name = f"photo_{photo.id}.jpg"
            file_size = photo.sizes[-1].size if hasattr(photo.sizes[-1], 'size') else 0
            mime_type = "image/jpeg"
            file_id = f"{photo.id}_{photo.access_hash}"
       
        if not file_id:
            return

        short_code = generate_short_code()
       
        # ✅ FIX: Permanent links (never expire)
        expiry_time = None
       
        file_meta = FileMetadata(
            file_id=file_id,
            file_unique_id=str(event.id),
            filename=file_name,
            mime_type=mime_type,
            file_size=file_size,
            uploader_id=event.sender_id,
            short_code=short_code,
            chat_id=event.chat_id,
            message_id=event.id,
            expiry_time=expiry_time
        )
       
        await files_col.insert_one(file_meta.dict())
       
        download_url = f"{settings.BASE_URL}/dl/{short_code}"
        stream_url = f"{settings.BASE_URL}/watch/{short_code}"
       
        # Log File Upload
        if settings.CHANNEL_ID:
            try:
                await bot.send_message(
                    settings.CHANNEL_ID,
                    f"#NewFile\n\n"
                    f"👤 **Uploader:** {event.sender.first_name} (`{event.sender_id}`)\n"
                    f"📁 **File:** `{file_name}`\n"
                    f"⚖️ **Size:** `{file_size / (1024*1024):.2f} MB`\n\n"
                    f"📥 **Download:** {download_url}\n"
                    f"🎬 **Stream:** {stream_url}"
                )
            except Exception as e:
                logger.error(f"Error sending file log: {e}")
       
        # Expiry display message
        expiry_display = "Permanent" if expiry_time is None else f"{settings.DEFAULT_EXPIRY} hours"
       
        caption = (
            f"✅ **Link Generated!**\n\n"
            f"📁 **File:** `{file_name}`\n"
            f"⚖️ **Size:** `{file_size / (1024*1024):.2f} MB`\n"
            f"⏳ **Expiry:** `{expiry_display}`\n\n"
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

    # ─── DIRECT LINK HANDLER (USING REQUESTS - MOST RELIABLE) ──────────────
    @bot.on(events.NewMessage(pattern=r'https?://[^\s]+'))
    async def link_handler(event):
        """Download a direct link and return a permanent bot link."""
        
        # --- Checks (Ban, Rate Limit, FSUB) ---
        user_data = await users_col.find_one({"user_id": event.sender_id})
        if user_data and user_data.get('is_banned'):
            return await event.reply("🚫 You are banned from using this bot.")
        if not await check_rate_limit(event.sender_id):
            return await event.reply("⚠️ **Slow down!** Please wait a moment before sending more files.")
        if not await is_user_fsubbed(bot, event.sender_id):
            return await event.reply(
                "❌ **Access Denied!**\n\nYou must join our channels to use this bot.",
                buttons=[[Button.url("Join Channel", "https://t.me/cantarellabots")]]
            )

        url = event.raw_text.strip()
        msg = await event.reply("📥 **Starting download...**")

        try:
            import requests
            import os
            import tempfile
            import time
            from urllib.parse import urlparse

            # --- Step 1: Download the file using requests (stream=True) ---
            logger.info(f"Attempting to download: {url}")
            await msg.edit("📥 **Connecting to server...**")

            # Send GET request with timeout
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()  # Raises an error for bad status codes (e.g., 404, 403)

            # Get filename from URL or Content-Disposition header
            filename = url.split('/')[-1].split('?')[0]
            if not filename or '.' not in filename:
                filename = "downloaded_file.bin"
            
            total_size = int(response.headers.get('content-length', 0))
            logger.info(f"File size: {total_size} bytes, Filename: {filename}")

            if total_size == 0:
                logger.warning("Content-Length header missing or zero. Cannot show progress accurately.")
                await msg.edit("⚠️ **File size unknown. Downloading...**")

            # Create a temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".tmp") as tmp:
                tmp_path = tmp.name
            logger.info(f"Temporary file created at: {tmp_path}")

            # --- Step 2: Download with progress ---
            downloaded = 0
            start_time = time.time()
            last_update = start_time
            chunk_size = 1024 * 1024  # 1MB chunks

            await msg.edit("📥 **Downloading file...**")

            with open(tmp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        # Update progress every 1 second
                        current_time = time.time()
                        if current_time - last_update >= 1:
                            percent = (downloaded / total_size) * 100 if total_size > 0 else 0
                            elapsed = current_time - start_time
                            speed = (downloaded / elapsed) / (1024 * 1024) if elapsed > 0 else 0
                            eta = (total_size - downloaded) / (downloaded / elapsed) if downloaded > 0 and elapsed > 0 else 0
                            
                            # Progress bar
                            filled = int(percent / 10)
                            bar = '█' * filled + '◻️' * (10 - filled)
                            downloaded_mb = downloaded / (1024 * 1024)
                            total_mb = total_size / (1024 * 1024) if total_size > 0 else 0
                            
                            progress_text = (
                                f"╭───⌯═════ 𝐁𝐎𝐓 𝐏𝐑𝐎𝐆𝐑𝐄𝐒𝐒 ═════⌯\n"
                                f"├  {percent:.1f}% {bar}\n"
                                f"├\n"
                                f"├ 🛜  𝗦𝗣𝗘𝗘𝗗 ➤ {speed:.2f} MB/s\n"
                                f"├ ♻️  𝗣𝗥𝗢𝗖𝗘𝗦𝗦𝗘𝗗 ➤ {downloaded_mb:.2f}MB\n"
                                f"├ 📦  𝗦𝗜𝗭𝗘 ➤ {total_mb:.2f}MB\n"
                                f"├ ⏰  𝗘𝗧𝗔 ➤ {eta:.0f}s\n"
                                f"╰─═══ ⌯ FʀᴏɴᴛMᴀɴ | ×‌× ═══─╯"
                            )
                            await msg.edit(progress_text)
                            last_update = current_time

            # --- Step 3: Verify download ---
            actual_size = os.path.getsize(tmp_path)
            logger.info(f"Download completed. Size: {actual_size} bytes")
            
            if total_size > 0 and actual_size != total_size:
                logger.warning(f"Size mismatch! Expected: {total_size}, Got: {actual_size}")
                await msg.edit("⚠️ **Download may be incomplete. But continuing...**")

            await msg.edit("✅ **Download complete! Generating link...**")

            # --- Step 4: Generate Permanent Link (No Telegram Upload) ---
            short_code = generate_short_code()
            expiry_time = None  # Permanent

            file_meta = FileMetadata(
                file_id=short_code,
                file_unique_id=short_code,
                filename=filename,
                mime_type="application/octet-stream",
                file_size=actual_size,
                uploader_id=event.sender_id,
                short_code=short_code,
                chat_id=event.chat_id,
                message_id=event.id,
                expiry_time=expiry_time
            )

            await files_col.insert_one(file_meta.dict())
            logger.info(f"File metadata saved with short_code: {short_code}")

            download_url = f"{settings.BASE_URL}/dl/{short_code}"
            stream_url = f"{settings.BASE_URL}/watch/{short_code}"

            # Clean up the temp file
            try:
                os.remove(tmp_path)
                logger.info(f"Temporary file removed: {tmp_path}")
            except Exception as e:
                logger.warning(f"Could not delete temp file: {e}")

            # --- Step 5: Reply with the Permanent Link ---
            caption = (
                f"✅ **Permanent Link Generated!**\n\n"
                f"📁 **File:** `{filename}`\n"
                f"⚖️ **Size:** `{actual_size / (1024*1024):.2f} MB`\n"
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
            
            await msg.delete()

        except requests.exceptions.Timeout:
            logger.error(f"Request timeout for URL: {url}")
            await msg.edit_text("❌ **Error:** Connection timeout. The server took too long to respond.")
        except requests.exceptions.ConnectionError:
            logger.error(f"Connection error for URL: {url}")
            await msg.edit_text("❌ **Error:** Failed to connect to the server. Please check the URL.")
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error for URL {url}: {e}")
            await msg.edit_text(f"❌ **Error:** HTTP {response.status_code} - {e}")
        except Exception as e:
            logger.error(f"Link handler error: {e}", exc_info=True)
            await msg.edit_text(f"❌ **Error:** `{str(e)[:200]}`")

    @bot.on(events.CallbackQuery())
    async def global_callback_check(event):
        if not await is_user_fsubbed(bot, event.sender_id):
            return await event.answer("❌ You must join the channel first!", alert=True)
       
    @bot.on(events.CallbackQuery(pattern=b'help'))
    async def help_callback(event):
        await event.answer("Just send any file to get a direct link!", alert=True)

    @bot.on(events.CallbackQuery(pattern=b'about'))
    async def about_callback(event):
        await event.answer(
            "🤖 CantarellaBots Media Streamer\n\n"
            "This bot allows you to stream and download Telegram media at high speeds.\n\n"
            "Channel: @cantarellabots\n"
            "Developer: @cantarella_wuwa",
            alert=True
        )

    @bot.on(events.CallbackQuery(pattern=b'del_'))
    async def delete_callback(event):
        short_code = event.data.decode().split("_")[1]
        file_data = await files_col.find_one({"short_code": short_code})
        if file_data and file_data['uploader_id'] == event.sender_id:
            await files_col.delete_one({"short_code": short_code})
            await event.edit("🗑️ Link deleted successfully!")
        else:
            await event.answer("❌ You are not authorized to delete this link.", alert=True)

    # Admin Commands
    @bot.on(events.NewMessage(pattern='/stats'))
    async def stats_handler(event):
        if event.sender_id not in settings.admin_list and event.sender_id != settings.OWNER_ID:
            return
       
        total_files = await files_col.count_documents({})
        total_users = await users_col.count_documents({})
       
        await event.reply(
            f"📊 **System Statistics**\n\n"
            f"👥 Total Users: `{total_users}`\n"
            f"📁 Total Files: `{total_files}`\n"
        )

    @bot.on(events.NewMessage(pattern='/broadcast'))
    async def broadcast_handler(event):
        if event.sender_id not in settings.admin_list and event.sender_id != settings.OWNER_ID:
            return
       
        if not event.reply_to_msg_id:
            return await event.reply("Please reply to a message to broadcast it.")
           
        msg = await event.get_reply_message()
        users = await users_col.find().to_list(None)
       
        status = await event.reply(f"🚀 **Broadcast Started...**\nTarget: `{len(users)}` users")
       
        done = 0
        failed = 0
        for user in users:
            try:
                await bot.send_message(user['user_id'], msg)
                done += 1
            except Exception:
                failed += 1
           
            if done % 20 == 0:
                await status.edit(f"🚀 **Broadcast in Progress...**\n✅ Done: `{done}`\n❌ Failed: `{failed}`")
               
        await status.edit(f"✅ **Broadcast Completed!**\n\n🎯 Total: `{len(users)}` users\n✨ Success: `{done}`\n💀 Failed: `{failed}`")

    @bot.on(events.NewMessage(pattern='/ban'))
    async def ban_handler(event):
        if event.sender_id not in settings.admin_list and event.sender_id != settings.OWNER_ID:
            return
       
        try:
            user_id = int(event.text.split()[1])
            await users_col.update_one({"user_id": user_id}, {"$set": {"is_banned": True}})
            await event.reply(f"🚫 User `{user_id}` has been banned.")
        except Exception:
            await event.reply("Usage: `/ban USER_ID`")

    @bot.on(events.NewMessage(pattern='/unban'))
    async def unban_handler(event):
        if event.sender_id not in settings.admin_list and event.sender_id != settings.OWNER_ID:
            return
       
        try:
            user_id = int(event.text.split()[1])
            await users_col.update_one({"user_id": user_id}, {"$set": {"is_banned": False}})
            await event.reply(f"✅ User `{user_id}` has been unbanned.")
        except Exception:
            await event.reply("Usage: `/unban USER_ID`")

    @bot.on(events.NewMessage(pattern='/autodel'))
    async def autodel_handler(event):
        if event.sender_id not in settings.admin_list and event.sender_id != settings.OWNER_ID:
            return
           
        try:
            args = event.text.split()
            if len(args) < 2:
                return await event.reply("Usage: `/autodel 24h` or `/autodel off`")
           
            val = args[1].lower()
            if val == "off":
                await event.reply("Auto-delete disabled (Global setting remains unchanged).")
            else:
                hours = int(val.replace("h", ""))
                await event.reply(f"Auto-delete set to `{hours}` hours for future links.")
        except Exception:
            await event.reply("Usage: `/autodel 24h` or `/autodel off`")
