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

SECTION_1_KEYWORDS = [
    "fondazioni lirico-sinfoniche",
    "fondazione lirico",
    "teatro alla scala",
]

SECTION_2_KEYWORDS = [
    "spettacolo dal vivo",
    "fondo nazionale per lo spettacolo dal vivo",
    "fnsv",
]

SECTION_3_KEYWORDS = [
    "ministero della cultura",
]

KEYWORDS = SECTION_1_KEYWORDS + SECTION_2_KEYWORDS + SECTION_3_KEYWORDS

MONTHS_IT = {
    "01": "gen",
    "02": "feb",
    "03": "mar",
    "04": "apr",
    "05": "mag",
    "06": "giu",
    "07": "lug",
    "08": "ago",
    "09": "set",
    "10": "ott",
    "11": "nov",
    "12": "dic",
}


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


def compact_date_italian(date_str: str) -> str:
    match = re.match(r"(\d{2})-(\d{2})-(\d{4})$", date_str)
    if not match:
        return date_str
    day, month, year = match.groups()
    month_it = MONTHS_IT.get(month, month)
    return f"{day}{month_it}{year}"


def format_issue_label(issue_label: str) -> str:
    match = re.search(
        r"n°\s*(\d+)\s+del\s+(\d{2}-\d{2}-\d{4})",
        issue_label,
        re.IGNORECASE,
    )
    if match:
        issue_number = match.group(1)
        compact_date = compact_date_italian(match.group(2))
        return f"GU {issue_number} {compact_date}"
    return issue_label


def get_recent_issues(days_back: int = DAYS_BACK):
    html = get_html(LAST_30_DAYS_URL)
    soup = BeautifulSoup(html, "html.parser")

    cutoff = date.today() - timedelta(days=days_back)
    issues = []
    seen = set()

    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        href = a["href"]

        if "n°" not in text.lower():
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

    if MAX_ACTS_PER_ISSUE is None:
        return acts
    return acts[:MAX_ACTS_PER_ISSUE]


def get_menu_url_from_detail(detail_url: str):
    html = get_html(detail_url)
    soup = BeautifulSoup(html, "html.parser")

    for a in soup.find_all("a", href=True):
        if "atto completo" in a.get_text(" ", strip=True).lower():
            return urljoin(BASE_URL, a["href"])
    return None


def extract_article_urls(menu_url: str):
    html = get_html(menu_url)
    soup = BeautifulSoup(html, "html.parser")

    urls = []
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

        urls.append(
            {
                "article_label": normalize_spaces(text),
                "url": full_url,
            }
        )

    return urls


def extract_article_text(article_url: str) -> str:
    html = get_html(article_url)
    soup = BeautifulSoup(html, "html.parser")
    return normalize_spaces(soup.get_text(" ", strip=True))


def find_keywords_in_text(text: str):
    lower = text.lower()
    return [kw for kw in KEYWORDS if kw.lower() in lower]


def classify_section(found_keywords):
    found_lower = {k.lower() for k in found_keywords}

    if any(k.lower() in found_lower for k in SECTION_2_KEYWORDS):
        return 2
    if any(k.lower() in found_lower for k in SECTION_1_KEYWORDS):
        return 1
    if any(k.lower() in found_lower for k in SECTION_3_KEYWORDS):
        return 3
    return None


def clean_label(text: str) -> str:
    text = normalize_spaces(text)
    text = re.sub(r"^articolo\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^art\.\s*", "", text, flags=re.IGNORECASE)
    return text.strip(" -–—:;")


def analyze():
    results = []
    issues = get_recent_issues()

    for issue in issues:
        for act in extract_acts_from_issue(issue):
            try:
                menu_url = get_menu_url_from_detail(act["detail_url"])
                if not menu_url:
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
                                "section": classify_section(found),
                            }
                        )
                        break

            except Exception as e:
                log(f"Errore su {act['detail_url']}: {e}")
                continue

    return results


def deduplicate(results):
    seen = set()
    output = []

    for item in results:
        key = (item["title"], item["article_label"], item["url"])
        if key in seen:
            continue
        seen.add(key)
        output.append(item)

    return output


def build_header(results_count: int):
    ts = datetime.now().strftime("%d-%m %H:%M")

    if results_count == 0:
        return [
            "<b>📭 Nessun risultato</b>",
            f"<i>{escape(ts)}</i>",
            "",
        ]

    return [
        f"<b>🚨 {results_count} risultati</b>",
        f"<i>{escape(ts)}</i>",
        "",
    ]


def build_section_title(section_number: int) -> str:
    return {
        1: "🎭 FLS",
        2: "🎟️ FNSV",
        3: "🏛️ MIC",
    }[section_number]


def build_result_block(index: int, item: dict):
    title_line = f'{index}. <a href="{escape(item["url"])}">{escape(item["title"])}</a>'

    issue_info = escape(format_issue_label(item["issue_label"]))
    article_info = escape(clean_label(item["article_label"]))

    if item["section"] == 3:
        detail_line = f"   {issue_info} | Art:{article_info}"
    else:
        keywords_info = escape(",".join(item["keywords"]))
        detail_line = f"   {issue_info} | Art:{article_info} | K:{keywords_info}"

    return [title_line, detail_line]


def build_message(results):
    results = sorted(results, key=lambda x: x["issue_date"], reverse=True)

    if not results:
        return "\n".join(build_header(0))

    sections = {
        1: [r for r in results if r["section"] == 1],
        2: [r for r in results if r["section"] == 2],
        3: [r for r in results if r["section"] == 3],
    }

    parts = build_header(len(results))
    included_count = 0
    progressive_index = 1
    max_results = min(len(results), MAX_RESULTS_IN_MESSAGE)

    for section_number in [1, 2, 3]:
        section_items = sections[section_number]
        if not section_items:
            continue

        section_header = [f"<b>{escape(build_section_title(section_number))}</b>"]

        candidate = "\n".join(parts + section_header)
        if len(candidate) > TELEGRAM_MAX_TEXT_LENGTH:
            break

        parts.extend(section_header)

        for item in section_items:
            if included_count >= max_results:
                break

            block = build_result_block(progressive_index, item)
            candidate = "\n".join(parts + block)

            if len(candidate) > TELEGRAM_MAX_TEXT_LENGTH:
                break

            parts.extend(block)
            included_count += 1
            progressive_index += 1

        if included_count >= max_results:
            break

    if included_count < max_results:
        omitted = max_results - included_count
        parts.append(f"<i>... altri {omitted} risultati non mostrati.</i>")

    message = "\n".join(parts)

    if len(message) > TELEGRAM_MAX_TEXT_LENGTH:
        log("Messaggio troppo lungo dopo build_message, applico fallback.")
        message = "\n".join(
            build_header(len(results))
            + ["<i>Risultati trovati ma non visualizzabili interamente.</i>"]
        )

    return message


def main():
    try:
        log("=== Avvio script Monitor Gazzetta ===")
        results = analyze()
        results = deduplicate(results)
        message = build_message(results)
        send_telegram_message_html(message)
        log("✅ Controllo completato con successo.")
    except Exception as e:
        log(f"❌ Errore fatale: {e}")
        raise


if __name__ == "__main__":
    main()
