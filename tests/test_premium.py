"""Bordeaux & Champagne PWA/UI smoke tests (no database migration)."""
from pathlib import Path
from PIL import Image
from jinja2 import Environment, FileSystemLoader
import json
import re

ROOT=Path(__file__).resolve().parents[1]

def test_premium_templates_parse():
    env=Environment(loader=FileSystemLoader(ROOT/'templates'))
    for path in (ROOT/'templates').glob('*.html'):
        env.parse(path.read_text(encoding='utf-8'))


def test_shelf_uses_real_icons_and_hides_ice_explanation():
    text=(ROOT/'templates/pantry.html').read_text(encoding='utf-8')
    assert "icon('chevron-down'" in text
    assert "icon('search'" in text
    assert 'Ice is always assumed available' not in text
    assert 'Missing garnishes and ice' not in text
    assert '⌄' not in text and '⌕' not in text
    assert 'name="category"' in text and 'name="ingredient"' in text


def test_navigation_icon_system_is_consistent():
    source=(ROOT/'templates/base.html').read_text(encoding='utf-8')
    for icon in ['house','glass-water','plus-circle','settings','moon','sun']:
        assert f"icon('{icon}'" in source
    for bad in '▦◫⚙☾☀': assert bad not in source
    assert 'premium.css' in source and 'theme-init.js' in source
    assert source.index('theme-init.js') < source.index('style.css')
    icons=(ROOT/'static/icons.svg').read_text(encoding='utf-8')
    for icon in ['house','glass-water','plus-circle','settings','moon','sun','chevron-down','search']:
        assert f'<symbol id="{icon}"' in icons


def test_color_system_and_styles_are_valid():
    css=(ROOT/'static/premium.css').read_text(encoding='utf-8')
    assert '#742E43' in css and '#F8F4EE' in css
    assert '#181619' in css and '#E9B7C6' in css
    assert 'font-family:Lato' in css
    assert 'env(safe-area-inset-bottom)' in css
    assert 'prefers-reduced-motion' in css
    assert css.count('{') == css.count('}')


def test_theme_metadata_and_pwa_cache_are_consistent():
    js=(ROOT/'static/theme-init.js').read_text(encoding='utf-8')
    assert 'cabinet-theme' in js
    assert 'prefers-color-scheme: dark' in js
    assert 'aria-label' in js
    assert 'theme-color' in js
    sw=(ROOT/'static/sw.js').read_text(encoding='utf-8')
    for path in ['premium.css','theme-init.js','icons.svg','app-icon.png','app-icon-512.png']:
        assert path in sw
    assert "url.pathname.startsWith('/static/')" in sw
    assert 'cabinet-assets-v7-atelier1' in sw
    manifest=json.loads((ROOT/'static/manifest.webmanifest').read_text(encoding='utf-8'))
    assert manifest['display']=='standalone'
    assert manifest['background_color']=='#F8F4EE'
    assert manifest['theme_color']=='#742E43'
    for icon,size in [('app-icon.png',192),('app-icon-512.png',512)]:
        with Image.open(ROOT/'static'/icon) as im:
            assert im.size==(size,size)
    assert '#742E43' in (ROOT/'static/icon.svg').read_text()
    assert '#2B2028' in (ROOT/'static/icon-dark.svg').read_text()
