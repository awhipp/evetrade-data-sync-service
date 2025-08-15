# EVE TRADE Data Sync Service

This project is a Python service that synchronizes trade data from EVE Online's ESI API. The service runs approximately every 5 minutes and updates both an Elasticsearch index and a Redis cache with relevant trade data.

Its primary purpose is to keep your search and cache layers up-to-date with the latest market information from EVE Online, enabling fast and accurate queries for trade data.

## Python Version

This project requires **Python 3.11+**. Please ensure you are using a compatible version.

## Environment Variables

The following environment variables must be set (see `.env.example`):

- `AWS_ACCESS_KEY`, `AWS_SECRET_KEY`, `AWS_BUCKET`: AWS S3 credentials for data storage.
- `ES_HOST`, `ES_TIMEOUT`, `ES_ALIAS`: Elasticsearch connection details.
- `ES_USER`, `ES_PASS`: (If required) Elasticsearch authentication.
- `ESI_CLIENT_ID`, `ESI_SECRET_KEY`, `ESI_REFRESH_TOKEN`: EVE Online ESI API credentials.

You can copy the example file and fill in your values:

```bash
cp .env.example .env
```

## Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/awhipp/evetrade-data-sync-service.git
   cd evetrade-data-sync-service
   ```

2. Install dependencies:
   ```bash
   poetry install
   ```

3. Update the `.env` file with your credentials and configuration.

## Running the Service

To start the main sync service:

```bash
poetry run python app.py
```

To run the data validation check:

```bash
poetry run python sync_service/check_data.py
```

## Precommit Hooks

This project uses [pre-commit](https://pre-commit.com/) to manage and maintain code quality. To enable pre-commit hooks, install the pre-commit package and run the following command:

```bash
poetry run pre-commit install
```

To run all hooks manually:

```bash
poetry run pre-commit run --all-files
```

## Continuous Integration & Automation

- **Linting and Formatting:** On every push and PR, code is checked with `black`, `isort`, and `flake8` ([see .github/workflows/ci.yml](.github/workflows/ci.yml)).
- **Data Sync:** The sync job runs every 5 minutes via GitHub Actions ([see .github/workflows/sync.yml](.github/workflows/sync.yml)).
- **Data Validation:** The check job ensures recent data is present in Elasticsearch ([see .github/workflows/check.yml](.github/workflows/check.yml)).

## ESIPy Fork for Python 3.10+

The original [ESIPy](https://github.com/ESIpy/ESIpy) library only supports Python versions up to 3.9 due to its dependency on [pyswagger](https://github.com/mission-liao/pyswagger), which is not maintained for newer Python versions.

This repository includes a copy of ESIPy (in the `esipy/` directory), updated and patched to support Python 3.10 and above. All changes are made here to ensure compatibility with future Python versions.

**Note:** If you are using Python 3.9 or below, you should use the original ESIPy package. Use this fork only if you require support for Python 3.10+.
