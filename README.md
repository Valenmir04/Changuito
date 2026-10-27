# Changuito

Comparador de precios de supermercados de CABA con datos abiertos de SEPA (Secretaría de Comercio, licencia CC BY 4.0).

## Qué hay en esta carpeta

| Archivo | Para qué sirve |
|---|---|
| `index.html` | La app (lo que ve la gente). |
| `data.json` | Los precios. Se reemplaza solo todos los días. |
| `scripts/actualizar.py` | Baja el archivo de SEPA, filtra CABA, clasifica en pasillos y genera `data.json`. |
| `scripts/frecuentes.json` | Los productos de "Más buscados" y sus marcas. Se puede editar. |
| `.github/workflows/actualizar.yml` | Le dice a GitHub que corra el script todos los días. |
| `requirements.txt` | Lo que necesita el script para funcionar. |

## Publicarlo (una sola vez, unos 20 minutos, gratis)

1. Creá una cuenta en https://github.com (si no tenés).
2. Arriba a la derecha: **+ → New repository**. Nombre: `changuito`. Marcá **Public**. Tocá **Create repository**.
3. En el repositorio nuevo: **Add file → Upload files**. Arrastrá `index.html`, `data.json`, `requirements.txt`, `README.md` y la carpeta `scripts`. Tocá **Commit changes**.
4. La carpeta `.github` a veces no se sube arrastrando (es una carpeta oculta). Creala a mano: **Add file → Create new file**, escribí como nombre `.github/workflows/actualizar.yml`, pegá el contenido de ese archivo y tocá **Commit changes**.
5. **Settings → Actions → General**: en "Workflow permissions" elegí **Read and write permissions** y guardá.
6. **Settings → Pages**: en "Source" elegí **Deploy from a branch**, rama **main**, carpeta **/ (root)**, y guardá. En uno o dos minutos aparece arriba la dirección del sitio (algo como `https://tu-usuario.github.io/changuito/`).
7. Probá la actualización: **Actions → Actualizar precios → Run workflow**. Tarda unos 10 minutos. Si termina en verde, ya está: se va a repetir sola todos los días a las 10:17.

## Promos bancarias

Están en `scripts/promos.csv`. Todos los lunes, una tarea programada de Claude busca las promos vigentes de las 7 cadenas, actualiza ese archivo y avisa para revisarlo.

Columnas: **Supermercado** (uno o varios: "Jumbo, Disco y Vea", o "Todos"), **Medio de pago**, **Descuento %**, **Tope $** (vacío = sin tope), **Compra mínima $**, **Días** ("Jueves", "Martes, Sábado" o "Todos"), **Vigente hasta** (dd/mm/aaaa; las vencidas se ocultan solas), **Condiciones** y **Fuente**.

Para corregir una promo a mano: abrí `scripts/promos.csv`, tocá el lápiz, editá la fila y guardá con **Commit changes**. Los cambios llegan al sitio en la próxima actualización diaria (o con **Run workflow**).

Las promos de MODO valen para tarjetas de Galicia, Santander, BBVA, Macro, Nación, Ciudad, ICBC, Supervielle, Credicoop, Comafi y Columbia (lista en `MODO_BANCOS`, dentro de `scripts/actualizar.py`).

## Si algo falla

- Si la actualización termina en rojo, abrí el paso que falló, copiá el mensaje de error y pasáselo a Claude.
- Si querés usar tu dominio propio: **Settings → Pages → Custom domain**.

## Cambiar cosas

- Productos de "Más buscados": editá `scripts/frecuentes.json` (cada marca es un nombre y su código de barras).
- Pasillos: las reglas están en `PASILLOS`, dentro de `scripts/actualizar.py`.
- Cadenas incluidas: `CADENAS`, en el mismo archivo.
