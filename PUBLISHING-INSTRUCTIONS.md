# ArrNexus GitHub Publishing Toolkit

This toolkit reconstructs the existing ArrNexus source snapshots as Git history.

Detected version order:

1. `dmm-arr-router-v0.2` -> `v0.2`
2. `dmm-arr-router-v1.0` -> `v1.0`
3. `arrnexus-v2.0` -> `v2.0`
4. `arrnexus-v3.0-validated` -> `v3.0`
5. `arrnexus-v4.0` -> `v4.0`
6. `arrnexus-v5.0` -> `v5.0`
7. `arrnexus-v6.0` -> `v6.0`
8. `arrnexus-v6.1` -> `v6.1`
9. `arrnexus-v7.0` -> `v7.0.0-beta`

## Safety design

`--prepare` cannot push to GitHub.

It:

- reads the original version directories without modifying them
- copies each version into a separate clean work repository
- excludes `.env`, databases, runtime `/data`, caches, logs and similar state
- scans for private IPv4 addresses
- scans for local username/home-directory paths
- scans for hard-coded credential literals and common token formats
- blocks absolute symlinks
- commits each clean snapshot in version order
- creates annotated version tags
- puts the public README, `.gitignore` and `SECURITY.md` into the v7/main snapshot
- re-scans every committed revision
- optionally uses `gitleaks` as an additional scanner when it is installed
- stops without pushing

## Server usage

Copy/unzip this toolkit somewhere on the ArrNexus server, for example:

```bash
mkdir -p /opt/dmm-arr-router/github-toolkit
cd /opt/dmm-arr-router/github-toolkit
# place the four toolkit files here
chmod +x publish_arrnexus_history.sh
```

Prepare only:

```bash
./publish_arrnexus_history.sh --prepare
```

If the scan reports a finding, do not push. Review the report files generated next to the work repository.

After the reports are clean:

```bash
./publish_arrnexus_history.sh --push
```

The push stage scans the full history again and refuses to proceed if the remote repository already contains unrelated commits.
