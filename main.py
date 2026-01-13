import os
import json
import time
import random
import requests
import feedparser
from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from openai import OpenAI
import cloudinary
import cloudinary.uploader

# =========================
# إعدادات عامة
# =========================
RSS_URL = os.getenv("RSS_URL")
BLOGGER_LABELS = [l.strip() for l in os.getenv("BLOGGER_LABELS", "").split(",") if l.strip()]
BOT_NAME = os.getenv("BOT_NAME", "Unknown Bot")
SCRAPE_MODE = os.getenv("SCRAPE_MODE", "default")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BLOG_ID = "8964557641790201632"
SCOPES = ["https://www.googleapis.com/auth/blogger"]
MAX_POSTS_PER_RUN = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.google.com/"
}

openai_client = OpenAI(api_key=OPENAI_API_KEY)

cloudinary.config(
    cloud_name="dldxptjuf",
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

# =========================
# وظائف المساعدة
# =========================

def send_telegram(status, message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id: return
    icons = {"success": "✅", "error": "🚨", "info": "ℹ️"}
    text = f"{icons.get(status, 'ℹ️')} <b>{BOT_NAME}</b>\n\n{message}"
    try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
    except: pass

def get_blogger_service():
    creds_json = os.environ.get("BLOGGER_CREDS_JSON")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_authorized_user_info(creds_dict, SCOPES)
    if creds.expired and creds.refresh_token: creds.refresh(Request())
    return build("blogger", "v3", credentials=creds)

def extract_article(link, mode):
    try:
        r = requests.get(link, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        
        # تحسين البحث عن المحتوى ليكون أكثر شمولاً
        container = None
        if mode == "default":
            container = soup.find("div", class_="entry") or soup.find("div", class_="content") or soup.find("article")
        else:
            container = soup.find(class_="paragraph-list") or soup.find("div", id="article-body") or soup.find("section", class_="article-text")

        if not container: return None, None

        for tag in container.find_all(["script", "style", "iframe", "aside", "ins"]): tag.decompose()
        
        text = container.get_text(" ", strip=True)
        
        # البحث عن الصورة بطرق متعددة
        img_url = None
        img_tag = soup.find("meta", property="og:image")
        if img_tag: img_url = img_tag.get("content")
        if not img_url:
            img = container.find("img")
            if img: img_url = img.get("src") or img.get("data-src")

        return text, img_url
    except Exception as e:
        print(f"Error extracting {link}: {e}")
        return None, None

def paraphrase_article(text):
    try:
        r = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"أعد صياغة هذا الخبر بأسلوب صحفي جذاب وحافظ على كافة التفاصيل:\n\n{text}"}],
            temperature=0.3
        )
        article = r.choices[0].message.content.strip()
        t = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"أعطني عنواناً صحفياً قوياً لهذا الخبر:\n\n{article}"}],
            temperature=0.3
        )
        return t.choices[0].message.content.strip(), article
    except: return None, None

# =========================
# التشغيل الرئيسي
# =========================

def main():
    print(f"🚀 Starting {BOT_NAME}...")
    try:
        service = get_blogger_service()
        feed = feedparser.parse(RSS_URL)
        published_count = 0

        for entry in feed.entries:
            if published_count >= MAX_POSTS_PER_RUN: break
            
            print(f"🧐 Checking: {entry.title}")
            
            # فحص التكرار
            posts = service.posts().list(blogId=BLOG_ID, maxResults=10).execute().get("items", [])
            if any(p["title"].strip() == entry.title.strip() for p in posts):
                print("⏭️ Already published, skipping.")
                continue

            text, img = extract_article(entry.link, SCRAPE_MODE)
            if not text or len(text) < 150:
                print("⚠️ No enough content found.")
                continue

            new_title, new_content = paraphrase_article(text)
            if not new_title: continue

            # رفع الصورة لـ Cloudinary
            final_img = img
            if img:
                try: 
                    up = cloudinary.uploader.upload(img, folder="news")
                    final_img = up["secure_url"]
                except: pass

            # تنسيق HTML للنشر
            html = f"<div dir='rtl' style='text-align:justify; font-size:18px; line-height:1.6;'>"
            if final_img: html += f"<div style='text-align:center'><img src='{final_img}' style='max-width:100%; border-radius:10px;'></div><br>"
            html += f"{new_content.replace(chr(10), '<br>')}</div>"

            # النشر
            service.posts().insert(
                blogId=BLOG_ID,
                body={"title": new_title, "content": html, "labels": BLOGGER_LABELS, "isDraft": False}
            ).execute()
            
            published_count += 1
            print(f"✅ Published: {new_title}")
            send_telegram("success", f"تم نشر خبر جديد:\n{new_title}")
            time.sleep(10)

        if published_count == 0:
            print("🏁 Run finished. No new articles to publish.")

    except Exception as e:
        print(f"🚨 Critical Error: {e}")
        send_telegram("error", f"خطأ في التشغيل: {e}")

if __name__ == "__main__":
    main()
