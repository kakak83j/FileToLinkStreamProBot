import aiohttp
import aiofiles
import os
import uuid

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

            # Chunk-by-chunk stream + async write — RAM me poori file kabhi nahi aati,
            # aur event loop bhi block nahi hota
            async with aiofiles.open(tmp_path, 'wb') as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):  # 1MB chunks
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
