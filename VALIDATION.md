# ArrNexus v13.1.3 validation

Validation for the v13.1.3 Magic Intake reliability/classification hotfix.

## Automated tests

The project test suite validates the existing v12/v13 recovery engines plus the v13.1.x Magic Intake features. v13.1.3 adds coverage for:

- persistent Movie / TV Series / Music source overrides;
- reclassification of an episode-looking source from TV to Movie;
- non-empty diagnostic text for timeout exceptions;
- safe recovery of blank pre-move failed rows;
- existing canonical grouping and pagination;
- exact episode marker handling;
- explicit verification states;
- Queue Janitor and Missing Media Orchestrator behaviour.

## Static validation

- all Python modules compile;
- all Jinja templates compile;
- `app/static/app.js` passes `node --check`;
- `docker-compose.yml` and `portainer-stack.yml` parse as YAML;
- shell helpers pass `bash -n`;
- `VERSION` is exactly `13.1.3`;
- Compose image references are `arrnexus:v13.1.3`.

## Deployment safety

- `/data/router.db` remains persistent;
- `/zurg_mnt` remains read-only;
- only `/zurg_magic` is writable;
- blank v13.1.2 failures are reset only when no destination path was ever recorded;
- import concurrency defaults to 3;
- media-type overrides are only allowed while the source still exists at top-level `__magic__`.
