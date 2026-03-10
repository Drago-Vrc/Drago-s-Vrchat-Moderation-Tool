# Drago's Moderation Tool

Windows-first VRChat session monitor that reads local data sources:

- VRChat log files for world changes and player join/leave events
- VRCX SQLite data for avatar changes plus best-effort avatar/platform metadata

It is meant to help review session activity. It does **not** sniff network traffic, collect IP addresses, or make moderation decisions for you.
This project is **not affiliated with, endorsed by, or sponsored by VRChat**.

## Supported Platform

- Windows
- Python 3.10+ for source runs
- VRCX installed if you want avatar/platform enrichment

## Quick Start

1. Install Python 3.10+ on Windows.
2. Install dependencies:

```bat
install_deps.bat
```

3. Run:

```bat
run_tool.bat
```

Or use the desktop installer from this folder:

```bat
install_on_desktop.bat
```

No extra scripting shell is required for install or launch.

## What It Tracks

- current world changes
- player joins and leaves
- avatar changes
- rapid-switch and mass-leave heuristics
- best-effort per-user and per-avatar risk summaries

## What It Does Not Do

- collect IP addresses
- inspect packets or scrape network connections
- bypass VRChat protections
- guarantee that a flagged event is malicious

## Runtime Files

These are local runtime outputs and are not meant for release archives:

- `moderation_tool_settings.json`
- `players.txt`
- `session_history.log`

Use `moderation_tool_settings.example.json` as a blank settings template.

## Limitations

- Windows-focused
- avatar/platform lookups depend on local VRChat logs and VRCX data
- heuristics can false positive; review results manually
- webhook delivery depends on local network and SSL setup

## Disclaimer

See `DISCLAIMER.md` or `DISCLAIMER.txt` before use. All outputs are informational and should be reviewed by the user.

## License

MIT (see `LICENSE`).
