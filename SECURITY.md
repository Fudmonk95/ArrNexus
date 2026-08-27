# Security and Privacy

ArrNexus is intended to connect to services that commonly use API keys, tokens and other credentials.

## Never commit secrets

Do not commit:

- `.env` files containing credentials
- Arr application API keys
- Real-Debrid tokens
- DUMB, NzbDAV, Decypharr or InfiniDysk credentials
- Spotify client secrets or OAuth tokens
- webhook URLs containing authentication tokens
- email passwords
- ArrNexus databases or persistent `/data`
- raw diagnostic bundles or logs containing deployment-specific information

## Public examples

Documentation should use placeholders such as:

```text
<ARRNEXUS_HOST>
<YOUR_API_KEY>
<YOUR_TOKEN>
<YOUR_MEDIA_PATH>
```

Do not place real local usernames, private hostnames or IP addresses in public examples.

## Screenshots

Before publishing a screenshot, check:

- browser address bars
- API key fields
- terminal prompts
- filesystem paths
- usernames
- hostnames and IP addresses
- notification/webhook configuration

## If a secret is committed

Deleting the file in a later commit is **not enough** because it remains in Git history.

Immediately:

1. revoke or rotate the exposed credential
2. remove it from Git history
3. force-push the cleaned history if required
4. verify the repository again before making it public

## Reporting a security issue

During the beta period, avoid posting live credentials or unsanitised diagnostic bundles in public GitHub issues.
