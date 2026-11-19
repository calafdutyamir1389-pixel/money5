"""Run this ONCE on your own computer to get SESSION_STRING."""
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("API_ID: ").strip())
api_hash = input("API_HASH: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\nSESSION_STRING (keep it secret!):\n")
    print(client.session.save())
