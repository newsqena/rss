import os, json, time, random, requests, feedparser
from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import cloudinary, cloudinary.uploader

# ======================
# ENV
# ======================
RSS_URL = os.getenv("RSS_URL")
BLOG_ID = "8964557641790201632"
LABELS = ["اخبار قنا"]
SCOPES = ["https://www.googleapis.com/auth/blogger"]
MAX_POSTS = 3
HISTORY_FILE = "history.txt"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "ar,en;q=0.8"
}

# ======================
# Cloudinary
# ======================
cloudinary.config(
    cloud_name="dldxptjuf",
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

# ======================
def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    return open(HISTORY_FILE, encoding="utf-8").read().splitlines()

def save_history(link):
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(link + "\n")

# ======================
def load_rss():
    proxy = f"https://textise.org/showtext.aspx?strURL={RSS_URL}"
    r = requests.get(proxy, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return feedparser.parse(r.text)

# ======================
def extract_article(url):
    r = requests.get(url, headers=HEADERS, timeout=25)
    soup = BeautifulSoup(r.text, "html.parser")

    text = " ".join(p.get_text() for p in soup.find_all("p") if len(p.text) > 40)

    img = soup.find("meta", property="og:image")
    img_url = img["content"] if img else None

    return text, img_url

# ======================
def paraphrase(text, title):
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{
            "role": "user",
            "content": f"أعد صياغة الخبر مع الحفاظ على المعنى:\n{title}\n{text}"
        }],
        "temperature": 0.3
    }

    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=45
    )

    content = r.json()["choices"][0]["message"]["content"]
    lines = content.split("\n")
    return lines[0], "\n".join(lines[1:])

# ======================
def blogger():
    creds = Credentials.from_authorized_user_info(
        json.loads(os.getenv("BLOGGER_CREDS_JSON")),
        SCOPES
    )
    if creds.expired:
        creds.refresh(Request())
    return build("blogger", "v3", credentials=creds)

# ======================
def main():
    print("🚀 Bot started")
    history = load_history()
    feed = load_rss()
    service = blogger()
    count = 0

    for e in feed.entries:
        if count >= MAX_POSTS:
            break
        if e.link in history:
            continue

        print("📰", e.title)
        text, img = extract_article(e.link)
        if not text:
            continue

        new_title, new_text = paraphrase(text, e.title)

        html = f"<div dir='rtl'>{new_text.replace(chr(10),'<br>')}</div>"

        service.posts().insert(
            blogId=BLOG_ID,
            body={
                "title": new_title,
                "content": html,
                "labels": LABELS,
                "isDraft": False
            }
        ).execute()

        save_history(e.link)
        count += 1
        time.sleep(random.randint(40, 80))

    print("✅ Done")

if __name__ == "__main__":
    main()
