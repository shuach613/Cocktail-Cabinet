# The Cocktail Cabinet

A **private, PWA-first cocktail recipe app** with a spacious desktop interface. Made to live on a Synology NAS (or any Docker host). Built with Flask, SQLite, server-rendered HTML, small progressive enhancements and the Lato font family.

## What it does

- **Private by design:** login required to see recipes, the shelf, photos or the ingredient catalogue. No public signup.
- **First-run setup without `.env`:** the container generates and persists private keys automatically. Retrieve the one-time setup key from the container logs, then create the first administrator in the browser. The setup page closes permanently once an account exists.
- **Admin-controlled users:** create members with temporary passwords or generate **single-use, seven-day, email-bound invitations**. Invitee chooses their username/password. The link token is stored as a hash and becomes invalid after use or revocation. Optional SMTP email delivery; otherwise copy and send the link.
- **Share recipes:** signed-in members can add recipes and edit their own; the admin can edit any recipe. Ingredient amounts, steps, glassware, method, notes and an optional photo.
- **Multi-level ingredient classification:** an ingredient may have several tags (e.g. Elephant Orange & Cocoa Gin → Gin + London Dry Gin + Citrus Gin). Tags inherit broader ancestors. Users can add new categories beneath existing ones and classify new branded ingredients when adding a recipe. Existing classifications can be corrected from **Ingredients**.
- **My shelf:** tick generic categories, specific subtypes and/or named bottles. Stored per account, shared across your devices. See fully available recipes first, then ones with missing essentials. Ice and marked optional garnishes do not block matching.
- **22 initial recipes, 105 original ingredient lines:** imported from `Drinks_Brand_Names_Restored_Exact_Layout(1).xlsx` (quantities and spelling preserved, including one garnish with an unspecified amount). Preparation steps researched from linked references, with adaptation notes for personal drinks. The original workbook is not needed at runtime.
- **Search and filters:** by drink, ingredient, ingredient category, preparation method.
- **Mobile PWA:** installable over HTTPS with bottom navigation, appropriate icon, standalone mode; static assets cached, **private content never stored by the service worker**. Desktop sidebar and roomy card/grid layout.
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
