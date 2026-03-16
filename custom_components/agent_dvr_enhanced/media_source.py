"""Media source platform for Agent DVR Enhanced recordings."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN, OBJECT_TYPE_CAMERA
from .coordinator import AgentDVRCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_get_media_source(hass: HomeAssistant) -> AgentDVRMediaSource:
    """Set up the Agent DVR media source."""
    return AgentDVRMediaSource(hass)


class AgentDVRMediaSource(MediaSource):
    """Provide AgentDVR recordings as a browsable media source."""

    name = "Agent DVR Recordings"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the media source."""
        super().__init__(DOMAIN)
        self.hass = hass

    # ------------------------------------------------------------------
    # Resolve: turn an identifier into a playable URL
    # ------------------------------------------------------------------

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a media item to a playable URL.

        Identifier format: {entry_id}/{oid}/{ot}/{filename}
        """
        parts = item.identifier.split("/", 3)
        if len(parts) != 4:
            raise Unresolvable(f"Invalid identifier: {item.identifier}")

        entry_id, oid, ot, filename = parts

        if entry_id not in self.hass.data.get(DOMAIN, {}):
            raise Unresolvable(f"Config entry not loaded: {entry_id}")

        url = (
            f"/api/agent_dvr_enhanced/recording/{entry_id}/{oid}/{ot}/{filename}"
        )

        mime_type = "video/mp4"
        lower = filename.lower()
        if lower.endswith(".mkv"):
            mime_type = "video/x-matroska"
        elif lower.endswith(".webm"):
            mime_type = "video/webm"
        elif lower.endswith(".avi"):
            mime_type = "video/x-msvideo"

        return PlayMedia(url=url, mime_type=mime_type)

    # ------------------------------------------------------------------
    # Browse: build the media tree
    # ------------------------------------------------------------------

    async def async_browse_media(
        self, item: MediaSourceItem
    ) -> BrowseMediaSource:
        """Browse media.

        Navigation hierarchy:
          root  ->  camera list
          camera  ({entry_id}/{oid}/{ot})  ->  recording list
        """
        if not item.identifier:
            return self._build_root()

        parts = item.identifier.split("/")

        if len(parts) == 3:
            return await self._build_device_recordings(
                parts[0], int(parts[1]), int(parts[2])
            )

        raise Unresolvable(f"Invalid browse path: {item.identifier}")

    # ------------------------------------------------------------------
    # Tree builders
    # ------------------------------------------------------------------

    def _iter_coordinators(self):
        """Yield (entry_id, coordinator) pairs."""
        for key, value in self.hass.data.get(DOMAIN, {}).items():
            if isinstance(value, AgentDVRCoordinator):
                yield key, value

    def _build_root(self) -> BrowseMediaSource:
        """Build the top-level list of cameras across all config entries."""
        children: list[BrowseMediaSource] = []

        for entry_id, coordinator in self._iter_coordinators():
            for device in coordinator.devices:
                if int(device.get("typeID", 0)) != OBJECT_TYPE_CAMERA:
                    continue
                oid = int(device["id"])
                ot = int(device["typeID"])
                name = device.get("name", f"Camera {oid}")
                children.append(
                    BrowseMediaSource(
                        domain=DOMAIN,
                        identifier=f"{entry_id}/{oid}/{ot}",
                        media_class=MediaClass.DIRECTORY,
                        media_content_type=MediaType.VIDEO,
                        title=name,
                        can_play=False,
                        can_expand=True,
                    )
                )

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier="",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title="Agent DVR Recordings",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _build_device_recordings(
        self, entry_id: str, oid: int, ot: int
    ) -> BrowseMediaSource:
        """Fetch recordings for a camera and list them."""
        if entry_id not in self.hass.data.get(DOMAIN, {}):
            raise Unresolvable(f"Config entry not loaded: {entry_id}")

        coordinator: AgentDVRCoordinator = self.hass.data[DOMAIN][entry_id]

        # Resolve camera name
        camera_name = f"Camera {oid}"
        for device in coordinator.devices:
            if int(device.get("id", 0)) == oid:
                camera_name = device.get("name", camera_name)
                break

        # Get recordings from AgentDVR
        try:
            events = await coordinator.client.get_events(oid=oid, ot=ot)
        except Exception:
            _LOGGER.exception("Error fetching recordings for camera %s", oid)
            events = []

        children: list[BrowseMediaSource] = []
        for event in events:
            child = self._event_to_browse_item(entry_id, oid, ot, event)
            if child:
                children.append(child)

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{entry_id}/{oid}/{ot}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=f"{camera_name} Recordings",
            can_play=False,
            can_expand=True,
            children=children,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _event_to_browse_item(
        entry_id: str, oid: int, ot: int, event: dict[str, Any]
    ) -> BrowseMediaSource | None:
        """Convert an AgentDVR event/recording dict to a browse item."""
        filename = event.get("fn", event.get("filename", ""))
        if not filename:
            return None

        # Build a human-readable title from available fields
        title = _format_event_title(event, filename)

        # Thumbnail: swap video extension for .jpg
        thumb_base = filename.rsplit(".", 1)[0]
        thumbnail = (
            f"/api/agent_dvr_enhanced/thumbnail/{entry_id}/{oid}/{thumb_base}.jpg"
        )

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{entry_id}/{oid}/{ot}/{filename}",
            media_class=MediaClass.VIDEO,
            media_content_type=MediaType.VIDEO,
            title=title,
            can_play=True,
            can_expand=False,
            thumbnail=thumbnail,
        )


def _format_event_title(event: dict[str, Any], filename: str) -> str:
    """Create a friendly title from event metadata."""
    # Try several common field names AgentDVR might use
    timestamp = event.get("time", event.get("timestamp", event.get("s", "")))
    duration = event.get("duration", event.get("dur", event.get("d", 0)))
    tags = event.get("tags", event.get("tag", ""))

    parts: list[str] = []

    if timestamp:
        try:
            if isinstance(timestamp, (int, float)):
                # JavaScript ticks (milliseconds since epoch)
                dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
                parts.append(dt.strftime("%Y-%m-%d %H:%M:%S"))
            else:
                parts.append(str(timestamp))
        except (ValueError, OSError):
            parts.append(str(timestamp))

    if not parts:
        parts.append(filename)

    if duration:
        try:
            dur_s = int(duration)
            minutes, seconds = divmod(dur_s, 60)
            if minutes:
                parts.append(f"({minutes}m {seconds}s)")
            else:
                parts.append(f"({seconds}s)")
        except (ValueError, TypeError):
            pass

    if tags:
        parts.append(f"[{tags}]")

    return " ".join(parts)
