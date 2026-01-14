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
# الإعدادات
# =========================
RSS_URL = os.getenv("RSS_URL")
BLOGGER_LABELS = [x.strip() for x in os.getenv("BLOGGER_LABELS", "").split(",") if x.strip()]
BOT_NAME = os.getenv("BOT_NAME", "News Bot")
BLOG_ID = "8964557641790201632"
SCOPES = ["https://www.googleapis.com/auth/blogger"]
MAX_POSTS_PER_RUN = 3
HISTORY_FILE = "published_urls.txt"

# =========================
# جلسة HTTP (لتجاوز الحظر)
# =========================
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Language": "ar,en-US;q=0.7,en;q=0.3"
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
# أدوات مساعدة
# =========================
def send_telegram(status, message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    icons = {"success": "✅", "error": "🚨", "info": "ℹ️"}
    text = f"{icons.get(status,'ℹ️')} <b>{BOT_NAME}</b>\n\n{message}"
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
    except:
        pass


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return [x.strip() for x in f if x.strip()]


def save_history(link):
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(link + "\n")


# =========================
# تحميل RSS (الحل النهائي)
# =========================
def load_news():
    print("🌐 Loading RSS directly with feedparser...")
    feed = feedparser.parse(RSS_URL)

    if not feed.entries:
        print("❌ RSS فارغ أو مرفوض")
        return []

    print(f"✅ RSS entries: {len(feed.entries)}")
    return [(e.title, e.link) for e in feed.entries]


# =========================
# استخراج المقال
# =========================
def extract_article(url):
    try:
        r = session.get(url, timeout=25)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        content_div = soup.find("div", class_="paragraph-list")
        if not content_div:
            paragraphs = soup.find_all("p")
            text = "\n".join(p.get_text(strip=True) for p in paragraphs if len(p.text) > 40)
        else:
            text = "\n".join(p.get_text(strip=True) for p in content_div.find_all("p"))

        img_url = None
        img_div = soup.find("div", class_="main-img")
        if img_div:
            img = img_div.find("img")
            if img:
                img_url = img.get("src")

        return text.strip(), img_url
    except Exception as e:
        print(f"❌ Extract error: {e}")
        return None, None


# =========================
# رفع الصورة
# =========================
def upload_image(img_url):
    if not img_url:
        return None
    try:
        res = session.get(img_url, timeout=20)
        upload = cloudinary.uploader.upload(
            res.content,
            folder="blogger_news",
            transformation=[{"width": 900, "crop": "limit"}, {"quality": "auto"}]
        )
        return upload["secure_url"]
    except:
        return img_url


# =========================
# OpenAI إعادة الصياغة
# =========================
def paraphrase(title, text):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return title, text

    payload = {
        "model": "gpt-4o-mini",
        "messages": [{
            "role": "user",
            "content": f"""
أعد صياغة الخبر التالي بأسلوب صحفي عربي واضح.
ابدأ بالعنوان فقط بدون أي مقدمات.

العنوان:
{title}

المحتوى:
{text}
"""
        }],
        "temperature": 0.4
    }

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=45
        )
        data = r.json()["choices"][0]["message"]["content"].strip()
        lines = data.split("\n")
        return lines[0], "\n".join(lines[1:])
    except Exception as e:
        print("⚠️ OpenAI Error:", e)
        return title, text


# =========================
# Blogger
# =========================
def get_blogger():
    creds = json.loads(os.environ["BLOGGER_CREDS_JSON"])
    credentials = Credentials.from_authorized_user_info(creds, SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    return build("blogger", "v3", credentials=credentials)


# =========================
# التشغيل الرئيسي
# =========================
def main():
    print(f"🚀 Starting {BOT_NAME}")
    send_telegram("info", "🚀 بدأ التشغيل")

    history = load_history()
    news = load_news()

    if not news:
        send_telegram("info", "❌ لا توجد أخبار")
        return

    service = get_blogger()
    published = 0

    for title, link in news:
        if published >= MAX_POSTS_PER_RUN:
            break

        if link in history:
            print(f"⏭️ مكرر: {title}")
            send_telegram("info", f"❌ عنوان مكرر\n{title}")
            continue

        print(f"🧐 Processing: {title}")
        text, img = extract_article(link)

        if not text or len(text) < 200:
            send_telegram("info", f"❌ تخطي لضعف المحتوى\n{title}")
            continue

        new_title, new_text = paraphrase(title, text)
        img_final = upload_image(img)

        html = "<div dir='rtl' style='font-size:18px;line-height:1.9'>"
        if img_final:
            html += f"<img src='{img_final}' style='max-width:100%;border-radius:10px'><br><br>"
        html += new_text.replace("\n", "<br>") + "</div>"

        service.posts().insert(
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
        send_telegram("success", f"✅ تم النشر\n{new_title}")

        time.sleep(random.randint(40, 75))

    if published == 0:
        send_telegram("info", "❌ لم يتم نشر أي خبر")


if __name__ == "__main__":
    main()
