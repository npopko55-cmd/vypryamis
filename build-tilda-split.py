#!/usr/bin/env python3
"""
Сборка блоков T123 для стартовой страницы «Выпрямись».

Tilda T123 — лимит ~30 000 символов на блок. Кириллица в T123 часто рендерится
как mojibake, поэтому вся не-ASCII кодируется в HTML-сущности &#NNNN; (раздувает
примерно в 1,85 раза). CSS и JS подключаются ссылками на GitHub Pages, ассеты
абсолютизируются туда же.

Порядок вставки на странице обязателен: блок 1 подключает стили, последний —
скрипт. Скрипт держит аккордеон вопросов и ПРОБРОС UTM-МЕТОК на ссылки
club.dimafivex.ru — без него метки до интенсивов и клуба не доедут.
"""
import re
from pathlib import Path

BASE = Path(__file__).parent
CDN = "https://npopko55-cmd.github.io/vypryamis"
VER = "vyp-G"

html = (BASE / "index.html").read_text(encoding="utf-8")

# подключение шрифтов забираем из index.html, чтобы строки не разъехались
_m = re.search(r'<link[^>]+fonts\.googleapis\.com/css2[^>]*>', html)
if not _m:
    raise SystemExit("В index.html не найдено подключение Google Fonts")
FONT_LINK = _m.group(0)

body = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL).group(1)

# 1. комментарии прочь, ассеты -> абсолютные адреса
body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
body = re.sub(r'(href|src)="(assets/[^"]+)"', lambda m: f'{m.group(1)}="{CDN}/{m.group(2)}"', body)

def _abs_srcset(m):
    attr, val = m.group(1), m.group(2)
    parts = []
    for item in val.split(","):
        item = item.strip()
        if not item:
            continue
        bits = item.split(None, 1)
        url = bits[0]
        rest = (" " + bits[1]) if len(bits) > 1 else ""
        if url.startswith("assets/"):
            url = f"{CDN}/{url}"
        parts.append(url + rest)
    return f'{attr}="' + ", ".join(parts) + '"'

body = re.sub(r'(srcset|imagesrcset)="([^"]+)"', _abs_srcset, body)

# внешние подключения из body убираем — их поставим сами, в нужные блоки
body = re.sub(r'<link rel="stylesheet"[^>]*styles\.css[^>]*>\s*', "", body)
body = re.sub(r'<script[^>]*script\.js[^>]*></script>\s*', "", body)


# 2. границы блоков (по сырому тексту, до кодирования)
def pos(patt):
    m = re.search(patt, body)
    if not m:
        raise SystemExit(f"Не нашёл: {patt}")
    return m.start()


p_club = pos(r'<section[^>]*id="club"')

part_a = body[:p_club]     # шапка + первый экран + «как подобрать» + бот + интенсивы
tail = body[p_club:]       # клуб + вопросы + консультация + подвал


# 3. анти-mojibake
def to_entities(text):
    return "".join(c if ord(c) < 128 else f"&#{ord(c)};" for c in text)


part_a, tail = map(to_entities, (part_a, tail))

HEAD = f"""<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link rel="preconnect" href="{CDN}" crossorigin />
{FONT_LINK}
<link rel="stylesheet" href="{CDN}/styles.css?v={VER}" />
"""
TAIL_SCRIPT = f'\n<script src="{CDN}/script.js?v={VER}"></script>\n'

block1 = HEAD + "\n" + part_a
block2 = tail + TAIL_SCRIPT

(BASE / "tilda-block-1.html").write_text(block1, encoding="utf-8")
(BASE / "tilda-block-2.html").write_text(block2, encoding="utf-8")


def sz(s):
    n = len(s)
    ok = "OK помещается" if n < 30000 else "!! ПРЕВЫШЕН ЛИМИТ 30000"
    return f"{n:,} chars ({n/1024:.1f} KB)  {ok}"


print("Готово")
print(f"  tilda-block-1.html: {sz(block1)}  — стили+шапка+первый экран+бот+интенсивы")
print(f"  tilda-block-2.html: {sz(block2)}  — клуб+вопросы+консультация+подвал+скрипт")
print(f"  Лимит T123: 30 000 chars / блок. CSS/JS — с {CDN}")
