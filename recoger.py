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
        "urls": ["https://dogc.gencat.cat/ca/inici/"],
        # Desde la portada, abre el sumario del último número
        "seguir": r"sumari-del-dogc/\?numDOGC=\d+$",
        "patron_enlace": r"document-del-dogc",
    },
    {
        "fuente": "Consell Comarcal del Segrià (web)",
        "urls": ["https://www.segria.cat/actualitat"],
    },
    {
        "fuente": "Ajuntament de Lleida",
        "urls": ["https://www.paeria.cat/ca/actualitat/noticies"],
    },
    {
        "fuente": "Consell Comarcal de la Noguera",
        "urls": ["https://www.ccnoguera.cat/"],
    },
]

# Canales RSS (más fiables que leer la página)
RSS = []  # El RSS del Segrià devuelve HTML; se usa su web de noticias

# JavaScript que devuelve cada enlace con los encabezados que tiene encima
# (sección y organismo), para saber, p. ej., de qué ayuntamiento es un anuncio.
JS_ENLACES = """() => {
  const nivel = {H1:1, H2:2, H3:3, H4:4, H5:5, H6:6};
  const actual = {};
  const salida = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  let n;
  while ((n = w.nextNode())) {
    if (nivel[n.tagName]) {
      const k = nivel[n.tagName];
      actual[k] = (n.innerText || '').trim().replace(/\\s+/g, ' ');
      for (let i = k + 1; i <= 6; i++) delete actual[i];
    } else if (n.tagName === 'A' && n.href) {
      const ctx = Object.keys(actual).sort().map(k => actual[k]).filter(Boolean).join(' > ');
      salida.push([n.innerText, n.href, ctx]);
    }
  }
  return salida;
}"""

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
            nuevo = item(
                "BDNS", c.get("descripcion") or c.get("titulo") or f"Convocatoria {num}",
                f"https://www.infosubvenciones.es/bdnstrans/GE/es/convocatorias/{num}",
                extracto=organo, fecha=c.get("fechaRecepcion"),
            )
            nuevo["numero"] = num
            resultados.append(nuevo)
        if not isinstance(datos, dict) or datos.get("last", True):
            break
        pagina += 1

    # Ficha completa solo de las convocatorias estatales, de Cataluña o de Lleida
    def interesa(r):
        o = r["extracto"].upper()
        return o.startswith("ESTADO") or "CATALU" in o or "LLEIDA" in o or "LÉRIDA" in o

    candidatas = [r for r in resultados if interesa(r)][:120]
    for r in candidatas:
        try:
            d = requests.get(
                "https://www.infosubvenciones.es/bdnstrans/api/convocatorias",
                params={"numConv": r["numero"], "vpd": "GE"},
                headers={"Accept": "application/json", "User-Agent": UA},
                timeout=30).json()
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(d, dict):
            continue
        r["detalle"] = {k: d.get(k) for k in (
            "tiposBeneficiarios", "tipoBeneficiarios", "sectores", "regiones",
            "finalidad", "presupuestoTotal", "abierto",
            "fechaInicioSolicitud", "fechaFinSolicitud", "textInicio", "textFin",
            "sedeElectronica", "urlBasesReguladoras", "descripcionBasesReguladoras",
            "instrumentos", "tipoConvocatoria") if d.get(k) not in (None, "", [])}
    return resultados, None


