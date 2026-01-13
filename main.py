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
# وظائف المساعدة
# =========================

def clean_text(text):
    if not text: return ""
    clean = re.sub(r'[*#\"\'“”«»]', '', text)
    return clean.strip()

def send_telegram(status, message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id: return
    icons = {"success": "✅", "error": "🚨", "info": "ℹ️"}
    text = f"{icons.get(status, 'ℹ️')} <b>{BOT_NAME}</b>\n\n{message}"
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                      data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
    except: pass

def get_blogger_service():
    creds_json = os.environ.get("BLOGGER_CREDS_JSON")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_authorized_user_info(creds_dict, SCOPES)
    if creds.expired and creds.refresh_token: creds.refresh(Request())
    return build("blogger", "v3", credentials=creds)

def extract_article(link):
    try:
        r = requests.get(link, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        
        container = soup.find("div", class_="entry") or \
                    soup.find("div", class_="article-content") or \
                    soup.find("div", id="article-body") or \
                    soup.find("article") or \
                    soup.find(class_="paragraph-list")
        
        if not container:
            paragraphs = soup.find_all("p")
            text = " ".join([p.get_text() for p in paragraphs if len(p.get_text()) > 60])
        else:
            for tag in container.find_all(["script", "style", "iframe", "aside", "ins"]): tag.decompose()
            text = container.get_text(" ", strip=True)
        
        img_url = None
        img_tag = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"})
        if img_tag: img_url = img_tag.get("content")
            
        return text, img_url
    except: return None, None

def paraphrase_all(original_title, original_text):
    api_key = os.getenv("OPENAI_API_KEY")
    url = "https://api.openai.com/v1/chat/completions"
    
    prompt = f"أنت صحفي محترف. أعد صياغة الخبر التالي.\n\nالعنوان الأصلي: {original_title}\n\nالمحتوى الأصلي: {original_text}\n\nالمطلوب:\n1. عنوان جديد جذاب وقوي بدون رموز.\n2. محتوى الخبر بصياغة احترافية.\n\nاجعل العنوان في السطر الأول، وباقي الخبر في الأسفل."
    
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4
    }
    
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=45)
        result = response.json()
        full_result = result['choices'][0]['message']['content'].strip()
        lines = full_result.split('\n')
        new_title = clean_text(lines[0])
        new_body = "\n".join(lines[1:]).strip()
        return new_title, new_body
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
        
        # سحب آخر 15 مقال تم نشرهم فعلياً في بلوجر للمقارنة
        print("🔍 Fetching latest published posts to prevent duplicates...")
        existing_posts = service.posts().list(blogId=BLOG_ID, maxResults=15, fetchBodies=False).execute().get("items", [])
        existing_titles = [p['title'].strip() for p in existing_posts]

        published_count = 0
        for entry in feed.entries:
            if published_count >= MAX_POSTS_PER_RUN: break
            
            # تنظيف عنوان الخبر الحالي للفحص
            current_rss_title = entry.title.strip()
            print(f"🧐 Checking: {current_rss_title}")
            
            # القاعدة الذهبية: إذا كان العنوان موجود في قائمة العناوين المنشورة، تخطاه فوراً
            if current_rss_title in existing_titles:
                print(f"⏭️ Skipping: '{current_rss_title}' is already on your blog.")
                continue

            # استخراج النص والصورة
            text, img = extract_article(entry.link)
            if not text or len(text) < 150: 
                print("⚠️ Skipping: No valid content found.")
                continue

            # إعادة الصياغة
            new_title, new_content = paraphrase_all(current_rss_title, text)
            if not new_title or not new_content: continue

            # رفع الصورة
            final_img = img
            if img:
                try: 
                    up = cloudinary.uploader.upload(img, folder="news")
                    final_img = up["secure_url"]
                except: pass

            # بناء الـ HTML
            html = f"<div dir='rtl' style='text-align:justify; font-size:18px; line-height:1.6;'>"
            if final_img: 
                html += f"<div style='text-align:center'><img src='{final_img}' style='max-width:100%; border-radius:10px;'></div><br>"
            html += f"{new_content.replace(chr(10), '<br>')}</div>"

            # النشر
            service.posts().insert(
                blogId=BLOG_ID,
                body={"title": new_title, "content": html, "labels": BLOGGER_LABELS, "isDraft": False}
            ).execute()
            
            # إضافة العنوان الجديد للقائمة لضمان عدم تكراره في نفس الدورة
            existing_titles.append(new_title)
            
            published_count += 1
            print(f"✅ Published: {new_title}")
            send_telegram("success", f"تم نشر خبر جديد:\n<b>{new_title}</b>")
            
            if published_count < MAX_POSTS_PER_RUN:
                wait_time = random.randint(WAIT_MIN, WAIT_MAX)
                print(f"😴 Sleeping for {wait_time}s...")
                time.sleep(wait_time)

        if published_count == 0:
            print("🏁 No new news found in this run.")

    except Exception as e:
        print(f"🚨 Error: {e}")
        send_telegram("error", f"خطأ في البوت: {e}")

if __name__ == "__main__":
    main()
