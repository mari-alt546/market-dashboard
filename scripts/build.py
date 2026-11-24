#!/usr/bin/env python3
"""Build index.html (GitHub Pages) and artifact/page.html (claude.ai) from page.html."""
import os, shutil
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(root, "page.html"), encoding="utf-8").read()
head = ('<!DOCTYPE html>\n<html lang="ko">\n<head>\n<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">\n'
        '<meta name="theme-color" content="#F2F4F6">\n<meta name="apple-mobile-web-app-capable" content="yes">\n')
title_end = src.index("</style>") + len("</style>")
page = head + src[:title_end] + "\n</head>\n<body>\n" + src[title_end:] + "\n</body>\n</html>\n"
open(os.path.join(root, "index.html"), "w", encoding="utf-8").write(page)
os.makedirs(os.path.join(root, "artifact"), exist_ok=True)
open(os.path.join(root, "artifact", "page.html"), "w", encoding="utf-8").write(src)
for f in ("data.json", "valuation.json"):
    shutil.copy(os.path.join(root, f), os.path.join(root, "artifact", f))
print("built index.html and artifact/page.html")
