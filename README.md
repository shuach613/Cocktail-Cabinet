# The Cocktail Cabinet

A **private, PWA-first cocktail recipe app** with a spacious desktop interface. Made to live on a Synology NAS (or any Docker host). Built with Flask, SQLite, server-rendered HTML, small progressive enhancements and the Lato font family.

## What it does

- **Private by design:** login required to see recipes, the shelf, photos or the ingredient catalogue. No public signup.
- **First-run setup without `.env`:** the container generates and persists private keys automatically. Retrieve the one-time setup key from the container logs, then create the first administrator in the browser. The setup page closes permanently once an account exists.
- **Admin-controlled users:** create members with temporary passwords or generate **single-use, seven-day, email-bound invitations**. Invitee chooses their username/password. The link token is stored as a hash and becomes invalid after use or revocation. Optional SMTP email delivery; otherwise copy and send the link.
- **Share recipes:** signed-in members can add recipes and add ingredients directly from the editor, including creating entirely new subcategories inside the Add Recipe flow. Recipe editing, photo replacement and deletion live under **Settings → Manage recipes** (members manage their own; admins can manage all).
- **Multi-level classification:** a bottle can live under several categories (Elephant Orange & Cocoa Gin → Gin, London Dry Gin and Citrus Gin). Brands appear within their categories, not in a separate list. The recipe editor can create a new ingredient *and* a new nested category, saving both atomically with the recipe. Admins organize shared categories, rename or merge labels under **Settings → Ingredients & categories**.
- **My shelf:** searchable nested families and named products, touch-friendly toggles and synced bottle choices across overlapping categories. Saved per account, with ready recipes, one-missing and several-missing groups. **Ice is always assumed available and never shown as a shelf choice.**
- **22 initial recipes, 105 original ingredient lines:** imported from the original spreadsheet, preserving the measurements and personal variations while presenting names, ingredients, and instructions in idiomatic English. Research sources are kept for internal provenance but are **not displayed** on recipe pages. Bundled photorealistic drink images accompany the seeded recipes; users can upload their own photos.
- **Search and filters:** by drink, ingredient, ingredient category, preparation method.
- **Mobile PWA:** installable over HTTPS with Discover, My Shelf, Add Recipe, and Settings in the thumb-friendly bottom bar; separate roomy desktop sidebar. Public static code/assets may be cached, while **private recipes and photos require login and are never cached by the service worker**.
- **Light/dark:** user switch, device default, persistent preference. Lato via Google Fonts with local system fallback.

## Synology deployment — one YAML file, no `.env`

Use [`compose.yaml`](compose.yaml) as the complete project configuration. **No `.env`, `.env.example`, pre-generated passwords, or manual secrets are required.** The app generates its session signing key and first-run administrator setup token inside the persistent `cabinet_data` volume.

### Deploy through Synology Container Manager

1. Open **Container Manager → Project → Create**. Give the project a name (e.g. `cocktail-cabinet`) and optionally set its path to `/volume1/docker/cocktail-cabinet`.
2. Select **Create `docker-compose.yml`**, paste the entire contents of [`compose.yaml`](compose.yaml), and deploy. You do **not** need to create an `.env` file or fill in any secret variables.
3. The container listens on NAS port **1931**. In **DSM → Login Portal → Advanced → Reverse Proxy**, forward your chosen **HTTPS** hostname to port `1931` on the NAS. Use a valid TLS certificate. Do not expose the HTTP port directly to the internet.
4. Open **Container Manager → Container → cocktail-cabinet → Log** (or run `sudo docker logs cocktail-cabinet` over SSH). Find the line **`One-time administrator setup key:`** and copy its value. This secret is visible only to whoever can inspect your container logs; **do not share it**.
5. Go to **`https://YOUR-HOSTNAME/setup`** and enter that one-time key, your name, email address and your chosen strong password. The first user becomes administrator. The setup key is invalidated and the setup route closes once the admin is created.
6. Sign in, create accounts or generate one-time invitation links from the administrator area, and install the PWA from your mobile browser.

**Private GitHub image:** The image is published by GitHub Actions as `ghcr.io/shuach613/cocktail-cabinet:latest`. Since the repository/package may be private, first log the NAS into GHCR using a GitHub personal access token with `read:packages` and package access, or set the package's visibility appropriately. This registry login is separate from the app's user login and does not require an `.env` file.

