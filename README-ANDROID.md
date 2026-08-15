# SliceAI Android-ready deployment

This package is designed so the Android user does NOT need Python, FFmpeg, or Termux.

## Deploy once
Use a host that supports Docker web services (the included `render.yaml` is prepared for Render):
1. Put this folder in a Git repository.
2. Create a new web service from the repository.
3. Use the included Dockerfile.
4. After deployment, open the HTTPS address in Chrome on Android.
5. Chrome menu -> Add to Home screen / Install app.

After that, SliceAI behaves like an Android web app. Video processing happens on the server.

## Important
A real backend must run somewhere. A phone browser cannot host the Python/FFmpeg server by itself reliably. The deployment is the one-time setup; users then only open the SliceAI URL/app.

## Current engine
FFmpeg scene-change detection + audio intensity ranking, followed by real H.264/AAC MP4 encoding.
