"""Gera dashboard/dist/ pronto para publicar na Netlify (ou qualquer hospedagem estática).

    python dashboard/build.py https://SEU-PROJETO.supabase.co SUA_CHAVE_PUBLICA

A chave pública (anon/publishable) pode ficar no site: quem protege os dados é
o login + as políticas RLS da migração. NUNCA use a service_role aqui.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
DIST = HERE / "dist"

SKELETON = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<script src="config.js"></script>
<style>:root{{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}}
body{{margin:0;font:14px system-ui,sans-serif}}img{{max-width:100%}}[hidden]{{display:none!important}}</style>
</head>
<body>
{body}
</body>
</html>
"""


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    key = sys.argv[2] if len(sys.argv) > 2 else ""
    if "service_role" in key:
        raise SystemExit("Essa é a chave service_role. Use a chave pública (anon/publishable).")
    DIST.mkdir(exist_ok=True)
    body = (HERE / "painel.html").read_text(encoding="utf-8")
    (DIST / "index.html").write_text(SKELETON.format(body=body), encoding="utf-8")
    (DIST / "config.js").write_text(
        "window.RADAR_CONFIG = " + json.dumps({"supabaseUrl": url, "supabaseKey": key}) + ";\n",
        encoding="utf-8")
    (DIST / "_headers").write_text(
        "/*\n  X-Frame-Options: DENY\n  X-Content-Type-Options: nosniff\n"
        "  Referrer-Policy: strict-origin-when-cross-origin\n", encoding="utf-8")
    print(f"Pronto: {DIST}/ (index.html + config.js + _headers)")


if __name__ == "__main__":
    main()
