# The Cocktail Cabinet

A **private, PWA-first cocktail recipe app** with a spacious desktop interface. Made to live on a Synology NAS (or any Docker host). Built with Flask, SQLite, server-rendered HTML, small progressive enhancements and the Lato font family.

## What it does

- **Private by design:** login required to see recipes, the shelf, photos or the ingredient catalogue. No public signup.
- **First-run setup:** create the first administrator using a secret setup key; the setup page closes permanently once an account exists.
- **Admin-controlled users:** create members with temporary passwords or generate **single-use, seven-day, email-bound invitations**. Invitee chooses their username/password. The link token is stored as a hash and becomes invalid after use or revocation. Optional SMTP email delivery; otherwise copy and send the link.
- **Share recipes:** signed-in members can add recipes and edit their own; the admin can edit any recipe. Ingredient amounts, steps, glassware, method, notes and an optional photo.
- **Multi-level ingredient classification:** an ingredient may have several tags (e.g. Elephant Orange & Cocoa Gin → Gin + London Dry Gin + Citrus Gin). Tags inherit broader ancestors. Users can add new categories beneath existing ones and classify new branded ingredients when adding a recipe. Existing classifications can be corrected from **Ingredients**.
- **My shelf:** tick generic categories, specific subtypes and/or named bottles. Stored per account, shared across your devices. See fully available recipes first, then ones with missing essentials. Ice and marked optional garnishes do not block matching.
- **22 initial recipes, 105 original ingredient lines:** imported from `Drinks_Brand_Names_Restored_Exact_Layout(1).xlsx` (quantities and spelling preserved, including one garnish with an unspecified amount). Preparation steps researched from linked references, with adaptation notes for personal drinks. The original workbook is not needed at runtime.
- **Search and filters:** by drink, ingredient, ingredient category, preparation method.
- **Mobile PWA:** installable over HTTPS with bottom navigation, appropriate icon, standalone mode; static assets cached, **private content never stored by the service worker**. Desktop sidebar and roomy card/grid layout.
- **Light/dark:** user switch, device default, persistent preference. Lato via Google Fonts with local system fallback.

## Synology setup

1. Create a **private** GitHub repository named `cocktail-cabinet` in the `shuach613` account (or change the image name everywhere if using a different owner/repo). Push these project files to `main`. GitHub Actions will run tests and publish `ghcr.io/shuach613/cocktail-cabinet:latest`. The package may initially be private — authenticate the NAS with a GitHub personal access token that has `read:packages` and access to the package, or change package visibility appropriately.
2. On the NAS, save `compose.yaml` in `/volume1/docker/cocktail-cabinet/` and copy `.env.example` to `.env` there. **Do not commit `.env`**.
3. Generate **two separate secrets** (e.g. `openssl rand -hex 32` twice) and set `SECRET_KEY` and `SETUP_TOKEN`. Keep both private.
4. Configure DSM reverse proxy to forward an HTTPS hostname to the NAS on port **1931**, using a valid TLS certificate. Set `COOKIE_SECURE=true` and `TRUST_PROXY=true` for this deployment. Restrict access while creating the first admin; without the setup token nobody can claim it.
5. From SSH in the Compose folder:

   ```sh
   sudo docker compose pull
   sudo docker compose up -d --remove-orphans
   ```

6. Visit `https://YOUR-HOSTNAME/setup`, enter the setup key from `.env`, and create your administrator. Then sign in, invite members, and install as a PWA from your device's browser.

If you only have an HTTP LAN address for testing, use `COOKIE_SECURE=false` and `TRUST_PROXY=false` **temporarily**. Do not put that configuration on the public internet. HTTPS is necessary for PWA installation and secure sessions.

### Local build (before GitHub image exists)

```sh
cp .env.example .env
# Fill in SECRET_KEY and SETUP_TOKEN in .env
docker compose -f compose.yaml -f compose.local.yaml up -d --build
```

**Updating** is `sudo docker compose pull && sudo docker compose up -d`. The `cabinet_data` named volume persists the database, accounts, invitations, photos, and pantry. Never use `docker compose down -v` for normal upgrades.

**Backup:** stop the container or run SQLite's `.backup` against `/data/cabinet.sqlite3`, then back up the database **and** `/data/uploads/`. If you use Synology snapshots, include the Docker named volume.

### SMTP (optional)

Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` in `.env`. SMTP uses STARTTLS by default (`SMTP_STARTTLS=true`). Without SMTP, the admin invitation page provides a link to copy. Failed SMTP sends do not cancel the generated invitation; use the displayed link.

## Taxonomy and matching semantics

- Categories form a tree. Example: `Spirits → Gin → London Dry Gin` and `Spirits → Gin → Citrus Gin`.
- An ingredient can be assigned to **multiple independent leaves**, plus their ancestors by inheritance. Elephant Orange & Cocoa is tagged `London Dry Gin` **and** `Citrus Gin` (the producer describes a citrus-rich London Dry-based gin).
- Selecting **Gin** on the shelf matches any recipe requiring a gin product. Selecting **London Dry Gin** matches London Dry products and recipes calling only for generic Gin; it does not claim to satisfy a citrus-specific branded ingredient unless that ingredient is tagged London Dry as well.
- Selecting a **specific bottle** satisfies a recipe calling for that bottle and generic recipes in the bottle's family. It does not imply you own another named brand.
- Missing essentials are calculated from recipe ingredients marked `Essential`, not `Ice`, `Garnish`, or `Optional`.
- An original entry listing multiple alternative vermouth brands is treated as a generic sweet red vermouth requirement while its original visible wording is preserved.
- Brand taxonomy is editable; do not apply automatic string substitutions or guess an unknown brand's type. New ingredient labels always require explicit classification.

## Research provenance

The `seed/recipes.json` file includes a `source_url` and explanatory `method_note` for **every one of the 22 recipes**. Research was used for the *preparation technique*, not to overwrite the original quantities. House variations without a verified exact recipe are clearly called adaptations, e.g. Gran Campericano and Amaretti. Gin & Tonic Salbei mentions sage in the name, but sage is absent from the source ingredients and is **not invented**. Hugo does not list ice in the original spreadsheet; the steps do not add it to the ingredient list.

## Security and operations

- Cookies use `HttpOnly`, `SameSite=Lax`, `Secure` by default. The secret key must be configured before startup.
- Form and JSON mutations require session-bound CSRF protection. Passwords are hashed with Werkzeug's modern password hashing. The setup key is checked before account creation.
- Invitation tokens are random and stored hashed; expired, used or revoked invitations cannot be reused.
- Uploads are re-encoded by Pillow with constrained dimensions, stored under random names, and served only to logged-in users.
- A strict-ish CSP prevents remote scripts; fonts are served via Google's CSS/font endpoints. To make fonts completely self-hosted, include a properly licensed Lato build separately.
- This compact app does not currently implement password reset emails or distributed login rate limits. Consider reverse-proxy rate limiting and strong passwords before internet exposure. Protect the NAS and keep backups.
- SQLite is meant for this small private membership; one Gunicorn worker with four threads uses WAL and busy timeout. This is *not* intended for high-concurrency public hosting.

## Development and tests

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
export DATA_DIR="$(mktemp -d)" SECRET_KEY="$(openssl rand -hex 32)" SETUP_TOKEN="$(openssl rand -hex 32)" COOKIE_SECURE=false TRUST_PROXY=false
pytest -q
python app.py
```

The automated tests validate the import count and taxonomy, first admin, login gate, one-time invitations, recipe authoring, and pantry matching. GitHub Actions runs them before building the Docker image.