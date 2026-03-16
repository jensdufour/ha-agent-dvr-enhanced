"""API client for AgentDVR."""

import asyncio
import json
import logging
import re
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

SAFE_FILENAME = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


class AgentDVRApiError(Exception):
    """Base exception for AgentDVR API errors."""


class AgentDVRConnectionError(AgentDVRApiError):
    """Connection error."""


class AgentDVRApiClient:
    """Async API client for AgentDVR."""

    def __init__(self, host: str, session: aiohttp.ClientSession) -> None:
        """Initialize the API client."""
        self._host = host.rstrip("/")
        self._session = session

    @property
    def server_url(self) -> str:
        """Return the server base URL."""
        return self._host

    async def _request_json(self, path: str, timeout: int = 10) -> Any:
        """Perform a GET request and return JSON."""
        url = f"{self._host}/{path}"
        try:
            async with asyncio.timeout(timeout):
                resp = await self._session.get(url)
                resp.raise_for_status()
                text = await resp.text()
                return json.loads(text)
        except asyncio.TimeoutError as err:
            raise AgentDVRConnectionError(
                "Timeout connecting to AgentDVR"
            ) from err
        except json.JSONDecodeError as err:
            _LOGGER.error("Invalid JSON from AgentDVR at %s: %s", url, err)
            raise AgentDVRApiError(
                f"Invalid JSON response from {url}"
            ) from err
        except (aiohttp.ClientError, aiohttp.ClientResponseError) as err:
            raise AgentDVRConnectionError(
                f"Error connecting to AgentDVR at {url}: {err}"
            ) from err

    async def _request_bytes(self, path: str, timeout: int = 10) -> bytes:
        """Perform a GET request and return raw bytes."""
        url = f"{self._host}/{path}"
        try:
            async with asyncio.timeout(timeout):
                resp = await self._session.get(url)
                resp.raise_for_status()
                return await resp.read()
        except asyncio.TimeoutError as err:
            raise AgentDVRConnectionError(
                "Timeout connecting to AgentDVR"
            ) from err
        except (aiohttp.ClientError, aiohttp.ClientResponseError) as err:
            raise AgentDVRConnectionError(
                "Error connecting to AgentDVR"
            ) from err

    async def get_status(self) -> dict[str, Any]:
        """Get server status."""
        return await self._request_json("command.cgi?cmd=getStatus")

    async def get_objects(self) -> dict[str, Any]:
        """Get all server objects (cameras, microphones)."""
        return await self._request_json("command.cgi?cmd=getObjects")

    async def get_object(self, oid: int, ot: int) -> dict[str, Any]:
        """Get a specific object by ID and type."""
        return await self._request_json(
            f"command.cgi?cmd=getObject&oid={oid}&ot={ot}"
        )

    async def get_events(
        self,
        oid: int | None = None,
        ot: int | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get recordings/events list. Handles pagination (max 400 per call)."""
        params: list[str] = []
        if oid is not None:
            params.append(f"oid={oid}")
        if ot is not None:
            params.append(f"ot={ot}")
        if tag:
            params.append(f"tag={tag}")

        base_query = "&".join(params)
        path = f"q/getEvents?{base_query}" if base_query else "q/getEvents"

        all_events: list[dict[str, Any]] = []
        while True:
            data = await self._request_json(path)
            if not isinstance(data, list):
                break
            all_events.extend(data)
            if len(data) < 400:
                break
            last_ts = data[-1].get("time", data[-1].get("timestamp"))
            if last_ts is None:
                break
            sep = "&" if base_query else ""
            path = f"q/getEvents?{base_query}{sep}enddate={last_ts}"

        return all_events

    async def get_still_image(self, oid: int) -> bytes:
        """Get a still JPEG image from a camera."""
        return await self._request_bytes(f"grab.jpg?oid={oid}")

    async def get_thumbnail(self, oid: int, filename: str) -> bytes:
        """Get a thumbnail for a recording."""
        return await self._request_bytes(
            f"fileThumb.jpg?oid={oid}&fn={filename}"
        )

    async def get_alerts(self) -> dict[str, Any]:
        """Get current alerts."""
        return await self._request_json("alerts.json")

    async def stream_recording(
        self, oid: int, ot: int, filename: str
    ) -> aiohttp.ClientResponse:
        """Get a streaming response for a recording file."""
        url = (
            f"{self._host}/streamFile.cgi?oid={oid}&ot={ot}&fn={filename}"
        )
        resp = await self._session.get(url)
        resp.raise_for_status()
        return resp

    def get_mjpeg_url(self, oid: int) -> str:
        """Get the MJPEG stream URL for a camera."""
        return f"{self._host}/video.mjpg?oid={oid}"

    def get_recording_url(self, oid: int, ot: int, filename: str) -> str:
        """Get the direct URL to stream a recording."""
        return f"{self._host}/streamFile.cgi?oid={oid}&ot={ot}&fn={filename}"
