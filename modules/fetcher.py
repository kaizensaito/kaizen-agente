import requests

def fetch_url_content(url, max_chars=5000):
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.text[:max_chars]
    except Exception as e:
        return f"❌ Erro ao buscar {url}: {e}"
