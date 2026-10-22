"""
Recoge las publicaciones del día de los boletines oficiales y las guarda en
datos/AAAA-MM-DD.json y datos/ultimo.json.

Fuentes:
  - BOE (API oficial de datos abiertos)
  - BDNS / infosubvenciones (API pública de convocatorias)
  - DOGC, BOP de Lleida, Ajuntament de Lleida, Consell Comarcal de la Noguera,
    Consell Comarcal del Segrià (navegador sin interfaz con Playwright)

Si una fuente falla, se registra el error y el resto sigue funcionando.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests

ZONA = ZoneInfo("Europe/Madrid")
HOY = datetime.now(ZONA).date()
# Permite forzar una fecha para pruebas: FECHA=2026-10-21 python recoger.py
if os.environ.get("FECHA"):
    HOY = datetime.strptime(os.environ["FECHA"], "%Y-%m-%d").date()

CARPETA = "datos"
TIMEOUT = 60
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

# Páginas que se abren con navegador. "filtro": si se indica, solo se guardan
# los enlaces cuyo texto contenga alguna de estas palabras (sin distinguir
# mayúsculas). "patron_enlace": solo enlaces cuya URL cumpla esta expresión.
PAGINAS = [
    {
        "fuente": "DOGC",
        "url": "https://dogc.gencat.cat/ca/inici/",
        "patron_enlace": r"document-del-dogc|documentId=",
    },
    {
        "fuente": "BOP Lleida",
        "url": "https://bop.diputaciolleida.cat/",
        "filtro": [
            "ajuntament de lleida", "paeria",
            "consell comarcal de la noguera", "noguera",
            "consell comarcal del segrià", "segrià", "segria",
            "subvenc", "ajut", "ordenança", "ordenanza", "conveni",
        ],
    },
    {
        "fuente": "Ajuntament de Lleida",
        "url": "https://www.paeria.cat/ca/actualitat/noticies",
    },
    {
        "fuente": "Consell Comarcal de la Noguera",
        "url": "https://www.ccnoguera.cat/",
    },
    {
        "fuente": "Consell Comarcal del Segrià",
        "url": "https://www.ccsegria.cat/",
    },
]

# Enlaces que no aportan nada (menús, redes, avisos legales...)
DESCARTAR_TEXTO = re.compile(
    r"^(inici|inicio|home|contacte|contacto|cerca|buscar|mapa web|accessibilitat|"
    r"avís legal|aviso legal|cookies|privacitat|facebook|twitter|x|instagram|"
    r"youtube|linkedin|més|mas|más|ca|es|en|oc)$",
    re.IGNORECASE,
)


def item(fuente, titulo, url, extracto="", seccion="", fecha=None):
    return {
        "fuente": fuente,
        "seccion": seccion,
        "titulo": " ".join(str(titulo).split()),
        "url": url,
        "extracto": " ".join(str(extracto).split())[:600],
        "fecha": fecha or HOY.isoformat(),
    }


# ---------------------------------------------------------------- BOE
def recoger_boe():
    url = f"https://www.boe.es/datosabiertos/api/boe/sumario/{HOY:%Y%m%d}"
    r = requests.get(url, headers={"Accept": "application/json", "User-Agent": UA},
                     timeout=TIMEOUT)
    if r.status_code == 404:
        return [], "Sin BOE publicado este día"
    r.raise_for_status()
    datos = r.json()

    resultados = []

    def recorrer(nodo, seccion="", departamento=""):
        if isinstance(nodo, list):
            for n in nodo:
                recorrer(n, seccion, departamento)
            return
        if not isinstance(nodo, dict):
            return
        if "codigo" in nodo and "nombre" in nodo and "departamento" in nodo:
            seccion = f"{nodo.get('codigo')} {nodo.get('nombre')}"
        if "nombre" in nodo and ("epigrafe" in nodo or "item" in nodo) and "codigo" in nodo \
                and "departamento" not in nodo:
            departamento = nodo.get("nombre", departamento)
        if "identificador" in nodo and "titulo" in nodo:
            enlace = nodo.get("url_html") or nodo.get("url_pdf") or ""
            if isinstance(enlace, dict):
                enlace = enlace.get("texto", "")
            resultados.append(item("BOE", nodo["titulo"], enlace,
                                   extracto=departamento, seccion=seccion))
            return
        for v in nodo.values():
            recorrer(v, seccion, departamento)

    recorrer(datos)
    return resultados, None


# ---------------------------------------------------------------- BDNS
def recoger_bdns():
    desde = (HOY - timedelta(days=1)).strftime("%d/%m/%Y")
    hasta = HOY.strftime("%d/%m/%Y")
    url = "https://www.infosubvenciones.es/bdnstrans/api/convocatorias/busqueda"
    resultados, pagina = [], 0
    while pagina < 20:
        r = requests.get(url, params={
            "fechaDesde": desde, "fechaHasta": hasta,
            "page": pagina, "pageSize": 50, "vpd": "GE",
        }, headers={"Accept": "application/json", "User-Agent": UA}, timeout=TIMEOUT)
        r.raise_for_status()
        datos = r.json()
        filas = datos.get("content", []) if isinstance(datos, dict) else datos
        for c in filas:
            num = c.get("numeroConvocatoria") or c.get("id")
            organo = " / ".join(x for x in [c.get("nivel1"), c.get("nivel2"),
                                            c.get("nivel3")] if x)
            resultados.append(item(
                "BDNS", c.get("descripcion") or c.get("titulo") or f"Convocatoria {num}",
                f"https://www.infosubvenciones.es/bdnstrans/GE/es/convocatorias/{num}",
                extracto=organo, fecha=c.get("fechaRecepcion"),
            ))
        if not isinstance(datos, dict) or datos.get("last", True):
            break
        pagina += 1
    return resultados, None


# ---------------------------------------------------------------- Webs
def recoger_paginas():
    from playwright.sync_api import sync_playwright

    salida = {}
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        contexto = navegador.new_context(user_agent=UA, locale="ca-ES")
        for cfg in PAGINAS:
            fuente = cfg["fuente"]
            try:
                pagina = contexto.new_page()
                pagina.goto(cfg["url"], wait_until="domcontentloaded", timeout=TIMEOUT * 1000)
                pagina.wait_for_timeout(3000)
                enlaces = pagina.eval_on_selector_all(
                    "a[href]", "els => els.map(e => [e.innerText, e.href])")
                vistos, items = set(), []
                for texto, href in enlaces:
                    texto = " ".join((texto or "").split())
                    if len(texto) < 15 or DESCARTAR_TEXTO.match(texto):
                        continue
                    href = urljoin(cfg["url"], href)
                    if href in vistos or href.startswith(("mailto:", "tel:", "javascript:")):
                        continue
                    if cfg.get("patron_enlace") and not re.search(cfg["patron_enlace"], href):
                        continue
                    if cfg.get("filtro") and not any(f in texto.lower() for f in cfg["filtro"]):
                        continue
                    vistos.add(href)
                    items.append(item(fuente, texto, href))
                salida[fuente] = (items, None if items else "Página abierta, sin enlaces útiles")
                pagina.close()
            except Exception as e:  # noqa: BLE001
                salida[fuente] = ([], f"{type(e).__name__}: {e}"[:300])
        navegador.close()
    return salida


# ---------------------------------------------------------------- Main
def main():
    resultado = {"fecha": HOY.isoformat(),
                 "generado": datetime.now(ZONA).isoformat(timespec="seconds"),
                 "fuentes": {}, "publicaciones": []}

    def anotar(nombre, items, error):
        resultado["fuentes"][nombre] = {
            "estado": "OK" if items else "ERROR" if error and "Sin " not in error else "VACIO",
            "elementos": len(items), "detalle": error or "",
        }
        resultado["publicaciones"].extend(items)
        print(f"{nombre:35} {len(items):4} elementos  {error or ''}")

    for nombre, funcion in [("BOE", recoger_boe), ("BDNS", recoger_bdns)]:
        try:
            anotar(nombre, *funcion())
        except Exception as e:  # noqa: BLE001
            anotar(nombre, [], f"{type(e).__name__}: {e}"[:300])

    try:
        for nombre, (items, error) in recoger_paginas().items():
            anotar(nombre, items, error)
    except Exception as e:  # noqa: BLE001
        anotar("Navegador", [], f"{type(e).__name__}: {e}"[:300])

    os.makedirs(CARPETA, exist_ok=True)
    for nombre in (f"{HOY.isoformat()}.json", "ultimo.json"):
        with open(os.path.join(CARPETA, nombre), "w", encoding="utf-8") as f:
            json.dump(resultado, f, ensure_ascii=False, indent=1)

    total_ok = sum(1 for f in resultado["fuentes"].values() if f["estado"] == "OK")
    print(f"\nFuentes OK: {total_ok}/{len(resultado['fuentes'])}. "
          f"Publicaciones: {len(resultado['publicaciones'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
