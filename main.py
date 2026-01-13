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
import cloudinary
import cloudinary.uploader

# =========================
# إعدادات عامة
# =========================
RSS_URL = os.getenv("RSS_URL")
BLOGGER_LABELS = [l.strip() for l in os.getenv("BLOGGER_LABELS", "").split(",") if l.strip()]
BOT_NAME = os.getenv("BOT_NAME", "Unknown Bot")
SCRAPE_MODE = os.getenv("SCRAPE_MODE", "default")
BLOG_ID = "8964557641790201632"
SCOPES = ["https://www.googleapis.com/auth/blogger"]
MAX_POSTS_PER_RUN = 3

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

def paraphrase_with_openai(text):
    """استخدام طلب HTTP مباشر لـ OpenAI لتجنب مشاكل المكتبة"""
    api_key = os.getenv("OPENAI_API_KEY")
    url = "https://api.openai.com/v1/chat/completions"
    
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "أنت صحفي محترف. أعد صياغة الخبر بأسلوب جذاب وعنوان قوي."},
            {"role": "user", "content": f"أعد صياغة الخبر التالي وضع له عنواناً في السطر الأول:\n\n{text}"}
        ],
        "temperature": 0.3
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    try:
        # محاولة الطلب بمهلة 40 ثانية
        response = requests.post(url, headers=headers, json=payload, timeout=40)
        response.raise_for_status()
        result = response.json()
        full_text = result['choices'][0]['message']['content'].strip()
        
        # تقسيم العنوان عن المحتوى (يفترض أن العنوان أول سطر)
        lines = full_text.split('\n')
        title = lines[0].replace('#', '').strip()
        body = "\n".join(lines[1:]).strip()
        
        return title, body
    except Exception as e:
        print(f"⚠️ OpenAI Direct Error: {e}")
        return None, None

# =========================
# التشغيل الرئيسي
# =========================

def main():
    print(f"🚀 Starting {BOT_NAME}...")
    try:
        service = get_blogger_service()
        response = requests.get(RSS_URL, headers=HEADERS, timeout=20)
        feed = feedparser.parse(response.content)
        
        published_count = 0
        for entry in feed.entries:
            if published_count >= MAX_POSTS_PER_RUN: break
            
            print(f"🧐 Checking: {entry.title}")
            
            # فحص التكرار
            posts = service.posts().list(blogId=BLOG_ID, maxResults=8).execute().get("items", [])
            if any(p["title"].strip() == entry.title.strip() for p in posts):
                print("⏭️ Already published.")
                continue

            text, img = extract_article(entry.link)
            if not text or len(text) < 150: continue

            # الصياغة باستخدام الطريقة المباشرة الجديدة
            new_title, new_content = paraphrase_with_openai(text)
            if not new_title or len(new_content) < 50: 
                print("❌ Failed to paraphrase.")
                continue

            # رفع الصورة
            final_img = img
            if img:
                try: 
                    up = cloudinary.uploader.upload(img, folder="news")
                    final_img = up["secure_url"]
                except: pass

            html = f"<div dir='rtl' style='text-align:justify; font-size:18px; line-height:1.6;'>"
            if final_img: html += f"<div style='text-align:center'><img src='{final_img}' style='max-width:100%; border-radius:10px;'></div><br>"
            html += f"{new_content.replace(chr(10), '<br>')}</div>"

            service.posts().insert(
                blogId=BLOG_ID,
                body={"title": new_title, "content": html, "labels": BLOGGER_LABELS, "isDraft": False}
            ).execute()
            
            published_count += 1
            print(f"✅ Published: {new_title}")
            send_telegram("success", f"تم نشر خبر جديد:\n<b>{new_title}</b>")
            time.sleep(5)

        if published_count == 0:
            print("🏁 No new articles found.")

    except Exception as e:
        print(f"🚨 Error: {e}")
        send_telegram("error", f"خطأ في البوت: {e}")

if __name__ == "__main__":
    main()
