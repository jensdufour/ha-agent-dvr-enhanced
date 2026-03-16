# Agent DVR Enhanced

A custom Home Assistant integration for [Agent DVR](https://www.ispyconnect.com/) that adds recording browsing, motion detection sensors, and enhanced camera support beyond the core HA integration.

## Features

- **Camera entities** with native MJPEG streaming proxied through HA
- **Recording browser** via HA's Media Source, with thumbnails and playback through HA (works with remote access)
- **Motion detection** binary sensor per camera
- **Alert** binary sensor per camera
- **Config flow** UI for easy setup

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Go to Integrations
3. Click the three-dot menu and select "Custom repositories"
4. Add this repository URL as an Integration
5. Search for "Agent DVR Enhanced" and install
6. Restart Home Assistant

### Manual

1. Copy the `custom_components/agent_dvr_enhanced` folder into your HA `custom_components` directory
2. Restart Home Assistant

## Setup

1. Go to **Settings > Devices & Services > Add Integration**
2. Search for "Agent DVR Enhanced"
3. Enter your AgentDVR server URL (e.g. `http://192.168.1.100:8090`)
4. The integration will discover all cameras on the server

> **Note:** If you have the core `agent_dvr` integration configured, remove it first to avoid duplicate camera entities.

## Entities created

For each camera on the AgentDVR server:

| Entity | Type | Description |
|--------|------|-------------|
| `camera.<name>` | Camera | Live MJPEG stream and still images |
| `binary_sensor.<name>_motion` | Binary Sensor | On when motion is detected |
| `binary_sensor.<name>_alert` | Binary Sensor | On when an alert is active |

## Browsing recordings

Open the **Media Browser** in HA (or use the media button in the Advanced Camera Card). You will see an "Agent DVR Recordings" source listing all cameras. Expand a camera to see its recordings with thumbnails.

Recordings are streamed through HA's HTTP API, so they work via Nabu Casa and other remote-access setups.

### Advanced Camera Card

To use with the [Advanced Camera Card](https://github.com/dermotduffy/advanced-camera-card):

```yaml
type: custom:advanced-camera-card
cameras:
  - camera_entity: camera.agent_dvr_enhanced_<your_camera>
```

Browse recordings through the card's media viewer button.

## AgentDVR API

This integration communicates with AgentDVR's local REST API. Key endpoints used:

| Endpoint | Purpose |
|----------|---------|
| `/command.cgi?cmd=getObjects` | Discover cameras |
| `/command.cgi?cmd=getStatus` | Server status |
| `/grab.jpg?oid=X` | Still image |
| `/video.mjpg?oid=X` | MJPEG live stream |
| `/q/getEvents?oid=X&ot=2` | List recordings |
| `/streamFile.cgi?oid=X&ot=2&fn=FILE` | Stream a recording |
| `/fileThumb.jpg?oid=X&fn=FILE` | Recording thumbnail |

## Requirements

- AgentDVR v5+ running on your local network
- Home Assistant 2024.1.0 or newer

## License

Apache 2.0
