"""
Service validation GitHub Action
"""

import os
import time
from typing import Any, Dict, List

from dotenv import load_dotenv
from elasticsearch import Elasticsearch, RequestsHttpConnection

load_dotenv()

ES_HOST = os.environ["ES_HOST"]
ES_TIMEOUT = int(os.environ.get("ES_TIMEOUT", 30))
ES_RETRY_ON_TIMEOUT = os.environ.get("ES_RETRY_ON_TIMEOUT", "true").lower() == "true"
ES_RETRIES = int(os.environ.get("ES_RETRIES", 10))
ES_INDEX = "market_data"
ONE_HOUR = 60 * 60


def create_es_client() -> Elasticsearch:
    """Create and return an Elasticsearch client with custom timeout settings."""
    return Elasticsearch(
        [ES_HOST],
        connection_class=RequestsHttpConnection,
        timeout=ES_TIMEOUT,
        max_retries=ES_RETRIES,
        retry_on_timeout=ES_RETRY_ON_TIMEOUT,
        http_auth=(
            os.environ.get("ES_USER", "user"),
            os.environ.get("ES_PASS", "password"),
        ),
        use_ssl=True,
        verify_certs=True,
        ssl_show_warn=False,
        # PoolManager is not used here; configure via connection/session if needed
    )


def get_recent_values(
    es_client: Elasticsearch, index_name: str
) -> List[Dict[str, Any]]:
    """
    Get the most recent values from the given index.
    """
    query = {"size": 1, "sort": {"issued": "desc"}, "query": {"match_all": {}}}
    try:
        hits = es_client.search(index=index_name, body=query)
        return hits["hits"]["hits"]
    except Exception as e:
        print(f"Error querying Elasticsearch: {e}")
        return []


def main() -> None:
    es_client = create_es_client()
    one_hour_ago = time.time() - ONE_HOUR
    print(
        f"Getting most recent document. 1 hour ago was: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(one_hour_ago))}"
    )
    results = get_recent_values(es_client, ES_INDEX)
    if not results:
        raise Exception("No documents found in Elasticsearch index.")
    last_order_time = int(results[0]["sort"][0]) / 1000
    print(
        f"Most recent document was from: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(last_order_time))}"
    )
    if last_order_time < one_hour_ago:
        raise Exception(
            "No new data ingested into Elasticsearch index for the last 60 minutes."
        )


if __name__ == "__main__":
    main()
