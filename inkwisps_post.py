# File: INKWISPS_post.py
import os
import time
import json
import logging
import requests
import dropbox
from telegram import Bot
from datetime import datetime, timedelta
from pytz import timezone, utc

class DropboxToInstagramUploader:
    DROPBOX_TOKEN_URL = "https://api.dropbox.com/oauth2/token"
    INSTAGRAM_API_BASE = "https://graph.facebook.com/v18.0"
    INSTAGRAM_REEL_STATUS_RETRIES = 20
    INSTAGRAM_REEL_STATUS_WAIT_TIME = 5

    def __init__(self):
        self.script_name = "inkwisps_post.py"
        self.ist = timezone('Asia/Kolkata')
        self.account_key = "inkwisps"
        self.schedule_file = "scheduler/config.json"

        # Logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler()]
        )
        self.logger = logging.getLogger()

        # Secrets from GitHub environment
        self.instagram_access_token = os.getenv("IG_INKWISPS_TOKEN")
        self.instagram_account_id = os.getenv("IG_INKWISPS_ID")
        self.dropbox_app_key = os.getenv("DROPBOX_INKWISPS_APP_KEY")
        self.dropbox_app_secret = os.getenv("DROPBOX_INKWISPS_APP_SECRET")
        self.dropbox_refresh_token = os.getenv("DROPBOX_INKWISPS_REFRESH")
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

        self.dropbox_folder = "/inkwisps"
        self.telegram_bot = Bot(token=self.telegram_bot_token)

        self.start_time = time.time()

    def send_message(self, msg, level=logging.INFO):
        prefix = f"[{self.script_name}]\n"
        full_msg = prefix + msg
        try:
            self.telegram_bot.send_message(chat_id=self.telegram_chat_id, text=full_msg)
            # Also log the message to console with the specified level
            if level == logging.ERROR:
                self.logger.error(full_msg)
            else:
                self.logger.info(full_msg)
        except Exception as e:
            self.logger.error(f"Telegram send error for message '{full_msg}': {e}")

    def refresh_dropbox_token(self):
        self.logger.info("Refreshing Dropbox token...")
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.dropbox_refresh_token,
            "client_id": self.dropbox_app_key,
            "client_secret": self.dropbox_app_secret,
        }
        r = requests.post(self.DROPBOX_TOKEN_URL, data=data)
        if r.status_code == 200:
            new_token = r.json().get("access_token")
            self.logger.info("Dropbox token refreshed.")
            return new_token
        else:
            self.send_message("❌ Dropbox refresh failed: " + r.text)
            raise Exception("Dropbox refresh failed.")

    def list_dropbox_files(self, dbx):
        try:
            files = dbx.files_list_folder(self.dropbox_folder).entries
            valid_exts = ('.mp4', '.mov', '.jpg', '.jpeg', '.png')
            return [f for f in files if f.name.lower().endswith(valid_exts)]
        except Exception as e:
            self.send_message(f"❌ Dropbox folder read failed: {e}", level=logging.ERROR)
            return []

    def get_caption_from_config(self):
        try:
            with open(self.schedule_file, 'r') as f:
                config = json.load(f)
            
            # Get today's caption from config
            today = datetime.now(self.ist).strftime("%A")
            day_config = config.get(self.account_key, {}).get(today, {})
            caption = day_config.get("caption", "")
            
            if not caption:
                self.send_message("⚠️ No caption found in config for today", level=logging.WARNING)
                return "✨ #ink_wisps ✨"  # Default caption if none found
            
            return caption
        except Exception as e:
            self.send_message(f"❌ Failed to read caption from config: {e}", level=logging.ERROR)
            return "✨ #ink_wisps ✨"  # Default caption if config read fails

    def post_to_instagram(self, dbx, file, caption):
        name = file.name
        ext = name.lower()
        media_type = "REELS" if ext.endswith((".mp4", ".mov")) else "IMAGE"

        temp_link = dbx.files_get_temporary_link(file.path_lower).link
        file_size = f"{file.size / 1024 / 1024:.2f}MB"
        total_files = len(self.list_dropbox_files(dbx))

        self.send_message(f"🚀 Uploading: {name}\n📂 Type: {media_type}\n📐 Size: {file_size}\n📦 Remaining: {total_files}")

        upload_url = f"{self.INSTAGRAM_API_BASE}/{self.instagram_account_id}/media"
        data = {
            "access_token": self.instagram_access_token,
            "caption": caption
        }

        if media_type == "REELS":
            data.update({"media_type": "REELS", "video_url": temp_link, "share_to_feed": "true"})
        else:
            data["image_url"] = temp_link

        res = requests.post(upload_url, data=data)
        if res.status_code != 200:
            err = res.json().get("error", {}).get("message", "Unknown")
            code = res.json().get("error", {}).get("code", "N/A")
            self.send_message(f"❌ Failed: {name}\n🧾 Error: {err}\n🪪 Code: {code}", level=logging.ERROR)
            return False

        creation_id = res.json().get("id")
        if not creation_id:
            self.send_message(f"❌ No media ID returned for: {name}", level=logging.ERROR)
            return False, media_type

        if media_type == "REELS":
            for _ in range(self.INSTAGRAM_REEL_STATUS_RETRIES):
                status = requests.get(
                    f"{self.INSTAGRAM_API_BASE}/{creation_id}?fields=status_code&access_token={self.instagram_access_token}"
                ).json()
                if status.get("status_code") == "FINISHED":
                    break
                elif status.get("status_code") == "ERROR":
                    self.send_message(f"❌ IG processing failed: {name}", level=logging.ERROR)
                    return False
                time.sleep(self.INSTAGRAM_REEL_STATUS_WAIT_TIME)

        publish_url = f"{self.INSTAGRAM_API_BASE}/{self.instagram_account_id}/media_publish"
        pub = requests.post(publish_url, data={"creation_id": creation_id, "access_token": self.instagram_access_token})
        if pub.status_code == 200:
            self.send_message(f"✅ Uploaded: {name}\n📦 Files left: {total_files - 1}")
            # Removed file deletion from here
            return True, media_type
        else:
            self.send_message(f"❌ Publish failed: {name}\n{pub.text}", level=logging.ERROR)
            return False, media_type

    def authenticate_dropbox(self):
        """Authenticate with Dropbox and return the client."""
        try:
            access_token = self.refresh_dropbox_token()
            return dropbox.Dropbox(oauth2_access_token=access_token)
        except Exception as e:
            self.send_message(f"❌ Dropbox authentication failed: {str(e)}", level=logging.ERROR)
            raise

    def process_files_with_retries(self, dbx, caption, max_retries=3):
        files = self.list_dropbox_files(dbx)
        if not files:
            self.send_message("📭 No files found in Dropbox folder.", level=logging.INFO)
            return False

        attempts = 0
        for file in files[:max_retries]:
            attempts += 1
            self.send_message(f"🎯 Attempt {attempts}/{max_retries} — Trying: {file.name}", level=logging.INFO)

            try:
                success, media_type = self.post_to_instagram(dbx, file, caption)
            except Exception as e:
                self.send_message(f"❌ Exception during post for {file.name}: {e}", level=logging.ERROR)
                success = False
                media_type = None

            # Always delete the file after an attempt
            try:
                dbx.files_delete_v2(file.path_lower)
                self.send_message(f"🗑️ Deleted file after attempt: {file.name}")
            except Exception as e:
                self.send_message(f"⚠️ Failed to delete file {file.name}: {e}", level=logging.WARNING)

            if success:
                if media_type == "REELS":
                    self.send_message("✅ Successfully posted one reel", level=logging.INFO)
                elif media_type == "IMAGE":
                    self.send_message("✅ Successfully posted one image", level=logging.INFO)
                else:
                    self.send_message("✅ Successfully posted", level=logging.INFO)
                return True  # Exit after successful post

        self.send_message("❌ All attempts failed. Exiting after 3 tries.", level=logging.ERROR)
        return False

    def run(self):
        """Main execution method that orchestrates the posting process."""
        self.send_message(f"📡 Run started at: {datetime.now(self.ist).strftime('%Y-%m-%d %H:%M:%S')}", level=logging.INFO)
        
        try:
            # Get caption from config
            caption = self.get_caption_from_config()
            
            # Authenticate with Dropbox
            dbx = self.authenticate_dropbox()
            
            # Try posting up to 3 times
            self.process_files_with_retries(dbx, caption, max_retries=3)
            
        except Exception as e:
            self.send_message(f"❌ Script crashed:\n{str(e)}", level=logging.ERROR)
            raise
        finally:
            duration = time.time() - self.start_time
            self.send_message(f"🏁 Run complete in {duration:.1f} seconds", level=logging.INFO)

if __name__ == "__main__":
    DropboxToInstagramUploader().run()
