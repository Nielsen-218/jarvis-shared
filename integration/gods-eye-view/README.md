# God's Eye View integration (World Intelligence)

The Hub frames a local, loopback-only God's Eye View (MIT, https://github.com/bilawalsidhu/gods-eye-view) as the globe engine.
It is NOT vendored here. Install once outside the repo, apply the one-file bridge patch, and JARVIS starts/stops it on demand:

    git clone https://github.com/bilawalsidhu/gods-eye-view %USERPROFILE%\gods-eye-view
    cd %USERPROFILE%\gods-eye-view && npm ci --ignore-scripts
    git apply --ignore-whitespace <path-to>\integration\gods-eye-view\jarvis-bridge.patch
    npx vite build        # JARVIS serves dist/ with `vite preview`; rebuild after updating God's Eye View or the patch

The patch adds only camera/selection reporting (`gev:camera`, `gev:pick` postMessages to the framing page). Without it the globe
still works; the Hub just can't read the camera or the picked marker. Needs Node >= 24. No API keys required (keyless Esri imagery
and terrain). The MIT licence covers GEV's code, NOT its third-party data/imagery: see its DATA_SOURCES.md (TeleGeography cables are
non-commercial).
