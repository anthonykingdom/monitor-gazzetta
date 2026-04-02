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

DAYS_BACK = 20
MAX_ISSUES = 20
MAX_ACTS_PER_ISSUE = None
MAX_RESULTS_IN_MESSAGE = 200

TELEGRAM_MAX_TEXT_LENGTH = 3900

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


def log(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")


def send_telegram_message_html(html_text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    log(f"Invio messaggio Telegram ({len(html_text)} caratteri)...")
    response = requests.post(url, data=payload, timeout=60)
    log(f"Telegram status_code: {response.status_code}")
    log(f"Telegram response: {response.text}")
    response.raise_for_status()


def get_html(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0"}
    log(f"Download HTML: {url}")
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
    log(f"Ricerca Gazzette degli ultimi {days_back} giorni...")
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
    selected = issues[:MAX_ISSUES]
    log(f"Gazzette rilevanti trovate: {len(selected)}")
    return selected


def extract_acts_from_issue(issue):
    log(f"Estrazione atti da: {issue['issue_label']}")
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

    if MAX_ACTS_PER_ISSUE is None:
        selected = acts
    else:
        selected = acts[:MAX_ACTS_PER_ISSUE]

    log(
        f"{issue['issue_label']}: atti totali trovati = {len(acts)}, "
        f"atti analizzati = {len(selected)}"
    )
    return selected


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


def analyze():
    log("Avvio analisi...")
    results = []

    issues = get_recent_issues()
    log(f"Gazzette da analizzare: {len(issues)}")

    for issue in issues:
        acts = extract_acts_from_issue(issue)

        for act_index, act in enumerate(acts, start=1):
            try:
                log(f"Analizzo atto {act_index}: {act['title']}")
                menu_url = get_menu_url_from_detail(act["detail_url"])
                if not menu_url:
                    log("Menu 'atto completo' non trovato.")
                    continue

                for article in extract_article_urls(menu_url):
                    text = extract_article_text(article["url"])
                    found = find_keywords_in_text(text)

                    if found:
                        results.append(
                            {
                                "issue_date": act["issue_date"],
                                "issue_label": act["issue_label"],
                                "title": act["title"],
                                "article_label": article["article_label"],
                                "url": article["url"],
                                "keywords": found,
                            }
                        )
                        log(
                            f"Match trovato al n. atto {act_index}: "
                            f"{act['title']} | Articolo: {article['article_label']} | "
                            f"Keyword: {', '.join(found)}"
                        )
                        break

            except Exception as e:
                log(f"Errore su {act['detail_url']}: {e}")

    log(f"Analisi completata. Risultati trovati: {len(results)}")
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

    log(f"Deduplica completata. Risultati unici: {len(unique)}")
    return unique


def format_check_timestamp() -> str:
    return datetime.now().strftime("%d-%m-%Y %H:%M")


def build_header(results_count: int) -> list[str]:
    checked_at = format_check_timestamp()

    if results_count == 0:
        return [
            f"<b>📭 Nessun atto rilevante trovato negli ultimi {DAYS_BACK} giorni</b>",
            f"<i>Controllo eseguito: {escape(checked_at)}</i>",
            "",
            "Controllo eseguito correttamente.",
        ]

    return [
        f"<b>🚨 Atti rilevanti trovati negli ultimi {DAYS_BACK} giorni: {results_count}</b>",
        f"<i>Controllo eseguito: {escape(checked_at)}</i>",
        "",
    ]


def build_result_block(index: int, item: dict) -> list[str]:
    return [
        f"{index}. <a href=\"{escape(item['url'])}\">{escape(item['title'])}</a>",
        f"   <i>{escape(format_issue_label(item['issue_label']))}</i>",
        f"   <i>Articolo:</i> {escape(clean_label(item['article_label']))}",
        f"   <i>Match:</i> {escape(', '.join(item['keywords']))}",
        "",
    ]


def build_message(results):
    results = sorted(results, key=lambda x: x["issue_date"], reverse=True)

    if not results:
        return "\n".join(build_header(0))

    header = build_header(len(results))
    parts = header[:]
    included_count = 0

    for i, item in enumerate(results[:MAX_RESULTS_IN_MESSAGE], start=1):
        block = build_result_block(i, item)
        candidate = "\n".join(parts + block)

        if len(candidate) > TELEGRAM_MAX_TEXT_LENGTH:
            break

        parts.extend(block)
        included_count += 1

    total_results = min(len(results), MAX_RESULTS_IN_MESSAGE)

    if included_count < total_results:
        omitted = total_results - included_count
        parts.append(f"<i>... altri {omitted} risultati non mostrati per limiti di lunghezza.</i>")

    message = "\n".join(parts)

    if len(message) > TELEGRAM_MAX_TEXT_LENGTH:
        log("Messaggio ancora troppo lungo dopo la costruzione controllata. Applico fallback.")
        message = (
            "\n".join(header)
            + "\n"
            + "<i>Messaggio troppo lungo: risultati trovati ma non interamente visualizzabili.</i>"
        )

    return message


def main():
    try:
        log("=== Avvio script Monitor Gazzetta ===")
        results = analyze()
        results = deduplicate(results)
        message = build_message(results)

        log(f"Lunghezza messaggio finale: {len(message)} caratteri")
        send_telegram_message_html(message)

        log("✅ Controllo completato con successo.")
    except Exception as e:
        log(f"❌ Errore fatale: {e}")
        raise


if __name__ == "__main__":
    main()
