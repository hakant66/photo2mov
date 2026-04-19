# Photo To MOV

This repo contains:

- A macOS Objective-C CLI renderer for local native use
- A dependency-free Python web app
- A Docker-ready Linux `ffmpeg` backend for containerized web app use

Both rendering paths produce a 5 second `.mov` with:

- A single smooth zoom from 100% to 111%
- Ease-in / ease-out motion with no position or rotation animation
- A subtle center-focused highlight treatment
- Slightly warmer overall color
- Original image resolution preserved
- QuickTime output encoded as H.264 to keep the file size low

The web UI exposes the render controls as variables:

- Duration in seconds, default `5`
- Zoom start percentage, default `100`
- Zoom end percentage, default `111`

## Build

```bash
make
```

This builds the native macOS CLI only. It is not used inside Docker.

## Run

Interactive prompt:

```bash
./photo_to_mov
```

Direct path input:

```bash
./photo_to_mov /path/to/photo.jpg
```

The output file is written next to the source image with the same base name and a `.mov` extension.

Use a source image from a writable folder, since the `.mov` is created beside it.

## Web App

Start the local web app:

```bash
make run-web
```

Then open:

```text
http://127.0.0.1:8000
```

Enter a real local source photo path in the browser and the app will:

- Read that file directly from disk
- Save the generated `.mov` either beside the source file or into the output folder you enter
- Use the duration and zoom values you enter in the form
- Show the created filename, source path, and full `.mov` path in the UI
- Expose the result as a clickable movie link and inline player attempt in the UI
- Prefill the output folder with your Google Drive Etsy folder by default

Notes:

- On macOS, the web server rebuilds `photo_to_mov` automatically if the source changed
- Leave the output folder blank to save the `.mov` beside the source photo
- The created file keeps the source base name and changes the extension to `.mov`
- This mode only works when the backend can access the path you entered

## Docker

The original Objective-C generator uses macOS frameworks, so Docker uses the Linux `ffmpeg` backend instead.

Build the container:

```bash
make docker-build
```

Run the container:

```bash
make docker-run
```

Then open:

```text
http://127.0.0.1:8000
```

You can also use Compose:

```bash
docker compose up --build
```

Both Docker flows mount your home directory into the container at the same absolute path, so local-path mode can read files under paths like `/Users/...`.
