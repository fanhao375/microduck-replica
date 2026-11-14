"""Bundle the offline prototype so it also opens with a double click."""
from pathlib import Path
import base64
import json
import re


def main():
    root = Path(__file__).resolve().parent
    html = (root / "index.html").read_text()

    def style(match):
        return "<style>\n" + (root / match[1].split("?", 1)[0]).read_text() + "\n</style>"

    scripts = []
    modules = []

    def script(match):
        scripts.append((root / match[1].split("?", 1)[0]).read_text())
        return ""

    html = re.sub(r'<link rel="stylesheet" href="([^"]+)">', style, html)
    html = re.sub(r'<script defer src="([^"]+)"></script>', script, html)

    def module(match):
        modules.append((root / match[1].split("?", 1)[0]).read_text())
        return ""

    def importmap(match):
        mapping = json.loads(match[1])
        for name, relative in mapping["imports"].items():
            source = (root / relative.split("?", 1)[0]).read_bytes()
            mapping["imports"][name] = "data:text/javascript;base64," + base64.b64encode(source).decode()
        return '<script type="importmap">' + json.dumps(mapping, separators=(",", ":")) + '</script>'

    html = re.sub(r'<script type="module" src="([^"]+)"></script>', module, html)
    html = re.sub(r'<script type="importmap"[^>]*>(.*?)</script>', importmap, html, flags=re.S)
    body_scripts = "<script>\n" + "\n".join(scripts) + "\n</script>\n"
    body_scripts += "\n".join('<script type="module">\n' + source + '\n</script>' for source in modules)
    html = html.replace("</body>", body_scripts + "\n</body>")
    (root / "prototype.html").write_text(html)
    print("Created prototype.html")


if __name__ == "__main__":
    main()
