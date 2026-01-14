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
HISTORY_FILE = "published_urls.txt"

# =========================
# Session (محاكاة متصفح حقيقي)
# =========================

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.7,en;q=0.3",
    "Referer": "https://www.google.com/"
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
# تحميل الأخبار (RSS أو Homepage)
# =========================

def extract_article(link):
    try:
        proxy_link = f"https://textise.org/showtext.aspx?strURL={link}"
        r = session.get(proxy_link, timeout=40)
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")

        paragraphs = soup.find_all("p")
        text = " ".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 40)

        if not text:
            return None, None

        # الصورة ناخدها من الرابط الحقيقي مش البروكسي
        real_page = session.get(link, timeout=30)
        soup_real = BeautifulSoup(real_page.text, "html.parser")

        img_url = None
        img_container = soup_real.find(class_="main-img")
        if img_container:
            img = img_container.find("img")
            if img:
                img_url = img.get("src")

        if not img_url:
            og = soup_real.find("meta", property="og:image")
            if og:
                img_url = og.get("content")

        return text, img_url

    except Exception as e:
        print("❌ Article extract failed:", e)
        return None, None


# =========================
# استخراج المقال
# =========================

def extract_article(link):
    r = session.get(link, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")

    text_container = soup.find(class_="paragraph-list") or soup.find("div", class_="entry")
    if not text_container:
        return None, None

    for tag in text_container.find_all(["script", "style", "iframe", "aside", "footer"]):
        tag.decompose()

    text = text_container.get_text(" ", strip=True)

    img_url = None
    img_container = soup.find(class_="main-img")
    if img_container:
        img = img_container.find("img")
        if img:
            img_url = img.get("src")

    if not img_url:
        og = soup.find("meta", property="og:image")
        if og:
            img_url = og.get("content")

    return text, img_url

# =========================
# OpenAI (إعادة صياغة)
# =========================

def paraphrase(title, text):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return title, text

    prompt = (
        "أعد صياغة الخبر بأسلوب صحفي واضح دون تغيير المعنى.\n\n"
        f"العنوان:\n{title}\n\n"
        f"المحتوى:\n{text}"
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
        content = r.json()["choices"][0]["message"]["content"].strip()
        lines = content.split("\n", 1)
        return lines[0], lines[1] if len(lines) > 1 else content

    except Exception as e:
        print("⚠️ OpenAI failed:", e)
        return title, text

# =========================
# Blogger
# =========================

def get_blogger_service():
    creds_dict = json.loads(os.environ["BLOGGER_CREDS_JSON"])
    creds = Credentials.from_authorized_user_info(creds_dict, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("blogger", "v3", credentials=creds)

# =========================
# MAIN
# =========================

def main():
    print(f"🚀 Starting {BOT_NAME}")

    service = get_blogger_service()
    history = load_history()

    news = load_news()
    if not news:
        send_telegram("error", "❌ لا توجد أخبار صالحة")
        return

    published = 0

    for title, link in news:
        if published >= MAX_POSTS_PER_RUN:
            break

        if link in history:
            continue

        print("🧐 Processing:", title)

        text, img = extract_article(link)
        if not text or len(text) < 150:
            continue

        new_title, new_text = paraphrase(title, text)

        html = "<div dir='rtl' style='font-size:18px;line-height:1.8'>"
        if img:
            html += f"<div style='text-align:center'><img src='{img}'></div><br>"
        html += new_text.replace("\n", "<br>")
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

        save_history(link)
        published += 1

        send_telegram("success", f"{new_title}\n{post['url']}")
        time.sleep(random.randint(MIN_SLEEP, MAX_SLEEP))

    if published == 0:
        send_telegram("info", "❌ لا توجد أخبار جديدة")

if __name__ == "__main__":
    main()