### Alternative: deploy from SSH using the same YAML

Save [`compose.yaml`](compose.yaml) as `/volume1/docker/cocktail-cabinet/compose.yaml` and run:

```sh
cd /volume1/docker/cocktail-cabinet
sudo docker compose pull
sudo docker compose up -d --remove-orphans
sudo docker logs cocktail-cabinet 2>&1 | grep 'One-time administrator setup key:'
```

Open `https://YOUR-HOSTNAME/setup` and finish administrator creation. If the key is not in the most recent log lines, use Container Manager's full log view. For existing installations that already have an administrator, **there is no setup key**; simply log in normally.

### Configuration and persistence

- **Port:** change `1931:8080` directly in `compose.yaml` if needed.
- **HTTPS:** `COOKIE_SECURE: "true"` and `TRUST_PROXY: "true"` are already set in `compose.yaml` for a Synology HTTPS reverse proxy. Only if testing on a trusted HTTP-only LAN, temporarily set both to `"false"` **in the YAML itself**, then restore HTTPS settings. An HTTPS connection is necessary for normal PWA installation.
- **SMTP invitations (optional):** uncomment and edit the `SMTP_*` entries in `compose.yaml` to send invitation emails. Otherwise, the administrator can copy each single-use invitation URL and send it manually. Keep SMTP passwords private; Docker/DSM project editors may be able to view configuration values.
- **Private session secret:** automatically stored as `/data/.session-secret`, never printed in logs. Keep the `cabinet_data` volume; the secret remains stable across container restarts and image upgrades.
- **One-time admin token:** stored as `/data/.admin-setup-token` until the first admin completes setup. It is printed to Docker logs while setup is pending and deleted after successful setup; it cannot grant another account afterward.
- **Updates:** run `sudo docker compose pull && sudo docker compose up -d` from your project folder or update the image in Container Manager. The `cabinet_data` named volume preserves accounts, recipes, photos, invitations, pantry, and instance secrets. Never use `docker compose down -v` for routine updates. Keep the same Compose project name to preserve the same named volume.
- **Backup:** back up the *entire* `cabinet_data` volume (including hidden `.session-secret`, SQLite database, and `/data/uploads/`). Stop the container for a consistent filesystem backup or use SQLite's `.backup` operation before copying while live.

**Upgrading from the earlier `.env`-based version:** Once the YAML is replaced, the app generates a persistent session secret automatically. Existing users and recipes remain in the same data volume, but active sessions may need to log in again because the cookie-signing secret has changed. The first-run admin screen will **not** reopen if users already exist. You can retire the old `.env` after confirming the new deployment works.

### Local development without `.env`

To build the Docker image locally using the same configuration, run:

```sh
docker compose -f compose.yaml -f compose.local.yaml up -d --build
```

For Python development outside Docker, set a writable local data path and HTTP-only flags for your own machine (shell variables, not a file). GitHub Actions automatically tests the code before publishing the image.


## Bordeaux & Champagne design system

The app uses **Lato** across desktop and mobile and offers coordinated light and dark palettes. Bordeaux is the identity color; **champagne is the visible complementary accent** for primary actions, selected navigation, category hierarchy, recipe badges, step counters, filter buttons, Settings icons, and ready-to-make indicators. Each theme uses contrast-adjusted champagne text and background tokens rather than reusing one gold shade everywhere. Success states deliberately avoid unrelated green styling.

The shaker-and-coupe pictogram uses Bordeaux/charcoal with champagne metalwork in both SVG variants, including its PWA icon sizes. The favicon responds to the selected app theme; the service worker cache and icon URLs are versioned so updates replace older assets. On some mobile platforms an already-installed PWA may need to be removed and reinstalled to refresh its home-screen icon.

No database migration or Compose update is required for this appearance-only change.

## Updating an existing installation

The new image runs a **one-time, transactional database migration** on first start, using the same persistent `cabinet_data` volume. It updates unchanged English translations and unmodified seed images without overwriting custom recipe fields or user-uploaded photos. Ingredient IDs, category mappings, user accounts, saved shelves, invitations and user-created recipes remain intact. The migration is version-marked so container restarts do not reapply it.

