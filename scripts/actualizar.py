"""Changuito: baja los precios de SEPA y genera data.json para el sitio.

Uso:
  python3 scripts/actualizar.py                 # descarga el archivo más reciente y genera data.json
  python3 scripts/actualizar.py --zip sepa.zip  # usa un archivo ya descargado
Salida: data.json en la raíz del repositorio.
"""
import argparse, datetime as dt, glob, io, json, os, re, shutil, statistics, sys, tempfile, unicodedata, urllib.request, zipfile
import pandas as pd

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTAL = "https://datos.produccion.gob.ar"
DATASET = "sepa-precios"
PROVINCIA = "AR-C"  # CABA. Para GBA agregar "AR-B" (filtrando localidades).

# (id_comercio, id_bandera) -> cadena
CADENAS = {(12, 1): "coto", (15, 1): "dia", (9, 3): "jumbo", (9, 2): "disco", (9, 1): "vea",
           (10, 1): "carrefour", (10, 2): "carrefour", (11, 4): "changomas", (11, 5): "changomas"}
ORDEN = ["coto", "dia", "carrefour", "jumbo", "disco", "vea", "changomas"]
PREF_DESC = ["coto", "carrefour", "changomas", "jumbo", "disco", "vea", "dia"]  # de qué cadena tomar el nombre

# ---------------------------------------------------------------- descarga
NAVEGADOR = {  # el portal rechaza (403) los pedidos que no parecen de un navegador
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
    "Accept": "text/html,application/json,application/zip,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9",
}

def pedir(url, timeout=60):
    return urllib.request.urlopen(urllib.request.Request(url, headers=NAVEGADOR), timeout=timeout)

def candidatos():
    """Lista de (nombre, url, fecha) de los zips del dataset. Prueba la API y, si falla, la página web."""
    errores = []
    try:
        with pedir(f"{PORTAL}/api/3/action/package_show?id={DATASET}") as r:
            rec = json.load(r)["result"]["resources"]
        zs = [(x.get("name", ""), x["url"], x.get("last_modified") or x.get("created") or "")
              for x in rec if str(x.get("url", "")).lower().split("?")[0].endswith(".zip")]
        if zs:
            return zs
        errores.append("la API no listó archivos .zip")
    except Exception as e:
        errores.append(f"API: {e}")
    try:
        with pedir(f"{PORTAL}/dataset/{DATASET}") as r:
            html = r.read().decode("utf-8", "ignore")
        urls = sorted(set(re.findall(r'href="([^"]+?\.zip)"', html)))
        urls = [u if u.startswith("http") else PORTAL + u for u in urls]
        if urls:
            return [(u.rsplit("/", 1)[-1], u, "") for u in urls]
        errores.append("la página del dataset no tiene links a .zip")
    except Exception as e:
        errores.append(f"página: {e}")
    sys.exit("No pude obtener la lista de archivos de SEPA (" + "; ".join(errores) + ").\n"
             "Si el error es 403, el portal está bloqueando los servidores de GitHub. "
             "Alternativa: pegar la dirección directa del zip en el secreto SEPA_URL del repositorio.")

