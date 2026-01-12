import os
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
SCRAPE_MODE = os.getenv("SCRAPE_MODE", "default")  # default | custom

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

BLOG_ID = "8964557641790201632"
CREDENTIALS_FILE = "creds.json"
SCOPES = ["https://www.googleapis.com/auth/blogger"]

MAX_POSTS_PER_RUN = 3
MIN_SLEEP = 30
MAX_SLEEP = 90

# =========================
# OpenAI
# =========================

openai_client = OpenAI(api_key=OPENAI_API_KEY)

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
        "info": "ℹ️",
        "skip": "⏭️",
        "duplicate": "🔁",
        "error": "🚨"
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
# RSS
# =========================

def get_latest_news(rss_url, limit=20):
    feed = feedparser.parse(rss_url)
    items = []
    for entry in feed.entries[:limit]:
        items.append((entry.title, entry.link))
    return items

# =========================
# سحب المقال - عادي
# =========================

def extract_default_article(link):
    r = requests.get(link, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    container = soup.find("div", class_="entry")
    if not container:
        return "", None

    for tag in container.find_all(["script", "style", "iframe", "img", "video", "aside"]):
        tag.decompose()

    text = container.get_text(" ", strip=True)

    img = soup.find("img")
    img_url = img.get("src") if img else None

    return text, img_url

# =========================
# سحب المقال - مخصص
# =========================

def extract_custom_article(link):
    r = requests.get(link, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    text_container = soup.find(class_="paragraph-list")
    if not text_container:
        return "", None

    for tag in text_container.find_all(["script", "style", "iframe", "video", "aside"]):
        tag.decompose()

    text = text_container.get_text(" ", strip=True)

    image_url = None
    img_container = soup.find(class_="main-img")
    if img_container:
        img = img_container.find("img")
        if img:
            image_url = img.get("src")

    return text, image_url

# =========================
# إعادة الصياغة
# =========================

def paraphrase_article(text):
    prompt = (
        "أعد صياغة الخبر صياغة صحفية خفيفة دون تغيير المعنى أو الترتيب، "
        "وقسّمه إلى 3 فقرات فقط:\n\n" + text
    )

    r = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.25
    )

    article = r.choices[0].message.content.strip()

    title_prompt = "أعد صياغة عنوان مناسب لهذا الخبر:\n\n" + article

    t = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": title_prompt}],
        temperature=0.25
    )

    title = t.choices[0].message.content.strip()
    return title, article

# =========================
# Cloudinary
# =========================

def upload_to_cloudinary(image_url):
    if not image_url:
        return None
    try:
        r = requests.get(image_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        res = cloudinary.uploader.upload(r.content, folder="blogger_news")
        return res["secure_url"]
    except:
        return image_url

# =========================
# Blogger
# =========================

def build_blogger_service():
    creds = Credentials.from_authorized_user_file(CREDENTIALS_FILE, SCOPES)
    if creds.expired:
        creds.refresh(Request())
    return build("blogger", "v3", credentials=creds)

def is_title_duplicate(service, title):
    posts = service.posts().list(
        blogId=BLOG_ID,
        maxResults=20,
        fetchBodies=False
    ).execute().get("items", [])

    for p in posts:
        if p["title"].strip() == title.strip():
            return True
    return False

def publish_to_blogger(service, title, content, labels):
    if is_title_duplicate(service, title):
        send_telegram("duplicate", f"عنوان مكرر:\n{title}")
        return None

    body = {
        "title": title,
        "content": content,
        "labels": labels,
        "isDraft": False
    }

    post = service.posts().insert(blogId=BLOG_ID, body=body).execute()
    return post["url"]

# =========================
# HTML المحتوى
# =========================

def build_post_content(text, image_url):
    img_html = ""
    if image_url:
        img_html = f"<div style='text-align:center'><img src='{image_url}'></div><br>"
    return img_html + f"<div dir='rtl'>{text.replace(chr(10), '<br>')}</div>"

# =========================
# MAIN
# =========================

def main():
    try:
        service = build_blogger_service()
        news = get_latest_news(RSS_URL)
        published = 0

        for title, link in news:
            if published >= MAX_POSTS_PER_RUN:
                break

            try:
                if SCRAPE_MODE == "default":
                    text, img = extract_default_article(link)
                else:
                    text, img = extract_custom_article(link)

                if not text:
                    send_telegram("skip", f"تم تخطي الخبر:\n{title}")
                    continue

                final_img = upload_to_cloudinary(img)
                new_title, new_article = paraphrase_article(text)
                html = build_post_content(new_article, final_img)

                url = publish_to_blogger(service, new_title, html, BLOGGER_LABELS)
                if url:
                    published += 1
                    send_telegram("success", f"{new_title}\n{url}")

                time.sleep(random.randint(MIN_SLEEP, MAX_SLEEP))

            except Exception as e:
                send_telegram("error", f"{title}\n<code>{e}</code>")

        if published == 0:
            send_telegram("info", "لا توجد أخبار جديدة في هذا التشغيل")

    except Exception as e:
        send_telegram("error", f"خطأ عام:\n<code>{e}</code>")
        raise

if __name__ == "__main__":
    main()
