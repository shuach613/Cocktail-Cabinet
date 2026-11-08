"""Cross-theme style and PWA brand regression checks. No app database needed."""
from pathlib import Path
import colorsys
import json
import re
from PIL import Image
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'static'


def rgb(hex_code):
    return tuple(int(hex_code[i:i+2], 16) / 255 for i in (1, 3, 5))


def luminance(code):
    c = rgb(code)
    c = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055)**2.4 for v in c]
    return .2126*c[0] + .7152*c[1] + .0722*c[2]


def contrast(a, b):
    x, y = sorted((luminance(a), luminance(b)), reverse=True)
    return (x + .05) / (y + .05)


def test_champagne_is_a_real_secondary_accent_across_both_modes():
    source = (STATIC / 'premium.css').read_text()
    assert '--champagne-button:#E8C994' in source
    assert '--champagne-button:#E9CA97' in source
    assert '--champagne-strong:#805D31' in source
    assert '--champagne-strong:#F0D6AD' in source
    assert '.button{border:1px solid var(--champagne-border)' in source
    assert '.nav-link.active{background:var(--champagne-surface)' in source
    for selector in ('.card-method', '.card-caption', '.step-num', '.shelf-generic',
                     '.settings-icon', '.bottom-nav a.active', '.match-icon.ready',
                     '.filter-button', '.detail-tags span', '.shelf-count'):
        assert selector in source
    # Contrast checked for both text and champagne CTA labels.
    assert contrast('#805D31', '#FFFCF8') >= 4.5
    assert contrast('#805D31', '#F2E3CC') >= 4.5
    assert contrast('#F0D6AD', '#272126') >= 4.5
    assert contrast('#442E20', '#C5A174') >= 4.5
    assert contrast('#442E20', '#E8C994') >= 4.5
    assert contrast('#342319', '#E9CA97') >= 4.5


def test_no_legacy_juniper_green_colors_in_styles_or_logo():
    for file in ('style.css', 'refinement.css', 'premium.css', 'icon.svg', 'icon-dark.svg'):
        content = (STATIC / file).read_text()
        for code in re.findall(r'#[0-9a-fA-F]{6}\b', content):
            hue, lightness, saturation = colorsys.rgb_to_hls(*rgb(code))
            assert not (90 < hue*360 < 185 and saturation > .12), (file, code)
    source = (STATIC / 'premium.css').read_text()
    assert '--good:#805F37' in source and '--good:#E4C18D' in source
    assert 'E8F2E9' not in source and '304C3C' not in source


def test_app_icon_and_favicon_always_use_champagne_palette():
    light = (STATIC / 'icon.svg').read_text()
    dark = (STATIC / 'icon-dark.svg').read_text()
    for svg in (light, dark):
        assert 'Cocktail shaker and glass' in svg
        assert '#183E32' not in svg and '#31463C' not in svg
    assert '#E3BE88' in light and '#E8C590' in dark
    with Image.open(STATIC / 'app-icon.png') as im:
        assert im.size == (192, 192)
    with Image.open(STATIC / 'app-icon-512.png') as im:
        assert im.size == (512, 512)
    js = (STATIC / 'theme-init.js').read_text()
    assert 'cabinet-favicon' in js and 'icon-dark.svg' in js and 'icon.svg' in js
    assert "'/static/' + (theme === 'dark' ? 'icon-dark.svg' : 'icon.svg') + '?v=5'" in js
    base = (ROOT / 'templates/base.html').read_text()
    assert 'id="cabinet-favicon"' in base
    assert "filename='app-icon.png',v=5" in base
    manifest = json.loads((STATIC / 'manifest.webmanifest').read_text())
    assert manifest['display'] == 'standalone'
    assert all('?v=5' in icon['src'] for icon in manifest['icons'])
    assert 'cabinet-assets-v6-polish2' in (STATIC / 'sw.js').read_text()


def test_existing_templates_still_parse_and_shelf_stays_quiet():
    env = Environment(loader=FileSystemLoader(ROOT / 'templates'))
    for template in (ROOT / 'templates').glob('*.html'):
        env.parse(template.read_text())
    shelf = (ROOT / 'templates/pantry.html').read_text()
    assert 'always assumed available' not in shelf
    assert 'ice' not in shelf.lower()
