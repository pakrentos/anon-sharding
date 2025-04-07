import sqlite3
import json
import time
import asyncio
import os
from dotenv import load_dotenv
from log import logger
import traceback

# Python-telegram-bot imports
from telegram import Update, Message, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CallbackContext
from telegram.constants import ParseMode

# Telethon imports (still needed for reactions)
from telethon import TelegramClient
from telethon.tl.types import PeerChannel
from telethon.tl.functions.messages import GetMessagesReactionsRequest

CUSTOM_EMOJI_TO_ID_MAP = {
    '(B)': 5224647090734375337,
    '[токнау]': 5307977565175029928,
    '(токнау)': 5305776162507596250,
    '𝕋𝕒𝕝': 5307935766553304360,
    '𝕜ℕ': 5305747476421027973,
    '𝕠𝕨': 5305495104142713367,
    '𝐓𝐚𝐥': 5307801694854191826,
    '𝐤𝐍': 5308050107172659858,
    '𝐨𝐰': 5305459232575857422,
    'юрец': 5307972372559568260,
    'гол': 5262944983400334596,
    'гоол': 5262983208609269585,
    'гооол': 5263001522349819939,
    'натер': 4978814394050806930,
}
ID_TO_CUSTOM_EMOJI_MAP = {v:k for k,v in CUSTOM_EMOJI_TO_ID_MAP.items()}

# Load environment variables
load_dotenv(".env")

