"""Make embed/rtp-map.html (standalone page) and embed/ghl-paste.txt (one block to paste into a site builder)."""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
body = open(os.path.join(ROOT, "dist/index.html")).read()
head = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>:root{color-scheme:light}body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style>
"""
i = body.index("</style>") + len("</style>")
doc = head + body[:i] + "\n</head>\n<body>\n" + body[i:] + "\n</body>\n</html>\n"
os.makedirs(os.path.join(ROOT, "embed"), exist_ok=True)
open(os.path.join(ROOT, "embed/rtp-map.html"), "w").write(doc)

# The page goes into an iframe's srcdoc via a JS template literal, so escape what would end it early.
t = doc.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${").replace("</script", "<\\/script").replace("<!--", "<\\!--")
paste = ('<!-- Rent-to-Price Map: paste this whole block into a GHL "Custom JS/HTML" element. -->\n'
         '<div id="rtp-map-embed"></div>\n<script>\n(function () {\n  var f = document.createElement("iframe");\n'
         '  f.title = "Rent-to-Price Map";\n  f.style.cssText = "width:100%;height:850px;border:0;display:block;";\n'
         "  f.srcdoc = `" + t + "`;\n  document.getElementById(\"rtp-map-embed\").appendChild(f);\n})();\n</script>\n")
open(os.path.join(ROOT, "embed/ghl-paste.txt"), "w").write(paste)
print("embed/rtp-map.html and embed/ghl-paste.txt written")
