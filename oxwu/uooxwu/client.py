from __future__ import annotations

import json
from importlib import resources
from typing import Any, Callable

import requests
import socketio

from .models import Report
from .models import ReportUpdateEvent
from .models import Town
from .models import Warning
from .models import WarningUpdateEvent


EventHandler = Callable[..., Any]


class Client:
    def __init__(
        self,
        url: str,
        api_key: str | None = None,
        *,
        timeout: float = 10.0,
        socket_transports: list[str] | None = None,
    ):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.socket_transports = socket_transports or ["polling"]

        self._session = requests.Session()
        self._socket: socketio.Client | None = None
        self._event_handlers: dict[str, EventHandler] = {}
        self._town_map_cache: dict[str, Town] | None = None

    def _headers(self, *, auth: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {}
        if auth and self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _request(self, method: str, path: str, *, auth: bool = False, **kwargs):
        response = self._session.request(
            method,
            f"{self.url}{path}",
            headers=self._headers(auth=auth),
            timeout=kwargs.pop("timeout", self.timeout),
            **kwargs,
        )
        response.raise_for_status()
        return response

    def _wrap_event_handler(self, event_name: str, handler: EventHandler) -> EventHandler:
        if event_name == "proxy_warning_update":
            return lambda payload: handler(WarningUpdateEvent.from_api(payload or {}))
        if event_name == "proxy_report_update":
            return lambda payload: handler(ReportUpdateEvent.from_api(payload or {}))
        return handler

    def get_report(self) -> Report:
        return Report.from_api(self._request("GET", "/api/data/report", auth=True).json())

    def get_warning(self) -> Warning:
        return Warning.from_api(self._request("GET", "/api/data/warning", auth=True).json())

    def _parse_town_map(self, payload: dict[str, Any]) -> dict[str, Town]:
        return {str(town_id): Town.from_api(str(town_id), town) for town_id, town in payload.items()}

    def load_builtin_town_map(self, *, use_cache: bool = True) -> dict[str, Town]:
        if use_cache and self._town_map_cache is not None:
            return dict(self._town_map_cache)

        data_path = resources.files("uooxwu").joinpath("data/town_id.json")
        payload = json.loads(data_path.read_text(encoding="utf-8"))
        town_map = self._parse_town_map(payload)
        self._town_map_cache = town_map
        return dict(town_map)

    def fetch_town_map(self, *, use_cache: bool = False) -> dict[str, Town]:
        payload = self._request("GET", "/api/town-map").json()
        town_map = self._parse_town_map(payload)
        if use_cache:
            self._town_map_cache = town_map
        return dict(town_map)

    def get_town_map(self, *, source: str = "builtin", use_cache: bool = True) -> dict[str, Town]:
        if source == "builtin":
            return self.load_builtin_town_map(use_cache=use_cache)
        if source == "remote":
            return self.fetch_town_map(use_cache=use_cache)
        if source == "auto":
            try:
                return self.fetch_town_map(use_cache=use_cache)
            except requests.RequestException:
                return self.load_builtin_town_map(use_cache=use_cache)
        raise ValueError("source must be one of: 'builtin', 'remote', 'auto'")

    def get_screenshot(self, type_: str) -> bytes:
        return self._request("GET", f"/api/screenshot/{type_}", auth=True).content

    def event(self, name: str | None = None):
        def decorator(func: EventHandler):
            event_name = name or func.__name__
            self._event_handlers[event_name] = func
            if self._socket is not None:
                self._socket.on(event_name, handler=self._wrap_event_handler(event_name, func))
            return func

        return decorator

    def on(self, name: str):
        return self.event(name)

    def _ensure_socket(self):
        if self._socket is not None:
            return self._socket

        self._socket = socketio.Client(reconnection=True)
        for event_name, handler in self._event_handlers.items():
            self._socket.on(event_name, handler=self._wrap_event_handler(event_name, handler))
        return self._socket

    def connect(self, *, wait: bool = False):
        client = self._ensure_socket()
        client.connect(
            self.url,
            headers=self._headers(auth=True),
            transports=self.socket_transports,
            socketio_path="socket.io",
        )
        if wait:
            client.wait()
        return client

    def wait(self):
        if self._socket is None:
            raise RuntimeError("Socket.IO client is not connected yet")
        self._socket.wait()

    def disconnect(self):
        if self._socket is not None and self._socket.connected:
            self._socket.disconnect()

    def close(self):
        self.disconnect()
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
