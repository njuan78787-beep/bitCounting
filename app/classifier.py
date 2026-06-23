"""Clasificación de dominios en categorías con listas semilla integradas."""
import threading

CATEGORIES = {
    "social":     {"label": "Redes sociales",      "color": "#7c5cff", "icon": "users"},
    "video":      {"label": "Videos y streaming",  "color": "#ff5c8a", "icon": "play"},
    "gaming":     {"label": "Juegos",               "color": "#1fb6a6", "icon": "gamepad-2"},
    "education":  {"label": "Educación",            "color": "#3b82f6", "icon": "graduation-cap"},
    "adult":      {"label": "Contenido adulto",     "color": "#e11d48", "icon": "shield-alert"},
    "gambling":   {"label": "Apuestas",             "color": "#d97706", "icon": "dices"},
    "shopping":   {"label": "Compras",              "color": "#0891b2", "icon": "shopping-bag"},
    "messaging":  {"label": "Mensajería",           "color": "#16a34a", "icon": "message-circle"},
    "news":       {"label": "Noticias",             "color": "#64748b", "icon": "newspaper"},
    "evasion":    {"label": "Intentos de evadir el control", "color": "#9333ea", "icon": "shield-off"},
    "other":      {"label": "Otros",                "color": "#94a3b8", "icon": "globe"},
}

SEED = {
    "social": [
        "facebook.com", "fbcdn.net", "instagram.com", "cdninstagram.com",
        "tiktok.com", "tiktokcdn.com", "twitter.com", "x.com", "twimg.com",
        "snapchat.com", "reddit.com", "redditmedia.com", "pinterest.com",
        "tumblr.com", "threads.net", "linkedin.com", "bsky.app",
    ],
    "video": [
        "youtube.com", "youtu.be", "ytimg.com", "googlevideo.com", "ggpht.com",
        "netflix.com", "nflxvideo.net", "nflximg.net", "twitch.tv", "ttvnw.net",
        "primevideo.com", "disneyplus.com", "hulu.com", "vimeo.com",
        "dailymotion.com", "hbomax.com", "max.com", "crunchyroll.com",
    ],
    "gaming": [
        "roblox.com", "rbxcdn.com", "minecraft.net", "mojang.com",
        "steampowered.com", "steamcommunity.com", "epicgames.com",
        "fortnite.com", "ea.com", "riotgames.com", "leagueoflegends.com",
        "battle.net", "blizzard.com", "supercell.com", "miniclip.com",
        "playstation.com", "xbox.com", "nintendo.com", "discord.com", "discord.gg",
    ],
    "education": [
        "wikipedia.org", "wikimedia.org", "khanacademy.org", "duolingo.com",
        "coursera.org", "edx.org", "udemy.com", "brainpop.com", "quizlet.com",
        "scratch.mit.edu", "code.org", "classroom.google.com", "edmodo.com",
        "ck12.org", "nationalgeographic.com", "britannica.com",
    ],
    "adult": [
        "pornhub.com", "xvideos.com", "xnxx.com", "xhamster.com", "redtube.com",
        "youporn.com", "onlyfans.com", "chaturbate.com", "adult-empire.com",
        "brazzers.com", "spankbang.com", "rule34.xxx", "nhentai.net",
    ],
    "gambling": [
        "bet365.com", "pokerstars.com", "888casino.com", "betway.com",
        "draftkings.com", "fanduel.com", "williamhill.com", "bovada.lv",
        "stake.com", "codere.com", "caliente.mx", "betfair.com",
    ],
    "shopping": [
        "amazon.com", "amazon.com.mx", "mercadolibre.com", "ebay.com",
        "aliexpress.com", "shein.com", "temu.com", "walmart.com",
        "etsy.com", "wish.com", "liverpool.com.mx",
    ],
    "messaging": [
        "whatsapp.com", "whatsapp.net", "telegram.org", "t.me", "messenger.com",
        "signal.org", "kik.com", "wechat.com", "line.me", "viber.com",
    ],
    "news": [
        "cnn.com", "bbc.com", "bbc.co.uk", "nytimes.com", "elpais.com",
        "eluniversal.com.mx", "milenio.com", "infobae.com", "univision.com",
        "reuters.com", "marca.com",
    ],
    "evasion": [
        "nordvpn.com", "expressvpn.com", "protonvpn.com", "surfshark.com",
        "tunnelbear.com", "hotspotshield.com", "windscribe.com", "hide.me",
        "torproject.org", "psiphon.ca", "ultrasurf.us", "cloudflare-dns.com",
        "dns.google", "doh.opendns.com", "1dot1dot1dot1.cloudflare-dns.com",
    ],
}

_index = {}
_lock = threading.Lock()


def _build_index():
    with _lock:
        _index.clear()
        for cat, domains in SEED.items():
            for d in domains:
                _index[d.lower().strip(".")] = cat


_build_index()


def classify(domain: str) -> str:
    domain = (domain or "").lower().strip(".")
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        cat = _index.get(candidate)
        if cat:
            return cat
    return "other"


def add_domains(category: str, domains):
    if category not in CATEGORIES:
        return
    with _lock:
        for d in domains:
            d = d.lower().strip(".")
            if d and d not in _index:
                _index[d] = category


def set_manual(domain: str, category: str):
    with _lock:
        _index[domain.lower().strip(".")] = category


def stats():
    counts = {}
    for cat in _index.values():
        counts[cat] = counts.get(cat, 0) + 1
    return {"total_domains": len(_index), "by_category": counts}
