# Social login setup (Google / Microsoft / Facebook / LinkedIn)

The buttons are already on the Login / Sign Up page. A button starts working as soon as its two keys are added to `.env`
(restart the app after editing). Buttons without keys show "This social login is not set up yet".

**Redirect URI (give this to every provider):** `PUBLIC_URL` + `/auth/social/<provider>/callback`
e.g. `https://your-domain.com/auth/social/google/callback`

> Google / Microsoft / Facebook / LinkedIn do NOT accept a private address like `http://192.168.1.27:5000`.
> For testing use `http://localhost:5000` (set `PUBLIC_URL=http://localhost:5000`), for live use your https domain.

| Provider | Where to create the keys | .env lines |
|---|---|---|
| Google | console.cloud.google.com -> APIs & Services -> Credentials -> OAuth client ID (Web application) | `GOOGLE_CLIENT_ID=` `GOOGLE_CLIENT_SECRET=` |
| Microsoft | portal.azure.com -> App registrations -> New (accounts in any directory + personal) -> Certificates & secrets | `MICROSOFT_CLIENT_ID=` `MICROSOFT_CLIENT_SECRET=` |
| Facebook | developers.facebook.com -> Create app -> Facebook Login (Web) | `FACEBOOK_APP_ID=` `FACEBOOK_APP_SECRET=` |
| LinkedIn | linkedin.com/developers -> Create app -> Products: "Sign In with LinkedIn using OpenID Connect" | `LINKEDIN_CLIENT_ID=` `LINKEDIN_CLIENT_SECRET=` |

## How it behaves
- First social login creates the account; the customer is then asked for a **contact number** (mandatory) and optional company name.
- Same social account later = direct login.
- If the social e-mail already belongs to a password account, Google / LinkedIn (verified e-mails) are linked to it; Facebook / Microsoft are refused for safety and the customer is asked to login with the password.
- A social-only account can set a password any time with **Forgot password**.
- All users (password or social) are listed in **Admin -> Website Users** (search, CSV export, delete). Data is stored in MongoDB database `srj_bot`, collection `users`.
