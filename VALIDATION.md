# ArrNexus v9.4.0-beta Validation Report

Release target: **ArrNexus 9.4.0-beta**

The v9.4 validator executes the complete retained chain first: v9.3 → v9.2 → v9.1 → v9 → v8 → v7.

It then validates the documentation release layer:

- public `/help` Help Centre
- contextual Help mapping for every primary private page
- 43 structured setup/use/troubleshooting topics covering 120 routes/actions
- detailed Spotify callback/OAuth setup guidance
- Music API Settings callback display and Help link
- generated `docs/USER_GUIDE.md`
- generated `docs/DOCUMENTATION_AUDIT.md`
- public landing Help entry
- v9.4 static/service-worker cache markers
- real Jinja compilation and Python compilation

The release remains beta until the documentation wording and live integrations are exercised by external users. Third-party APIs can change; upstream-specific requirements should be verified against the provider's current official documentation when troubleshooting.
