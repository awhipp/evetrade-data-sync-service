# -*- encoding: utf-8 -*-
"""EsiPy Client"""
from __future__ import absolute_import

import logging
import time
import warnings
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor

from requests import Session
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout

from .events import API_CALL_STATS
from .exceptions import APIException
from .utils import check_cache, get_cache_time_left, make_cache_key

LOGGER = logging.getLogger(__name__)

# create a named tuple to store the data
CachedResponse = namedtuple(
    "CachedResponse", ["status_code", "headers", "content", "url"]
)


class EsiClient:
    """EsiClient is a client that override some behavior and
    also add some features like auto retry, parallel calls..."""

    __schemes__ = set(["https"])
    __uncached_methods__ = ["POST", "PUT", "DELETE", "HEAD"]

    def __init__(self, security=None, retry_requests=False, **kwargs):
        """Init the ESI client object

        :param security: (optional) the security object [default: None]
        :param retry_requests: (optional) use a retry loop for all requests
        :param headers: (optional) additional headers we want to add
        :param transport_adapter: (optional) an HTTPAdapter object / implement
        :param cache: (optional) esipy.cache.BaseCache cache implementation.
        :param raw_body_only: (optional) default value [False] for all requests
        :param signal_api_call_stats: (optional) allow to define a specific
            signal to use, instead of using the global API_CALL_STATS
        :param timeout: (optional) default value [None=No timeout]
        timeout in seconds for requests
        :param no_etag_body: (optional) default False, set to return empty
        response when ETag requests return 304 (normal http behavior)
        """
        # No BaseClient init needed
        self.security = security
        self._session = Session()

        # set the proper request implementation
        if retry_requests:
            self.request = self._retry_request
        else:
            self.request = self._request

        # store default raw_body_only in case user never want parsing
        self.raw_body_only = kwargs.pop("raw_body_only", False)

        # check for specified headers and update session.headers
        headers = kwargs.pop("headers", {})
        if "User-Agent" not in headers:
            warning_message = (
                "Defining a 'User-Agent' header is a"
                " good practice, and allows CCP to contact you if required."
                " To do this, simply add the following when creating"
                " the client: headers={'User-Agent':'something'}."
            )
            LOGGER.warning(warning_message)
            warnings.warn(warning_message)

            headers["User-Agent"] = "EsiPy/Client - " "https://github.com/Kyria/EsiPy"
        self._session.headers.update({"Accept": "application/json"})
        self._session.headers.update(headers)

        # transport adapter
        transport_adapter = kwargs.pop("transport_adapter", None)
        if isinstance(transport_adapter, HTTPAdapter):
            self._session.mount("http://", transport_adapter)
            self._session.mount("https://", transport_adapter)

        # initiate the cache object
        self.cache = check_cache(kwargs.pop("cache", False))

        # other
        self.signal_api_call_stats = kwargs.pop("signal_api_call_stats", API_CALL_STATS)

        self.timeout = kwargs.pop("timeout", None)
        self.no_etag_body = kwargs.pop("no_etag_body", False)

    def _retry_request(self, method, url, **kwargs):
        """Retry loop for HTTP requests (for 5xx level errors)."""
        retries = 0
        while retries < 5:
            try:
                response = self._session.request(method, url, **kwargs)
                if 500 <= response.status_code <= 599:
                    retries += 1
                    LOGGER.warning(
                        f"[failure #{retries}] {url} {response.status_code}: {response.text}"
                    )
                    time.sleep(retries**4 / 100)
                    continue
                return response
            except (RequestsConnectionError, Timeout) as exc:
                retries += 1
                LOGGER.warning(f"[retry #{retries}] {url} connection error: {exc}")
                time.sleep(retries**4 / 100)
        raise APIException(
            url, 500, response=None, request_param=None, response_header=None
        )

    def multi_request(self, requests_data, threads=20, **kwargs):
        """Send multiple HTTP requests in parallel.
        :param requests_data: iterable of dicts with 'method', 'url', and optional kwargs
        :param threads: number of concurrent workers to use
        :return: a list of (request_data, response) tuples
        """
        threads = max(min(threads, 100), 1)

        def _multi_shim(request_data):
            method = request_data.get("method", "GET")
            url = request_data["url"]
            req_kwargs = request_data.get("kwargs", {})
            response = self._session.request(method, url, **req_kwargs)
            return request_data, response

        results = []
        with ThreadPoolExecutor(max_workers=threads) as pool:
            for result in pool.map(_multi_shim, requests_data):
                results.append(result)
        return results

    def request(self, method, url, **kwargs):
        """Make a single HTTP request with caching and error handling.
        :param method: HTTP method (GET, POST, etc)
        :param url: URL to request
        :param kwargs: passed to requests.Session.request
        :return: requests.Response
        """
        cache_key = make_cache_key({"method": method, "url": url, **kwargs})
        cached_response = self.cache.get(cache_key, None)
        if cached_response is not None:
            expires = cached_response.headers.get("expires", None)
            if expires is not None:
                cache_timeout = get_cache_time_left(cached_response.headers["expires"])
                if cache_timeout >= 0:
                    return cached_response
            etag = cached_response.headers.get("etag", None)
            if etag is not None:
                kwargs.setdefault("headers", {})["If-None-Match"] = etag
            if (expires is None or cache_timeout < 0) and etag is None:
                self.cache.invalidate(cache_key)
        response = self._session.request(method, url, timeout=self.timeout, **kwargs)
        if response.status_code == 200:
            self.__cache_response(cache_key, response, method.upper())
        if "warning" in response.headers:
            LOGGER.warning(f"[{response.url}] {response.headers['warning']}")
            warnings.warn(f"[{response.url}] {response.headers['warning']}")
        if response.status_code >= 400 and kwargs.get("raise_on_error", False):
            raise APIException(
                url,
                response.status_code,
                response=response.content,
                request_param=None,
                response_header=response.headers,
            )
        return response

    def head(self, url, **kwargs):
        """Make a HEAD request to the given URL."""
        return self.request("HEAD", url, **kwargs)

    def __cache_response(self, cache_key, res, method):
        """cache the response

        if method is one of self.__uncached_method__, don't cache anything
        """
        if "expires" in res.headers and method not in self.__uncached_methods__:
            cache_timeout = get_cache_time_left(res.headers.get("expires"))

            # Occasionally CCP swagger will return an outdated expire
            # warn and skip cache if timeout is <0
            if cache_timeout >= 0:
                self.cache.set(
                    cache_key,
                    CachedResponse(
                        status_code=res.status_code,
                        headers=res.headers,
                        content=res.content,
                        url=res.url,
                    ),
                    cache_timeout,
                )
            else:
                LOGGER.warning("[%s] returned expired result: %s", res.url, res.headers)
                warnings.warn("[%s] returned expired result" % res.url)

    # __make_request is now handled in request()
