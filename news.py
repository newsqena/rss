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
HISTORY_FILE = "published_urls.txt" 

WAIT_MIN = 40
WAIT_MAX = 70

# هيدرز متقدمة جداً لمحاكاة متصفح حقيقي بالكامل
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.7,en;q=0.3",
    "Referer": "https://www.google.com/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1"
}

cloudinary.config(
    cloud_name="dldxptjuf",
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

# =========================
# وظائف الإدارة والتنظيف
# =========================

def load_history():
    if not os.path.exists(HISTORY_FILE): return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return [line.strip() for line in f.readlines()]
    except: return []

def save_to_history(link):
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(link + "\n")

def clean_for_display(text):
    if not text: return ""
    # حذف الكلمات التي طلبتِها
    text = re.sub(r'(اسلام نبيل|بتوقيت النجع|شمالي محافظة قنا|إسلام نبيل)', '', text)
    clean = re.sub(r'[*#\"\'“”«»]', '', text)
    clean = re.sub(r'^(عنوان جديد|العنوان|عنوان الخبر|Title|New Title|Headline):\s*', '', clean, flags=re.IGNORECASE)
    return clean.strip()

def send_telegram(status, message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id: return
    icons = {"success": "✅", "error": "🚨"}
    formatted_text = f"{icons.get(status, 'ℹ️')} <b>{BOT_NAME}</b>\n\n{message}"
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                      data={"chat_id": chat_id, "text": formatted_text, "parse_mode": "HTML"}, timeout=10)
    except: pass

# =========================
# وظيفة الرفع المطورة (Download then Upload)
# =========================

def upload_to_cloudinary_pro(image_url):
    if not image_url or "data:image" in image_url: return None
    try:
        img_response = requests.get(image_url, headers=HEADERS, timeout=20)
        img_response.raise_for_status()
        response = cloudinary.uploader.upload(
            img_response.content,
            folder="blogger_news",
            transformation=[{'width': 800, 'crop': "limit"}, {'quality': "auto"}]
        )
        return response['secure_url']
    except Exception as e:
        print(f"⚠️ Cloudinary Error: {e}")
        return image_url

# =========================
# استخراج البيانات من الكلاسات المحددة
# =========================

def extract_article(link):
    try:
        r = requests.get(link, headers=HEADERS, timeout=25)
        r.encoding = 'utf-8'
        soup = BeautifulSoup(r.text, "html.parser")
        
        # 1. سحب النص من كلاس entry (موقع بتوقيت النجع)
        container = soup.find("div", class_="entry") or soup.find("div", class_="entry-content")
        if container:
            for tag in container.find_all(["script", "style", "iframe", "aside", "ins", "footer", "div"]):
                if tag.get('class') and 'related' in tag.get('class'): tag.decompose()
            text = container.get_text(" ", strip=True)
        else:
            text = " ".join([p.get_text() for p in soup.find_all("p") if len(p.get_text()) > 50])

        # 2. سحب الصورة من كلاس single-post-thumb
        img_url = None
        img_container = soup.find("div", class_="single-post-thumb")
        if img_container:
            img_tag = img_container.find("img")
            if img_tag:
                # معالجة Lazy Load (تحميل كسلان)
                img_url = img_tag.get("data-src") or img_tag.get("src") or img_tag.get("data-lazy-src")

        if not img_url:
            og_img = soup.find("meta", property="og:image")
            img_url = og_img.get("content") if og_img else None

        return text, img_url
    except: return None, None

# =========================
# الذكاء الاصطناعي وبلوجر
# =========================

def paraphrase_all(original_title, original_text):
    api_key = os.getenv("OPENAI_API_KEY")
    url = "https://api.openai.com/v1/chat/completions"
    prompt = (
        f"أنت صحفي محترف. أعد صياغة الخبر التالي بأسلوب مشوق.\n\n"
        f"العنوان الأصلي: {original_title}\n\n"
        f"المحتوى الأصلي: {original_text}\n\n"
        f"المطلوب:\n"
        f"1. ابدأ بالعنوان الجديد مباشرة.\n"
        f"2. قسّم الخبر إلى 3 فقرات مع سطرين فارغين بينها.\n"
        f"3. احذف أسماء الأشخاص والمواقع (إسلام نبيل، بتوقيت النجع).\n"
        f"4. لا تستخدم علامات ** نهائياً."
    )
    payload = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "temperature": 0.4}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=45)
        full_result = response.json()['choices'][0]['message']['content'].strip()
        lines = full_result.split('\n')
        return clean_for_display(lines[0]), "\n".join(lines[1:]).strip()
    except: return None, None

def get_blogger_service():
    creds_json = os.environ.get("BLOGGER_CREDS_JSON")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_authorized_user_info(creds_dict, SCOPES)
    if creds.expired and creds.refresh_token: creds.refresh(Request())
    return build("blogger", "v3", credentials=creds)

# =========================
# التشغيل الرئيسي
# =========================

def main():
    print(f"🚀 Starting {BOT_NAME}...")
    try:
        service = get_blogger_service()
        response = requests.get(RSS_URL, headers=HEADERS, timeout=20)
        feed = feedparser.parse(response.content)
        published_links = load_history()

        published_count = 0
        for entry in feed.entries:
            if published_count >= MAX_POSTS_PER_RUN: break
            if entry.link in published_links: 
                print(f"⏭️ Skipped: {entry.title}")
                continue

            print(f"🧐 Processing: {entry.title}")
            text, img = extract_article(entry.link)
            if not text or len(text) < 100: continue

            new_title, new_content = paraphrase_all(entry.title, text)
            if not new_title: continue

            final_img = upload_to_cloudinary_pro(img)

            html = f"<div dir='rtl' style='text-align:justify; font-size:18px; line-height:1.8;'>"
            if final_img: 
                html += f"<div style='text-align:center'><img src='{final_img}' style='max-width:100%; border-radius:12px;'></div><br>"
            html += f"{new_content.replace(chr(10), '<br>')}</div>"

            inserted_post = service.posts().insert(
                blogId=BLOG_ID,
                body={"title": new_title, "content": html, "labels": BLOGGER_LABELS, "isDraft": False}
            ).execute()
            
            save_to_history(entry.link)
            published_count += 1
            print(f"✅ Published: {new_title}")
            send_telegram("success", f"<b>{new_title}</b>\n\n🔗 رابط الخبر:\n{inserted_post.get('url')}")
            
            if published_count < MAX_POSTS_PER_RUN:
                time.sleep(random.randint(WAIT_MIN, WAIT_MAX))

    except Exception as e:
        print(f"🚨 Error: {e}")
        send_telegram("error", f"خطأ في البوت: {e}")

if __name__ == "__main__":
    main()