def descargar(destino):
    """Descarga el zip de SEPA más reciente. Si existe la variable SEPA_URL, usa esa dirección."""
    url = os.environ.get("SEPA_URL", "").strip()
    if not url:
        zs = candidatos()
        dias = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
        hoy = dt.date.today().weekday()
        def puntaje(x):  # preferimos el del día más reciente según el nombre; si no, por fecha de modificación
            nombre = (x[0] + x[1]).lower()
            for atras in range(7):
                if dias[(hoy - atras) % 7] in nombre:
                    return (0, atras, "")
            return (1, 0, "".join(chr(255 - ord(c)) for c in x[2]))
        url = sorted(zs, key=puntaje)[0][1]
    print("Descargando", url, file=sys.stderr)
    if "drive.google.com" in url or "docs.google.com" in url:
        m = re.search(r"/d/([\w-]{20,})", url) or re.search(r"[?&]id=([\w-]{20,})", url)
        if not m:
            sys.exit("El enlace de Google Drive no tiene el formato esperado. Copiá de nuevo el enlace para compartir del archivo.")
        fid = m.group(1)
        try:  # descarga directa (confirm=t evita el aviso de "archivo grande")
            with pedir(f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t", timeout=600) as r, open(destino, "wb") as f:
                shutil.copyfileobj(r, f, 1024 * 1024)
        except Exception as e:
            print("Descarga directa falló:", e, file=sys.stderr)
        if not zipfile.is_zipfile(destino):
            import gdown  # respaldo: maneja las variantes de confirmación de Drive
            if not gdown.download(id=fid, output=destino, quiet=False):
                sys.exit("No pude bajar el archivo de Drive. Revisá que esté compartido como 'Cualquier persona con el enlace'.")
        print(f"Descargado: {os.path.getsize(destino) / 1e6:.0f} MB", file=sys.stderr)
        return destino
    with pedir(url, timeout=600) as r, open(destino, "wb") as f:
        shutil.copyfileobj(r, f, 1024 * 1024)
    print(f"Descargado: {os.path.getsize(destino) / 1e6:.0f} MB", file=sys.stderr)
    return destino

def extraer(zip_path, carpeta):
    """Extrae el zip diario y los zips de cada cadena que trae adentro."""
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(carpeta)
    for interno in glob.glob(os.path.join(carpeta, "**", "*.zip"), recursive=True):
        if os.path.getsize(interno) == 0:
            continue
        com = re.search(r"comercio-sepa-(\d+)_", interno)
        if not com or not any(c == int(com.group(1)) for c, _ in CADENAS):
            continue
        destino = os.path.join(carpeta, "cadena-" + com.group(1))
        with zipfile.ZipFile(interno) as z:
            z.extractall(destino)

# ---------------------------------------------------------------- agregación
def leer(path, **kw):
    return pd.read_csv(path, sep="|", dtype=str, encoding="utf-8-sig", on_bad_lines="skip", **kw)

def agregar(carpeta):
    cols = ["id_bandera", "id_sucursal", "id_producto", "productos_ean", "productos_descripcion",
            "productos_marca", "productos_precio_lista", "productos_precio_unitario_promo1", "productos_leyenda_promo1",
            "productos_cantidad_presentacion", "productos_unidad_medida_presentacion"]
    partes, sucursales, fecha = [], {}, None
    for com in sorted({c for c, _ in CADENAS}):
        dirs = glob.glob(os.path.join(carpeta, f"cadena-{com}"))
        if not dirs:
            print(f"Aviso: no vino la cadena {com}", file=sys.stderr)
            continue
        d = dirs[0]
        suc = leer(os.path.join(d, "sucursales.csv"), usecols=["id_bandera", "id_sucursal", "sucursales_provincia"])
        suc = suc[suc.sucursales_provincia.str.strip() == PROVINCIA]
        keep = {(int(b), s.strip()) for b, s in zip(suc.id_bandera, suc.id_sucursal) if (com, int(b)) in CADENAS}
        for b, _ in keep:
            sucursales[CADENAS[(com, b)]] = sucursales.get(CADENAS[(com, b)], 0) + 1
        try:
            c = leer(os.path.join(d, "comercio.csv"), usecols=["comercio_ultima_actualizacion"])
            f = str(c.comercio_ultima_actualizacion.dropna().iloc[0])[:10]
            fecha = max(fecha or f, f)
        except Exception:
            pass
        for ch in leer(os.path.join(d, "productos.csv"), usecols=cols, chunksize=500_000):
            ch = ch.dropna(subset=["id_bandera", "id_sucursal"])
            ch = ch[ch.id_bandera.str.strip().str.isdigit()]
            ch = ch[[(int(b), s.strip()) in keep for b, s in zip(ch.id_bandera, ch.id_sucursal)]]
            ch = ch[ch.productos_ean.str.strip() == "1"]
            if len(ch):
                ch["chain"] = [CADENAS[(com, int(b))] for b in ch.id_bandera]
                partes.append(ch)
    df = pd.concat(partes, ignore_index=True)
    df["ean"] = df.id_producto.str.strip().str.zfill(13)
    df["precio"] = pd.to_numeric(df.productos_precio_lista, errors="coerce")
    df["p1"] = pd.to_numeric(df.productos_precio_unitario_promo1, errors="coerce")
    df = df[(df.precio > 0) & ~df.ean.str.startswith("2")]  # sin productos pesables de la propia tienda

    moda = lambda s: s.dropna().mode().iloc[0] if s.notna().any() else None
    g = df.groupby(["chain", "ean"]).agg(precio=("precio", moda), sucursales=("id_sucursal", "nunique"),
                                         desc=("productos_descripcion", "first"), marca=("productos_marca", "first"),
                                         cant=("productos_cantidad_presentacion", "first"),
                                         unidad=("productos_unidad_medida_presentacion", "first"))
    pr = df.dropna(subset=["productos_leyenda_promo1"]).copy()
    pr["ley"] = pr.productos_leyenda_promo1.str.strip()
    pr = pr[pr.ley != ""]
    pg = pr.groupby(["chain", "ean"]).agg(promo=("ley", moda), promo_precio=("p1", moda), promo_suc=("id_sucursal", "nunique"))
    return g.join(pg, how="left").reset_index(), sucursales, fecha

# ---------------------------------------------------------------- catálogo
def norm(s):
    s = unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode().upper()
    return re.sub(r"\s+", " ", s)

PASILLOS = [  # (clave, nombre, palabras); gana la primera regla que coincide
    ("mascotas", "Mascotas", r"\b(PERRO|PERROS|GATOS? |GATITO|CANINO|FELINO|MASCOTA|DOG CHOW|CAT CHOW|PEDIGREE|WHISKAS|EUKANUBA|PIEDRAS SANITARIAS)\b"),
    ("bebes", "Bebés", r"\b(PANAL|PANALES|PAÑAL|TOALLITAS HUMEDAS|BEBE|BABY|PAMPERS|HUGGIES|BABYSEC|ESTRELLA BABY|PAPILLA|NAN |NUTRILON|SANCOR BEBE)"),
    ("limpieza", "Limpieza", r"\b(DETERG|ESCOBILLON|MOPA|VANISH|PRELAVADO|DEO DE AMBIENTE|PAÑO|PANO |PAÑOS|BALDE|LIMP\.|DIFUSOR|SAHUMERIO|INICIADOR|LAVANDINA|LAVAVAJILLA|SUAVIZANTE|JABON (EN POLVO|LIQ\w* (P/|PARA )?ROPA|ROPA|PARA ROPA|BLANCO)|JABON ROPA|QUITAMANCHAS|LIMPIADOR|LIMPIA|DESINFECT|INSECTIC|ESPIRAL|REPELENTE|PAPEL HIG|ROLLO (DE )?COCINA|SERVILLETA|ESPONJA|BOLSA(S)? (DE )?RESIDUO|RESIDUOS|AROMATIZ|DESODORANTE (DE )?AMBIENTE|DEO AMBIENTE|LUSTRA|CERA |TRAPO|REJILLA|GUANTE|ESCOBA|SECADOR|CIF |MR MUSCULO|POETT|GLADE|RAID|FUYI|OFF |AYUDIN|SKIP|ALA |ACE |ARIEL|MAGISTRAL|QUERUBIN|PALILLO|FILM|ALUMINIO|FOSFORO|CARBON|VELA)"),
    ("perfumeria", "Perfumería", r"\b(SHAMPOO|AGUA MICELAR|AGUA OXIGENADA|QUITAESMALTE|KOLESTON|COLOR PERM|EXCELLENCE|NUTRISSE|TRATAMIENTO CAP|POMADA|BAGOVIT|PROTEC\.SOLAR|JAB\.|ENJUAGUE|TOALLA|SHAMP|ACONDICIONADOR|ACOND|CREMA DE ENJUAGUE|JABON|DESODORANTE|DEO |ANTITRANSP|AEROSOL|DENTAL|DENTIFRICO|CEPILLO|ENJUAGUE BUCAL|AFEIT|MAQUINA DE AFEITAR|TOALLA(S|ITAS)? (FEMENINA|HIGIEN)|PROTECTOR(ES)? (DIARIO|SOLAR)|TAMPON|CREMA (CORPORAL|DE MANOS|FACIAL|HIDRAT)|PROTECTOR SOLAR|ALGODON|HISOPO|COLONIA|PERFUME|TINTURA|COLORACION|GEL |FIJADOR|TALCO|MAQUILLAJE|ESMALTE|PRESERVATIVO|SHAMPU|DOVE|REXONA|AXE|NIVEA|COLGATE|ODOL|PANTENE|SEDAL|ELVIVE|PLUSBELLE|LADYSOFT|SIEMPRE LIBRE|DONCELLA|ALWAYS|GILLETTE|PRESTOBARBA)"),
    ("bebidas", "Bebidas", r"\b(GASEOSA|CHAMPAÑA|JEREZ|MALBEC|CABERNET|SPRITZ|CYNAR|CAMPARI|AGUA|SODA|JUGO|CERVEZA|VINO|ESPUMANTE|CHAMPAGNE|SIDRA|FERNET|APERITIVO|APERIT|LICOR|WHISKY|WHISKEY|VODKA|GIN |RON |TEQUILA|VERMOUTH|ENERGIZANTE|ISOTONIC|BEBIDA|AMARGO|SPEED|GATORADE|POWERADE|COCA|PEPSI|SPRITE|FANTA|SEVEN|7UP|SCHWEPPES|MANAOS|CUNNINGTON|AQUARIUS|LEVITE|BAGGIO|CEPITA|TANG|CLIGHT|QUILMES|BRAHMA|STELLA|IMPERIAL|SCHNEIDER|AMSTEL|HEINEKEN|ANDES|PATAGONIA|CORONA)\b"),
    ("golosinas", "Golosinas y snacks", r"\b(CHOCOLATE|POCHOCLO|NACHOS|PAPAS FRITAS|CONITOS|CHOCOLATIN|ALFAJOR|CARAMELO|CHICLE|CHUPETIN|GOMITA|PASTILLA|BOMBON|TURRON|OBLEA|BARRA DE CEREAL|BARRITA|PAPAS FRITAS|SNACK|PALITOS|CHIZITOS|MANI|PALOMITA|PEPAS|BONOBON|MILKA|COFLER|ARCOR CHOC|BILLIKEN|MOGUL|TOPLINE|BELDENT|FERRERO|KINDER|LAY'?S|PRINGLES|DORITOS|CHEETOS|3D|TWISTOS|CHIPS)\b"),
    ("lacteos", "Lácteos", r"\b(LECHE|LECH\.|YOGURT|ILOLAY|SVELTY|NESTUM|YOGUR|YOG\.|YOGHURT|QUESO|MANTECA|CREMA DE LECHE|CREMA DOBLE|DULCE DE LECHE|D\.LECHE|POSTRE|FLAN|RICOTA|MUZZARELLA|MOZZARELLA|SERENISIMA|SANCOR|MILKAUT|TREGAR|CASANCREM|DANONINO|ACTIVIA|SER |CINDOR|CHOCOLATADA|LECHE CHOC)\b"),
    ("frescos", "Fiambres, frescos y congelados", r"\b(JAMON|CHORIZO|HUMMUS|ENSALADA|LECHUGA|PUERRO|CEBOLLA|ZANAHORIA|ESPINACA|RUCULA|PAPA |FRUTILLA|HUEVO|BURGANA|VEGGIE|SALAME|SALAMIN|MORTADELA|BONDIOLA|SALCHICH|PANCETA|LEBERWURST|PATE|HAMBURGUES|MEDALLON|NUGGET|REBOZAD|MILANESA|CONGELAD|HELADO|PIZZA|PREPIZZA|EMPANADA|TAPA(S)? (DE|PARA)|PASCUALINA|TARTA|ÑOQUI|NOQUI|RAVIOL|SORRENTINO|CAPELETTI|TALLARINES FRESCOS|PASTAS FRESCAS|TOFU|SWIFT|PATY|GRANJA DEL SOL|PALADINI|CALCHAQUI|LA SALTEÑA|FRIGOR|POLLO|CARNE|PESCADO|MERLUZA|ATUN FRESCO|HUEVO(S)? (BLANCO|COLOR|DE GALLINA))\b"),
    ("panaderia", "Panadería", r"\b(PAN |SCONES|PANES|PAN LACTAL|LACTAL|TOSTADAS|TOSTADA|BUDIN|MAGDALENA|BIZCOCHO|BIZCOCHUELO|MEDIALUNA|PIONONO|GRISIN|PAN DULCE|BAGLEY PAN|FARGO|BIMBO|LA SALTEÑA PAN)\b"),
    ("desayuno", "Desayuno y merienda", r"\b(YERBA|MATE|CAFE|TE |TÉ |SAQUITO|CACAO|NESQUIK|TODDY|CEREAL|COPOS|AVENA|GRANOLA|GALLET|CRACKER|MERMELADA|DULCE DE (MEMBRILLO|BATATA)|MIEL|EDULCORANTE|AZUCAR|NESCAFE|DOLCA|TARAGUI|PLAYADITO|ROSAMONTE|AMANDA|CRUZ DE MALTA|LA VIRGINIA|CABRALES|GRANIX|TERRABUSI|BAGLEY|OREO|DIVERSION|SURTIDO|CRIOLLITAS|TRAVIATA|MAGDALENAS)\b"),
    ("almacen", "Almacén", r"\b(FIDEO|ACETO|RISOTTO|ALMIDON|MAIZ|AJO |SABORIZADOR|SEMILLA|LENTEJON|MIX DE|DULCE DE|CREMA DE (CHOCOLATE|AVELLANA)|SPAGHETTI|TALLARIN|MOSTACHOL|TIRABUZON|ARROZ|HARINA|POLENTA|SEMOLA|ACEITE|VINAGRE|SAL |SAL FINA|SAL GRUESA|PIMIENTA|ORÉGANO|OREGANO|CONDIMENTO|ESPECIA|PIMENTON|COMINO|CALDO|SOPA|PURE|SALSA|TOMATE|MAYONESA|KETCHUP|MOSTAZA|ADEREZO|ACEITUNA|ARVEJA|LENTEJA|POROTO|GARBANZO|CHOCLO|ATUN|CABALLA|SARDINA|DURAZNO|CONSERVA|PICKLE|LEVADURA|POLVO PARA HORNEAR|GELATINA|BIZCOCHUELO|PREMEZCLA|REBOZADOR|PAN RALLADO|FECULA|MAICENA|ESENCIA|COCO RALLADO|FRUTOS SECOS|NUEZ|ALMENDRA|PASAS)\b"),
]
PASILLOS_RE = [(k, n, re.compile(p[:-2] if p.endswith(r")\b") else p)) for k, n, p in PASILLOS]  # sin \b final: acepta plurales

def pasillo(texto):
    t = norm(texto)
    if t.startswith("LB "):  # libros
        return "otros"
    for k, _, rx in PASILLOS_RE:
        if rx.search(" " + t + " "):
            return k
    return "otros"

# ---------------------------------------------------------------- tamaño del paquete
# Se normaliza a gramos (g), mililitros (ml) o unidades (u), para la calculadora de recetas.
UNIDADES = {"g": ("g", 1), "gr": ("g", 1), "grm": ("g", 1), "grs": ("g", 1), "gramos": ("g", 1),
            "kg": ("g", 1000), "kgm": ("g", 1000), "kgs": ("g", 1000),
            "ml": ("ml", 1), "mlt": ("ml", 1), "cc": ("ml", 1), "cmq": ("ml", 1), "cm3": ("ml", 1),
            "l": ("ml", 1000), "lt": ("ml", 1000), "ltr": ("ml", 1000), "lts": ("ml", 1000), "litro": ("ml", 1000), "litros": ("ml", 1000)}

def _tam(cant, unidad):
    u = re.sub(r"[^a-z0-9]", "", str(unidad or "").lower())
    try:
        c = float(str(cant).replace(",", "."))
    except ValueError:
        return None
    if u in UNIDADES and c > 0:
        base, f = UNIDADES[u]
        return (round(c * f, 1), base)
    return None

_RX_TAM = re.compile(r"(\d+(?:[.,]\d+)?)\s*-?\s*(kgm|kgs|kg|grm|grs|gr|g|mlt|ml|cc|cmq|ltr|lts|lt|l)\b", re.I)
_RX_UNI = re.compile(r"(?:x\s*|\b)(\d{1,3})\s*(?:un\b|uni\b|unid|u\b)", re.I)

def tam_desde_nombre(texto):
    t = str(texto or "")
    m = None
    for m in _RX_TAM.finditer(t):  # nos quedamos con la última medida (suele ser la del paquete)
        pass
    if m:
        return _tam(m.group(1), m.group(2))
    mu = _RX_UNI.search(t)
    if mu and int(mu.group(1)) > 0:
        return (float(mu.group(1)), "u")
    return None

def tam_paquete(filas):
    """Tamaño más repetido entre cadenas (g o ml); si no hay, lo lee del nombre."""
    votos = {}
    for f in filas.values():
        t = _tam(f.cant, f.unidad)
        if t:
            votos[t] = votos.get(t, 0) + 1
    nombre = None
    for c in PREF_DESC:
        if c in filas:
            nombre = tam_desde_nombre(filas[c].desc)
            if nombre:
                break
    votos = {t: v for t, v in votos.items() if t[0] >= 5}  # "1 g" o "1 ml" suele ser un error de carga
    if votos:
        top = max(votos.items(), key=lambda kv: (kv[1], kv[0][0]))[0]
        # si el nombre dice otra cosa muy distinta (ej. 1 kg vs 1 g), le creemos al nombre
        if nombre and nombre[1] == top[1] and not (0.2 <= top[0] / nombre[0] <= 5):
            return nombre
        return top
    return nombre

CHICAS = {"de", "del", "la", "las", "el", "los", "y", "x", "con", "sin", "en", "p/", "c/", "s/", "para", "a"}
def lindo(desc):
    d = re.sub(r"\s+[A-Z]{2,4}-[\d.,]+-?\s*[a-zA-Z]+\.?\s*$", "", str(desc).strip())  # "PAQ-500-gr." de Cencosud
    d = re.sub(r"\s+", " ", d).strip(" .")
    out = []
    for i, w in enumerate(d.lower().split(" ")):
        out.append(w if (i and w in CHICAS) else (w[:1].upper() + w[1:]))
    return " ".join(out)

def etiqueta_promo(texto, pct):
    m = re.search(r"(?:hasta|AL)\s+(\d{2}/\d{2})", texto or "", re.I)
    return f"{pct}% off" + (f" hasta {m.group(1)}" if m else "")

def precios_item(filas):
    """filas: dict cadena -> fila agregada. Devuelve lista alineada a ORDEN o None si los datos no cierran."""
    base = {c: f.precio for c, f in filas.items()}
    if len(base) >= 3:
        med = statistics.median(base.values())
        base = {c: p for c, p in base.items() if med / 2.5 <= p <= med * 2.5}
    elif len(base) == 2:
        a, b = sorted(base.values())
        if b > a * 3:
            return None
    if len(base) < 2:
        return None
    out = []
    for c in ORDEN:
        if c not in base:
            out.append(None); continue
        f = filas[c]
        cel = [round(float(f.precio), 2)]
        if isinstance(f.promo, str) and f.promo_precio == f.promo_precio and 0 < f.promo_precio < f.precio and f.promo_suc >= 0.5 * f.sucursales:
            pct = round((1 - f.promo_precio / f.precio) * 100)
            if pct >= 3:
                cel += [round(float(f.promo_precio), 2), etiqueta_promo(f.promo, pct)]
        out.append(cel)
    return out

def construir(agg, sucursales, fecha):
    curados = json.load(open(os.path.join(RAIZ, "scripts", "frecuentes.json"), encoding="utf-8"))
    por = {}
    for f in agg.itertuples():
        por.setdefault(f.ean, {})[f.chain] = f
    items = []
    for ean, filas in por.items():
        if len(filas) < 2:
            continue
        p = precios_item(filas)
        if not p:
            continue
        ref = next(filas[c] for c in PREF_DESC if c in filas)
        nombre = lindo(ref.desc)
        marca = str(ref.marca).strip() if isinstance(ref.marca, str) else ""
        tam = tam_paquete(filas)
        items.append([ean, nombre, lindo(marca) if marca else "", pasillo(nombre + " " + marca), p,
                      [tam[0] if tam[0] % 1 else int(tam[0]), tam[1]] if tam else None])
    items.sort(key=lambda x: (-sum(1 for v in x[4] if v), x[1]))
    idx = {x[0]: x for x in items}
    frecuentes = []
    for prod in curados:
        vs = [{"brand": b, "ean": e} for b, e in prod["variantes"] if e in idx]
        if vs:
            frecuentes.append({"id": prod["id"], "name": prod["nombre"], "pres": prod["pres"], "variants": vs})
    pas = [["frecuentes", "Más buscados"]] + [[k, n] for k, n, _ in PASILLOS if any(x[3] == k for x in items)]
    if any(x[3] == "otros" for x in items):
        pas.append(["otros", "Hogar y otros"])
    orden_pas = ["frecuentes", "almacen", "desayuno", "lacteos", "frescos", "panaderia", "bebidas", "golosinas", "limpieza", "perfumeria", "bebes", "mascotas", "otros"]
    pas.sort(key=lambda x: orden_pas.index(x[0]))
    return {"fecha": fecha, "region": "CABA", "cadenas": ORDEN, "sucursales": sucursales,
            "pasillos": pas, "frecuentes": frecuentes, "items": items}

# ---------------------------------------------------------------- promos bancarias
# Se cargan en una planilla (Google Sheets publicada como CSV) con estas columnas:
# Supermercado | Medio de pago | Descuento % | Tope $ | Compra mínima $ | Días | Vigente hasta | Condiciones | Fuente
SUPER = {"coto": "coto", "dia": "dia", "carrefour": "carrefour", "carrefour market": "carrefour", "jumbo": "jumbo",
         "disco": "disco", "vea": "vea", "changomas": "changomas", "chango mas": "changomas", "mas": "changomas"}
# Bancos cuyas tarjetas sirven para las promos de MODO (si la persona elige uno, se le aplican las de MODO)
MODO_BANCOS = ["Galicia", "Santander", "BBVA", "Macro", "Banco Nación", "Banco Ciudad", "ICBC", "Supervielle",
               "Credicoop", "Comafi", "Banco Columbia"]
DIAS = {"domingo": 0, "lunes": 1, "martes": 2, "miercoles": 3, "jueves": 4, "viernes": 5, "sabado": 6}

def _clave(s):
    return unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().lower().strip()

def _numero(s):
    s = re.sub(r"[^\d,.]", "", str(s or ""))
    if not s:
        return 0.0
    if "," in s:  # formato argentino: 15.000,50
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") >= 1 and len(s.split(".")[-1]) == 3:  # 15.000
        s = s.replace(".", "")
    return float(s)

def leer_promos():
    """Lee la planilla de promos. Usa la dirección de scripts/promos_url.txt; si está vacía, scripts/promos.csv."""
    import csv
    url_file = os.path.join(RAIZ, "scripts", "promos_url.txt")
    url = open(url_file, encoding="utf-8").read().strip() if os.path.exists(url_file) else ""
    url = os.environ.get("PROMOS_URL", "").strip() or url
    try:
        if url:
            with pedir(url) as r:
                texto = r.read().decode("utf-8-sig")
            print("Promos leídas de la planilla", file=sys.stderr)
        else:
            texto = open(os.path.join(RAIZ, "scripts", "promos.csv"), encoding="utf-8-sig").read()
            print("Promos leídas de scripts/promos.csv", file=sys.stderr)
    except Exception as e:
        print(f"Aviso: no pude leer las promos ({e}). El sitio queda sin promos bancarias.", file=sys.stderr)
        return []
    filas = list(csv.reader(io.StringIO(texto)))
    if not filas:
        return []
    cab = [_clave(c) for c in filas[0]]
    def col(*nombres):
        for i, c in enumerate(cab):
            if any(n in c for n in nombres):
                return i
        return None
    i_sup, i_med, i_pct = col("super", "cadena"), col("medio", "pago", "banco"), col("descuento", "%")
    i_top, i_dia, i_has, i_con = col("tope"), col("dia"), col("vigente", "hasta"), col("condic")
    i_min, i_fte = col("minim"), col("fuente")
    if None in (i_sup, i_med, i_pct):
        print("Aviso: la planilla de promos no tiene las columnas Supermercado, Medio de pago y Descuento %.", file=sys.stderr)
        return []
    hoy = dt.date.today()
    promos = []
    for n, f in enumerate(filas[1:], start=2):
        f = f + [""] * (len(cab) - len(f))
        if not any(x.strip() for x in f):
            continue
        sup, med = _clave(f[i_sup]), f[i_med].strip()
        cadenas = list(dict.fromkeys(SUPER[s] for s in re.split(r"\s*(?:,|/|\by\b)\s*", sup) if s in SUPER)) if sup not in ("todos", "todas") else ORDEN
        pct = _numero(f[i_pct])
        if not cadenas or not med or not 0 < pct <= 100:
            print(f"Aviso: fila {n} de promos ignorada (revisá supermercado, medio de pago y descuento).", file=sys.stderr)
            continue
        dias_txt = _clave(f[i_dia]) if i_dia is not None else ""
        dias = sorted({v for k, v in DIAS.items() if k in dias_txt}) or list(range(7))
        hasta = None
        if i_has is not None and f[i_has].strip():
            m = re.match(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", f[i_has].strip())
            if m:
                a = int(m.group(3) or hoy.year)
                a = a + 2000 if a < 100 else a
                try:
                    hasta = dt.date(a, int(m.group(2)), int(m.group(1)))
                except ValueError:
                    hasta = None
            if hasta and hasta < hoy:
                continue  # promo vencida
        for c in cadenas:
            promos.append({"c": c, "m": med, "p": pct, "tope": _numero(f[i_top]) if i_top is not None else 0, "d": dias,
                           "hasta": (hasta.strftime("%d/%m") if hasta.year == hoy.year else hasta.strftime("%d/%m/%Y")) if hasta else "", "cond": f[i_con].strip() if i_con is not None else "",
                           "min": _numero(f[i_min]) if i_min is not None else 0, "fuente": f[i_fte].strip() if i_fte is not None else ""})
    print(f"{len(promos)} promos bancarias vigentes", file=sys.stderr)
    return promos

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", help="zip de SEPA ya descargado")
    ap.add_argument("--carpeta", help="carpeta ya extraída (con subcarpetas cadena-N)")
    ap.add_argument("--salida", default=os.path.join(RAIZ, "data.json"))
    ap.add_argument("--solo-promos", action="store_true", help="solo actualiza las promos del data.json existente")
    a = ap.parse_args()
    if a.solo_promos:
        data = json.load(open(a.salida, encoding="utf-8"))
        data["promos"] = leer_promos()
        data["modo_bancos"] = MODO_BANCOS
        json.dump(data, open(a.salida, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        return
    tmp = tempfile.mkdtemp(prefix="sepa-")
    try:
        carpeta = a.carpeta
        if not carpeta:
            z = a.zip or descargar(os.path.join(tmp, "sepa.zip"))
            if not zipfile.is_zipfile(z):
                sys.exit("El archivo descargado no es un .zip válido. Revisá que en Drive esté el zip de SEPA completo.")
            carpeta = os.path.join(tmp, "x")
            extraer(z, carpeta)
        agg, suc, fecha = agregar(carpeta)
        data = construir(agg, suc, fecha)
        data["promos"] = leer_promos()
        data["modo_bancos"] = MODO_BANCOS
        with open(a.salida, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        cuenta = {}
        for x in data["items"]:
            cuenta[x[3]] = cuenta.get(x[3], 0) + 1
        print(f"Listo: {len(data['items'])} productos, fecha {fecha}, sucursales {suc}", file=sys.stderr)
        print("Por pasillo:", cuenta, file=sys.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    main()
