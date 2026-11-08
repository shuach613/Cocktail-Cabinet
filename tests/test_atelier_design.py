"""Atelier design-system regression checks: brand, pages and responsive styles."""
from __future__ import annotations
import re
from pathlib import Path
import pytest
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "finishing.css").read_text(encoding="utf-8")
BASE = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")


def test_same_brand_palette_font_and_single_final_design_layer():
    premium = (ROOT / "static" / "premium.css").read_text(encoding="utf-8")
    assert "--accent:#742E43" in premium and "--champagne:#C5A174" in premium
    assert "--bg:#181619" in premium and "--champagne:#E4C18D" in premium
    assert "font-family:Lato" in premium
    assert 'fonts.googleapis.com/css2?family=Lato' in BASE
    assert "font-family:Lato" in CSS
    assert "finishing.css" in BASE and "finishing.css',v=3" in BASE
    # This is a derived system, not a new color palette.
    assert ":root{" in CSS and "--ui-radius-sm:" in CSS
    assert "--ui-surface-tinted:" in CSS
    assert not re.search(r'--(?:bg|surface|ink|muted|line|accent|champagne|brand-wine|focus)\s*:', CSS)
    assert not re.search(r'#[0-9a-fA-F]{3,8}\b', CSS)


def test_all_templates_parse():
    env = Environment(loader=FileSystemLoader(str(ROOT / "templates")))
    for template in (ROOT / "templates").glob("*.html"):
        env.parse(template.read_text(encoding="utf-8"))


@pytest.mark.parametrize("component", [
    ".desktop-sidebar", ".mobile-header", ".bottom-nav",
    ".discover-header", ".recipe-grid", ".recipe-card", ".card-visual",
    ".detail-hero", ".detail-columns", ".ingredient-line.is-on-shelf",
    ".ingredient-main", ".shelf-status-symbol .ui-icon",
    ".ingredient-shelf-summary", ".ingredient-shelf-meter",
    ".pantry-layout", ".shelf-branch", ".shelf-option",
    ".pantry-match", ".editor-form", ".category-dialog",
    ".settings-grid", ".settings-appearance", ".admin-two-col",
    ".member-row", ".management-item", ".management-photo-upload",
    ".api-layout", ".api-token-row", ".auth-panel", ".oauth-consent",
    ".invite-result-panel", ".secure-result-panel", ".account-actions",
])
def test_every_screen_uses_unified_component_styles(component):
    assert component in CSS, component


def test_small_screen_controls_and_accessible_layouts():
    assert CSS.count("{") == CSS.count("}")
    for point in (1250, 1030, 900, 760, 590, 420):
        assert f"@media(max-width:{point}px)" in CSS
    for rule in (
        "aspect-ratio:4/3", "grid-template-columns:20px minmax(0,1fr) auto",
        "min-height:44px", "prefers-reduced-motion:reduce",
        "color-mix(", ":focus-visible", ".skip-link:focus",
    ):
        assert rule in CSS
    assert '<a class="skip-link" href="#main-content">' in BASE
    assert 'id="main-content"' in BASE
    assert 'aria-current="page"' in BASE
    assert 'name="viewport"' in BASE


def test_oauth_and_one_time_secrets_share_the_visual_language():
    oauth = (ROOT / "templates" / "mcp_consent.html").read_text()
    assert "oauth-consent" in oauth
    assert "oauth-actions" in oauth
    assert 'style="' not in oauth
    assert 'name="decision" value="allow"' in oauth
    assert 'name="decision" value="deny"' in oauth
    assert 'name="_csrf"' in oauth
    assert 'name="{{ key }}" value="{{ value }}"' in oauth
    for name in ("invite_created.html", "api_token_created.html"):
        text = (ROOT / "templates" / name).read_text()
        assert "editor-panel" in text
        assert "data-copy-input=" in text


def test_recipies_and_shelf_checkmarks_are_separate_visual_signals():
    recipe = (ROOT / "templates" / "recipe.html").read_text()
    assert 'data-prep-item="{{ i.id }}"' in recipe
    assert 'data-shelf-state="' in recipe
    assert "ingredient-shelf-status" in recipe
    assert "ingredient-quantity" in recipe
    assert "shelf-status-label" in recipe
    assert "matched" in recipe and "owned_products" in recipe
    assert "data-prep-item" in CSS
    assert "ingredient-line.is-on-shelf" in CSS


def test_asset_caches_are_bumped_and_browser_audit_is_available():
    sw = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
    assert "cabinet-assets-v7-atelier1" in sw
    assert "/static/finishing.css?v=3" in sw
    assert (ROOT / "tools" / "visual_audit.py").exists()
    assert (ROOT / ".github" / "workflows" / "visual-review.yml").exists()
