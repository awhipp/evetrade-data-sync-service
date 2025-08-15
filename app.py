"""
Data Sync Service which pulls data from the EVE API and loads it into the Elasticsearch instance
"""

import asyncio
import logging
import os
import threading
import time
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from elasticsearch import Elasticsearch, helpers

from sync_service import citadel_data
from sync_service.market_data import MarketData

load_dotenv()

AWS_BUCKET: Optional[str] = os.getenv("AWS_BUCKET")
ES_ALIAS: Optional[str] = os.getenv("ES_ALIAS")
ES_HOST: Optional[str] = os.getenv("ES_HOST")

if not ES_HOST or not ES_ALIAS:
    raise EnvironmentError(
        "Missing required environment variables: ES_HOST and/or ES_ALIAS."
    )

es_client = Elasticsearch(ES_HOST)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Suppress Elasticsearch client logging
logging.getLogger("elasticsearch").setLevel(logging.WARNING)


# Function which pulls universeList.json file from S3
# and returns the regionID values as an array
def get_region_ids() -> List[int]:
    """
    Gets the region IDs from the universeList.json file
    """
    url = "https://evetrade.s3.amazonaws.com/resources/universeList.json"
    try:
        s3_file_json: Dict[str, Any] = requests.get(url, timeout=30).json()
    except Exception as e:
        logger.error(f"Failed to fetch region IDs: {e}")
        raise

    region_ids = {
        station["region"] for station in s3_file_json.values() if "region" in station
    }
    logger.info(f"Getting orders for {len(region_ids)} regions.")
    return list(region_ids)


def get_data(index_name: str, region_ids: List[int]) -> int:
    """
    Gets market data for a given region and saves it to Elasticsearch
    """
    threads: List[threading.Thread] = []
    order_count = 0

    # Ingest citadel orders synchronously (can be made async if needed)
    try:
        citadel_orders = citadel_data.get_citadel_orders()
        load_orders_to_es(index_name, citadel_orders, "Citadels")
        order_count += len(citadel_orders)
    except Exception as e:
        logger.error(f"Error loading citadel orders: {e}")

    def ingest_region(region_id: int):
        try:
            market_data = MarketData(region_id)
            orders = asyncio.run(market_data.execute_requests())
            if orders:
                load_orders_to_es(index_name, orders, region_id)
                nonlocal order_count
                order_count += len(orders)
        except Exception as e:
            logger.error(f"Error ingesting region {region_id}: {e}")

    for region_id in region_ids:
        t = threading.Thread(
            target=ingest_region,
            args=(region_id,),
            name=f"Ingesting Orders for {region_id}",
        )
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    return order_count


def create_index(index_name: str) -> str:
    """
    Creates the index for the data sync service
    """
    logger.info(f"Creating new index {index_name}")
    es_index_settings = {"settings": {}}
    es_client.indices.create(index=index_name, body=es_index_settings)
    return index_name


def load_orders_to_es(index_name: str, all_orders: List[dict], region_id: Any) -> None:
    """
    Loads a list of orders to the Elasticsearch instance
    """
    logger.info(
        f"Ingesting {len(all_orders)} orders from {region_id} into {index_name}"
    )
    if all_orders:
        helpers.bulk(es_client, all_orders, index=index_name, request_timeout=30)


def get_index_with_alias(alias: str) -> Optional[str]:
    """
    Returns the index name that the alias points to
    """
    logger.info(f"Getting index with alias {alias}")
    if es_client.indices.exists_alias(name=alias):
        return list(es_client.indices.get_alias(index=alias).keys())[0]
    return None


def update_alias(new_index: str, alias: str) -> None:
    """
    Updates the alias to point to the new index
    """
    logger.info(f"Updating alias {alias} to point to {new_index}")
    if new_index and alias:
        es_client.indices.update_aliases(
            body={
                "actions": [
                    {"remove": {"index": "*", "alias": alias}},
                    {"add": {"index": new_index, "alias": alias}},
                ]
            }
        )


def refresh_index(index_name: str) -> None:
    """
    Refreshes an index
    """
    logger.info(f"Refreshing index {index_name}")
    if index_name and es_client.indices.exists(index_name):
        es_client.indices.refresh(index=index_name)


def delete_index(index_name: str) -> None:
    """
    Deletes an index from the Elasticsearch instance
    """
    logger.info(f"Deleting index {index_name}")
    if index_name and es_client.indices.exists(index_name):
        es_client.indices.delete(index_name)


def delete_stale_indices(protected_indices: List[str]) -> None:
    """
    Loop through all indices and delete any that are not currently in use
    """
    indices = es_client.indices.get_alias(index="*")
    for index in indices:
        if index not in protected_indices:
            logger.info(f"Deleting stale index {index}")
            delete_index(index)


def main() -> None:
    """
    Executes the data sync process
    """
    start = time.time()
    now = datetime.now()

    try:
        index_name = f'market-data-{now.strftime("%Y%m%d-%H%M%S")}'
        logger.info(f"--Executing sync on index {index_name}")

        previous_index = get_index_with_alias(ES_ALIAS)
        protected = [i for i in [previous_index, index_name, "evetrade_jump_data"] if i]
        delete_stale_indices(protected)
        region_ids = get_region_ids()
        create_index(index_name)
        get_data(index_name, region_ids)
        update_alias(index_name, ES_ALIAS)
        refresh_index(ES_ALIAS)
        end = time.time()
        minutes = round((end - start) / 60, 2)
        logger.info(f"Completed in {minutes} minutes.")

    except Exception as general_exception:
        logger.error(
            f"Error ingesting data into {index_name}. Removing new index. Exception: {str(general_exception)}"
        )
        delete_index(index_name)
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical(f"An error occurred: {e}")
        traceback.print_exc()
        exit(1)
