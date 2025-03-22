import sqlite3
from aiogram import Bot, Dispatcher, types
from aiogram import Router
from aiogram.types import Message
from telethon import TelegramClient, events
from telethon.tl.types import UpdateMessageReactions, PeerChannel
from telethon.tl.functions.messages import GetMessagesReactionsRequest
import json
import time


import asyncio
# from aiogram.utils import executor
from log import logger
import os
from dotenv import load_dotenv

load_dotenv(".env")

# Replace these with your own values
API_TOKEN = os.getenv("API_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
PHONE_NUMBER = os.getenv("PHONE_NUMBER")

# Replace these with the channel IDs or usernames
channel1 = os.getenv("CHANNEL1")
channel2 = os.getenv("CHANNEL2")
channel1_telethon = int(channel1[4:])
channel2_telethon = int(channel2[4:])

if isinstance(channel1, str) and isinstance(channel2, str):
    channel1, channel2 = int(channel1), int(channel2)

# Initialize aiogram Bot and Dispatcher
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
rt = Router(name=__name__)

# Initialize Telethon client
telethon_client = TelegramClient('telethon_session', API_ID, API_HASH)

# Connect to the SQLite database (or create it if it doesn't exist)
conn = sqlite3.connect('message_mapping.db')
cursor = conn.cursor()

# Create the table to store message mappings if it doesn't exist
cursor.execute('''CREATE TABLE IF NOT EXISTS message_mapping
                  (original_channel INTEGER, original_id INTEGER, copied_channel INTEGER, copied_id INTEGER)''')

# Create a table to store message reactions
cursor.execute('''CREATE TABLE IF NOT EXISTS message_reactions
                  (channel_id INTEGER, 
                   message_id INTEGER, 
                   reaction_data TEXT,
                   last_updated INTEGER,
                   PRIMARY KEY (channel_id, message_id))''')

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
    try:
        if message.poll or message.forward_origin:
            copied_message = await bot.forward_message(target_channel,
                                                    message.chat.id,
                                                    message.message_id)
        else:
            copied_message = await bot.copy_message(
                target_channel,
                message.chat.id,
                message.message_id,
                reply_to_message_id=reply_to_message_id
            )
    except Exception as e:
        logger.error(f"Failed to forward message {message.message_id} in channel {target_channel}: {e}")
        copied_message = await bot.forward_message(target_channel,
                                                message.chat.id,
                                                message.message_id)
    
    return copied_message

@rt.channel_post()
async def copy_message(message: types.Message):
    logger.info(f"Copy message: {message.text}")
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

@rt.edited_channel_post()
async def handle_edited_message(message: types.Message):
    logger.info(f"Edited message: {message.text}")
    source_channel = message.chat.id
    target_channel = channel2 if source_channel == channel1 else channel1

    # Get the copied message ID from the database
    copied_message_id = get_copied_id(source_channel, message.message_id, target_channel)
    logger.info(f"{copied_message_id=}")
    if copied_message_id:

        await bot.edit_message_caption(target_channel, copied_message_id, caption=message.caption)

def get_stored_reactions(channel_id, message_id):
    """Get stored reactions for a message from the database."""
    cursor.execute("SELECT reaction_data FROM message_reactions WHERE channel_id = ? AND message_id = ?", 
                  (channel_id, message_id))
    result = cursor.fetchone()
    return json.loads(result[0]) if result else None

def store_reactions(channel_id, message_id, reaction_data):
    """Store reactions for a message in the database."""
    # Convert reaction data to JSON string
    reaction_json = json.dumps(reaction_data)
    current_time = int(time.time())
    
    # Use REPLACE to update if exists or insert if not
    cursor.execute("""
        REPLACE INTO message_reactions 
        (channel_id, message_id, reaction_data, last_updated) 
        VALUES (?, ?, ?, ?)
    """, (channel_id, message_id, reaction_json, current_time))
    conn.commit()

def reactions_changed(channel_id, message_id, current_reactions):
    """Check if reactions have changed compared to what's stored in the database."""
    stored_reactions = get_stored_reactions(channel_id, message_id)
    
    if stored_reactions is None:
        # First time seeing this message's reactions
        return True
    
    # Compare current reactions with stored reactions
    return stored_reactions != current_reactions

def to_aiogram_channel(channel_id):
    channel_id = "-100" + str(channel_id)
    return channel_id

# Add this method to periodically check for reaction updates
async def check_reactions():
    """Periodically check for reactions on messages in monitored channels."""
    while True:
        try:
            for channel_id in [channel1_telethon, channel2_telethon]:
                # Get recent messages from the channel
                async for message in telethon_client.iter_messages(PeerChannel(channel_id), limit=20):
                    if message.reactions:
                        # Convert reactions to a dictionary format for storage and comparison
                        reactions_dict = {}
                        if hasattr(message.reactions, 'results'):
                            for reaction in message.reactions.results:
                                reaction_type = str(reaction.reaction.emoticon)
                                reaction_count = reaction.count
                                reactions_dict[reaction_type] = reaction_count
                        
                        # logger.info(f"Reactions for message {message.id} in channel {channel_id}: {reactions_dict}")
                        # Check if reactions have changed
                        if reactions_changed(channel_id, message.id, reactions_dict):
                            logger.info(f"Reactions changed for message {message.id} in channel {channel_id}")
                            logger.info(f"New reactions: {reactions_dict}")
                            
                            # Store the updated reactions
                            store_reactions(channel_id, message.id, reactions_dict)
                            
                            # Find the corresponding message in the other channel
                            target_channel = channel2_telethon if channel_id == channel1_telethon else channel1_telethon
                            target_channel_aiogram = channel2 if channel_id == channel1_telethon else channel1
                            source_channel_aiogram = channel1 if channel_id == channel1_telethon else channel2
                            
                            copied_message_id = get_copied_id(to_aiogram_channel(channel_id), message.id, to_aiogram_channel(target_channel))
                            
                            if copied_message_id:
                                logger.info(f"Corresponding message {copied_message_id} in channel {target_channel}")
                                
                                # Get reactions for the copied message in the target channel
                                target_reactions_dict = get_stored_reactions(target_channel, copied_message_id) or {}
                                
                                # Combine reactions from both channels
                                combined_reactions = {}
                                for emoji in set(list(reactions_dict.keys()) + list(target_reactions_dict.keys())):
                                    combined_reactions[emoji] = (reactions_dict.get(emoji, 0) + 
                                                               target_reactions_dict.get(emoji, 0))
                                
                                # Create the reactions summary text
                                reactions_text = "---\n"
                                for emoji, count in combined_reactions.items():
                                    reactions_text += f"{emoji} {count} "
                                
                                # Get original message content
                                try:
                                    # Retrieve the source message using telethon first
                                    source_message = await telethon_client.get_messages(
                                        PeerChannel(channel_id), ids=[message.id])
                                    source_message = source_message[0]
                                    
                                    # Retrieve the target message
                                    target_message = await telethon_client.get_messages(
                                        PeerChannel(target_channel), ids=[copied_message_id])
                                    target_message = target_message[0]
                                    
                                    # Update both messages with the reactions summary using aiogram
                                    # First, update the source message
                                    if source_message.text:
                                        # Check if the message already has a reaction summary
                                        message_text = source_message.text
                                        if "---" in message_text:
                                            message_text = message_text.split("---")[0].strip()
                                        
                                        # Add the reactions summary
                                        new_text = f"{message_text}\n{reactions_text}"
                                        await bot.edit_message_text(
                                            text=new_text,
                                            chat_id=source_channel_aiogram,
                                            message_id=message.id
                                        )
                                    elif source_message.caption:
                                        # Handle messages with captions (media messages)
                                        caption = source_message.caption
                                        if "---" in caption:
                                            caption = caption.split("---")[0].strip()
                                        
                                        new_caption = f"{caption}\n{reactions_text}"
                                        await bot.edit_message_caption(
                                            caption=new_caption,
                                            chat_id=source_channel_aiogram,
                                            message_id=message.id
                                        )
                                    
                                    # Now update the target message
                                    if target_message.text:
                                        message_text = target_message.text
                                        if "---" in message_text:
                                            message_text = message_text.split("---")[0].strip()
                                        
                                        new_text = f"{message_text}\n{reactions_text}"
                                        await bot.edit_message_text(
                                            text=new_text,
                                            chat_id=target_channel_aiogram,
                                            message_id=copied_message_id
                                        )
                                    elif target_message.caption:
                                        caption = target_message.caption
                                        if "---" in caption:
                                            caption = caption.split("---")[0].strip()
                                        
                                        new_caption = f"{caption}\n{reactions_text}"
                                        await bot.edit_message_caption(
                                            caption=new_caption,
                                            chat_id=target_channel_aiogram,
                                            message_id=copied_message_id
                                        )
                                    
                                    logger.info(f"Updated both messages with reaction summary: {reactions_text}")
                                except Exception as e:
                                    logger.error(f"Error updating messages with reactions: {e}")
                                
                                # Get detailed reaction info if needed
                                try:
                                    reactions = await telethon_client(GetMessagesReactionsRequest(
                                        peer=PeerChannel(channel_id),
                                        id=[message.id]
                                    ))
                                    logger.info(f"Detailed reactions for message {message.id}: {reactions}")
                                except Exception as e:
                                    logger.error(f"Error getting detailed reactions: {e}")
        except Exception as e:
            logger.error(f"Error in check_reactions: {e}")
        
        # Wait before checking again
        await asyncio.sleep(30)  # Check every 30 seconds

async def run_telethon():
    """Run the Telethon client."""
    await telethon_client.start(phone=PHONE_NUMBER)
    logger.info("Telethon client started")
    await telethon_client.run_until_disconnected()

async def run_aiogram():
    """Run the aiogram Bot and Dispatcher."""
    dp.include_router(rt)
    await dp.start_polling(bot)

async def main():
    """Run both Telethon and aiogram clients concurrently."""
    await asyncio.gather(
        run_telethon(),
        run_aiogram(),
        check_reactions()  # Add the periodic reaction checker
    )

if __name__ == '__main__':
    logger.info("Script is running. Press Ctrl+C to stop.")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Script stopped by user.")
    finally:
        # Close the database connection when the script ends
        conn.close()