**Back up your named `cabinet_data` volume before upgrading.** Keep your current Compose YAML and project name; no new environment variables, `.env` file or administrator setup are necessary. The PR branch is not published as `:latest` until merged; review and merge it before updating Synology.

### Adding a new ingredient and subcategory

1. Tap **Add Recipe** and type the new ingredient, for example `Singleton`.
2. Select existing classifications or tap **Create a new category** (for example `Single Malt Scotch Whisky`, with `Scotch Whisky` as its parent).
3. Select the newly created category for that ingredient and save the recipe. The new category and ingredient are committed with the recipe; if validation fails, everything rolls back together.
4. Admins can reorganize categories, rename ingredients and merge duplicate labels via **Settings → Ingredients & categories**. The category tree is used throughout My Shelf and recipe search.

## Taxonomy and matching semantics

- Categories form a tree. Example: `Spirits → Gin → London Dry Gin` and `Spirits → Gin → Citrus Gin`.
- An ingredient can be assigned to **multiple independent leaves**, plus their ancestors by inheritance. Elephant Orange & Cocoa is tagged `London Dry Gin` **and** `Citrus Gin` (the producer describes a citrus-rich London Dry-based gin).
- Selecting **Gin** on the shelf matches any recipe requiring a gin product. Selecting **London Dry Gin** matches London Dry products and recipes calling only for generic Gin; it does not claim to satisfy a citrus-specific branded ingredient unless that ingredient is tagged London Dry as well.
- Selecting a **specific bottle** satisfies a recipe calling for that bottle and generic recipes in the bottle's family. It does not imply you own another named brand.
- Missing essentials are calculated from recipe ingredients marked `Essential`, not `Ice`, `Garnish`, or `Optional`.
- An original entry listing multiple alternative vermouth brands is treated as a generic sweet red vermouth requirement while its original visible wording is preserved.
- Brand taxonomy is editable; do not apply automatic string substitutions or guess an unknown brand's type. New ingredient labels always require explicit classification.

## Research provenance

Original preparation references remain in the seed/DB for maintenance and auditability, but are never rendered to members in the recipe interface. The English display translation respects the intended drink, exact amounts, branded products, and personal variations. For instance, Sage Gin & Tonic retains its name without silently adding sage to ingredients because none was listed in the original workbook.

## Security and operations

