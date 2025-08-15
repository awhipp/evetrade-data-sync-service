# -*- encoding: utf-8 -*-
"""App entry point. Uses Esi Meta Endpoint to work"""
import logging
import time

import requests

from .exceptions import APIException
from .utils import check_cache, get_cache_time_left

LOGGER = logging.getLogger(__name__)


class EsiApp(object):
    """EsiApp is an app object that'll allows us to play with ESI Meta
    API, not to have to deal with all ESI versions manually / meta"""

    def __init__(self, **kwargs):
        """Constructor.

        :param cache: if specified, use that cache, else use DictCache
        :param cache_time: is the minimum cache time for versions
            endpoints. If set to 0, never expires". None uses header expires
            Default 86400 (1d)
        :param cache_prefix: the prefix used to all cache key for esiapp
        :param meta_url: the meta url you want to use. Default is meta esi URL
            https://esi.evetech.net/swagger.json
        :param datasource: the EVE datasource to be used. Default: tranquility
        """
        self.meta_url = kwargs.pop("meta_url", "https://esi.evetech.net/swagger.json")
        self.expire = kwargs.pop("cache_time", 86400)
        if self.expire is not None and self.expire < 0:
            self.expire = 86400

        self.cache_prefix = kwargs.pop("cache_prefix", "esipy")
        self.esi_meta_cache_key = "%s:app:meta_swagger_url" % self.cache_prefix

        cache = kwargs.pop("cache", False)
        self.caching = True if cache is not None else False
        self.cache = check_cache(cache)
        self.datasource = kwargs.pop("datasource", "tranquility")

        self.swagger = self.__get_or_create_swagger(
            self.meta_url, self.esi_meta_cache_key
        )

    def __get_or_create_swagger(self, url, cache_key):
        """Fetch and cache the ESI Swagger JSON spec."""
        headers = {"Accept": "application/json"}
        swagger_url = f"{url}?datasource={self.datasource}"
        cached = self.cache.get(cache_key, (None, None, 0))
        if cached is None or len(cached) != 3:
            self.cache.invalidate(cache_key)
            cached_swagger, cached_headers, cached_expiry = (cached, None, 0)
        else:
            cached_swagger, cached_headers, cached_expiry = cached

        if cached_swagger is not None and cached_headers is not None:
            expires = cached_headers.get("expires", None)
            cache_timeout = -1
            if self.expire is None and expires is not None:
                cache_timeout = get_cache_time_left(cached_headers["expires"])
                if cache_timeout >= 0:
                    return cached_swagger
            else:
                if self.expire == 0 or cached_expiry >= time.time():
                    return cached_swagger
            etag = cached_headers.get("etag", None)
            if etag is not None:
                headers["If-None-Match"] = etag
            if (
                expires is None or cache_timeout < 0 or cached_expiry < time.time()
            ) and etag is None:
                self.cache.invalidate(cache_key)

        timeout = 0
        if self.expire is not None and self.expire > 0:
            timeout = time.time() + self.expire

        res = requests.head(swagger_url, headers=headers)
        if self.expire is not None and self.expire > 0:
            expiration = self.expire
        else:
            expiration = get_cache_time_left(res.headers.get("expires"))
        if res.status_code == 304 and cached_swagger is not None:
            self.cache.set(
                cache_key, (cached_swagger, res.headers, timeout), expiration
            )
            return cached_swagger

        # Fetch the Swagger JSON
        swagger = None
        for _retry in range(1, 4):
            try:
                swagger_res = requests.get(swagger_url, headers=headers)
                swagger_res.raise_for_status()
                swagger = swagger_res.json()
            except Exception as error:
                LOGGER.warning(f"[failure #{_retry}] {swagger_url}: {error}")
                continue
            break

        if swagger is None:
            raise APIException(
                swagger_url, 500, response=f"Cannot fetch '{swagger_url}'."
            )

        if self.caching and swagger:
            self.cache.set(cache_key, (swagger, res.headers, timeout), expiration)

        return swagger

    def get_swagger(self):
        """Return the cached ESI Swagger JSON spec."""
        return self.swagger

    def call_endpoint(
        self, path, method="get", params=None, headers=None, data=None, json=None
    ):
        """
        Call an ESI endpoint using the loaded Swagger spec.
        :param path: The endpoint path, e.g. '/v1/universe/types/'
        :param method: HTTP method (get, post, etc)
        :param params: Query parameters (dict)
        :param headers: HTTP headers (dict)
        :param data: POST/PUT data
        :param json: POST/PUT json
        :return: requests.Response
        """
        url = f"https://esi.evetech.net{path}"
        headers = headers or {"Accept": "application/json"}
        try:
            response = requests.request(
                method, url, params=params, headers=headers, data=data, json=json
            )
            response.raise_for_status()
            return response
        except Exception as e:
            LOGGER.error(f"ESI endpoint call failed: {e}")
            raise APIException(url, getattr(e, "response", None), response=str(e))

    # __getattribute__ no longer needed

    def clear_cached_swagger(self):
        """Invalidate the cached Swagger spec."""
        self.cache.invalidate(self.esi_meta_cache_key)
        self.swagger = None
