# EVE TRADE Data Sync Service

This project is a Python service that synchronizes trade data from EVE Online's ESI API. The service runs approximately every 5 minutes and updates both an Elasticsearch index and a Redis cache with relevant trade data.

Its primary purpose is to keep your search and cache layers up-to-date with the latest market information from EVE Online, enabling fast and accurate queries for trade data.

## Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/awhipp/evetrade-data-sync-service.git
   cd evetrade-data-sync-service
   ```

2. Install Dependencies:
   ```bash
   poetry install
   ```

3. Set up your environment variables:
   ```bash
   cp .env.example .env
   ```

4. Update the `.env` file with your AWS and Elasticsearch credentials.

5. Start the service:
   ```bash
   poetry run python app.py
   ```

## Precommit Hooks

This project uses [pre-commit](https://pre-commit.com/) to manage and maintain code quality. To enable pre-commit hooks, install the pre-commit package and run the following command:

```bash
poetry run pre-commit install
```

```bash
poetry run pre-commit run --all-files
```

## ESIPy Fork for Python 3.10+

The original [ESIPy](https://github.com/ESIpy/ESIpy) library only supports Python versions up to 3.9 due to its dependency on [pyswagger](https://github.com/mission-liao/pyswagger), which is not maintained for newer Python versions.

This module is a copy of ESIPy, updated and patched to support Python 3.10 and above. All changes are made here to ensure compatibility with future Python versions.

**Note:** If you are using Python 3.9 or below, you should use the original ESIPy package. Use this fork only if you require support for Python 3.10+.