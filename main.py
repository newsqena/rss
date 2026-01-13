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
HISTORY_FILE = "published_urls.txt" # ملف حفظ الروابط

WAIT_MIN = 40
WAIT_MAX = 70

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

# =========================
# وظائف إدارة الملف النصي
# =========================

def load_history():
    """تحميل الروابط المنشورة مسبقاً من الملف"""
    if not os.path.exists(HISTORY_FILE):
        return []
    with open(HISTORY_FILE, "r") as f:
        return [line.strip() for line in f.readlines()]

def save_to_history(link):
    """إضافة رابط جديد للملف النصي"""
    with open(HISTORY_FILE, "a") as f:
        f.write(link + "\n")

# =========================
# وظائف المساعدة الأخرى
# =========================

def clean_for_display(text):
    if not text: return ""
    clean = re.sub(r'[*#\"\'“”«»]', '', text)
    return clean.strip()

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
    prompt = f"أنت صحفي محترف. أعد صياغة الخبر التالي.\n\nالعنوان الأصلي: {original_title}\n\nالمحتوى الأصلي: {original_text}\n\nالمطلوب:\n1. عنوان جديد جذاب.\n2. محتوى احترافي.\nاجعل العنوان في السطر الأول."
    payload = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "temperature": 0.4}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=45)
        full_result = response.json()['choices'][0]['message']['content'].strip()
        lines = full_result.split('\n')
        return clean_for_display(lines[0]), "\n".join(lines[1:]).strip()
    except: return None, None

def main():
    print(f"🚀 Starting {BOT_NAME}...")
    try:
        service = get_blogger_service()
        response = requests.get(RSS_URL, headers=HEADERS, timeout=20)
        feed = feedparser.parse(response.content)
        
        # تحميل الروابط القديمة من ملف الـ txt
        published_links = load_history()
        print(f"📁 Loaded {len(published_links)} links from history file.")

        published_count = 0
        for entry in feed.entries:
            if published_count >= MAX_POSTS_PER_RUN: break
            
            # الفحص بالرابط من داخل الملف النصي (أدق وأضمن طريقة)
            if entry.link in published_links:
                print(f"⏭️ Already published (In History File): {entry.title}")
                continue

            print(f"🧐 Processing: {entry.title}")
            text, img = extract_article(entry.link)
            if not text or len(text) < 150: continue

            new_title, new_content = paraphrase_all(entry.title, text)
            if not new_title: continue

            html = f"<div dir='rtl' style='text-align:justify; font-size:18px; line-height:1.6;'>"
            if img: html += f"<div style='text-align:center'><img src='{img}' style='max-width:100%; border-radius:10px;'></div><br>"
            html += f"{new_content.replace(chr(10), '<br>')}</div>"

            # النشر في بلوجر
            service.posts().insert(blogId=BLOG_ID, body={"title": new_title, "content": html, "labels": BLOGGER_LABELS, "isDraft": False}).execute()
            
            # حفظ الرابط في الملف النصي فوراً
            save_to_history(entry.link)
            published_links.append(entry.link)
            
            published_count += 1
            print(f"✅ Published and saved to history: {new_title}")
            time.sleep(random.randint(WAIT_MIN, WAIT_MAX))

    except Exception as e:
        print(f"🚨 Error: {e}")

if __name__ == "__main__":
    main()
