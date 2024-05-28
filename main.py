import sqlite3
from telethon import TelegramClient, events
from log import logger
import os


# Replace these with your own values
api_id = os.environ["API_ID"]
api_hash = os.environ["API_HASH"]
session_name = 'main'

# Replace these with the channel IDs or usernames
channel1 = os.getenv("CHANNEL1")
channel2 = os.getenv("CHANNEL2")

if isinstance(channel1, str) and isinstance(channel2, str):
    channel1, channel2 = int(channel1), int(channel2)

client = TelegramClient(session_name, api_id, api_hash)

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

@client.on(events.NewMessage(chats=[channel1, channel2]))
async def copy_message(event):
    message = event.message
    source_channel = event.chat_id
    logger.info(f"{event.chat_id=}")
    logger.info(f"{message.id=}")
    logger.info(f"{message.reply_to_msg_id=}")
    target_channel = channel2 if source_channel == channel1 else channel1

    # Check if the message is a reply to another message
    if message.reply_to_msg_id:
        # Get the original message ID of the replied message
        copied_reply_id = message.reply_to_msg_id
        # Check if the original reply ID exists in the database for the source channel
        original_reply_id = get_original_id(target_channel, copied_reply_id, source_channel)
        if not original_reply_id is None:
            # Send the message as a reply to the copied message in the target channel
            copied_message = await client.send_message(target_channel, message, reply_to=original_reply_id)
        else:
            # If the original reply ID doesn't exist in the database, send the message without a reply
            copied_message = await client.send_message(target_channel, message)
    else:
        # If the message is not a reply, send it as a new message
        copied_message = await client.send_message(target_channel, message)

    logger.info(f"{copied_message.id=}")

    # Store the mapping of original message ID to copied message ID in the database
    store_mapping(source_channel, message.id, target_channel, copied_message.id)

@client.on(events.MessageEdited(chats=[channel1, channel2]))
async def handle_edited_message(event):
    message = event.message
    source_channel = event.chat_id
    target_channel = channel2 if source_channel == channel1 else channel1

    # Get the copied message ID from the database
    copied_message_id = get_copied_id(source_channel, message.id, target_channel)
    if copied_message_id:
        await client.edit_message(target_channel, copied_message_id, message.text, file=message.media, link_preview=message.web_preview)

with client:
    logger.info("Script is running. Press Ctrl+C to stop.")
    client.run_until_disconnected()

# Close the database connection when the script ends
conn.close()
