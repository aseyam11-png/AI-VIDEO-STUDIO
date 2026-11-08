# Optional CapCut Handoff

CapCut is an optional desktop finishing destination, not a required server-side rendering engine.

- Keep the canonical timeline in OpenTimelineIO plus project JSON.
- Export media assets, captions (SRT/VTT), stills and an edit-decision manifest.
- Provide a local Windows handoff folder with clip order, source in/out points, track, title and transition notes.
- Use a CapCut project bridge only if it is verified against the exact desktop version and its terms permit the integration.
- Do not make core production depend on undocumented project formats or reverse-engineered APIs.
- Always retain an independently rendered FFmpeg master.
- The remote server cannot directly operate the desktop application without a local Windows bridge. The bridge must authenticate to the private server and download only approved assets.
