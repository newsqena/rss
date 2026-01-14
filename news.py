import os
import json
import time
import random
import requests
import feedparser
import re
from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import cloudinary
import cloudinary.uploader

# =========================
# الإعدادات العامة
# =========================

RSS_URL = os.getenv("RSS_URL")
BLOGGER_LABELS = [l.strip() for l in os.getenv("BLOGGER_LABELS", "").split(",") if l.strip()]
BOT_NAME = os.getenv("BOT_NAME", "Unknown Bot")

BLOG_ID = "8964557641790201632"
SCOPES = ["https://www.googleapis.com/auth/blogger"]

MAX_POSTS_PER_RUN = 3
HISTORY_FILE = "published_urls.txt"

MIN_SLEEP = 40
MAX_SLEEP = 75

# =========================
# Session (مهم جداً)
# =========================

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar,en;q=0.8",
})

# =========================
# Cloudinary
# =========================

cloudinary.config(
    cloud_name="dldxptjuf",
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

# =========================
# Telegram
# =========================

def send_telegram(status, message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return

    icons = {
        "success": "✅",
        "error": "🚨",
        "info": "ℹ️",
        "skip": "⏭️"
    }

    text = f"{icons.get(status, 'ℹ️')} <b>{BOT_NAME}</b>\n\n{message}"

    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
    except:
        pass

# =========================
# History
# =========================

def load_history():
    if not os.path.exists(HISTORY_FILE):
        return set()
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_history(link):
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(link + "\n")

# =========================
# تنظيف النص
# =========================

def clean_text(text):
    text = re.sub(r'(اسلام نبيل|بتوقيت النجع|إسلام نبيل)', '', text)
    text = re.sub(r'[*#\"“”]', '', text)
    return text.strip()

# =========================
# RSS (الحل النهائي)
# =========================

def load_rss():
    try:
        r = session.get(RSS_URL, timeout=30)
        r.raise_for_status()

        feed = feedparser.parse(r.content)

        if not feed.entries:
            print("❌ RSS fetched but no entries found")
        else:
            print(f"✅ RSS OK: {len(feed.entries)} items")

        return feed.entries

    except Exception as e:
        print("🚨 RSS load failed:", e)
        return []


# =========================
# استخراج المقال
# =========================

def extract_article(url):
    r = session.get(url, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    text_box = soup.find("div", class_="paragraph-list")
    if not text_box:
        return None, None

    for tag in text_box.find_all(["script", "style", "iframe", "aside"]):
        tag.decompose()

    text = text_box.get_text(" ", strip=True)

    img_url = None
    img_box = soup.find("div", class_="main-img")
    if img_box:
        img = img_box.find("img")
        if img:
            img_url = img.get("src")

    return text, img_url

# =========================
# OpenAI
# =========================

def paraphrase(title, text):
    api_key = os.getenv("OPENAI_API_KEY")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    prompt = f"""
أعد صياغة الخبر التالي بأسلوب صحفي عربي واضح دون تغيير المعنى.

العنوان:
{title}

المحتوى:
{text}

الشروط:
- عنوان جديد فقط في أول سطر
- الخبر 3 فقرات
- بدون أسماء أشخاص أو مواقع
"""

    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3
    }

    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=60
    )

    result = r.json()["choices"][0]["message"]["content"].strip()
    lines = result.split("\n")

    return clean_text(lines[0]), clean_text("\n".join(lines[1:]))

# =========================
# Cloudinary
# =========================

def upload_image(url):
    if not url:
        return None
    try:
        img = session.get(url, timeout=20)
        res = cloudinary.uploader.upload(img.content, folder="blogger_news")
        return res["secure_url"]
    except:
        return None

# =========================
# Blogger
# =========================

def blogger_service():
    creds = json.loads(os.getenv("BLOGGER_CREDS_JSON"))
    credentials = Credentials.from_authorized_user_info(creds, SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    return build("blogger", "v3", credentials=credentials)

# =========================
# MAIN
# =========================

def main():
    print(f"🚀 Starting {BOT_NAME}")

    entries = load_rss()
    print("📡 RSS entries:", len(entries))

    if not entries:
        send_telegram("error", "❌ لا توجد أخبار في RSS")
        return

    history = load_history()
    service = blogger_service()

    published = 0

    for entry in entries:
        if published >= MAX_POSTS_PER_RUN:
            break

        if entry.link in history:
            continue

        print("🧐 Checking:", entry.title)

        try:
            text, img = extract_article(entry.link)
            if not text or len(text) < 150:
                send_telegram("skip", f"❌ تخطي خبر ضعيف:\n{entry.title}")
                continue

            new_title, new_body = paraphrase(entry.title, text)
            final_img = upload_image(img)

            html = "<div dir='rtl'>"
            if final_img:
                html += f"<img src='{final_img}' style='max-width:100%'><br><br>"
            html += new_body.replace("\n", "<br>")
            html += "</div>"

            post = service.posts().insert(
                blogId=BLOG_ID,
                body={
                    "title": new_title,
                    "content": html,
                    "labels": BLOGGER_LABELS,
                    "isDraft": False
                }
            ).execute()

            save_history(entry.link)
            published += 1

            send_telegram("success", f"{new_title}\n{post['url']}")

            time.sleep(random.randint(MIN_SLEEP, MAX_SLEEP))

        except Exception as e:
            send_telegram("error", f"{entry.title}\n{e}")

    if published == 0:
        send_telegram("info", "❌ لم يتم نشر أي أخبار")

if __name__ == "__main__":
    main()