# Configuration from environment variables
API_TOKEN = os.getenv("API_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
PHONE_NUMBER = os.getenv("PHONE_NUMBER")
MESSAGE_CHECK_FOR_REACTIONS_LIMIT = int(os.getenv("MESSAGE_CHECK_FOR_REACTIONS_LIMIT", 100))

# Channel configurations
channel1 = os.getenv("CHANNEL1")
channel2 = os.getenv("CHANNEL2")
channel1_telethon = int(channel1[4:])
channel2_telethon = int(channel2[4:])

if isinstance(channel1, str) and isinstance(channel2, str):
    channel1, channel2 = int(channel1), int(channel2)

# Initialize Telethon client (still needed for reaction handling)
telethon_client = TelegramClient('telethon_session', API_ID, API_HASH)

# Connect to SQLite database
conn = sqlite3.connect('message_mapping.db')
cursor = conn.cursor()

# Create tables if they don't exist
cursor.execute('''CREATE TABLE IF NOT EXISTS message_mapping
                (original_channel INTEGER, original_id INTEGER, copied_channel INTEGER, copied_id INTEGER)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS message_reactions
                (channel_id INTEGER, 
                 message_id INTEGER, 
                 reaction_data TEXT,
                 last_updated INTEGER,
                 PRIMARY KEY (channel_id, message_id))''')

# Database helper functions
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

def get_corresponding_message_id(channel_id, message_id, target_channel):
    """
    Get the corresponding message ID in the target channel,
    checking both directions in the database.
    """
    # First try as if this is the original message
    corresponding_id = get_copied_id(channel_id, message_id, target_channel)
    if corresponding_id:
        logger.info(f"Found corresponding message as original->copy: {message_id}->{corresponding_id}")
        return corresponding_id
    
    # Then try as if this is the copied message
    corresponding_id = get_original_id(target_channel, message_id, channel_id)
    if corresponding_id:
        logger.info(f"Found corresponding message as copy->original: {message_id}->{corresponding_id}")
        return corresponding_id
    
    # No correspondence found
    return None

def get_stored_reactions(channel_id, message_id):
    """Get stored reactions for a message from the database."""
    cursor.execute("SELECT reaction_data FROM message_reactions WHERE channel_id = ? AND message_id = ?", 
                  (channel_id, message_id))
    result = cursor.fetchone()
    return json.loads(result[0]) if result else {}

def store_reactions(channel_id, message_id, reaction_data):
    """Store reactions for a message in the database."""
    reaction_json = json.dumps(reaction_data)
    current_time = int(time.time())
    
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
        return True
    
    return stored_reactions != current_reactions

def to_ptb_channel(channel_id):
    """Convert a numeric channel ID to a format usable by python-telegram-bot."""
    if not str(channel_id).startswith('-100'):
        channel_id = "-100" + str(channel_id)
    return channel_id

# Reaction handling functions
async def extract_message_reactions(message: Message):
    """Extract reactions from a telethon message object into a dictionary."""
    reactions_dict = {}
    if hasattr(message, 'reactions') and message.reactions:
        if hasattr(message.reactions, 'results'):
            for reaction in message.reactions.results:
                if hasattr(reaction.reaction, 'emoticon'):
                    reaction_type = str(reaction.reaction.emoticon)
                elif hasattr(reaction.reaction, 'document_id'):
                    reaction_type = ID_TO_CUSTOM_EMOJI_MAP.get(reaction.reaction.document_id, 'хз')
                else:
                    reaction_type = "✡"
                reaction_count = reaction.count
                reactions_dict[reaction_type] = reaction_count
    return reactions_dict

async def get_message_text_and_type(message):
    """Get message text/caption and determine if it's text or caption."""
    message_text = ""
    is_text = False
    
    if hasattr(message, 'text') and message.text:
        message_text = message.text
        is_text = True
    elif hasattr(message, 'caption') and message.caption:
        message_text = message.caption
        is_text = False
    
    # Remove existing reaction section if present
    if "---" in message_text:
        message_text = message_text.split("---")[0].strip()
        
    return message_text, is_text

async def build_reactions_summary(reactions_dict):
    """Build a formatted string of reactions for display."""
    if not reactions_dict:
        return ""
        
    reactions_text = "---\n"
    for emoji, count in reactions_dict.items():
        reactions_text += f"{emoji} {count} "
    return reactions_text

async def combine_reactions(source_reactions, target_reactions):
    """Combine reactions from source and target channels."""
    combined_reactions = {}
    all_emojis = set(list(source_reactions.keys()) + list(target_reactions.keys()))
    
    for emoji in all_emojis:
        combined_reactions[emoji] = (source_reactions.get(emoji, 0) + target_reactions.get(emoji, 0))
    
    return combined_reactions

async def update_message_with_reactions(bot: Bot, chat_id, message, message_text, reactions_summary, is_text):
    """Update a message with the given text and reactions."""
    try:
        new_text = f"{message_text}\n{reactions_summary}" if reactions_summary else message_text
        if message.media is None:
            await bot.edit_message_text(
                text=new_text,
                chat_id=chat_id,
                message_id=message.id,
            )
        else:
            await bot.edit_message_caption(
                caption=new_text,
                chat_id=chat_id,
                message_id=message.id
            )
        return True
    except Exception as e:
        logger.error(f"Error updating message {message.id} in chat {chat_id}: {e}")
        return False

async def process_reaction_change(bot, channel_id, message, reactions_dict):
    """Process a change in message reactions."""
    message_id = message.id
    try:
        logger.info(f"Processing reaction change for message {message_id} in channel {channel_id}")
        
        pred_reaction = get_stored_reactions(channel_id, message_id)
        for k, v in pred_reaction.items():
            reactions_dict[k] = max(reactions_dict.get(k, 0), v)
        # Store the updated reactions
        store_reactions(channel_id, message_id, reactions_dict)
        
        # Find corresponding message in the other channel
        target_channel = channel2_telethon if channel_id == channel1_telethon else channel1_telethon
        target_channel_ptb = channel2 if channel_id == channel1_telethon else channel1
        source_channel_ptb = channel1 if channel_id == channel1_telethon else channel2
        
        # Use the bidirectional lookup function to find the corresponding message
        copied_message_id = get_corresponding_message_id(
            to_ptb_channel(channel_id), 
            message_id, 
            to_ptb_channel(target_channel)
        )
        
        if not copied_message_id:
            logger.info(f"No corresponding message found for {message_id} in target channel")
            return
            
        logger.info(f"Corresponding message {copied_message_id} found in channel {target_channel}")
        
        # Get reactions for the copied message in the target channel
        target_reactions_dict = get_stored_reactions(target_channel, copied_message_id) or {}
        
        # Combine reactions from both channels
        combined_reactions = await combine_reactions(reactions_dict, target_reactions_dict)
        
        # Create the reactions summary text
        reactions_text = await build_reactions_summary(combined_reactions)
        
        # Retrieve the source and target messages
        source_message = await telethon_client.get_messages(PeerChannel(channel_id), ids=[message_id])
        source_message = source_message[0] if source_message else None
        
        target_message = await telethon_client.get_messages(PeerChannel(target_channel), ids=[copied_message_id])
        target_message = target_message[0] if target_message else None
        
        # Update both messages with the combined reactions
        if source_message:
            source_text, source_is_text = await get_message_text_and_type(source_message)
            if source_text:
                success = await update_message_with_reactions(
                    bot, 
                    source_channel_ptb,
                    source_message,
                    source_text,
                    reactions_text,
                    source_is_text
                )
                if success:
                    logger.info(f"Updated source message {message_id} with reactions")
        
        if target_message:
            target_text, target_is_text = await get_message_text_and_type(target_message)
            if target_text:
                success = await update_message_with_reactions(
                    bot,
                    target_channel_ptb,
                    target_message,
                    target_text,
                    reactions_text,
                    target_is_text
                )
                if success:
                    logger.info(f"Updated target message {copied_message_id} with reactions")
                    
    except Exception as e:
        stack_trace = traceback.format_exc()
        logger.error(f"Error processing reaction change: {e}\n{stack_trace}")

# Message handling functions
async def forward_media(bot: Bot, message: Message, target_channel: int, reply_to_message_id: int = None):
    """Forward or copy a message to the target channel."""
    try:
        # Check if the message is part of a media group
        if message.media_group_id:
            # We'll handle media groups separately in the channel_post_handler
            # Just return the message for now to maintain the function signature
            logger.info(f"Message {message.message_id} is part of media group {message.media_group_id}, will handle separately")
            return message
        
        # Regular forwarding for non-media group messages
        # Check if message is a forward or poll - these need to be forwarded, not copied
        is_forward = hasattr(message, 'forward_origin') and message.forward_origin
                     
        if (hasattr(message, 'poll') and message.poll) or is_forward:
            logger.info(f"Forwarding message {message.message_id} instead of copying")
            copied_message = await bot.forward_message(
                chat_id=target_channel,
                from_chat_id=message.chat_id,
                message_id=message.message_id
            )
        else:
            copied_message = await bot.copy_message(
                chat_id=target_channel,
                from_chat_id=message.chat_id,
                message_id=message.message_id,
                reply_to_message_id=reply_to_message_id
            )
    except Exception as e:
        logger.error(f"Failed to forward message {message.message_id} to channel {target_channel}: {e}")
        # Fallback to direct forwarding
        copied_message = await bot.forward_message(
            chat_id=target_channel,
            from_chat_id=message.chat_id,
            message_id=message.message_id
        )
    
    return copied_message

async def forward_single_media(bot: Bot, message: Message, target_channel: int, reply_to_message_id: int = None):
    """Forward a single media message, used as fallback for media groups."""
    try:
        logger.info(f"Forwarding single media message {message.message_id}")
        return await bot.forward_message(
            chat_id=target_channel,
            from_chat_id=message.chat_id,
            message_id=message.message_id
        )
    except Exception as e:
        logger.error(f"Failed to forward single media message: {e}")
        return None

# Handler for new channel posts
async def channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.channel_post
    if not message:
        return

    logger.info(f"Copy message: {message.text if message.text else '(Media message)'}")
    source_channel = message.chat_id
    logger.info(f"chat_id={source_channel}")
    logger.info(f"message_id={message.message_id}")
    logger.info(f"reply_to_message_id={message.reply_to_message.message_id if message.reply_to_message else None}")
    logger.info(f"media_group_id={message.media_group_id}")
    
    target_channel = channel2 if source_channel == channel1 else channel1

    # Handle media groups - collect all messages and then process them as a group
    if message.media_group_id:
        # Set up the global media group tracking dictionary in application
        if not hasattr(context.application, 'media_groups_data'):
            context.application.media_groups_data = {}
            
        media_group_id = message.media_group_id
        logger.info(f"Message is part of media group {media_group_id}")
        
        # Check if we already have this media group in our tracking
        if media_group_id not in context.application.media_groups_data:
            # Initialize new media group entry
            context.application.media_groups_data[media_group_id] = {
                'messages': [],
                'source_channel': source_channel,
                'target_channel': target_channel,
                'last_update': time.time(),
                'processed': False,
                'task': None,
                'fallback_sent': False  # Track if we've sent individual messages as fallback
            }
            
            # Create an async task to process this media group after a delay
            async def delayed_process():
                try:
                    await asyncio.sleep(2.0)  # 2 seconds should be enough to collect all media
                    
                    # Check if we still have the media group data
                    if hasattr(context.application, 'media_groups_data') and media_group_id in context.application.media_groups_data:
                        # Process the media group
                        await process_media_group(context, media_group_id)
                    else:
                        logger.warning(f"Media group {media_group_id} data was lost before processing")
                except Exception as e:
                    stack_trace = traceback.format_exc()
                    logger.error(f"Error in delayed processing of media group {media_group_id}: {e}\n{stack_trace}")
                    
                    # If there was an error processing as a group, send individually as fallback
                    await fallback_process_media_group(context, media_group_id)
            
            # Schedule task to process media group after delay
            task = asyncio.create_task(delayed_process())
            context.application.media_groups_data[media_group_id]['task'] = task
            logger.info(f"Scheduled processing of media group {media_group_id} in 2 seconds")
            
            # Set up fallback timer to ensure messages get sent even if the task fails
            async def fallback_timer():
                try:
                    await asyncio.sleep(5.0)  # Wait 5 seconds
                    # If the media group hasn't been processed by now, process it individually
                    if (hasattr(context.application, 'media_groups_data') and 
                        media_group_id in context.application.media_groups_data and 
                        not context.application.media_groups_data[media_group_id].get('processed', False)):
                        logger.warning(f"Fallback timer triggered for media group {media_group_id}")
                        await fallback_process_media_group(context, media_group_id)
                except Exception as e:
                    logger.error(f"Error in fallback timer: {e}")
            
            # Start the fallback timer
            asyncio.create_task(fallback_timer())
        
        # Add message to the media group
        context.application.media_groups_data[media_group_id]['messages'].append(message)
        context.application.media_groups_data[media_group_id]['last_update'] = time.time()
        logger.info(f"Added message to media group {media_group_id}, now has {len(context.application.media_groups_data[media_group_id]['messages'])} messages")
        
        # Don't process immediately - let the scheduled task do it after collecting all messages
        return
            
    # Regular message handling (non-media group)
    if message.reply_to_message:
        # Get the original message ID of the replied message
        copied_reply_id = message.reply_to_message.message_id
        # Check if the original reply ID exists in the database for the source channel
        original_reply_id = get_original_id(target_channel, copied_reply_id, source_channel)
        copied_original_reply_id = get_copied_id(source_channel, copied_reply_id, target_channel)
        
        if original_reply_id is not None:
            # Send the message as a reply to the copied message in the target channel
            copied_message = await forward_media(context.bot, message, target_channel, reply_to_message_id=original_reply_id)
        else:
            # If the original reply ID doesn't exist in the database, try the other direction
            copied_message = await forward_media(context.bot, message, target_channel, reply_to_message_id=copied_original_reply_id)
    else:
        # If the message is not a reply, send it as a new message
        copied_message = await forward_media(context.bot, message, target_channel)

    # Handle media group special case
    if hasattr(copied_message, 'media_group_id') and copied_message.media_group_id:
        # We've already handled this in the media group section
        return
        
    logger.info(f"copied_message_id={copied_message.message_id}")

    # Store the mapping of original message ID to copied message ID in the database
    store_mapping(source_channel, message.message_id, target_channel, copied_message.message_id)

# Helper function to process media groups
async def process_media_group(context, media_group_id):
    """Process a complete media group and send it to the target channel."""
    if not hasattr(context.application, 'media_groups_data') or media_group_id not in context.application.media_groups_data:
        logger.error(f"Media group {media_group_id} not found in tracking")
        return
    
    group_data = context.application.media_groups_data[media_group_id]
    
    # Check if this group has already been processed
    if group_data.get('processed', False):
        logger.info(f"Media group {media_group_id} already processed, skipping")
        return
    
    # Mark as processed to prevent double processing
    context.application.media_groups_data[media_group_id]['processed'] = True
    
    messages = group_data['messages']
    source_channel = group_data['source_channel']
    target_channel = group_data['target_channel']
    
    if not messages:
        logger.error(f"No messages found in media group {media_group_id}")
        return
    
    logger.info(f"Processing media group {media_group_id} with {len(messages)} messages as a single group")
    
    try:
        # Sort messages by message_id to ensure correct order
        messages.sort(key=lambda msg: msg.message_id)
        
        # Prepare media for sending
        media = []
        reply_to_message_id = None
        
        # Check if any message is a reply
        first_message = messages[0]
        if first_message.reply_to_message:
            copied_reply_id = first_message.reply_to_message.message_id
            original_reply_id = get_original_id(target_channel, copied_reply_id, source_channel)
            copied_original_reply_id = get_copied_id(source_channel, copied_reply_id, target_channel)
            
            if original_reply_id is not None:
                reply_to_message_id = original_reply_id
            else:
                reply_to_message_id = copied_original_reply_id
        
        # Create InputMedia objects
        from telegram import InputMediaPhoto, InputMediaVideo, InputMediaAudio, InputMediaDocument
        
        for msg in messages:
            if msg.photo:
                media.append(InputMediaPhoto(
                    media=msg.photo[-1].file_id,
                    caption=msg.caption,
                    parse_mode=ParseMode.HTML if hasattr(msg, 'caption_html') and msg.caption_html else None
                ))
            elif msg.video:
                media.append(InputMediaVideo(
                    media=msg.video.file_id,
                    caption=msg.caption,
                    parse_mode=ParseMode.HTML if hasattr(msg, 'caption_html') and msg.caption_html else None
                ))
            elif msg.audio:
                media.append(InputMediaAudio(
                    media=msg.audio.file_id,
                    caption=msg.caption,
                    parse_mode=ParseMode.HTML if hasattr(msg, 'caption_html') and msg.caption_html else None
                ))
            elif msg.document:
                media.append(InputMediaDocument(
                    media=msg.document.file_id,
                    caption=msg.caption,
                    parse_mode=ParseMode.HTML if hasattr(msg, 'caption_html') and msg.caption_html else None
                ))
        
        if not media:
            logger.error(f"No valid media found in media group {media_group_id}")
            return
        
        # Send the media group
        logger.info(f"Sending media group with {len(media)} items")
        sent_messages = await context.bot.send_media_group(
            chat_id=target_channel,
            media=media,
            reply_to_message_id=reply_to_message_id
        )
        
        # Store mappings
        for i, sent_msg in enumerate(sent_messages):
            if i < len(messages):
                original_msg = messages[i]
                store_mapping(source_channel, original_msg.message_id, target_channel, sent_msg.message_id)
                logger.info(f"Stored mapping: {source_channel}:{original_msg.message_id} -> {target_channel}:{sent_msg.message_id}")
        
        # Clean up
        del context.application.media_groups_data[media_group_id]
        logger.info(f"Media group {media_group_id} processed successfully as a single group")
        
    except Exception as e:
        stack_trace = traceback.format_exc()
        logger.error(f"Error processing media group {media_group_id}: {e}\n{stack_trace}")
        
        # Fallback to individual forwarding
        logger.info(f"Falling back to individual forwarding for media group {media_group_id}")
        for msg in messages:
            try:
                copied_msg = await context.bot.forward_message(
                    chat_id=target_channel,
                    from_chat_id=msg.chat_id,
                    message_id=msg.message_id
                )
                store_mapping(source_channel, msg.message_id, target_channel, copied_msg.message_id)
                logger.info(f"Forwarded individual media: {msg.message_id} -> {copied_msg.message_id}")
            except Exception as forward_error:
                logger.error(f"Error forwarding individual media: {forward_error}")
        
        # Clean up
        del context.application.media_groups_data[media_group_id]

# Fallback function to process media groups individually
async def fallback_process_media_group(context, media_group_id):
    """Process a media group by forwarding messages individually."""
    if not hasattr(context.application, 'media_groups_data') or media_group_id not in context.application.media_groups_data:
        logger.error(f"Media group {media_group_id} not found in tracking for fallback processing")
        return
    
    group_data = context.application.media_groups_data[media_group_id]
    
    # If already processed or fallback already sent, don't do it again
    if group_data.get('processed', False) or group_data.get('fallback_sent', False):
        return
    
    # Mark as fallback sent
    context.application.media_groups_data[media_group_id]['fallback_sent'] = True
    
    messages = group_data['messages']
    source_channel = group_data['source_channel']
    target_channel = group_data['target_channel']
    
    if not messages:
        logger.error(f"No messages found in media group {media_group_id} for fallback processing")
        return
    
    logger.info(f"FALLBACK: Processing media group {media_group_id} with {len(messages)} messages individually")
    
    try:
        # Sort messages by message_id to ensure correct order
        messages.sort(key=lambda msg: msg.message_id)
        
        # Forward each message individually
        for msg in messages:
            try:
                copied_msg = await forward_single_media(context.bot, msg, target_channel)
                if copied_msg:
                    store_mapping(source_channel, msg.message_id, target_channel, copied_msg.message_id)
                    logger.info(f"FALLBACK: Forwarded media message: {msg.message_id} -> {copied_msg.message_id}")
            except Exception as e:
                logger.error(f"FALLBACK: Error forwarding individual media: {e}")
        
        # Clean up
        del context.application.media_groups_data[media_group_id]
        logger.info(f"FALLBACK: Media group {media_group_id} processed individually")
        
    except Exception as e:
        stack_trace = traceback.format_exc()
        logger.error(f"FALLBACK: Error in fallback processing for media group {media_group_id}: {e}\n{stack_trace}")
        
        # Try one last approach - just direct forward each message
        try:
            for msg in messages:
                copied_msg = await context.bot.forward_message(
                    chat_id=target_channel,
                    from_chat_id=msg.chat_id,
                    message_id=msg.message_id
                )
                store_mapping(source_channel, msg.message_id, target_channel, copied_msg.message_id)
                logger.info(f"FALLBACK EMERGENCY: Forwarded media message: {msg.message_id} -> {copied_msg.message_id}")
        except Exception as final_e:
            logger.error(f"FALLBACK EMERGENCY: Final error: {final_e}")
        
        # Clean up
        if hasattr(context.application, 'media_groups_data') and media_group_id in context.application.media_groups_data:
            del context.application.media_groups_data[media_group_id]

# Handler for edited channel posts
async def edited_channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.edited_channel_post
    if not message:
        return
        
    logger.info(f"Edited message: {message.text}")
    source_channel = message.chat_id
    target_channel = channel2 if source_channel == channel1 else channel1

    # Get the copied message ID from the database
    copied_message_id = get_copied_id(source_channel, message.message_id, target_channel)
    logger.info(f"copied_message_id={copied_message_id}")
    
    if copied_message_id:
        try:
            if message.text:
                await context.bot.edit_message_text(
                    text=message.text,
                    chat_id=target_channel,
                    message_id=copied_message_id
                )
            elif message.caption:
                await context.bot.edit_message_caption(
                    caption=message.caption,
                    chat_id=target_channel,
                    message_id=copied_message_id
                )
        except Exception as e:
            logger.error(f"Error editing message: {e}")

# Function to periodically check for reactions
async def check_reactions(app: Application):
    """Periodically check for reactions on messages in monitored channels."""
    bot = app.bot
    
    # Wait for telethon client to be ready
    while not telethon_client.is_connected():
        logger.info("Waiting for Telethon client to connect...")
        await asyncio.sleep(1)
    
    logger.info("Telethon client is connected, starting reaction checker")
    
    while True:
        try:
            for channel_id in [channel1_telethon, channel2_telethon]:
                # Get recent messages from the channel
                async for message in telethon_client.iter_messages(PeerChannel(channel_id), limit=MESSAGE_CHECK_FOR_REACTIONS_LIMIT):
                    if message.reactions:
                        # Extract reactions from the message
                        reactions_dict = await extract_message_reactions(message)
                        
                        # Check if reactions have changed
                        if reactions_changed(channel_id, message.id, reactions_dict):
                            logger.info(f"Reactions changed for message {message.id} in channel {channel_id}")
                            logger.info(f"New reactions: {reactions_dict}")
                            
                            # Process the reaction change
                            await process_reaction_change(bot, channel_id, message, reactions_dict)
        except Exception as e:
            stack_trace = traceback.format_exc()
            logger.error(f"Error in check_reactions: {e}\n{stack_trace}")
        
        # Wait before checking again
        await asyncio.sleep(30)  # Check every 30 seconds

async def run_telethon():
    """Run the Telethon client."""
    await telethon_client.start(phone=PHONE_NUMBER)
    logger.info("Telethon client started")
    await telethon_client.run_until_disconnected()

async def main():
    """Set up and run the bot."""
    # Create the Application
    application = Application.builder().token(API_TOKEN).build()
    
    # Add handlers for channel posts
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.UpdateType.CHANNEL_POST, channel_post_handler))
    
    # Add handler for edited channel posts
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.UpdateType.EDITED_CHANNEL_POST, edited_channel_post_handler))
    
    # Start the bot
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    # Start the telethon client
    telethon_task = asyncio.create_task(run_telethon())
    
    # Start the reaction checker as a background task
    reactions_task = asyncio.create_task(check_reactions(application))
    
    logger.info("Bot started")
    
    # Keep the bot running until interrupted
    stop_signal = asyncio.Future()
    
    try:
        # Wait for a signal to stop
        await stop_signal
    except asyncio.CancelledError:
        # Handle cancellation
        logger.info("Application task was cancelled")
    finally:
        # Cancel background tasks
        telethon_task.cancel()
        reactions_task.cancel()
        
        # Stop and shutdown the app
        logger.info("Stopping updater...")
        await application.updater.stop()
        
        logger.info("Shutting down application...")
        await application.stop()
        await application.shutdown()
        
        # Close database
        conn.close()
        
        logger.info("Application shut down successfully")

if __name__ == '__main__':
    logger.info("Script is running. Press Ctrl+C to stop.")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Script stopped by user.")
    finally:
        # Close the database connection when the script ends
        if 'conn' in locals():
            conn.close() 