"""
market_data module is a helper module for other EVETrade functions
"""

import asyncio
import json
import sys
from collections import defaultdict
from typing import Any, Dict, List

import aiohttp

ESI_ENDPOINT: str = "https://esi.evetech.net"
ERROR_LIMIT_THRESHOLD: int = 20
NON_STATION_ID_THRESHOLD: int = 99999999

if (
    sys.version_info[0] == 3
    and sys.version_info[1] >= 8
    and sys.platform.startswith("win")
):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class MarketData:
    """
    Market Data class for a given region, order_type, and set of station_ids (optional)
    """

    region: int
    orders: List[dict]
    page_count: int
    backoff: int

    def __init__(self, region: int) -> None:
        self.region = region
        self.orders: List[dict] = []
        self.page_count: int = -1
        self.backoff: int = 1

    def __repr__(self) -> str:
        """
        String representation of a MarketData class
        """
        return json.dumps({"region": self.region})

    def construct_next_esi_endpoint(self, idx: int) -> str:
        """
        Constructs the ESI endpoint for a given region, order_type, and page
        """
        return (
            f"{ESI_ENDPOINT}/latest/markets/{self.region}"
            f"/orders/?datasource=tranquility&order_type=all"
            f"&page={idx}"
        )

    async def get_initial_market_data(
        self, session: aiohttp.ClientSession, url: str
    ) -> None:
        """
        Gets an initial page of market data (asynchronously) in order to get the number of pages
        """
        async with session.get(url) as response:
            self.orders.extend(await response.json())
            self.page_count = int(response.headers["x-pages"])
            limit_remain = int(response.headers["X-Esi-Error-Limit-Remain"])
            if limit_remain < ERROR_LIMIT_THRESHOLD:
                print(f"WARNING: ESI limit remaining is {limit_remain}")
                await asyncio.sleep(self.backoff)
                self.backoff *= 2

    @staticmethod
    async def get_market_data(session: aiohttp.ClientSession, url: str) -> Any:
        """
        Asynchronously requests the market data for a given ESI page
        """
        async with session.get(url) as resp:
            limit_remain = int(resp.headers["X-Esi-Error-Limit-Remain"])
            if limit_remain < ERROR_LIMIT_THRESHOLD:
                print(f"WARNING: ESI limit remaining is {limit_remain}")
                await asyncio.sleep(3)
            return await resp.json()

    async def execute_requests(self) -> List[str]:
        """
        Executes all requests for a given market data class
        """
        async with aiohttp.ClientSession() as session:
            await self.get_initial_market_data(
                session, self.construct_next_esi_endpoint(1)
            )

            tasks: List[asyncio.Future] = []
            for idx in range(2, self.page_count + 1):
                url = self.construct_next_esi_endpoint(idx)
                tasks.append(asyncio.ensure_future(self.get_market_data(session, url)))

            all_orders = await asyncio.gather(*tasks)
            for order_page in all_orders:
                if isinstance(order_page, list):
                    self.orders.extend(order_page)
                else:
                    print(f"Not valid order_page: {order_page}")

        best_orders: Dict[int, Dict[int, Dict[str, dict]]] = defaultdict(
            lambda: defaultdict(dict)
        )

        for order in self.orders:
            if order.get("location_id", 0) > NON_STATION_ID_THRESHOLD:
                continue
            order["citadel"] = False
            station_id = order.get("location_id")
            type_id = order.get("type_id")
            if station_id is None or type_id is None:
                continue

            if order.get("is_buy_order"):
                buy_order = best_orders[station_id][type_id].get("buy_order")
                if not buy_order or order["price"] > buy_order["price"]:
                    best_orders[station_id][type_id]["buy_order"] = order
            else:
                sell_order = best_orders[station_id][type_id].get("sell_order")
                if not sell_order or order["price"] < sell_order["price"]:
                    best_orders[station_id][type_id]["sell_order"] = order

        valid_orders: List[dict] = []
        for station_id, type_dict in best_orders.items():
            for type_id, orders in type_dict.items():
                if "buy_order" in orders:
                    order = orders["buy_order"]
                    order["station_id"] = order["location_id"]
                    order["region_id"] = self.region
                    del order["location_id"]
                    valid_orders.append(order)
                if "sell_order" in orders:
                    order = orders["sell_order"]
                    order["station_id"] = order["location_id"]
                    order["region_id"] = self.region
                    del order["location_id"]
                    valid_orders.append(order)

        return [json.dumps(record) for record in valid_orders]
