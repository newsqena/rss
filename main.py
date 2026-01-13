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
MIN_SLEEP = 40
MAX_SLEEP = 75

# رأس طلب يحاكي متصفح حقيقي لتجاوز حماية المواقع
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.7,en;q=0.3",
    "Referer": "https://www.google.com/",
    "Connection": "keep-alive"
}

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

    icons = {"success": "✅", "info": "ℹ️", "skip": "⏭️", "duplicate": "🔁", "error": "🚨"}
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
# Blogger Credentials
# =========================

def get_blogger_credentials():
    creds_json = os.environ.get("BLOGGER_CREDS_JSON")
    if not creds_json:
        raise Exception("BLOGGER_CREDS_JSON secret not found")

    creds_dict = json.loads(creds_json)
    creds = Credentials.from_authorized_user_info(creds_dict, SCOPES)

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds

def build_blogger_service():
    creds = get_blogger_credentials()
    return build("blogger", "v3", credentials=creds)

# =========================
# RSS
# =========================

def get_latest_news(rss_url, limit=20):
    feed = feedparser.parse(rss_url)
    return [(e.title, e.link) for e in feed.entries[:limit]]

# =========================
# سحب المقال بالكامل
# =========================

def extract_article(link, mode):
    try:
        r = requests.get(link, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        if mode == "default":
            # موقع بتوقيت النجع
            container = soup.find("div", class_="entry") or soup.find("article")
        else:
            # موقع صدى البلد
            container = soup.find(class_="paragraph-list") or soup.find("div", id="article-body")

        if not container:
            return "", None

        # تنظيف المحتوى مع الحفاظ على النص فقط
        for tag in container.find_all(["script", "style", "iframe", "video", "aside", "ins", "button"]):
            tag.decompose()

        text = container.get_text(" ", strip=True)

        # سحب الصورة الأصلية
        img_url = None
        main_img = soup.find("img")
        if mode == "custom":
            img_container = soup.find(class_="main-img")
            if img_container and img_container.find("img"):
                main_img = img_container.find("img")
        
        if main_img:
            img_url = main_img.get("src") or main_img.get("data-src") or main_img.get("data-lazy-src")

        return text, img_url
    except Exception as e:
        print(f"Connection Error: {e}")
        return None, None

# =========================
# إعادة الصياغة (بدون تحديد حجم النص)
# =========================

def paraphrase_article(text):
    try:
        # هنا يتم إرسال الخبر بالكامل مهما كان طوله
        prompt = (
            "أعد صياغة الخبر التالي بأسلوب صحفي احترافي وشيق. "
            "حافظ على جميع المعلومات والأسماء والأرقام الواردة في الخبر دون حذف أو اختصار مخل. "
            "اجعل الخبر منظماً في فقرات واضحة:\n\n" + text
        )

        r = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        article = r.choices[0].message.content.strip()

        # طلب عنوان جديد بناءً على النص المصاغ بالكامل
        t = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"أعطني عنواناً صحفياً قوياً لهذا الخبر:\n\n{article}"}],
            temperature=0.3
        )
        return t.choices[0].message.content.strip(), article
    except Exception as e:
        print(f"OpenAI Error: {e}")
        return None, None

# =========================
# Cloudinary & Blogger Helpers
# =========================

def upload_to_cloudinary(image_url):
    if not image_url or not image_url.startswith("http"):
        return None
    try:
        res = cloudinary.uploader.upload(image_url, folder="blogger_news")
        return res["secure_url"]
    except:
        return image_url

def is_title_duplicate(service, title):
    try:
        posts = service.posts().list(blogId=BLOG_ID, maxResults=15, fetchBodies=False).execute().get("items", [])
        return any(p["title"].strip() == title.strip() for p in posts)
    except:
        return False

def publish_to_blogger(service, title, content, labels):
    if is_title_duplicate(service, title):
        return "duplicate"

    post = service.posts().insert(
        blogId=BLOG_ID,
        body={"title": title, "content": content, "labels": labels, "isDraft": False}
    ).execute()
    return post["url"]

def build_post_content(text, image_url):
    img_html = f"<div style='text-align:center'><img src='{image_url}' style='max-width:100%'></div><br>" if image_url else ""
    return img_html + f"<div dir='rtl' style='text-align:justify; font-size:18px; line-height:1.6;'>{text.replace(chr(10), '<br>')}</div>"

# =========================
# تشغيل البوت
# =========================

def main():
    try:
        service = build_blogger_service()
        news = get_latest_news(RSS_URL)
        published_count = 0

        for title, link in news:
            if published_count >= MAX_POSTS_PER_RUN:
                break

            # سحب الخبر الأصلي بالكامل
            text, img = extract_article(link, SCRAPE_MODE)
            
            if not text or len(text) < 100:
                continue

            # صياغة النص بالكامل
            new_title, new_article = paraphrase_article(text)
            if not new_title:
                continue

            final_img = upload_to_cloudinary(img)
            html_content = build_post_content(new_article, final_img)

            result = publish_to_blogger(service, new_title, html_content, BLOGGER_LABELS)
            
            if result == "duplicate":
                continue
            elif result:
                published_count += 1
                send_telegram("success", f"<b>{new_title}</b>\n\n{result}")
                time.sleep(random.randint(MIN_SLEEP, MAX_SLEEP))

    except Exception as e:
        send_telegram("error", f"🚨 خطأ عام:\n<code>{e}</code>")
        raise

if __name__ == "__main__":
    main()