# ---------------------------------------------------------------- RSS
def recoger_rss(cfg):
    """Lee un RSS con expresiones regulares, para tolerar XML mal formado."""
    from email.utils import parsedate_to_datetime
    from html import unescape
    r = requests.get(cfg["url"], headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    texto = r.content.decode(r.encoding or "utf-8", errors="replace")

    def campo(bloque, etiqueta):
        m = re.search(rf"<{etiqueta}[^>]*>(.*?)</{etiqueta}>", bloque, re.S | re.I)
        if not m:
            return ""
        valor = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", m.group(1), flags=re.S)
        return re.sub(r"<[^>]+>", " ", unescape(valor)).strip()

    bloques = re.findall(r"<item\b.*?</item>", texto, re.S | re.I)
    if not bloques:
        return [], f"La respuesta no contiene elementos RSS (empieza por: {texto[:80]!r})"
    limite = HOY - timedelta(days=7)
    resultados = []
    for b in bloques:
        fecha = None
        try:
            fecha = parsedate_to_datetime(campo(b, "pubDate")).date()
        except Exception:  # noqa: BLE001
            pass
        if fecha and fecha < limite:
            continue
        resultados.append(item(cfg["fuente"], campo(b, "title"), campo(b, "link"),
                               extracto=campo(b, "description"),
                               fecha=fecha.isoformat() if fecha else None))
    return resultados, None if resultados else "Sin novedades en los últimos 7 días"


# ---------------------------------------------------------------- Webs
def recoger_paginas():
    from playwright.sync_api import sync_playwright

    salida = {}
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        contexto = navegador.new_context(user_agent=UA, locale="ca-ES")
        os.makedirs(os.path.join(CARPETA, "debug"), exist_ok=True)
        for cfg in PAGINAS:
            fuente = cfg["fuente"]
            ultimo_error = None
            for url in cfg["urls"]:
                try:
                    pagina = contexto.new_page()
                    pagina.goto(url, wait_until=cfg.get("carga", "domcontentloaded"),
                                timeout=cfg.get("espera", TIMEOUT) * 1000)
                    pagina.wait_for_timeout(8000 if cfg.get("carga") == "commit" else 5000)
                    enlaces = pagina.evaluate(JS_ENLACES)
                    if cfg.get("seguir"):
                        destino = next((h for _, h, _c in enlaces
                                        if re.search(cfg["seguir"], h)), None)
                        if not destino:
                            raise RuntimeError("No se encontró el enlace al sumario")
                        menu = {h for _, h, _c in enlaces}
                        pagina.goto(destino, wait_until="domcontentloaded",
                                    timeout=TIMEOUT * 1000)
                        pagina.wait_for_timeout(5000)
                        url = destino
                        enlaces = [e for e in pagina.evaluate(JS_ENLACES)
                                   if e[1] not in menu and "#" not in e[1]]
                    titulo_pagina = pagina.title()
                    pagina.close()
                except Exception as e:  # noqa: BLE001
                    ultimo_error = f"{url} -> {type(e).__name__}: {str(e).splitlines()[0]}"[:300]
                    continue

                # Volcado de diagnóstico: todos los enlaces de la página
                nombre = re.sub(r"[^a-z0-9]+", "-", fuente.lower()).strip("-")
                with open(os.path.join(CARPETA, "debug", f"{nombre}.json"), "w",
                          encoding="utf-8") as f:
                    json.dump({"url": url, "titulo": titulo_pagina,
                               "enlaces": [[" ".join((t or "").split()), h, c]
                                           for t, h, c in enlaces][:400]},
                              f, ensure_ascii=False, indent=1)

                vistos, items = set(), []
                for texto, href, ctx in enlaces:
                    texto = " ".join((texto or "").split())
                    if len(texto) < 15 or DESCARTAR_TEXTO.match(texto):
                        continue
                    href = urljoin(url, href)
                    if href in vistos or href.startswith(("mailto:", "tel:", "javascript:")):
                        continue
                    if cfg.get("patron_enlace") and not re.search(cfg["patron_enlace"], href):
                        continue
                    if cfg.get("filtro") and not any(f in texto.lower() for f in cfg["filtro"]):
                        continue
                    vistos.add(href)
                    # El encabezado (organismo) va en "seccion" y en el extracto
                    items.append(item(fuente, texto, href, extracto=ctx, seccion=ctx))
                salida[fuente] = (items, None if items else
                                  f"{url} abierta ({len(enlaces)} enlaces), ninguno útil")
                break
            else:
                salida[fuente] = ([], ultimo_error)
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

    for cfg in RSS:
        try:
            anotar(cfg["fuente"], *recoger_rss(cfg))
        except Exception as e:  # noqa: BLE001
            anotar(cfg["fuente"], [], f"{type(e).__name__}: {e}"[:300])

    try:
        for nombre, (items, error) in recoger_paginas().items():
            anotar(nombre, items, error)
    except Exception as e:  # noqa: BLE001
        anotar("Navegador", [], f"{type(e).__name__}: {e}"[:300])

    os.makedirs(CARPETA, exist_ok=True)
    # Marca como "nuevo" lo que no se había visto en días anteriores
    ruta_vistos = os.path.join(CARPETA, "vistos.json")
    try:
        with open(ruta_vistos, encoding="utf-8") as f:
            vistos = json.load(f)
    except Exception:  # noqa: BLE001
        vistos = {}
    for pub in resultado["publicaciones"]:
        clave = pub["url"] or pub["titulo"]
        primera = vistos.get(clave)
        pub["nuevo"] = primera is None or primera == HOY.isoformat()
        pub["visto_desde"] = primera or HOY.isoformat()
        vistos.setdefault(clave, HOY.isoformat())
    limite = (HOY - timedelta(days=180)).isoformat()
    vistos = {k: v for k, v in vistos.items() if v >= limite}
    with open(ruta_vistos, "w", encoding="utf-8") as f:
        json.dump(vistos, f, ensure_ascii=False)

    for nombre in (f"{HOY.isoformat()}.json", "ultimo.json"):
        with open(os.path.join(CARPETA, nombre), "w", encoding="utf-8") as f:
            json.dump(resultado, f, ensure_ascii=False, indent=1)

    total_ok = sum(1 for f in resultado["fuentes"].values() if f["estado"] == "OK")
    print(f"\nFuentes OK: {total_ok}/{len(resultado['fuentes'])}. "
          f"Publicaciones: {len(resultado['publicaciones'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
