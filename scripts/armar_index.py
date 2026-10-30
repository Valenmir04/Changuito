"""Genera el index.html para GitHub Pages: agrega la cabecera que necesitan los navegadores
(codificación, ajuste al ancho del celular, color de la barra) al archivo de la app.
Uso: python3 armar_index.py <app.html> <salida index.html>"""
import sys
CABECERA = '''<!doctype html>
<html lang="es">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#7A1F2B">
<link rel="icon" href="icon.svg" type="image/svg+xml">
<link rel="icon" href="favicon-32.png" type="image/png" sizes="32x32">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<meta name="apple-mobile-web-app-title" content="Changuito">
<meta name="description" content="Armá tu lista del súper y compará precios reales de Coto, Día, Carrefour, Jumbo, Disco, Vea y ChangoMás en CABA, con las promos bancarias de cada día.">
<style>html{-webkit-text-size-adjust:100%;text-size-adjust:100%}:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>
'''
app = open(sys.argv[1], encoding="utf-8").read()
if app.startswith(CABECERA):  # ya tenía la cabecera: la reemplazamos por la actual
    app = app[len(CABECERA):]
elif app.lstrip().lower().startswith("<!doctype"):
    sys.exit("El archivo ya tiene otra cabecera; revisalo a mano.")
open(sys.argv[2], "w", encoding="utf-8").write(CABECERA + app)
