# ArrNexus Documentation Audit

This file is generated from `app/main.py` routes and the same `app/help_catalog.py` mapping used by the contextual Help button.

- Application routes/actions audited: **120**
- Help topics: **43**
- Primary private pages receive a contextual Help link from the application shell.
- Public/auth/bootstrap pages are documented in the Help Centre and user guide even when they do not use the private shell.

| Method | Route | Handler | Help topic |
| --- | --- | --- | --- |
| GET | `/api/health` | `health` | `getting-started` |
| GET | `/login` | `login_page` | `authentication` |
| GET | `/setup` | `setup_page` | `onboarding` |
| POST | `/setup` | `setup_create` | `onboarding` |
| GET | `/forgot-password` | `forgot_password_page` | `password-recovery` |
| POST | `/forgot-password` | `forgot_password` | `password-recovery` |
| GET | `/reset-password` | `reset_password_page` | `password-recovery` |
| POST | `/reset-password` | `reset_password` | `password-recovery` |
| POST | `/login` | `login` | `authentication` |
| GET | `/logout` | `logout` | `authentication` |
| GET | `/help` | `help_centre` | `getting-started` |
| GET | `/` | `public_landing` | `getting-started` |
| GET | `/download/latest` | `public_release_download` | `release-management` |
| GET | `/download/latest.sha256` | `public_release_sha256` | `release-management` |
| GET | `/api/public/release` | `public_release_metadata` | `release-management` |
| GET | `/dashboard` | `dashboard` | `dashboard` |
| POST | `/api/dashboard/refresh` | `refresh_dashboard` | `dashboard` |
| GET | `/inbox` | `inbox` | `dmm-inbox` |
| GET | `/item` | `item_detail` | `item-review` |
| POST | `/item/language-check` | `item_language_check` | `item-review` |
| POST | `/item/state` | `item_state` | `item-review` |
| POST | `/bulk-import` | `bulk_import` | `dmm-inbox` |
| POST | `/import` | `single_import` | `dmm-inbox` |
| GET | `/jobs` | `jobs_page` | `jobs` |
| GET | `/jobs/{job_id}` | `job_page` | `jobs` |
| GET | `/api/jobs/{job_id}` | `job_api` | `jobs` |
| GET | `/api/jobs-active` | `active_jobs_api` | `jobs` |
| GET | `/logs` | `logs_page` | `logs` |
| GET | `/api/logs/external` | `api_external_logs` | `logs` |
| POST | `/undo/{import_id}` | `undo_import` | `dmm-inbox` |
| GET | `/libraries` | `libraries_page` | `libraries` |
| GET | `/browser` | `browser_page` | `libraries` |
| GET | `/browser/file` | `browser_file` | `libraries` |
| GET | `/problems` | `problems_page` | `problem-centre` |
| GET | `/maintenance` | `maintenance_page` | `maintenance` |
| POST | `/maintenance/repair` | `repair_link` | `maintenance` |
| GET | `/rules` | `rules_page` | `routing` |
| POST | `/rules/add` | `rule_add` | `routing` |
| POST | `/rules/delete/{rule_id}` | `rule_delete` | `routing` |
| GET | `/arrs` | `arrs_page` | `connections` |
| POST | `/settings/connection` | `save_connection_route` | `connections` |
| POST | `/media-servers/custom` | `save_custom_media_server_route` | `media-servers` |
| POST | `/media-servers/custom/{media_id}/delete` | `delete_custom_media_server_route` | `media-servers` |
| GET | `/profile` | `profile_page` | `profile` |
| POST | `/profile` | `profile_save` | `profile` |
| GET | `/settings` | `settings_page` | `settings` |
| POST | `/settings/path-root` | `settings_path_root` | `settings` |
| POST | `/settings/mount/add` | `settings_mount_add` | `settings` |
| POST | `/settings/mount/delete/{mount_id}` | `settings_mount_delete` | `settings` |
| GET | `/music/settings` | `music_settings_page` | `music-api-settings` |
| POST | `/music/settings` | `music_settings_save` | `music-api-settings` |
| POST | `/settings/music-providers` | `settings_music_providers` | `settings` |
| POST | `/settings/general` | `settings_general` | `settings` |
| POST | `/settings/users/add` | `settings_user_add` | `settings` |
| POST | `/settings/users/delete/{user_id}` | `settings_user_delete` | `settings` |
| POST | `/settings/language-guard` | `settings_language_guard` | `language-guard` |
| POST | `/settings/policy` | `settings_policy` | `settings` |
| POST | `/settings/acquisition` | `settings_acquisition` | `settings` |
| POST | `/settings/notifications` | `settings_notifications` | `notifications` |
| POST | `/settings/notifications/test` | `settings_notifications_test` | `notifications` |
| POST | `/settings/users/access/{user_id}` | `settings_user_access` | `settings` |
| POST | `/settings/backup` | `settings_backup` | `backups` |
| GET | `/settings/backup/{name}` | `settings_backup_download` | `backups` |
| GET | `/settings/export-config` | `settings_export_config` | `backups` |
| POST | `/settings/import-config` | `settings_import_config` | `backups` |
| GET | `/diagnostics/download` | `diagnostics_download` | `backups` |
| POST | `/settings/update-repo` | `settings_update_repo` | `updates` |
| GET | `/api/update-check` | `api_update_check` | `updates` |
| GET | `/timeline` | `timeline_page` | `timeline` |
| POST | `/settings/provider-plugin` | `settings_provider_plugin` | `providers-sdk` |
| GET | `/ecosystem` | `ecosystem_page` | `ecosystem` |
| POST | `/ecosystem/save` | `ecosystem_save` | `ecosystem` |
| POST | `/ecosystem/plugin` | `ecosystem_plugin` | `ecosystem` |
| GET | `/api/ecosystem` | `api_ecosystem` | `ecosystem` |
| GET | `/infinidysk` | `infinidysk_page` | `infinidysk` |
| GET | `/api/infinidysk/live` | `infinidysk_live` | `infinidysk` |
| POST | `/infinidysk/action` | `infinidysk_action` | `infinidysk` |
| GET | `/decypharr` | `decypharr_page` | `decypharr` |
| GET | `/indexers` | `indexers_page` | `indexers` |
| POST | `/indexers/{indexer_id}` | `indexer_update` | `indexers` |
| GET | `/quality-lab` | `quality_lab_page` | `quality-lab` |
| GET | `/self-healing` | `self_healing_page` | `self-healing` |
| POST | `/self-healing/settings` | `self_healing_settings` | `self-healing` |
| POST | `/self-healing/search` | `self_healing_search` | `self-healing` |
| GET | `/queue` | `queue_page` | `queue` |
| GET | `/discover` | `discover_page` | `discover` |
| POST | `/discover/add` | `discover_add_route` | `discover` |
| GET | `/scraping` | `scraping_page` | `acquisition` |
| GET | `/api/scraping` | `scraping_api` | `acquisition` |
| GET | `/debrid` | `debrid_page` | `debrid-dmm` |
| POST | `/debrid/add-smart-show` | `debrid_add_smart_show` | `debrid-dmm` |
| POST | `/debrid/add-missing-show` | `debrid_add_missing_show` | `debrid-dmm` |
| POST | `/debrid/add-release` | `debrid_add_release` | `debrid-dmm` |
| POST | `/debrid/connect` | `debrid_connect` | `debrid-dmm` |
| GET | `/debrid/auth` | `debrid_auth_page` | `debrid-dmm` |
| GET | `/api/debrid/poll` | `debrid_poll` | `debrid-dmm` |
| POST | `/debrid/disconnect` | `debrid_disconnect` | `debrid-dmm` |
| GET | `/music` | `music_page` | `music-hub` |
| GET | `/music/spotify/connect` | `spotify_connect` | `spotify` |
| GET | `/music/spotify/callback` | `spotify_callback` | `spotify` |
| POST | `/music/spotify/disconnect` | `spotify_disconnect` | `spotify` |
| GET | `/music/artist` | `music_artist_page` | `music-hub` |
| POST | `/music/add-result` | `music_add_result` | `music-hub` |
| POST | `/music/add-artist` | `music_add_artist` | `music-hub` |
| POST | `/music/search-album` | `music_search_album` | `music-hub` |
| GET | `/about` | `about_page` | `getting-started` |
| GET | `/onboarding` | `onboarding_page` | `onboarding` |
| POST | `/onboarding/finish` | `onboarding_finish` | `onboarding` |
| GET | `/providers` | `providers_page` | `providers` |
| POST | `/providers/{provider_id}` | `provider_save_route` | `providers` |
| GET | `/readiness` | `readiness_page` | `readiness` |
| GET | `/aiostreams` | `aiostreams_page` | `aiostreams` |
| POST | `/aiostreams/save` | `aiostreams_save` | `aiostreams` |
| POST | `/aiostreams/verify` | `aiostreams_verify` | `aiostreams` |
| POST | `/aiostreams/preview` | `aiostreams_preview` | `aiostreams` |
| POST | `/aiostreams/apply` | `aiostreams_apply` | `aiostreams` |
| POST | `/aiostreams/rollback` | `aiostreams_rollback` | `aiostreams` |
| GET | `/aiostreams/search` | `aiostreams_search_page` | `aiostreams` |
| GET | `/api/aiostreams/status` | `aiostreams_status_api` | `aiostreams` |
| GET | `/api/aiostreams/search` | `aiostreams_search_api` | `aiostreams` |

## Release rule

A new primary page or materially new user-facing workflow must add or update Help coverage in `app/help_catalog.py`. `validate.py` checks this mapping so documentation is part of the release gate rather than a follow-up task.
