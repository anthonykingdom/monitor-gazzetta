import os
import re
from datetime import date, datetime, timedelta
from html import escape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

BASE_URL = "https://www.gazzettaufficiale.it"
LAST_30_DAYS_URL = f"{BASE_URL}/30giorni/serie_generale"

DAYS_BACK = 3
MAX_ISSUES = 3
MAX_ACTS_PER_ISSUE = 12
MAX_RESULTS_IN_MESSAGE = 50

KEYWORDS = [
    "fondazioni lirico-sinfoniche",
    "fondazione lirico",
    "lirico-sinfoniche",
    "teatro alla scala",
    "spettacolo dal vivo",
    "fondo nazionale per lo spettacolo dal vivo",
    "fnsv",
    "ministero della cultura",
]


def send_telegram_message_html(html_text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    response = requests.post(url, data=payload, timeout=60)
    response.raise_for_status()


def get_html(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    return response.text


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_issue_date(text: str):
    match = re.search(r"(\d{2}-\d{2}-\d{4})", text)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%d-%m-%Y").date()


def format_issue_label(issue_label: str) -> str:
    match = re.search(
        r"n°\s*(\d+)\s+del\s+(\d{2}-\d{2}-\d{4})",
        issue_label,
        re.IGNORECASE,
    )
    if match:
        return f"G.U. {match.group(1)} — {match.group(2)}"
    return f"G.U. {issue_label}"


def get_recent_issues(days_back: int = DAYS_BACK):
    html = get_html(LAST_30_DAYS_URL)
    soup = BeautifulSoup(html, "html.parser")

    cutoff = date.today() - timedelta(days=days_back)
    issues = []
    seen = set()

    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        href = a["href"]

        if "n°" not in text.lower() or "del" not in text.lower():
            continue

        issue_date = parse_issue_date(text)
        if not issue_date or issue_date < cutoff:
            continue

        full_url = urljoin(BASE_URL, href)
        key = (text, full_url)
        if key in seen:
            continue
        seen.add(key)

        issues.append(
            {
                "issue_label": text,
                "issue_date": issue_date,
                "url": full_url,
            }
        )

    issues.sort(key=lambda x: x["issue_date"], reverse=True)
    return issues[:MAX_ISSUES]


def extract_acts_from_issue(issue):
    html = get_html(issue["url"])
    soup = BeautifulSoup(html, "html.parser")

    acts = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        title = a.get_text(" ", strip=True)

        if not href or not title:
            continue

        full_url = urljoin(BASE_URL, href)

        if "caricaDettaglioAtto" not in full_url:
            continue

        if full_url in seen:
            continue
        seen.add(full_url)

        acts.append(
            {
                "title": normalize_spaces(title),
                "detail_url": full_url,
                "issue_label": issue["issue_label"],
                "issue_date": issue["issue_date"],
            }
        )

    return acts[:MAX_ACTS_PER_ISSUE]


def get_menu_url_from_detail(detail_url: str):
    html = get_html(detail_url)
    soup = BeautifulSoup(html, "html.parser")

    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True).lower()
        href = a["href"]
        if "atto completo" in text:
            return urljoin(BASE_URL, href)

    return None


def extract_article_urls(menu_url: str):
    html = get_html(menu_url)
    soup = BeautifulSoup(html, "html.parser")

    article_urls = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(" ", strip=True)

        if not href or not text:
            continue

        full_url = urljoin(BASE_URL, href)

        if "caricaArticolo" not in full_url:
            continue

        if full_url in seen:
            continue
        seen.add(full_url)

        article_urls.append(
            {
                "article_label": normalize_spaces(text),
                "url": full_url,
            }
        )

    return article_urls


def extract_article_text(article_url: str) -> str:
    html = get_html(article_url)
    soup = BeautifulSoup(html, "html.parser")
    return normalize_spaces(soup.get_text(" ", strip=True))


def find_keywords_in_text(text: str):
    lower_text = text.lower()
    return [kw for kw in KEYWORDS if kw.lower() in lower_text]


def clean_label(text: str) -> str:
    text = normalize_spaces(text)
    text = re.sub(r"^articolo\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^art\.\s*", "Art. ", text, flags=re.IGNORECASE)
    return text.strip(" -–—:;")


def extract_note_from_text(text: str) -> str:
    sentences = re.split(r"[.;:\n]", text)

    for s in sentences:
        s = normalize_spaces(s)

        if not s:
            continue
        if len(s) < 20:
            continue
        if s.lower().startswith("il presente"):
            continue
        if s.lower().startswith("vista"):
            continue
        if s.lower().startswith("considerato"):
            continue

        return s

    return text


def analyze():
    results = []

    for issue in get_recent_issues():
        acts = extract_acts_from_issue(issue)

        for act in acts:
            try:
                menu_url = get_menu_url_from_detail(act["detail_url"])
                if not menu_url:
                    continue

                for article in extract_article_urls(menu_url):
                    text = extract_article_text(article["url"])
                    found = find_keywords_in_text(text)

                    if found:
                        note = extract_note_from_text(text)

                        results.append(
                            {
                                "issue_date": act["issue_date"],
                                "issue_label": act["issue_label"],
                                "title": act["title"],
                                "article_label": article["article_label"],
                                "url": article["url"],
                                "keywords": found,
                                "note": note,
                            }
                        )
                        break

            except Exception as e:
                print(f"Errore su {act['detail_url']}: {e}")

    return results


def deduplicate(results):
    unique = []
    seen = set()

    for item in results:
        key = (item["title"], item["article_label"], item["url"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    return unique


def build_message(results):
    if not results:
        return (
            f"<b>📭 Nessun atto rilevante trovato negli ultimi {DAYS_BACK} giorni</b>\n\n"
            "Controllo eseguito correttamente."
        )

    results.sort(key=lambda x: x["issue_date"], reverse=True)

    parts = [f"<b>🚨 Atti rilevanti trovati negli ultimi {DAYS_BACK} giorni</b>\n"]

    for i, item in enumerate(results[:MAX_RESULTS_IN_MESSAGE], start=1):
        parts.append(f"{i}. <a href=\"{escape(item['url'])}\">{escape(item['title'])}</a>")
        parts.append(f"   <i>{escape(format_issue_label(item['issue_label']))}</i>")
        parts.append(f"   <i>Articolo:</i> {escape(clean_label(item['article_label']))}")
        parts.append(f"   <i>Match:</i> {escape(', '.join(item['keywords']))}")
        parts.append(f"   <b>{escape(item['note'])}</b>\n")

    return "\n".join(parts)


def main():
    results = analyze()
    results = deduplicate(results)
    message = build_message(results)
    send_telegram_message_html(message)
    print("Controllo completato.")


if __name__ == "__main__":
    main()
