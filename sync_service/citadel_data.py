"""
Pulls citadel data from ESI and stores it in the database.
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple
from uuid import uuid4

import requests
import urllib3
from dotenv import load_dotenv

from esipy import EsiSecurity

load_dotenv()
urllib3.disable_warnings()

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CLIENT_ID: str = os.getenv("ESI_CLIENT_ID", "TO BE ADDED")
SECRET_KEY: str = os.getenv("ESI_SECRET_KEY", "TO BE ADDED")
CALL_BACK: str = "https://evetrade.space"
USER_AGENT: str = (
    "EVETrade.space - https://evetrade.space - Structure Market Data Application"
)
REFRESH_TOKEN: str = os.getenv("ESI_REFRESH_TOKEN", "TO BE ADDED")

security = EsiSecurity(
    redirect_uri=CALL_BACK,
    client_id=CLIENT_ID,
    secret_key=SECRET_KEY,
    headers={"User-Agent": USER_AGENT},
)


def generate_auth_url(esi_security: EsiSecurity) -> None:
    """
    Generates an auth URL from the ESI endpoint (not used)
    """
    logger.info(
        esi_security.get_auth_uri(
            state=str(uuid4()),
            scopes=[
                "esi-universe.read_structures.v1",
                "esi-markets.structure_markets.v1",
            ],
        )
    )


def generate_token(esi_security: EsiSecurity) -> None:
    """
    Generates the access token from the ESI endpoint (not used)
    """
    logger.info(esi_security.auth("TO_ADD"))


def refresh_token(token: str) -> Dict[str, Any]:
    """
    Refreshes the access token from the ESI endpoint
    """
    security.update_token(
        {
            "access_token": "",  # leave this empty
            "expires_in": -1,  # seconds until expiry, so we force refresh anyway
            "refresh_token": token,
        }
    )
    return security.refresh()


def get_citadel_info() -> Dict[str, Any]:
    """
    Get all known Citadel Information
    """
    url = "https://evetrade.s3.amazonaws.com/resources/structureInfo.json"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch citadel info: {e}")
        raise


def get_citadel_data(
    access_token: str, citadel_id: str
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Pulls citadel data from ESI
    """
    page_idx = 0
    total_pages = 1
    citadel_orders: List[Dict[str, Any]] = []
    backoff_timer = 1
    rate_limit = 100

    while page_idx < total_pages:
        page_idx += 1
        url = (
            f"https://esi.evetech.net/latest/markets/structures/"
            f"{citadel_id}/?datasource=tranquility&page={page_idx}&token={access_token}"
        )
        try:
            response = requests.get(url, timeout=30, verify=False)
            if response.status_code == 200:
                data = response.json()
                total_pages = int(response.headers["X-Pages"])
                citadel_orders += data
                rate_limit = int(response.headers.get("X-Esi-Error-Limit-Remain", 100))
                if rate_limit < 10:
                    logger.warning(f"Rate Limit: {rate_limit}")
                    time.sleep(60)
            else:
                logger.error(f"Error: {response.status_code} - {response.text}")
                time.sleep(backoff_timer)
                backoff_timer *= 2
                break
        except Exception as e:
            logger.error(f"Exception fetching citadel {citadel_id}: {e}")
            time.sleep(backoff_timer)
            backoff_timer *= 2
            break

    return citadel_orders, rate_limit


def get_all_orders(access_token: str, citadels: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Get all citadel orders in parallel, respecting rate limits
    """
    logger.info(f"Processing Total Citadels: {len(citadels)}")
    citadel_orders: List[Dict[str, Any]] = []
    citadel_ids = list(citadels.keys())
    max_workers = min(10, len(citadel_ids))  # Tune as needed for rate limits

    def fetch_orders(cid: str) -> Tuple[str, List[Dict[str, Any]]]:
        orders, rate_limit = get_citadel_data(access_token, cid)
        if rate_limit < 10:
            logger.warning(f"Rate Limit: {rate_limit} (citadel {cid})")
            time.sleep(60)
        return cid, orders

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_cid = {executor.submit(fetch_orders, cid): cid for cid in citadel_ids}
        for idx, future in enumerate(as_completed(future_to_cid)):
            cid = future_to_cid[future]
            try:
                _, orders = future.result()
                citadel_orders += orders
            except Exception as e:
                logger.error(f"Error fetching orders for citadel {cid}: {e}")
            logger.info(
                f"-- Citadel Order Percentage: {round((idx + 1) / len(citadel_ids) * 100, 2)}%"
            )

    return citadel_orders


def enrich_orders(
    citadel_orders: List[Dict[str, Any]], citadels: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Enriches citadel orders with citadel information
    """
    for order in citadel_orders:
        order["citadel"] = True
        order["station_id"] = order["location_id"]
        del order["location_id"]
        citadel_info = citadels.get(str(order["station_id"]))
        if citadel_info:
            order["system_id"] = citadel_info["system_id"]
            order["region_id"] = citadel_info["region_id"]
        else:
            logger.warning(f"No citadel info for station_id {order['station_id']}")
    return citadel_orders


def find_best_orders(citadel_orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Ensure we get the best buy and sell orders for each citadel
    """
    best_orders: Dict[Any, Dict[Any, Dict[str, Dict[str, Any]]]] = {}
    for order in citadel_orders:
        station_id = order["station_id"]
        type_id = order["type_id"]
        if station_id not in best_orders:
            best_orders[station_id] = {}
        if type_id not in best_orders[station_id]:
            best_orders[station_id][type_id] = {}
        if order["is_buy_order"]:
            if "buy_order" not in best_orders[station_id][type_id] or (
                order["price"] > best_orders[station_id][type_id]["buy_order"]["price"]
            ):
                best_orders[station_id][type_id]["buy_order"] = order
        else:
            if "sell_order" not in best_orders[station_id][type_id] or (
                order["price"] < best_orders[station_id][type_id]["sell_order"]["price"]
            ):
                best_orders[station_id][type_id]["sell_order"] = order

    valid_orders: List[Dict[str, Any]] = []
    for station_id in best_orders:
        for type_id in best_orders[station_id]:
            if "buy_order" in best_orders[station_id][type_id]:
                valid_orders.append(best_orders[station_id][type_id]["buy_order"])
            if "sell_order" in best_orders[station_id][type_id]:
                valid_orders.append(best_orders[station_id][type_id]["sell_order"])
    return valid_orders


def get_citadel_orders() -> List[str]:
    """
    Main function to get citadel orders
    """
    start = time.time()
    citadels_info = get_citadel_info()
    try:
        token_info = refresh_token(REFRESH_TOKEN)
        access_token = token_info["access_token"]
    except Exception as e:
        logger.critical(f"Failed to refresh token: {e}")
        raise
    orders = get_all_orders(access_token, citadels_info)
    orders = enrich_orders(orders, citadels_info)
    orders = find_best_orders(orders)
    end = time.time()
    if orders:
        logger.info(f"Sample Order: {orders[0]}")
    logger.info(f"Time to pull Citadels: {end - start} seconds")
    orders = [json.dumps(record) for record in orders]
    return orders