- Cookies use `HttpOnly`, `SameSite=Lax`, `Secure` by default. A strong session secret is generated automatically and kept in the persistent Docker volume; no manual secret configuration is necessary.
- Form and JSON mutations require session-bound CSRF protection. Passwords are hashed with Werkzeug's modern password hashing. A one-time setup key printed to administrator-only Docker logs must be supplied before the first admin account can be created. Restrict access to container logs and complete setup promptly.
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
DATA_DIR="$(mktemp -d)" COOKIE_SECURE=false TRUST_PROXY=false python app.py
```

Or use the local-build Compose override shown above. No `.env` file is involved. The automated tests cover the 22-recipe import, taxonomy, login-only access, first-run administrator setup (including the automatically generated key), single-use invitations, recipe authoring, and ingredient matching. GitHub Actions runs `python -m pytest -q` before building the Docker image.

### Recipe copy, brand labels and identity

The recipe interface uses English cocktail terminology, with sources retained only as internal editorial references. System-supplied recipe descriptions, quantities and preparation steps are refined through the versioned `recipe_english_copy_v3` migration. It changes only unchanged seed-owned fields, leaving customized recipes and quantities alone. Back up the persistent `/data` volume before deploying updates; no new admin setup is required.

Recipe ingredients display a branded product followed by its **most specific saved generic categories**, e.g. `Tanqueray London Dry Gin (London Dry Gin)` or `Elephant Gin Orange & Cocoa (London Dry Gin, Citrus Gin)`. These labels are computed at render time, so edits in Settings → Ingredients & Categories take effect across all recipes automatically. Unbranded ingredients are displayed plainly.

The web, favicon and PWA identity share a compact cocktail-shaker-and-coupe pictogram. Light and dark themes use harmonizing SVG variants, while the installed PWA has matching 192px and 512px PNG icons. The service worker's static-asset version is updated to refresh them; on some phones the home-screen icon may need the PWA to be reinstalled to update.

### Bordeaux & Champagne interface

The application uses the **Bordeaux & Champagne** design system in both light and dark modes, with Lato throughout. Mobile and desktop share the same SVG icon set, tactile navigation, and responsive recipe/shelf layouts. The shelf does not require selecting ice or display any ice explanation; matching behavior remains unchanged.

The installed PWA uses its own matching cocktail-shaker-and-glass icons, plus a same-origin, versioned static-asset cache. The browser theme follows the device initially, then remembers a user's explicit light/dark choice. No extra secrets, database migrations, Compose changes or third-party UI dependencies are introduced by this redesign.


## Per-user REST API (v1)

Cocktail Cabinet now includes a private JSON API at `/api/v1/`. Each request requires a personal, hashed bearer token issued under **Settings → API access** or **My account → API access**. Tokens are either **read-only** or **read/write**, are revocable, can expire, and always inherit the owner's website permissions. Session cookies cannot be used as API credentials. No new .env, Docker ports, databases or setup steps are needed.

- **Recipes:** `GET /api/v1/recipes` (paginated; `page`, `per_page`, `q`, `technique`), `GET /api/v1/recipes/{id}`, `POST /api/v1/recipes`, `PATCH/PUT /api/v1/recipes/{id}`, `DELETE /api/v1/recipes/{id}`.
- **Private recipe images:** `GET /api/v1/recipes/{id}/image?download=1` (binary WebP), `POST /api/v1/recipes/{id}/image` (multipart form with file field `image`; JPG/PNG/WebP up to 4MB), `DELETE /api/v1/recipes/{id}/image`. All three require bearer authentication; uploading/deleting also requires write scope and recipe ownership or admin.
- **Ingredients/categories:** `GET /api/v1/ingredients` and `/categories`; `POST /api/v1/ingredients` supports a new branded ingredient plus new nested categories transactionally. Administrators can additionally rename/reclassify ingredients and create/move categories through their API endpoints.
- **Integration helpers:** `GET /api/v1/me`, `GET /api/v1/changes` (collection revision), `GET /api/v1/openapi.json` (full authenticated OpenAPI 3.0 documentation).
- **Visibility:** API and browser edits immediately affect the shared SQLite database. Open, visible recipe/collection pages in the PWA/desktop browser check for revisions every **5 seconds** and automatically update, without a manual reload. When the user has unsaved shelf selections, a refresh notice preserves their input.
- **Persistence:** API tokens, users, recipes and photos survive ordinary Docker image updates in the original `cabinet_data` volume. New tables and revision triggers are created automatically and idempotently. Back up the volume before upgrading.

Example:

```bash
curl -H 'Authorization: Bearer YOUR_PERSONAL_TOKEN' \
  'https://cocktails.example.com/api/v1/recipes?page=1&per_page=50'

curl -L -H 'Authorization: Bearer YOUR_PERSONAL_TOKEN' \
  'https://cocktails.example.com/api/v1/recipes/1/image?download=1' -o cocktail.webp

curl -X POST -H 'Authorization: Bearer YOUR_WRITE_TOKEN' \
  -F 'image=@cocktail.jpg' 'https://cocktails.example.com/api/v1/recipes/23/image'
```

Use HTTPS and keep tokens in an integration's secrets store, never in query strings. Image URLs in JSON responses are private and must be fetched with the same Authorization header. The PWA service worker continues to cache only public static files, not private API, photo or recipe responses.

## ChatGPT / Model Context Protocol connector

Cocktail Cabinet now exposes a private Streamable HTTP MCP endpoint at `/mcp`, using
existing per-user API keys for compatible MCP clients and secure OAuth 2.1 account
linking for ChatGPT. The connection offers recipe CRUD, classification browsing,
photo downloads/uploads, and the same live PWA synchronization as the REST API.

See [MCP connector setup and security](docs/MCP_CONNECTOR.md) for step-by-step
Synology, ChatGPT and bearer-key client instructions. No extra container, port,
`.env` file or database reset is necessary. **ChatGPT write-enabled MCP support
is subject to account/workspace availability.** Do not paste API keys into chats.
