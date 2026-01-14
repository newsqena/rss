import os
import json
import time
import random
import requests
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
# ملاحظة: حتى لو الـ RSS_URL لم يعمل، سنستخدم الدومين الرئيسي للسحب
HOME_URL = "https://betawqit-elnagaa.com/"
BLOGGER_LABELS = [l.strip() for l in os.getenv("BLOGGER_LABELS", "").split(",") if l.strip()]
BOT_NAME = os.getenv("BOT_NAME", "اخبار قنا")
BLOG_ID = "8964557641790201632"
SCOPES = ["https://www.googleapis.com/auth/blogger"]
MAX_POSTS_PER_RUN = 3
HISTORY_FILE = "published_urls.txt" 

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Referer": "https://www.google.com/",
})

cloudinary.config(
    cloud_name="dldxptjuf",
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

# =========================
# وظائف الإدارة
# =========================

def load_history():
    if not os.path.exists(HISTORY_FILE): return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return [line.strip() for line in f.readlines() if line.strip()]
    except: return []

def save_to_history(link):
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(link + "\n")

def clean_for_display(text):
    if not text: return ""
    text = re.sub(r'(اسلام نبيل|بتوقيت النجع|شمالي محافظة قنا|إسلام نبيل)', '', text)
    clean = re.sub(r'[*#\"\'“”«»]', '', text)
    return clean.strip()

def send_telegram(status, message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id: return
    icons = {"success": "✅", "error": "🚨"}
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                      data={"chat_id": chat_id, "text": f"{icons.get(status, 'ℹ️')} <b>{BOT_NAME}</b>\n\n{message}", "parse_mode": "HTML"}, timeout=10)
    except: pass

# =========================
# استخراج الأخبار من الصفحة الرئيسية مباشرة (بديل RSS)
# =========================

def get_latest_articles_from_home():
    articles = []
    try:
        print(f"🌐 Fetching articles directly from homepage: {HOME_URL}")
        r = session.get(HOME_URL, timeout=25)
        soup = BeautifulSoup(r.text, "html.parser")
        
        # البحث عن الروابط داخل عناوين المقالات في الصفحة الرئيسية لـ WordPress
        # غالباً ما تكون داخل h2 أو h3 بكلاس يحتوي على post-title
        headings = soup.find_all(['h2', 'h3'], class_=re.compile(r'post-title|entry-title|title'))
        
        for h in headings:
            a_tag = h.find('a')
            if a_tag and a_tag.get('href'):
                link = a_tag.get('href')
                title = a_tag.get_text(strip=True)
                if link not in [art['link'] for art in articles]:
                    articles.append({'title': title, 'link': link})
            if len(articles) >= 10: break # نأخذ أول 10 أخبار فقط للمقارنة
            
        print(f"🔎 Found {len(articles)} potential articles on homepage.")
    except Exception as e:
        print(f"❌ Error fetching homepage: {e}")
    return articles

def extract_article_content(link):
    try:
        r = session.get(link, timeout=25)
        r.encoding = 'utf-8'
        soup = BeautifulSoup(r.text, "html.parser")
        
        # النص من كلاس entry
        container = soup.find("div", class_="entry") or soup.find("div", class_="entry-content")
        text = container.get_text(" ", strip=True) if container else ""

        # الصورة من كلاس single-post-thumb
        img_url = None
        img_container = soup.find("div", class_="single-post-thumb")
        if img_container:
            img_tag = img_container.find("img")
            if img_tag:
                img_url = img_tag.get("data-src") or img_tag.get("src")
        
        if not img_url:
            og_img = soup.find("meta", property="og:image")
            img_url = og_img.get("content") if og_img else None

        return text, img_url
    except: return None, None

def paraphrase_all(original_title, original_text):
    api_key = os.getenv("OPENAI_API_KEY")
    url = "https://api.openai.com/v1/chat/completions"
    prompt = f"أعد صياغة الخبر التالي بأسلوب صحفي مشوق:\nالعنوان: {original_title}\nالمحتوى: {original_text}\nالمطلوب: ابدأ بالعنوان الجديد مباشرة، قسمه لـ 3 فقرات، واحذف أي إشارة لـ 'بتوقيت النجع'."
    payload = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "temperature": 0.4}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=45)
        res = response.json()['choices'][0]['message']['content'].strip().split('\n')
        return clean_for_display(res[0]), "\n".join(res[1:]).strip()
    except: return None, None

def get_blogger_service():
    creds_json = os.environ.get("BLOGGER_CREDS_JSON")
    creds = Credentials.from_authorized_user_info(json.loads(creds_json), SCOPES)
    if creds.expired and creds.refresh_token: creds.refresh(Request())
    return build("blogger", "v3", credentials=creds)

# =========================
# التشغيل الرئيسي
# =========================

def main():
    print(f"🚀 Starting {BOT_NAME}...")
    try:
        service = get_blogger_service()
        articles = get_latest_articles_from_home()
        history = load_history()

        published_count = 0
        for art in articles:
            if published_count >= MAX_POSTS_PER_RUN: break
            if art['link'] in history: continue

            print(f"🧐 Processing: {art['title']}")
            text, img = extract_article_content(art['link'])
            
            if not text or len(text) < 150: continue

            new_title, new_content = paraphrase_all(art['title'], text)
            if not new_title: continue

            # رفع الصورة لـ Cloudinary
            final_img = img
            if img:
                try:
                    up = cloudinary.uploader.upload(session.get(img).content, folder="news_system")
                    final_img = up["secure_url"]
                except: pass

            html = f"<div dir='rtl' style='text-align:justify; font-size:18px; line-height:1.8;'>"
            if final_img: html += f"<div style='text-align:center'><img src='{final_img}' style='max-width:100%; border-radius:12px;'></div><br>"
            html += f"{new_content.replace(chr(10), '<br>')}</div>"

            post = service.posts().insert(blogId=BLOG_ID, body={"title": new_title, "content": html, "labels": BLOGGER_LABELS, "isDraft": False}).execute()
            
            save_to_history(art['link'])
            published_count += 1
            print(f"✅ Published: {new_title}")
            send_telegram("success", f"<b>{new_title}</b>\n\n🔗 رابط الخبر:\n{post.get('url')}")
            time.sleep(random.randint(30, 60))

    except Exception as e:
        print(f"🚨 Error: {e}")
        send_telegram("error", f"خطأ: {e}")

if __name__ == "__main__":
    main()
