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
# وظائف إدارة الذاكرة والتنظيف
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
    if not text: return ""
    clean = re.sub(r'[*#\"\'“”«»]', '', text)
    clean = re.sub(r'^(عنوان جديد|العنوان|عنوان الخبر|Title|New Title|Headline):\s*', '', clean, flags=re.IGNORECASE)
    return clean.strip()

def send_telegram(status, message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id: return
    
    icons = {"success": "✅", "error": "🚨", "info": "ℹ️"}
    formatted_text = f"{icons.get(status, 'ℹ️')} <b>{BOT_NAME}</b>\n\n{message}"
    
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage", 
            data={
                "chat_id": chat_id, 
                "text": formatted_text, 
                "parse_mode": "HTML",
                "disable_web_page_preview": False 
            }, timeout=10)
    except: pass

# =========================
# وظائف بلوجر والذكاء الاصطناعي
# =========================

def get_blogger_service():
    creds_json = os.environ.get("BLOGGER_CREDS_JSON")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_authorized_user_info(creds_dict, SCOPES)
    if creds.expired and creds.refresh_token: creds.refresh(Request())
    return build("blogger", "v3", credentials=creds)

def extract_article(link):
    try:
        r = requests.get(link, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        container = soup.find("div", class_="entry") or soup.find("div", class_="article-content") or soup.find("article")
        if not container:
            paragraphs = soup.find_all("p")
            text = " ".join([p.get_text() for p in paragraphs if len(p.get_text()) > 60])
        else:
            for tag in container.find_all(["script", "style", "iframe", "aside", "ins"]): tag.decompose()
            text = container.get_text(" ", strip=True)
        img_tag = soup.find("meta", property="og:image")
        img_url = img_tag.get("content") if img_tag else None
        return text, img_url
    except: return None, None

def paraphrase_all(original_title, original_text):
    api_key = os.getenv("OPENAI_API_KEY")
    url = "https://api.openai.com/v1/chat/completions"
    prompt = (
        f"أنت صحفي محترف. أعد صياغة الخبر التالي بأسلوب مشوق.\n\n"
        f"العنوان الأصلي: {original_title}\n\n"
        f"المحتوى الأصلي: {original_text}\n\n"
        f"المطلوب حرفياً:\n"
        f"1. اكتب العنوان الجديد في السطر الأول مباشرة بدون مقدمات.\n"
        f"2. اترك سطراً فارغاً ثم الخبر بصياغة احترافية.\n"
        f"3. لا تستخدم النجوم (**) أو علامات التنصيص نهائياً."
    )
    payload = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "temperature": 0.4}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=45)
        full_result = response.json()['choices'][0]['message']['content'].strip()
        lines = full_result.split('\n')
        return clean_for_display(lines[0]), "\n".join(lines[1:]).strip()
    except: return None, None

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
            if entry.link in published_links: continue

            print(f"🧐 Processing: {entry.title}")
            text, img = extract_article(entry.link)
            if not text or len(text) < 150: continue

            new_title, new_content = paraphrase_all(entry.title, text)
            if not new_title: continue

            # رفع الصورة لـ Cloudinary
            final_img = img
            if img:
                try: 
                    up = cloudinary.uploader.upload(img, folder="news")
                    final_img = up["secure_url"]
                except: pass

            # --- [تعديل هام هنا] ---
            # تم تغيير بنية الـ HTML لتوافق معايير بلوجر لاستخراج الصور المصغرة
            html = f"<div dir='rtl' style='text-align:justify; font-size:18px; line-height:1.6;'>"
            if final_img: 
                html += f"""
<div class="separator" style="clear: both; text-align: center;">
    <a href="{final_img}" imageanchor="1" style="margin-left: 1em; margin-right: 1em;">
        <img border="0" src="{final_img}" data-original-width="1280" data-original-height="720" style='max-width:100%; border-radius:10px;' />
    </a>
</div>
<br>
"""
            html += f"{new_content.replace(chr(10), '<br>')}</div>"
            # ----------------------

            # النشر والحصول على الرابط
            inserted_post = service.posts().insert(
                blogId=BLOG_ID,
                body={"title": new_title, "content": html, "labels": BLOGGER_LABGER_LABELS if 'BLOGGER_LABELS' in globals() else BLOGGER_LABELS, "isDraft": False}
            ).execute()
            
            post_url = inserted_post.get('url')
            save_to_history(entry.link)
            published_links.append(entry.link)
            published_count += 1
            
            print(f"✅ Published: {new_title}")
            send_telegram("success", f"<b>{new_title}</b>\n\n🔗 رابط الخبر:\n{post_url}")
            
            if published_count < MAX_POSTS_PER_RUN:
                time.sleep(random.randint(WAIT_MIN, WAIT_MAX))

    except Exception as e:
        send_telegram("error", f"خطأ في البوت: {e}")

if __name__ == "__main__":
    main()
