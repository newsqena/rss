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
# إعدادات عامة
# =========================

RSS_URL = os.getenv("RSS_URL")
BLOGGER_LABELS = [l.strip() for l in os.getenv("BLOGGER_LABELS", "").split(",") if l.strip()]
BOT_NAME = os.getenv("BOT_NAME", "Unknown Bot")
BLOG_ID = "8964557641790201632"
SCOPES = ["https://www.googleapis.com/auth/blogger"]

MAX_POSTS_PER_RUN = 3
MIN_SLEEP = 40
MAX_SLEEP = 75

# =========================
# Session (تجاوز الحماية)
# =========================

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Language": "ar,en;q=0.9",
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

    text = f"{icons.get(status,'ℹ️')} <b>{BOT_NAME}</b>\n\n{message}"

    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
    except:
        pass

# =========================
# Blogger
# =========================

def get_blogger_service():
    creds_json = os.getenv("BLOGGER_CREDS_JSON")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_authorized_user_info(creds_dict, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("blogger", "v3", credentials=creds)

# =========================
# RSS (المهم)
# =========================

def load_rss():
    r = session.get(RSS_URL, timeout=25)
    r.encoding = "utf-8"
    feed = feedparser.parse(r.text)
    return feed.entries or []

# =========================
# استخراج المقال
# =========================

def extract_article(link):
    r = session.get(link, timeout=25)
    soup = BeautifulSoup(r.text, "html.parser")

    text_container = soup.find("div", class_="entry") or soup.find("div", class_="entry-content")
    if not text_container:
        return None, None

    for tag in text_container.find_all(["script", "style", "iframe", "aside"]):
        tag.decompose()

    text = text_container.get_text(" ", strip=True)

    img = None
    img_container = soup.find("div", class_="single-post-thumb")
    if img_container:
        img_tag = img_container.find("img")
        if img_tag:
            img = img_tag.get("src")

    if not img:
        og = soup.find("meta", property="og:image")
        img = og.get("content") if og else None

    return text, img

# =========================
# Cloudinary Upload
# =========================

def upload_image(img_url):
    if not img_url:
        return None
    try:
        r = session.get(img_url, timeout=20)
        res = cloudinary.uploader.upload(
            r.content,
            folder="blogger_news",
            transformation=[{"width": 800, "crop": "limit"}, {"quality": "auto"}]
        )
        return res["secure_url"]
    except:
        return img_url

# =========================
# OpenAI (REST)
# =========================

def paraphrase(title, text):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, None

    prompt = (
        f"أعد صياغة الخبر التالي صياغة صحفية احترافية دون تغيير المعنى.\n\n"
        f"العنوان: {title}\n\n"
        f"النص:\n{text}\n\n"
        f"- قسّم الخبر إلى 3 فقرات فقط\n"
        f"- احذف أي ذكر لأسماء (إسلام نبيل، بتوقيت النجع)\n"
        f"- لا تستخدم علامات **"
    )

    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=40
        )
        result = r.json()["choices"][0]["message"]["content"].strip()
        lines = result.split("\n")
        return lines[0].strip(), "\n".join(lines[1:]).strip()
    except:
        return None, None

# =========================
# MAIN
# =========================

def main():
    print(f"🚀 Starting {BOT_NAME}")
    send_telegram("info", "🚀 بدء تشغيل البوت")

    service = get_blogger_service()
    entries = load_rss()

    if not entries:
        send_telegram("info", "❌ لا توجد أخبار في رابط RSS")
        return

    published = 0
    used_titles = set()

    for entry in entries:
        if published >= MAX_POSTS_PER_RUN:
            break

        if entry.title in used_titles:
            continue

        print(f"📰 {entry.title}")
        text, img = extract_article(entry.link)
        if not text or len(text) < 150:
            send_telegram("skip", f"تم تخطي خبر ضعيف:\n{entry.title}")
            continue

        new_title, new_text = paraphrase(entry.title, text)
        if not new_title:
            send_telegram("error", f"فشل OpenAI:\n{entry.title}")
            continue

        final_img = upload_image(img)

        html = "<div dir='rtl' style='text-align:justify;font-size:18px;line-height:1.8;'>"
        if final_img:
            html += f"<div style='text-align:center'><img src='{final_img}' style='max-width:100%;border-radius:12px;'></div><br>"
        html += new_text.replace("\n", "<br>") + "</div>"

        post = service.posts().insert(
            blogId=BLOG_ID,
            body={
                "title": new_title,
                "content": html,
                "labels": BLOGGER_LABELS,
                "isDraft": False
            }
        ).execute()

        used_titles.add(entry.title)
        published += 1

        send_telegram("success", f"{new_title}\n{post.get('url')}")
        time.sleep(random.randint(MIN_SLEEP, MAX_SLEEP))

    if published == 0:
        send_telegram("info", "❌ لم يتم نشر أي خبر")

if __name__ == "__main__":
    main()
