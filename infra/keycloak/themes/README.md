# Keycloak login theme: `mate`

The Keycloak-hosted pages - email & password sign-in, forgot password, update
password, errors - rendered in the platform's own look (glass card, aurora, border
beam, light/dark), so signing in does not feel like leaving the product. Only the
**login** theme exists; the account console, the admin console and emails stay
Keycloak's own.

## How it is built

`parent=base`, with **one overridden template**. Keycloak's stock markup carries
`kc*Class` hooks (`kcFormGroupClass`, `kcButtonClass`, ...); `theme.properties` points
each of them at a `.mate-*` class in `css/mate.css`. So every page - also the ones
this theme never touches (OTP, WebAuthn, verify email, update profile, terms, ...) -
renders in the same look without a template of its own.

```
mate/login/
├── theme.properties        parent=base, the stylesheet, and the kc*Class -> .mate-* map
├── template.ftl            the layout: glass card, logo, title, alerts, theme toggle
├── messages/messages_en.properties   wording: "Email", "Sign in", ...
└── resources/
    ├── css/mate.css        everything visual
    ├── js/theme-init.js    blocking, in <head>: applies the saved light/dark theme
    ├── js/mate.js          the theme toggle (+ a back/forward-cache fix)
    └── img/favicon.svg     copy of apps/web/app/icon.svg
```

`template.ftl` keeps Keycloak's sections, scripts and form contract unchanged
(`url.loginAction`, field names, ids such as `kc-form-login`); only the markup around
them differs. The look mirrors `apps/web`: the tokens in `mate.css` are copied from
`app/globals.css`, the card is the one in `app/(auth)/login/page.tsx`, the beam and
aurora are `components/glass/*`, inputs and buttons are `components/ui/*`. When the
platform's tokens change, update `:root` / `.dark` at the top of `mate.css`.

Light/dark reads the same `theme` localStorage key next-themes writes. In production
Keycloak is served under `/auth` on the web app's origin, so both sides share the
choice; in local dev they sit on different ports and each remembers its own.

## How it is wired

- **realm**: `"loginTheme": "mate"` in `realm-export/flows-funds-realm.json` (fresh
  imports) and `apply-realm-hardening.sh` (the live realm, on every deploy - the JSON
  only imports into an empty DB).
- **container**: `docker-compose.yml` mounts this directory read-only at
  `/opt/keycloak/themes/mate`.
- **caching**: `compose.dev.yml` turns theme caching off (edit, reload the browser);
  `docker-compose.prod.yml` sets `KC_SPI_THEME_STATIC_MAX_AGE=3600`. Keycloak serves
  theme assets under a URL keyed to its own version, not to the theme, with a 30-day
  max-age by default - an updated theme would otherwise stay stale in browsers.
- A theme name that does not exist falls back to Keycloak's stock theme (with a log
  warning), so a typo degrades the look, not the login.

## Working on it

Run Keycloak with the dev overlay, which turns theme caching off, so an edit shows
on a browser reload (`make up-dev` starts it too, with the rest of the stack):

```
docker compose -f docker-compose.yml -f compose.dev.yml up -d keycloak
```

Then open a sign-in page without going through the app, so `DEMO_MODE` does not matter.
The realm requires PKCE, and the compose Keycloak pins its hostname to `keycloak`
(`KC_HOSTNAME`), so open it as `keycloak:8080` (needs `127.0.0.1 keycloak` in
`/etc/hosts`). On `localhost:8080` the page renders but signing in fails with "Cookie
not found", and the admin console never loads:

```
http://keycloak:8080/realms/flows-funds/protocol/openid-connect/auth
  ?client_id=flows-funds-web&response_type=code&scope=openid&state=x
  &redirect_uri=http://localhost:3000/api/auth/callback/keycloak
  &code_challenge=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa&code_challenge_method=S256
```

A Keycloak volume created before this theme existed still has the old realm (the JSON
only imports into an empty database): set *Realm settings -> Themes -> Login theme* to
`mate` in the admin console at `http://keycloak:8080/admin`.

To go through the app instead, turn `DEMO_MODE` off in `.env` (web and API both read
it), set `AUTH_SECRET`, and set `KEYCLOAK_ISSUER=http://keycloak:8080/realms/flows-funds`:
that is the issuer this Keycloak advertises, and `localhost:8080` (the `.env.example`
default) fails with `"issuer" property does not match the expected value`.

Other pages come from real flows: *Forgot password?* on the sign-in page, a user
with the `UPDATE_PASSWORD` required action (the seeded admin has it), a bad
`client_id` for the error page.

Messages go through `MessageFormat`: write a literal apostrophe as `''`.

## Upgrading Keycloak

The image is pinned to 26.0.x. On a new release:

1. Diff `template.ftl` against the stock `themes/base/login/template.ftl` of that
   version and carry over what changed (new sections, scripts).
2. Check that every `kc*Class` key the stock templates use has a value in
   `theme.properties` (`grep -rho 'properties\.kc[A-Za-z]*' themes/base/login`).
3. Click through sign-in, forgot password, update password and an error page.

## Not renamed

"Keycloak" is only the label this theme removes from the user's view. The technical
names stay: the Auth.js provider id `keycloak` (it is in the registered callback URL
`/api/auth/callback/keycloak`) and the IdP alias `keycloak-oidc`.
