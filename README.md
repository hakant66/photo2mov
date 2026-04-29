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

Batch mode is also available at:

```text
http://127.0.0.1:8000/batch.html
```

In batch mode, provide:

- One main folder path
- One or more subfolder names (line-separated or comma-separated)
- The same duration and zoom settings

For each subfolder, the app checks `main/subfolder/Etsy/` for `1.png`, `1.jpg`, or `1.jpeg`, then generates or replaces `1.mov` in that same `Etsy` folder.

Enhance photo mode is available at:

```text
http://127.0.0.1:8000/enhance.html
```

In enhance mode, provide one main folder path. The app scans all nested subfolders up to 3 levels and processes folders that match:

- `main/<level1>/Etsy`
- `main/<level1>/<level2>/Etsy`
- `main/<level1>/<level2>/<level3>/Etsy`

For each matching Etsy folder, it looks for `1.png`, `1.jpg`, or `1.jpeg` and creates/replaces `1_etsy.jpg`.

Enhance mode now runs as an async batch job:

- Enter `Images To Process` to define how many matched images should be processed in that run
- Enter `Resolution (WIDTHxHEIGHT)` to control output size (default `2000x2000`)
- See live progress and log messages in the UI while the job is running
- Use `Stop Batch` to stop gracefully (the current image finishes first, then processing stops)

Single enhance mode is available at:

```text
http://127.0.0.1:8000/single-enhance.html
```

In single enhance mode, provide:

- A full source image path (including folder + file name)
- `Resolution (WIDTHxHEIGHT)` input (default `2000x2000`)

It creates or replaces `1_etsy.jpg` in the same folder as the source image.

AVIF conversion mode is available at:

```text
http://127.0.0.1:8000/convert-avif.html
```

Provide either:

- A full `.avif` source file path to convert one file, or
- A folder path to convert all `.avif` files under that folder (recursive)

The output keeps the same base name and writes `.jpg` beside each source file, overriding existing `.jpg` files with the same name.

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
