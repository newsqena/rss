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

WAIT_MIN = 40
WAIT_MAX = 70

HISTORY_FILE = "published_urls.txt"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

cloudinary.config(
    cloud_name="dldxptjuf",
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

# =========================
# إدارة الذاكرة والتنظيف
# =========================

def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    with open(HISTORY_FILE, "r") as f:
        return [line.strip() for line in f.readlines()]

def save_to_history(link):
    with open(HISTORY_FILE, "a") as f:
        f.write(link + "\n")

def clean_for_display(text):
    if not text:
        return ""
    clean = re.sub(r'[*#\"\'“”«»]', '', text)
    clean = re.sub(
        r'^(عنوان جديد|العنوان|عنوان الخبر|Title|New Title|Headline):\s*',
        '',
        clean,
        flags=re.IGNORECASE
    )
    return clean.strip()

def trim_to_max_length(text, max_len=1600):
    if len(text) <= max_len:
        return text

    trimmed = text[:max_len]
    last_stop = max(
        trimmed.rfind("。"),
        trimmed.rfind("."),
        trimmed.rfind("،"),
        trimmed.rfind(",")
    )

    if last_stop > 400:
        return trimmed[:last_stop + 1]

    return trimmed

# =========================
# Telegram
# =========================

def send_telegram(status, message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return

    icons = {"success": "✅", "error": "🚨", "info": "ℹ️"}
    formatted = f"{icons.get(status, 'ℹ️')} <b>{BOT_NAME}</b>\n\n{message}"

    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": formatted,
                "parse_mode": "HTML",
                "disable_web_page_preview": False
            },
            timeout=10
        )
    except:
        pass

# =========================
# Blogger
# =========================

def get_blogger_service():
    creds_dict = json.loads(os.environ.get("BLOGGER_CREDS_JSON"))
    creds = Credentials.from_authorized_user_info(creds_dict, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("blogger", "v3", credentials=creds)

# =========================
# استخراج المقال
# =========================

def extract_article(link):
    try:
        r = requests.get(link, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")

        container = (
            soup.find("div", class_="entry") or
            soup.find("div", class_="article-content") or
            soup.find("article")
        )

        if container:
            for tag in container.find_all(["script", "style", "iframe", "aside", "ins"]):
                tag.decompose()
            text = container.get_text(" ", strip=True)
        else:
            paragraphs = soup.find_all("p")
            text = " ".join([p.get_text() for p in paragraphs if len(p.get_text()) > 60])

        img = soup.find("meta", property="og:image")
        img_url = img.get("content") if img else None

        return text, img_url
    except:
        return None, None

# =========================
# إعادة الصياغة (إجباري)
# =========================

def paraphrase_all(title, content):
    api_key = os.getenv("GEMINI_API_KEY")
    
    # 1. فحص هل المفتاح موجود أصلاً؟
    if not api_key:
        print("❌ الخطأ: مفتاح GEMINI_API_KEY غير موجود في Secrets")
        return None, None
    else:
        print(f"✅ المفتاح موجود، طوله: {len(api_key)} حرف")

    # 2. فحص الموديلات المتاحة لهذا المفتاح (أهم خطوة)
    # سنطلب من جوجل إعطاءنا قائمة بالموديلات التي يسمح لنا باستخدامها
    list_models_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    
    try:
        print("🔍 جاري فحص الموديلات المتاحة لحسابك...")
        check_r = requests.get(list_models_url, timeout=10)
        if check_r.status_code == 200:
            models_data = check_r.json()
            available_models = [m['name'] for m in models_data.get('models', [])]
            print(f"📋 الموديلات المتاحة لك هي: {available_models}")
        else:
            print(f"❌ فشل فحص الموديلات. كود الخطأ: {check_r.status_code}")
            print(f"📝 رسالة جوجل: {check_r.text}")
    except Exception as e:
        print(f"❌ حدث خطأ أثناء الاتصال بجوجل: {e}")

    # 3. محاولة الإرسال النهائية مع طباعة تفاصيل الطلب
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": "test"}]}]}
    
    print(f"🚀 محاولة إرسال طلب تجريبي للموديل gemini-1.5-flash...")
    try:
        r = requests.post(url, json=payload, timeout=10)
        print(f"📡 نتيجة الطلب التجريبي - كود الحالة: {r.status_code}")
        if r.status_code != 200:
            print(f"⚠️ تفاصيل الخطأ من جوجل: {r.text}")
    except Exception as e:
        print(f"❌ فشل الطلب التجريبي: {e}")

    return None, None # سنوقف البوت هنا فقط لنرى النتائج في الـ Logs

# =========================
# التشغيل الرئيسي
# =========================

def main():
    print(f"🚀 Starting {BOT_NAME}...")

    service = get_blogger_service()
    feed = feedparser.parse(requests.get(RSS_URL, headers=HEADERS, timeout=20).content)
    published = load_history()

    latest_entries = feed.entries[:3]
    published_count = 0

    for entry in latest_entries:
        if published_count >= MAX_POSTS_PER_RUN:
            break
        if entry.link in published:
            continue

        print(f"🧐 Processing: {entry.title}")

        text, img = extract_article(entry.link)
        if not text:
            continue

        new_title, new_content = paraphrase_all(entry.title, text)
        time.sleep(random.randint(WAIT_MIN, WAIT_MAX))

        if not new_title or not new_content:
            continue

        new_content = trim_to_max_length(new_content, 10000)

        final_img = img
        if img:
            try:
                up = cloudinary.uploader.upload(img, folder="news")
                final_img = up["secure_url"]
            except:
                pass

        html = "<div dir='rtl' style='text-align:justify;font-size:18px;line-height:1.6;'>"
        if final_img:
            html += f"<div style='text-align:center'><img src='{final_img}' style='max-width:100%;border-radius:10px;'></div><br>"
        html += new_content.replace("\n", "<br>")
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

        save_to_history(entry.link)
        published.append(entry.link)
        published_count += 1

        print(f"✅ Published: {new_title}")
        send_telegram("success", f"<b>{new_title}</b>\n\n🔗 {post.get('url')}")

if __name__ == "__main__":
    main()







