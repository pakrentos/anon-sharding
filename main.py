import sqlite3
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from log import logger
import os

# Replace these with your own values
API_TOKEN = os.environ["API_TOKEN"]

# Replace these with the channel IDs or usernames
channel1 = os.getenv("CHANNEL1")
channel2 = os.getenv("CHANNEL2")

if isinstance(channel1, str) and isinstance(channel2, str):
    channel1, channel2 = int(channel1), int(channel2)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# Connect to the SQLite database (or create it if it doesn't exist)
conn = sqlite3.connect('message_mapping.db')
cursor = conn.cursor()

# Create the table to store message mappings if it doesn't exist
cursor.execute('''CREATE TABLE IF NOT EXISTS message_mapping
                  (original_channel INTEGER, original_id INTEGER, copied_channel INTEGER, copied_id INTEGER)''')

def store_mapping(original_channel, original_id, copied_channel, copied_id):
    cursor.execute("INSERT INTO message_mapping VALUES (?, ?, ?, ?)", (original_channel, original_id, copied_channel, copied_id))
    conn.commit()

def get_original_id(original_channel, copied_id, copied_channel):
    cursor.execute("SELECT original_id FROM message_mapping WHERE original_channel = ? AND copied_id = ? AND copied_channel = ?", (original_channel, copied_id, copied_channel))
    result = cursor.fetchone()
    return result[0] if result else None

def get_copied_id(original_channel, original_id, copied_channel):
    cursor.execute("SELECT copied_id FROM message_mapping WHERE original_channel = ? AND original_id = ? AND copied_channel = ?", (original_channel, original_id, copied_channel))
    result = cursor.fetchone()
    return result[0] if result else None

async def forward_media(message: types.Message, target_channel: int, reply_to_message_id: int = None):
    if message.photo:
        copied_message = await bot.send_photo(target_channel, message.photo[-1].file_id, caption=message.caption, reply_to_message_id=reply_to_message_id)
    elif message.video:
        copied_message = await bot.send_video(target_channel, message.video.file_id, caption=message.caption, reply_to_message_id=reply_to_message_id)
    elif message.document:
        copied_message = await bot.send_document(target_channel, message.document.file_id, caption=message.caption, reply_to_message_id=reply_to_message_id)
    elif message.audio:
        copied_message = await bot.send_audio(target_channel, message.audio.file_id, caption=message.caption, reply_to_message_id=reply_to_message_id)
    elif message.sticker:
        copied_message = await bot.send_audio(target_channel, message.sticker.file_id, caption=message.caption, reply_to_message_id=reply_to_message_id)
    elif message.poll:
        copied_message = await bot.forward_message(target_channel,
                                                   message.chat.id,
                                                   message.message_id)
    elif message.is_forward():
        copied_message = await bot.forward_message(target_channel,
                                               message.chat.id,
                                               message.message_id)
    else:
        if message.text:
            copied_message = await bot.send_message(target_channel, message.text, reply_to_message_id=reply_to_message_id)
        else:
            # Skip sending the message if it doesn't have any text content
            return None
    
    return copied_message

@dp.channel_post_handler(lambda message: message.chat.id in [channel1, channel2], content_types=types.ContentTypes.ANY)
async def copy_message(message: types.Message):
    source_channel = message.chat.id
    logger.info(f"{message.chat.id=}")
    logger.info(f"{message.message_id=}")
    logger.info(f"{message.reply_to_message.message_id if message.reply_to_message else None=}")
    target_channel = channel2 if source_channel == channel1 else channel1

    # Check if the message is a reply to another message
    if message.reply_to_message:
        # Get the original message ID of the replied message
        copied_reply_id = message.reply_to_message.message_id
        # Check if the original reply ID exists in the database for the source channel
        original_reply_id = get_original_id(target_channel, copied_reply_id, source_channel)
        copied_original_reply_id = get_copied_id(source_channel, copied_reply_id, target_channel)
        if original_reply_id is not None:
            # Send the message as a reply to the copied message in the target channel
            copied_message = await forward_media(message, target_channel, reply_to_message_id=original_reply_id)
        else:
            # If the original reply ID doesn't exist in the database, send the message without a reply
            copied_message = await forward_media(message, target_channel, reply_to_message_id=copied_original_reply_id)
    else:
        # If the message is not a reply, send it as a new message
        copied_message = await forward_media(message, target_channel)

    logger.info(f"{copied_message.message_id=}")

    # Store the mapping of original message ID to copied message ID in the database
    store_mapping(source_channel, message.message_id, target_channel, copied_message.message_id)

@dp.edited_channel_post_handler(lambda message: message.chat.id in [channel1, channel2], content_types=types.ContentTypes.ANY)
async def handle_edited_message(message: types.Message):
    source_channel = message.chat.id
    target_channel = channel2 if source_channel == channel1 else channel1

    # Get the copied message ID from the database
    copied_message_id = get_copied_id(source_channel, message.message_id, target_channel)
    if copied_message_id:
        await bot.edit_message_caption(target_channel, copied_message_id, caption=message.caption)

if __name__ == '__main__':
    logger.info("Script is running. Press Ctrl+C to stop.")
    executor.start_polling(dp, skip_updates=True)

# Close the database connection when the script ends
conn.close()
