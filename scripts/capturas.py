"""Capturas reales de la UI para docs/capturas (Fase F/H)."""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8123"
OUT = "docs/capturas"

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.goto(BASE + "/")
    pg.wait_for_selector("#form-login")
    pg.screenshot(path=f"{OUT}/01-login.png")
    pg.fill("#login-usuario", "m.vela")
    pg.fill("#login-clave", "demo-2026")
    pg.click("#btn-entrar")
    pg.wait_for_selector("#lista-convs .conv", timeout=15000)
    pg.wait_for_timeout(800)
    pg.screenshot(path=f"{OUT}/02-conversaciones.png")
    pg.click("#lista-convs .conv >> nth=0")
    pg.wait_for_selector("#historial .msg", timeout=10000)
    pg.wait_for_timeout(500)
    pg.screenshot(path=f"{OUT}/03-chat.png")
    pg.click("#dtab-fuentes")
    pg.screenshot(path=f"{OUT}/04-fuentes.png")
    # Admin (sesion separada con rol admin)
    a = b.new_page(viewport={"width": 1440, "height": 900})
    a.goto(BASE + "/?vista=admin")
    a.wait_for_selector("#form-login")
    a.fill("#login-usuario", "admin.andrade")
    a.fill("#login-clave", "demo-2026")
    a.click("#btn-entrar")
    a.wait_for_selector("#tab-usuarios table", timeout=15000)
    a.wait_for_timeout(500)
    a.screenshot(path=f"{OUT}/05-admin.png")
    # Métricas
    a.click('button[data-tab="metricas"]')
    a.wait_for_timeout(800)
    a.screenshot(path=f"{OUT}/06-metricas.png")
    # Burbuja
    pg.click("#btn-burbuja")
    pg.wait_for_selector("#burbuja:not([hidden])", timeout=5000)
    pg.wait_for_timeout(400)
    pg.screenshot(path=f"{OUT}/07-burbuja.png")
    # Móvil
    m = b.new_page(viewport={"width": 390, "height": 844})
    m.goto(BASE + "/")
    m.wait_for_selector("#form-login")
    m.fill("#login-usuario", "admin.andrade")
    m.fill("#login-clave", "demo-2026")
    m.click("#btn-entrar")
    m.wait_for_selector("#lista-convs .conv", timeout=15000)
    m.click("#btn-menu")
    m.wait_for_timeout(400)
    m.screenshot(path=f"{OUT}/08-movil.png")
    b.close()
print("OK capturas")
